#!/usr/bin/env python3
"""Run the AC820 DDR capacity and alias gate over the bridge UART."""
import re
import sys
import time

import serial


BAUD = 115200
READY = "AC820-AWG bridge ready"
CAPACITY_RE = re.compile(
    r"DDR CAPACITY PASS SIZE (?P<size>[0-9]+)MiB "
    r"ALIAS512 (?P<alias>YES|NO) BOUNDARY (?P<boundary>PASS|SKIPPED)"
)


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


def validate_result(result):
    match = CAPACITY_RE.fullmatch(result or "")
    if not match:
        raise SystemExit("DDR capacity gate failed: %r" % result)
    size = int(match.group("size"))
    alias = match.group("alias")
    boundary = match.group("boundary")
    if size not in (512, 1024):
        raise SystemExit("DDR capacity size invalid: %r" % result)
    if alias == "YES" and (size != 512 or boundary != "SKIPPED"):
        raise SystemExit("DDR capacity alias result invalid: %r" % result)
    if alias == "NO" and (size != 1024 or boundary != "PASS"):
        raise SystemExit("DDR capacity boundary result invalid: %r" % result)
    return result


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
        result = validate_result(send_line(ser, pending, "SET DDR CAPACITY TEST", 10.0))
        print("DDR capacity gate PASS")
        print(result)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
