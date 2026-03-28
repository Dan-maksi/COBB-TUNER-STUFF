"""
COBB Firmware Intercept v2
==========================

v1 hooked APManager's WritePipe/ReadPipe wrappers — they didn't fire.
v2 hooks WinUsb_WritePipe and WinUsb_ReadPipe in winusb.dll directly.
Also uses the CRC function hook (RVA 0x465940) which IS confirmed working.

Strategy:
  1. Hook winusb.dll!WinUsb_WritePipe — see ALL USB writes
  2. Hook winusb.dll!WinUsb_ReadPipe — see ALL USB reads, MODIFY responses
  3. When cmd 0x04 response comes back → spoof version to trigger update
  4. When cmd 0x03 response comes back → spoof version fields there too
  5. Capture all data during firmware update flow
  6. Optionally block firmware writes to keep device safe

Usage:
  1. Plug in Accessport (normal mode)
  2. Launch APManager.exe
  3. Run: python fw_intercept_v2.py
  4. APManager should show "Firmware Update Available"
  5. Click Update
  6. Ctrl+C when done → check ~/Desktop/cobb_dump/
"""

import frida
import sys
import time
import os
import struct
from datetime import datetime

# ── Configuration ─────────────────────────────────────
DUMP_DIR = os.path.expanduser("~/Desktop/cobb_dump")
BLOCK_WRITES = True       # True = don't send firmware data to device (SAFE)
SPOOF_VERSION = "1.5.0.0-10000"  # Old version to trigger update

