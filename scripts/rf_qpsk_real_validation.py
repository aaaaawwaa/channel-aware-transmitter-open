#!/usr/bin/env python3
"""One bounded 6-frame real QPSK demod smoke, driven by the existing UI runner.

Frame matrix (one physical capture each):
  OFF-first, QPSK seed39, QPSK seed40, QPSK seed41,
  BPSK seed6 (legacy RX compatibility), OFF-last.
6 sessions / 6 init / 6 load / 4 ON TX / 6 captures; no retry, no re-capture.

This module is an offline orchestrator until a real run is explicitly launched:
it never imports a release at import time and never touches hardware by itself.
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

FRAMES = (
    dict(tag="01-off-first", physical=False, tx_mode="BPSK", tx_seed=1,
         rx_mode="QPSK", rx_seed=39),
    dict(tag="02-qpsk-39", physical=True, tx_mode="QPSK", tx_seed=39,
         rx_mode="QPSK", rx_seed=39),
    dict(tag="03-qpsk-40", physical=True, tx_mode="QPSK", tx_seed=40,
         rx_mode="QPSK", rx_seed=40),
    dict(tag="04-qpsk-41", physical=True, tx_mode="QPSK", tx_seed=41,
         rx_mode="QPSK", rx_seed=41),
    dict(tag="05-bpsk-6", physical=True, tx_mode="BPSK", tx_seed=6,
         rx_mode="BPSK", rx_seed=6),
    dict(tag="06-off-last", physical=False, tx_mode="BPSK", tx_seed=1,
         rx_mode="QPSK", rx_seed=39),
)
BUDGET = dict(sessions=6, initializations=6, loads=6,
              on_tx=4, captures=6, retries=0)
WORKPOINT = dict(lo_hz=80_000_000, attenuation_mdB=46_000, amplitude=60,
                 sample_rate_hz=60_000_000, symbol_rate_hz=3_000_000,
                 shaping="RECT", samples=4096)


def _summary_row(frame, record, full_demod, payload=None):
    record = record if isinstance(record, dict) else {}
    result = record.get("result") or {}
    return dict(
        tag=frame["tag"], physical=frame["physical"],
        tx_mode=frame["tx_mode"], tx_seed=frame["tx_seed"],
        rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"],
        capture_id=record.get("capture_id"), iq_sha256=record.get("iq_sha256"),
        release_id=record.get("release_id"),
        result=result,
        demod_result=full_demod,
        tx_load_evidence=record.get("tx_load_evidence"),
        frame_identity=(record.get("frame_identity") or
                        (payload or {}).get("frame")),
        pre_tx_source_selected=record.get("pre_tx_source_selected"),
        post_tx_source_selected=record.get("post_tx_source_selected"),
        off_tx_ok=record.get("off_tx_ok"),
    )


def _structural_problem(frame, record, full_demod):
    if not isinstance(record, dict):
        return "malformed_record"
    result = record.get("result") or {}
    if not record.get("capture_id") or not record.get("iq_sha256"):
        return "missing_frame_identity"
    if result.get("envelope_present") is not True or full_demod is None:
        return "missing_product_demod_result"
    if not result.get("algorithm_version"):
        return "missing_demod_algorithm_version"
    if result.get("demod_config") is None:
        return "missing_demod_submission"
    demod_config = result.get("demod_config") or {}
    cfg = demod_config.get("config") if isinstance(demod_config, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("mode") != frame["rx_mode"]:
        return "demod_config_mode_mismatch"
    allowed_versions = ("qpsk-rect-snapshot-v1" if frame["rx_mode"] == "QPSK"
                        else "bpsk-rect-snapshot-v2")
    if result.get("algorithm_version") != allowed_versions:
        return "demod_algorithm_version_mismatch"
    embedded = full_demod.get("result") if isinstance(full_demod, dict) else None
    if isinstance(embedded, dict):
        if embedded.get("capture_id") != record.get("capture_id"):
            return "stale_demod_capture_id"
        if embedded.get("release_id") != record.get("release_id"):
            return "stale_demod_release_id"
    if result.get("rx_mode") != frame["rx_mode"]:
        return "rx_mode_mismatch"
    if result.get("tx_mode") != frame["tx_mode"]:
        return "tx_mode_mismatch"
    if result.get("rx_seed") not in (frame["rx_seed"], None):
        return "rx_seed_mismatch"
    if frame["physical"]:
        if result.get("tx_seed") != frame["tx_seed"]:
            return "tx_seed_mismatch"
        if frame["rx_mode"] == "QPSK":
            if result.get("iq_order") != "QI":
                return "qpsk_iq_order_not_qi"
            if result.get("valid_symbols") is None:
                return "missing_valid_symbols"
            if result.get("algorithm_version") != "qpsk-rect-snapshot-v1":
                return "qpsk_algorithm_version_mismatch"
        elif result.get("algorithm_version") != "bpsk-rect-snapshot-v2":
            return "bpsk_algorithm_version_mismatch"
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


def run_validation(expected_release, output, *, session_factory=LiveUiSession,
                   cable_confirmed=False):
    if not cable_confirmed:
        raise ValueError("explicit cable confirmation is required")
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plan = dict(schema="rf-qpsk-real-validation-plan-v1", expected_release=expected_release,
                frames=FRAMES, budget=BUDGET, workpoint=WORKPOINT,
                policy="one frame per session; no retry; structural failure stops")
    (output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    rows = []
    counts = dict(sessions=0, initializations=0, loads=0, on_tx=0, captures=0)
    failure = None
    try:
        for index, frame in enumerate(FRAMES):
            frame_dir = output / frame["tag"]
            frame_dir.mkdir(parents=True, exist_ok=True)
            counts["sessions"] += 1
            session = session_factory(
                expected_release, frame_dir, tx_mode=frame["tx_mode"],
                seed=frame["tx_seed"], amplitude=WORKPOINT["amplitude"],
                session_index=index, run_id=frame["tag"],
                rx_mode=frame["rx_mode"], rx_seed=frame["rx_seed"])
            owned = False
            record = None
            full_demod = None
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
                row = _summary_row(frame, record, full_demod, payload)
                row["problem"] = problem
                try:
                    row["decoded"] = dataclasses.asdict(envelope) if dataclasses.is_dataclass(envelope) else full_demod
                except Exception:
                    row["decoded"] = full_demod
                rows.append(row)
                (frame_dir / "frame-result.json").write_text(
                    json.dumps(row, indent=2, default=str), encoding="utf-8")
                if problem is not None:
                    failure = problem + ":" + frame["tag"]
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
                        cleanup = {"safe": False, "disarm_ack": False,
                                   "error": str(exc) or type(exc).__name__}
                try:
                    session.close()
                except Exception:
                    pass
                if owned and (not isinstance(cleanup, dict) or
                              cleanup.get("safe") is not True or
                              cleanup.get("disarm_ack") is not True):
                    failure = failure or ("cleanup_not_safe:" + frame["tag"])
                    break
            if failure is not None:
                break
    finally:
        pass
    qpsk_rows = [r for r in rows if r.get("physical") and r.get("rx_mode") == "QPSK"]
    bpsk_rows = [r for r in rows if r.get("physical") and r.get("rx_mode") == "BPSK"]
    summary = dict(schema="rf-qpsk-real-validation-summary-v1",
                   expected_release=expected_release,
                   plan=plan, rows=rows, counts=counts, budget=BUDGET,
                   stop_cause=failure,
                   structural_complete=(failure is None and len(rows) == len(FRAMES)),
                   qpsk_frames=len(qpsk_rows),
                   qpsk_sync=sum(1 for r in qpsk_rows
                                 if (r.get("result") or {}).get("reference_sync") is True),
                   bpsk_frames=len(bpsk_rows),
                   bpsk_sync=sum(1 for r in bpsk_rows
                                 if (r.get("result") or {}).get("reference_sync") is True),
                   note="ordinary desync is retained as a negative result; no retry/re-capture")
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
    a = p.parse_args(argv)
    if not a.cable_confirmed:
        raise SystemExit("REFUSE: explicit current cable confirmation is required")
    summary = run_validation(a.expected_release, a.output, cable_confirmed=True)
    print(json.dumps({k: summary[k] for k in
                      ("structural_complete", "stop_cause", "counts",
                       "qpsk_sync", "bpsk_sync")}))
    return 0 if summary["structural_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
