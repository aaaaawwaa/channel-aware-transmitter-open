"""Finite BPSK UI acceptance: only owns sessions it armed; stops on incomplete closure.

The offline orchestrator (campaign) is transport-agnostic: the session protocol is
initial()/setup(seed)/capture(tag, physical)/cleanup()/close().  The production path
uses rf_bpsk_ui_board_session.LiveUiSession with actual UiView hit events on the
verified installed release. This module is an offline orchestrator, not a live CLI.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

SEEDS = (1, 37, 73)
FOURCLASS_TRUTH = {
    "BPSK": dict(seed=1, tx_payload_sha256="38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6",
                 reference_4096_sha256="e20369e47f38e351b20aef91aeae91dae8c30ab0ed79a5f45a97bfcd973a24df"),
    "QPSK": dict(seed=37, tx_payload_sha256="d33a0e0e97a9c7d1ad8ee20ba52b8d5e0b6c3c123fa31fd8163770176c3adcc2",
                 reference_4096_sha256="90730f9c856b9c6bf9379e0087a875dcb219eeb8af4bfaa5259530ca192907c9"),
    "QAM16": dict(seed=73, tx_payload_sha256="ef7aaa422fe35406d8bf3390fc7fae19a46a21811c379dc8c0b03dd5d39e4a3c",
                  reference_4096_sha256="b90469002df9edceaa82bb143dd6d01fa903648e70051f442423ca708e6023fc"),
    "QAM64": dict(seed=127, tx_payload_sha256="a32068f5abc9892a92fdd3d601c7f84522d48a23ec4902410bd9bc4bd3adb152",
                  reference_4096_sha256="322c973903e751edc92fd7ab728917b288a7fa5470ac8d64f62e4cd5dabc56a4"),
}
FOURCLASS_LAYOUT = (
    ("BPSK", (("off-first", False), ("bpsk-1", True), ("bpsk-2", True), ("bpsk-3", True))),
    ("QPSK", (("qpsk-1", True), ("qpsk-2", True), ("qpsk-3", True))),
    ("QAM16", (("qam16-1", True), ("qam16-2", True), ("qam16-3", True))),
    ("QAM64", (("qam64-1", True), ("qam64-2", True), ("qam64-3", True), ("off-last", False))),
)  # noqa: E501
ON_FRAMES = 10
OFF_FRAMES = 1
EXPECTED_RECORDS = 3 * (ON_FRAMES + OFF_FRAMES)
EXPECTED_ALIGNED = 27
MAX_AGGREGATE_BER = .01
CAPTURE_DEADLINE = 10.0


def campaign(factory, output, expected_release, split="test", single=False,
              seed=None, physical=None, require_ber=True):
    """Run a finite acceptance campaign; factory() yields one session per seed.

    A session that reports an unsafe initial state, a release mismatch, a capture
    timeout, clipping, an unsafe egress readback or an unconfirmed disarm is the
    LAST session: the remaining hardware flow stops immediately and nothing after
    it is recorded.  Clipped/unsafe frame records stay for diagnosis but never
    count as valid.  Missing final disarm acknowledgement fails the campaign even
    when the live readback reports safe.

    ``single=True`` is the one-frame, one-session, one-init mode used for a
    bounded real receive smoke: it uses SEEDS[0] or explicit ``seed`` and the
    same cleanup/disarm/readback checks; the default 33-frame campaign is
    unchanged.  ``physical=False`` is the OFF path (ordinary rx.capture, never
    loopback/TX).  ``require_ber=False`` is for non-BPSK single frames: accept
    the frame on valid modrec4 classification evidence instead of BPSK sync/BER."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    seeds = ((seed if seed is not None else SEEDS[0]),) if single else SEEDS
    if single:
        on_frames = 1 if physical is not False else 0
        off_frames = 0 if on_frames else 1
    else:
        on_frames, off_frames = ON_FRAMES, OFF_FRAMES
    sessions, records = [], []
    bit_errors = compared_bits = 0
    on_aligned = off_false_ber = 0
    cause = None
    for index, seed_value in enumerate(seeds):
        session = factory()
        if hasattr(session, "set_session_context"):
            session.set_session_context(session_index=index)
        owned = False
        session_error = None
        try:
            initial = session.initial()
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                cause = "initial_not_safe"
                break
            if initial.get("release_id") != expected_release:
                cause = "release_mismatch"
                break
            owned = True
            session.setup(seed_value)
            for frame_index in range(on_frames + off_frames):
                physical = frame_index < on_frames
                tag = "s%02d-%s-%02d" % (index, "on" if physical else "off", frame_index)
                try:
                    record = session.capture(tag, physical)
                except TimeoutError:
                    cause = "capture_timeout"
                    break
                if not isinstance(record, dict):
                    cause = "malformed_record"
                    break
                record = dict(record)
                record.setdefault("session_index", index)
                record.setdefault("seed", seed)
                record.setdefault("tag", tag)
                records.append(record)
                if record.get("egress_safe") is not True:
                    cause = "egress_unsafe"
                    break
                result = record.get("result") or {}
                if result.get("reason") in ("CLIPPED", "INVALID_IQ"):
                    cause = "clipped"
                    break
                if physical:
                    if result.get("reference_sync") is True:
                        errors, compared, ber = (result.get("bit_errors"),
                            result.get("compared_bits"), result.get("ber"))
                        valid = (type(errors) is int and type(compared) is int
                            and 0 <= errors <= compared and compared > 0
                            and type(ber) in (int, float) and math.isfinite(ber)
                            and 0 <= ber <= 1 and abs(ber - errors / compared) <= 1e-12)
                        if not valid:
                            # Keep malformed values as text if JSON cannot encode them.
                            record['result'] = dict(result)
                            if type(ber) is float and not math.isfinite(ber):
                                record['result']['ber'] = repr(ber)
                            cause = 'invalid_ber_counts'
                            break
                        on_aligned += 1
                        bit_errors += errors
                        compared_bits += compared
                    elif any(result.get(k) is not None for k in ('bit_errors','compared_bits','ber')):
                        cause = 'unsynchronized_ber'
                        break
                elif result.get("ber") is not None:
                    off_false_ber += 1
        except TimeoutError:
            cause = "capture_timeout"
        except Exception as exc:
            cause = "control_error: %s" % type(exc).__name__
            session_error = str(exc)
        finally:
            # Any session we armed (owned) must be cleaned up even when setup or a
            # capture raised; only initial-state refusals skip cleanup.
            if owned:
                try:
                    cleanup = session.cleanup()
                    closed = bool(isinstance(cleanup, dict) and cleanup.get("safe"))
                    ack = bool(isinstance(cleanup, dict) and cleanup.get("disarm_ack"))
                except Exception:
                    closed = ack = False
            else:
                closed = ack = False
            sessions.append(dict(index=index, seed=seed_value, closed=closed, disarm_ack=ack,
                                 cause=cause, error=session_error))
            try:
                session.close()
            except Exception:
                pass
        if cause is not None:
            break
        if not closed or not ack:
            cause = "close_unconfirmed"
            break
    aggregate_ber = bit_errors / compared_bits if compared_bits else None
    if single:
        if physical is False:
            # A single OFF capture is valid only when the frame/identity/IQ and
            # the no-TX-source gate are present; no BPSK BER is expected.
            record = records[0] if len(records) == 1 else {}
            passed = (cause is None and len(sessions) == 1 and len(records) == 1
                      and record.get("egress_safe") is True
                      and record.get("off_tx_ok") is True
                      and record.get("capture_id") and record.get("iq_sha256")
                      and len([s for s in sessions if s["closed"] and s["disarm_ack"]]) == 1)
        else:
            record = records[0] if len(records) == 1 else {}
            mod = record.get("modrec4") if isinstance(record, dict) else None
            modulation = (mod or {}).get("modulation") or {}
            if require_ber:
                passed = (cause is None and len(sessions) == 1 and len(records) == 1
                          and on_aligned >= 1
                          and compared_bits > 0 and aggregate_ber <= MAX_AGGREGATE_BER
                          and off_false_ber == 0
                          and len([s for s in sessions if s["closed"] and s["disarm_ack"]]) == 1)
            else:
                # Non-BPSK single frame: BPSK sync/BER is explicitly not required.
                passed = (cause is None and len(sessions) == 1 and len(records) == 1
                          and record.get("egress_safe") is True
                          and record.get("capture_id") and record.get("iq_sha256")
                          and isinstance(mod, dict) and mod.get("status") == "OK"
                          and isinstance(modulation.get("final_label"), str)
                          and type(modulation.get("accepted")) is bool
                          and len([s for s in sessions if s["closed"] and s["disarm_ack"]]) == 1)
    else:
        passed = (cause is None and len(sessions) == len(SEEDS)
                  and len(records) == EXPECTED_RECORDS and on_aligned >= EXPECTED_ALIGNED
                  and compared_bits > 0 and aggregate_ber <= MAX_AGGREGATE_BER
                  and off_false_ber == 0 and len([s for s in sessions if s["closed"] and s["disarm_ack"]]) == len(SEEDS))
    result = dict(passed=passed, stop_cause=cause, sessions=sessions, records=records,
                  on_aligned=on_aligned, off_false_ber=off_false_ber,
                  bit_errors=bit_errors, compared_bits=compared_bits,
                  aggregate_ber=aggregate_ber, expected_release=expected_release,
                  split=split, single_frame=single, require_ber=require_ber,
                  physical=(physical if single else None))
    (output / "records.json").write_text(json.dumps(records, indent=1, allow_nan=False),
                                          encoding="utf-8")
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False),
                                        encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("passed", "stop_cause", "on_aligned",
                                               "off_false_ber", "aggregate_ber")}))
    return result


