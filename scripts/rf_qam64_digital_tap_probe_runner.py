#!/usr/bin/env python3
"""Bounded tap29->tap25 probe with raw daemon-refusal capture and tap29 recovery.

Main research session (at most one):
  read-only initial -> rf.init/configure29/load -> digital baseline
  -> configure25 -> digital probe -> configure29 restore

If the main session cannot be restored, one reserved recovery init is allowed:
  read-only initial -> arm/init/configure29 -> cleanup; no load and no capture.

The wrapper only observes `_send_and_read_json`: it records the daemon's raw
refusal (op, request id, error code/message, stage, elapsed) and returns the
original object unchanged.  No product/daemon/RTL code is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time

from rf_bpsk_ui_board_session import LiveUiSession

MAX_SECONDS = 180.0
CAPTURE_SECONDS = 10.0
BUDGET = dict(main_sessions=1, recovery_inits=1, rf_init=2, tx_load=1,
              rf_configure=3, digital_captures=2, tx_source_activations=2,
              physical_captures=0, retries=0)
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, shaping="RECT", rolloff="0.35", iq_order="QI",
                 seed=39)


class RawProtocolRecorder:
    """Record the daemon response before the client turns it into an exception."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.events = []
        self._seq = 0

    def install(self, client):
        previous = client._send_and_read_json

        def wrapped(sock, buffer, deadline, request_id, op, args,
                    payload=b"", validate_id=True):
            started = time.monotonic()
            try:
                response, rest = previous(
                    sock, buffer, deadline, request_id, op, args,
                    payload=payload, validate_id=validate_id)
            except BaseException as exc:
                self._append(dict(
                    seq=self._seq + 1, op=op, request_id=request_id,
                    stage="send_and_read_json_exception",
                    elapsed_s=time.monotonic() - started,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc)))
                raise
            if (op == "loopback.digital.capture"
                    and isinstance(response, dict)
                    and response.get("ok") is False):
                error = response.get("error")
                self._append(dict(
                    seq=self._seq + 1, op=op, request_id=request_id,
                    stage="daemon_response_refusal",
                    elapsed_s=time.monotonic() - started,
                    error_code=(error.get("code") if isinstance(error, dict) else None),
                    error_message=(error.get("message") if isinstance(error, dict) else None),
                    response=response))
                # Reuse the SAME socket/session and at most one extra request;
                # never reconnect and never replace the original refusal.
                self._probe_pl_status(client, previous, sock, rest, deadline,
                                      request_id + 1)
            return response, rest

        client._send_and_read_json = wrapped
        return self

    def _probe_pl_status(self, client, previous, sock, buffer, deadline,
                         request_id):
        started = time.monotonic()
        probe_deadline = min(deadline, started + 1.0)
        event = dict(
            seq=self._seq + 1, stage="post_refusal_pl_status",
            op="pl.status.get", request_id=request_id,
            requested_after_request_id=request_id - 1,
            elapsed_s=0.0, available=False, error=None,
            max_reads=3, reads=[],
            fields=dict(io_ok=None, rx_armed=None, rx_done=None,
                        rx_position_words=None, tx_source_selected=None,
                        tx_length_words=None, tx_position_words=None,
                        sampled_at_monotonic_ns=None))
        for attempt in range(3):
            if time.monotonic() >= probe_deadline:
                event["error"] = {"type": "status_observation_deadline"}
                break
            read_id = request_id + attempt
            read_started = time.monotonic()
            try:
                response, _ = previous(
                    sock, buffer, probe_deadline, read_id, "pl.status.get", {},
                    payload=b"", validate_id=True)
                client._session_id = max(int(getattr(client, "_session_id", 0)),
                                         read_id)
            except BaseException as exc:
                event["reads"].append(dict(
                    attempt=attempt + 1, request_id=read_id,
                    elapsed_s=time.monotonic() - read_started,
                    available=False,
                    error={"type": type(exc).__name__,
                           "message": str(exc) or "pl.status.get failed"}))
                event["error"] = {"type": type(exc).__name__,
                                  "message": str(exc) or "pl.status.get failed"}
                break
            elapsed = time.monotonic() - read_started
            read = dict(attempt=attempt + 1, request_id=read_id,
                        elapsed_s=elapsed, available=False, error=None,
                        response=response,
                        fields=dict(io_ok=None, rx_armed=None, rx_done=None,
                                    rx_position_words=None,
                                    tx_source_selected=None,
                                    tx_length_words=None,
                                    tx_position_words=None,
                                    sampled_at_monotonic_ns=None))
            if not isinstance(response, dict) or response.get("ok") is not True:
                error = response.get("error") if isinstance(response, dict) else None
                read["error"] = {
                    "type": "daemon_refused_pl_status",
                    "code": error.get("code") if isinstance(error, dict) else None,
                    "message": error.get("message") if isinstance(error, dict) else None,
                }
            else:
                result = response.get("result")
                if not isinstance(result, dict):
                    read["error"] = {"type": "missing_pl_status_result"}
                else:
                    read["available"] = True
                    read["result"] = result
                    fields = read["fields"]
                    rx = result.get("rx") if isinstance(result.get("rx"), dict) else {}
                    tx = result.get("tx") if isinstance(result.get("tx"), dict) else {}
                    def pick(mapping, key):
                        value = mapping.get(key)
                        return value if value is not None else None
                    fields["io_ok"] = pick(result, "io_ok")
                    fields["rx_armed"] = pick(rx, "armed")
                    fields["rx_done"] = pick(rx, "done")
                    fields["rx_position_words"] = pick(rx, "position_words")
                    fields["tx_source_selected"] = pick(tx, "source_selected")
                    fields["tx_length_words"] = pick(tx, "length_words")
                    fields["tx_position_words"] = pick(tx, "position_words")
                    fields["sampled_at_monotonic_ns"] = pick(
                        result, "sampled_at_monotonic_ns")
                    if not event["available"]:
                        event["available"] = True
                        event["fields"] = fields
            event["reads"].append(read)
            if attempt < 2:
                time.sleep(0.002)
        event["elapsed_s"] = time.monotonic() - started
        event["request_ids"] = [r["request_id"] for r in event["reads"]]
        event["seq"] = self._seq + 1
        self._append(event)

    def _append(self, event):
        self._seq += 1
        event["seq"] = self._seq
        # Written immediately, before the client converts/raises/drops the session.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
            f.flush()
        self.events.append(event)




