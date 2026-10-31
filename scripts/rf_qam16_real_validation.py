#!/usr/bin/env python3
"""Fixed, offline-prepared QAM16/RECT real-RX runner entry (one plan, no retry).

The runner never infers RX mode from CNN output, TX mode, or the previous frame.
The submitted product config must itself be QAM16/RECT/3 MSym/s with an explicit
reference seed and QI.  Acceptance/evidence comes from the actual
Qam16Envelope/ModemResult, not from a standalone receiver.

A nonzero BER and a legal desynchronization are retained negative *metrics*:
they do not make the structural batch invalid and must not stop later frames.
Identity/config/corruption/timeout/cleanup/OFF-residual failures are structural
stops.  A different reference seed is never rejected merely because it differs
from the TX seed; a true mismatch is tested with the real mismatch fixture.

No board access, no install and no capture happens when this module is only
imported or tested.  A physical run needs a new authorization (current TX1-RX1
cable confirmation) and the QAM candidate deployed; that is not asserted here.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import time

from rf_bpsk_ui_board_session import LiveUiSession

QAM16_VERSION = "qam16-rect-snapshot-v1"
MAX_SECONDS = 180
CAPTURE_SECONDS = 10.0
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 samples=4096, shaping="RECT", rolloff="0.35",
                 iq_order="QI", expected_mode="QAM16")
BUDGET = dict(sessions=4, initializations=4, loads=4, on_tx=2,
              captures=4, retries=0)

# OFF-first no TX; two legal QAM16/RECT seed-39 ON frames; OFF-last no TX.
# There is no TX39/RX40 "must desync" frame: different seeds may align cyclically.
FRAMES = (
    dict(tag="qam16-off-first", physical=False, tx_mode="QAM16", tx_seed=39,
         tx_shaping="RECT", rx_mode="QAM16", rx_seed=39, rx_shaping="RECT"),
    dict(tag="qam16-rect-39-a", physical=True, tx_mode="QAM16", tx_seed=39,
         tx_shaping="RECT", rx_mode="QAM16", rx_seed=39, rx_shaping="RECT"),
    dict(tag="qam16-rect-39-b", physical=True, tx_mode="QAM16", tx_seed=39,
         tx_shaping="RECT", rx_mode="QAM16", rx_seed=39, rx_shaping="RECT"),
    dict(tag="qam16-off-last", physical=False, tx_mode="QAM16", tx_seed=39,
         tx_shaping="RECT", rx_mode="QAM16", rx_seed=39, rx_shaping="RECT"),
)

# Pre-written per-frame metric contract.  "bit_errors"/"ber" are actual values;
# only the denominator/interval/type/range/consistency are structural.
FRAME_METRICS = {
    "qam16-off-first": dict(kind="off", carrier_sync=False,
                            timing_sync=False, reference_sync=False,
                            bit_errors=None, compared_bits=None, ber=None),
    "qam16-rect-39-a": dict(kind="on", compared_bits=256, valid_symbols=127,
                            alignment_bits=252, comparison_start_symbol=63,
                            metric_policy="0..256 accepted; 0 is not structural"),
    "qam16-rect-39-b": dict(kind="on", compared_bits=256, valid_symbols=127,
                            alignment_bits=252, comparison_start_symbol=63,
                            metric_policy="0..256 accepted; 0 is not structural"),
    "qam16-off-last": dict(kind="off", carrier_sync=False,
                           timing_sync=False, reference_sync=False,
                           bit_errors=None, compared_bits=None, ber=None),
}


def _jsonable(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, pathlib.Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def qam16_submission_problem(result):
    """The submitted product config, not TX/CNN state, must be QAM16/RECT."""
    submission = (result or {}).get("demod_config")
    if not isinstance(submission, dict):
        return "submission_missing"
    config = submission.get("config")
    if not isinstance(config, dict):
        return "submission_config_missing"
    if config.get("mode") != "QAM16":
        return "submission_mode_not_qam16"
    if config.get("shaping") != "RECT":
        return "submission_shaping_not_rect"
    if config.get("iq_order") != "QI":
        return "submission_iq_order_not_qi"
    if config.get("symbol_rate_hz") != 3_000_000:
        return "submission_symbol_rate"
    seed = config.get("reference_prbs7_seed")
    if type(seed) is not int or not 1 <= seed <= 127:
        return "submission_seed_missing"
    if submission.get("decim") not in (None, 1):
        return "submission_decim"
    if submission.get("rx_len") not in (None, 4096):
        return "submission_rx_len"
    if submission.get("input_fs_hz") not in (None, 60_000_000):
        return "submission_input_fs"
    return None


def _metric_container_problem(result):
    """Structural integrity of a SYNCHRONIZED QAM16 metric container."""
    metrics = tuple(result.get(k) for k in ("bit_errors", "compared_bits", "ber"))
    if any(v is None for v in metrics):
        return "incomplete_metric_triplet"
    errors, compared, ber = metrics
    if type(errors) is not int or type(compared) is not int:
        return "metric_type_invalid"
    if type(ber) not in (int, float) or isinstance(ber, bool):
        return "metric_type_invalid"
    if compared != 256:
        return "metric_denominator_mismatch"
    if not 0 <= errors <= compared:
        return "metric_count_out_of_range"
    if abs(float(ber) - errors / compared) > 1e-12:
        return "metric_ber_inconsistent"
    return None


def qam16_evidence_problem(frame, result):
    """Validate actual Qam16Envelope/ModemResult structure and legal negatives.

    Synchronized frames may carry any valid 0..256 BER; legal desync must carry
    all-null metrics and a named reason.  Metric acceptance is decided separately.
    """
    if not isinstance(result, dict):
        return "result_missing"
    if result.get("algorithm_version") != QAM16_VERSION:
        return "qam16_algorithm_version_mismatch"
    if result.get("iq_order") != "QI":
        return "qam16_iq_order_not_qi"
    problem = qam16_submission_problem(result)
    if problem:
        return problem
    config = (result.get("demod_config") or {}).get("config") or {}
    if config.get("reference_prbs7_seed") != frame["rx_seed"]:
        return "submission_seed_mismatch"
    if result.get("envelope_present") is not True:
        return "missing_product_demod"
    if result.get("evm_available") is not False or result.get("evm_rms") is not None:
        return "evm_should_be_unavailable"

    reference_sync = result.get("reference_sync")
    if reference_sync is True:
        if result.get("reason") != "":
            return "synced_reason_nonempty"
        if result.get("carrier_sync") is not True or result.get("timing_sync") is not True:
            return "sync_flag_mismatch"
        if result.get("valid_symbols") != 127:
            return "qam16_valid_symbols"
        if result.get("alignment_bits") != 252:
            return "qam16_alignment_bits"
        if result.get("comparison_start_symbol") != 63:
            return "qam16_comparison_start"
        return _metric_container_problem(result)

    if reference_sync is False:
        if not result.get("reason"):
            return "desync_reason_missing"
        if any(result.get(k) is not None
               for k in ("bit_errors", "compared_bits", "ber")):
            return "desync_metrics_present"
        return None
    return "reference_sync_not_boolean"


def _off_problem(frame, result):
    if result.get("algorithm_version") != QAM16_VERSION:
        return "off_algorithm_version_mismatch"
    if result.get("iq_order") != "QI":
        return "off_iq_order_not_qi"
    for key in ("carrier_sync", "timing_sync", "reference_sync"):
        if result.get(key) is not False:
            return "off_%s_residual" % key
    if any(result.get(k) is not None
           for k in ("bit_errors", "compared_bits", "ber")):
        return "off_ber_residual"
    if result.get("reference_sync") is True:
        return "off_reference_sync_residual"
    return None


def _structural_problem(frame, record, expected_release=None):
    if not isinstance(record, dict):
        return "malformed_record"
    if record.get("egress_safe") is not True:
        return "egress_unsafe"
    if not record.get("capture_id") or not record.get("iq_sha256"):
        return "missing_frame_identity"
    if expected_release is not None and record.get("release_id") != expected_release:
        return "release_mismatch"
    identity = record.get("frame_identity")
    if isinstance(identity, dict) and identity.get("capture_id") not in (
            None, record.get("capture_id")):
        return "frame_identity_mismatch"
    if frame["physical"]:
        if record.get("post_tx_source_selected") is not False:
            return "on_tx_source_selected_after_capture"
    else:
        if record.get("off_tx_ok") is not True:
            return "off_tx_not_clean"
    result = record.get("result") or {}
    if not isinstance(result, dict):
        return "result_missing"
    if result.get("capture_id") not in (None, record.get("capture_id")):
        return "stale_demod_capture_id"
    if result.get("release_id") not in (None, record.get("release_id")):
        return "stale_demod_release_id"
    if frame["physical"]:
        return qam16_evidence_problem(frame, result)
    return _off_problem(frame, result)


def _metric_problem(frame, record):
    """Metric verdict only; never a structural stop and never 0-only."""
    if not frame["physical"]:
        return None
    result = record.get("result") or {}
    if result.get("reference_sync") is not True:
        return "legal_desync_negative"
    if result.get("bit_errors") != 0:
        return "metric_negative_nonzero_ber"
    return None


def _summary_row(frame, record, seconds):
    result = record.get("result") or {}
    return dict(
        tag=frame["tag"], physical=frame["physical"],
        tx_mode=frame["tx_mode"], tx_seed=frame["tx_seed"],
        tx_shaping=frame["tx_shaping"], rx_mode=frame["rx_mode"],
        rx_seed=frame["rx_seed"], rx_shaping=frame["rx_shaping"],
        rx_rolloff="0.35", capture_id=record.get("capture_id"),
        iq_sha256=record.get("iq_sha256"), release_id=record.get("release_id"),
        result=result, seconds=seconds, frame_metrics=FRAME_METRICS[frame["tag"]],
        problem=None, metric_problem=None)


def run_validation(expected_release, output, *, session_factory=LiveUiSession,
                   cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit current cable confirmation is required")
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="rf-qam16-real-validation-plan-v1", plan_name="qam16",
                expected_release=expected_release, frames=list(FRAMES),
                budget=BUDGET, workpoint=WORKPOINT, max_seconds=MAX_SECONDS,
                capture_seconds=CAPTURE_SECONDS, frame_metrics=FRAME_METRICS,
                policy="four fixed QAM16/RECT frames; no retry; structural identity/"
                       "config/corruption/timeout/cleanup/OFF-residual stops; any legal "
                       "0..256 BER or legal desync is retained as a metric negative and "
                       "later frames continue; execution_complete != metrics_passed")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    rows, counts = [], dict(sessions=0, initializations=0, loads=0, on_tx=0, captures=0)
    failure = None
    deadline = time.monotonic() + MAX_SECONDS
    for index, frame in enumerate(FRAMES):
        if time.monotonic() > deadline:
            failure = "budget_exceeded:" + frame["tag"]
            break
        frame_dir = output / frame["tag"]
        frame_dir.mkdir(parents=True, exist_ok=True)
        counts["sessions"] += 1
        started = time.monotonic()
        owned = False
        session = None
        try:
            session = session_factory(
                expected_release, frame_dir, tx_mode=frame["tx_mode"],
                seed=frame["tx_seed"], amplitude=WORKPOINT["amplitude"],
                session_index=index, run_id=frame["tag"],
                rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
                tx_shaping=frame["tx_shaping"], rx_shaping=frame["rx_shaping"],
                tx_rolloff="0.35", rx_rolloff="0.35",
                capture_deadline=CAPTURE_SECONDS)
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
            row = _summary_row(frame, record, time.monotonic() - started)
            row["problem"] = _structural_problem(frame, record, expected_release)
            row["metric_problem"] = _metric_problem(frame, record)
            rows.append(row)
            (frame_dir / "frame-result.json").write_text(
                json.dumps(row, indent=2, default=str), encoding="utf-8")
            if row["problem"] is not None:
                failure = row["problem"] + ":" + frame["tag"]
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
            if owned and session is not None:
                try:
                    cleanup = session.cleanup()
                except Exception as exc:
                    cleanup = dict(safe=False, disarm_ack=False,
                                   error=str(exc) or type(exc).__name__)
            try:
                if session is not None:
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
    execution_complete = bool(
        failure is None and len(rows) == len(FRAMES)
        and all(counts.get(k) == v for k, v in BUDGET.items() if k != "retries"))
    structural_valid = bool(execution_complete
                            and all(r.get("problem") is None for r in rows))
    metric_failures = [r.get("tag") for r in rows
                       if r.get("metric_problem") is not None]
    clean_on = [r for r in rows if r.get("physical")]
    summary = dict(
        schema="rf-qam16-real-validation-summary-v1", plan_name="qam16",
        expected_release=expected_release, plan=plan, rows=rows, counts=counts,
        budget=BUDGET, stop_cause=failure, execution_complete=execution_complete,
        structural_valid=structural_valid, metrics_passed=bool(
            structural_valid and not metric_failures),
        metric_failures=metric_failures,
        qam16_on_frames=len(clean_on),
        qam16_clean_on_frames=len(clean_on),
        qam16_clean_sync=sum(1 for r in clean_on
                             if (r.get("result") or {}).get("reference_sync") is True),
        qam16_expected_negative_frames=0,
        note="QAM16/RECT is read from the product submission/ModemResult; CNN/TX mode "
             "is not used as a receiver verdict; legal nonzero BER/desync are retained "
             "metric negatives and do not stop later structural frames; "
             "execution_complete is not metrics_passed")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                         encoding="utf-8")
    (output / "final.json").write_text(json.dumps(summary, indent=2, default=str),
                                       encoding="utf-8")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cable-confirmed", action="store_true")
    a = p.parse_args(argv)
    if not a.cable_confirmed:
        raise SystemExit("REFUSE: explicit current cable confirmation is required")
    summary = run_validation(a.expected_release, a.output, cable_confirmed=True)
    print(json.dumps({k: summary[k] for k in
                      ("plan_name", "execution_complete", "structural_valid",
                       "metrics_passed", "stop_cause", "counts", "qam16_clean_sync",
                       "qam16_on_frames", "metric_failures")}))
    return 0 if (summary["execution_complete"] and summary["metrics_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
