#!/usr/bin/env python3
"""
COBB AP3 JTAG File Exfiltrator v2.1
Uses ARM syscall shellcode + OpenOCD TCL port.
Reads files 256 bytes at a time directly into JTAG-readable RAM.

Usage:
  python jtag_exfil.py                    # interactive mode
  python jtag_exfil.py <path> [output]    # dump single file

Requires: OpenOCD running with TCL port on localhost:6666
Device must be halted in User mode at breakpoint 0x40B1B174

Changes from v2:
  - Removed push from shellcode (was creeping SP by 20 bytes/chunk)
  - Re-arms breakpoint before every resume in read_chunk
  - Verifies PC == LOOP_ADDR after halt; wrong-context halts trigger retry
  - Auto re-establishes userspace context on EFAULT/wrong-context and retries chunk
"""
import socket, struct, time, sys, os, re

OPENOCD_HOST = "127.0.0.1"
OPENOCD_PORT = 6666

SHELLCODE_ADDR = 0x43CDE400
FILENAME_ADDR  = 0x43CDE500
OUTPUT_ADDR    = 0x43CDE600
THUNK_BP       = 0x40B1B174
LOOP_ADDR      = 0x43CDE464  # FIX: was 0x468 — push removal shifts b. down by one slot
CHUNK_SIZE     = 256
MAX_RETRIES    = 5            # max re-establish-context retries per chunk
DUMPS_DIR      = "dumps"

# ARM shellcode: open(filename) -> lseek(offset) -> read(256) -> close() -> b.
#
# FIX: removed "push {r4,r5,r6,r7,lr}" that was at the top.
# We never return from this shellcode (it spins at b.), so saving regs is
# pointless — but the push was decrementing SP by 20 bytes every chunk,
# corrupting the ap-app stack after ~30+ calls.
#
# All branch encodings happen to be identical after the removal because the
# relative offsets work out the same. Only LOOP_ADDR changes (0x468 → 0x464).
#
SHELLCODE = [
    0xE1A05001,  # mov r5, r1          ; save offset
    0xE3A01000,  # mov r1, #0          ; O_RDONLY
    0xE3A02000,  # mov r2, #0
    0xE3A07005,  # mov r7, #5          ; __NR_open
    0xEF000000,  # svc #0
    0xE3500000,  # cmp r0, #0
    0xBA000011,  # blt done            ; open failed → r0=errno, jump to b.
    0xE1A04000,  # mov r4, r0          ; save fd
    0xE3550000,  # cmp r5, #0
    0x0A000004,  # beq read            ; skip lseek if offset==0
    0xE1A00004,  # mov r0, r4          ; fd
    0xE1A01005,  # mov r1, r5          ; offset
    0xE3A02000,  # mov r2, #0          ; SEEK_SET
    0xE3A07013,  # mov r7, #19         ; __NR_lseek
    0xEF000000,  # svc #0
    0xE1A00004,  # mov r0, r4          ; fd        (read:)
    0xE59F1020,  # ldr r1, [pc, #32]   ; load OUTPUT_ADDR from literal pool
    0xE3A02C01,  # mov r2, #256
    0xE3A07003,  # mov r7, #3          ; __NR_read
    0xEF000000,  # svc #0
    0xE1A06000,  # mov r6, r0          ; save bytes_read
    0xE1A00004,  # mov r0, r4          ; fd
    0xE3A07006,  # mov r7, #6          ; __NR_close
    0xEF000000,  # svc #0
    0xE1A00006,  # mov r0, r6          ; r0 = bytes_read (done:)
    0xEAFFFFFE,  # b .                 ; spin — LOOP_ADDR = 0x43CDE464
    OUTPUT_ADDR, # literal pool
]

# Sanity check at import time
assert SHELLCODE_ADDR + len(SHELLCODE) * 4 <= FILENAME_ADDR, \
    "Shellcode overlaps filename buffer!"
assert FILENAME_ADDR + 256 <= OUTPUT_ADDR, \
    "Filename buffer overlaps output buffer!"


