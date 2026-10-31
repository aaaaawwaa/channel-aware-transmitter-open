#!/usr/bin/env python3
"""Run the Stage5 M2 PP normal-mode data-plane gate over the bridge UART."""
import argparse
import re
import sys
import time
import zlib
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host.siggen.protocol import DDR_PP_BANK_BYTES  # noqa: E402
from scripts import ddr_pp_long_gate as base  # noqa: E402

BAUD = 115200
PP_STATUS_VALID = 1 << 31
PP_STATUS_TLAST = 1 << 30
PP_STATUS_TKEEP_ERR = 1 << 29
PP_STATUS_UNDERFLOW = 1 << 28
PP_STATUS_COUNT_MASK = 0x001FFFFF
FIFO_RAW_COUNT_MAX = 4097
PP_OBS_RE = re.compile(
    r"PP_OBS PP0_STATUS (?P<p0_status>[0-9A-Fa-f]{8}) "
    r"PP1_STATUS (?P<p1_status>[0-9A-Fa-f]{8}) "
    r"PP0_CRC32 (?P<p0_crc>[0-9A-Fa-f]{8}) "
    r"PP1_CRC32 (?P<p1_crc>[0-9A-Fa-f]{8}) "
    r"PP0_TLAST (?P<p0_tlast>\d+) PP1_TLAST (?P<p1_tlast>\d+) "
    r"PP0_TKEEP (?P<p0_keep>[0-9A-Fa-f]+) PP1_TKEEP (?P<p1_keep>[0-9A-Fa-f]+) "
    r"PP0_UNDERFLOW (?P<p0_underflow>\d+) PP1_UNDERFLOW (?P<p1_underflow>\d+) "
    r"PP0_BYTES (?P<p0_bytes>\d+) PP1_BYTES (?P<p1_bytes>\d+) "
    r"PP0_FIFO_MIN (?P<p0_fifo_min>\d+) PP0_FIFO_MAX (?P<p0_fifo_max>\d+) "
    r"PP1_FIFO_MIN (?P<p1_fifo_min>\d+) PP1_FIFO_MAX (?P<p1_fifo_max>\d+) "
    r"PP_GAP_CYCLES (?P<gap>\d+)$"
)
PROFILES = (
    ("BPSK", "RECT", "0.35", 800_000),
    ("QPSK", "RRC", "0.2", 800_000),
    ("QAM16", "RECT", "0.35", 1_000_000),
    ("QAM64", "RRC", "0.5", 1_000_000),
)


class GateError(RuntimeError):
    pass


def parse_pp_obs(line):
    match = PP_OBS_RE.search(line or "")
    if not match:
        raise GateError("DMA BENCH response has no PP_OBS payload: %r" % line)
    values = match.groupdict()
    for key in values:
        values[key] = int(values[key], 16) if key.endswith(("status", "crc")) else int(values[key], 16) if key.endswith("keep") else int(values[key])
    return values


def check_bank(values, prefix, payload):
    status = values["%s_status" % prefix]
    expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if not status & PP_STATUS_VALID or not status & PP_STATUS_TLAST:
        raise GateError("%s missing valid/TLAST: %08X" % (prefix, status))
    if status & (PP_STATUS_TKEEP_ERR | PP_STATUS_UNDERFLOW):
        raise GateError("%s reports TKEEP/underflow error: %08X" % (prefix, status))
    if bool(status & (1 << 23)) != (prefix == "p1"):
        raise GateError("%s bank selector mismatch: %08X" % (prefix, status))
    if (status & PP_STATUS_COUNT_MASK) != len(payload):
        raise GateError("%s byte count mismatch: %d" % (prefix, status & PP_STATUS_COUNT_MASK))
    if values["%s_crc" % prefix] != expected_crc:
        raise GateError("%s CRC mismatch: got %08X expected %08X" %
                        (prefix, values["%s_crc" % prefix], expected_crc))
    if values["%s_tlast" % prefix] != 1 or values["%s_underflow" % prefix] != 0:
        raise GateError("%s decoded TLAST/underflow mismatch" % prefix)
    if values["%s_keep" % prefix] != 0xF:
        raise GateError("%s final TKEEP mismatch: %X" %
                        (prefix, values["%s_keep" % prefix]))
    fifo_min = values["%s_fifo_min" % prefix]
    fifo_max = values["%s_fifo_max" % prefix]
    if fifo_min > fifo_max or fifo_max > FIFO_RAW_COUNT_MAX:
        raise GateError("%s FIFO range invalid: %d..%d" % (prefix, fifo_min, fifo_max))


