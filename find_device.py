"""Find Accessport V3 USB device path - run this first."""
import ctypes
import ctypes.wintypes as wintypes
import subprocess
import re

kernel32 = ctypes.WinDLL('kernel32')

VID = "1A84"
PID = "0121"
GUID = "{dee824ef-729b-4a0e-9c14-b7117d33a817}"

print("=== Accessport V3 Device Finder ===\n")

# Method 1: Registry scan for USB device instance
print("[1] Checking registry for VID_1A84&PID_0121...")
try:
    import winreg
    usb_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Enum\USB")
    i = 0
    while True:
        try:
            subkey_name = winreg.EnumKey(usb_key, i)
            if f"VID_{VID}&PID_{PID}" in subkey_name.upper():
                print(f"    Found: {subkey_name}")
                dev_key = winreg.OpenKey(usb_key, subkey_name)
                j = 0
                while True:
                    try:
                        instance = winreg.EnumKey(dev_key, j)
                        print(f"    Instance ID: {instance}")

                        # Try to read device parameters
                        try:
                            param_key = winreg.OpenKey(
                                dev_key, f"{instance}\\Device Parameters")
                            ki = 0
                            while True:
                                try:
                                    name, val, _ = winreg.EnumValue(param_key, ki)
                                    if isinstance(val, str):
                                        print(f"      {name} = {val}")
                                    ki += 1
                                except OSError:
                                    break
                        except OSError:
                            pass

                        # Build the device path
                        path = f"\\\\?\\USB#VID_{VID}&PID_{PID}#{instance}#{GUID}"
                        print(f"    Trying path: {path}")

                        # Test if we can open it
                        hFile = kernel32.CreateFileW(
                            path,
                            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
                            0x01 | 0x02,  # FILE_SHARE_READ | FILE_SHARE_WRITE
                            None, 3,  # OPEN_EXISTING
                            0x80 | 0x40000000,  # NORMAL | OVERLAPPED
                            None
                        )
                        if hFile != ctypes.c_void_p(-1).value:
                            print(f"    *** OPENED SUCCESSFULLY! ***")
                            kernel32.CloseHandle(hFile)
                            print(f"\n{'='*60}")
                            print(f"USE THIS PATH:")
                            print(f"  python ap_unmarry.py --probe --device-path \"{path}\"")
                            print(f"{'='*60}")
                        else:
                            err = kernel32.GetLastError()
                            print(f"    CreateFile error: {err}")

                            # Also try lowercase vid/pid
                            path2 = f"\\\\?\\usb#vid_{VID.lower()}&pid_{PID.lower()}#{instance}#{GUID}"
                            hFile2 = kernel32.CreateFileW(
                                path2, 0x80000000 | 0x40000000,
                                0x01 | 0x02, None, 3,
                                0x80 | 0x40000000, None
                            )
                            if hFile2 != ctypes.c_void_p(-1).value:
                                print(f"    *** OPENED with lowercase path! ***")
                                kernel32.CloseHandle(hFile2)
                                print(f"\n{'='*60}")
                                print(f"USE THIS PATH:")
                                print(f"  python ap_unmarry.py --probe --device-path \"{path2}\"")
                                print(f"{'='*60}")
                            else:
                                print(f"    Lowercase path error: {kernel32.GetLastError()}")

                        j += 1
                    except OSError:
                        break
            i += 1
        except OSError:
            break
except Exception as e:
    print(f"    Error: {e}")