def _pl_snapshot(session, label):
    """Read the existing live PL status snapshot; diagnostics only, no retry."""
    try:
        snap = session.client.poll().to_dict()
        return dict(label=label, available=True,
                    captured_at_monotonic=time.monotonic(),
                    safe=snap.get("safe"), pl_status=snap.get("pl_status"))
    except BaseException as exc:
        return dict(label=label, available=False,
                    captured_at_monotonic=time.monotonic(),
                    error=type(exc).__name__ + ": " + (str(exc) or "snapshot failed"))

def _precheck(session):
    for name in ("initial", "setup", "cleanup", "close"):
        if not callable(getattr(session, name, None)):
            return "missing_session_method:" + name
    client = getattr(session, "client", None)
    if client is None:
        return "missing_session_client"
    for name in ("digital_capture", "capture", "observe_capture_timing"):
        if not callable(getattr(client, name, None)):
            return "missing_client_method:" + name
    if not hasattr(client, "capture_extra"):
        return "missing_client_capture_extra"
    underlying = getattr(client, "client", None)
    if underlying is None or not callable(getattr(underlying, "_send_and_read_json", None)):
        return "missing_underlying_protocol"
    return None


def _install_counts(session, counts):
    previous = session.client.control_op

    def recorded(op, args=None, payload=b""):
        if op not in ("rf.init", "tx.load", "rf.configure"):
            return previous(op, args, payload=payload)
        name = {"rf.init": "rf_init", "tx.load": "tx_load",
                "rf.configure": "rf_configure"}[op]
        counts["attempted"][name] += 1
        try:
            result = previous(op, args, payload=payload)
        except BaseException:
            counts["unknown"][name] += 1
            raise
        counts["completed"][name] += 1
        return result

    session.client.control_op = recorded


