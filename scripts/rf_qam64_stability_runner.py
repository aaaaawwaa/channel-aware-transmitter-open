#!/usr/bin/env python3
"""Fixed QAM64/RECT stability diagnostic runner (no retry, no product change).

Two sessions, five frames each, one TX setup/init/load per session:
  S1: OFF -> ON(wait=0) -> ON(wait=5000) -> ON(wait=0) -> OFF
  S2: OFF -> ON(wait=5000) -> ON(wait=0) -> ON(wait=5000) -> OFF
Physical waits are sent through the existing product physical-capture fields
(observe_timing + settle_wait_us), never by sleeping on the host.  The test
harness injects those two reviewed fields into the capture call; no installed
product file, daemon, RTL or candidate is modified.

This runner is intentionally strict about the already-reviewed interface:
before setup/init it verifies the actual wrapper methods, ON observation
content is checked (not only the outer ``usable`` flag), cleanup return values
are enforced, counts distinguish attempted from completed work, and every
frame gets a unique result file.  Legal named desynchronization remains a
research observation; malformed or contradictory results stop the batch.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import time

from rf_bpsk_ui_board_session import LiveUiSession

QAM64_VERSION = "qam64-rect-snapshot-v2"
MAX_SECONDS = 180.0
CAPTURE_SECONDS = 10.0
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, shaping="RECT", rolloff="0.35", iq_order="QI",
                 seed=39)
BUDGET = dict(sessions=2, initializations=2, loads=2, on_tx=6,
              captures=10, retries=0)
SESSIONS = (
    dict(session=1, frames=(
        dict(tag="s1-off-first", physical=False, wait_us=None),
        dict(tag="s1-on-0-a", physical=True, wait_us=0),
        dict(tag="s1-on-5000-a", physical=True, wait_us=5000),
        dict(tag="s1-on-0-b", physical=True, wait_us=0),
        dict(tag="s1-off-last", physical=False, wait_us=None),
    )),
    dict(session=2, frames=(
        dict(tag="s2-off-first", physical=False, wait_us=None),
        dict(tag="s2-on-5000-a", physical=True, wait_us=5000),
        dict(tag="s2-on-0-a", physical=True, wait_us=0),
        dict(tag="s2-on-5000-b", physical=True, wait_us=5000),
        dict(tag="s2-off-last", physical=False, wait_us=None),
    )),
)


def _precheck_session(session):
    """Verify the real wrapper interface before any init/load/arm attempt."""
    for name in ("initial", "setup", "capture", "cleanup", "close"):
        if not callable(getattr(session, name, None)):
            return "missing_session_method:" + name
    client = getattr(session, "client", None)
    if client is None:
        return "missing_session_client"
    if not callable(getattr(client, "observe_capture_timing", None)):
        return "missing_client_method:observe_capture_timing"
    if not hasattr(client, "capture_extra"):
        return "missing_client_capture_extra"
    if not callable(getattr(client, "capture", None)):
        return "missing_client_method:capture"
    return None


def _observation_problem(record):
    """Check real ON observation content and same-frame identity."""
    obs = record.get("capture_observation")
    if not isinstance(obs, dict):
        return "on_observation_missing"
    if obs.get("usable") is not True or obs.get("stop") is not False:
        return "on_observation_not_usable"
    if obs.get("capture_id") != record.get("capture_id"):
        return "on_observation_capture_id_mismatch"
    for key in ("request_id", "daemon_timestamp_ns", "release_id"):
        if key in obs and key in record and obs.get(key) != record.get(key):
            return "on_observation_%s_mismatch" % key
    inner = obs.get("observation")
    if not isinstance(inner, dict):
        return "on_observation_inner_missing"
    if inner.get("observation_present") is not True:
        return "on_observation_not_present"
    if inner.get("identity_ok") is not True:
        return "on_observation_identity_bad"
    gain = inner.get("in_window_rx_gain_index")
    if not isinstance(gain, dict) or gain.get("available") is not True:
        return "on_in_window_gain_missing"
    if type(gain.get("value")) is not int:
        return "on_in_window_gain_value_invalid"
    attenuation = inner.get("chip_tx_attenuation_mdb")
    if not isinstance(attenuation, dict) or attenuation.get("available") is not True:
        return "on_chip_attenuation_missing"
    value = attenuation.get("value")
    if (not isinstance(value, list) or len(value) != 2
            or any(type(v) is not int for v in value)):
        return "on_chip_attenuation_value_invalid"
    return None


def _metric_problem(result):
    errors = result.get("bit_errors")
    compared = result.get("compared_bits")
    ber = result.get("ber")
    if type(errors) is not int or type(compared) is not int:
        return "metric_type_invalid"
    if compared != 384:
        return "metric_denominator_mismatch"
    if errors < 0 or errors > compared:
        return "metric_count_out_of_range"
    if (not isinstance(ber, (int, float)) or isinstance(ber, bool)
            or not math.isfinite(float(ber))):
        return "metric_ber_not_finite"
    if float(ber) < 0.0 or float(ber) > 1.0:
        return "metric_ber_out_of_range"
    if abs(float(ber) - errors / compared) > 1e-12:
        return "metric_ber_inconsistent"
    return None


def _result_problem(frame, record):
    if not isinstance(record, dict):
        return "malformed_record"
    if record.get("egress_safe") is not True:
        return "egress_unsafe"
    if not isinstance(record.get("capture_id"), str) or not record["capture_id"]:
        return "missing_capture_id"
    if not isinstance(record.get("iq_sha256"), str) or len(record["iq_sha256"]) != 64:
        return "missing_iq_sha256"
    result = record.get("result")
    if not isinstance(result, dict):
        return "result_missing"
    if result.get("algorithm_version") != QAM64_VERSION:
        return "algorithm_version_mismatch"
    if result.get("capture_id") not in (None, record.get("capture_id")):
        return "stale_capture_id"
    if result.get("release_id") not in (None, record.get("release_id")):
        return "stale_release_id"
    if result.get("evm_available") is not False or result.get("evm_rms") is not None:
        return "evm_should_be_unavailable"
    if frame["physical"]:
        problem = _observation_problem(record)
        if problem:
            return problem
        reference_sync = result.get("reference_sync")
        if reference_sync is True:
            if result.get("carrier_sync") is not True or result.get("timing_sync") is not True:
                return "on_sync_not_true"
            if result.get("valid_symbols") != 127:
                return "on_valid_symbols"
            if result.get("alignment_bits") != 378:
                return "on_alignment_bits"
            if result.get("comparison_start_symbol") != 63:
                return "on_comparison_start"
            return _metric_problem(result)
        if reference_sync is False:
            # A named, complete three-sync-false desync is a legal research
            # observation and must not be converted into a structural stop.
            if (result.get("carrier_sync") is not False
                    or result.get("timing_sync") is not False):
                return "on_desync_flags_not_all_false"
            if not isinstance(result.get("reason"), str) or not result["reason"]:
                return "on_desync_reason_missing"
            if any(result.get(k) is not None
                   for k in ("bit_errors", "compared_bits", "ber")):
                return "on_desync_metrics_present"
            return None
        return "on_reference_sync_not_boolean"
    for key in ("carrier_sync", "timing_sync", "reference_sync"):
        if result.get(key) is not False:
            return "off_%s_residual" % key
    if any(result.get(k) is not None for k in ("bit_errors", "compared_bits", "ber")):
        return "off_ber_residual"
    return None


def _timing_record(frame, timing, requested_wait):
    out = dict(requested_wait_us=requested_wait, timing=timing,
               timing_available=False, actual_wait_us=None,
               settle_begin_ns=None, settle_end_ns=None,
               settle_duration_ns=None, problem=None)
    if not frame["physical"]:
        # OFF has no physical capture window; its timing sidecar is absent by
        # contract.  Record that fact, never synthesize a physical observation.
        return out
    if not isinstance(timing, dict) or timing.get("reason") is not None:
        out["problem"] = "capture_timing_unavailable"
        return out
    trace = timing.get("last_capture_timing")
    if not isinstance(trace, dict) or trace.get("available") is not True:
        out["problem"] = "capture_timing_missing"
        return out
    out["timing_available"] = True
    actual = trace.get("settle_wait_requested_us")
    out["actual_wait_us"] = actual
    if actual != int(requested_wait):
        out["problem"] = "wait_mismatch"
        return out
    if requested_wait:
        begin, end = trace.get("settle_begin_ns"), trace.get("settle_end_ns")
        if type(begin) is not int or type(end) is not int or end < begin:
            out["problem"] = "settle_bracket_missing"
            return out
        out["settle_begin_ns"] = begin
        out["settle_end_ns"] = end
        out["settle_duration_ns"] = end - begin
    return out


def _summary_row(session_index, frame, record, timing, seconds):
    result = record.get("result") or {}
    return dict(
        session=session_index, tag=frame["tag"], physical=frame["physical"],
        requested_wait_us=frame["wait_us"],
        capture_id=record.get("capture_id"), release_id=record.get("release_id"),
        iq_sha256=record.get("iq_sha256"), iq_file=record.get("iq_file"),
        metadata_file=record.get("metadata_file"),
        capture_observation_file=record.get("capture_observation_file"),
        capture_observation=record.get("capture_observation"),
        timing=_timing_record(frame, timing,
                              frame["wait_us"] if frame["physical"] else 0),
        tx_load_evidence=record.get("tx_load_evidence"),
        result=result, seconds=seconds, problem=None,
    )


def _safe_tag(tag):
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(tag))


def run_stability(expected_release, output, *, session_factory=LiveUiSession,
                  cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit current cable confirmation is required")
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("refusing to overwrite existing execution output: %s" % output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="rf-qam64-stability-plan-v1",
                expected_release=expected_release, sessions=list(SESSIONS),
                budget=BUDGET, workpoint=WORKPOINT, max_seconds=MAX_SECONDS,
                capture_seconds=CAPTURE_SECONDS, policy=(
                    "two sessions, one setup/init/load each, fixed 0/5000us waits; "
                    "no retry; nonzero BER and legal desync retained; structural, "
                    "identity, observation, timing, cleanup or safety failures stop "
                    "the batch; attempted and completed counts are both recorded"))
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    rows = []
    counts = dict(sessions=0, initializations=0, loads=0, on_tx=0, captures=0)
    attempts = dict(sessions=0, initializations=0, loads=0, on_tx=0, captures=0)
    seen_tags = set()
    failure = None
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    for spec in SESSIONS:
        if failure:
            break
        if time.monotonic() > deadline:
            failure = "batch_deadline"
            break
        session_dir = output / ("session-%d" % spec["session"])
        session_dir.mkdir(parents=True, exist_ok=True)
        session = None
        attempts["sessions"] += 1
        try:
            session = session_factory(
                expected_release, session_dir, tx_mode="QAM64", seed=39,
                amplitude=60, session_index=spec["session"],
                run_id="stability-s%d" % spec["session"], rx_mode="QAM64",
                rx_seed=39, tx_shaping="RECT", rx_shaping="RECT",
                tx_rolloff="0.35", rx_rolloff="0.35",
                capture_deadline=CAPTURE_SECONDS)
            check = _precheck_session(session)
            if check:
                failure = "precheck:" + check
                break
            attempts["initializations"] += 1
            initial = session.initial()
            counts["initializations"] += 1
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                failure = "initial_not_safe"
                break
            if initial.get("release_id") != expected_release:
                failure = "initial_release_mismatch"
                break
            attempts["loads"] += 1
            session.setup(39)
            counts["loads"] += 1
            for frame in spec["frames"]:
                if time.monotonic() > deadline:
                    failure = "batch_deadline"
                    break
                tag = frame["tag"]
                if tag in seen_tags:
                    failure = "duplicate_frame_tag:" + tag
                    break
                seen_tags.add(tag)
                extra = {}
                if frame["physical"]:
                    extra = {"observe_timing": True,
                             "settle_wait_us": int(frame["wait_us"])}
                session.client.capture_extra = extra
                attempts["captures"] += 1
                if frame["physical"]:
                    attempts["on_tx"] += 1
                t0 = time.monotonic()
                try:
                    record = session.capture(tag, frame["physical"])
                finally:
                    session.client.capture_extra = {}
                seconds = time.monotonic() - t0
                counts["captures"] += 1
                if frame["physical"]:
                    counts["on_tx"] += 1
                if frame["physical"]:
                    try:
                        timing = session.client.observe_capture_timing(
                            session.client.frame)
                    except Exception as exc:  # exact error recorded, no retry
                        timing = dict(reason=type(exc).__name__ + ": " + str(exc),
                                      last_capture_timing=None)
                else:
                    timing = None
                row = _summary_row(spec["session"], frame, record, timing, seconds)
                row["problem"] = _result_problem(frame, record)
                if row["problem"] is None:
                    row["problem"] = row["timing"]["problem"]
                rows.append(row)
                result_path = session_dir / ("frame-result-%s.json" % _safe_tag(tag))
                if result_path.exists():
                    failure = "frame_result_exists:" + tag
                    break
                result_path.write_text(
                    json.dumps(row, indent=2, sort_keys=True), encoding="utf-8")
                if row["problem"]:
                    failure = "%s:%s" % (tag, row["problem"])
                    break
        except Exception as exc:
            failure = "%s: %s" % (type(exc).__name__, str(exc) or "session error")
        finally:
            if session is not None:
                cleanup_result = None
                try:
                    cleanup_result = session.cleanup()
                except Exception as exc:
                    if failure is None:
                        failure = "cleanup: " + (str(exc) or type(exc).__name__)
                if failure is None and (
                        not isinstance(cleanup_result, dict)
                        or cleanup_result.get("safe") is not True
                        or cleanup_result.get("disarm_ack") is not True):
                    failure = "cleanup_unsafe"
                try:
                    session.close()
                except Exception as exc:
                    if failure is None:
                        failure = "close: " + (str(exc) or type(exc).__name__)
            if failure is None:
                counts["sessions"] += 1
    elapsed = time.monotonic() - started
    structural_valid = failure is None
    summary = dict(
        schema="rf-qam64-stability-summary-v1", expected_release=expected_release,
        execution_complete=structural_valid, structural_valid=structural_valid,
        stop_cause=failure, counts=counts, attempts=attempts, budget=BUDGET,
        elapsed_s=elapsed, max_seconds=MAX_SECONDS, rows=rows,
        qam64_on_frames=sum(1 for r in rows if r["physical"]),
        qam64_clean_sync=sum(1 for r in rows if r["physical"]
                              and (r.get("result") or {}).get("reference_sync") is True),
        metric_failures=[r["tag"] for r in rows if r["physical"]
                         and (r.get("result") or {}).get("reference_sync") is True
                         and (r.get("result") or {}).get("bit_errors") != 0])
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "final.json").write_text(
        json.dumps({k: summary[k] for k in (
            "schema", "expected_release", "execution_complete",
            "structural_valid", "stop_cause", "counts", "attempts",
            "elapsed_s", "metric_failures")}, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="QAM64 stability diagnostic runner")
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    a = p.parse_args(argv)
    summary = run_stability(a.expected_release, a.output,
                            cable_confirmed=a.cable_confirmed)
    print(json.dumps({k: summary[k] for k in (
        "execution_complete", "stop_cause", "counts", "attempts",
        "elapsed_s", "metric_failures")}, indent=2))
    return 0 if summary["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
