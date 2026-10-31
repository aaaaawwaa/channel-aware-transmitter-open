#!/usr/bin/env python3
"""Software-level PP reload timeline readback gate (Stage5 M2, no RTL change).

Runs a real double-bank DDR PP playback against banks already committed on the
bridge, then reads the DDR_PP_TIMELINE values that firmware recorded during the
playback back through the existing `SET DMA BENCH` response line.  This is the
deliberately software-only route: no RTL/BD/firmware change, no new public
command, and `GET DDR PP` wire format is untouched.

What the readback can honestly show (PS XTime, COUNTS_PER_SECOND on the frozen
M2 BSP is the CPU clock /2 = 333333343):
  * IOC_TICKS        bank#1 DMA start -> DMA IOC as observed by the service loop
                      (includes the symbol-rate gated FIFO consumption);
  * DRAIN_TICKS      bank#1 IOC -> user_stream_done observation;
  * NEXT_SUBMIT_TICKS  bank#0 drain observation -> next `SimpleTransfer` submit,
                      i.e. the firmware bounded-reload critical section length;
  * GAP_TICKS        equals NEXT_SUBMIT_TICKS here (the first-beat update path is
                      gated by the legacy BRAM user_valid flag, which is not set
                      in the DDR-PP stream path) -- do NOT read it as a hardware
                      drain->first-AXIS-valid gap;
  * FIRST_BEAT_TICKS 0 by construction in the PP path (see above).

The bank payload bytes are not re-verified at playback (no board readback path);
this gate is timing/order evidence only.
"""
import argparse
import re
import sys
import time

import serial

import ddr_pp_long_gate as base

# Frozen M2 fresh BSP: xtime COUNTS_PER_SECOND = CPU clock /2 = 666666687/2.
CPS = 333333343
TIMELINE_RE = re.compile(
    r"DMA BENCH PASS BYTES (\d+) CRC ([0-9A-F]{8}) STATUS ([0-9A-F]{8}) "
    r"TICKS (\d+) RATE_MBPS_X1000 (\d+) "
    r"FIRST_VALID_CYCLE (\d+) FIFO_MIN (\d+) FIFO_MAX (\d+) "
    r"DDR_PP_TIMELINE IOC_TICKS (\d+) DRAIN_TICKS (\d+) NEXT_SUBMIT_TICKS (\d+) "
    r"FIRST_BEAT_TICKS (\d+) GAP_TICKS (\d+) FIFO_MIN (\d+) FIFO_MAX (\d+)"
    r"(?: PP_OBS .*)?$"
)
PROFILES = (
    ("QAM16", "RECT", "0.35", 1_000_000),
    ("QAM64", "RRC", "0.5", 1_000_000),
)


class GateError(RuntimeError):
    pass


def run_profile(ser, pending, profile, timeout):
    mode, filt, rolloff, symrate = profile
    line, status = base.get_status(ser, pending)
    if not (status["b0_valid"] and status["b1_valid"] and
            status["b0_length"] and status["b1_length"]):
        raise GateError("both committed banks required (upload first): %s" % line)
    base.reset_profile_state(ser, pending)
    base.configure_profile(ser, pending, mode, filt, rolloff, symrate)
    base.expect_ok(base.send_command(ser, pending, base.ddr_pp_start_cmd(), 5.0),
                   "DDR PP START")
    started = time.monotonic()
    state_changes = []
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
            state_changes.append((time.monotonic() - started, line))
            previous = state_key
        if status["active"]:
            saw_active = True
            if status["active_bank"] != "NONE" and \
                    status["active_bank"] not in active_banks:
                active_banks.append(status["active_bank"])
        if status["error"]:
            base.send_command(ser, pending, "SET DDR PP STOP", 5.0)
            raise GateError("profile %s entered ERROR: %s" % (mode, line))
        if saw_active and not status["active"] and status["state"] == "IDLE":
            break
        time.sleep(0.01)
    else:
        base.send_command(ser, pending, "SET DDR PP STOP", 5.0)
        raise GateError("profile %s timed out; last=%r" % (mode, final_status))
    if not saw_active:
        raise GateError("profile %s finished without observing active" % mode)
    if active_banks != ["0", "1"]:
        raise GateError("profile %s bank order mismatch: %s" % (mode, active_banks))
    # Read firmware's recorded PP timeline back through the existing DMA BENCH
    # response.  This runs an independent 1 MiB benchmark afterwards; it does not
    # reset DDR_PP_TIMELINE (only SET DDR PP CLEAR does).
    bench_line = base.send_command(ser, pending, "SET DMA BENCH", 15.0)
    match = TIMELINE_RE.fullmatch(bench_line or "")
    if not match:
        raise GateError("DMA BENCH timeline readback invalid: %r" % bench_line)
    return {
        "mode": mode,
        "filter": filt,
        "rolloff": rolloff,
        "symrate": symrate,
        "elapsed_s": time.monotonic() - started,
        "state_changes": state_changes,
        "final": final_status,
        "bench_line": bench_line,
        "timeline": {
            # DMA BENCH line: g1 BYTES g2 CRC g3 STATUS g4 TICKS g5 RATE g6
            # FIRST_VALID g7 FIFO_MIN g8 FIFO_MAX g9 IOC g10 DRAIN g11
            # NEXT_SUBMIT g12 FIRST_BEAT g13 GAP g14 FIFO_MIN g15 FIFO_MAX.
            "ioc_ticks": int(match.group(9)),
            "drain_ticks": int(match.group(10)),
            "next_submit_ticks": int(match.group(11)),
            "first_beat_ticks": int(match.group(12)),
            "gap_ticks": int(match.group(13)),
        },
    }


def us(ticks):
    return ticks / CPS * 1e6


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="COM11")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--profile", choices=("qam16", "qam64", "all"),
                        default="all")
    args = parser.parse_args()
    if args.profile == "qam16":
        profiles = PROFILES[:1]
    elif args.profile == "qam64":
        profiles = PROFILES[1:]
    else:
        profiles = PROFILES

    with serial.Serial(args.port, base.BAUD, timeout=0.1) as ser:
        pending = bytearray()
        base.wait_for_bridge(ser, pending)
        ser.reset_input_buffer()
        pending.clear()
        for profile in profiles:
            result = run_profile(ser, pending, profile, args.timeout)
            mode = result["mode"]
            print("PP PROFILE PASS MODE %s FILTER %s ROLLOFF %s SYMRATE %d "
                  "ELAPSED_S %.3f" %
                  (mode, result["filter"], result["rolloff"], result["symrate"],
                   result["elapsed_s"]))
            for elapsed, state_line in result["state_changes"]:
                print("PP STATE %.3f %s" % (elapsed, state_line))
            tl = result["timeline"]
            if not (tl["next_submit_ticks"] and tl["ioc_ticks"]):
                raise GateError("profile %s PP timeline not populated" % mode)
            print("PP TIMELINE MODE %s IOC_TICKS %lu DRAIN_TICKS %lu "
                  "NEXT_SUBMIT_TICKS %lu FIRST_BEAT_TICKS %lu GAP_TICKS %lu "
                  "NEXT_SUBMIT_US %.1f" %
                  (mode, tl["ioc_ticks"], tl["drain_ticks"],
                   tl["next_submit_ticks"], tl["first_beat_ticks"],
                   tl["gap_ticks"], us(tl["next_submit_ticks"])))
            print(result["bench_line"])
    print("M2 DDR PP TIMELINE GATE PASS PROFILES %d" % len(profiles))


if __name__ == "__main__":
    try:
        main()
    except (GateError, serial.SerialException) as exc:
        raise SystemExit(str(exc))
