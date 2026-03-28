"""
Hook the REAL CRC function (vtable[2] at RVA 0x465960) and dump the runtime table.
Also hooks the wrapper (vtable[1] at RVA 0x465940).
python hook_realcrc.py
"""

import frida
import sys
import time

JS = r"""
var apBase = Process.findModuleByName("APManager.exe").base;

function hexBytes(buf, len) {
    var b = [];
    for (var i = 0; i < Math.min(len, 300); i++) {
        b.push(('0' + buf.add(i).readU8().toString(16)).slice(-2));
    }
    return b.join(' ');
}

// CRC table at VA 0xBDD938, RVA = 0x7DD938
var crcTableRVA = 0x7DD938;
var crcTable = apBase.add(crcTableRVA);
send("CRC table at " + crcTable);

// Read first 16 and last entries of the live table
send("Live CRC table:");
for (var i = 0; i < 16; i++) {
    var val = crcTable.add(i * 4).readU32();
    send("  T[" + i + "] = 0x" + ('00000000' + val.toString(16)).slice(-8));
}
var t255 = crcTable.add(255 * 4).readU32();
send("  T[255] = 0x" + ('00000000' + t255.toString(16)).slice(-8));

// Dump entire table for offline analysis
var tableHex = [];
for (var i = 0; i < 256; i++) {
    var val = crcTable.add(i * 4).readU32();
    tableHex.push(('00000000' + val.toString(16)).slice(-8));
}
send("FULL_TABLE:" + tableHex.join(','));

// Hook vtable[2] - the actual CRC loop at RVA 0x465960
var crcFunc = apBase.add(0x465960);
send("CRC function at " + crcFunc);
send("First bytes: " + hexBytes(crcFunc, 16));

Interceptor.attach(crcFunc, {
    onEnter: function(args) {
        var dataPtr = this.context.esp.add(4).readPointer();
        var lenPtr = this.context.esp.add(8).readPointer();
        var len = lenPtr.readU32();
        
        this.dataPtr = dataPtr;
        this.len = len;
        
        if (len > 0 && len < 10000) {
            send("CRC_COMPUTE data=" + dataPtr + " len=" + len);
            send("  input: " + hexBytes(dataPtr, len));
        }
    },
    onLeave: function(retval) {
        var result = retval.toInt32() >>> 0;
        send("  result: 0x" + ('00000000' + result.toString(16)).slice(-8));
    }
});

// Also hook vtable[1] wrapper at RVA 0x465940
var wrapFunc = apBase.add(0x465940);
send("CRC wrapper at " + wrapFunc);

Interceptor.attach(wrapFunc, {
    onEnter: function(args) {
        var bufStruct = this.context.esp.add(4).readPointer();
        try {
            var begin = bufStruct.readPointer();
            var end = bufStruct.add(4).readPointer();
            var len = end.sub(begin).toInt32();
            send("CRC_WRAPPER begin=" + begin + " end=" + end + " len=" + len);
            if (len > 0 && len < 10000) {
                send("  buffer: " + hexBytes(begin, len));
            }
        } catch(e) {
            send("CRC_WRAPPER error: " + e);
        }
    },
    onLeave: function(retval) {
        var result = retval.toInt32() >>> 0;
        send("  wrapper result: 0x" + ('00000000' + result.toString(16)).slice(-8));
    }
});

send("Ready - do firmware check");
"""

def on_message(message, data):
    if message['type'] == 'send':
        payload = message['payload']
        if payload.startswith('FULL_TABLE:'):
            entries = payload[11:].split(',')
            with open('runtime_crc_table.txt', 'w') as f:
                for i, h in enumerate(entries):
                    f.write(f"T[{i:3d}] = 0x{h.upper()}\n")
            print(f"[Saved {len(entries)} table entries to runtime_crc_table.txt]")
        else:
            print(payload)
    elif message['type'] == 'error':
        print(f"[ERROR] {message['description']}")

def main():
    print("Attaching...")
    try:
        session = frida.attach("APManager.exe")
    except frida.ProcessNotFoundError:
        print("Not found!")
        sys.exit(1)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()

    print("Ctrl+C to stop\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        session.detach()

if __name__ == '__main__':
    main()