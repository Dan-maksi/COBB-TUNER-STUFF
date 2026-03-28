#!/usr/bin/env python3
"""
Accessport V3 Tool v5
- Fixed file read (tries multiple payload formats)
- Binary exfiltration via base64 chunks
- EEPROM read + AES key extraction
"""
import ctypes, struct, sys, time, datetime, os, base64

kernel32 = ctypes.windll.kernel32
winusb = ctypes.windll.winusb

VID, PID = 0x1A84, 0x0121
GUID = "{dee824ef-729b-4a0e-9c14-b7117d33a817}"
EP_OUT, EP_IN = 0x03, 0x82

TABLE = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    TABLE.append(c)


def jamcrc(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ TABLE[(crc ^ b) & 0xFF]
    return crc


BOOST_HDR = bytes.fromhex(
    "16000000"
    "73657269616c697a6174696f6e3a3a61726368697665"
    "0304040408"
)


def build_packet(cmd, payload=b''):
    length = len(payload) + 4
    header = struct.pack('<I', 2) + struct.pack('<H', length) + bytes([cmd])
    buf = header + payload + b'\x00\x00\x00\x00'
    crc = jamcrc(buf)
    return header + payload + struct.pack('>I', crc)


def boost_string(s):
    enc = s.encode('ascii')
    return BOOST_HDR + struct.pack('<I', 1) + struct.pack('<I', len(enc)) + enc


class SETUP(ctypes.Structure):
    _fields_ = [("RequestType", ctypes.c_ubyte),
                ("Request", ctypes.c_ubyte),
                ("Value", ctypes.c_ushort),
                ("Index", ctypes.c_ushort),
                ("Length", ctypes.c_ushort)]


def find_path():
    import winreg
    vid_pid = f"VID_{VID:04X}&PID_{PID:04X}"
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                         f"SYSTEM\\CurrentControlSet\\Enum\\USB\\{vid_pid}")
    inst = winreg.EnumKey(key, 0)
    winreg.CloseKey(key)
    return f"\\\\?\\USB#{vid_pid}#{inst}#{GUID}"


def connect(quiet=False):
    path = find_path()
    if not quiet: print(f"  Connecting...")
    hf = kernel32.CreateFileW(path, 0xC0000000, 0x03, None, 3, 0x40000080, None)
    if hf == ctypes.c_void_p(-1).value:
        return None, None
    hw = ctypes.c_void_p()
    if not winusb.WinUsb_Initialize(hf, ctypes.byref(hw)):
        kernel32.CloseHandle(hf)
        return None, None
    setup = SETUP(0x00, 0x09, 3, 0, 0)
    xferred = ctypes.c_ulong()
    winusb.WinUsb_ControlTransfer(hw, setup, None, 0, ctypes.byref(xferred), None)
    winusb.WinUsb_Free(hw)
    time.sleep(0.15)
    hw2 = ctypes.c_void_p()
    winusb.WinUsb_Initialize(hf, ctypes.byref(hw2))
    timeout = ctypes.c_ulong(4000)
    winusb.WinUsb_SetPipePolicy(hw2, EP_IN, 3, 4, ctypes.byref(timeout))
    winusb.WinUsb_SetPipePolicy(hw2, EP_OUT, 3, 4, ctypes.byref(timeout))
    if not quiet: print("  Connected!")
    return hf, hw2


def disconnect(hf, hw):
    if hw: winusb.WinUsb_Free(hw)
    if hf: kernel32.CloseHandle(hf)


def reconnect(hf, hw, quiet=False):
    disconnect(hf, hw)
    time.sleep(1.5)
    return connect(quiet=quiet)


def send_recv(hw, pkt, delay=0.15):
    buf = (ctypes.c_ubyte * len(pkt))(*pkt)
    n = ctypes.c_ulong()
    ok = winusb.WinUsb_WritePipe(hw, EP_OUT, buf, len(pkt), ctypes.byref(n), None)
    if not ok or not n.value:
        return None
    time.sleep(delay)
    rbuf = (ctypes.c_ubyte * 65536)()
    rn = ctypes.c_ulong()
    ok2 = winusb.WinUsb_ReadPipe(hw, EP_IN, rbuf, 65536, ctypes.byref(rn), None)
    return bytes(rbuf[:rn.value]) if ok2 and rn.value else None


def hex_dump(data, prefix="  "):
    for i in range(0, len(data), 16):
        c = data[i:i + 16]
        h = ' '.join(f'{b:02x}' for b in c)
        a = ''.join(chr(b) if 32 <= b < 127 else '.' for b in c)
        print(f"{prefix}{i:04x}: {h:<48s}  {a}")


def save(data, name):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fn = f"{name}_{ts}.bin"
    open(fn, 'wb').write(data)
    print(f"  Saved: {fn} ({len(data)}B)")
    return fn


def do_cmd(hf, hw, cmd, payload=b'', delay=0.15):
    pkt = build_packet(cmd, payload)
    resp = send_recv(hw, pkt, delay)
    if resp is not None:
        return hf, hw, resp
    print("  Session dropped — reconnecting...")
    hf, hw = reconnect(hf, hw)
    if hw is None:
        print("  Reconnect failed");
        return hf, hw, None
    for attempt in range(5):
        time.sleep(1.0 + attempt * 0.5)
        resp = send_recv(hw, pkt, delay)
        if resp is not None:
            return hf, hw, resp
        print(f"  Retry {attempt + 1}/5...")
    return hf, hw, None


