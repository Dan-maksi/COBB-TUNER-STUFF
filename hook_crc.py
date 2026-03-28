"""
Hook APManager to capture CRC computation and WritePipe calls.

Requirements:
  pip install frida frida-tools

Usage:
  1. Start APManager (let it load but don't need device connected)
  2. Run: python hook_crc.py
  3. Connect the Accessport device (or it may already be communicating)
  4. Watch the output - it will show what data the CRC covers
  5. Press Ctrl+C to stop
"""

import frida
import sys
import time

JS_HOOK = """
'use strict';

// VA addresses from binary analysis
var imageBase = Process.enumerateModules()[0].base;
console.log("[*] APManager base: " + imageBase);

// CRC update function: VA 0x007D62C0
// Signature: thiscall, ecx=this, [ebp+8]=data_ptr, [ebp+0xc]=length
// Reads init from [this+4], updates [this+4] with result
var crcUpdateVA = imageBase.add(0x3D62C0);  // file offset, not VA
// Actually need RVA: VA 0x7D62C0 - imageBase 0x400000 = RVA 0x3D62C0
// But Frida uses actual loaded base. Let me compute properly.

// The binary has imageBase=0x400000 in PE header, so RVA = VA - 0x400000
var crcUpdate = imageBase.add(0x7D62C0 - 0x400000);
console.log("[*] CRC update func at: " + crcUpdate);

// WinUsb_WritePipe is called indirectly through [0xC289D8]
// Let's hook the WritePipe wrapper at VA 0x7E4B10
var writePipeWrapper = imageBase.add(0x7E4B10 - 0x400000);
console.log("[*] WritePipe wrapper at: " + writePipeWrapper);

// ReadPipe wrapper at VA 0x7E4A90
var readPipeWrapper = imageBase.add(0x7E4A90 - 0x400000);

// Hook CRC update
var crcCallCount = 0;
Interceptor.attach(crcUpdate, {
    onEnter: function(args) {
        // thiscall: ecx = this
        this.thisPtr = this.context.ecx;
        this.dataPtr = ptr(this.context.esp.add(4).readU32());  // [esp+4] = first arg after ret addr... 
        // Actually for thiscall with push ebp/mov ebp,esp:
        // [ebp+8] = arg1 (data ptr), [ebp+0xc] = arg2 (length)
        // But at entry, before prologue: [esp+4]=arg1, [esp+8]=arg2
        var sp = this.context.esp;
        this.dataPtr = ptr(sp.add(4).readU32());
        this.dataLen = sp.add(8).readU32();
        this.initCRC = this.thisPtr.add(4).readU32();
        
        var dataBytes = this.dataPtr.readByteArray(Math.min(this.dataLen, 128));
        
        crcCallCount++;
        console.log("\\n[CRC #" + crcCallCount + "] init=0x" + this.initCRC.toString(16).padStart(8,'0') +
                    " len=" + this.dataLen);
        console.log("  data: " + hexdump(dataBytes, {length: Math.min(this.dataLen, 64), header: false}));
    },
    onLeave: function(retval) {
        var resultCRC = this.thisPtr.add(4).readU32();
        console.log("  result CRC = 0x" + resultCRC.toString(16).padStart(8,'0'));
    }
});

// Hook WritePipe wrapper
Interceptor.attach(writePipeWrapper, {
    onEnter: function(args) {
        // thiscall: ecx=this (WinUSBDevice object)
        // Need to figure out args. The wrapper at 0x7E4B10:
        // push ebp; mov ebp,esp; ... 
        // args on stack: [ebp+8]=buffer, [ebp+0xc]=length (or similar)
        var sp = this.context.esp;
        // After CALL instruction pushes return address:
        // [esp+0] = return address
        // [esp+4] = arg1 (buffer ptr)
        // [esp+8] = arg2 (length)
        this.bufPtr = ptr(sp.add(4).readU32());
        this.bufLen = sp.add(8).readU32();
        
        if (this.bufLen > 0 && this.bufLen < 10000) {
            var bufData = this.bufPtr.readByteArray(Math.min(this.bufLen, 256));
            console.log("\\n[WRITE EP] len=" + this.bufLen);
            console.log("  " + hexdump(bufData, {length: Math.min(this.bufLen, 128), header: false}));
        }
    }
});

// Hook ReadPipe wrapper
Interceptor.attach(readPipeWrapper, {
    onLeave: function(retval) {
        // Check if data was read
        var bytesRead = retval.toInt32();
        if (bytesRead > 0) {
            console.log("\\n[READ EP] " + bytesRead + " bytes");
        }
    }
});

console.log("[*] Hooks installed. Waiting for USB traffic...");
"""

def on_message(message, data):
    if message['type'] == 'send':
        print(message['payload'])
    elif message['type'] == 'error':
        print(f"[ERROR] {message['description']}")
    else:
        print(f"[MSG] {message}")

def main():
    print("Looking for APManager process...")

    try:
        session = frida.attach("APManager.exe")
    except frida.ProcessNotFoundError:
        print("APManager.exe not found! Make sure it's running.")
        print("Start APManager first, then run this script.")
        sys.exit(1)

    print("Attached to APManager!")
    script = session.create_script(JS_HOOK)
    script.on('message', on_message)
    script.load()

    print("\n" + "="*60)
    print("  Monitoring CRC computation and USB writes")
    print("  Connect/reconnect the Accessport to trigger traffic")
    print("  Press Ctrl+C to stop")
    print("="*60 + "\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDetaching...")
        session.detach()
        print("Done.")

if __name__ == '__main__':
    main()