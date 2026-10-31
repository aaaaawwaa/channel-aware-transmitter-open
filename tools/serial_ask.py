"""Send one command on the serial console and print the reply.
Usage: python serial_ask.py "uptime" [timeout_sec]"""
import sys
import time

import serial

ser = serial.Serial("COM11", 115200, timeout=0.2)
time.sleep(0.3)
ser.reset_input_buffer()
cmd = sys.argv[1]
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
ser.write(b"\r\n")
time.sleep(0.5)
ser.write(cmd.encode() + b"\r\n")
buf = bytearray()
t0 = time.time()
while time.time() - t0 < dur:
    chunk = ser.read(4096)
    if chunk:
        buf += chunk
ser.close()
sys.stdout.buffer.write(bytes(buf[-1500:]))
print("\n=== asked: %s ===" % cmd)
