#!/usr/bin/env python3
"""One-session, digital-only IDELAY tap comparison runner (no physical captures).

Fixed order: tap29 baseline (2) -> tap25 (2) -> tap27 (2) -> tap31 (2) -> restore tap29 (2).
Uses the existing daemon ``rf.configure`` and ``loopback.digital.capture`` only.
The tap change also runs the daemon's ordinary AD9361 configuration; this is not
a pure tap write and no independent tap readback is invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import time

from rf_bpsk_ui_board_session import LiveUiSession

MAX_SECONDS = 180.0
CAPTURE_SECONDS = 10.0
BUDGET = dict(sessions=1, rf_init=1, tx_load=1, rf_configure=5,
              digital_captures=10, tx_source_activations=10,
              physical_captures=0, retries=0)
TAPS = (29, 25, 27, 31, 29)
CAPS_PER_TAP = 2


def _precheck(session):
    for name in ("initial", "setup", "cleanup", "close"):
        if not callable(getattr(session, name, None)):
            return "missing_session_method:" + name
    client = getattr(session, "client", None)
    if client is None:
        return "missing_session_client"
    for name in ("observe_capture_timing", "digital_capture", "capture"):
        if not callable(getattr(client, name, None)):
            return "missing_client_method:" + name
    if not hasattr(client, "capture_extra"):
        return "missing_client_capture_extra"
    return None


def _install_control_counts(session, counts, configure_records=None):
    previous = session.client.control_op

    def recorded(op, args=None, payload=b""):
        if op not in ("rf.init", "tx.load", "rf.configure"):
            return previous(op, args, payload=payload)
        name = {"rf.init": "rf_init", "tx.load": "tx_load",
                "rf.configure": "rf_configure"}[op]
        counts["attempted"][name] += 1
        request = dict(args or {})
        try:
            result = previous(op, args, payload=payload)
        except BaseException as exc:
            counts["unknown"][name] += 1
            if configure_records is not None and name == "rf_configure":
                configure_records.append(dict(
                    requested_tap=request.get("idelay_tap"),
                    request=request, response=None,
                    error=type(exc).__name__ + ": " + str(exc),
                    evidence="request+exception; no independent tap readback"))
            raise
        counts["completed"][name] += 1
        if configure_records is not None and name == "rf_configure":
            configure_records.append(dict(
                requested_tap=request.get("idelay_tap"), request=request,
                response=result,
                evidence="request+response+operation success; no independent "
                         "tap register readback"))
        return result

    session.client.control_op = recorded


def _write_digital(out, tag, tap, frame, load_evidence):
    iq_path = out / (tag + ".iq")
    meta_path = out / (tag + ".json")
    iq_path.write_bytes(frame.payload)
    meta = dict(
        schema="qam64-digital-tap-frame-v1", tag=tag, path="digital",
        op=frame.op, physical=frame.physical, capture_id=frame.capture_id,
        samples=frame.samples, sample_rate_hz=frame.sample_rate_hz,
        ratio=frame.ratio, daemon_timestamp_ns=frame.daemon_timestamp_ns,
        connection_generation=frame.connection_generation,
        config_revision=frame.config_revision, request_id=frame.request_id,
        release_id=frame.release_id, requested_tap=tap,
        iq_sha256=hashlib.sha256(frame.payload).hexdigest(),
        post_rx_armed=frame.post_readback.rx_armed,
        post_tx_source_selected=frame.post_readback.tx_source_selected,
        pre_tx_source_selected=frame.pre_readback.tx_source_selected,
        tx_load_evidence=load_evidence,
    )
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True),
                         encoding="utf-8")
    return iq_path, meta_path, meta


def run_tap_compare(expected_release, output, *, session_factory=LiveUiSession,
                    cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit current cable confirmation is required")
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("refusing to overwrite existing execution output: %s" % output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="rf-qam64-digital-tap-plan-v1",
                expected_release=expected_release,
                taps=list(TAPS), captures_per_tap=CAPS_PER_TAP,
                budget=BUDGET, max_seconds=MAX_SECONDS,
                capture_seconds=CAPTURE_SECONDS,
                order="tap29 baseline -> tap25 -> tap27 -> tap31 -> restore tap29",
                policy="one session, one rf.init/load; digital captures only; "
                       "each tap uses rf.configure (not pure tap write); no retry; "
                       "restore tap29 before close; no physical capture")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    counts = dict(
        attempted=dict(sessions=0, rf_init=0, tx_load=0, rf_configure=0,
                       captures=0, tx_source_activations=0, physical_captures=0),
        completed=dict(sessions=0, rf_init=0, tx_load=0, rf_configure=0,
                       captures=0, tx_source_activations=0, physical_captures=0),
        unknown=dict(sessions=0, rf_init=0, tx_load=0, rf_configure=0,
                     captures=0, tx_source_activations=0, physical_captures=0),
        precheck_reads=0)
    rows = []
    configure_records = []
    failure = None
    session = None
    session_dir = output / "session-1"
    session_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    counts["attempted"]["sessions"] += 1
    try:
        session = session_factory(
            expected_release, session_dir, tx_mode="QAM64", seed=39,
            amplitude=60, session_index=1, run_id="digital-tap-s1",
            rx_mode="QAM64", rx_seed=39, tx_shaping="RECT",
            rx_shaping="RECT", tx_rolloff="0.35", rx_rolloff="0.35",
            capture_deadline=CAPTURE_SECONDS)
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
            _install_control_counts(session, counts, configure_records)
            # The setup itself performs the initial configure, so baseline tap29
            # is already the configured state.  Explicitly request it first so
            # the requested value is part of the plan/evidence.
            why = session.app.set_rf_parameter("idelay_tap", TAPS[0])
            if why is not None:
                failure = "set_tap_baseline:" + why
            session.setup(39)
            load_evidence = None
            records = getattr(session, "_tx_load_records", None)
            if isinstance(records, list) and records:
                load_evidence = records[-1]
            (session_dir / "tx-load-evidence.json").write_text(
                json.dumps(load_evidence, indent=2, sort_keys=True),
                encoding="utf-8")
            if failure is None:
                group_names = ("baseline", "25", "27", "31", "restore")
                for group_index, tap in enumerate(TAPS):
                    if time.monotonic() > deadline:
                        failure = "batch_deadline"
                        break
                    if group_index > 0:
                        why = session.app.set_rf_parameter("idelay_tap", tap)
                        if why is not None:
                            failure = "set_tap_%d:%s" % (tap, why)
                            break
                        args, why = session.app.rf_parameter_plan("configure")
                        if why is not None:
                            failure = "plan_configure_%d:%s" % (tap, why)
                            break
                        try:
                            queued = session.worker.submit_control(
                                "configure", "/etc/rf-control.token", args)
                            if queued.get("queued") is not True:
                                raise RuntimeError("configure not queued: %r" % (queued,))
                            response = session._wait_control("configure")
                        except BaseException:
                            raise
                        if response is not None and response.get("ok") is False:
                            failure = "configure_refused_%d" % tap
                            break
                    for n in range(CAPS_PER_TAP):
                        tag = "tap%02d-%s-%d" % (tap, group_names[group_index],
                                                 n + 1)
                        counts["attempted"]["captures"] += 1
                        counts["attempted"]["tx_source_activations"] += 1
                        t0 = time.monotonic()
                        try:
                            frame = session.client.digital_capture(
                                dict(samples=4096, ratio=0))
                            counts["completed"]["captures"] += 1
                            counts["completed"]["tx_source_activations"] += 1
                        except BaseException:
                            counts["unknown"]["captures"] += 1
                            counts["unknown"]["tx_source_activations"] += 1
                            raise
                        iq_path, meta_path, meta = _write_digital(
                            session_dir, tag, tap, frame, load_evidence)
                        row = dict(
                            session=1, tag=tag, path="digital", requested_tap=tap,
                            requested_wait_us=None, op=frame.op,
                            physical=frame.physical, capture_id=frame.capture_id,
                            release_id=frame.release_id,
                            iq_sha256=meta["iq_sha256"], iq_file=iq_path.name,
                            metadata_file=meta_path.name,
                            post_rx_armed=frame.post_readback.rx_armed,
                            post_tx_source_selected=frame.post_readback.tx_source_selected,
                            tx_load_evidence=load_evidence,
                            seconds=time.monotonic() - t0, problem=None)
                        if frame.op != "loopback.digital.capture":
                            row["problem"] = "digital_op_mismatch"
                        elif frame.physical is not False:
                            row["problem"] = "digital_mislabeled_physical"
                        elif frame.post_readback.rx_armed is not False:
                            row["problem"] = "digital_rx_not_released"
                        elif frame.post_readback.tx_source_selected is not False:
                            row["problem"] = "digital_tx_source_not_off"
                        rows.append(row)
                        (session_dir / ("frame-result-%s.json" % tag)).write_text(
                            json.dumps(row, indent=2, sort_keys=True),
                            encoding="utf-8")
                        if row["problem"]:
                            failure = "%s:%s" % (tag, row["problem"])
                            break
                    if failure:
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
        schema="rf-qam64-digital-tap-summary-v1",
        expected_release=expected_release, execution_complete=structural_valid,
        structural_valid=structural_valid, stop_cause=failure, counts=counts,
        budget=BUDGET, elapsed_s=elapsed, max_seconds=MAX_SECONDS, rows=rows,
        configure_records=configure_records, taps=list(TAPS),
        restored_tap=TAPS[-1] if structural_valid else None,
        physical_captures=0)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "final.json").write_text(
        json.dumps({k: summary[k] for k in (
            "execution_complete", "structural_valid", "stop_cause", "counts",
            "elapsed_s", "taps", "restored_tap", "physical_captures")}, indent=2),
        encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="QAM64 digital IDELAY tap comparison")
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    a = p.parse_args(argv)
    summary = run_tap_compare(a.expected_release, a.output,
                              cable_confirmed=a.cable_confirmed)
    print(json.dumps({k: summary[k] for k in (
        "execution_complete", "structural_valid", "stop_cause", "counts",
        "elapsed_s", "taps", "restored_tap", "physical_captures")}, indent=2))
    return 0 if summary["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