def run_profile(ser, pending, profile, payloads, timeout):
    mode, filt, rolloff, symrate = profile
    base.configure_profile(ser, pending, mode, filt, rolloff, symrate)
    base.expect_ok(base.send_command(ser, pending, base.ddr_pp_start_cmd(), 5.0),
                   "DDR PP START")
    started = time.monotonic()
    previous = None
    saw_active = False
    active_banks = []
    final_status = None
    while time.monotonic() - started < timeout:
        line, status = base.get_status(ser, pending)
        final_status = status
        state_key = (status["active"], status["active_bank"], status["next_bank"],
                     status["state"], status["error"])
        if state_key != previous:
            print("PP STATE %.3f %s" % (time.monotonic() - started, line))
            previous = state_key
        if status["active"]:
            saw_active = True
            if status["active_bank"] != "NONE" and status["active_bank"] not in active_banks:
                active_banks.append(status["active_bank"])
        if status["error"]:
            base.send_command(ser, pending, "SET DDR PP STOP", 5.0)
            raise GateError("profile %s entered ERROR: %s" % (mode, line))
        if saw_active and not status["active"] and status["state"] == "IDLE":
            break
        time.sleep(0.01)
    else:
        base.send_command(ser, pending, "SET DDR PP STOP", 5.0)
        raise GateError("profile %s timed out; last status=%r" % (mode, final_status))
    if not saw_active:
        raise GateError("profile %s completed without observing active state" % mode)
    if active_banks != ["0", "1"]:
        raise GateError("profile %s bank order mismatch: %s" % (mode, active_banks))

    bench_line = base.send_command(ser, pending, "SET DMA BENCH", 30.0)
    values = parse_pp_obs(bench_line)
    check_bank(values, "p0", payloads[0])
    check_bank(values, "p1", payloads[1])
    if values["gap"] <= 0:
        raise GateError("profile %s has no positive PP_GAP_CYCLES" % mode)
    print("PP_OBS PASS MODE %s FILTER %s ROLLOFF %s SYMRATE %d "
          "PP0_CRC32 %08X PP1_CRC32 %08X PP0_FIFO_MIN %d PP0_FIFO_MAX %d "
          "PP1_FIFO_MIN %d PP1_FIFO_MAX %d PP_GAP_CYCLES %d" %
          (mode, filt, rolloff, symrate,
           values["p0_crc"], values["p1_crc"],
           values["p0_fifo_min"], values["p0_fifo_max"],
           values["p1_fifo_min"], values["p1_fifo_max"], values["gap"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="COM11")
    parser.add_argument("--size", type=int, default=DDR_PP_BANK_BYTES)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--group", choices=("low", "full", "all"), default="all")
    args = parser.parse_args()
    if args.size < 4 or args.size > DDR_PP_BANK_BYTES or args.size % 4:
        raise SystemExit("--size must be a 4-byte aligned value in 4..%d" % DDR_PP_BANK_BYTES)

    if args.group == "all":
        profiles = PROFILES
    elif args.group == "low":
        profiles = tuple(profile for profile in PROFILES if profile[3] < 1_000_000)
    else:
        profiles = tuple(profile for profile in PROFILES if profile[3] >= 1_000_000)

    with serial.Serial(args.port, BAUD, timeout=0.1) as ser:
        pending = bytearray()
        base.wait_for_bridge(ser, pending)
        ser.reset_input_buffer()
        pending.clear()
        payloads_by_size = {}
        for profile in profiles:
            if args.size not in payloads_by_size:
                base.reset_profile_state(ser, pending)
                payloads = [base.make_payload(bank, args.size) for bank in (0, 1)]
                base.upload_bank(ser, pending, 0, payloads[0])
                base.upload_bank(ser, pending, 1, payloads[1])
                payloads_by_size[args.size] = payloads
            else:
                base.reset_profile_state(ser, pending)
                payloads = payloads_by_size[args.size]
                base.upload_bank(ser, pending, 0, payloads[0])
            run_profile(ser, pending, profile, payloads, args.timeout)
        print("M2 DDR PP DATA-PLANE GATE PASS GROUP %s" % args.group)
        print("BANK_BYTES %d PROFILES %d" % (args.size, len(profiles)))


if __name__ == "__main__":
    try:
        main()
    except (GateError, serial.SerialException) as exc:
        raise SystemExit(str(exc))
