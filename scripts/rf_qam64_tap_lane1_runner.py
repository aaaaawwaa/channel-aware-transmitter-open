#!/usr/bin/env python3
"""One bounded lane1-only IDELAY isolation session.

No AD9361 reconfigure between captures.  Baseline all-lane tap29, then
set_idelay_lanes(mask=0x02,tap=25) via the candidate control op, capture,
restore all lanes to 29.  The old rf.configure tap29->tap25 experiment is not run.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import time
from collections import Counter

import numpy as np
from rf_bpsk_ui_board_session import LiveUiSession
from rf_qam64_digital_tap_probe_runner import (
    RawProtocolRecorder, _install_counts, _pl_snapshot, _precheck, _save_digital,
    _recovery_tap29, CAPTURE_SECONDS, MAX_SECONDS)

BUDGET = dict(main_sessions=1, recovery_inits=1, rf_init=2, tx_load=1,
              rf_configure=2, idelay_configure=2, digital_captures=2,
              tx_source_activations=2, physical_captures=0, retries=0)
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, shaping="RECT", rolloff="0.35", iq_order="QI", seed=39)


def _new_counts():
    zero = dict(main_sessions=0, recovery_sessions=0, rf_init=0, tx_load=0,
                rf_configure=0, idelay_configure=0, digital_captures=0,
                tx_source_activations=0, physical_captures=0)
    return dict(attempted=dict(zero), completed=dict(zero), unknown=dict(zero),
                precheck_reads=0)


def _idelay(session, mask, tap, counts, records, label):
    client = getattr(getattr(session, "client", None), "client", None)
    if client is None or not callable(getattr(client, "set_idelay_lanes", None)):
        raise RuntimeError("missing_set_idelay_lanes")
    counts["attempted"]["idelay_configure"] += 1
    rec = dict(label=label, mask=mask, tap=tap, requested_at=time.monotonic())
    try:
        response = client.set_idelay_lanes(mask, tap)
        rec["response"] = response
        counts["completed"]["idelay_configure"] += 1
        records.append(rec)
        return response
    except BaseException as exc:
        counts["unknown"]["idelay_configure"] += 1
        rec["error"] = type(exc).__name__ + ": " + (str(exc) or "")
        records.append(rec)
        raise


def _compare(tx_path, iq_path):
    tx = np.frombuffer(pathlib.Path(tx_path).read_bytes(), dtype="<i2").reshape(-1, 2).astype(np.int16)
    iq = np.frombuffer(pathlib.Path(iq_path).read_bytes(), dtype="<i2").reshape(-1, 2).astype(np.int16)
    best = None
    for swap in (False, True):
        d = iq[:, ::-1] if swap else iq
        for off in range(len(tx)):
            ref = tx[(np.arange(len(d)) + off) % len(tx)]
            mismatch = np.any(d != ref, axis=1)
            exact = int(np.count_nonzero(~mismatch))
            if best is None or exact > best["exact_pairs"]:
                best = dict(iq_swap=swap, offset=off, exact_pairs=exact,
                            mismatch_pairs=int(np.count_nonzero(mismatch)))
    sample = iq[:, ::-1] if best["iq_swap"] else iq
    ref = tx[(np.arange(len(sample)) + best["offset"]) % len(tx)]
    xor = np.bitwise_xor(sample.astype(np.uint16), ref.astype(np.uint16))
    bad = np.argwhere(xor != 0)
    hist = Counter(int(xor[i, j]) for i, j in bad)
    bits = {}
    for bit in (1, 7):
        events = []
        for i, j in bad:
            if (int(xor[i, j]) >> bit) & 1:
                events.append(dict(sample=int(i), axis="I" if j == 0 else "Q",
                                   got=int(sample[i, j]), want=int(ref[i, j]),
                                   direction=("0->1" if ((int(sample[i, j]) >> bit) & 1) and
                                              not ((int(ref[i, j]) >> bit) & 1) else "1->0")))
        bits[str(bit)] = dict(count=len(events),
                              direction=dict(Counter(e["direction"] for e in events)))
    samples = sorted({int(i) for i, _ in bad})
    runs = []
    if samples:
        start = prev = samples[0]
        for v in samples[1:]:
            if v == prev + 1:
                prev = v
            else:
                runs.append(dict(start=start, end=prev, length=prev - start + 1))
                start = prev = v
        runs.append(dict(start=start, end=prev, length=prev - start + 1))
    return dict(alignment=best, xor_histogram={str(k): v for k, v in sorted(hist.items())},
                bits=bits, bad_samples=len({int(i) for i, _ in bad}), runs=runs,
                iq_sha256=hashlib.sha256(pathlib.Path(iq_path).read_bytes()).hexdigest())


def run(expected_release, output, cable_confirmed=False, probe_mask=0x02, probe_tap=25, tag="lane1"):
    output = pathlib.Path(output); output.mkdir(parents=True, exist_ok=True)
    counts = _new_counts(); rows = []; snapshots = []; idelay_records = []
    failure = None; session = None
    if not cable_confirmed:
        failure = "cable_confirmation_missing"
    plan = dict(schema="qam64-tap-probe-plan-v1", expected_release=expected_release,
                probe_mask=probe_mask, probe_tap=probe_tap, tag=tag,
                budget=BUDGET, workpoint=WORKPOINT, max_seconds=MAX_SECONDS,
                policy="one main session; baseline all-29 and lane1-only probe; no AD9361 reconfigure between captures; no retry; no old full tap25")
    (output/"plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    session_dir = output/"session-1"; session_dir.mkdir(parents=True, exist_ok=True)
    recorder = RawProtocolRecorder(session_dir/"raw-protocol-errors.jsonl")
    started = time.monotonic(); deadline = started + MAX_SECONDS
    counts["attempted"]["main_sessions"] += 1
    try:
        session = LiveUiSession(expected_release, session_dir, tx_mode="QAM64", seed=39,
            amplitude=60, session_index=1, run_id="tap-lane1",
            rx_mode="QAM64", rx_seed=39, tx_shaping="RECT", rx_shaping="RECT",
            tx_rolloff="0.35", rx_rolloff="0.35", capture_deadline=CAPTURE_SECONDS)
        if hasattr(session.client, "client"):
            session.client.client.capture_timeout = CAPTURE_SECONDS
            recorder.install(session.client.client)
        if not callable(getattr(getattr(session.client, "client", None), "set_idelay_lanes", None)):
            failure = "precheck:missing_set_idelay_lanes"
        if failure is None:
            counts["precheck_reads"] += 1
            initial = session.initial()
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                failure = "initial_not_safe"
            elif initial.get("release_id") != expected_release:
                failure = "initial_release_mismatch"
            snapshots.append(_pl_snapshot(session, "after_initial"))
        if failure is None:
            _install_counts(session, counts)
            why = session.app.set_rf_parameter("idelay_tap", 29)
            if why is not None:
                failure = "set_tap29:" + why
            else:
                session.setup(39)
                load_evidence = None
                records = getattr(session, "_tx_load_records", None)
                if isinstance(records, list) and records:
                    load_evidence = records[-1]
                (session_dir/"tx-load-evidence.json").write_text(json.dumps(load_evidence, indent=2, sort_keys=True), encoding="utf-8")
                snapshots.append(_pl_snapshot(session, "after_load_before_baseline"))
                # Read-only capability check on the armed live daemon: the new op
                # must be advertised before any RF capture is attempted.
                info = session.client.client._request(
                    time.monotonic() + CAPTURE_SECONDS, "device.info", {})
                ops = ((info.get("capabilities") or {}).get("control_ops") or [])
                (session_dir/"device-info-armed.json").write_text(
                    json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
                if "idelay.configure" not in ops:
                    failure = "new_op_not_advertised"
        if failure is None and time.monotonic() > deadline:
            failure = "research_deadline_exceeded"
        if failure is None:
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
            iq, meta, m = _save_digital(session_dir, tag + "-baseline", 29, frame, load_evidence)
            rows.append(dict(tag=tag + "-baseline", requested_tap=29, lane_mask=0x7F,
                             path="digital", op=frame.op, physical=frame.physical,
                             capture_id=frame.capture_id, iq_sha256=m["iq_sha256"],
                             iq_file=iq.name, metadata_file=meta.name,
                             post_rx_armed=frame.post_readback.rx_armed,
                             post_tx_source_selected=frame.post_readback.tx_source_selected))
            snapshots.append(_pl_snapshot(session, "after_baseline_before_lane1"))
            _idelay(session, probe_mask, probe_tap, counts, idelay_records, tag + "-probe")
            snapshots.append(_pl_snapshot(session, "after_lane1_before_probe"))
            pre = snapshots[-1]
            diag = ((pre.get("pl_status") or {}).get("diagnostics") or {})
            if (diag.get("idelay_en", {}).get("raw") != probe_mask or
                    diag.get("idelay_tap", {}).get("raw") != probe_tap):
                failure = "probe_config_readback_mismatch"
            if failure is None and time.monotonic() > deadline:
                failure = "research_deadline_exceeded"
            if failure is None:
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
                iq, meta, m = _save_digital(session_dir, tag + "-probe", probe_tap, frame, load_evidence)
                rows.append(dict(tag=tag + "-probe", requested_tap=probe_tap, lane_mask=probe_mask,
                                 path="digital", op=frame.op, physical=frame.physical,
                                 capture_id=frame.capture_id, iq_sha256=m["iq_sha256"],
                                 iq_file=iq.name, metadata_file=meta.name,
                                 post_rx_armed=frame.post_readback.rx_armed,
                                 post_tx_source_selected=frame.post_readback.tx_source_selected))
    except BaseException as exc:
        failure = type(exc).__name__ + ": " + (str(exc) or "runner error")
    finally:
        restored_in_main = False
        if session is not None:
            # Restore if the lane1 op was actually attempted, on success OR failure;
            # never bypass FAULT.
            if idelay_records:
                try:
                    _idelay(session, 0x7F, 29, counts, idelay_records, "restore-all-29")
                    snapshots.append(_pl_snapshot(session, "after_restore_in_main"))
                    restored_in_main = True
                except BaseException as exc:
                    if failure is None:
                        failure = "restore_idelay: " + (str(exc) or type(exc).__name__)
            cleanup = None
            try:
                cleanup = session.cleanup()
            except BaseException as exc:
                if failure is None:
                    failure = "cleanup: " + (str(exc) or type(exc).__name__)
            if (failure is None and (not isinstance(cleanup, dict) or
                    cleanup.get("safe") is not True or cleanup.get("disarm_ack") is not True)):
                failure = "cleanup_unsafe"
            try:
                session.close()
            except Exception as exc:
                if failure is None:
                    failure = "close: " + (str(exc) or type(exc).__name__)
        if failure is None:
            counts["completed"]["main_sessions"] += 1
    # Restoration is judged only by an independent read-only PL readback, never
    # by "failure is None".  A failed restore must remain visible.
    post_readback = {}
    try:
        from rf_status_client import RfStatusClient
        pc = RfStatusClient()
        try:
            ps = pc.poll().to_dict()
        finally:
            pc.close()
        post_readback = dict(available=True, release_id=ps.get("release_id"),
            armed=ps.get("armed"), core=ps.get("core"),
            diagnostics=(ps.get("pl_status") or {}).get("diagnostics"))
    except BaseException as exc:
        post_readback = dict(available=False, error=type(exc).__name__ + ": " + (str(exc) or ""))
    diag = (post_readback.get("diagnostics") or {})
    restored_ok = (post_readback.get("available") is True and
                   diag.get("idelay_en", {}).get("raw") == 0x7F and
                   diag.get("idelay_tap", {}).get("raw") == 29)
    recovery = None
    research_started = counts["attempted"]["rf_init"] > 0 or counts["attempted"]["digital_captures"] > 0
    if not restored_ok and research_started:
        recovery = _recovery_tap29(expected_release, output, counts, [])
        # recovery helper closes its session; verify with a second read-only read
        try:
            from rf_status_client import RfStatusClient
            pc = RfStatusClient()
            try: ps = pc.poll().to_dict()
            finally: pc.close()
            post_readback = dict(available=True, release_id=ps.get("release_id"),
                armed=ps.get("armed"), core=ps.get("core"),
                diagnostics=(ps.get("pl_status") or {}).get("diagnostics"))
            diag = (post_readback.get("diagnostics") or {})
            restored_ok = (diag.get("idelay_en", {}).get("raw") == 0x7F and
                           diag.get("idelay_tap", {}).get("raw") == 29)
        except BaseException as exc:
            post_readback = dict(available=False, error=type(exc).__name__ + ": " + (str(exc) or ""))
            restored_ok = False
    elif not restored_ok:
        recovery = dict(used=False, ok=False, reason="no research started")
    if not restored_ok and failure is None:
        failure = "restore_not_verified"
    elapsed = time.monotonic() - started
    # RF is already closed/cleaned before this offline comparison.
    comparison = {}
    try:
        base = session_dir/(tag + "-baseline.iq"); probe = session_dir/(tag + "-probe.iq")
        tx = session_dir/(json.loads((session_dir/"tx-load-evidence.json").read_text())["payload_file"])
        if base.is_file():
            comparison["baseline"] = _compare(tx, base)
        if probe.is_file():
            comparison["probe"] = _compare(tx, probe)
    except BaseException as exc:
        comparison["error"] = type(exc).__name__ + ": " + (str(exc) or "")
    summary = dict(schema="qam64-tap-lane1-summary-v1", expected_release=expected_release,
        execution_complete=failure is None, structural_valid=failure is None,
        stop_cause=failure, counts=counts, budget=BUDGET, elapsed_s=elapsed,
        max_seconds=MAX_SECONDS, rows=rows, idelay_records=idelay_records,
        pl_snapshots=snapshots, post_readback=post_readback,
        raw_protocol_errors=recorder.events,
        comparison=comparison, restored_tap=29 if restored_ok else None,
        tap29_restore_pending=not restored_ok, recovery=recovery,
        physical_captures=0)
    (output/"summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output/"final.json").write_text(json.dumps({k: summary[k] for k in
        ("execution_complete","structural_valid","stop_cause","counts","elapsed_s",
         "restored_tap","tap29_restore_pending","rows","comparison","recovery")}, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    p.add_argument("--mask", type=lambda x: int(x, 0), default=0x02)
    p.add_argument("--tap", type=int, default=25)
    p.add_argument("--tag", default="lane1")
    a = p.parse_args(argv)
    s = run(a.expected_release, a.output, a.cable_confirmed,
            probe_mask=a.mask, probe_tap=a.tap, tag=a.tag)
    print(json.dumps({k: s[k] for k in ("execution_complete","structural_valid","stop_cause","counts","elapsed_s","restored_tap","tap29_restore_pending","comparison")}, indent=2))
    return 0 if s["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