JS_SCRIPT = r"""
'use strict';

var apBase = Process.findModuleByName("APManager.exe").base;
send("[*] APManager base: " + apBase);

var BLOCK_WRITES = """ + str(BLOCK_WRITES).lower() + r""";
var SPOOF_VERSION = '""" + SPOOF_VERSION + r"""';

// ═══════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════

function hexBytes(buf, len) {
    var b = [];
    for (var i = 0; i < Math.min(len, 300); i++) {
        b.push(('0' + buf.add(i).readU8().toString(16)).slice(-2));
    }
    if (len > 300) b.push('...+' + (len - 300));
    return b.join(' ');
}

function readAscii(buf, maxLen) {
    var s = '';
    for (var i = 0; i < maxLen; i++) {
        var c = buf.add(i).readU8();
        if (c === 0) break;
        if (c >= 32 && c < 127) s += String.fromCharCode(c);
    }
    return s;
}

// JAMCRC for rebuilding packets after modification
var CRC_TABLE = new Array(256);
(function() {
    for (var i = 0; i < 256; i++) {
        var c = i;
        for (var j = 0; j < 8; j++) {
            c = (c & 1) ? ((c >>> 1) ^ 0xEDB88320) : (c >>> 1);
        }
        CRC_TABLE[i] = c;
    }
})();

function jamcrc(buf, len) {
    var crc = 0xFFFFFFFF;
    for (var i = 0; i < len; i++) {
        crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buf.add(i).readU8()) & 0xFF];
    }
    return crc;
}

function writeCrcBE(buf, offset, crc) {
    buf.add(offset).writeU8((crc >>> 24) & 0xFF);
    buf.add(offset + 1).writeU8((crc >>> 16) & 0xFF);
    buf.add(offset + 2).writeU8((crc >>> 8) & 0xFF);
    buf.add(offset + 3).writeU8(crc & 0xFF);
}

// ═══════════════════════════════════════════════
// STATE TRACKING
// ═══════════════════════════════════════════════

var lastTxCmd = -1;        // Last command we sent
var updateMode = false;     // True when firmware update is in progress
var txSeq = 0;
var rxSeq = 0;
var spoofCount = 0;

var CMD_NAMES = {
    0x03: "DeviceSettings", 0x04: "FwVersion", 0x05: "Reboot",
    0x12: "FileInfo", 0x1A: "UINav", 0x1F: "Unknown1F",
    0x20: "DownloadReq", 0x21: "DownloadData",
    0x22: "UploadReq", 0x23: "UploadData",
    0x26: "ListDir", 0x28: "DeviceInfo",
    0x31: "SettingsField"
};

function cmdName(cmd) {
    return CMD_NAMES[cmd] || ("0x" + ('00' + cmd.toString(16)).slice(-2));
}


// ═══════════════════════════════════════════════
// HOOK: winusb.dll!WinUsb_WritePipe
// Sees ALL data going to the device
// ═══════════════════════════════════════════════

var pWritePipe = Module.findExportByName("winusb.dll", "WinUsb_WritePipe");
if (pWritePipe) {
    send("[*] WinUsb_WritePipe at " + pWritePipe);
    
    Interceptor.attach(pWritePipe, {
        onEnter: function(args) {
            // WinUsb_WritePipe(handle, pipeId, buffer, bufferLen, &bytesWritten, overlapped)
            this.pipeId = args[1].toInt32() & 0xFF;
            this.buf = args[2];
            this.bufLen = args[3].toInt32();
            
            if (this.bufLen < 7 || this.pipeId === 0) return;
            
            txSeq++;
            
            // Parse COBB packet header
            var magic = this.buf.readU32();
            if (magic === 2) {
                var pktLen = this.buf.add(4).readU16();
                var cmd = this.buf.add(6).readU8();
                lastTxCmd = cmd;
                
                send(">>> TX #" + txSeq + " pipe=0x" + this.pipeId.toString(16) + 
                     " cmd=" + cmdName(cmd) + " (" + this.bufLen + "B)");
                
                // Detect firmware update commands
                if (cmd === 0x22 || cmd === 0x23 || cmd === 0x0B || cmd === 0x0C) {
                    if (!updateMode) {
                        send("");
                        send("[!] ★★★ FIRMWARE UPDATE FLOW DETECTED ★★★");
                        send("");
                        updateMode = true;
                    }
                }
                
                // Log payload for interesting commands
                if (this.bufLen > 11 && this.bufLen < 500) {
                    send("    payload: " + hexBytes(this.buf.add(7), Math.min(this.bufLen - 11, 100)));
                }
            } else {
                send(">>> TX #" + txSeq + " pipe=0x" + this.pipeId.toString(16) + 
                     " (raw " + this.bufLen + "B)");
            }
            
            // ── Capture firmware data during update ──
            if (updateMode && this.bufLen > 100) {
                var chunk = this.buf.readByteArray(this.bufLen);
                send({type: "fw_tx", seq: txSeq, size: this.bufLen, cmd: lastTxCmd}, chunk);
            }
            
            // ── Block firmware writes if BLOCK_WRITES ──
            if (BLOCK_WRITES && updateMode) {
                var cmd2 = (magic === 2) ? this.buf.add(6).readU8() : -1;
                if (cmd2 === 0x23 || cmd2 === 0x0C) {
                    send("[BLOCK] ★ Blocking firmware data packet (cmd " + cmdName(cmd2) + ", " + this.bufLen + "B)");
                    // Overwrite buffer with a minimal no-op to prevent actual write
                    // Set length to 0 via args
                    args[3] = ptr(0);
                }
            }
        }
    });
} else {
    send("[!] WinUsb_WritePipe not found in winusb.dll!");
}


// ═══════════════════════════════════════════════
// HOOK: winusb.dll!WinUsb_ReadPipe
// Sees ALL data coming from the device
// This is where we SPOOF the version
// ═══════════════════════════════════════════════

var pReadPipe = Module.findExportByName("winusb.dll", "WinUsb_ReadPipe");
if (pReadPipe) {
    send("[*] WinUsb_ReadPipe at " + pReadPipe);
    
    Interceptor.attach(pReadPipe, {
        onEnter: function(args) {
            // WinUsb_ReadPipe(handle, pipeId, buffer, bufferLen, &bytesRead, overlapped)
            this.pipeId = args[1].toInt32() & 0xFF;
            this.buf = args[2];
            this.bufLen = args[3].toInt32();
            this.pBytesRead = args[4];
        },
        onLeave: function(retval) {
            if (!retval.toInt32()) return;  // Failed read
            
            var bytesRead = this.pBytesRead.readU32();
            if (bytesRead < 7) return;
            
            rxSeq++;
            var magic = this.buf.readU32();
            if (magic !== 2) {
                send("<<< RX #" + rxSeq + " (raw " + bytesRead + "B)");
                return;
            }
            
            var pktLen = this.buf.add(4).readU16();
            var status = this.buf.add(6).readU8();
            var isOk = (status === 0x01);
            
            send("<<< RX #" + rxSeq + " resp_for=" + cmdName(lastTxCmd) + 
                 " " + (isOk ? "OK" : "ERR") + " (" + bytesRead + "B)");
            
            // ── Capture during update ──
            if (updateMode && bytesRead > 20) {
                var chunk = this.buf.readByteArray(bytesRead);
                send({type: "fw_rx", seq: rxSeq, size: bytesRead, cmd: lastTxCmd}, chunk);
            }
            
            // ═══════════════════════════════════════
            // VERSION SPOOF — cmd 0x04 response
            // ═══════════════════════════════════════
            if (lastTxCmd === 0x04 && isOk && bytesRead > 15) {
                var payStart = 7;
                var payEnd = bytesRead - 4;
                
                send("[VERSION] Scanning FwVersion response (" + (payEnd - payStart) + "B payload)");
                send("[VERSION] Raw: " + hexBytes(this.buf.add(payStart), Math.min(payEnd - payStart, 100)));
                
                // Find version string pattern "1.7." or "1.6." etc
                var spoofed = this._spoofVersion(this.buf, payStart, payEnd, bytesRead);
                if (spoofed) {
                    send("[VERSION] ★★★ FwVersion SPOOFED → '" + SPOOF_VERSION + "' ★★★");
                } else {
                    send("[VERSION] Could not find version pattern to spoof");
                }
            }
            
            // ═══════════════════════════════════════
            // VERSION SPOOF — cmd 0x03 DeviceSettings
            // Also contains active_fw_version and 
            // latest_fw_version fields
            // ═══════════════════════════════════════
            if (lastTxCmd === 0x03 && isOk && bytesRead > 50) {
                send("[SETTINGS] DeviceSettings response (" + bytesRead + "B)");
                send("[SETTINGS] " + hexBytes(this.buf.add(7), Math.min(bytesRead - 11, 150)));
                
                // Find and spoof ALL version strings in settings
                var count = this._spoofAllVersions(this.buf, 7, bytesRead - 4, bytesRead);
                if (count > 0) {
                    send("[SETTINGS] Spoofed " + count + " version string(s) in DeviceSettings");
                }
            }
            
            // Log all responses for debugging
            if (bytesRead > 11 && bytesRead < 500) {
                var payloadHex = hexBytes(this.buf.add(7), Math.min(bytesRead - 11, 80));
                send("    payload: " + payloadHex);
                
                // Try ASCII decode
                var ascii = readAscii(this.buf.add(7), Math.min(bytesRead - 11, 80));
                if (ascii.length > 3) {
                    send("    ascii: " + ascii);
                }
            }
        },
        
        // ── Helper: spoof a single version string ──
        _spoofVersion: function(buf, searchStart, searchEnd, totalLen) {
            // Scan for "1.X." pattern (version strings)
            for (var i = searchStart; i < searchEnd - 4; i++) {
                var b0 = buf.add(i).readU8();
                var b1 = buf.add(i+1).readU8();
                var b3 = buf.add(i+3).readU8();
                
                // Match "1.N." where N is a digit
                if (b0 === 0x31 && b1 === 0x2E && b3 === 0x2E) {
                    var b2 = buf.add(i+2).readU8();
                    if (b2 >= 0x30 && b2 <= 0x39) {
                        // Found version start. Find end.
                        var end = i;
                        while (end < searchEnd) {
                            var c = buf.add(end).readU8();
                            if (c < 0x20 || c >= 0x7F) break;
                            end++;
                        }
                        var origVer = readAscii(buf.add(i), end - i);
                        var origLen = end - i;
                        
                        send("[SPOOF] Found '" + origVer + "' at offset " + i + " (" + origLen + " chars)");
                        
                        // Check for Boost string length prefix at i-4
                        var hasLenPrefix = false;
                        var lenPrefixOffset = i - 4;
                        if (lenPrefixOffset >= 7) {
                            var prefixVal = buf.add(lenPrefixOffset).readU32();
                            if (prefixVal === origLen) {
                                hasLenPrefix = true;
                                send("[SPOOF] Boost length prefix confirmed at offset " + lenPrefixOffset);
                            }
                        }
                        
                        // Write spoofed version
                        if (SPOOF_VERSION.length <= origLen) {
                            // Update length prefix if present
                            if (hasLenPrefix) {
                                buf.add(lenPrefixOffset).writeU32(SPOOF_VERSION.length);
                            }
                            
                            // Write new version string
                            for (var j = 0; j < SPOOF_VERSION.length; j++) {
                                buf.add(i + j).writeU8(SPOOF_VERSION.charCodeAt(j));
                            }
                            // Pad remainder with nulls
                            for (var k = SPOOF_VERSION.length; k < origLen; k++) {
                                buf.add(i + k).writeU8(0x00);
                            }
                            
                            // Recalculate CRC (big-endian, last 4 bytes)
                            var newCrc = jamcrc(buf, totalLen - 4);
                            writeCrcBE(buf, totalLen - 4, newCrc);
                            
                            spoofCount++;
                            return true;
                        } else {
                            send("[SPOOF] Spoof version too long! " + SPOOF_VERSION.length + " > " + origLen);
                        }
                    }
                }
            }
            return false;
        },
        
        // ── Helper: spoof ALL version strings in a response ──
        _spoofAllVersions: function(buf, searchStart, searchEnd, totalLen) {
            var count = 0;
            var pos = searchStart;
            
            while (pos < searchEnd - 4) {
                var b0 = buf.add(pos).readU8();
                var b1 = buf.add(pos+1).readU8();
                var b3 = buf.add(pos+3).readU8();
                
                if (b0 === 0x31 && b1 === 0x2E && b3 === 0x2E) {
                    var b2 = buf.add(pos+2).readU8();
                    if (b2 >= 0x30 && b2 <= 0x39) {
                        // Found version
                        var end = pos;
                        while (end < searchEnd) {
                            var c = buf.add(end).readU8();
                            if (c < 0x20 || c >= 0x7F) break;
                            end++;
                        }
                        var origVer = readAscii(buf.add(pos), end - pos);
                        var origLen = end - pos;
                        
                        if (SPOOF_VERSION.length <= origLen) {
                            // Check for length prefix
                            var lp = pos - 4;
                            if (lp >= 7) {
                                var pv = buf.add(lp).readU32();
                                if (pv === origLen) {
                                    buf.add(lp).writeU32(SPOOF_VERSION.length);
                                }
                            }
                            
                            for (var j = 0; j < SPOOF_VERSION.length; j++) {
                                buf.add(pos + j).writeU8(SPOOF_VERSION.charCodeAt(j));
                            }
                            for (var k = SPOOF_VERSION.length; k < origLen; k++) {
                                buf.add(pos + k).writeU8(0x00);
                            }
                            
                            send("[SPOOF] Replaced '" + origVer + "' at offset " + pos);
                            count++;
                            pos = end;
                        } else {
                            pos++;
                        }
                    } else {
                        pos++;
                    }
                } else {
                    pos++;
                }
            }
            
            // Recalc CRC once after all modifications
            if (count > 0) {
                var newCrc = jamcrc(buf, totalLen - 4);
                writeCrcBE(buf, totalLen - 4, newCrc);
            }
            
            return count;
        }
    });
} else {
    send("[!] WinUsb_ReadPipe not found in winusb.dll!");
}


// ═══════════════════════════════════════════════
// Also hook BlowfishStream decrypt (RVA 0x465C90)
// to capture raw decrypted firmware before USB
// ═══════════════════════════════════════════════

var bfDecrypt = apBase.add(0x465C90);
send("[*] BlowfishStream decrypt hook at " + bfDecrypt);

Interceptor.attach(bfDecrypt, {
    onEnter: function(args) {
        var sp = this.context.esp;
        try {
            this.arg1 = ptr(sp.add(4).readU32());
            this.arg2 = sp.add(8).readU32();
        } catch(e) {
            this.arg2 = 0;
        }
        
        if (this.arg2 > 10000) {
            send("[DECRYPT] BlowfishStream called: " + this.arg2 + " bytes input");
        }
    },
    onLeave: function(retval) {
        if (this.arg2 > 10000) {
            send("[DECRYPT] Completed: " + this.arg2 + " bytes");
            // Try to capture output from various possible locations
            try {
                var thisPtr = ptr(this.context.esi);  // 'this' often in esi after thiscall
                // std::vector<uint8_t> output might be at this+offset
                for (var off = 0x04; off <= 0x20; off += 4) {
                    try {
                        var p = thisPtr.add(off).readPointer();
                        var p2 = thisPtr.add(off + 4).readPointer();
                        var sz = p2.sub(p).toInt32();
                        if (sz > 1000 && sz < 50000000) {
                            send("[DECRYPT] Possible output at this+" + off + ": " + sz + " bytes");
                            // Check first few bytes for signatures
                            var first16 = hexBytes(p, 16);
                            send("[DECRYPT] First 16B: " + first16);
                            // Save it
                            var chunk = p.readByteArray(Math.min(sz, 20000000));
                            send({type: "decrypt", size: sz, offset: off}, chunk);
                            break;
                        }
                    } catch(e2) {}
                }
            } catch(e) {}
        }
    }
});


// ═══════════════════════════════════════════════
// ALSO hook the APManager CRC wrapper (confirmed 
// working from hook_usb.py) as a backup tracker
// ═══════════════════════════════════════════════

var crcWrapper = apBase.add(0x465940);
send("[*] CRC wrapper (backup tracker) at " + crcWrapper);

Interceptor.attach(crcWrapper, {
    onEnter: function(args) {
        try {
            var bufStruct = this.context.esp.add(4).readPointer();
            var begin = bufStruct.readPointer();
            var end = bufStruct.add(4).readPointer();
            var len = end.sub(begin).toInt32();
            
            if (len >= 7 && len < 100000) {
                var magic = begin.readU32();
                if (magic === 2) {
                    var cmd = begin.add(6).readU8();
                    send("[CRC] Building packet: cmd=" + cmdName(cmd) + " (" + len + "B)");
                }
            }
        } catch(e) {}
    }
});


send("");
send("═══════════════════════════════════════════════════════");
send("  COBB Firmware Intercept v2 — READY");
send("═══════════════════════════════════════════════════════════");
send("  Hooks: winusb.dll WritePipe + ReadPipe (direct)");
send("  Hooks: BlowfishStream decrypt");
send("  Hooks: CRC wrapper (backup tracker)");
send("  Spoof: " + SPOOF_VERSION);
send("  Block: " + BLOCK_WRITES);
send("");
send("  Waiting for USB traffic...");
send("═══════════════════════════════════════════════════════");
"""


