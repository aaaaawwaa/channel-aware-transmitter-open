#!/usr/bin/env python3
"""One-session digital-vs-physical QAM64 path comparison runner.

Fixed order:
  OFF-first -> physical A(wait=0) -> digital A -> digital B -> physical B(wait=0) -> OFF-last

The host-only adapter in ``EvidenceClient.digital_capture`` reuses the frozen
client's live advertised-op check, connection/session lock, binary IQ
header/payload validation and timeout path.  It records the real
``loopback.digital.capture`` op, checks RX release and TX source off, and never
pretends the frame is physical or OFF.  No product source, daemon or candidate
is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time

from rf_bpsk_ui_board_session import LiveUiSession
from rf_qam64_stability_runner import _precheck_session, _result_problem

QAM64_VERSION = "qam64-rect-snapshot-v2"
MAX_SECONDS = 180.0
CAPTURE_SECONDS = 10.0
BUDGET = dict(sessions=1, rf_init=1, tx_load=1, captures=6,
              tx_source_activations=4, retries=0)
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, shaping="RECT", rolloff="0.35", iq_order="QI",
                 seed=39)
FRAMES = (
    dict(tag="path-off-first", path="off", physical=False, wait_us=None),
    dict(tag="path-physical-a", path="physical", physical=True, wait_us=0),
    dict(tag="path-digital-a", path="digital", physical=False, wait_us=None),
    dict(tag="path-digital-b", path="digital", physical=False, wait_us=None),
    dict(tag="path-physical-b", path="physical", physical=True, wait_us=0),
    dict(tag="path-off-last", path="off", physical=False, wait_us=None),
)


def _precheck(session):
    problem = _precheck_session(session)
    if problem:
        return problem
    if not callable(getattr(session.client, "digital_capture", None)):
        return "missing_client_method:digital_capture"
    return None


def _install_control_counts(session, counts):
    previous = session.client.control_op

    def recorded(op, args=None, payload=b""):
        if op not in ("rf.init", "tx.load"):
            return previous(op, args, payload=payload)
        name = "rf_init" if op == "rf.init" else "tx_load"
        counts["attempted"][name] += 1
        try:
            result = previous(op, args, payload=payload)
        except BaseException:
            counts["unknown"][name] += 1
            raise
        counts["completed"][name] += 1
        return result

    session.client.control_op = recorded


def _write_digital_artifact(output_dir, tag, frame, load_evidence):
    iq_path = output_dir / (tag + ".iq")
    meta_path = output_dir / (tag + ".json")
    iq_path.write_bytes(frame.payload)
    meta = dict(
        schema="qam64-path-digital-frame-v1", tag=tag, path="digital",
        op=frame.op, physical=frame.physical, capture_id=frame.capture_id,
        samples=frame.samples, sample_rate_hz=frame.sample_rate_hz,
        ratio=frame.ratio, daemon_timestamp_ns=frame.daemon_timestamp_ns,
        connection_generation=frame.connection_generation,
        config_revision=frame.config_revision, request_id=frame.request_id,
        release_id=frame.release_id,
        iq_sha256=hashlib.sha256(frame.payload).hexdigest(),
        post_rx_armed=frame.post_readback.rx_armed,
        post_tx_source_selected=frame.post_readback.tx_source_selected,
        pre_tx_source_selected=frame.pre_readback.tx_source_selected,
        tx_load_evidence=load_evidence,
        algorithm_evidence="NOT_APPLIED: digital path is an independent path "
                           "comparison, not product comparison-region BER",
    )
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True),
                         encoding="utf-8")
    return iq_path, meta_path, meta


def run_path_compare(expected_release, output, *, session_factory=LiveUiSession,
                     cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit current cable confirmation is required")
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("refusing to overwrite existing execution output: %s" % output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="rf-qam64-path-compare-plan-v1",
                expected_release=expected_release, frames=list(FRAMES),
                budget=BUDGET, workpoint=WORKPOINT, max_seconds=MAX_SECONDS,
                capture_seconds=CAPTURE_SECONDS,
                policy="one session, one rf.init/load, OFF-first/A physical/"
                       "A,B digital/B physical/OFF-last; no retry; real digital "
                       "op and path preserved; physical nonzero BER retained")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    counts = dict(
        attempted=dict(sessions=0, rf_init=0, tx_load=0, captures=0,
                       tx_source_activations=0),
        completed=dict(sessions=0, rf_init=0, tx_load=0, captures=0,
                       tx_source_activations=0),
        unknown=dict(sessions=0, rf_init=0, tx_load=0, captures=0,
                     tx_source_activations=0),
        precheck_reads=0)
    rows = []
    failure = None
    session = None
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    session_dir = output / "session-1"
    session_dir.mkdir(parents=True, exist_ok=True)
    counts["attempted"]["sessions"] += 1
    try:
        session = session_factory(
            expected_release, session_dir, tx_mode="QAM64", seed=39,
            amplitude=60, session_index=1, run_id="path-compare-s1",
            rx_mode="QAM64", rx_seed=39, tx_shaping="RECT",
            rx_shaping="RECT", tx_rolloff="0.35", rx_rolloff="0.35",
            capture_deadline=CAPTURE_SECONDS)
        # Digital capture uses the frozen client's capture_timeout, not the UI
        # session deadline; make it the same reviewed 10 s value.
        if hasattr(session.client, "client"):
            session.client.client.capture_timeout = CAPTURE_SECONDS
        check = _precheck(session)
        if check:
            failure = "precheck:" + check
        if failure is None:
            counts["precheck_reads"] += 1
            initial = session.initial()
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                failure = "initial_not_safe"
            elif initial.get("release_id") != expected_release:
                failure = "initial_release_mismatch"
        if failure is None:
            _install_control_counts(session, counts)
            # setup() performs the real rf.init and tx.load; the read-only
            # initial() above is deliberately not counted as initialization.
            session.setup(39)
            load_evidence = None
            records = getattr(session, "_tx_load_records", None)
            if isinstance(records, list) and records:
                load_evidence = records[-1]
            (session_dir / "tx-load-evidence.json").write_text(
                json.dumps(load_evidence, indent=2, sort_keys=True),
                encoding="utf-8")
            for frame in FRAMES:
                if time.monotonic() > deadline:
                    failure = "batch_deadline"
                    break
                counts["attempted"]["captures"] += 1
                t0 = time.monotonic()
                if frame["path"] == "digital":
                    counts["attempted"]["tx_source_activations"] += 1
                    try:
                        record = session.client.digital_capture(
                            dict(samples=4096, ratio=0))
                        counts["completed"]["captures"] += 1
                        counts["completed"]["tx_source_activations"] += 1
                    except BaseException:
                        counts["unknown"]["captures"] += 1
                        counts["unknown"]["tx_source_activations"] += 1
                        raise
                    iq_path, meta_path, meta = _write_digital_artifact(
                        session_dir, frame["tag"], record, load_evidence)
                    row = dict(
                        session=1, tag=frame["tag"], path="digital",
                        physical=False, requested_wait_us=None,
                        capture_id=record.capture_id, release_id=record.release_id,
                        iq_sha256=meta["iq_sha256"], iq_file=iq_path.name,
                        metadata_file=meta_path.name,
                        op=record.op, post_rx_armed=record.post_readback.rx_armed,
                        post_tx_source_selected=record.post_readback.tx_source_selected,
                        tx_load_evidence=load_evidence, result=None,
                        seconds=time.monotonic() - t0, problem=None)
                else:
                    extra = {}
                    if frame["physical"]:
                        counts["attempted"]["tx_source_activations"] += 1
                        extra = {"observe_timing": True, "settle_wait_us": 0}
                    session.client.capture_extra = extra
                    try:
                        record = session.capture(frame["tag"], frame["physical"])
                        counts["completed"]["captures"] += 1
                        if frame["physical"]:
                            counts["completed"]["tx_source_activations"] += 1
                    except BaseException:
                        counts["unknown"]["captures"] += 1
                        if frame["physical"]:
                            counts["unknown"]["tx_source_activations"] += 1
                        raise
                    finally:
                        session.client.capture_extra = {}
                    timing = None
                    if frame["physical"]:
                        try:
                            timing = session.client.observe_capture_timing(
                                session.client.frame)
                        except Exception as exc:
                            timing = dict(
                                reason=type(exc).__name__ + ": " + str(exc),
                                last_capture_timing=None)
                    row = dict(
                        session=1, tag=frame["tag"], path=frame["path"],
                        physical=frame["physical"],
                        requested_wait_us=frame["wait_us"],
                        capture_id=record.get("capture_id"),
                        release_id=record.get("release_id"),
                        iq_sha256=record.get("iq_sha256"),
                        iq_file=record.get("iq_file"),
                        metadata_file=record.get("metadata_file"),
                        capture_observation_file=record.get("capture_observation_file"),
                        capture_observation=record.get("capture_observation"),
                        timing=timing, tx_load_evidence=load_evidence,
                        result=record.get("result"),
                        seconds=time.monotonic() - t0, problem=None)
                    problem = _result_problem(frame, record)
                    if problem is None and frame["physical"]:
                        tr = timing or {}
                        trace = tr.get("last_capture_timing") or {}
                        if tr.get("reason") is not None:
                            problem = "capture_timing_unavailable"
                        elif trace.get("settle_wait_requested_us") != 0:
                            problem = "wait_mismatch"
                    row["problem"] = problem
                rows.append(row)
                (session_dir / ("frame-result-%s.json" % frame["tag"])).write_text(
                    json.dumps(row, indent=2, sort_keys=True), encoding="utf-8")
                if row["problem"]:
                    failure = "%s:%s" % (frame["tag"], row["problem"])
                    break
    except Exception as exc:
        failure = "%s: %s" % (type(exc).__name__, str(exc) or "runner error")
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
            counts["completed"]["sessions"] += 1
    elapsed = time.monotonic() - started
    structural_valid = failure is None
    summary = dict(
        schema="rf-qam64-path-compare-summary-v1",
        expected_release=expected_release, execution_complete=structural_valid,
        structural_valid=structural_valid, stop_cause=failure, counts=counts,
        budget=BUDGET, elapsed_s=elapsed, max_seconds=MAX_SECONDS, rows=rows,
        physical_errors=[dict(tag=r["tag"],
                              errors=(r.get("result") or {}).get("bit_errors"),
                              compared=(r.get("result") or {}).get("compared_bits"),
                              ber=(r.get("result") or {}).get("ber"))
                         for r in rows if r["path"] == "physical"
                         and r.get("result") is not None],
        digital_frames=[r["tag"] for r in rows if r["path"] == "digital"])
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "final.json").write_text(
        json.dumps({k: summary[k] for k in (
            "execution_complete", "structural_valid", "stop_cause", "counts",
            "elapsed_s", "physical_errors", "digital_frames")}, indent=2),
        encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="QAM64 digital/physical path comparison")
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    a = p.parse_args(argv)
    summary = run_path_compare(a.expected_release, a.output,
                               cable_confirmed=a.cable_confirmed)
    print(json.dumps({k: summary[k] for k in (
        "execution_complete", "structural_valid", "stop_cause", "counts",
        "elapsed_s", "physical_errors", "digital_frames")}, indent=2))
    return 0 if summary["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
