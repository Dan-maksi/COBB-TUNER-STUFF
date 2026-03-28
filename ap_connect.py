#!/usr/bin/env python3
import ctypes, struct, sys, time

kernel32 = ctypes.windll.kernel32
winusb   = ctypes.windll.winusb

VID, PID = 0x1A84, 0x0121
GUID     = "{dee824ef-729b-4a0e-9c14-b7117d33a817}"

import winreg
vid_pid  = f"VID_{VID:04X}&PID_{PID:04X}"
key      = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                          f"SYSTEM\\CurrentControlSet\\Enum\\USB\\{vid_pid}")
instance = winreg.EnumKey(key, 0)
winreg.CloseKey(key)
path = f"\\\\?\\USB#{vid_pid}#{instance}#{GUID}"
print(f"Path: {path}")

class SETUP(ctypes.Structure):
    _fields_ = [("RequestType", ctypes.c_ubyte),
                ("Request",     ctypes.c_ubyte),
                ("Value",       ctypes.c_ushort),
                ("Index",       ctypes.c_ushort),
                ("Length",      ctypes.c_ushort)]

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

def build_packet(cmd, payload=b''):
    length = len(payload) + 4
    header = struct.pack('<I', 2) + struct.pack('<H', length) + bytes([cmd])
    buf = header + payload + b'\x00\x00\x00\x00'
    crc = jamcrc(buf)
    return header + payload + struct.pack('>I', crc)

def open_device():
    hf = kernel32.CreateFileW(path, 0xC0000000, 0x03, None, 3, 0x40000080, None)
    if hf == ctypes.c_void_p(-1).value:
        raise RuntimeError(f"CreateFile failed: {kernel32.GetLastError()}")
    hw = ctypes.c_void_p()
    if not winusb.WinUsb_Initialize(hf, ctypes.byref(hw)):
        kernel32.CloseHandle(hf)
        raise RuntimeError(f"WinUsb_Initialize failed: {kernel32.GetLastError()}")
    return hf, hw

def set_cfg(hw, cfg):
    setup   = SETUP(0x00, 0x09, cfg, 0, 0)
    xferred = ctypes.c_ulong()
    ok  = winusb.WinUsb_ControlTransfer(hw, setup, None, 0, ctypes.byref(xferred), None)
    err = kernel32.GetLastError()
    print(f"SET_CONFIGURATION({cfg}): ok={ok} err={err}")
    return ok

def try_write(hw, ep, data):
    buf = (ctypes.c_ubyte * len(data))(*data)
    n   = ctypes.c_ulong()
    ok  = winusb.WinUsb_WritePipe(hw, ep, buf, len(data), ctypes.byref(n), None)
    err = kernel32.GetLastError()
    print(f"  WritePipe(EP=0x{ep:02X}): ok={ok} written={n.value} err={err}")
    return ok, n.value

def try_read(hw, ep, size=65536):
    buf = (ctypes.c_ubyte * size)()
    n   = ctypes.c_ulong()
    ok  = winusb.WinUsb_ReadPipe(hw, ep, buf, size, ctypes.byref(n), None)
    err = kernel32.GetLastError()
    print(f"  ReadPipe(EP=0x{ep:02X}): ok={ok} n={n.value} err={err}")
    if n.value:
        data = bytes(buf[:n.value])
        print(f"  Data: {data.hex()}")
        print(f"  ASCII: {''.join(chr(b) if 32<=b<127 else '.' for b in data)}")
        return data
    return None

pkt = build_packet(0x03)
print(f"Packet: {pkt.hex()}")

# Strategy 1: SET_CONFIG then reopen WinUSB on same file handle
print("\n=== Strategy 1: SET_CONFIG(3) then reopen WinUSB ===")
hf, hw = open_device()
set_cfg(hw, 3)
winusb.WinUsb_Free(hw)
time.sleep(0.3)
hw2 = ctypes.c_void_p()
winusb.WinUsb_Initialize(hf, ctypes.byref(hw2))
for ep in [0x82, 0x03]:
    t = ctypes.c_ulong(3000)
    winusb.WinUsb_SetPipePolicy(hw2, ep, 3, 4, ctypes.byref(t))
ok, n = try_write(hw2, 0x03, pkt)
if ok and n:
    time.sleep(0.2)
    try_read(hw2, 0x82)
winusb.WinUsb_Free(hw2)
kernel32.CloseHandle(hf)

# Strategy 2: No SET_CONFIG, just open and write all EPs
print("\n=== Strategy 2: No SET_CONFIG, try all EPs ===")
time.sleep(0.5)
hf, hw = open_device()
for ep in [0x82, 0x03, 0x81, 0x02]:
    t = ctypes.c_ulong(3000)
    winusb.WinUsb_SetPipePolicy(hw, ep, 3, 4, ctypes.byref(t))
for out_ep, in_ep in [(0x03, 0x82), (0x02, 0x81)]:
    print(f"\n  EP OUT=0x{out_ep:02X} IN=0x{in_ep:02X}")
    ok, n = try_write(hw, out_ep, pkt)
    if ok and n:
        time.sleep(0.2)
        try_read(hw, in_ep)
winusb.WinUsb_Free(hw)
kernel32.CloseHandle(hf)

# Strategy 3: SET_CONFIG then full close/reopen
print("\n=== Strategy 3: SET_CONFIG then full close/reopen ===")
time.sleep(0.5)
hf, hw = open_device()
set_cfg(hw, 3)
winusb.WinUsb_Free(hw)
kernel32.CloseHandle(hf)
time.sleep(0.5)
hf, hw = open_device()
for ep in [0x82, 0x03]:
    t = ctypes.c_ulong(3000)
    winusb.WinUsb_SetPipePolicy(hw, ep, 3, 4, ctypes.byref(t))
ok, n = try_write(hw, 0x03, pkt)
if ok and n:
    time.sleep(0.2)
    try_read(hw, 0x82)
winusb.WinUsb_Free(hw)
kernel32.CloseHandle(hf)

print("\nDone.")