def _save_digital(session_dir, tag, tap, frame, load_evidence):
    iq_path = session_dir / (tag + ".iq")
    meta_path = session_dir / (tag + ".json")
    iq_path.write_bytes(frame.payload)
    meta = dict(schema="qam64-tap-probe-frame-v1", tag=tag, path="digital",
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
                tx_load_evidence=load_evidence)
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True),
                         encoding="utf-8")
    return iq_path, meta_path, meta


def _configure(session, tap, configure_records, counts, label):
    why = session.app.set_rf_parameter("idelay_tap", tap)
    if why is not None:
        raise RuntimeError("set_tap_%s: %s" % (label, why))
    args, why = session.app.rf_parameter_plan("configure")
    if why is not None:
        raise RuntimeError("plan_configure_%s: %s" % (label, why))
    try:
        queued = session.worker.submit_control(
            "configure", "/etc/rf-control.token", args)
        if queued.get("queued") is not True:
            raise RuntimeError("configure not queued: %r" % (queued,))
        response = session._wait_control("configure")
    except BaseException:
        raise
    configure_records.append(dict(
        label=label, requested_tap=tap, request=args, response=response,
        evidence="request+response+operation success; no independent tap readback"))
    if response is not None and response.get("ok") is False:
        raise RuntimeError("configure_refused_%s" % label)
    return response


def _recovery_tap29(expected_release, parent_dir, counts, configure_records):
    """Reserved recovery: one init, configure tap29, cleanup; no load/capture."""
    rec_dir = parent_dir / "recovery-init"
    rec_dir.mkdir(parents=True, exist_ok=True)
    session = None
    counts["attempted"]["recovery_sessions"] += 1
    try:
        session = LiveUiSession(
            expected_release, rec_dir, tx_mode="QAM64", seed=39,
            amplitude=60, session_index=2, run_id="tap-probe-recovery",
            rx_mode="QAM64", rx_seed=39, tx_shaping="RECT",
            rx_shaping="RECT", tx_rolloff="0.35", rx_rolloff="0.35",
            capture_deadline=CAPTURE_SECONDS)
        if hasattr(session.client, "client"):
            session.client.client.capture_timeout = CAPTURE_SECONDS
        initial = session.initial()
        if not isinstance(initial, dict) or initial.get("safe") is not True:
            raise RuntimeError("recovery initial not safe")
        _install_counts(session, counts)
        # Recovery deliberately does NOT call setup(): setup would load TX.
        # rf.init/configure need the operator parameters the panel has not yet
        # populated in a fresh session, so seed only the required fields.
        for name, value in (("lo_hz", 80000000), ("sample_rate_hz", 60000000),
                            ("rx_bandwidth_hz", 30000000), ("tx_bandwidth_hz", 30000000),
                            ("attenuation_mdB", 46000), ("phy_mode_1r1t", 0),
                            ("idelay_en", 1), ("idelay_tap", 29)):
            why = session.app.set_rf_parameter(name, value)
            if why is not None:
                raise RuntimeError("recovery set %s: %s" % (name, why))
        for action in ("arm", "init"):
            args, why = session.app.rf_parameter_plan(action)
            if why is not None:
                raise RuntimeError("recovery plan %s: %s" % (action, why))
            queued = session.worker.submit_control(
                action, "/etc/rf-control.token", args)
            if queued.get("queued") is not True:
                raise RuntimeError("recovery queue %s: %r" % (action, queued))
            session._wait_control(action)
        _configure(session, 29, configure_records, counts, "recovery-tap29")
        result = dict(used=True, ok=True, initial=initial)
    except BaseException as exc:
        result = dict(used=True, ok=False,
                      error=type(exc).__name__ + ": " + str(exc))
    finally:
        if session is not None:
            cleanup = None
            try:
                cleanup = session.cleanup()
            except BaseException as exc:
                cleanup = {"error": type(exc).__name__ + ": " + str(exc)}
            try:
                session.close()
            except Exception:
                pass
            if result is None:
                result = dict(used=True, ok=False, error="recovery unknown")
            result["cleanup"] = cleanup
            if (result.get("ok") is True and (
                    not isinstance(cleanup, dict)
                    or cleanup.get("safe") is not True
                    or cleanup.get("disarm_ack") is not True)):
                result["ok"] = False
                result["error"] = "recovery cleanup unsafe"
            if result.get("ok") is True:
                counts["completed"]["recovery_sessions"] += 1
            else:
                counts["unknown"]["recovery_sessions"] += 1
    return result


