#!/usr/bin/env python3
"""Frame=29 fixed; compare a small set of data-lane1 IDELAY taps.

Session 1: baseline all-29, lane1 tap27, lane1 tap31, explicit restore all-29.
Session 2 (only if a point has zero lane1 errors and introduces no new other-bit
error type): fresh init/load, set selected lane1 point, two frames, restore all-29.
No AD9361 reconfigure between points, no product/RTL changes, no retries.
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, time
from collections import Counter
import numpy as np
from rf_bpsk_ui_board_session import LiveUiSession
from rf_qam64_digital_tap_probe_runner import (
    RawProtocolRecorder, _install_counts, _pl_snapshot, _save_digital,
    _recovery_tap29, CAPTURE_SECONDS, MAX_SECONDS)
from rf_qam64_tap_lane1_runner import BUDGET, WORKPOINT, _new_counts, _idelay

ATTEMPT = "qam64-frame29-lane1-scan-2026-10-26"
BASELINE = dict(label="baseline", mask=0x7F, tap=29)
POINTS = (dict(label="lane1-tap27", mask=0x02, tap=27),
          dict(label="lane1-tap31", mask=0x02, tap=31))
ALLOWED = {(BASELINE["mask"], BASELINE["tap"])} | {(p["mask"], p["tap"]) for p in POINTS}
MAX_BOARD_SECONDS = 180.0
CAPTURE_DEADLINE = 10.0
SESSION_BUDGET = dict(main_sessions=2, recovery_sessions=1, rf_init=3,
                      rf_configure=3, tx_load=2, idelay_configure=5,
                      digital_captures=5, tx_source_activations=5,
                      physical_captures=0, retries=0)


def _sha_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def _idle_check(session):
    """Read TX source/RX armed immediately before a tap write."""
    s = session.client.poll().to_dict()
    pl = s.get("pl_status") or {}
    io = pl.get("io_ok")
    tx = pl.get("tx") or {}
    rx = pl.get("rx") or {}
    return (io is True and tx.get("source_selected") is False
            and rx.get("armed") is False), {
                "io_ok": io, "tx_source_selected": tx.get("source_selected"),
                "rx_armed": rx.get("armed")}


def _budget_guard(counts, key):
    limit = SESSION_BUDGET.get(key)
    if limit is not None and counts["attempted"].get(key, 0) >= limit:
        raise RuntimeError("budget_exceeded:" + key)


def _diagnostics(session):
    s = session.client.poll().to_dict()
    pl = s.get("pl_status") or {}
    return dict(release_id=s.get("release_id"), safe=s.get("safe"),
                diagnostics=pl.get("diagnostics"), tx=pl.get("tx"),
                rx=pl.get("rx"), io_ok=pl.get("io_ok"))


def _compare_all(tx_path, iq_path):
    """All-bit, both I/Q components, direction; fixed cyclic alignment method."""
    tx = np.frombuffer(pathlib.Path(tx_path).read_bytes(), dtype="<i2").reshape(-1, 2).astype(np.int16)
    iq = np.frombuffer(pathlib.Path(iq_path).read_bytes(), dtype="<i2").reshape(-1, 2).astype(np.int16)
    best = None
    for swap in (False, True):
        d = iq[:, ::-1] if swap else iq
        for off in range(len(tx)):
            ref = tx[(np.arange(len(d)) + off) % len(tx)]
            mism = np.any(d != ref, axis=1)
            exact = int(np.count_nonzero(~mism))
            if best is None or exact > best["exact_pairs"]:
                best = dict(iq_swap=swap, offset=off, exact_pairs=exact,
                            mismatch_pairs=int(np.count_nonzero(mism)))
    sample = iq[:, ::-1] if best["iq_swap"] else iq
    ref = tx[(np.arange(len(sample)) + best["offset"]) % len(tx)]
    xor = np.bitwise_xor(sample.astype(np.uint16), ref.astype(np.uint16))
    bad = np.argwhere(xor != 0)
    hist = Counter(int(xor[i, j]) for i, j in bad)
    bits = {}
    for bit in range(16):
        events = []
        for i, j in bad:
            if (int(xor[i, j]) >> bit) & 1:
                direction = ("0->1" if ((int(sample[i, j]) >> bit) & 1)
                             and not ((int(ref[i, j]) >> bit) & 1) else "1->0")
                events.append(dict(sample=int(i), axis="I" if j == 0 else "Q",
                                   got=int(sample[i, j]), want=int(ref[i, j]),
                                   direction=direction))
        if events:
            bits[str(bit)] = dict(count=len(events),
                                  i=sum(1 for e in events if e["axis"] == "I"),
                                  q=sum(1 for e in events if e["axis"] == "Q"),
                                  direction=dict(Counter(e["direction"] for e in events)),
                                  first=events[:8])
    lane1_bits = {k: bits[k] for k in ("1", "7") if k in bits}
    lane1_count = sum(v["count"] for v in lane1_bits.values())
    other_types = sorted(int(k) for k in bits if int(k) not in (1, 7))
    samples = sorted({int(i) for i, _ in bad})
    runs = []
    if samples:
        start = prev = samples[0]
        for v in samples[1:]:
            if v == prev + 1: prev = v
            else:
                runs.append(dict(start=start, end=prev, length=prev - start + 1)); start = prev = v
        runs.append(dict(start=start, end=prev, length=prev - start + 1))
    return dict(alignment=best, exact_pairs=best["exact_pairs"],
                mismatch_pairs=best["mismatch_pairs"],
                xor_histogram={str(k): v for k, v in sorted(hist.items())},
                bits=bits, lane1_bits=lane1_bits, lane1_error_count=lane1_count,
                other_bit_types=other_types,
                bad_samples=len(samples), runs=runs, iq_sha256=_sha_file(iq_path))


def _point(session, point, counts, records, session_dir, tag_prefix, load_evidence, snapshots):
    ok, idle = _idle_check(session)
    if not ok:
        raise RuntimeError("quiet_check_failed:" + json.dumps(idle, sort_keys=True))
    if (point["mask"], point["tap"]) not in ALLOWED:
        raise RuntimeError("unapproved_mask_tap")
    _budget_guard(counts, "idelay_configure")
    _idelay(session, point["mask"], point["tap"], counts, records, point["label"])
    snapshots.append(_pl_snapshot(session, "after_" + point["label"]))
    diag = ((snapshots[-1].get("pl_status") or {}).get("diagnostics") or {})
    if (diag.get("idelay_en", {}).get("raw") != point["mask"] or
            diag.get("idelay_tap", {}).get("raw") != point["tap"]):
        raise RuntimeError("config_readback_mismatch:" + point["label"])
    _budget_guard(counts, "digital_captures")
    _budget_guard(counts, "tx_source_activations")
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
    iq, meta, m = _save_digital(session_dir, tag_prefix + "-" + point["label"], point["tap"], frame, load_evidence)
    return dict(tag=tag_prefix + "-" + point["label"], point=point["label"],
                lane_mask=point["mask"], requested_tap=point["tap"],
                path="digital", op=frame.op, physical=frame.physical,
                capture_id=frame.capture_id, iq_sha256=m["iq_sha256"], iq_file=iq.name,
                metadata_file=meta.name, post_rx_armed=frame.post_readback.rx_armed,
                post_tx_source_selected=frame.post_readback.tx_source_selected)


def _session1(expected_release, out, counts, records, snapshots, deadline, holder):
    sd = out / "session-1"; sd.mkdir(parents=True, exist_ok=True)
    counts["attempted"]["main_sessions"] += 1
    rec = RawProtocolRecorder(sd / "raw-protocol-errors.jsonl")
    session = LiveUiSession(expected_release, sd, tx_mode="QAM64", seed=39,
        amplitude=60, session_index=1, run_id="frame29-lane1-scan",
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
    snapshots.append(_pl_snapshot(session, "session1_after_initial"))
    _install_counts(session, counts)
    why = session.app.set_rf_parameter("idelay_tap", 29)
    if why is not None:
        raise RuntimeError("set_tap29:" + why)
    session.setup(39)
    load_evidence = None
    recs = getattr(session, "_tx_load_records", None)
    if isinstance(recs, list) and recs: load_evidence = recs[-1]
    (sd / "tx-load-evidence.json").write_text(json.dumps(load_evidence, indent=2, sort_keys=True), encoding="utf-8")
    snapshots.append(_pl_snapshot(session, "session1_after_load"))
    info = session.client.client._request(time.monotonic() + CAPTURE_DEADLINE, "device.info", {})
    (sd / "device-info-armed.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    if "idelay.configure" not in ((info.get("capabilities") or {}).get("control_ops") or []):
        raise RuntimeError("new_op_not_advertised")
    rows = []
    # Capture A: all-29 baseline; no idelay write.
    ok, idle = _idle_check(session)
    if not ok: raise RuntimeError("baseline_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
    snapshots.append(_pl_snapshot(session, "session1_before_baseline"))
    _budget_guard(counts, "digital_captures")
    _budget_guard(counts, "tx_source_activations")
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
    iq, meta, m = _save_digital(sd, "frame29-baseline", 29, frame, load_evidence)
    rows.append(dict(tag="frame29-baseline", point="baseline", lane_mask=0x7F,
                     requested_tap=29, path="digital", op=frame.op, physical=frame.physical,
                     capture_id=frame.capture_id, iq_sha256=m["iq_sha256"], iq_file=iq.name,
                     metadata_file=meta.name, post_rx_armed=frame.post_readback.rx_armed,
                     post_tx_source_selected=frame.post_readback.tx_source_selected))
    snapshots.append(_pl_snapshot(session, "session1_after_baseline"))
    for p in POINTS:
        if time.monotonic() > deadline:
            raise RuntimeError("board_deadline_exceeded_in_session1")
        rows.append(_point(session, p, counts, records, sd, "frame29", load_evidence, snapshots))
    counts["completed"]["main_sessions"] += 1
    return session, sd, rows


def run(expected_release, output, cable_confirmed=False, attempt=ATTEMPT):
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("output_already_nonempty")
    output.mkdir(parents=True, exist_ok=True)
    counts = _new_counts()
    for k in ("idelay_configure",):
        counts["attempted"][k] = 0; counts["completed"][k] = 0; counts["unknown"][k] = 0
    records, snapshots, failures = [], [], []
    if not cable_confirmed:
        failures.append("cable_confirmation_missing")
    started = time.monotonic(); deadline = started + MAX_BOARD_SECONDS
    plan = dict(schema="qam64-frame29-lane1-scan-plan-v1", attempt=attempt,
                expected_release=expected_release, budget=SESSION_BUDGET,
                points=[BASELINE] + list(POINTS), max_board_seconds=MAX_BOARD_SECONDS,
                capture_deadline=CAPTURE_DEADLINE, selection="lane1_error_count, then all errors; tie keeps 29",
                policy="frame=29 fixed; only lane1 mask=0x02; no AD9361 reconfigure; no retry; restore all-29")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    session = None; session2 = None; rows1 = []; rows2 = []; analysis = {}; post = {}
    try:
        if failures: raise RuntimeError(failures[0])
        holder = {}
        session, sd1, rows1 = _session1(expected_release, output, counts, records, snapshots, deadline, holder)
        # restore all-29; this is required even when all captures completed
        if time.monotonic() > deadline: raise RuntimeError("board_deadline_exceeded")
        ok, idle = _idle_check(session)
        if not ok: raise RuntimeError("restore_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
        _idelay(session, 0x7F, 29, counts, records, "restore-all-29")
        snapshots.append(_pl_snapshot(session, "session1_after_restore"))
    except BaseException as exc:
        failures.append(type(exc).__name__ + ": " + (str(exc) or "runner error"))
        if session is None:
            session = holder.get("session") if "holder" in locals() else None
    finally:
        if session is not None:
            try:
                cleanup = session.cleanup()
                if not isinstance(cleanup, dict) or cleanup.get("safe") is not True or cleanup.get("disarm_ack") is not True:
                    failures.append("session1_cleanup_unsafe")
            except BaseException as exc:
                failures.append("session1_cleanup:" + type(exc).__name__ + ": " + (str(exc) or ""))
            try: session.close()
            except BaseException as exc: failures.append("session1_close:" + type(exc).__name__ + ": " + (str(exc) or ""))
        # independent restoration check
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
        if not restored:
            failures.append("restore_not_verified")
            # one reserved recovery, no load/capture
            try:
                counts["attempted"]["recovery_sessions"] += 1
                recovery = _recovery_tap29(expected_release, output, counts, [])
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
        # RF is closed before this offline analysis.
        analysis = {}
        try:
            tx = None
            load_path = output / "session-1" / "tx-load-evidence.json"
            if load_path.is_file():
                load = json.loads(load_path.read_text(encoding="utf-8"))
                if load and load.get("payload_file"):
                    tx = output / "session-1" / load["payload_file"]
            if tx and tx.is_file():
                analysis["baseline"] = _compare_all(tx, output / "session-1" / "frame29-baseline.iq")
                for p in POINTS:
                    f = output / "session-1" / ("frame29-" + p["label"] + ".iq")
                    if f.is_file(): analysis[p["label"]] = _compare_all(tx, f)
        except BaseException as exc:
            failures.append("offline_analysis:" + type(exc).__name__ + ": " + (str(exc) or ""))
        # Selection only from completed frames; no capture after a failure.
        selected = None
        if all(k in analysis for k in ["baseline"] + [p["label"] for p in POINTS]):
            candidates = [("baseline", analysis["baseline"], 0x7F, 29)]
            for p in POINTS:
                candidates.append((p["label"], analysis[p["label"]], p["mask"], p["tap"]))
            base_other = set(analysis["baseline"]["other_bit_types"])
            viable = [c for c in candidates if c[1]["lane1_error_count"] == 0
                      and set(c[1]["other_bit_types"]) <= base_other]
            # Predefined rule: lane1 count, then all errors, then keep 29.
            ranked = sorted(candidates, key=lambda c: (c[1]["lane1_error_count"],
                                                       c[1]["mismatch_pairs"], 0 if c[2] == 0x7F else 1))
            analysis["selection_rule"] = "min lane1_error_count, then mismatch_pairs; tie keeps 29"
            analysis["viable_for_confirmation"] = [c[0] for c in viable]
            # `selected` is reserved for an eligible confirmation point; a merely
            # best-ranked point with nonzero lane1 errors is only an observation.
            analysis["best_observed"] = dict(label=ranked[0][0], mask=ranked[0][2],
                                             tap=ranked[0][3],
                                             lane1_error_count=ranked[0][1]["lane1_error_count"])
            if ranked[0][0] in [c[0] for c in viable] and ranked[0][0] != "baseline":
                selected = dict(label=ranked[0][0], mask=ranked[0][2], tap=ranked[0][3])
            analysis["selected"] = selected
        # Session 2 only when a point has zero lane1 errors and no new other-bit type.
        if selected is not None and selected["label"] in analysis.get("viable_for_confirmation", []):
            if time.monotonic() > deadline:
                failures.append("board_deadline_exceeded_before_session2")
            else:
                try:
                    session2, sd2, rows2 = _session2(expected_release, output, selected, counts, records, snapshots)
                    analysis["session2"] = {}
                    tx = output / "session-1" / json.loads((output / "session-1" / "tx-load-evidence.json").read_text())["payload_file"]
                    for row in rows2:
                        f = sd2 / row["iq_file"]
                        analysis["session2"][row["tag"]] = _compare_all(tx, f)
                except BaseException as exc:
                    failures.append("session2:" + type(exc).__name__ + ": " + (str(exc) or ""))
                    if session2 is not None:
                        try: session2.cleanup(); session2.close()
                        except BaseException: pass
    elapsed = time.monotonic() - started
    final_ok = not failures
    summary = dict(schema="qam64-frame29-lane1-scan-summary-v1", attempt=attempt,
                   expected_release=expected_release, execution_complete=final_ok,
                   stop_cause="; ".join(failures) if failures else None,
                   counts=counts, budget=SESSION_BUDGET, elapsed_s=elapsed,
                   max_board_seconds=MAX_BOARD_SECONDS, rows=rows1, rows2=rows2,
                   idelay_records=records, pl_snapshots=snapshots, post_readback=post,
                   analysis=analysis, failures=failures,
                   restored_tap=29 if restored else None,
                   tap29_restore_pending=not restored)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "final.json").write_text(json.dumps({k: summary[k] for k in (
        "attempt","execution_complete","stop_cause","counts","elapsed_s",
        "restored_tap","tap29_restore_pending","analysis","rows","rows2","post_readback")}, indent=2), encoding="utf-8")
    return summary


def _session2(expected_release, out, selected, counts, records, snapshots):
    sd = out / "session-2"; sd.mkdir(parents=True, exist_ok=True)
    counts["attempted"]["main_sessions"] += 1
    session = LiveUiSession(expected_release, sd, tx_mode="QAM64", seed=39, amplitude=60,
        session_index=2, run_id="frame29-lane1-confirm", rx_mode="QAM64", rx_seed=39,
        tx_shaping="RECT", rx_shaping="RECT", tx_rolloff="0.35", rx_rolloff="0.35",
        capture_deadline=CAPTURE_DEADLINE)
    _install_counts(session, counts)
    initial = session.initial()
    if not isinstance(initial, dict) or initial.get("safe") is not True or initial.get("release_id") != expected_release:
        raise RuntimeError("session2_initial_not_safe")
    why = session.app.set_rf_parameter("idelay_tap", 29)
    if why is not None: raise RuntimeError("session2_set_tap29:" + why)
    session.setup(39)
    load_evidence = (getattr(session, "_tx_load_records", []) or [None])[-1]
    (sd / "tx-load-evidence.json").write_text(json.dumps(load_evidence, indent=2, sort_keys=True), encoding="utf-8")
    rows = []
    try:
        ok, idle = _idle_check(session)
        if not ok: raise RuntimeError("session2_quiet_check_failed:" + json.dumps(idle, sort_keys=True))
        if (selected["mask"], selected["tap"]) not in ALLOWED: raise RuntimeError("unapproved_session2_point")
        _budget_guard(counts, "idelay_configure")
        _idelay(session, selected["mask"], selected["tap"], counts, records, "session2-" + selected["label"])
        snapshots.append(_pl_snapshot(session, "session2_after_selected"))
        for i in (1, 2):
            _budget_guard(counts, "digital_captures")
            _budget_guard(counts, "tx_source_activations")
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
            iq, meta, m = _save_digital(sd, "confirm-%d-%s" % (i, selected["label"]),
                                        selected["tap"], frame, load_evidence)
            rows.append(dict(tag="confirm-%d-%s" % (i, selected["label"]), point=selected["label"],
                             lane_mask=selected["mask"], requested_tap=selected["tap"], path="digital",
                             op=frame.op, physical=frame.physical, capture_id=frame.capture_id,
                             iq_sha256=m["iq_sha256"], iq_file=iq.name, metadata_file=meta.name))
        _idelay(session, 0x7F, 29, counts, records, "session2-restore-all-29")
    finally:
        try: cleanup = session.cleanup()
        except BaseException as exc: cleanup = None; snapshots.append(dict(label="session2_cleanup_error", error=str(exc)))
        try: session.close()
        except BaseException: pass
    if not isinstance(cleanup, dict) or cleanup.get("safe") is not True or cleanup.get("disarm_ack") is not True:
        raise RuntimeError("session2_cleanup_unsafe")
    counts["completed"]["main_sessions"] += 1
    return session, sd, rows


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
