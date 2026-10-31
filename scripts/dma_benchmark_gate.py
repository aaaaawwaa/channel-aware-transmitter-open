#!/usr/bin/env python3
"""Run the Stage5 M2 DMA benchmark measurement gate over the bridge UART."""
import re
import sys
import time

import serial


BAUD = 115200
READY = "AC820-AWG bridge ready"
DMA_BENCH_BYTES = 0x00100000
FIFO_DEPTH = 4096
DMA_BENCH_STATUS_ACTIVE = 1 << 31
DMA_BENCH_STATUS_DONE = 1 << 30
DMA_BENCH_STATUS_TLAST = 1 << 29
DMA_BENCH_STATUS_ERROR = 1 << 28
DMA_BENCH_STATUS_COUNT_MASK = 0x001FFFFF

BENCH_RE = re.compile(
    r"^DMA BENCH PASS BYTES (?P<bytes>\d+) CRC (?P<crc>[0-9A-F]{8}) "
    r"STATUS (?P<status>[0-9A-F]{8}) TICKS (?P<ticks>\d+) "
    r"RATE_MBPS_X1000 (?P<rate>\d+) FIRST_VALID_CYCLE (?P<first>\d+) "
    r"FIFO_MIN (?P<min>\d+) FIFO_MAX (?P<max>\d+)"
    r"(?: DDR_PP_TIMELINE .*)?$"
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


def parse_benchmark(line):
    match = BENCH_RE.fullmatch(line or "")
    if not match:
        raise SystemExit("M2 benchmark response invalid: %r" % line)
    values = {key: int(value, 16) if key in ("crc", "status") else int(value)
              for key, value in match.groupdict().items()}
    if values["bytes"] != DMA_BENCH_BYTES:
        raise SystemExit("M2 benchmark byte count invalid: %r" % values["bytes"])
    status = values["status"]
    if not status & DMA_BENCH_STATUS_ACTIVE:
        raise SystemExit("M2 benchmark status is not active: %08X" % status)
    if not status & DMA_BENCH_STATUS_DONE:
        raise SystemExit("M2 benchmark status is not done: %08X" % status)
    if not status & DMA_BENCH_STATUS_TLAST:
        raise SystemExit("M2 benchmark status has no TLAST: %08X" % status)
    if status & DMA_BENCH_STATUS_ERROR:
        raise SystemExit("M2 benchmark status has error: %08X" % status)
    if status & DMA_BENCH_STATUS_COUNT_MASK != DMA_BENCH_BYTES:
        raise SystemExit("M2 benchmark byte count status invalid: %08X" % status)
    first_valid_cycle = values["first"]
    fifo_min = values["min"]
    fifo_max = values["max"]
    if first_valid_cycle <= 0:
        raise SystemExit("M2 benchmark first_valid_cycle missing: %d" % first_valid_cycle)
    if fifo_min < 0 or fifo_min > fifo_max or fifo_max > FIFO_DEPTH:
        raise SystemExit("M2 benchmark FIFO range invalid: %d..%d" % (fifo_min, fifo_max))
    if values["ticks"] <= 0 or values["rate"] <= 0:
        raise SystemExit("M2 benchmark timing invalid: ticks=%d rate=%d" %
                         (values["ticks"], values["rate"]))
    return values


def main():
    port = sys.argv[1] if len(sys.argv) == 2 else "COM11"
    with serial.Serial(port, BAUD, timeout=0.1) as ser:
        pending = bytearray()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if read_line(ser, pending, 0.2) == READY:
                break
        ser.reset_input_buffer()
        pending.clear()
        line = send_line(ser, pending, "SET DMA BENCH", 20.0)
        values = parse_benchmark(line)
        print("M2 DMA benchmark gate PASS")
        print(line)
        print("FIRST_VALID_CYCLE %d FIFO_MIN %d FIFO_MAX %d" %
              (values["first"], values["min"], values["max"]))


if __name__ == "__main__":
    main()