class OpenOCD:
    def __init__(self, host=OPENOCD_HOST, port=OPENOCD_PORT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock.settimeout(30)

    def cmd(self, command, timeout=15):
        self.sock.sendall(command.encode('ascii') + b'\x1a')
        response = b''
        self.sock.settimeout(timeout)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b'\x1a' in response:
                    response = response[:response.index(b'\x1a')]
                    break
        except socket.timeout:
            pass
        return response.decode('ascii', errors='replace').strip()

    def mww(self, addr, val):
        self.cmd(f"mww 0x{addr:08X} 0x{val:08X}")

    def mdw(self, addr, count=1):
        resp = self.cmd(f"mdw 0x{addr:08X} {count}")
        result = []
        for line in resp.split('\n'):
            if ':' in line:
                parts = line.split(':', 1)[1].strip().split()
                for p in parts:
                    try:
                        result.append(int(p, 16))
                    except:
                        pass
        return result

    def read_reg(self, reg):
        resp = self.cmd(f"reg {reg}")
        m = re.search(r'0x([0-9a-fA-F]+)', resp)
        return int(m.group(1), 16) if m else None

    def write_reg(self, reg, val):
        self.cmd(f"reg {reg} 0x{val:08X}")

    def halt(self):
        self.cmd("halt")
        time.sleep(0.2)

    def resume(self):
        self.cmd("resume")

    def wait_halt(self, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            resp = self.cmd("targets", timeout=2)
            if "halted" in resp:
                return True
            time.sleep(0.3)
        return False

    def flush_icache(self):
        self.cmd("arm mcr 15 0 7 5 0 0")

    def rbp_all(self):
        self.cmd("rbp all")

    def set_bp(self, addr):
        self.cmd(f"bp 0x{addr:08X} 4 hw")


def ensure_dumps_dir():
    os.makedirs(DUMPS_DIR, exist_ok=True)


def dumps_path(filename):
    """Return path inside the dumps directory."""
    return os.path.join(DUMPS_DIR, os.path.basename(filename))


def get_to_user_mode(ocd):
    """Get halted in User mode (ap-app context)"""
    print("  Getting to User mode...", end='', flush=True)
    ocd.halt()
    ocd.rbp_all()
    ocd.set_bp(THUNK_BP)
    ocd.resume()

    for attempt in range(15):
        if not ocd.wait_halt(timeout=2):
            continue
        pc = ocd.read_reg("pc")
        if pc is not None and pc == THUNK_BP:
            print(f" got it (pc=0x{pc:08X})")
            ocd.rbp_all()
            return True
        ocd.resume()
        time.sleep(0.1)

    # Fallback: halt wherever we are and check if userspace is accessible
    print(" thunk not firing, trying halt+step...", end='', flush=True)
    ocd.rbp_all()
    for attempt in range(30):
        ocd.halt()
        time.sleep(0.1)
        pc = ocd.read_reg("pc")
        if pc is not None and 0x40000000 <= pc <= 0x50000000:
            print(f" got userspace (pc=0x{pc:08X})")
            return True
        ocd.cmd("step")
        time.sleep(0.05)
        ocd.resume()
        time.sleep(0.5)
        ocd.halt()
        pc = ocd.read_reg("pc")
        if pc is not None and 0x40000000 <= pc <= 0x50000000:
            print(f" got userspace (pc=0x{pc:08X})")
            return True

    print(" FAILED")
    return False


def setup_shellcode(ocd):
    """Write shellcode to memory"""
    print("  Writing shellcode...", end='', flush=True)
    for i, word in enumerate(SHELLCODE):
        ocd.mww(SHELLCODE_ADDR + i * 4, word)
    ocd.flush_icache()
    print(" done")


def write_filename(ocd, path):
    """Write filename string to memory"""
    raw = path.encode('ascii') + b'\x00'
    while len(raw) % 4:
        raw += b'\x00'
    for i in range(0, len(raw), 4):
        word = struct.unpack('<I', raw[i:i+4])[0]
        ocd.mww(FILENAME_ADDR + i, word)


def zero_output(ocd):
    """Clear the 256-byte output buffer"""
    for i in range(0, CHUNK_SIZE, 4):
        ocd.mww(OUTPUT_ADDR + i, 0)


# Sentinel return value for read_chunk: wrong process context
_ERR_WRONG_CONTEXT = -1000


def read_chunk(ocd, offset):
    """Execute shellcode to read one 256-byte chunk at given offset.

    Returns (data_bytes, bytes_read) on success.
    Returns (None, 0) on timeout with no halt.
    Returns (None, -N) on syscall errno N.
    Returns (None, _ERR_WRONG_CONTEXT) if CPU halted at unexpected PC
    — caller should re-establish userspace context and retry.
    """
    zero_output(ocd)
    ocd.write_reg("r0", FILENAME_ADDR)
    ocd.write_reg("r1", offset)
    ocd.write_reg("pc", SHELLCODE_ADDR)

    # FIX: re-arm breakpoint before every resume, not just once per file.
    # On ARM926 there are only 2 HW breakpoints; clear first to avoid conflicts.
    ocd.rbp_all()
    ocd.set_bp(LOOP_ADDR)
    ocd.resume()

    if not ocd.wait_halt(timeout=30):
        print("\n  WARNING: Timeout waiting for shellcode halt")
        ocd.halt()
        return None, 0

    # FIX: verify we actually halted at LOOP_ADDR.
    # A scheduler preemption or timer interrupt can halt the CPU mid-shellcode
    # at some other address. If that happens, r0 is garbage.
    pc = ocd.read_reg("pc")
    if pc != LOOP_ADDR:
        print(f"\n  WARNING: Halted at 0x{pc:08X} instead of LOOP_ADDR 0x{LOOP_ADDR:08X} "
              f"(wrong process context — will retry)")
        return None, _ERR_WRONG_CONTEXT

    bytes_read = ocd.read_reg("r0")
    if bytes_read is None:
        return None, 0

    # Negative return = syscall errno (high bit set in unsigned 32-bit)
    if bytes_read > 0x80000000:
        errno_val = (~bytes_read + 1) & 0xFFFFFFFF
        return None, -errno_val

    if bytes_read == 0:
        return b'', 0

    words_needed = (bytes_read + 3) // 4
    words = ocd.mdw(OUTPUT_ADDR, words_needed)

    data = b''
    for w in words:
        data += struct.pack('<I', w)

    return data[:bytes_read], bytes_read


def _reestablish_context(ocd, remote_path):
    """Re-establish userspace context and reload shellcode + filename.
    Called after a wrong-context or EFAULT error.
    Returns True on success."""
    print("  Re-establishing userspace context...")
    if not get_to_user_mode(ocd):
        return False
    setup_shellcode(ocd)
    write_filename(ocd, remote_path)
    return True


def exfil_file(ocd, remote_path, local_path):
    """Exfiltrate a complete file"""
    print(f"\n{'='*60}")
    print(f"  File: {remote_path}")
    print(f"  Save: {local_path}")
    print(f"{'='*60}")

    # Check if we're already halted in userspace
    ocd.halt()
    pc = ocd.read_reg("pc")
    in_userspace = pc is not None and 0x40000000 <= pc <= 0x50000000

    if not in_userspace:
        if not get_to_user_mode(ocd):
            return False

    setup_shellcode(ocd)
    write_filename(ocd, remote_path)

    all_data = b''
    offset = 0
    retries = 0

    while True:
        data, n = read_chunk(ocd, offset)

        # --- Wrong context (scheduler preempted us mid-shellcode) ---
        if n == _ERR_WRONG_CONTEXT:
            if retries < MAX_RETRIES:
                retries += 1
                print(f"  Retry {retries}/{MAX_RETRIES} after wrong-context halt "
                      f"(offset=0x{offset:X})")
                if not _reestablish_context(ocd, remote_path):
                    print("  Could not re-establish context — aborting")
                    break
                continue
            else:
                print(f"  Giving up after {MAX_RETRIES} wrong-context retries")
                break

        # --- Syscall error ---
        if data is None and n < 0:
            errno_val = -n
            if errno_val == 14:  # EFAULT — address not mapped → wrong process
                if retries < MAX_RETRIES:
                    retries += 1
                    print(f"\n  EFAULT (errno 14) at offset 0x{offset:X} — wrong process context. "
                          f"Retry {retries}/{MAX_RETRIES}...")
                    if not _reestablish_context(ocd, remote_path):
                        print("  Could not re-establish context — aborting")
                        break
                    continue
                else:
                    print(f"\n  EFAULT: giving up after {MAX_RETRIES} retries")
                    break
            else:
                print(f"\n  Error: errno {errno_val} at offset 0x{offset:X} "
                      f"({'ENOENT' if errno_val == 2 else 'unknown'})")
                break

        # --- Timeout with no halt ---
        if data is None and n == 0:
            print(f"\n  Error: timeout at offset 0x{offset:X}")
            break

        # --- EOF ---
        if n == 0:
            print(f"\n  EOF at offset 0x{offset:X}")
            break

        # --- Good chunk ---
        retries = 0  # Reset retry counter on success
        all_data += data
        offset += n
        print(f"\r  [{offset:>10d} bytes read]", end='', flush=True)

        if n < CHUNK_SIZE:
            print(f"\n  EOF (short read: {n} bytes)")
            break

    if all_data:
        with open(local_path, 'wb') as f:
            f.write(all_data)
        print(f"  Saved {len(all_data)} bytes → {local_path}")
        return True
    else:
        print("  No data recovered")
        return False


def interactive(ocd):
    """Interactive mode"""
    print("\nCommands:")
    print("  dump <path> [output]  - dump a file")
    print("  mtd                   - dump /proc/mtd")
    print("  mounts                - dump /proc/mounts")
    print("  all                   - dump key files")
    print("  explore               - dump remaining files for full RE")
    print("  settings              - dump raw NAND settings partitions")
    print("  installmap            - dump ALL install flow files + PIC state")
    print("  q                     - quit")

    while True:
        try:
            cmd = input("\nexfil> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue
        if cmd == 'q':
            break
        elif cmd == 'mtd':
            exfil_file(ocd, "/proc/mtd", dumps_path("proc_mtd.txt"))
        elif cmd == 'mounts':
            exfil_file(ocd, "/proc/mounts", dumps_path("proc_mounts.txt"))
        elif cmd == 'all':
            files = [
                ("/proc/mtd",                 "proc_mtd.txt"),
                ("/proc/mounts",              "proc_mounts.txt"),
                ("/proc/cpuinfo",             "proc_cpuinfo.txt"),
                ("/proc/version",             "proc_version.txt"),
                ("/ap-app/bin/writeeeprom",   "writeeeprom"),
                ("/ap-app/bin/readeeprom",    "readeeprom"),
                ("/ap-app/lib/libvehicle.so", "libvehicle.so"),
                ("/ap-app/lib/libap.so",      "libap.so"),
                ("/sbin/gadget_enable",       "gadget_enable.sh"),
                ("/sbin/gadget_disable",      "gadget_disable.sh"),
            ]
            for remote, local in files:
                exfil_file(ocd, remote, dumps_path(local))
        elif cmd == 'explore':
            files = [
                # Main application + subprocesses
                ("/ap-app/bin/gui",           "gui"),
                ("/ap-app/bin/usbmgr",        "usbmgr"),
                ("/ap-app/bin/main_menu",      "main_menu"),
                ("/ap-app/bin/exec_util",      "exec_util"),
                ("/ap-app/bin/about",          "about"),
                # Scripts
                ("/ap-app/bin/create_user_fs.sh",              "create_user_fs.sh"),
                ("/ap-app/bin/create_log.sh",                  "create_log.sh"),
                ("/ap-app/bin/create_screenshot.sh",           "create_screenshot.sh"),
                ("/ap-app/bin/using_realtime.sh",              "using_realtime.sh"),
                ("/ap-app/bin/spoof_user_rom.sh",              "spoof_user_rom.sh"),
                ("/ap-app/bin/update_maps_with_successor.sh",  "update_maps_with_successor.sh"),
                # Additional libraries
                ("/ap-app/lib/libUtil.so",          "libUtil.so"),
                ("/ap-app/lib/libPackaging.so",     "libPackaging.so"),
                ("/ap-app/lib/libExtPackaging.so",  "libExtPackaging.so"),
                ("/ap-app/lib/libUnits.so",         "libUnits.so"),
                ("/ap-app/lib/libtinyxml.so",       "libtinyxml.so"),
                ("/ap-app/lib/libparser.so",        "libparser.so"),
                # Configuration
                ("/ap-app/settings",                "ap-app_settings"),
                # System info
                ("/proc/self/environ",              "environ.txt"),
                ("/proc/self/maps",                 "maps.txt"),
                ("/proc/self/cmdline",              "cmdline.txt"),
                # Boot scripts
                ("/etc/inittab",                    "inittab.txt"),
                ("/etc/init.d/rcS",                 "rcS.sh"),
                ("/etc/fstab",                      "fstab.txt"),
                ("/etc/profile",                    "profile.txt"),
                # EEPROM
                ("/sys/devices/platform/s3c2410-i2c/i2c-0/0-0053/eeprom", "eeprom_raw.bin"),
            ]
            for remote, local in files:
                exfil_file(ocd, remote, dumps_path(local))
        elif cmd == 'settings':
            # Raw NAND settings partitions (128KB each, mostly 0xFF)
            files = [
                ("/dev/mtd2", "mtd2_settings_a.bin"),
                ("/dev/mtd3", "mtd3_settings_b.bin"),
                ("/dev/mtd4", "mtd4_settings_c.bin"),
                ("/dev/mtd5", "mtd5_settings_d.bin"),
            ]
            for remote, local in files:
                exfil_file(ocd, remote, dumps_path(local))
        elif cmd == 'installmap':
            files = [
                # Install flow scripts
                ("/ap-app/bin/install",                "install"),
                ("/ap-app/bin/uninstall",              "uninstall"),
                ("/ap-app/bin/subaru_funcs.sh",        "subaru_funcs.sh"),
                ("/ap-app/bin/sh_funcs.sh",            "sh_funcs.sh"),
                ("/ap-app/bin/obd_funcs.sh",           "obd_funcs.sh"),
                ("/ap-app/bin/main_menu",              "main_menu"),
                ("/ap-app/bin/main_menu.bin",           "main_menu.bin"),
                ("/ap-app/bin/uiInterface.sh",         "uiInterface.sh"),
                # Install binaries
                ("/ap-app/bin/subaru_identify",         "subaru_identify"),
                ("/ap-app/bin/subaru_test",             "subaru_test"),
                ("/ap-app/bin/subaru_flash",            "subaru_flash"),
                ("/ap-app/bin/subaru_reset",            "subaru_reset"),
                ("/ap-app/bin/subaru_rom_validator",    "subaru_rom_validator"),
                ("/ap-app/bin/subaru_ap_ident",         "subaru_ap_ident"),
                ("/ap-app/bin/prep_rom",                "prep_rom"),
                ("/ap-app/bin/map2rom",                 "map2rom"),
                ("/ap-app/bin/map_props",               "map_props"),
                ("/ap-app/bin/common_map_select",       "common_map_select"),
                ("/ap-app/bin/ap_mount",                "ap_mount"),
                # Settings tools
                ("/ap-app/bin/writeeeprom",             "writeeeprom"),
                ("/ap-app/bin/readeeprom",              "readeeprom"),
                # PIC / OBD firmware
                ("/ap-app/obd_firmware/dx_pic_fw_latest.ap", "dx_pic_fw_latest.ap"),
                # PIC sysfs
                ("/sys/dx_hw_pic/state",                "pic_state.txt"),
                ("/sys/dx_hw_pic/vbat",                 "pic_vbat.txt"),
                ("/sys/dx_hw_pic/fw_ver",               "pic_fw_ver.txt"),
                ("/sys/dx_hw_pic/memtest16",            "pic_memtest16.txt"),
                # User data (old install remnants)
                ("/user/ap-data/user.rom",              "user.rom"),
                ("/user/ap-data/prev_install",          "prev_install"),
                ("/user/ap-data/original_ecu.txt",      "original_ecu.txt"),
                # Config and environment
                ("/ap-app/settings",                    "ap-app_settings"),
                ("/proc/self/environ",                  "environ.txt"),
                ("/proc/self/maps",                     "maps.txt"),
            ]
            for remote, local in files:
                exfil_file(ocd, remote, dumps_path(local))
        elif cmd.startswith('dump '):
            parts = cmd.split(None, 2)
            remote = parts[1]
            local = dumps_path(parts[2] if len(parts) > 2 else os.path.basename(remote))
            exfil_file(ocd, remote, local)
        else:
            print(f"  Unknown: {cmd}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        print("Usage:")
        print("  python jtag_exfil.py                    # interactive mode")
        print("  python jtag_exfil.py <path> [output]    # dump single file")
        sys.exit(0)

    print(f"Connecting to OpenOCD at {OPENOCD_HOST}:{OPENOCD_PORT}...")
    ensure_dumps_dir()
    print(f"Outputs will be saved to: {os.path.abspath(DUMPS_DIR)}/")
    try:
        ocd = OpenOCD()
    except Exception as e:
        print(f"ERROR: {e}")
        print("Make sure OpenOCD is running with TCL port on 6666")
        sys.exit(1)
    print("Connected!")

    if len(sys.argv) > 1:
        remote = sys.argv[1]
        local = dumps_path(sys.argv[2] if len(sys.argv) > 2 else os.path.basename(remote))
        exfil_file(ocd, remote, local)
    else:
        interactive(ocd)

    print("\nDone!")


if __name__ == '__main__':
    main()