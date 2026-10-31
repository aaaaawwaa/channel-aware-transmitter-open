#!/usr/bin/env python3
"""Run the AC820 MM2S DMA/FIFO smoke gate over the bridge UART."""
import re
import sys
import time

import serial


BAUD = 115200
READY = "AC820-AWG bridge ready"
EXPECTED = "DMA TEST PASS PATTERN PASS BACKPRESSURE PASS RECOVERY PASS"


def read_line(ser, pending, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        newline = pending.find(b"\n")
        if newline >= 0:
            line = bytes(pending[:newline])
            del pending[:newline + 1]
            return line.decode(errors="replace").strip()
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            pending.extend(chunk)
    return None


def send_line(ser, pending, command, timeout=5.0):
    ser.write((command + "\r\n").encode())
    return read_line(ser, pending, timeout)


def main():
    port = sys.argv[1] if len(sys.argv) == 2 else "COM11"
    ser = serial.Serial(port, BAUD, timeout=0.1)
    pending = bytearray()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if read_line(ser, pending, 0.2) == READY:
                break
        ser.reset_input_buffer()
        pending.clear()
        result = send_line(ser, pending, "SET DMA TEST", 15.0)
        if result != EXPECTED:
            raise SystemExit("DMA gate failed: %r (expected %r)" % (result, EXPECTED))
        status = send_line(ser, pending, "GET DMA", 2.0)
        if not status or not re.fullmatch(
                r"DMA BASE [0-9A-F]{8} BUFFER [0-9A-F]{8} BYTES [0-9]+ "
                r"STATUS [0-9A-F]{8} STATE READY", status):
            raise SystemExit("DMA status invalid: %r" % status)
        print("DMA gate PASS")
        print(status)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
