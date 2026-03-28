import ctypes
import ctypes.wintypes as wintypes
import struct
import time

winusb   = ctypes.WinDLL('winusb')
kernel32 = ctypes.WinDLL('kernel32')

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ         = 0x80000000
GENERIC_WRITE        = 0x40000000
FILE_SHARE_READ      = 0x01
FILE_SHARE_WRITE     = 0x02
OPEN_EXISTING        = 3
FILE_ATTRIBUTE_NORMAL= 0x80
FILE_FLAG_OVERLAPPED = 0x40000000

INSTANCE = "0123456789.0123456789.0123456789"
PATH = f"\\\\?\\USB#VID_1A84&PID_0121#{INSTANCE}#{{dee824ef-729b-4a0e-9c14-b7117d33a817}}"

# Confirmed from pipe enumeration + binary analysis:
EP_CMD_OUT  = 0x03   # Bulk OUT 512-byte (DATA channel - what works)
EP_CMD_IN   = 0x82   # Bulk IN  512-byte (DATA channel)
EP_SMALL_OUT= 0x02   # Bulk OUT 64-byte  (CMD channel)
EP_SMALL_IN = 0x81   # Bulk IN  64-byte  (CMD channel)

class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType",          ctypes.c_uint),
        ("PipeId",            ctypes.c_ubyte),
        ("MaximumPacketSize", ctypes.c_ushort),
        ("Interval",          ctypes.c_ubyte),
    ]

def open_device():
    hFile = kernel32.CreateFileW(
        PATH, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None
    )
    if hFile == INVALID_HANDLE_VALUE:
        raise RuntimeError(f"CreateFile failed: {kernel32.GetLastError()}")
    hWinUSB = ctypes.c_void_p()
    if not winusb.WinUsb_Initialize(hFile, ctypes.byref(hWinUSB)):
        raise RuntimeError(f"WinUsb_Initialize failed: {kernel32.GetLastError()}")
    print("Device opened.")
    return hFile, hWinUSB

def set_timeout(hWinUSB, pipe_id, ms):
    val = wintypes.ULONG(ms)
    winusb.WinUsb_SetPipePolicy(hWinUSB, pipe_id, 0x03, 4, ctypes.byref(val))

def raw_write(hWinUSB, pipe_id, data):
    buf = (ctypes.c_uint8 * len(data))(*data)
    n = wintypes.ULONG(0)
    ok = winusb.WinUsb_WritePipe(hWinUSB, pipe_id, buf, len(data), ctypes.byref(n), None)
    if not ok:
        err = kernel32.GetLastError()
        print(f"  WritePipe(0x{pipe_id:02X}) error: {err}")
        return 0
    return n.value

def raw_read(hWinUSB, pipe_id, length=1033):
    buf = (ctypes.c_uint8 * length)()
    n = wintypes.ULONG(0)
    ok = winusb.WinUsb_ReadPipe(hWinUSB, pipe_id, buf, length, ctypes.byref(n), None)
    if not ok:
        err = kernel32.GetLastError()
        print(f"  ReadPipe(0x{pipe_id:02X}) error: {err}")
        return None
    return bytes(buf[:n.value])

def abort_pipe(hWinUSB, pipe_id):
    winusb.WinUsb_AbortPipe(hWinUSB, pipe_id)
    winusb.WinUsb_ResetPipe(hWinUSB, pipe_id)
    winusb.WinUsb_FlushPipe(hWinUSB, pipe_id)

# --------------------------------------------------------------------------
# Boost binary archive format (what the device speaks):
#
# From APManager.exe binary analysis:
#   - Uses boost::archive::binary_oarchive to serialize commands
#   - Objects serialized: DeviceSettings@ap, APMapMetaData, etc.
#   - Archive header: "serialization::archive" + version (uint32 LE)
#
# The simplest approach: build a raw Boost binary archive header
# and try known command strings the device might understand.
#
# Boost binary archive wire format:
#   [4 bytes] archive flags (usually 0x00000000)
#   [4 bytes] version      (usually 0x00000011 = 17)
#   [4 bytes] object count
#   ... serialized fields follow
# --------------------------------------------------------------------------

def make_boost_archive(payload_bytes):
    """Wrap payload in a minimal boost binary archive header."""
    header = struct.pack('<III',
        0x00000000,  # flags
        0x00000011,  # version 17
        0x00000001,  # 1 object
    )
    return header + payload_bytes

def make_raw_cmd(cmd_byte):
    """Try the simplest possible command: just a command byte padded to 64."""
    pkt = bytes([cmd_byte]) + bytes(63)
    return pkt

def make_length_prefixed_cmd(cmd_byte, payload=b''):
    """Format: [4-byte LE length][cmd_byte][payload] padded to packet size."""
    body = bytes([cmd_byte]) + payload
    return struct.pack('<I', len(body)) + body