def try_read_file(hf, hw, path, offset=0):
    """Try multiple payload formats for cmd 0x27."""
    path_b = path.encode('ascii')

    formats = [
        ("boost+offset", boost_string(path) + struct.pack('<I', offset)),
        ("raw+offset", path_b + b'\x00' + struct.pack('<I', offset)),
        ("raw+offset+len", path_b + b'\x00' + struct.pack('<II', offset, 65536)),
        ("boost_only", boost_string(path)),
        ("raw_only", path_b + b'\x00'),
    ]

    for name, payload in formats:
        hf, hw, resp = do_cmd(hf, hw, 0x27, payload, delay=0.5)
        if resp and len(resp) > 13:
            print(f"  Format '{name}' worked! {len(resp)}B")
            return hf, hw, resp
        status = resp[6] if resp else 0xFF
        print(f"  Format '{name}': {len(resp) if resp else 0}B cmd=0x{status:02X}")

    return hf, hw, None


def read_file_chunked(hf, hw, path):
    """Read a file in chunks, trying all format variants."""
    print(f"  Reading: {path}")
    all_data = b''
    offset = 0
    chunk_size = 4096

    while True:
        path_b = path.encode('ascii')
        # Try the formats most likely to work
        payloads = [
            boost_string(path) + struct.pack('<I', offset),
            path_b + b'\x00' + struct.pack('<I', offset),
            path_b + b'\x00' + struct.pack('<II', offset, chunk_size),
        ]

        got_data = False
        for payload in payloads:
            hf, hw, resp = do_cmd(hf, hw, 0x27, payload, delay=0.5)
            if resp and len(resp) > 13:
                data = resp[7:-4]
                all_data += data
                offset += len(data)
                got_data = True
                print(f"  +{len(data)}B (total {len(all_data)}B)")
                if len(data) < chunk_size:
                    print(f"  EOF")
                    return hf, hw, all_data
                break

        if not got_data:
            if offset == 0:
                print(f"  Failed - no format worked")
                return hf, hw, None
            print(f"  EOF (no more data)")
            return hf, hw, all_data


def do_read_eeprom(hf, hw):
    """Read raw EEPROM via known sysfs path."""
    eeprom_path = '/sys/devices/platform/s3c2410-i2c/i2c-0/0-0053/eeprom'
    print(f"\n  Reading EEPROM: {eeprom_path}")
    hf, hw, data = read_file_chunked(hf, hw, eeprom_path)
    if data:
        print(f"  EEPROM data ({len(data)}B):")
        hex_dump(data)
        save(data, 'eeprom_raw')
    else:
        print("  EEPROM read failed - trying raw format test first")
        hf, hw, _ = try_read_file(hf, hw, eeprom_path)
    return hf, hw


def main():
    print("=" * 60)
    print("  Accessport V3 Tool  v5")
    print("=" * 60)
    hf, hw = connect()
    if hw is None:
        print("ERROR: Cannot connect");
        sys.exit(1)

    try:
        while True:
            print()
            print("  1  Read settings (cmd 0x03)")
            print("  2  Device info (cmd 0x28)")
            print("  e  Read raw EEPROM")
            print("  f  Read file (format probe)")
            print("  x  Exfiltrate binary (ap-app or libvehicle)")
            print("  r  Raw command")
            print("  q  Quit")
            choice = input("\n> ").strip().lower()
            if choice == 'q':
                break

            elif choice == '1':
                hf, hw, resp = do_cmd(hf, hw, 0x03)
                if resp and len(resp) > 50:
                    payload = resp[7:-4]
                    state = payload[36] if len(payload) > 36 else None
                    states = {0x0A: "Installed (married)", 0x00: "Not installed (FREE)",
                              0x09: "Recovery"}
                    print(f"  {len(resp)}B")
                    hex_dump(resp)
                    print(f"\n  ecu_install_state = 0x{state:02X} — {states.get(state, 'UNKNOWN')}")
                    save(resp, 'settings')

            elif choice == '2':
                hf, hw, resp = do_cmd(hf, hw, 0x28, delay=0.2)
                if resp:
                    import re
                    text = resp[7:].decode('latin-1', errors='replace')
                    for m in re.findall(r'[ -~]{4,}', text):
                        if m.strip(): print(f"    {m.strip()}")

            elif choice == 'e':
                hf, hw = do_read_eeprom(hf, hw)

            elif choice == 'f':
                p = input("  File path: ").strip()
                hf, hw, data = try_read_file(hf, hw, p)
                if data and len(data) > 13:
                    hex_dump(data[:128])

            elif choice == 'x':
                print("  1. ap-app binary")
                print("  2. libvehicle.so")
                print("  3. writeeeprom binary")
                print("  4. Custom path")
                sub = input("  Choice: ").strip()
                paths = {
                    '1': '/ap-app/bin/ap-app',
                    '2': '/ap-app/lib/libvehicle.so',
                    '3': '/ap-app/bin/writeeeprom',
                }
                if sub == '4':
                    p = input("  Path: ").strip()
                else:
                    p = paths.get(sub, '')
                if p:
                    hf, hw, data = read_file_chunked(hf, hw, p)
                    if data:
                        fn = save(data, os.path.basename(p))
                        print(f"  Saved to {fn}")

            elif choice == 'r':
                ch = input("  cmd (hex): ").strip()
                ph = input("  payload hex (empty=none): ").strip()
                pay = bytes.fromhex(ph) if ph else b''
                hf, hw, resp = do_cmd(hf, hw, int(ch, 16), pay, delay=0.5)
                if resp:
                    print(f"  RX ({len(resp)}B) cmd=0x{resp[6]:02X}")
                    hex_dump(resp)

    finally:
        disconnect(hf, hw)
        print("Disconnected.")


if __name__ == '__main__':
    main()