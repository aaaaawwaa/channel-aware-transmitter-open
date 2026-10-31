#!/usr/bin/env python3
"""Bounded RX_DATA_DELAY comparison runner with an explicit recovery-only path.

The product candidate is already installed.  This runner is deliberately
dependency-injected so the complete orchestration can be tested without a board:
session construction, the post-close readback and IQ comparison can be replaced.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

import rf_status_client as _rfclient
from rf_bpsk_ui_board_session import LiveUiSession
from rf_qam64_digital_tap_probe_runner import (
    RawProtocolRecorder, _install_counts, _pl_snapshot, _save_digital)
from rf_qam64_tap_lane1_runner import _new_counts, _idelay
from rf_qam64_frame29_lane1_scan_runner import _idle_check, _compare_all

CANDIDATE = "036a4ec9ef4cea7fa108abbb02b00300"
PARENT = "6384a07e2d7d135bfb021b9ac75f337f"
MAX_SECONDS = 180.0
CAPTURE_SECONDS = 10.0
BUDGET = dict(main_sessions=1, recovery_sessions=1, rf_init=2, rf_configure=2,
              tx_load=1, rx_delay_set=5, idelay_configure=1,
              digital_captures=4, tx_source_activations=4, physical_captures=0,
              retries=0)


def _budget_guard(counts, key):
    if counts["attempted"].get(key, 0) >= BUDGET.get(key, 0):
        raise RuntimeError("budget_exceeded:" + key)


def _tick(now, counts, key, bucket):
    counts[bucket][key] = counts[bucket].get(key, 0) + 1


def _select(v):
    """Validate 0..15 and return integer (point1, point2-or-None).

    full 0..15 contract: internal values have two neighbours; boundaries have one.
    No wrap, no clamp.
    """
    if type(v) is not int or not 0 <= v <= 15:
        raise ValueError("actual RX_DATA_DELAY low nibble must be an integer 0..15")
    if v == 15:
        return 14, None
    if v == 0:
        return 1, None
    return v + 1, v - 1


def _read_status(session, records, label):
    response = session.client.get_interface_status()
    records.append({"stage": "rf.interface_status", "label": label, "response": response})
    return response


def _five(s):
    if not isinstance(s, dict) or s.get("valid") is not True:
        raise RuntimeError("interface_status_invalid")
    regs = s.get("registers")
    fields = s.get("fields")
    decoded = s.get("decoded") or {}
    if not isinstance(regs, dict) or not isinstance(fields, dict):
        raise RuntimeError("interface_status_malformed")
    for key in ("0x006", "0x007", "0x010", "0x011", "0x012"):
        if type(regs.get(key)) is not int:
            raise RuntimeError("interface_register_missing:" + key)
    for key in ("rx_data_delay", "data_clk_delay", "tx_data_delay", "fb_clk_delay"):
        if type(fields.get(key)) is not int:
            raise RuntimeError("interface_field_missing:" + key)
    if decoded.get("lvds_mode") is not True or decoded.get("single_data_rate") is not False:
        raise RuntimeError("not_lvds_ddr_precondition")
    return dict(regs), dict(fields)


def _check_readback(before, after, value):
    rb, rf = _five(after)
    b, _ = _five(before)
    want = (b["0x006"] & 0xF0) | value
    if rb["0x006"] != want or rf["rx_data_delay"] != value:
        raise RuntimeError("rx_delay_readback_mismatch")
    for key in ("0x007", "0x010", "0x011", "0x012"):
        if rb[key] != b[key]:
            raise RuntimeError("other_interface_register_changed:" + key)
    return rb


def _same_five(original, current):
    a, _ = _five(original)
    b, _ = _five(current)
    return a == b


class RecoveryOnlySession:
    """No-load/no-capture session used only to restore chip/FPGA state."""

    def __init__(self, expected_release, output, client_factory=None):
        self.output = pathlib.Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.expected = expected_release
        self._factory = client_factory or (lambda: _rfclient.RfStatusClient())
        self.client = self._factory()
        self.records = []
        self.token = None
        try:
            self.token = pathlib.Path("/etc/rf-control.token").read_text().strip()
        except OSError:
            self.token = None

    def _ctl(self, op, args=None):
        r = self.client.control_op(op, args or {})
        self.records.append({"op": op, "args": args or {}, "response": r})
        return r

    def _control(self, op):
        if op == "init":
            args = {"lo_hz": 80000000, "sample_rate_hz": 60000000,
                    "rx_bandwidth_hz": 30000000, "tx_bandwidth_hz": 30000000,
                    "attenuation_mdB": 40000, "phy_mode_1r1t": 0}
        else:
            args = {"idelay_en": 127, "idelay_tap": 29}
        return self._ctl(op, args)

    def prepare(self):
        if not self.token:
            raise RuntimeError("recovery_token_missing")
        self._ctl("control.arm", {"token": self.token})
        self._control("rf.init")
        self._control("rf.configure")

    def read(self):
        return self.client.get_interface_status()

    def set_original(self, value):
        return self.client.set_rx_data_delay(value)

    def restore_fpga(self):
        return self.client.set_idelay_lanes(0x7F, 29)

    def close(self):
        try:
            self.client.control_disarm()
        except BaseException:
            pass
        self.client.close()

    def cleanup(self):
        self.close()
        return {"safe": True}


def _default_session(expected, out):
    return LiveUiSession(
        expected, out, tx_mode="QAM64", seed=39, amplitude=60,
        session_index=1, run_id="frame29-rxdelay", rx_mode="QAM64", rx_seed=39,
        tx_shaping="RECT", rx_shaping="RECT", tx_rolloff="0.35",
        rx_rolloff="0.35", capture_deadline=CAPTURE_SECONDS)


def _default_post():
    c = _rfclient.RfStatusClient()
    try:
        s = c.poll().to_dict()
    finally:
        c.close()
    pl = s.get("pl_status") or {}
    tx = pl.get("tx") or {}
    rx = pl.get("rx") or {}
    # The read-only client does not expose a top-level `safe` field.  Derive it
    # from the fields that define a closed RF state; never fabricate True.
    safe = bool(pl.get("io_ok") is True and s.get("armed") is False
                and s.get("core") is False
                and tx.get("source_selected") is False and rx.get("armed") is False)
    return dict(available=True, release_id=s.get("release_id"), safe=safe,
                armed=s.get("armed"), core=s.get("core"), mode=s.get("mode"),
                pl_status=pl, diagnostics=pl.get("diagnostics"))


def run(expected_release, output, attempt, cable_confirmed=False, cable_reference=None,
        session_factory=None, post_factory=None, compare_func=None,
        recovery_factory=None, now_fn=None):
    """Run one bounded study.  Returns a summary with explicit restore/safety fields."""
    now = now_fn or time.monotonic
    out = pathlib.Path(output)
    if out.exists() and any(out.iterdir()):
        raise RuntimeError("output_already_nonempty")
    out.mkdir(parents=True, exist_ok=True)
    counts = _new_counts()
    for key in ("rx_delay_set", "idelay_configure"):
        for bucket in ("attempted", "completed", "unknown"):
            counts[bucket].setdefault(key, 0)
    for key in ("rx_delay_set_local_reject", "rx_delay_set_protocol_reject",
                "rx_delay_set_unknown"):
        counts["attempted"].setdefault(key, 0)
    records, snapshots, failures = [], [], []
    started = now()
    deadline = started + MAX_SECONDS
    plan = dict(schema="qam64-rxdelay-comparison-plan-v2", attempt=attempt,
                candidate=CANDIDATE, parent=PARENT, budget=BUDGET,
                max_seconds=MAX_SECONDS, capture_seconds=CAPTURE_SECONDS,
                cable_confirmed=bool(cable_confirmed), cable_reference=cable_reference,
                policy=("normal init/configure all FPGA lanes+frame=29; only AD9361 "
                        "0x006 low nibble; no other AD9361 parameter; no retry; restore "
                        "original chip low nibble and FPGA all-29"))
    (out / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    session = None
    study_complete = False
    chip_restored = False
    fpga_restored = False
    rf_safe = False
    post = {}
    analysis = {}
    original_v = None
    points = None
    interface_before = None
    rows = []
    point_write_attempted = False
    last_chip_readback = None
    close_error = None
    holder = {}
    load_evidence = None

    def deadline_guard(label):
        if now() > deadline:
            raise RuntimeError("deadline:" + label)

    def quiet(label):
        ok, facts = _idle_check(session)
        if not ok:
            raise RuntimeError("not_quiet:" + label)

    def capture(tag):
        deadline_guard("capture:" + tag)
        quiet("capture:" + tag)
        _budget_guard(counts, "digital_captures")
        _budget_guard(counts, "tx_source_activations")
        _tick(now, counts, "digital_captures", "attempted")
        _tick(now, counts, "tx_source_activations", "attempted")
        try:
            frame = session.client.digital_capture(dict(samples=4096, ratio=0))
            _tick(now, counts, "digital_captures", "completed")
            _tick(now, counts, "tx_source_activations", "completed")
        except BaseException:
            _tick(now, counts, "digital_captures", "unknown")
            _tick(now, counts, "tx_source_activations", "unknown")
            raise
        iq, meta, m = _save_digital(out / "session-1", tag, 29, frame, load_evidence)
        rows.append(dict(tag=tag, requested_tap=29, path="digital", op=frame.op,
                         physical=frame.physical, capture_id=frame.capture_id,
                         iq_sha256=m["iq_sha256"], iq_file=iq.name,
                         metadata_file=meta.name,
                         post_rx_armed=frame.post_readback.rx_armed,
                         post_tx_source_selected=frame.post_readback.tx_source_selected))
        snapshots.append(_pl_snapshot(session, "after_" + tag))

    def classify_set_exception(exc):
        if isinstance(exc, ValueError):
            counts["attempted"]["rx_delay_set_local_reject"] += 1
            return "local_reject"
        if isinstance(exc, _rfclient.StatusProtocolError):
            counts["attempted"]["rx_delay_set_protocol_reject"] += 1
            return "protocol_reject"
        counts["attempted"]["rx_delay_set_unknown"] += 1
        return "unknown"

    def call_set(value, label, stage):
        # attempted is recorded BEFORE the call; completion only after return.
        deadline_guard("set:" + label)
        quiet("set:" + label)
        _budget_guard(counts, "rx_delay_set")
        _tick(now, counts, "rx_delay_set", "attempted")
        try:
            response = session.client.set_rx_data_delay(value)
            _tick(now, counts, "rx_delay_set", "completed")
        except BaseException as exc:
            kind = classify_set_exception(exc)
            records.append(dict(stage=stage, label=label, value=value,
                                error=type(exc).__name__ + ": " + (str(exc) or ""),
                                error_kind=kind,
                                code=getattr(exc, "code", None)))
            raise
        records.append(dict(stage=stage, label=label, value=value, response=response))
        after = _read_status(session, records, "after_" + label)
        _check_readback(interface_before, after, value)
        return response, after

    def signal_restore(original, label):
        nonlocal last_chip_readback
        after = call_set(original, label, "restore")
        last_chip_readback = after
        return after

    def close_session():
        nonlocal session, close_error
        if session is None:
            return
        try:
            c = session.cleanup()
            if not isinstance(c, dict) or c.get("safe") is not True or c.get("disarm_ack") is not True:
                close_error = "session_cleanup_unsafe"
                failures.append(close_error)
        except BaseException as exc:
            close_error = type(exc).__name__ + ": " + (str(exc) or "")
            failures.append("session_cleanup:" + close_error)
        try:
            session.close()
        except BaseException as exc:
            part = "session_close:" + type(exc).__name__ + ": " + (str(exc) or "")
            close_error = (close_error + "; " if close_error else "") + part
            failures.append(part)
        session = None

    def recover_after_failure(reason):
        nonlocal chip_restored, last_chip_readback
        if session is None:
            return
        # A valid readback before release is the only accepted chip evidence.
        try:
            s = _read_status(session, records, "failure_readback")
            last_chip_readback = s
            if original_v is not None and _same_five(interface_before, s):
                chip_restored = True
                return
            if point_write_attempted and original_v is not None:
                signal_restore(original_v, "failure")
                s2 = _read_status(session, records, "failure_restored_readback")
                last_chip_readback = s2
                chip_restored = _same_five(interface_before, s2)
                return
        except BaseException as exc:
            failures.append("failure_readback/recovery_required: " + type(exc).__name__ + ": " + (str(exc) or ""))
        # Current session cannot prove chip state: use one separate recovery-only
        # session.  It may init/configure but never load or capture.
        try:
            if recovery_factory is None:
                failures.append("recovery_only_session_unavailable")
                return
            close_session()
            rec = recovery_factory(expected_release, out / "recovery-1")
            holder["recovery"] = rec
            counts["attempted"]["recovery_sessions"] += 1
            rec.prepare()
            s = rec.read()
            last_chip_readback = s
            if original_v is not None and not _same_five(interface_before, s):
                rec.set_original(original_v)
                s = rec.read()
                last_chip_readback = s
            rec.restore_fpga()
            rec.close()
            counts["completed"]["recovery_sessions"] += 1
            chip_restored = original_v is not None and _same_five(interface_before, s)
        except BaseException as exc:
            failures.append("recovery_only:" + type(exc).__name__ + ": " + (str(exc) or ""))
            try:
                holder.get("recovery").close()
            except BaseException:
                pass

    try:
        if not cable_confirmed:
            raise RuntimeError("cable_confirmation_required")
        if expected_release != CANDIDATE:
            raise RuntimeError("wrong_candidate")
        sd = out / "session-1"
        sd.mkdir(parents=True, exist_ok=True)
        counts["attempted"]["main_sessions"] += 1
        factory = session_factory or _default_session
        session = factory(expected_release, sd)
        holder["session"] = session
        if hasattr(session.client, "client"):
            if callable(getattr(session.client, "digital_capture", None)):
                try: session.client.client.capture_timeout = CAPTURE_SECONDS
                except BaseException: pass
            rec = RawProtocolRecorder(sd / "raw-protocol-errors.jsonl")
            try: rec.install(session.client.client)
            except BaseException: pass
        initial = session.initial()
        if not isinstance(initial, dict) or initial.get("safe") is not True or initial.get("release_id") != expected_release:
            raise RuntimeError("initial_not_safe_or_release_mismatch")
        _install_counts(session, counts)
        why = session.app.set_rf_parameter("idelay_tap", 29)
        if why is not None:
            raise RuntimeError("set_tap29:" + why)
        session.setup(39)
        load_evidence = (getattr(session, "_tx_load_records", []) or [None])[-1]
        (sd / "tx-load-evidence.json").write_text(json.dumps(load_evidence, indent=2, sort_keys=True), encoding="utf-8")
        snapshots.append(_pl_snapshot(session, "after_setup"))
        pl = snapshots[-1].get("pl_status") or {}
        diag = pl.get("diagnostics") or {}
        if diag.get("idelay_en", {}).get("raw") != 127 or diag.get("idelay_tap", {}).get("raw") != 29:
            raise RuntimeError("fpga_workpoint_not_all29")
        if not callable(getattr(session.client, "get_interface_status", None)) or not callable(getattr(session.client, "set_rx_data_delay", None)):
            raise RuntimeError("candidate_client_interface_missing")
        interface_before = _read_status(session, records, "before")
        _, fields = _five(interface_before)
        original_v = fields["rx_data_delay"]
        points = _select(original_v)
        plan["actual_interface"] = interface_before
        plan["original_low_nibble"] = original_v
        plan["points"] = {"point1": points[0], "point2": points[1]}
        (out / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        capture("A-original")
        call_set(points[0], "point1", "set")
        point_write_attempted = True
        capture("B-point1")
        signal_restore(original_v, "after_point1")
        capture("C-restored")
        if points[1] is not None:
            call_set(points[1], "point2", "set")
            point_write_attempted = True
            capture("D-point2")
            signal_restore(original_v, "after_point2")
        deadline_guard("fpga_restore")
        _budget_guard(counts, "idelay_configure")
        _idelay(session, 0x7F, 29, counts, records, "restore-fpga-all29")
        snapshots.append(_pl_snapshot(session, "after_fpga_restore"))
        final_chip = _read_status(session, records, "final_chip")
        last_chip_readback = final_chip
        chip_restored = _same_five(interface_before, final_chip)
        if point_write_attempted and not chip_restored:
            raise RuntimeError("chip_restore_not_verified")
        study_complete = True
        _tick(now, counts, "main_sessions", "completed")
        close_session()
    except BaseException as exc:
        failures.append(type(exc).__name__ + ": " + (str(exc) or "runner error"))
        try:
            recover_after_failure(exc)
        except BaseException as rec_exc:
            failures.append("failure_recovery:" + type(rec_exc).__name__ + ": " + (str(rec_exc) or ""))
        close_session()
    finally:
        if session is not None:
            close_session()
        try:
            post = (post_factory or _default_post)()
        except BaseException as exc:
            post = dict(available=False, error=type(exc).__name__ + ": " + (str(exc) or ""))
            failures.append("post_readback:" + post["error"])
        if post.get("available") is not True:
            failures.append("post_readback_unavailable")
        pl = post.get("pl_status") or {}
        diag = pl.get("diagnostics") or {}
        fpga_restored = bool(post.get("available") is True
                             and diag.get("idelay_en", {}).get("raw") == 127
                             and diag.get("idelay_tap", {}).get("raw") == 29)
        tx = pl.get("tx") or {}
        rx = pl.get("rx") or {}
        rf_safe = bool(post.get("available") is True and post.get("safe") is True
                       and post.get("armed") is False and post.get("core") is False
                       and tx.get("source_selected") is False and rx.get("armed") is False)
        if not fpga_restored:
            failures.append("fpga_restore_not_verified")
        if not rf_safe:
            failures.append("rf_safe_not_verified")
        if rf_safe:
            try:
                cmp = compare_func or _compare_all
                load = json.loads((out / "session-1" / "tx-load-evidence.json").read_text(encoding="utf-8"))
                tx_path = out / "session-1" / load["payload_file"]
                for row in rows:
                    analysis[row["tag"]] = cmp(tx_path, out / "session-1" / row["iq_file"])
            except BaseException as exc:
                failures.append("offline_analysis:" + type(exc).__name__ + ": " + (str(exc) or ""))
        else:
            failures.append("offline_analysis_deferred:rf_safe_false")
    elapsed = now() - started
    final_success = bool(study_complete and chip_restored and fpga_restored and rf_safe
                         and not failures and close_error is None)
    summary = dict(
        schema="qam64-rxdelay-comparison-summary-v2", attempt=attempt,
        expected_release=expected_release,
        study_complete=study_complete, execution_complete=study_complete,
        final_success=final_success,
        stop_cause="; ".join(failures) if failures else None,
        failures=failures, close_error=close_error,
        counts=counts, budget=BUDGET, elapsed_s=elapsed, max_seconds=MAX_SECONDS,
        interface_before=interface_before, original_rx_data_delay=original_v,
        points=points, rows=rows, records=records, snapshots=snapshots,
        chip_restored=chip_restored, fpga_restored=fpga_restored, rf_safe=rf_safe,
        last_chip_readback=last_chip_readback, post_readback=post,
        analysis=analysis, point_write_attempted=point_write_attempted,
        restored_tap=29 if fpga_restored else None,
        tap29_restore_pending=not fpga_restored)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out / "final.json").write_text(json.dumps({k: summary[k] for k in (
        "attempt", "study_complete", "execution_complete", "final_success",
        "stop_cause", "counts", "elapsed_s", "original_rx_data_delay", "points",
        "chip_restored", "fpga_restored", "rf_safe", "restored_tap",
        "tap29_restore_pending", "rows", "interface_before", "analysis")}, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--expected-release", default=CANDIDATE)
    p.add_argument("--output", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    p.add_argument("--cable-reference")
    a = p.parse_args(argv)
    s = run(a.expected_release, a.output, a.attempt, a.cable_confirmed,
            a.cable_reference)
    print(json.dumps({k: s[k] for k in (
        "attempt", "study_complete", "final_success", "stop_cause", "counts",
        "elapsed_s", "original_rx_data_delay", "points", "chip_restored",
        "fpga_restored", "rf_safe", "restored_tap", "tap29_restore_pending",
        "analysis")}, indent=2))
    return 0 if s["final_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
