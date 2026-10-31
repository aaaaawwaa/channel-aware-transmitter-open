#!/usr/bin/env python3
"""One preauthorized lane1 tap30 session, frame=29 fixed.

A: all lanes 29; then lane1 mask=0x02/tap=30 with two captures B and C;
explicit restore all lanes 29; independent post-readback; offline all-bit compare.
No AD9361 reconfigure between frames; no retries; no tap32.
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, time
from collections import Counter
import numpy as np
from rf_bpsk_ui_board_session import LiveUiSession
from rf_qam64_digital_tap_probe_runner import (
    RawProtocolRecorder, _install_counts, _pl_snapshot, _save_digital,
    _recovery_tap29, CAPTURE_SECONDS)
from rf_qam64_tap_lane1_runner import BUDGET, WORKPOINT, _new_counts, _idelay
from rf_qam64_frame29_lane1_scan_runner import _idle_check, _compare_all

ATTEMPT = "qam64-frame29-lane1-tap30-2026-10-26"
BASELINE = dict(label="baseline29", mask=0x7F, tap=29)
POINT = dict(label="lane1-tap30", mask=0x02, tap=30)
ALLOWED = {(BASELINE["mask"], BASELINE["tap"]), (POINT["mask"], POINT["tap"])}
MAX_BOARD_SECONDS = 180.0
CAPTURE_DEADLINE = 10.0
BUDGET = dict(main_sessions=1, recovery_sessions=1, rf_init=2, rf_configure=2,
              tx_load=1, idelay_configure=2, digital_captures=3,
              tx_source_activations=3, physical_captures=0, retries=0)


def _budget_guard(counts, key):
    limit = BUDGET.get(key)
    if limit is not None and counts["attempted"].get(key, 0) >= limit:
        raise RuntimeError("budget_exceeded:" + key)


def _diag(session):
    s = session.client.poll().to_dict(); pl = s.get("pl_status") or {}
    return (pl.get("diagnostics") or {})


def _capture(session, counts, tag, tap, out, load_evidence):
    _budget_guard(counts, "digital_captures"); _budget_guard(counts, "tx_source_activations")
    counts["attempted"]["digital_captures"] += 1
    counts["attempted"]["tx_source_activations"] += 1
    try:
        frame = session.client.digital_capture(dict(samples=4096, ratio=0))
        counts["completed"]["digital_captures"] += 1
        counts["completed"]["tx_source_activations"] += 1
    except BaseException:
        counts["unknown"]["digital_captures"] += 1
        counts["unknown"]["tx_source_activations"] += 1
        raise
    iq, meta, m = _save_digital(out, tag, tap, frame, load_evidence)
    return dict(tag=tag, point="lane1-tap30" if tap == 30 else "baseline29",
                lane_mask=0x02 if tap == 30 else 0x7F, requested_tap=tap,
                path="digital", op=frame.op, physical=frame.physical,
                capture_id=frame.capture_id, iq_sha256=m["iq_sha256"],
                iq_file=iq.name, metadata_file=meta.name,
                post_rx_armed=frame.post_readback.rx_armed,
                post_tx_source_selected=frame.post_readback.tx_source_selected)


def _session(expected_release, out, counts, records, snapshots, deadline, holder):
    sd = out / "session-1"; sd.mkdir(parents=True, exist_ok=True)
    counts["attempted"]["main_sessions"] += 1
    rec = RawProtocolRecorder(sd / "raw-protocol-errors.jsonl")
    session = LiveUiSession(expected_release, sd, tx_mode="QAM64", seed=39, amplitude=60,
        session_index=1, run_id="frame29-lane1-tap30",
        rx_mode="QAM64", rx_seed=39, tx_shaping="RECT", rx_shaping="RECT",
        tx_rolloff="0.35", rx_rolloff="0.35", capture_deadline=CAPTURE_DEADLINE)
    holder["session"] = session
    if hasattr(session.client, "client"):
        session.client.client.capture_timeout = CAPTURE_DEADLINE
        rec.install(session.client.client)
    if not callable(getattr(getattr(session.client, "client", None), "set_idelay_lanes", None)):
        raise RuntimeError("missing_set_idelay_lanes")
    initial = session.initial()
    if not isinstance(initial, dict) or initial.get("safe") is not True or initial.get("release_id") != expected_release:
        raise RuntimeError("initial_not_safe_or_release_mismatch")
    snapshots.append(_pl_snapshot(session, "after_initial"))
    _install_counts(session, counts)
    why = session.app.set_rf_parameter("idelay_tap", 29)
    if why is not None: raise RuntimeError("set_tap29:" + why)
    session.setup(39)
    load_evidence = (getattr(session, "_tx_load_records", []) or [None])[-1]
    (sd / "tx-load-evidence.json").write_text(json.dumps(load_evidence, indent=2, sort_keys=True), encoding="utf-8")
    snapshots.append(_pl_snapshot(session, "after_load"))
    info = session.client.client._request(time.monotonic() + CAPTURE_DEADLINE, "device.info", {})
    (sd / "device-info-armed.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    if "idelay.configure" not in ((info.get("capabilities") or {}).get("control_ops") or []):
        raise RuntimeError("new_op_not_advertised")
    rows = []
    # A baseline
    ok, idle = _idle_check(session)
    if not ok: raise RuntimeError("baseline_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
    _budget_guard(counts, "digital_captures"); _budget_guard(counts, "tx_source_activations")
    snapshots.append(_pl_snapshot(session, "before_baseline"))
    rows.append(_capture(session, counts, "frame29-baseline29", 29, sd, load_evidence))
    snapshots.append(_pl_snapshot(session, "after_baseline"))
    # exact point
    if (POINT["mask"], POINT["tap"]) not in ALLOWED: raise RuntimeError("unapproved_mask_tap")
    ok, idle = _idle_check(session)
    if not ok: raise RuntimeError("probe_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
    _budget_guard(counts, "idelay_configure")
    _idelay(session, POINT["mask"], POINT["tap"], counts, records, POINT["label"])
    snapshots.append(_pl_snapshot(session, "after_tap30"))
    diag = _diag(session)
    if (diag.get("idelay_en", {}).get("raw") != POINT["mask"] or
            diag.get("idelay_tap", {}).get("raw") != POINT["tap"]):
        raise RuntimeError("tap30_config_readback_mismatch")
    for name in ("frame29-lane1-tap30-b", "frame29-lane1-tap30-c"):
        if time.monotonic() > deadline: raise RuntimeError("board_deadline_exceeded_in_session")
        ok, idle = _idle_check(session)
        if not ok: raise RuntimeError("capture_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
        rows.append(_capture(session, counts, name, 30, sd, load_evidence))
        snapshots.append(_pl_snapshot(session, "after_" + name))
    counts["completed"]["main_sessions"] += 1
    return session, sd, rows


def run(expected_release, output, cable_confirmed=False, attempt=ATTEMPT):
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()): raise RuntimeError("output_already_nonempty")
    output.mkdir(parents=True, exist_ok=True)
    counts = _new_counts()
    for k in ("idelay_configure",):
        counts["attempted"][k] = 0; counts["completed"][k] = 0; counts["unknown"][k] = 0
    records, snapshots, failures = [], [], []
    if not cable_confirmed: failures.append("cable_confirmation_missing")
    # Reject an unapproved point before any control/session object is created.
    if (POINT["mask"], POINT["tap"]) not in ALLOWED or (BASELINE["mask"], BASELINE["tap"]) not in ALLOWED:
        failures.append("unapproved_mask_tap")
    started = time.monotonic(); deadline = started + MAX_BOARD_SECONDS
    (output / "plan.json").write_text(json.dumps(dict(
        schema="qam64-frame29-lane1-tap30-plan-v1", attempt=attempt,
        expected_release=expected_release, budget=BUDGET, workpoint=WORKPOINT,
        baseline=BASELINE, point=POINT, max_board_seconds=MAX_BOARD_SECONDS,
        capture_deadline=CAPTURE_DEADLINE,
        policy="frame=29 and all other lanes=29; only lane1 tap30; two frames; explicit restore; no AD9361 reconfigure; no tap32; no retry"), indent=2), encoding="utf-8")
    session = None; rows = []; analysis = {}; post = {}; holder = {}; restore_done = False
    try:
        if failures: raise RuntimeError(failures[0])
        session, sd, rows = _session(expected_release, output, counts, records, snapshots, deadline, holder)
        if time.monotonic() > deadline: raise RuntimeError("board_deadline_exceeded_before_restore")
        ok, idle = _idle_check(session)
        if not ok: raise RuntimeError("restore_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
        _budget_guard(counts, "idelay_configure")
        _idelay(session, 0x7F, 29, counts, records, "restore-all-29")
        restore_done = True
        snapshots.append(_pl_snapshot(session, "after_restore"))
    except BaseException as exc:
        failures.append(type(exc).__name__ + ": " + (str(exc) or "runner error"))
        if session is None: session = holder.get("session")
    finally:
        if session is not None:
            # On a research failure the normal restore above was skipped; attempt
            # the guarded restore before cleanup.  If the state guard refuses, the
            # reserved recovery path below handles all-29 without load/capture.
            if not restore_done and any(r.get("tap") == 30 or r.get("mask") == 2 for r in records):
                try:
                    _idelay(session, 0x7F, 29, counts, records, "restore-on-failure")
                    restore_done = True
                    snapshots.append(_pl_snapshot(session, "after_restore_on_failure"))
                except BaseException as exc:
                    failures.append("restore_on_failure:" + type(exc).__name__ + ": " + (str(exc) or ""))
            try:
                cleanup = session.cleanup()
                if not isinstance(cleanup, dict) or cleanup.get("safe") is not True or cleanup.get("disarm_ack") is not True:
                    failures.append("session_cleanup_unsafe")
            except BaseException as exc: failures.append("session_cleanup:" + type(exc).__name__ + ": " + (str(exc) or ""))
            try: session.close()
            except BaseException as exc: failures.append("session_close:" + type(exc).__name__ + ": " + (str(exc) or ""))
        try:
            from rf_status_client import RfStatusClient
            pc = RfStatusClient()
            try: ps = pc.poll().to_dict()
            finally: pc.close()
            post = dict(available=True, release_id=ps.get("release_id"), armed=ps.get("armed"),
                        core=ps.get("core"), diagnostics=(ps.get("pl_status") or {}).get("diagnostics"))
        except BaseException as exc:
            post = dict(available=False, error=type(exc).__name__ + ": " + (str(exc) or ""))
        diag = post.get("diagnostics") or {}
        restored = (post.get("available") is True and diag.get("idelay_en", {}).get("raw") == 0x7F
                    and diag.get("idelay_tap", {}).get("raw") == 29)
        if not restored and counts["attempted"]["rf_init"] > 0:
            failures.append("restore_not_verified")
            try:
                counts["attempted"]["recovery_sessions"] += 1
                _recovery_tap29(expected_release, output, counts, [])
                counts["completed"]["recovery_sessions"] += 1
                try:
                    pc = RfStatusClient()
                    try: ps = pc.poll().to_dict()
                    finally: pc.close()
                    post = dict(available=True, release_id=ps.get("release_id"), armed=ps.get("armed"),
                                core=ps.get("core"), diagnostics=(ps.get("pl_status") or {}).get("diagnostics"))
                except BaseException as exc:
                    post = dict(available=False, error=type(exc).__name__ + ": " + (str(exc) or ""))
                diag = post.get("diagnostics") or {}
                restored = (diag.get("idelay_en", {}).get("raw") == 0x7F and
                            diag.get("idelay_tap", {}).get("raw") == 29)
            except BaseException as exc:
                failures.append("recovery_failed:" + type(exc).__name__ + ": " + (str(exc) or ""))
        # RF closed before expensive analysis.
        try:
            load = json.loads((output / "session-1" / "tx-load-evidence.json").read_text(encoding="utf-8"))
            tx = output / "session-1" / load["payload_file"]
            for row in rows:
                iq = output / "session-1" / row["iq_file"]
                analysis[row["tag"]] = _compare_all(tx, iq)
        except BaseException as exc:
            failures.append("offline_analysis:" + type(exc).__name__ + ": " + (str(exc) or ""))
    elapsed = time.monotonic() - started
    summary = dict(schema="qam64-frame29-lane1-tap30-summary-v1", attempt=attempt,
                   expected_release=expected_release, execution_complete=not failures,
                   stop_cause="; ".join(failures) if failures else None,
                   counts=counts, budget=BUDGET, elapsed_s=elapsed,
                   max_board_seconds=MAX_BOARD_SECONDS, rows=rows,
                   idelay_records=records, pl_snapshots=snapshots,
                   post_readback=post, analysis=analysis, failures=failures,
                   restored_tap=29 if restored else None,
                   tap29_restore_pending=not restored)
    (output/"summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output/"final.json").write_text(json.dumps({k: summary[k] for k in (
        "attempt","execution_complete","stop_cause","counts","elapsed_s","restored_tap",
        "tap29_restore_pending","analysis","rows","post_readback")}, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    p.add_argument("--attempt", default=ATTEMPT)
    a = p.parse_args(argv)
    s = run(a.expected_release, a.output, a.cable_confirmed, a.attempt)
    print(json.dumps({k: s[k] for k in ("attempt","execution_complete","stop_cause","counts","elapsed_s","restored_tap","tap29_restore_pending","analysis")}, indent=2))
    return 0 if s["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
