#!/usr/bin/env python3
"""Run the Stage5 M2 long-payload DDR PP gate over the bridge UART."""
import argparse
import re
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.siggen.protocol import (  # noqa: E402
    DDR_PP_BANK_BYTES,
    crc16_ccitt,
    ddr_pp_chunk_cmd,
    ddr_pp_clear_cmd,
    ddr_pp_commit_cmd,
    ddr_pp_start_cmd,
)

BAUD = 115200
READY = "AC820-AWG bridge ready"
CHUNK_BYTES = 20
LOW_PAYLOAD_BYTES = 400 * 1024
STATUS_RE = re.compile(
    r"^DDR PP ACTIVE (?P<active>[01]) ACTIVE_BANK (?P<active_bank>NONE|[01]) "
    r"NEXT_BANK (?P<next_bank>NONE|[01]) STATE (?P<state>\w+) "
    r"ERROR (?P<error>[0-9A-F]{2}) "
    r"BANK0 VALID (?P<b0_valid>[01]) LENGTH (?P<b0_length>\d+) CRC (?P<b0_crc>[0-9A-F]{4}) "
    r"BANK1 VALID (?P<b1_valid>[01]) LENGTH (?P<b1_length>\d+) CRC (?P<b1_crc>[0-9A-F]{4})$"
)
PROFILES = (
    ("BPSK", "RECT", "0.35", 800_000),
    ("QPSK", "RRC", "0.2", 800_000),
    ("QAM16", "RECT", "0.35", 1_000_000),
    ("QAM64", "RRC", "0.5", 1_000_000),
)


class GateError(RuntimeError):
    pass


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


def send_command(ser, pending, command, timeout=5.0):
    ser.write((command + "\r\n").encode())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = read_line(ser, pending, min(0.5, deadline - time.monotonic()))
        if line is None:
            return None
        if line == READY:
            continue
        return line
    return None