# ── Python receiver ──

captured_tx = []
captured_rx = []
captured_decrypt = []
log_lines = []

def ensure_dump_dir():
    os.makedirs(DUMP_DIR, exist_ok=True)

def on_message(message, data):
    if message['type'] == 'send':
        payload = message['payload']

        if isinstance(payload, dict):
            chunk_type = payload.get('type', '')
            if data:
                seq = payload.get('seq', len(captured_tx) + len(captured_rx))
                size = payload.get('size', len(data))
                cmd = payload.get('cmd', -1)

                if chunk_type == 'fw_tx':
                    fn = os.path.join(DUMP_DIR, f"tx_{seq:04d}_cmd{cmd:02x}_{size}B.bin")
                    with open(fn, 'wb') as f:
                        f.write(data)
                    captured_tx.append(data)

                elif chunk_type == 'fw_rx':
                    fn = os.path.join(DUMP_DIR, f"rx_{seq:04d}_{size}B.bin")
                    with open(fn, 'wb') as f:
                        f.write(data)
                    captured_rx.append(data)

                elif chunk_type == 'decrypt':
                    idx = len(captured_decrypt)
                    fn = os.path.join(DUMP_DIR, f"decrypt_{idx:04d}_{size}B.bin")
                    with open(fn, 'wb') as f:
                        f.write(data)
                    captured_decrypt.append(data)
                    print(f"  ★ DECRYPT CAPTURED: {size:,}B → {fn}")

        elif isinstance(payload, str):
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            line = f"[{ts}] {payload}"
            print(line)
            log_lines.append(line)

    elif message['type'] == 'error':
        print(f"[ERROR] {message.get('description', str(message))}")