# Method 2: SetupDi with multiple GUIDs
print("\n[2] SetupDi enumeration...")
try:
    import uuid
    setupapi = ctypes.WinDLL('setupapi')

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    DIGCF_ALLCLASSES = 0x04

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("ClassGuid", ctypes.c_byte * 16),
            ("DevInst", ctypes.c_ulong),
            ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("InterfaceClassGuid", ctypes.c_byte * 16),
            ("Flags", ctypes.c_ulong),
            ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
        ]

    # Try the Cobb GUID and also the generic USB GUID
    guids_to_try = [
        ("{dee824ef-729b-4a0e-9c14-b7117d33a817}", "Cobb WinUSB"),
        ("{a5dcbf10-6530-11d2-901f-00c04fb951ed}", "USB Device"),
        ("{88bae032-5a81-49f0-bc3d-a4ff138216d6}", "WinUSB Generic"),
    ]

    for guid_str, guid_name in guids_to_try:
        guid_bytes = uuid.UUID(guid_str).bytes_le
        guid_arr = (ctypes.c_byte * 16)(*guid_bytes)

        hDevInfo = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid_arr), None, None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
        )

        if hDevInfo == ctypes.c_void_p(-1).value:
            continue

        iface_data = SP_DEVICE_INTERFACE_DATA()
        iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)

        idx = 0
        while True:
            if not setupapi.SetupDiEnumDeviceInterfaces(
                hDevInfo, None, ctypes.byref(guid_arr), idx,
                ctypes.byref(iface_data)
            ):
                break

            # Get required size
            required = ctypes.c_ulong(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(iface_data),
                None, 0, ctypes.byref(required), None
            )

            # Allocate and get detail
            buf_size = required.value
            buf = ctypes.create_string_buffer(buf_size)
            # Set cbSize for SP_DEVICE_INTERFACE_DETAIL_DATA_W
            # On 64-bit: 8, on 32-bit: 6
            ptr_size = ctypes.sizeof(ctypes.c_void_p)
            if ptr_size == 8:
                ctypes.memmove(buf, ctypes.c_ulong(8), 4)
            else:
                ctypes.memmove(buf, ctypes.c_ulong(6), 4)

            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(iface_data),
                buf, buf_size, ctypes.byref(required), None
            ):
                # Device path starts at offset 4
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                if VID.lower() in path.lower() and PID.lower() in path.lower():
                    print(f"    [{guid_name}] Found: {path}")

                    hFile = kernel32.CreateFileW(
                        path, 0x80000000 | 0x40000000,
                        0x01 | 0x02, None, 3,
                        0x80 | 0x40000000, None
                    )
                    if hFile != ctypes.c_void_p(-1).value:
                        print(f"    *** OPENS SUCCESSFULLY! ***")
                        kernel32.CloseHandle(hFile)
                        print(f"\n{'='*60}")
                        print(f"USE THIS PATH:")
                        print(f"  python ap_unmarry.py --probe --device-path \"{path}\"")
                        print(f"{'='*60}")
                    else:
                        print(f"    CreateFile error: {kernel32.GetLastError()}")

            idx += 1

        setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)

except Exception as e:
    print(f"    Error: {e}")

# Method 3: pnputil / wmic fallback
print("\n[3] System device list (pnputil)...")
try:
    result = subprocess.run(
        ['pnputil', '/enum-devices', '/connected'],
        capture_output=True, text=True, timeout=10
    )
    lines = result.stdout.split('\n')
    found = False
    for i, line in enumerate(lines):
        if '1A84' in line.upper() or '0121' in line.upper():
            # Print surrounding context
            start = max(0, i-3)
            end = min(len(lines), i+5)
            for j in range(start, end):
                print(f"    {lines[j].rstrip()}")
            print()
            found = True
    if not found:
        print("    No VID_1A84 or PID_0121 found in connected devices")
        print("    Is the Accessport plugged in and powered on?")
except Exception as e:
    print(f"    Error: {e}")

# Method 4: Check Device Manager via PowerShell
print("\n[4] PowerShell device query...")
try:
    ps_cmd = (
        "Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -like '*1A84*' -or $_.InstanceId -like '*0121*' } | "
        "Select-Object Status, Class, FriendlyName, InstanceId | "
        "Format-List"
    )
    result = subprocess.run(
        ['powershell', '-Command', ps_cmd],
        capture_output=True, text=True, timeout=15
    )
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("    No matching PnP devices found")
except Exception as e:
    print(f"    Error: {e}")

print("\n" + "="*60)
print("If no path was found above, check:")
print("  1. Is the Accessport connected via USB and powered on?")
print("  2. Does Device Manager show it (possibly with yellow !)?")
print("  3. Is the WinUSB driver installed? (Use Zadig if needed)")
print("  4. Try: Device Manager → View → Show hidden devices")
print("="*60)
