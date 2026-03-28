#!/usr/bin/env python3
"""
COBB AP3 — PIC Power-Up and Firmware Flash via JTAG
Forces the PIC32 into powered state and flashes firmware.

Requirements:
  - OpenOCD running on localhost:6666
  - Device halted in User mode
  - 12V connected to OBD pin 16
"""

import socket
import struct
import time
import sys

OPENOCD_HOST = "127.0.0.1"
OPENOCD_PORT = 6666

SHELLCODE_ADDR = 0x43CDE400
FILENAME_ADDR  = 0x43CDE500
DATA_ADDR      = 0x43CDE600
LOOP_ADDR      = 0x43CDE464


class OpenOCD:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((OPENOCD_HOST, OPENOCD_PORT))
        self.sock.settimeout(15)

    def cmd(self, command, timeout=10):
        self.sock.sendall((command + "\x1a").encode())
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\x1a" in buf:
                    return buf.split(b"\x1a")[0].decode(errors='replace').strip()
            except socket.timeout:
                break
        return buf.decode(errors='replace').strip()

    def mww(self, addr, val):
        self.cmd(f"mww 0x{addr:08X} 0x{val:08X}")

    def mdw(self, addr):
        resp = self.cmd(f"mdw 0x{addr:08X} 1")
        if "data abort" in resp.lower():
            return None
        parts = resp.split(":")
        if len(parts) >= 2:
            return int(parts[1].strip().split()[0], 16)
        return None

    def reg_read(self, name):
        resp = self.cmd(f"reg {name}")
        if "0x" in resp:
            return int(resp.split("0x")[1].split()[0], 16)
        return None

    def reg_write(self, name, val):
        self.cmd(f"reg {name} 0x{val:08X}")

    def halt(self):
        self.cmd("halt", timeout=5)

    def resume(self):
        self.cmd("resume", timeout=2)

    def rbp_all(self):
        self.cmd("rbp all")

    def bp(self, addr):
        self.cmd(f"bp 0x{addr:08X} 4 hw")

    def flush_icache(self):
        self.cmd("arm mcr 15 0 7 5 0 0")

    def wait_halt(self, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            resp = self.cmd("targets", timeout=2)
            if "halted" in resp:
                return True
            time.sleep(0.5)
        return False


def write_string(ocd, addr, s):
    """Write a string to memory."""
    raw = s.encode('ascii') + b'\x00'
    while len(raw) % 4:
        raw += b'\x00'
    for i in range(0, len(raw), 4):
        word = struct.unpack('<I', raw[i:i+4])[0]
        ocd.mww(addr + i, word)


def write_bytes(ocd, addr, data):
    """Write raw bytes to memory."""
    while len(data) % 4:
        data += b'\x00'
    for i in range(0, len(data), 4):
        word = struct.unpack('<I', data[i:i+4])[0]
        ocd.mww(addr + i, word)


def run_write_file(ocd, filepath, content):
    """Write content to a file using syscall shellcode.
    
    open(filepath, O_WRONLY|O_TRUNC) = syscall 5, flags = 0x201
    write(fd, content, len) = syscall 4
    close(fd) = syscall 6
    """
    # Write shellcode:
    # r0 = FILENAME_ADDR (filepath)
    # syscall open
    # r0 = fd, r1 = DATA_ADDR, r2 = len
    # syscall write
    # close
    # b . (loop)

    shellcode = [
        # open(filename, O_WRONLY|O_TRUNC, 0)
        0xE59F0044,  # ldr r0, [pc, #0x44] → FILENAME_ADDR
        0xE3A01C02,  # mov r1, #0x200 (O_TRUNC)
        0xE2811001,  # add r1, r1, #1 (O_WRONLY) → r1 = 0x201
        0xE3A02000,  # mov r2, #0
        0xE3A07005,  # mov r7, #5 (__NR_open)
        0xEF000000,  # svc #0

        # check if open failed
        0xE3500000,  # cmp r0, #0
        0xBA000009,  # blt error (skip to loop with negative r0)

        # write(fd, data, len)
        0xE1A04000,  # mov r4, r0 (save fd)
        0xE59F1024,  # ldr r1, [pc, #0x24] → DATA_ADDR
        0xE59F2024,  # ldr r2, [pc, #0x24] → content length
        0xE3A07004,  # mov r7, #4 (__NR_write)
        0xEF000000,  # svc #0
        0xE1A05000,  # mov r5, r0 (save write return)

        # close(fd)
        0xE1A00004,  # mov r0, r4
        0xE3A07006,  # mov r7, #6 (__NR_close)
        0xEF000000,  # svc #0

        0xE1A00005,  # mov r0, r5 (return write result in r0)

        # loop (halt here)
        0xEAFFFFFE,  # b .  → LOOP at offset 0x48

        # Literal pool
        FILENAME_ADDR,       # offset 0x4C
        DATA_ADDR,           # offset 0x50
        len(content),        # offset 0x54
    ]

    # Recalculate LOOP_ADDR for this shellcode
    loop_addr = SHELLCODE_ADDR + 0x48

    # Write shellcode
    for i, word in enumerate(shellcode):
        ocd.mww(SHELLCODE_ADDR + i * 4, word)

    # Write filename
    write_string(ocd, FILENAME_ADDR, filepath)

    # Write content
    write_bytes(ocd, DATA_ADDR, content)

    # Execute
    ocd.rbp_all()
    ocd.bp(loop_addr)
    ocd.flush_icache()
    ocd.reg_write("pc", SHELLCODE_ADDR)
    ocd.resume()

    if not ocd.wait_halt(timeout=10):
        print("  WARNING: Timeout")
        ocd.halt()

    r0 = ocd.reg_read("r0")
    return r0


def run_read_file(ocd, filepath, max_bytes=256):
    """Read a small file using syscall shellcode. Returns bytes or None."""
    
    shellcode = [
        # open(filename, O_RDONLY)
        0xE59F0040,  # ldr r0, [pc, #0x40] → FILENAME_ADDR
        0xE3A01000,  # mov r1, #0 (O_RDONLY)
        0xE3A02000,  # mov r2, #0
        0xE3A07005,  # mov r7, #5 (__NR_open)
        0xEF000000,  # svc #0

        0xE3500000,  # cmp r0, #0
        0xBA000009,  # blt loop (open failed, r0 has errno)

        # read(fd, buf, len)
        0xE1A04000,  # mov r4, r0 (save fd)
        0xE59F1024,  # ldr r1, [pc, #0x24] → DATA_ADDR
        0xE3A020FF,  # mov r2, #255
        0xE3A07003,  # mov r7, #3 (__NR_read)
        0xEF000000,  # svc #0
        0xE1A05000,  # mov r5, r0 (save bytes read)

        # close
        0xE1A00004,  # mov r0, r4
        0xE3A07006,  # mov r7, #6 (__NR_close)
        0xEF000000,  # svc #0

        0xE1A00005,  # mov r0, r5

        # loop
        0xEAFFFFFE,  # b .  → LOOP at offset 0x44

        # Literals
        FILENAME_ADDR,       # 0x48
        DATA_ADDR,           # 0x4C
    ]

    loop_addr = SHELLCODE_ADDR + 0x44

    # Zero output buffer
    for i in range(0, 256, 4):
        ocd.mww(DATA_ADDR + i, 0)

    for i, word in enumerate(shellcode):
        ocd.mww(SHELLCODE_ADDR + i * 4, word)

    write_string(ocd, FILENAME_ADDR, filepath)

    ocd.rbp_all()
    ocd.bp(loop_addr)
    ocd.flush_icache()
    ocd.reg_write("pc", SHELLCODE_ADDR)
    ocd.resume()

    if not ocd.wait_halt(timeout=10):
        ocd.halt()
        return None

    r0 = ocd.reg_read("r0")
    if r0 is None or r0 > 0x80000000:
        return None

    if r0 == 0:
        return b''

    words_needed = (r0 + 3) // 4
    data = b''
    for i in range(words_needed):
        w = ocd.mdw(DATA_ADDR + i * 4)
        if w is None:
            break
        data += struct.pack('<I', w)
    return data[:r0]


def read_sysfs(ocd, path):
    """Read a sysfs value, return as stripped string."""
    data = run_read_file(ocd, path)
    if data:
        return data.decode('ascii', errors='replace').strip()
    return None


def main():
    print("=" * 60)
    print("  COBB AP3 — PIC Power-Up & Firmware Flash")
    print("=" * 60)

    print(f"\nConnecting to OpenOCD...")
    try:
        ocd = OpenOCD()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print("Connected!\n")

    # Step 1: Check current state
    print("[1] Reading current PIC state...")
    vbat = read_sysfs(ocd, "/sys/dx_hw_pic/vbat")
    state = read_sysfs(ocd, "/sys/dx_hw_pic/state")
    fw_ver = read_sysfs(ocd, "/sys/dx_hw_pic/fw_ver")
    print(f"  vbat:   {vbat} mV")
    print(f"  state:  {state}")
    print(f"  fw_ver: {fw_ver}")

    # Step 2: Power up PIC
    print("\n[2] Powering up PIC (writing 1 to state)...")
    r0 = run_write_file(ocd, "/sys/dx_hw_pic/state", b"1\n")
    print(f"  write returned: {r0}")
    time.sleep(1)

    # Step 3: Check state again
    print("\n[3] Reading PIC state after power-up...")
    vbat = read_sysfs(ocd, "/sys/dx_hw_pic/vbat")
    state = read_sysfs(ocd, "/sys/dx_hw_pic/state")
    fw_ver = read_sysfs(ocd, "/sys/dx_hw_pic/fw_ver")
    print(f"  vbat:   {vbat} mV")
    print(f"  state:  {state}")
    print(f"  fw_ver: {fw_ver}")

    if state != "1":
        print(f"\n  ✗ PIC did not power up (state={state})")
        print("  Check 12V connection to OBD pin 16")
        sys.exit(1)

    print(f"\n  ✓ PIC is running!")

    # Step 4: Check if firmware update needed
    print("\n[4] Checking if PIC firmware update is needed...")
    # The firmware file header byte 0 should be the version
    # fw_ver from PIC vs expected version
    print(f"  Current PIC firmware: {fw_ver}")
    print(f"  Expected: 11 (from dx_pic_fw_latest.ap header)")

    if fw_ver == "11":
        print("  ✓ PIC firmware is up to date!")
        print("\n  PIC is powered and running. Try installing on the car now.")
        sys.exit(0)

    # Step 5: Enter bootloader
    print("\n[5] Entering PIC bootloader (writing 2 to state)...")
    r0 = run_write_file(ocd, "/sys/dx_hw_pic/state", b"2\n")
    print(f"  write returned: {r0}")
    time.sleep(1)

    state = read_sysfs(ocd, "/sys/dx_hw_pic/state")
    print(f"  state after bootloader request: {state}")

    if state != "2":
        print(f"\n  ✗ PIC did not enter bootloader (state={state})")
        print("  This is the hardware(0) error.")
        print("  The PIC might need more voltage or has a hardware fault.")
        sys.exit(1)

    print("  ✓ PIC in bootloader mode!")

    # Step 6: Flash firmware
    print("\n[6] Flashing PIC firmware...")
    print("  Reading firmware file from device...")
    
    # We need to copy the firmware file to the PIC SPI device
    # This is: cat /ap-app/obd_firmware/dx_pic_fw_latest.ap > /dev/picspi0
    # We'll do it with open/read/write/close syscalls in chunks

    # First, get firmware size by reading it
    FW_PATH = "/ap-app/obd_firmware/dx_pic_fw_latest.ap"
    SPI_DEV = "/dev/picspi0"
    CHUNK = 256

    # Open firmware file
    print("  Opening firmware file...")
    
    # Build a shellcode that reads firmware and writes to SPI in chunks
    # This is complex - let's use a simpler approach:
    # system("cat /ap-app/obd_firmware/dx_pic_fw_latest.ap > /dev/picspi0")
    
    SYSTEM_ADDR = 0x4097B6F8  # system() in libc - may need recalculation
    
    cmd = b"cat /ap-app/obd_firmware/dx_pic_fw_latest.ap > /dev/picspi0\x00"
    write_string(ocd, FILENAME_ADDR, "cat /ap-app/obd_firmware/dx_pic_fw_latest.ap > /dev/picspi0")

    # Simple shellcode: call system(cmd_string)
    system_sc = [
        0xE59F0004,  # ldr r0, [pc, #4] → cmd string addr
        0xE59F1004,  # ldr r1, [pc, #4] → system() addr
        0xE12FFF31,  # blx r1
        0xEAFFFFFE,  # b .
        FILENAME_ADDR,
        SYSTEM_ADDR,
    ]

    print("  WARNING: system() address may have changed this boot.")
    print("  If this hangs, the address needs recalculation.")
    print()
    
    resp = input("  Proceed with PIC flash? [y/N] ").strip().lower()
    if resp != 'y':
        # Reset PIC back to normal
        run_write_file(ocd, "/sys/dx_hw_pic/state", b"1\n")
        print("  Aborted. PIC reset to normal mode.")
        sys.exit(0)

    loop_addr = SHELLCODE_ADDR + 0x0C

    for i, word in enumerate(system_sc):
        ocd.mww(SHELLCODE_ADDR + i * 4, word)

    ocd.rbp_all()
    ocd.bp(loop_addr)
    ocd.flush_icache()
    ocd.reg_write("pc", SHELLCODE_ADDR)
    ocd.resume()

    print("  Flashing... (this may take 10-30 seconds)")
    if not ocd.wait_halt(timeout=60):
        print("  Timeout — checking state anyway...")
        ocd.halt()

    r0 = ocd.reg_read("r0")
    print(f"  system() returned: {r0}")

    # Step 7: Reset PIC to normal mode
    print("\n[7] Resetting PIC to normal mode...")
    time.sleep(2)
    r0 = run_write_file(ocd, "/sys/dx_hw_pic/state", b"1\n")
    time.sleep(1)

    # Step 8: Verify
    print("\n[8] Verifying PIC state...")
    vbat = read_sysfs(ocd, "/sys/dx_hw_pic/vbat")
    state = read_sysfs(ocd, "/sys/dx_hw_pic/state")
    fw_ver = read_sysfs(ocd, "/sys/dx_hw_pic/fw_ver")
    memtest = read_sysfs(ocd, "/sys/dx_hw_pic/memtest16")
    print(f"  vbat:      {vbat} mV")
    print(f"  state:     {state}")
    print(f"  fw_ver:    {fw_ver}")
    print(f"  memtest16: {memtest}")

    if state == "1" and fw_ver and fw_ver != "-1":
        print(f"\n  ✓ PIC firmware updated successfully! Version: {fw_ver}")
        print("  Power cycle the device and try installing on the car.")
    else:
        print(f"\n  ✗ PIC may not have updated properly.")
        print("  Try power cycling and running this again.")


if __name__ == '__main__':
    main()