def save_results():
    if not any([captured_tx, captured_rx, captured_decrypt]):
        print("\n  No data captured.")
        return

    print(f"\n  {'═' * 50}")
    print(f"  CAPTURE SUMMARY")
    print(f"  {'═' * 50}")
    print(f"  TX chunks:      {len(captured_tx)}")
    print(f"  RX chunks:      {len(captured_rx)}")
    print(f"  Decrypt chunks: {len(captured_decrypt)}")

    for name, chunks in [("tx", captured_tx), ("rx", captured_rx), ("decrypt", captured_decrypt)]:
        if chunks:
            combined = b''.join(chunks)
            fn = os.path.join(DUMP_DIR, f"combined_{name}.bin")
            with open(fn, 'wb') as f:
                f.write(combined)
            print(f"  Combined {name}: {len(combined):,}B → {fn}")

    # Scan for signatures in captured data
    for name, chunks in [("TX", captured_tx), ("Decrypt", captured_decrypt)]:
        if not chunks:
            continue
        combined = b''.join(chunks)
        print(f"\n  Scanning {name} data ({len(combined):,}B) for firmware signatures...")

        for sig_name, sig_bytes in [
            ("STMP (.sb)", b'STMP'), ("ext2/3/4", b'\x53\xef'),
            ("JFFS2", b'\x85\x19'), ("squashfs", b'hsqs'),
            ("UBI", b'UBI#'), ("gzip", b'\x1f\x8b'),
            ("Linux", b'Linux'), ("root", b'root'),
            ("install_state", b'install_state'),
            ("Not installed", b'Not installed'),
            ("ecu_install", b'ecu_install'),
        ]:
            idx = combined.find(sig_bytes)
            if idx >= 0:
                ctx = combined[max(0, idx-8):idx+len(sig_bytes)+16]
                print(f"  ★ Found '{sig_name}' at offset 0x{idx:X}")
                print(f"    Context: {ctx.hex()}")

    # Save log
    fn = os.path.join(DUMP_DIR, "intercept_log.txt")
    with open(fn, 'w') as f:
        f.write('\n'.join(log_lines))
    print(f"\n  Log: {fn}")


