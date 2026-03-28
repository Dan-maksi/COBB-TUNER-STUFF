#!/usr/bin/env python3
"""
COBB Accessport V3 Firmware Grabber & Analyzer
===============================================
Downloads firmware directly from COBB's update server, bypassing APManager.
No device connection needed - just internet access.

Usage:
    python cobb_firmware_grab.py              # Download firmware
    python cobb_firmware_grab.py --analyze    # Analyze already-downloaded firmware
    python cobb_firmware_grab.py --proxy      # Set up MITM proxy for APManager
"""

import os
import sys
import hashlib
import struct
import json
import argparse
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# ============================================================================
# CONSTANTS from APManager.exe reverse engineering
# ============================================================================

BASE_URL = "http://software.cobbtuning.com/Update/AccessportManager/"
API_URL = "https://api.cobbtuning.com"

# Version file names (constructed from binary strings at 0x5CD8D8 and 0x66C260)
VERSION_FILES = [
    "version_win_v3.txt",          # Release version file
    "version_win_v3_beta.txt",     # Beta version file  
    "version_win.txt",             # Generic version (fallback)
    "version.txt",                 # Bare minimum fallback
]

# V3 firmware image files (from binary at 0x64F28A)
# Two known variants for V3:
V3_FIRMWARE_FILES_A = [
    "ap-app.img",       # Application
    "ap-data.img",      # Data partition (CONTAINS SETTINGS!)
    "ap-data2.img",     # Secondary data
    "kernel.img",       # Linux kernel
    "rootfs2.img",      # Root filesystem
    "loader.img",       # Bootloader
]

V3_FIRMWARE_FILES_B = [
    "ap-app.img",       # Application
    "ap-data.img",      # Data partition (CONTAINS SETTINGS!)
    "bootstream.img",   # i.MX28 .sb boot image (KEY FILE for USB recovery!)
    "rootfs.img",       # Root filesystem
]

# Also need these metadata files
METADATA_FILES = [
    "MD5SUMS",          # File checksums
    "manifest",         # Update manifest
    "manifest2",        # Secondary manifest (seen in binary)
]

# All unique files to try
ALL_FILES = list(set(
    V3_FIRMWARE_FILES_A + V3_FIRMWARE_FILES_B + METADATA_FILES + VERSION_FILES
))

# i.MX28 .sb file magic
SB_MAGIC = b'STMP'  # First 4 bytes of .sb files

# Output directory
OUTPUT_DIR = Path("cobb_firmware")


def download_file(url, dest_path, verbose=True):
    """Download a file from URL, return True if successful."""
    try:
        req = Request(url, headers={
            'User-Agent': 'AccessportManager/3.1.12',  # Mimic APManager
        })
        if verbose:
            print(f"  Trying: {url}")
        
        response = urlopen(req, timeout=30)
        data = response.read()
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(data)
        
        md5 = hashlib.md5(data).hexdigest()
        print(f"  ✓ Downloaded: {dest_path.name} ({len(data):,} bytes, MD5: {md5})")
        return True
        
    except HTTPError as e:
        if verbose:
            print(f"  ✗ HTTP {e.code}: {url}")
        return False
    except URLError as e:
        if verbose:
            print(f"  ✗ Connection error: {e.reason}")
        return False
    except Exception as e:
        if verbose:
            print(f"  ✗ Error: {e}")
        return False


