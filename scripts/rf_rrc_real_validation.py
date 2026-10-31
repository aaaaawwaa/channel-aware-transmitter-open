#!/usr/bin/env python3
"""One bounded 6-frame real RRC smoke, driven by the existing UI runner.

Fixed order:
  OFF-first(QPSK/RRC seed39, no TX)
  QPSK/RRC seed39
  QPSK/RRC seed40
  BPSK/RRC seed6
  QPSK/RECT seed41 (legacy control)
  OFF-last(QPSK/RRC seed39, no TX)

6 sessions / 6 init / 6 load / 4 ON TX / 6 captures / zero retries.
The TX and RX shaping are independent explicit fields; RRC RX always carries
the product RrcEnvelope/ModemResult and its measured intervals.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from rf_bpsk_ui_board_session import LiveUiSession, _jsonable  # noqa: E402

RRC_VERSION = "rrc-alpha035-sps20-v3"
FRAMES = (
    dict(tag="01-off-first", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
    dict(tag="02-qpsk-rrc-39", physical=True, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
    dict(tag="03-qpsk-rrc-40", physical=True, tx_mode="QPSK", tx_seed=40,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=40, rx_shaping="RRC"),
    dict(tag="04-bpsk-rrc-6", physical=True, tx_mode="BPSK", tx_seed=6,
         tx_shaping="RRC", rx_mode="BPSK", rx_seed=6, rx_shaping="RRC"),
    dict(tag="05-qpsk-rect-41", physical=True, tx_mode="QPSK", tx_seed=41,
         tx_shaping="RECT", rx_mode="QPSK", rx_seed=41, rx_shaping="RECT"),
    dict(tag="06-off-last", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
)
NEXT_MIN_FRAMES = (
    dict(tag="01-off-first", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
    dict(tag="02-qpsk-rrc-40", physical=True, tx_mode="QPSK", tx_seed=40,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=40, rx_shaping="RRC"),
    dict(tag="03-bpsk-rrc-6", physical=True, tx_mode="BPSK", tx_seed=6,
         tx_shaping="RRC", rx_mode="BPSK", rx_seed=6, rx_shaping="RRC"),
    dict(tag="04-qpsk-rect-41", physical=True, tx_mode="QPSK", tx_seed=41,
         tx_shaping="RECT", rx_mode="QPSK", rx_seed=41, rx_shaping="RECT"),
    dict(tag="05-off-last", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
)
ROOTCAUSE_FRAMES = (
    dict(tag="01-off-first", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
    dict(tag="02-qpsk-rrc-40-a", physical=True, tx_mode="QPSK", tx_seed=40,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=40, rx_shaping="RRC"),
    dict(tag="03-qpsk-rrc-40-b", physical=True, tx_mode="QPSK", tx_seed=40,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=40, rx_shaping="RRC"),
    dict(tag="04-qpsk-rrc-39", physical=True, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
    dict(tag="05-qpsk-rect-41", physical=True, tx_mode="QPSK", tx_seed=41,
         tx_shaping="RECT", rx_mode="QPSK", rx_seed=41, rx_shaping="RECT"),
    dict(tag="06-off-last", physical=False, tx_mode="QPSK", tx_seed=39,
         tx_shaping="RRC", rx_mode="QPSK", rx_seed=39, rx_shaping="RRC"),
)
ROOTCAUSE_BUDGET = dict(sessions=6, initializations=6, loads=6,
                        on_tx=4, captures=6, retries=0)
SIX_BUDGET = dict(sessions=6, initializations=6, loads=6,
                  on_tx=4, captures=6, retries=0)
NEXT_MIN_BUDGET = dict(sessions=5, initializations=5, loads=5,
                       on_tx=3, captures=5, retries=0)
BUDGET = SIX_BUDGET
PLANS = {"six": (FRAMES, SIX_BUDGET),
         "next-min": (NEXT_MIN_FRAMES, NEXT_MIN_BUDGET),
         "rootcause": (ROOTCAUSE_FRAMES, ROOTCAUSE_BUDGET)}


def _budget_for(frames):
    return dict(sessions=len(frames), initializations=len(frames),
                loads=len(frames),
                on_tx=sum(1 for f in frames if f.get("physical")),
                captures=len(frames), retries=0)
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, rrc_alpha=0.35, rrc_sps=20,
                 comparison_start_symbol=67, comparison_end_symbol=123,
                 valid_symbol_start=4, valid_symbol_end=123,
                 valid_symbols=119, qpsk_bits=112, bpsk_bits=56)


def _summary_row(frame, record, full_demod, payload=None, seconds=None):
    record = record if isinstance(record, dict) else {}
    result = record.get("result") or {}
    return dict(
        tag=frame["tag"], physical=frame["physical"],
        tx_mode=frame["tx_mode"], tx_seed=frame["tx_seed"],
        tx_shaping=frame["tx_shaping"], tx_rolloff="0.35",
        rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
        rx_shaping=frame["rx_shaping"], rx_rolloff="0.35",
        capture_id=record.get("capture_id"), iq_sha256=record.get("iq_sha256"),
        release_id=record.get("release_id"),
        result=result,
        rx_submission=result.get("rx_submission") or record.get("rx_submission"),
        demod_result=full_demod,
        tx_load_evidence=record.get("tx_load_evidence"),
        frame_identity=(record.get("frame_identity") or
                        (payload or {}).get("frame")),
        pre_tx_source_selected=record.get("pre_tx_source_selected"),
        post_tx_source_selected=record.get("post_tx_source_selected"),
        off_tx_ok=record.get("off_tx_ok"),
        capture_observation=record.get("capture_observation"),
        capture_observation_file=record.get("capture_observation_file"),
        seconds=seconds,
    )


def _config_mode(result):
    cfg = (result.get("demod_config") or {})
    cfg = cfg.get("config") if isinstance(cfg, dict) else None
    return cfg.get("mode") if isinstance(cfg, dict) else None


def _observation_sidecar_identity_problem(record, side):
    if side.get("capture_id") != record.get("capture_id"):
        return "capture_observation_capture_id_mismatch"
    if side.get("request_id") != record.get("request_id"):
        return "capture_observation_request_id_mismatch"
    if side.get("daemon_timestamp_ns") != record.get("daemon_timestamp_ns"):
        return "capture_observation_timestamp_mismatch"
    side_release = side.get("release_id")
    if (side_release is not None and record.get("release_id") is not None
            and side_release != record.get("release_id")):
        return "capture_observation_release_id_mismatch"
    return None


def _off_normal_absence(side):
    """A normal OFF path has no observation member, not a damaged observation.

    The forwarder may still write an additive sidecar for the OFF frame; the
    extractor then reports ``observation_present=False`` and ``stop=True`` with
    OBSERVATION_MEMBER_MISSING.  That is "this path does not provide it", not an
    ON failure.  Identity is checked separately first.
    """
    if not isinstance(side, dict):
        return False
    obs = side.get("observation")
    if not isinstance(obs, dict) or obs.get("observation_present") is not False:
        return False
    if side.get("usable") is not False or side.get("stop") is not True:
        return False
    reasons = side.get("reasons") or []
    return all(str(r) == "OBSERVATION_MEMBER_MISSING" for r in reasons)


def _observation_disposition(frame, record, required):
    """Return (problem_or_None, status_text); never fabricate an observation."""
    if not required:
        return None, "NOT_REQUIRED"
    record = record if isinstance(record, dict) else {}
    side = record.get("capture_observation")
    error = record.get("capture_observation_error")
    if not frame["physical"]:
        # OFF: ordinary rx.capture legitimately carries no observation.  An
        # absent sidecar/error is "not provided", never an ON failure and never
        # a fabricated reading.  If a sidecar IS present, damage or identity
        # mismatch is NOT ignored.
        if side is None and error is None:
            return None, "OFF_PATH_NOT_PROVIDED"
        if error is not None:
            return "off_capture_observation_read_failed", "OFF_OBSERVATION_READ_FAILED"
        if not isinstance(side, dict):
            return "off_capture_observation_malformed", "OFF_OBSERVATION_MALFORMED"
        identity = _observation_sidecar_identity_problem(record, side)
        if identity is not None:
            return "off_" + identity, "OFF_OBSERVATION_IDENTITY_MISMATCH"
        if _off_normal_absence(side):
            return None, "OFF_PATH_NOT_PROVIDED"
        if side.get("usable") is not True or side.get("stop") is not False:
            return "off_capture_observation_unusable", "OFF_OBSERVATION_INVALID"
        if not isinstance(side.get("observation"), dict):
            return "off_capture_observation_payload_missing", "OFF_OBSERVATION_INVALID"
        return None, "OFF_OBSERVATION_PRESENT_VALID"

    # Every physical ON frame must have one usable, same-frame observation.
    if error is not None:
        return "capture_observation_read_failed", "ON_OBSERVATION_READ_FAILED"
    if side is None:
        return "capture_observation_missing", "ON_OBSERVATION_MISSING"
    if not isinstance(side, dict):
        return "capture_observation_malformed", "ON_OBSERVATION_MALFORMED"
    if _off_normal_absence(side):
        return "capture_observation_missing", "ON_OBSERVATION_MISSING"
    if side.get("usable") is not True or side.get("stop") is not False:
        return "capture_observation_unusable", "ON_OBSERVATION_UNUSABLE"
    identity = _observation_sidecar_identity_problem(record, side)
    if identity is not None:
        return identity, "ON_OBSERVATION_IDENTITY_MISMATCH"
    if not isinstance(side.get("observation"), dict):
        return "capture_observation_payload_missing", "ON_OBSERVATION_PAYLOAD_MISSING"
    return None, "ON_OBSERVATION_VALID"


def _structural_problem(frame, record, full_demod):
    if not isinstance(record, dict):
        return "malformed_record"
    result = record.get("result") or {}
    if not record.get("capture_id") or not record.get("iq_sha256"):
        return "missing_frame_identity"
    if result.get("envelope_present") is not True or full_demod is None:
        return "missing_product_demod_result"
    if result.get("tx_shaping") != frame["tx_shaping"]:
        return "tx_shaping_mismatch"
    if result.get("rx_shaping") != frame["rx_shaping"]:
        return "rx_shaping_mismatch"
    if str(result.get("tx_rolloff")) != "0.35" or str(result.get("rx_rolloff")) != "0.35":
        return "rolloff_mismatch"
    if result.get("rx_submission") is None:
        return "missing_rx_submission"
    if _config_mode(result) != frame["rx_mode"]:
        return "demod_config_mode_mismatch"
    embedded = full_demod.get("result") if isinstance(full_demod, dict) else None
    if isinstance(embedded, dict):
        if embedded.get("capture_id") != record.get("capture_id"):
            return "stale_demod_capture_id"
        if embedded.get("release_id") != record.get("release_id"):
            return "stale_demod_release_id"
    if result.get("tx_mode") != frame["tx_mode"]:
        return "tx_mode_mismatch"
    if result.get("rx_mode") != frame["rx_mode"]:
        return "rx_mode_mismatch"
    if result.get("rx_seed") != frame["rx_seed"]:
        return "rx_seed_mismatch"
    if frame["physical"]:
        if result.get("tx_seed") != frame["tx_seed"]:
            return "tx_seed_mismatch"
        if result.get("tx_seed") != result.get("rx_seed"):
            return "tx_rx_seed_mismatch"
    else:
        if record.get("off_tx_ok") is not True:
            return "off_tx_not_safe"
        if result.get("pre_tx_source_selected") is not False \
                or result.get("post_tx_source_selected") is not False:
            return "off_tx_source_selected"
        if result.get("reference_sync") is True:
            return "off_reference_sync_residual"
        if any(result.get(k) is not None for k in
               ("bit_errors", "compared_bits", "ber")):
            return "off_ber_residual"
        return None
    if frame["rx_shaping"] == "RRC":
        # The installed candidate may carry any registered alpha035/sps20
        # receiver revision; record the exact one, but never accept RECT.
        if not str(result.get("algorithm_version", "")).startswith(
                "rrc-alpha035-sps20-v"):
            return "rrc_algorithm_version_mismatch"
        if result.get("shaping") != "RRC":
            return "rrc_shaping_not_reported"
        if result.get("iq_order") != "QI":
            return "rrc_iq_order_not_qi"
    else:
        if result.get("algorithm_version") != "qpsk-rect-snapshot-v1":
            return "rect_algorithm_version_mismatch"
        if result.get("iq_order") != "QI":
            return "rect_iq_order_not_qi"
    # Metric container integrity is structural; a non-zero BER itself is not.
    metrics = tuple(result.get(k) for k in ("bit_errors", "compared_bits", "ber"))
    if any(v is not None for v in metrics):
        if any(v is None for v in metrics):
            return "incomplete_metric_triplet"
        errors, compared, ber = metrics
        if (type(errors) is not int or type(compared) is not int
                or not isinstance(ber, (int, float)) or isinstance(ber, bool)):
            return "metric_type_invalid"
        expected = (112 if frame["rx_mode"] == "QPSK" else 56) \
            if frame["rx_shaping"] == "RRC" else 128
        if compared != expected:
            return "metric_denominator_mismatch"
        if not 0 <= errors <= compared:
            return "metric_count_out_of_range"
        if abs(float(ber) - errors / compared) > 1e-12:
            return "metric_ber_inconsistent"
        if result.get("reference_sync") is not True:
            return "metrics_present_without_reference_sync"
    elif result.get("reference_sync") is True:
        return "missing_metrics_for_synced_result"
    return None


def _metric_problem(frame, result):
    """Research metric verdict only; never a reason to stop the campaign."""
    if not frame["physical"]:
        return None, True
    if result.get("reference_sync") is not True:
        if all(result.get(k) is None for k in ("bit_errors", "compared_bits", "ber")):
            return "legal_desync_negative", False
        return "metric_negative_sync", False
    if result.get("bit_errors") != 0:
        return "metric_negative_nonzero_ber", False
    if frame["rx_shaping"] == "RRC":
        if result.get("valid_symbols") != 119:
            return "metric_negative_valid_symbols", False
        if (result.get("group_delay_samples") != 0
                or result.get("transition_symbols") != 4
                or result.get("valid_symbol_start") != 4
                or result.get("valid_symbol_end") != 123
                or result.get("comparison_start_symbol") != 67
                or result.get("comparison_end_symbol") != 123):
            return "metric_negative_interval", False
    return None, True

def _resolve_plan(plan, frames):
    if frames is None:
        if isinstance(plan, str):
            if plan not in PLANS:
                raise ValueError("unknown plan %r" % (plan,))
            plan_name = plan
            frames, budget = PLANS[plan]
        else:
            plan_name = "custom"
            frames = list(plan)
            budget = _budget_for(frames)
    else:
        frames = list(frames)
        if frames == list(NEXT_MIN_FRAMES):
            plan_name, budget = "next-min", NEXT_MIN_BUDGET
        elif frames == list(ROOTCAUSE_FRAMES):
            plan_name, budget = "rootcause", ROOTCAUSE_BUDGET
        elif frames == list(FRAMES):
            plan_name, budget = "six", SIX_BUDGET
        else:
            plan_name, budget = "custom", _budget_for(frames)
    if not frames:
        raise ValueError("plan must contain at least one frame")
    return plan_name, frames, budget


def run_validation(expected_release, output, *, session_factory=LiveUiSession,
                   cable_confirmed=False, plan="six", frames=None):
    if not cable_confirmed:
        raise ValueError("explicit cable confirmation is required")
    plan_name, frames, budget = _resolve_plan(plan, frames)
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan_doc = dict(schema="rf-rrc-real-validation-plan-v1",
                    plan_name=plan_name,
                    expected_release=expected_release, frames=frames,
                    budget=budget, workpoint=WORKPOINT,
                    policy="one frame per session; no retry; structural failure stops")
    plan = plan_doc
    (output / "plan.json").write_text(json.dumps(plan_doc, indent=2), encoding="utf-8")
    rows = []
    counts = dict(sessions=0, initializations=0, loads=0, on_tx=0, captures=0)
    failure = None
    for index, frame in enumerate(frames):
        frame_dir = output / frame["tag"]
        frame_dir.mkdir(parents=True, exist_ok=True)
        counts["sessions"] += 1
        started = time.monotonic()
        session = session_factory(
            expected_release, frame_dir, tx_mode=frame["tx_mode"],
            seed=frame["tx_seed"], amplitude=WORKPOINT["amplitude"],
            session_index=index, run_id=frame["tag"],
            rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
            tx_shaping=frame["tx_shaping"], rx_shaping=frame["rx_shaping"],
            tx_rolloff="0.35", rx_rolloff="0.35",
            require_capture_observation=(plan_name == "rootcause"))
        owned = False
        try:
            initial = session.initial()
            if not isinstance(initial, dict) or initial.get("safe") is not True:
                failure = "initial_not_safe:" + frame["tag"]
                break
            if initial.get("release_id") != expected_release:
                failure = "release_mismatch:" + frame["tag"]
                break
            counts["initializations"] += 1
            owned = True
            session.setup(frame["tx_seed"])
            counts["loads"] += 1
            record = session.capture(frame["tag"], frame["physical"])
            counts["captures"] += 1
            if frame["physical"]:
                counts["on_tx"] += 1
            payload = getattr(session, "_last_payload", None) or {}
            envelope = payload.get("demod_result") if isinstance(payload, dict) else None
            full_demod = _jsonable(envelope) if envelope is not None else None
            problem = _structural_problem(frame, record, full_demod)
            observation_problem, observation_status = _observation_disposition(
                frame, record, plan_name == "rootcause")
            metric_problem, metrics_ok = _metric_problem(
                frame, record.get("result") or {})
            row = _summary_row(frame, record, full_demod, payload,
                               seconds=time.monotonic() - started)
            row["problem"] = problem
            row["observation_problem"] = observation_problem
            row["capture_observation_status"] = observation_status
            row["metric_problem"] = metric_problem
            row["metrics_ok"] = bool(metrics_ok)
            try:
                row["decoded"] = (dataclasses.asdict(envelope)
                                  if dataclasses.is_dataclass(envelope)
                                  else full_demod)
            except Exception:
                row["decoded"] = full_demod
            rows.append(row)
            (frame_dir / "frame-result.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8")
            if problem is not None:
                failure = problem + ":" + frame["tag"]
                break
            if observation_problem is not None:
                failure = observation_problem + ":" + frame["tag"]
                break
        except KeyboardInterrupt:
            failure = "cancelled:" + frame["tag"]
            raise
        except Exception as exc:
            failure = "runtime_error:%s:%s" % (type(exc).__name__, frame["tag"])
            rows.append(dict(tag=frame["tag"], error=str(exc) or type(exc).__name__))
            break
        finally:
            cleanup = None
            if owned:
                try:
                    cleanup = session.cleanup()
                except Exception as exc:
                    cleanup = dict(safe=False, disarm_ack=False,
                                   error=str(exc) or type(exc).__name__)
            try:
                session.close()
            except Exception:
                pass
            if owned and (not isinstance(cleanup, dict)
                          or cleanup.get("safe") is not True
                          or cleanup.get("disarm_ack") is not True):
                failure = failure or ("cleanup_not_safe:" + frame["tag"])
                break
        if failure is not None:
            break
    rrc_on = [r for r in rows if r.get("physical") and r.get("rx_shaping") == "RRC"]
    rect_on = [r for r in rows if r.get("physical") and r.get("rx_shaping") == "RECT"]
    execution_complete = bool(
        failure is None and len(rows) == len(frames)
        and all(counts.get(k) == v for k, v in budget.items() if k != "retries"))
    structural_valid = bool(execution_complete
                            and all(r.get("problem") is None for r in rows))
    metric_failures = [r.get("tag") for r in rows if r.get("metrics_ok") is False]
    metrics_passed = bool(structural_valid and not metric_failures)
    summary = dict(schema="rf-rrc-real-validation-summary-v1",
                   plan_name=plan_name,
                   expected_release=expected_release, plan=plan_doc, rows=rows,
                   counts=counts, budget=budget, stop_cause=failure,
                   execution_complete=execution_complete,
                   structural_valid=structural_valid,
                   metrics_passed=metrics_passed,
                   metric_failures=metric_failures,
                   structural_complete=execution_complete,
                   rrc_on_frames=len(rrc_on),
                   rrc_on_sync=sum(1 for r in rrc_on
                                   if (r.get("result") or {}).get("reference_sync") is True),
                   rect_control_frames=len(rect_on),
                   rect_control_sync=sum(1 for r in rect_on
                                         if (r.get("result") or {}).get("reference_sync") is True),
                   note="metric negatives are retained and do not stop later frames; "
                        "execution_complete is not metric acceptance")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                         encoding="utf-8")
    (output / "final.json").write_text(json.dumps(summary, indent=2, default=str),
                                       encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    p.add_argument("--plan", choices=("six", "next-min", "rootcause"), default="six",
                   help="fixed frame plan (default six; next-min=5; rootcause=6-observation)")
    a = p.parse_args(argv)
    if not a.cable_confirmed:
        raise SystemExit("REFUSE: explicit current cable confirmation is required")
    summary = run_validation(a.expected_release, a.output,
                              cable_confirmed=True, plan=a.plan)
    print(json.dumps({k: summary[k] for k in
                      ("plan_name", "execution_complete", "structural_valid",
                       "metrics_passed", "stop_cause", "counts", "rrc_on_sync",
                       "rect_control_sync", "metric_failures")}))
    return 0 if (summary["execution_complete"] and summary["metrics_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
