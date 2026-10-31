"""Interrupt the U-Boot BOOTP loop and leave the board at the Zynq prompt.
Does not modify environment and does not boot Linux."""
import sys
import time
import serial

ser = serial.Serial("COM11", 115200, timeout=0.2)
time.sleep(0.3)
ser.reset_input_buffer()
for _ in range(8):
    ser.write(b"\x03")
    time.sleep(0.4)
ser.write(b"\r\n")
time.sleep(0.7)
ser.write(b"echo UBOOT_PROMPT_READY\r\n")

buf = bytearray()
t0 = time.time()
while time.time() - t0 < 8:
    chunk = ser.read(4096)
    if chunk:
        buf += chunk
ser.close()
sys.stdout.buffer.write(bytes(buf[-2500:]))
print("\n=== captured %d bytes ===" % len(buf))