def wait_for_bridge(ser, pending, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = read_line(ser, pending, min(0.5, deadline - time.monotonic()))
        if line == READY:
            break


def expect_ok(line, command):
    if line != "OK":
        raise GateError("%s failed: %r" % (command, line))


def parse_status(line):
    match = STATUS_RE.fullmatch(line or "")
    if not match:
        raise GateError("GET DDR PP response invalid: %r" % line)
    values = match.groupdict()
    for key in ("active", "b0_valid", "b1_valid"):
        values[key] = int(values[key])
    for key in ("b0_length", "b1_length"):
        values[key] = int(values[key])
    values["b0_crc"] = int(values["b0_crc"], 16)
    values["b1_crc"] = int(values["b1_crc"], 16)
    values["error"] = int(values["error"], 16)
    return values


def get_status(ser, pending):
    line = send_command(ser, pending, "GET DDR PP", 3.0)
    return line, parse_status(line)


def make_payload(bank, size):
    multiplier = 0x11 + bank * 0x12
    bias = 0x5A + bank * 0x69
    return bytes((i * multiplier + bias) & 0xFF for i in range(size))


def upload_bank(ser, pending, bank, payload):
    total = len(payload)
    for offset in range(0, total, CHUNK_BYTES):
        chunk = payload[offset:offset + CHUNK_BYTES]
        line = send_command(ser, pending, ddr_pp_chunk_cmd(bank, offset, chunk), 3.0)
        expect_ok(line, "DDR PP CHUNK bank %d offset %d" % (bank, offset))
        sent = offset + len(chunk)
        if sent == total or sent % 0x40000 == 0:
            print("UPLOAD BANK%d %d/%d" % (bank, sent, total))
    crc = crc16_ccitt(payload)
    line = send_command(ser, pending, ddr_pp_commit_cmd(bank, total * 8, crc), 5.0)
    expect_ok(line, "DDR PP COMMIT bank %d" % bank)
    return crc


def upload_payload_pair(ser, pending, size):
    reset_profile_state(ser, pending)
    payloads = [make_payload(bank, size) for bank in (0, 1)]
    expected_crc = [upload_bank(ser, pending, bank, payloads[bank]) for bank in (0, 1)]
    line, status = get_status(ser, pending)
    expected_bits = size * 8
    if (status["b0_valid"], status["b1_valid"], status["b0_length"], status["b1_length"],
            status["b0_crc"], status["b1_crc"]) != (
                1, 1, expected_bits, expected_bits, expected_crc[0], expected_crc[1]):
        raise GateError("uploaded bank metadata mismatch: %s" % line)
    print("PP UPLOAD PASS SIZE %d CRC0 %04X CRC1 %04X" %
          (size, expected_crc[0], expected_crc[1]))


def reset_profile_state(ser, pending):
    expect_ok(send_command(ser, pending, "SET DIGMOD MODE OFF", 5.0),
              "DIGMOD MODE OFF")
    expect_ok(send_command(ser, pending, ddr_pp_clear_cmd(), 5.0), "DDR PP CLEAR")


def upload_one_bank(ser, pending, bank, size):
    reset_profile_state(ser, pending)
    payload = make_payload(bank, size)
    expected_crc = upload_bank(ser, pending, bank, payload)
    line, status = get_status(ser, pending)
    valid = status["b0_valid"] if bank == 0 else status["b1_valid"]
    length = status["b0_length"] if bank == 0 else status["b1_length"]
    crc = status["b0_crc"] if bank == 0 else status["b1_crc"]
    if (valid, length, crc) != (1, size * 8, expected_crc):
        raise GateError("uploaded bank metadata mismatch: %s" % line)
    print("PP UPLOAD BANK%d PASS SIZE %d CRC %04X" %
          (bank, size, expected_crc))


def profile_payload_size(profile, max_size):
    return min(max_size, LOW_PAYLOAD_BYTES) if profile[3] < 1_000_000 else max_size


def configure_profile(ser, pending, mode, filt, rolloff, symrate):
    commands = (
        "SET DIGMOD MODE OFF",
        "SET DIGMOD FILTER %s" % filt,
        "SET DIGMOD ROLLOFF %s" % rolloff,
        "SET DIGMOD SYMRATE %d" % symrate,
        "SET DIGMOD MODE %s" % mode,
    )
    for command in commands:
        expect_ok(send_command(ser, pending, command, 3.0), command)


def run_profile(ser, pending, profile, timeout):
    mode, filt, rolloff, symrate = profile
    configure_profile(ser, pending, mode, filt, rolloff, symrate)
    expect_ok(send_command(ser, pending, ddr_pp_start_cmd(), 5.0), "DDR PP START")
    started = time.monotonic()
    state_changes = []
    previous = None
    saw_active = False
    active_banks = []
    final_status = None
    while time.monotonic() - started < timeout:
        line, status = get_status(ser, pending)
        final_status = status
        state_key = (status["active"], status["active_bank"], status["next_bank"],
                     status["state"], status["error"])
        if state_key != previous:
            state_changes.append((time.monotonic() - started, line))
            previous = state_key
        if status["active"]:
            saw_active = True
            if status["active_bank"] != "NONE" and status["active_bank"] not in active_banks:
                active_banks.append(status["active_bank"])
        if status["error"]:
            send_command(ser, pending, "SET DDR PP STOP", 5.0)
            raise GateError("profile %s entered ERROR: %s" % (mode, line))
        if saw_active and not status["active"] and status["state"] == "IDLE":
            break
        time.sleep(0.01)
    else:
        send_command(ser, pending, "SET DDR PP STOP", 5.0)
        raise GateError("profile %s timed out; last status=%r" % (mode, final_status))
    if not saw_active:
        raise GateError("profile %s completed without observing active state" % mode)
    if active_banks != ["0", "1"]:
        raise GateError("profile %s bank order mismatch: %s" % (mode, active_banks))
    return {
        "mode": mode,
        "filter": filt,
        "rolloff": rolloff,
        "symrate": symrate,
        "elapsed_s": time.monotonic() - started,
        "state_changes": state_changes,
        "final": final_status,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="COM11")
    parser.add_argument("--size", type=int, default=DDR_PP_BANK_BYTES)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--group", choices=("low", "full", "all"), default="all")
    parser.add_argument("--upload-bank", type=int, choices=(0, 1))
    parser.add_argument("--reuse-upload", action="store_true")
    args = parser.parse_args()
    if args.upload_bank is not None and args.reuse_upload:
        raise SystemExit("--upload-bank and --reuse-upload are mutually exclusive")
    if args.reuse_upload and args.group != "full":
        raise SystemExit("--reuse-upload requires --group full")
    if args.size < 4 or args.size > DDR_PP_BANK_BYTES or args.size % 4:
        raise SystemExit("--size must be a 4-byte aligned value in 4..%d" % DDR_PP_BANK_BYTES)

    with serial.Serial(args.port, BAUD, timeout=0.1) as ser:
        pending = bytearray()
        wait_for_bridge(ser, pending)
        ser.reset_input_buffer()
        pending.clear()

        if args.upload_bank is not None:
            upload_one_bank(ser, pending, args.upload_bank, args.size)
            return

        results = []
        uploaded_size = args.size if args.reuse_upload else None
        if args.group == "all":
            profiles = PROFILES
        elif args.group == "low":
            profiles = tuple(profile for profile in PROFILES if profile[3] < 1_000_000)
        else:
            profiles = tuple(profile for profile in PROFILES if profile[3] >= 1_000_000)
        for profile in profiles:
            payload_size = profile_payload_size(profile, args.size)
            if payload_size != uploaded_size:
                upload_payload_pair(ser, pending, payload_size)
                uploaded_size = payload_size
            else:
                reset_profile_state(ser, pending)
            result = run_profile(ser, pending, profile, args.timeout)
            result["payload_bytes"] = payload_size
            results.append(result)
            print("PP PROFILE PASS MODE %s FILTER %s ROLLOFF %s SYMRATE %d "
                  "PAYLOAD_BYTES %d ELAPSED_S %.3f" %
                  (result["mode"], result["filter"], result["rolloff"],
                   result["symrate"], result["payload_bytes"], result["elapsed_s"]))
            for elapsed, state_line in result["state_changes"]:
                print("PP STATE %.3f %s" % (elapsed, state_line))
        print("M2 DDR PP LONG GATE PASS GROUP %s" % args.group)
        print("BANK_BYTES %d PROFILES %d" % (args.size, len(results)))


if __name__ == "__main__":
    try:
        main()
    except (GateError, serial.SerialException) as exc:
        raise SystemExit(str(exc))
