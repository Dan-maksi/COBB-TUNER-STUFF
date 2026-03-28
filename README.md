# COBB Accessport V3 (AP3-SUB-003) — RE Dump

Reverse engineering dump for the COBB Accessport V3. Acquired as e-waste, married to a totaled vehicle with no way to unmarry it through normal channels. This repo is the full collection of scripts used to get into it via JTAG and USB.

Findings were posted publicly on automotive forums. This is for research and recovery purposes. Don't use this on hardware you don't own.

I'm not going to actively support this — it was an exercise that has run its course. These are just the scripts I made, dumped here so anyone else interested has a jump start.

---

## Hardware & Software Requirements

**Hardware**
- COBB Accessport V3 (AP3-SUB-003) — VID `0x1A84`, PID `0x0121`
- FT232H breakout board (for JTAG)
- A Windows machine (the USB scripts use `winusb.dll` and `winreg` — Windows only)

**Software**
- Python 3
- [Frida](https://frida.re) (`pip install frida-tools`) — required for `hook_usb.py`, `hook_crc.py`, `capture_usb.py`
- OpenOCD with TCL port enabled on `localhost:6666` — required for `jtag_exfil.py`
- APManager.exe (COBB's official Windows software) — required for the Frida scripts
- [Zadig](https://zadig.akeo.ie) — install WinUSB driver for the device if it doesn't show up

---

## Script Overview

### `find_device.py` — Run this first

Confirms the device is visible and finds its USB path before you try anything else. Uses four methods: registry scan, SetupDi enumeration, pnputil, and a PowerShell PnP query. Prints a ready-to-use device path string if it finds it.

```
python find_device.py
```

If nothing comes back, check Device Manager. You may need to install the WinUSB driver via Zadig before the device is accessible outside of APManager.

---

### `ap_connect.py` — Low-level USB connection test

Tests whether you can open the device and send a raw COBB packet (cmd `0x03`). Tries three different connection strategies: SET_CONFIG then reinit on the same handle, no SET_CONFIG with all endpoint combinations, and SET_CONFIG with a full close/reopen cycle. Good for verifying your USB setup is working before going further.

```
python ap_connect.py
```

---

### `ap_tool_v3.py` — Main interactive tool

The primary tool for talking to the device directly over USB. Connects via `WinUsb_Initialize`, forces USB configuration 3, and gives you an interactive menu:

- **Option 1** — Read device settings (cmd `0x03`). The marriage state lives at byte offset 36 of the response payload: `0x0A` = married (installed), `0x00` = not installed (free), `0x09` = recovery mode.
- **Option 2** — Device info (cmd `0x28`). Dumps firmware version, serial, and other strings.
- **Option e** — Read raw EEPROM via the device's sysfs path (`/sys/devices/platform/s3c2410-i2c/i2c-0/0-0053/eeprom`).
- **Option f** — File read probe (cmd `0x27`). Tries multiple payload formats (Boost-serialized, raw + offset, raw + offset + length) to find one that works for a given path.
- **Option x** — Binary exfiltration. Reads `/ap-app/bin/ap-app`, `/ap-app/lib/libvehicle.so`, `/ap-app/bin/writeeeprom`, or a custom path in 4KB chunks and saves them to disk.
- **Option r** — Raw command. Sends any arbitrary command byte with a hex payload.

```
python ap_tool_v3.py
```

> ⚠️ **APManager must not be running** when you use this script. APManager holds the WinUSB handle and you'll get a `CreateFile` error if it's open at the same time.

---

### `hook_usb.py` — Frida CRC hook / USB traffic logger

Attaches to `APManager.exe` via Frida and hooks two functions in the APManager binary: the CRC wrapper at RVA `0x465940` and the actual JAMCRC loop at RVA `0x465960`. Reads and dumps the live CRC table to `runtime_crc_table.txt` and logs every packet being assembled with its command byte and data. Good starting point for understanding the packet format.

```
python hook_usb.py
# APManager.exe must already be running with the device connected
```

---

### `hook_crc.py` — Standalone CRC function hook

Similar to `hook_usb.py` but focused purely on the CRC vtable hooks. Used earlier in the process to verify the CRC algorithm (JAMCRC / inverted CRC-32) and confirm which APManager binary offsets were actually firing.

```
python hook_crc.py
```

---

### `capture_usb.py` — Frida firmware intercept (the important one)

This is the firmware intercept script. It hooks `winusb.dll!WinUsb_WritePipe` and `winusb.dll!WinUsb_ReadPipe` directly (v1 tried to hook APManager's own wrappers — they didn't fire; v2 goes straight to winusb.dll). It also hooks the BlowfishStream decrypt function at RVA `0x465C90` to capture decrypted firmware before it hits USB.

**The firmware version spoof:** When APManager sends a cmd `0x04` (FwVersion) request and the device responds, this script intercepts the response and rewrites the version string to `1.5.0.0-10000`. APManager sees a very old version and immediately offers a firmware update. This triggers the full firmware update flow over USB, which is what exposes the higher-level USB commands that `ap_tool_v3.py` relies on. **You need to run this script and let a firmware update complete before commands like cmd `0x27` (file read) and EEPROM access will work.**

`BLOCK_WRITES = True` by default — in this mode it intercepts and logs everything but does NOT actually send firmware data to the device (safe for observation). Set it to `False` when you're ready to let the update go through.

```
# 1. Plug in the Accessport
# 2. Launch APManager.exe
# 3. Run:
python capture_usb.py
# 4. APManager should show "Firmware Update Available"
# 5. Click Update
# 6. Ctrl+C when done — output goes to ~/Desktop/cobb_dump/
```

> ⚠️ **Do not run `hook_usb.py` and `capture_usb.py` at the same time.** Only one Frida script can be attached to APManager at once.

---

### `jtag_exfil.py` — JTAG file exfiltrator via OpenOCD

Completely independent of USB. Uses OpenOCD's TCL port (`localhost:6666`) to inject a small ARM shellcode into RAM (`0x43CDE400`) while the device is halted in the ap-app process's userspace context. The shellcode calls `open`/`lseek`/`read`/`close` ARM syscalls directly to read files off the device filesystem 256 bytes at a time, writing the data to an OpenOCD-readable output buffer.

Setup: get OpenOCD running and connected to the device via JTAG (FT232H), then run the script. It will try to catch the ap-app process at a known thunk breakpoint (`0x40B1B174`). If that doesn't fire, it falls back to halting in userspace by polling the PC register.

```
python jtag_exfil.py               # interactive mode
python jtag_exfil.py /proc/mtd     # dump single file
```

Interactive commands:
- `dump <path> [output]` — dump any arbitrary path
- `mtd` — `/proc/mtd`
- `mounts` — `/proc/mounts`
- `all` — key files: procfs info, writeeeprom, readeeprom, libvehicle.so, libap.so, gadget scripts
- `explore` — extended file list: all ap-app binaries, libraries, shell scripts, EEPROM, boot scripts
- `settings` — raw NAND MTD partitions (mtd2–mtd5)
- `installmap` — everything related to the install/unmarry flow: install/uninstall scripts, subaru_flash, subaru_identify, map2rom, PIC sysfs, user.rom, prev_install

All output goes to the `dumps/` directory.

---

### `cobb_firmware_grab.py` — Firmware fetcher

Pulls firmware from COBB's update servers. Used to get a clean reference copy for comparison against what was dumped from the device.

---

### `coddreader.py` — Map file reader

Reads and parses `.codd` map files (COBB's proprietary ECU map format). Used during analysis to understand what the installed maps actually contain.

---

### `pic_flash.py` — PIC firmware flasher

Interfaces with the PIC microcontroller (`/sys/dx_hw_pic/`) that handles OBD communication. Used to inspect and reflash the PIC firmware separately from the main AP firmware.

---

## Recommended Workflow

```
1. find_device.py         — confirm the device is visible
2. hook_usb.py            — learn the packet format with APManager running
3. capture_usb.py         — spoof firmware version, trigger update, let it flash
                            (this is what unlocks the USB command set)
4. ap_tool_v3.py          — read marriage state, exfil EEPROM + binaries
5. jtag_exfil.py          — pull any file you can't reach over USB
```

---

## Key Technical Notes

- All COBB packets use magic `0x00000002` at offset 0, a 2-byte length at offset 4, command byte at offset 6, payload starting at offset 7, and a big-endian JAMCRC (inverted CRC-32) at the last 4 bytes.
- The marriage state is stored in NAND and reflected in the cmd `0x03` response at payload offset 36.
- The device runs Linux on an ARM926EJ-S (Samsung S3C-family SoC). The filesystem lives in JFFS2 partitions.
- BlowfishStream is used to encrypt firmware in transit between APManager and the device.
- The EEPROM at I2C address `0x53` holds device identity and marriage-related data.
- The `dumps/` and `dump*/` folders in this repo contain raw binary output from the exfiltration runs.

---

## Disclaimer

This was done on hardware I own, acquired as e-waste. Responsible disclosure was completed before any of this was posted publicly.