def try_download_firmware():
    """Try various URL patterns to download firmware from COBB's server."""
    
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    print("=" * 70)
    print("COBB Accessport V3 Firmware Grabber")
    print("=" * 70)
    
    # Phase 1: Get version file
    print("\n[Phase 1] Downloading version information...")
    version_data = None
    
    for vf in VERSION_FILES:
        url = BASE_URL + vf
        dest = OUTPUT_DIR / vf
        if download_file(url, dest):
            version_data = dest.read_text(errors='replace')
            print(f"\n  Version file contents:\n  {'-'*40}")
            for line in version_data.strip().split('\n'):
                print(f"  {line}")
            print(f"  {'-'*40}")
            break
    
    if not version_data:
        print("\n  Could not download version files from base URL.")
        print("  Trying alternate URL patterns...")
    
    # Phase 2: Try direct firmware download from base URL
    print("\n[Phase 2] Trying firmware files from base URL...")
    downloaded = []
    
    for fname in ALL_FILES:
        dest = OUTPUT_DIR / fname
        if dest.exists():
            print(f"  → Already exists: {fname}")
            downloaded.append(fname)
            continue
            
        # Try several URL patterns
        urls_to_try = [
            f"{BASE_URL}{fname}",
            f"{BASE_URL}v3/{fname}",
            f"{BASE_URL}V3/{fname}",
            f"{BASE_URL}AP3/{fname}",
            f"{BASE_URL}firmware/{fname}",
            f"{BASE_URL}latest/{fname}",
        ]
        
        # If we got a version string, try version-based paths
        if version_data:
            # Extract version-like strings
            import re
            versions = re.findall(r'(\d+\.\d+\.\d+[\.\-]\d+)', version_data)
            for ver in versions:
                urls_to_try.append(f"{BASE_URL}{ver}/{fname}")
                urls_to_try.append(f"{BASE_URL}v{ver}/{fname}")
        
        for url in urls_to_try:
            if download_file(url, dest, verbose=True):
                downloaded.append(fname)
                break
    
    # Phase 3: Try the API endpoint
    print("\n[Phase 3] Trying API endpoint...")
    api_urls = [
        f"{API_URL}/api/v1/accessports",
        f"{API_URL}/api/v1/installs",
    ]
    for url in api_urls:
        try:
            req = Request(url, headers={
                'User-Agent': 'AccessportManager/3.1.12',
                'Accept': 'application/json',
            })
            print(f"  Trying: {url}")
            response = urlopen(req, timeout=15)
            data = response.read()
            dest = OUTPUT_DIR / f"api_{url.split('/')[-1]}.json"
            dest.write_bytes(data)
            print(f"  ✓ Got API response: {len(data)} bytes")
            try:
                j = json.loads(data)
                print(f"  Response: {json.dumps(j, indent=2)[:500]}")
            except:
                print(f"  Raw: {data[:200]}")
        except Exception as e:
            print(f"  ✗ {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("DOWNLOAD SUMMARY")
    print("=" * 70)
    
    if downloaded:
        print(f"\nSuccessfully downloaded {len(downloaded)} files:")
        for f in downloaded:
            path = OUTPUT_DIR / f
            size = path.stat().st_size if path.exists() else 0
            print(f"  {f:30s} {size:>12,} bytes")
    else:
        print("\nNo files downloaded from direct URLs.")
        print("\nFallback options:")
        print("  1. Use the MITM proxy approach: python cobb_firmware_grab.py --proxy")
        print("  2. Check APManager's local cache (see below)")
        print("  3. Use Wireshark to capture APManager's download traffic")
    
    # Phase 4: Check local APManager cache
    print("\n[Phase 4] Checking for local APManager firmware cache...")
    check_local_cache()
    
    return downloaded


def check_local_cache():
    """Check if APManager has cached firmware locally."""
    
    # APManager stores downloads in AppData
    # Path construction: /Globals/AppStoragePath + /APMUpdates
    possible_paths = []
    
    # Windows paths
    appdata = os.environ.get('APPDATA', '')
    localappdata = os.environ.get('LOCALAPPDATA', '')
    userprofile = os.environ.get('USERPROFILE', '')
    
    if appdata:
        possible_paths.extend([
            Path(appdata) / "COBB" / "Accessport Manager" / "APMUpdates",
            Path(appdata) / "COBB" / "APMUpdates",
            Path(appdata) / "AccessportManager" / "APMUpdates",
        ])
    if localappdata:
        possible_paths.extend([
            Path(localappdata) / "COBB" / "Accessport Manager" / "APMUpdates",
            Path(localappdata) / "COBB" / "APMUpdates",
        ])
    if userprofile:
        possible_paths.extend([
            Path(userprofile) / "Documents" / "COBB" / "APMUpdates",
            Path(userprofile) / "COBB" / "APMUpdates",
        ])
    
    # Also check common installation paths
    for drive in ['C:', 'D:']:
        possible_paths.extend([
            Path(drive) / "COBB" / "APMUpdates",
            Path(drive) / "Program Files" / "COBB" / "Accessport Manager" / "APMUpdates",
            Path(drive) / "Program Files (x86)" / "COBB" / "Accessport Manager" / "APMUpdates",
        ])
    
    print("\n  Checking these locations for cached firmware:")
    found_any = False
    for p in possible_paths:
        if p.exists():
            print(f"\n  ★ FOUND: {p}")
            found_any = True
            for f in sorted(p.rglob('*')):
                if f.is_file():
                    print(f"    {f.relative_to(p):40s} {f.stat().st_size:>12,} bytes")
        else:
            print(f"    ✗ {p}")
    
    if not found_any:
        print("\n  No local cache found. You can also manually search with:")
        print('  dir /s /b "%APPDATA%\\*APMUpdates*" 2>nul')
        print('  dir /s /b "%LOCALAPPDATA%\\*COBB*" 2>nul')
        print('  dir /s /b "C:\\*bootstream.img" 2>nul')
        print('  dir /s /b "C:\\*ap-data.img" 2>nul')


def analyze_firmware():
    """Analyze downloaded firmware files."""
    
    print("=" * 70)
    print("FIRMWARE ANALYSIS")
    print("=" * 70)
    
    if not OUTPUT_DIR.exists():
        print(f"\nNo firmware directory found at: {OUTPUT_DIR}")
        print("Run without --analyze first to download firmware.")
        return
    
    for f in sorted(OUTPUT_DIR.iterdir()):
        if not f.is_file():
            continue
            
        data = f.read_bytes()
        print(f"\n{'─'*50}")
        print(f"File: {f.name}")
        print(f"Size: {len(data):,} bytes")
        print(f"MD5:  {hashlib.md5(data).hexdigest()}")
        
        # Check for .sb magic (i.MX28 Secure Boot image)
        if data[:4] == SB_MAGIC or data[:4] == b'STMP':
            print(f"Type: ★ i.MX28 Secure Boot (.sb) image!")
            print(f"  This is the bootstream we need for USB recovery!")
            analyze_sb_header(data)
        
        # Check for Linux filesystem signatures
        elif data[:2] == b'\x1f\x8b':
            print(f"Type: gzip compressed data")
            print(f"  Likely a compressed filesystem image")
        
        elif data[:4] == b'hsqs' or data[0:4] == b'\x68\x73\x71\x73':
            print(f"Type: SquashFS filesystem")
        
        elif data[0x438:0x43A] == b'\x53\xEF':
            print(f"Type: ext2/ext3/ext4 filesystem")
        
        elif b'UBI#' in data[:64]:
            print(f"Type: UBI (Unsorted Block Image) - NAND filesystem")
        
        # Check for JFFS2
        elif data[:2] == b'\x85\x19' or data[:2] == b'\x19\x85':
            print(f"Type: JFFS2 filesystem")
        
        # Check for settings-like content
        if b'ecu_install_state' in data:
            print(f"  ★★★ CONTAINS ecu_install_state! This is the data partition!")
            # Find and show context
            idx = data.find(b'ecu_install_state')
            start = max(0, idx - 100)
            end = min(len(data), idx + 200)
            context = data[start:end]
            readable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in context)
            print(f"  Context: ...{readable}...")
        
        if b'Not installed' in data:
            print(f"  ★ Contains 'Not installed' string")
        if b'Installed' in data and b'Not installed' not in data:
            print(f"  Contains 'Installed' string (without 'Not')")
        
        # Show first 64 bytes hex dump
        print(f"  Header hex: {data[:64].hex()}")
        print(f"  Header asc: {''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:64])}")