# --------------------------------------------------------------------------
# From binary: the UI_ITEM strings map to internal command IDs
# Found: UI_ITEM_READ_CODES, UI_ITEM_CLEAR_CODES, UI_ITEM_INSTALLED etc.
# These are strings, not wire bytes. The wire format is Boost serialized.
#
# BUT: APManager talks to child process via pipe using these strings,
# child process translates to USB. So the USB layer might be simpler.
#
# Key binary finding at 007E4C90:
#   6a 12  = PUSH 0x12
#   6a 00  = PUSH 0
#   6a 00  = PUSH 0
#   PUSH 0xB00EA8  -> CreateFile/SetupDi call with 3 args
# This is NOT WritePipe args - it's a SetupDi/CreateFile call.
#
# The actual WritePipe at 007E4DDD:
#   PUSH [EDI]     = InterfaceHandle
#   PUSH 1         = PipeId (EP 0x01?? or alternate setting 1?)
#   PUSH 0         = Overlapped = NULL
#   PUSH EAX       = pBytesTransferred
#   PUSH 0x409     = BufferLength = 1033 bytes
#   PUSH [ESI+0x14]= pBuffer
#   PUSH 0x12      = ??? (extra arg or misread)
# --------------------------------------------------------------------------

def send_and_recv(hWinUSB, out_ep, in_ep, data, label=""):
    print(f"\n>>> {label} OUT=0x{out_ep:02X} ({len(data)} bytes): {data[:16].hex()}")
    w = raw_write(hWinUSB, out_ep, data)
    print(f"    Wrote {w} bytes")
    if w == 0:
        return None
    time.sleep(0.1)
    resp = raw_read(hWinUSB, in_ep)
    if resp:
        print(f"    Response ({len(resp)}b): {resp[:32].hex()}")
        printable = ''.join(chr(b) if 32<=b<127 else '.' for b in resp)
        print(f"    ASCII: {printable[:80]}")
    return resp

def main():
    print("=== COBB Accessport Protocol Probe ===\n")
    try:
        hFile, hWinUSB = open_device()
    except Exception as e:
        print(f"ERROR: {e}")
        return

    # Confirmed working pipe pair from last run: OUT=0x03, IN=0x82
    # Set generous timeouts
    for ep in [EP_CMD_OUT, EP_CMD_IN, EP_SMALL_OUT, EP_SMALL_IN]:
        set_timeout(hWinUSB, ep, 2000)

    # Flush any stale data first
    for ep in [EP_CMD_IN, EP_SMALL_IN]:
        abort_pipe(hWinUSB, ep)

    time.sleep(0.5)

    # -- Try 1: Single null byte (minimal probe, won't crash device)
    send_and_recv(hWinUSB, EP_CMD_OUT, EP_CMD_IN,
                  bytes(64), "NULL probe 64b")

    time.sleep(0.3)

    # -- Try 2: Single command byte variants (no framing)
    for cmd in [0x01, 0x02, 0x10, 0xFF]:
        pkt = bytes([cmd]) + bytes(1032)  # 1033 bytes total (matches 0x409)
        send_and_recv(hWinUSB, EP_CMD_OUT, EP_CMD_IN,
                      pkt[:64], f"CMD=0x{cmd:02X}")
        time.sleep(0.2)

    # -- Try 3: Length-prefixed format
    for cmd in [0x01, 0x10]:
        pkt = make_length_prefixed_cmd(cmd)
        pkt = pkt + bytes(max(0, 64 - len(pkt)))
        send_and_recv(hWinUSB, EP_CMD_OUT, EP_CMD_IN,
                      pkt, f"LenPrefix CMD=0x{cmd:02X}")
        time.sleep(0.2)

    # -- Try 4: Boost archive header only
    boost_pkt = make_boost_archive(b'')
    send_and_recv(hWinUSB, EP_CMD_OUT, EP_CMD_IN,
                  boost_pkt + bytes(max(0, 64-len(boost_pkt))),
                  "Boost archive header")

    # -- Try 5: The small CMD channel (0x02/0x81) with same packets
    print("\n\n=== TRYING SMALL CMD CHANNEL (0x02/0x81) ===")
    send_and_recv(hWinUSB, EP_SMALL_OUT, EP_SMALL_IN,
                  bytes(64), "NULL probe small")
    time.sleep(0.3)
    for cmd in [0x01, 0x10]:
        pkt = bytes([cmd]) + bytes(63)
        send_and_recv(hWinUSB, EP_SMALL_OUT, EP_SMALL_IN,
                      pkt, f"Small CMD=0x{cmd:02X}")
        time.sleep(0.2)

    winusb.WinUsb_Free(hWinUSB)
    kernel32.CloseHandle(hFile)
    print("\nDone. Unplug/replug if device is unresponsive.")

if __name__ == '__main__':
    main()