def fourclass_campaign(factory, output, expected_release, amplitude=60):
    """Run the 4-session/14-frame small-sample plan.

    Session order is BPSK (OFF-first + 3 ON), QPSK (3 ON), QAM16 (3 ON), QAM64
    (3 ON + OFF-last).  ``factory(mode, seed, amplitude)`` must return the same
    session protocol as ``campaign``.  Only frames actually captured are
    recorded; a failure stops the remaining plan (no retry/re-collection).
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    sessions, records = [], []
    cause = None
    for session_index, (mode, frames) in enumerate(FOURCLASS_LAYOUT):
        seed = FOURCLASS_TRUTH[mode]["seed"]
        session = factory(mode, seed, amplitude)
        if hasattr(session, "set_session_context"):
            session.set_session_context(session_index=session_index)
        owned = False
        session_error = None
        try:
            initial = session.initial()
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                cause = "initial_not_safe"
                break
            if initial.get("release_id") != expected_release:
                cause = "release_mismatch"
                break
            owned = True
            session.setup(seed)
            for frame_index, (label, physical) in enumerate(frames):
                tag = "s%02d-%s" % (session_index, label)
                started = time.monotonic()
                try:
                    record = session.capture(tag, physical)
                except TimeoutError:
                    cause = "capture_timeout"
                    break
                except Exception as exc:  # noqa: BLE001 - recorded, plan stops
                    cause = "capture_error: %s" % type(exc).__name__
                    session_error = str(exc)
                    break
                elapsed = time.monotonic() - started
                if not isinstance(record, dict):
                    cause = "malformed_record"
                    break
                record = dict(record)
                truth = FOURCLASS_TRUTH.get(mode) if physical else None
                record.update({
                    "session_index": session_index,
                    "frame_index": frame_index,
                    "mode": ("OFF" if not physical else mode),
                    "tx_seed": seed,
                    "tx_amplitude": amplitude,
                    "physical": physical,
                    "expected_demod_mode": "BPSK",
                    "truth_class": mode if physical else "OFF",
                    "truth_source": ("hardware_amp60 manifest + independent PRBS/constellation"
                                     if physical else "OFF has no class truth"),
                    "truth_tx_payload_sha256": truth["tx_payload_sha256"] if truth else None,
                    "truth_reference_4096_sha256": truth["reference_4096_sha256"] if truth else None,
                    "capture_command_seconds": elapsed,
                    "capture_command_timing_note": "command closure time, not TX-on time",
                    "pre_tx_source_selected": record.get("pre_tx_source_selected"),
                    "post_tx_source_selected": record.get("post_tx_source_selected"),
                })
                records.append(record)
                if not physical and record.get("off_tx_ok") is not True:
                    cause = "off_tx_invalid"
                    break
                if record.get("egress_safe") is not True:
                    cause = "egress_unsafe"
                    break
        except Exception as exc:  # noqa: BLE001 - a failed session ends the plan
            cause = "session_error: %s" % type(exc).__name__
            session_error = str(exc)
        finally:
            if owned:
                try:
                    cleanup = session.cleanup()
                    closed = bool(isinstance(cleanup, dict) and cleanup.get("safe"))
                    ack = bool(isinstance(cleanup, dict) and cleanup.get("disarm_ack"))
                except Exception:
                    closed = ack = False
            else:
                closed = ack = False
            sessions.append(dict(index=session_index, mode=mode, seed=seed, closed=closed,
                                 disarm_ack=ack, cause=cause, error=session_error))
            try:
                session.close()
            except Exception:
                pass
        if cause is not None:
            break
        if not closed or not ack:
            cause = "close_unconfirmed"
            break

    on_records = [r for r in records if r.get("physical") is True]
    off_records = [r for r in records if r.get("physical") is False]

    def _valid_on(record):
        mod = record.get("modrec4") or {}
        return (isinstance(record.get("capture_id"), str) and bool(record.get("capture_id"))
                and isinstance(record.get("iq_sha256"), str) and len(record["iq_sha256"]) == 64
                and record.get("release_id") == expected_release
                and isinstance(mod, dict) and mod.get("status") in ("OK", "INVALID", "UNAVAILABLE"))

    classification = {}
    for mode in FOURCLASS_TRUTH:
        rows = [r for r in on_records if r.get("truth_class") == mode]
        errors = []
        for record in rows:
            mod = record.get("modrec4") or {}
            modulation = mod.get("modulation") or {}
            predicted = modulation.get("final_label")
            accepted = modulation.get("accepted")
            if predicted != mode or accepted is not True:
                errors.append(dict(frame_index=record.get("frame_index"),
                                   predicted=predicted, accepted=accepted,
                                   reason=(mod.get("validity") or mod.get("reason"))))
        classification[mode] = dict(
            total=len(rows),
            label_correct=sum(1 for r in rows
                              if ((r.get("modrec4") or {}).get("modulation") or {}).get("final_label") == mode),
            accepted=sum(1 for r in rows
                         if ((r.get("modrec4") or {}).get("modulation") or {}).get("accepted") is True),
            errors=errors)

    off_checks = []
    for record in off_records:
        mod = record.get("modrec4") or {}
        modulation = mod.get("modulation") or {}
        accepted = modulation.get("accepted")
        predicted = modulation.get("final_label")
        accepted_known = type(accepted) is bool
        valid = (isinstance(record.get("capture_id"), str) and bool(record.get("capture_id"))
                 and isinstance(record.get("iq_sha256"), str) and len(record["iq_sha256"]) == 64
                 and record.get("release_id") == expected_release
                 and record.get("off_tx_ok") is True
                 and record.get("egress_safe") is True
                 and bool(mod) and accepted_known)
        correct_rejection = bool(valid and (accepted is False or predicted == "UNKNOWN"))
        off_checks.append(dict(
            frame_index=record.get("frame_index"), valid=valid,
            accepted=accepted, predicted=predicted,
            correct_rejection=correct_rejection,
            false_accept=bool(valid and accepted is True),
            invalid_reason=(None if valid else
                            ("accepted_missing" if not accepted_known
                             else "missing frame/identity/IQ or TX-source gate"))))

    valid_on = sum(1 for r in on_records if _valid_on(r))
    valid_off = sum(1 for r in off_checks if r["valid"])
    false_accepts = sum(1 for r in off_checks if r["false_accept"])
    passed = (cause is None and len(sessions) == len(FOURCLASS_LAYOUT)
              and len(on_records) == 12 and len(off_records) == 2
              and valid_on == 12 and valid_off == 2
              and false_accepts == 0
              and len([s for s in sessions if s["closed"] and s["disarm_ack"]]) == len(FOURCLASS_LAYOUT))
    result = dict(schema="rf-fourclass-campaign-v1", passed=passed, stop_cause=cause,
                  expected_release=expected_release, amplitude=amplitude,
                  counts=dict(sessions=len(sessions), initializations=len(sessions),
                              loads=len(sessions), tx_frames=len(on_records),
                              physical_captures=len(records), on_frames=len(on_records),
                              off_frames=len(off_records), valid_on=valid_on, valid_off=valid_off),
                  sessions=sessions, records=records, classification=classification,
                  off=off_checks, false_accepts=false_accepts,
                  truth=dict(amp_config="ml_training/fourclass_test_vectors/hardware_amp60/config/hardware_amp60.json",
                             tx_payload_samples=2540, reference_window_samples=4096,
                             construction="tile(tx_payload_period,2)[:4096]",
                             real_rx_byte_equality_required=False,
                             source="hardware_amp60 manifest + independent PRBS/constellation"),
                  timing_note="capture_command_seconds is command closure, not TX-on time")
    (output / "records.json").write_text(json.dumps(records, indent=1, allow_nan=False),
                                         encoding="utf-8")
    (output / "fourclass.json").write_text(json.dumps(result, indent=2, allow_nan=False),
                                           encoding="utf-8")
    (output / "final.json").write_text(json.dumps(result, indent=2, allow_nan=False),
                                       encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("passed", "stop_cause", "counts",
                                              "classification", "off", "false_accepts")}))
    return result


if __name__ == "__main__":
    raise SystemExit("REFUSE: use rf_bpsk_ui_board_run.py for verified UiView acceptance")