def analyze_sb_header(data):
    """Parse i.MX28 .sb file header."""
    if len(data) < 96:
        print(f"  Too small for .sb header")
        return
    
    # SB header format (from Freescale documentation)
    try:
        magic = data[0:4]          # 'STMP'
        major = struct.unpack('<H', data[4:6])[0]
        minor = struct.unpack('<H', data[6:8])[0]
        flags = struct.unpack('<H', data[8:10])[0]
        image_blocks = struct.unpack('<I', data[12:16])[0]
        first_boot_tag = struct.unpack('<I', data[16:20])[0]
        first_boot_section_id = struct.unpack('<I', data[20:24])[0]
        key_count = struct.unpack('<H', data[24:26])[0]
        header_blocks = struct.unpack('<H', data[28:30])[0]
        section_count = struct.unpack('<H', data[30:32])[0]
        
        print(f"  SB Version: {major}.{minor}")
        print(f"  Flags: 0x{flags:04X}")
        print(f"  Image blocks: {image_blocks} ({image_blocks * 16} bytes)")
        print(f"  Key count: {key_count}")
        print(f"  Header blocks: {header_blocks}")
        print(f"  Section count: {section_count}")
        
        # Check if encrypted
        if key_count > 0:
            print(f"  ⚠ Image appears to be ENCRYPTED ({key_count} keys)")
        else:
            print(f"  ✓ Image appears to be UNENCRYPTED")
            
    except Exception as e:
        print(f"  Failed to parse .sb header: {e}")


