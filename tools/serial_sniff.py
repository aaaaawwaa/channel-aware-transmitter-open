"""Passive serial sniffer: capture N seconds of console to a file, no writes.
Usage: python serial_sniff.py <seconds> <outfile>"""
import sys
import time

import serial

dur = float(sys.argv[1])
out_path = sys.argv[2]
ser = serial.Serial("COM11", 115200, timeout=0.2)
buf = bytearray()
t0 = time.time()
with open(out_path, "wb") as f:
    while time.time() - t0 < dur:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            f.write(chunk)
            f.flush()
ser.close()
sys.stdout.buffer.write(bytes(buf[-2000:]))
print("\n=== %d bytes -> %s ===" % (len(buf), out_path))