def run_probe(expected_release, output, *, cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit current cable confirmation is required")
    output = pathlib.Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("refusing to overwrite existing execution output: %s" % output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="qam64-tap-probe-plan-v2",
                expected_release=expected_release, order=[
                    "initial read-only", "rf.init/configure29/load",
                    "digital baseline (1)", "configure tap25",
                    "digital probe (1)", "configure tap29 restore",
                    "close plus independent read-only post"],
                budget=BUDGET, workpoint=WORKPOINT,
                max_seconds=MAX_SECONDS, capture_seconds=CAPTURE_SECONDS,
                policy="one main research session; no tap27/31; no retry; "
                       "no load/capture in recovery; raw refusal preserved")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    session_dir = output / "session-1"
    session_dir.mkdir(parents=True, exist_ok=True)
    counts = dict(
        attempted=dict(main_sessions=0, recovery_sessions=0, rf_init=0,
                       tx_load=0, rf_configure=0, digital_captures=0,
                       tx_source_activations=0, physical_captures=0),
        completed=dict(main_sessions=0, recovery_sessions=0, rf_init=0,
                       tx_load=0, rf_configure=0, digital_captures=0,
                       tx_source_activations=0, physical_captures=0),
        unknown=dict(main_sessions=0, recovery_sessions=0, rf_init=0,
                     tx_load=0, rf_configure=0, digital_captures=0,
                     tx_source_activations=0, physical_captures=0),
        precheck_reads=0)
    rows = []
    configure_records = []
    pl_snapshots = []
    failure = None
    session = None
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    counts["attempted"]["main_sessions"] += 1
    recorder = RawProtocolRecorder(session_dir / "raw-protocol-errors.jsonl")
    try:
        session = LiveUiSession(
            expected_release, session_dir, tx_mode="QAM64", seed=39,
            amplitude=60, session_index=1, run_id="tap-probe",
            rx_mode="QAM64", rx_seed=39, tx_shaping="RECT",
            rx_shaping="RECT", tx_rolloff="0.35", rx_rolloff="0.35",
            capture_deadline=CAPTURE_SECONDS)
        if hasattr(session.client, "client"):
            session.client.client.capture_timeout = CAPTURE_SECONDS
            recorder.install(session.client.client)
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
            pl_snapshots.append(_pl_snapshot(session, "after_initial"))
        if failure is None:
            _install_counts(session, counts)
            why = session.app.set_rf_parameter("idelay_tap", 29)
            if why is not None:
                failure = "set_tap29:" + why
            session.setup(39)
            load_evidence = None
            records = getattr(session, "_tx_load_records", None)
            if isinstance(records, list) and records:
                load_evidence = records[-1]
            (session_dir / "tx-load-evidence.json").write_text(
                json.dumps(load_evidence, indent=2, sort_keys=True),
                encoding="utf-8")
            pl_snapshots.append(_pl_snapshot(session, "after_load_before_tap25"))
            if failure is None:
                # baseline
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
                iq, meta, m = _save_digital(session_dir, "tap29-baseline", 29,
                                            frame, load_evidence)
                rows.append(dict(tag="tap29-baseline", requested_tap=29,
                                 path="digital", op=frame.op,
                                 physical=frame.physical,
                                 capture_id=frame.capture_id,
                                 iq_sha256=m["iq_sha256"], iq_file=iq.name,
                                 metadata_file=meta.name, result=None,
                                 post_rx_armed=frame.post_readback.rx_armed,
                                 post_tx_source_selected=frame.post_readback.tx_source_selected))
                # probe tap25 (do not continue on any failure)
                pl_snapshots.append(_pl_snapshot(session, "before_configure_tap25"))
                _configure(session, 25, configure_records, counts, "tap25")
                pl_snapshots.append(_pl_snapshot(session, "after_configure_tap25"))
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
                iq, meta, m = _save_digital(session_dir, "tap25-probe", 25,
                                            frame, load_evidence)
                rows.append(dict(tag="tap25-probe", requested_tap=25,
                                 path="digital", op=frame.op,
                                 physical=frame.physical,
                                 capture_id=frame.capture_id,
                                 iq_sha256=m["iq_sha256"], iq_file=iq.name,
                                 metadata_file=meta.name, result=None,
                                 post_rx_armed=frame.post_readback.rx_armed,
                                 post_tx_source_selected=frame.post_readback.tx_source_selected))
                # restore tap29 in the main session if still usable
                _configure(session, 29, configure_records, counts, "restore-tap29")
    except BaseException as exc:
        failure = type(exc).__name__ + ": " + (str(exc) or "runner error")
    finally:
        if session is not None:
            cleanup_result = None
            try:
                cleanup_result = session.cleanup()
            except BaseException as exc:
                if failure is None:
                    failure = "cleanup: " + (str(exc) or type(exc).__name__)
            if (failure is None and (
                    not isinstance(cleanup_result, dict)
                    or cleanup_result.get("safe") is not True
                    or cleanup_result.get("disarm_ack") is not True)):
                failure = "cleanup_unsafe"
            try:
                session.close()
            except Exception as exc:
                if failure is None:
                    failure = "close: " + (str(exc) or type(exc).__name__)
        if failure is None:
            counts["completed"]["main_sessions"] += 1
    # A failure after a research step gets one reserved recovery init only if
    # the main session's tap29 restore did not already succeed.
    restored_in_main = failure is None
    recovery = None
    research_started = (counts["attempted"]["rf_init"] > 0
                        or counts["attempted"]["tx_load"] > 0
                        or counts["attempted"]["digital_captures"] > 0)
    if not restored_in_main and research_started:
        recovery = _recovery_tap29(expected_release, output, counts,
                                   configure_records)
    elif not restored_in_main:
        recovery = dict(used=False, ok=False,
                        reason="no physical research started; recovery init not used")
    elapsed = time.monotonic() - started
    restored_ok = restored_in_main or bool(recovery and recovery.get("ok"))
    summary = dict(
        schema="qam64-tap-probe-summary-v2",
        expected_release=expected_release, execution_complete=failure is None,
        structural_valid=failure is None, stop_cause=failure, counts=counts,
        budget=BUDGET, elapsed_s=elapsed, max_seconds=MAX_SECONDS,
        rows=rows, configure_records=configure_records,
        pl_snapshots=pl_snapshots,
        raw_protocol_errors=recorder.events,
        restored_tap=29 if restored_ok else None,
        tap29_restore_pending=not restored_ok,
        recovery=recovery, physical_captures=0)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "pl-snapshots.json").write_text(
        json.dumps(pl_snapshots, indent=2, sort_keys=True), encoding="utf-8")
    (output / "final.json").write_text(
        json.dumps({k: summary[k] for k in (
            "execution_complete", "structural_valid", "stop_cause", "counts",
            "elapsed_s", "restored_tap", "tap29_restore_pending",
            "raw_protocol_errors", "rows", "recovery")}, indent=2),
        encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="QAM64 tap refusal probe/recovery")
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    a = p.parse_args(argv)
    s = run_probe(a.expected_release, a.output, cable_confirmed=a.cable_confirmed)
    print(json.dumps({k: s[k] for k in (
        "execution_complete", "structural_valid", "stop_cause", "counts",
        "elapsed_s", "restored_tap", "tap29_restore_pending",
        "raw_protocol_errors")}, indent=2))
    return 0 if s["execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