def setup_proxy():
    """Set up a simple HTTP proxy to intercept APManager firmware downloads."""
    
    print("=" * 70)
    print("MITM PROXY SETUP FOR APMANAGER")
    print("=" * 70)
    print("""
This sets up a proxy that intercepts APManager's firmware download requests
and saves the files locally while passing them through to the device.

SETUP STEPS:
─────────────────────────────────────────────────────────────────────

Method 1: System Proxy (easiest)
  1. Run this proxy: python cobb_firmware_grab.py --proxy
  2. Set Windows proxy: Settings → Network → Proxy → Manual
     Address: 127.0.0.1  Port: 8888
  3. Launch APManager and trigger a firmware check/update
  4. Files will be saved to ./cobb_firmware/
  5. Don't forget to disable the proxy when done!

Method 2: Hosts file redirect
  1. Edit C:\\Windows\\System32\\drivers\\etc\\hosts (as Admin)
  2. Add: 127.0.0.1 software.cobbtuning.com
  3. Run this proxy: python cobb_firmware_grab.py --proxy
  4. Launch APManager
  5. Remove the hosts entry when done!

Method 3: Wireshark capture (no proxy needed)
  1. Start Wireshark, filter: http.host contains "cobbtuning"
  2. Launch APManager, let it check for updates
  3. Export captured HTTP objects: File → Export Objects → HTTP
  4. Look for .img files and version files

Method 4: APManager Alternate URL (from binary RE)
  APManager reads /Updates/UseAlternateUpdateURL and 
  /Updates/AlternateUpdateURL from its config.
  You might be able to set these in APManager's config file
  to redirect downloads to a local server.
  
  Check for config at:
    %APPDATA%\\COBB\\Accessport Manager\\config.ini
    %APPDATA%\\COBB\\Accessport Manager\\settings.cfg
""")
    
    # Start simple proxy
    print("\nStarting proxy server on port 8888...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import urllib.request
        
        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Reconstruct full URL
                if self.path.startswith('http'):
                    url = self.path
                else:
                    url = f"http://{self.headers['Host']}{self.path}"
                
                print(f"[PROXY] GET {url}")
                
                try:
                    req = urllib.request.Request(url)
                    for key, val in self.headers.items():
                        if key.lower() not in ('host', 'proxy-connection'):
                            req.add_header(key, val)
                    
                    response = urllib.request.urlopen(req, timeout=60)
                    data = response.read()
                    
                    # Save interesting files
                    fname = url.split('/')[-1]
                    if fname and ('.' in fname):
                        save_path = OUTPUT_DIR / fname
                        save_path.parent.mkdir(parents=True, exist_ok=True)
                        save_path.write_bytes(data)
                        print(f"[SAVED] {save_path} ({len(data):,} bytes)")
                    
                    # Forward response
                    self.send_response(response.status)
                    for key, val in response.headers.items():
                        self.send_header(key, val)
                    self.end_headers()
                    self.wfile.write(data)
                    
                except Exception as e:
                    print(f"[ERROR] {e}")
                    self.send_error(502, str(e))
            
            def do_CONNECT(self):
                # For HTTPS tunneling
                self.send_error(501, "HTTPS CONNECT not supported - use HTTP")
            
            def log_message(self, format, *args):
                pass  # Suppress default logging
        
        server = HTTPServer(('127.0.0.1', 8888), ProxyHandler)
        OUTPUT_DIR.mkdir(exist_ok=True)
        print(f"Proxy listening on http://127.0.0.1:8888")
        print(f"Saving captured files to: {OUTPUT_DIR.absolute()}")
        server.serve_forever()
        
    except ImportError:
        print("Error: Required modules not available")
    except KeyboardInterrupt:
        print("\nProxy stopped.")
    except OSError as e:
        print(f"Could not start proxy: {e}")
        print("Port 8888 may be in use. Try closing other applications.")


def print_next_steps(downloaded):
    """Print what to do with downloaded firmware."""
    
    print("\n" + "=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    
    has_bootstream = any('bootstream' in f for f in downloaded)
    has_apdata = any('ap-data' in f for f in downloaded)
    
    if has_bootstream:
        print("""
★ BOOTSTREAM.IMG FOUND - USB RECOVERY PATH AVAILABLE!

This is the i.MX28 .sb boot image. You can use it to boot the device
via USB recovery mode (NAND short method):

  1. Short NAND pin 29 (IO0) to pin 33 (VSS) during power-on
  2. Device enters USB recovery: VID_15A2 PID_004F
  3. Send bootstream.img using imx_usb_loader:
     
     imx_usb_loader bootstream.img
     
  4. Device should boot into normal Linux
  5. From there, access the settings partition

Get imx_usb_loader from:
  https://github.com/boundarydevices/imx_usb_loader
""")
    
    if has_apdata:
        print("""
★ AP-DATA.IMG FOUND - SETTINGS PARTITION!

This likely contains the default /settings file with 
ecu_install_state = "Not installed". If we can flash just this
partition to the device, it should unmarry it.

Analyze it with:
  python cobb_firmware_grab.py --analyze
""")
    
    if not downloaded:
        print("""
No firmware files downloaded directly. Try these approaches:

  1. PROXY METHOD (recommended):
     python cobb_firmware_grab.py --proxy
     Then set your Windows proxy to 127.0.0.1:8888
     Then open APManager and go to the Updates tab
     
  2. LOCAL CACHE:
     If you've EVER updated an Accessport V3 with APManager,
     the firmware may be cached on your PC. Search for:
       dir /s /b "%APPDATA%\\*bootstream*" 2>nul
       dir /s /b "%APPDATA%\\*ap-data*" 2>nul  
       dir /s /b "C:\\*APMUpdates*" 2>nul
     
  3. WIRESHARK:
     Capture HTTP traffic while APManager checks for updates.
     Filter: http.host contains "cobbtuning"
     
  4. APManager CONFIG HACK:
     APManager supports /Updates/UseAlternateUpdateURL setting.
     If you can find its config file, you may be able to redirect
     downloads to a local server where you control the files.
     
  5. COMMUNITY:
     Search for COBB AP V3 firmware on forums - other people may
     have the files cached or backed up.
""")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='COBB AP V3 Firmware Grabber')
    parser.add_argument('--analyze', action='store_true', help='Analyze downloaded firmware')
    parser.add_argument('--proxy', action='store_true', help='Start MITM proxy')
    parser.add_argument('--cache', action='store_true', help='Only check local cache')
    args = parser.parse_args()
    
    if args.analyze:
        analyze_firmware()
    elif args.proxy:
        setup_proxy()
    elif args.cache:
        check_local_cache()
    else:
        downloaded = try_download_firmware()
        print_next_steps(downloaded)
