#!/usr/bin/env python3
"""Run one compile-time Stage5 PP fault mode over the bridge UART."""
import argparse
import re
import time

import serial


BAUD = 115200
READY = "AC820-AWG bridge ready"
FAULTS = {
    "DMA_ERROR": ("23", 10.0),
    "RESET_TIMEOUT": ("21", 8.0),
    "STREAM_UNDERFLOW": ("25", 10.0),
    "TIMEOUT": ("24", 8.0),
}
PP_PAYLOADS = (bytes.fromhex("A55A3CC3"), bytes.fromhex("5AA5C33C"))


def crc8_atm(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_ccitt(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def read_line(ser, pending, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        newline = pending.find(b"\n")
        if newline >= 0:
            line = bytes(pending[:newline])
            del pending[: newline + 1]
            return line.decode(errors="replace").strip()
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            pending.extend(chunk)
    return None


def send_line(ser, pending, command, timeout=5.0):
    ser.write((command + "\r\n").encode())
    line = read_line(ser, pending, timeout)
    if line != "OK":
        raise RuntimeError("%s -> %r" % (command, line))
    return line


def wait_ready(ser, pending):
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if read_line(ser, pending, min(0.5, deadline - time.monotonic())) == READY:
            return
    ser.reset_input_buffer()
    pending.clear()
    ser.write(b"GET USERBUF\r\n")
    line = read_line(ser, pending, 1.0)
    if line and line.startswith("USERBUF "):
        return
    raise RuntimeError("bridge ready banner not received")


def parse_pp_status(line):
    match = re.fullmatch(
        r"DDR PP ACTIVE ([01]) ACTIVE_BANK (NONE|[01]) NEXT_BANK (NONE|[01]) "
        r"STATE (IDLE|RUNNING|DRAIN|ERROR) ERROR ([0-9A-F]{2}) "
        r"BANK0 VALID ([01]) LENGTH ([0-9]+) CRC ([0-9A-F]{4}) "
        r"BANK1 VALID ([01]) LENGTH ([0-9]+) CRC ([0-9A-F]{4})",
        line or "",
    )
    if not match:
        return None
    fields = match.groups()
    return {
        "active": int(fields[0]),
        "active_bank": None if fields[1] == "NONE" else int(fields[1]),
        "next_bank": None if fields[2] == "NONE" else int(fields[2]),
        "state": fields[3],
        "error": int(fields[4], 16),
        "bank0": (int(fields[5]), int(fields[6]), int(fields[7], 16)),
        "bank1": (int(fields[8]), int(fields[9]), int(fields[10], 16)),
    }


def user_metadata(line):
    match = re.fullmatch(
        r"USERBUF VALID ([01]) LENGTH ([0-9]+) CRC ([0-9A-F]{4}) BANK ([01]) "
        r"UPLOAD (IDLE|RECEIVING) LOOP (ON|OFF) COMMIT_SEEN ([01]) READY ([01]) "
        r"DONE ([01]) UNDERFLOW ([01])",
        line or "",
    )
    if not match:
        raise RuntimeError("USERBUF status invalid: %r" % line)
    fields = match.groups()
    return tuple((int(fields[i], 16) if i == 2 else int(fields[i])) for i in (0, 1, 2, 3))


def pp_setup(ser, pending):
    send_line(ser, pending, "SET DIGMOD MODE OFF")
    send_line(ser, pending, "SET DIGMOD SOURCE PRBS7")
    send_line(ser, pending, "SET DIGMOD FILTER RECT")
    send_line(ser, pending, "SET DIGMOD SYMRATE 10000")
    send_line(ser, pending, "SET DDR PP CLEAR")
    for bank, payload in enumerate(PP_PAYLOADS):
        send_line(
            ser,
            pending,
            "SET DDR PP CHUNK %d 0 %s %02X"
            % (bank, payload.hex().upper(), crc8_atm(payload)),
        )
        send_line(
            ser,
            pending,
            "SET DDR PP COMMIT %d 32 %04X" % (bank, crc16_ccitt(payload)),
        )
    send_line(ser, pending, "SET DIGMOD MODE BPSK")


def wait_fault(ser, pending, expected_error, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_line = send_query(ser, pending, "GET DDR PP", 1.0)
        status = parse_pp_status(status_line)
        if status and status["state"] == "ERROR":
            if status["error"] != int(expected_error, 16):
                raise RuntimeError("unexpected fault status: %r" % status_line)
            return status
    raise RuntimeError("fault status not observed")


def send_query(ser, pending, command, timeout=2.0):
    ser.write((command + "\r\n").encode())
    line = read_line(ser, pending, timeout)
    if line is None:
        raise RuntimeError("%s timed out" % command)
    return line


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="COM11")
    parser.add_argument("--fault", required=True, choices=sorted(FAULTS))
    args = parser.parse_args(argv)

    expected_error, fault_timeout = FAULTS[args.fault]
    with serial.Serial(args.port, BAUD, timeout=0.1) as ser:
        pending = bytearray()
        wait_ready(ser, pending)
        ser.reset_input_buffer()
        pending.clear()
        before_user = user_metadata(send_query(ser, pending, "GET USERBUF"))
        pp_setup(ser, pending)
        send_line(ser, pending, "SET DDR PP START")
        print("FAULT INJECTED %s ERROR %s" % (args.fault, expected_error))
        wait_fault(ser, pending, expected_error, fault_timeout)
        send_line(ser, pending, "SET DDR PP STOP")
        stopped = parse_pp_status(send_query(ser, pending, "GET DDR PP"))
        if not stopped or stopped["active"] or stopped["state"] != "IDLE" or stopped["error"]:
            raise RuntimeError("fault stop did not recover: %r" % stopped)
        after_user = user_metadata(send_query(ser, pending, "GET USERBUF"))
        if before_user != after_user:
            raise RuntimeError("active BRAM metadata changed: %r -> %r" % (before_user, after_user))
        send_line(ser, pending, "SET DDR PP START")
        restarted = parse_pp_status(send_query(ser, pending, "GET DDR PP"))
        if not restarted or restarted["state"] == "ERROR" or restarted["error"]:
            raise RuntimeError("restart failed: %r" % restarted)
        send_line(ser, pending, "SET DDR PP STOP")
        print("FAULT RECOVERED %s ACTIVE_BRAM_UNCHANGED" % args.fault)
        print("M1 FAULT %s PASS" % args.fault)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, serial.SerialException) as exc:
        raise SystemExit(str(exc))