def main():
    print("=" * 60)
    print("  COBB Firmware Intercept v2")
    print("  (Direct winusb.dll hooks)")
    print("=" * 60)
    print(f"  Spoof: {SPOOF_VERSION}")
    print(f"  Block: {BLOCK_WRITES}")
    print(f"  Dump:  {DUMP_DIR}")
    print()

    ensure_dump_dir()

    # Make sure no other Frida scripts are attached
    print("  ⚠ Make sure hook_usb.py and capture_usb.py are NOT running!")
    print("    Only ONE Frida script should be attached at a time.")
    print()

    print("  Attaching to APManager.exe...")
    try:
        session = frida.attach("APManager.exe")
    except frida.ProcessNotFoundError:
        print("  ERROR: APManager.exe not running!")
        print("  Steps: 1) Plug in AP  2) Launch APManager  3) Run this script")
        sys.exit(1)

    print("  Loading hooks...")
    script = session.create_script(JS_SCRIPT)
    script.on('message', on_message)
    script.load()

    print()
    print("  ════════════════════════════════════════")
    print("  READY — watching for USB traffic")
    print("  If APManager is already connected,")
    print("  disconnect and reconnect the AP to")
    print("  force a fresh handshake.")
    print("  ════════════════════════════════════════")
    print()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    print("\n  Stopping...")
    save_results()

    try:
        session.detach()
    except:
        pass

    print(f"\n  Files in: {DUMP_DIR}")
    print("  Done.")


if __name__ == '__main__':
    main()