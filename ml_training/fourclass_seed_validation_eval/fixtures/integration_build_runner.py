#!/usr/bin/env python3
"""Offline end-to-end TX-evidence test.

Real product build_tx_waveform -> real runner load-recording methods -> evaluator.
Only the transport response is fake; tx_load_metadata and worker_args are produced by
the actual product/runner path, never hand-written to a different shape.

This fixture is SYNTHETIC_FIXTURE only.  It covers ON, OFF, payload mismatch and
worker_args mismatch.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "integration_run"
RAW = FIXTURE / "raw"
OUT = BASE / "results" / "integration_eval"
RELEASE = "4776ba0bf6cabac383934f0437358595"
BPSK_SOURCE = {
    "mode": "BPSK", "seed": 2, "amplitude": 60, "filter": "RECT",
    "rolloff": "0.35", "sample_rate_hz": 60000000,
    "symbol_rate_hz": 3000000, "waveform": "digital",
}
OFF_SOURCE = dict(BPSK_SOURCE, seed=1)

sys.dont_write_bytecode = True
for path in (ROOT / "scripts", ROOT / "pynq_overlay" / "sw"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rf_bpsk_ui_board_session import LiveUiSession  # noqa: E402
from rf_tx_waveform import build_tx_waveform  # noqa: E402
from ml_training.fourclass_seed_validation_eval.seed_validation_eval import run  # noqa: E402


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def jwrite(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def actual_runner_evidence(source_args):
    """Use real build_tx_waveform and real LiveUiSession recording methods."""
    daemon_metadata, payload = build_tx_waveform(source_args)
    recorder = object.__new__(LiveUiSession)
    recorder._session_index = 1
    recorder.run_id = "synthetic-fixture"
    recorder._tx_mode = source_args["mode"]
    recorder._tx_seed = source_args["seed"]
    recorder._tx_amplitude = source_args["amplitude"]
    recorder._tx_load_records = []
    # Only the transport response is fake.
    recorder._tx_control_op = lambda op, args=None, payload=b"": {"ok": True}
    response = LiveUiSession._record_control_op(recorder, "tx.load",
                                                daemon_metadata, payload)
    worker_result = {"ok": True, "result": response, "args": dict(source_args),
                     "code": None, "message": ""}
    evidence = LiveUiSession._finalize_load_evidence(recorder, worker_result)
    return daemon_metadata, payload, evidence


def reference_4096(payload):
    if len(payload) != 10160:
        raise ValueError("unexpected digital payload length")
    return (payload * 3)[:4096 * 4]


def write_frame(label, iq, source_args, evidence, plan_index, physical,
                off=False, mutate_payload=False, worker_mismatch=False):
    frame_dir = RAW / label
    frame_dir.mkdir(parents=True, exist_ok=True)
    iq_path = frame_dir / (label + ".iq")
    iq_path.write_bytes(iq)
    digest = sha_bytes(iq)
    observed = {"format": "s16_iq_le", "samples": 4096,
                "sample_rate_hz": 60000000, "decimation": 1,
                "symbol_rate_hz": 3000000, "shaping": "RECT"}
    ev = json.loads(json.dumps(evidence))
    if mutate_payload:
        ev["payload_sha256"] = "0" * 64
    # For the worker_args mismatch case, the actual production worker args stay
    # intact; only the fixture's plan expectation is intentionally different.
    meta = {
        "SYNTHETIC_FIXTURE": True,
        "fixture_note": "not real RF; build_tx_waveform + real runner recording",
        "capture_id": "fixture-" + label,
        "request_id": plan_index,
        "release_id": RELEASE,
        "connection_generation": 8,
        "config_revision": 2,
        "iq_sha256": digest,
        "input_domain_status": "VALID",
        "input_domain_observed": observed,
        "format": "s16_iq_le",
        "samples": 4096,
        "sample_rate_hz": 60000000,
        "decimation": 1,
        "demod_result": {"submitted": {"config": {
            "mode": "BPSK", "symbol_rate_hz": 3000000, "shaping": "RECT"}}},
        "tx_load_evidence": ev,
        "off_tx_ok": bool(off),
        "pre_tx_source_selected": False,
        "post_tx_source_selected": False,
        "modrec4_result": {
            "status": "OK",
            "input_domain": {"status": "VALID", "observed": observed},
            "frame_identity": {"capture_id": "fixture-" + label,
                               "request_id": plan_index, "release_id": RELEASE,
                               "iq_sha256": digest},
            "modulation": {"final_label": "UNKNOWN", "accepted": False,
                           "reason": "fixture_board_result_not_truth"},
        },
    }
    record = {
        "iq_file": iq_path.name, "metadata_file": label + ".json",
        "iq_sha256": digest, "release_id": RELEASE,
        "capture_id": meta["capture_id"], "request_id": plan_index,
        "off_tx_ok": bool(off), "pre_tx_source_selected": False,
        "post_tx_source_selected": False,
        "tx_load_evidence": ev,
        "modrec4": meta["modrec4_result"],
    }
    jwrite(frame_dir / (label + ".json"), meta)
    jwrite(frame_dir / "records.json", [record])
    return {
        "frame_index": plan_index,
        "label": label,
        "physical": bool(physical),
        "truth_class": "OFF" if off else "BPSK",
        "expected_load": dict(source_args) if not worker_mismatch else dict(OFF_SOURCE),
    }


def main():
    # Clean only the synthetic fixture subtree.
    if FIXTURE.exists():
        import shutil
        shutil.rmtree(FIXTURE)
    RAW.mkdir(parents=True, exist_ok=True)

    daemon_meta, payload, evidence = actual_runner_evidence(BPSK_SOURCE)
    assert set(daemon_meta) == {"samples", "bytes", "format", "direction", "wrap"}, daemon_meta
    assert set(evidence["tx_load_metadata"]) == {"samples", "bytes", "format",
                                                  "direction", "wrap"}
    assert evidence["worker_args"] == BPSK_SOURCE
    ref = reference_4096(payload)
    truth_rows = []
    plan_rows = []

    plan_rows.append(write_frame("01-off-first", b"\x00\x00\x00\x00" * 4096,
                                 OFF_SOURCE, actual_runner_evidence(OFF_SOURCE)[2],
                                 1, False, off=True))
    plan_rows.append(write_frame("02-bpsk-valid", ref, BPSK_SOURCE, evidence,
                                 2, True))
    truth_rows.append({"frame_index": 2, "mode": "BPSK", "seed": 2,
                       "amplitude": 60, "filter": "RECT", "rolloff": "0.35",
                       "symbol_rate_hz": 3000000, "sample_rate_hz": 60000000,
                       "tx_samples": 2540, "tx_bytes": 10160,
                       "tx_payload_sha256": evidence["payload_sha256"],
                       "reference_4096_sha256": sha_bytes(ref),
                       "truth_class": "BPSK",
                       "truth_source": "actual build_tx_waveform payload; runner evidence"})
    # Payload mismatch: actual production evidence, payload SHA deliberately differs
    # from the independent truth row.
    plan_rows.append(write_frame("03-bpsk-payload-mismatch", ref, BPSK_SOURCE,
                                 evidence, 3, True, mutate_payload=True))
    truth_rows.append({"frame_index": 3, "mode": "BPSK", "seed": 2,
                       "amplitude": 60, "filter": "RECT", "rolloff": "0.35",
                       "symbol_rate_hz": 3000000, "sample_rate_hz": 60000000,
                       "tx_samples": 2540, "tx_bytes": 10160,
                       "tx_payload_sha256": evidence["payload_sha256"],
                       "reference_4096_sha256": sha_bytes(ref),
                       "truth_class": "BPSK",
                       "truth_source": "actual build_tx_waveform payload; mismatch fixture"})
    # Worker-args mismatch: actual production worker args use seed=2, while the plan
    # expects seed=1.  tx_load_metadata remains the real daemon metadata.
    plan_rows.append(write_frame("04-bpsk-worker-mismatch", ref, BPSK_SOURCE,
                                 evidence, 4, True, worker_mismatch=True))
    truth_rows.append({"frame_index": 4, "mode": "BPSK", "seed": 2,
                       "amplitude": 60, "filter": "RECT", "rolloff": "0.35",
                       "symbol_rate_hz": 3000000, "sample_rate_hz": 60000000,
                       "tx_samples": 2540, "tx_bytes": 10160,
                       "tx_payload_sha256": evidence["payload_sha256"],
                       "reference_4096_sha256": sha_bytes(ref),
                       "truth_class": "BPSK",
                       "truth_source": "actual build_tx_waveform payload; worker mismatch fixture"})
    plan = {"schema": "SYNTHETIC_FIXTURE_integration_plan",
            "dataset_id": "synthetic_integration_fixture",
            "release": RELEASE, "order": plan_rows}
    truth = {"schema": "SYNTHETIC_FIXTURE_integration_truth",
             "dataset_id": "synthetic_integration_fixture",
             "training_use": False, "real_capture": False, "rows": truth_rows}
    jwrite(FIXTURE / "fixture_plan.json", plan)
    jwrite(FIXTURE / "fixture_truth.json", truth)
    doc = run(RAW, FIXTURE / "fixture_truth.json", FIXTURE / "fixture_plan.json", OUT)
    assert doc["status"] == "PARTIAL", doc["status"]
    assert len(doc["frames"]) == 4, len(doc["frames"])
    assert len(doc["valid_frames"]) == 2, len(doc["valid_frames"])
    assert len(doc["invalid_frames"]) == 2, len(doc["invalid_frames"])
    errors = {frame["frame"]: frame["audit"]["errors"] for frame in doc["invalid_frames"]}
    assert any("tx_payload_sha_mismatch" in e for e in errors["03-bpsk-payload-mismatch"]), errors
    assert any("worker_arg:seed" in e for e in errors["04-bpsk-worker-mismatch"]), errors
    for name, summary in doc["summary"].items():
        assert summary["per_class"]["BPSK"]["correct"] == 1, (name, summary)
        assert summary["off"]["correct_rejection"] == 1, (name, summary)
    print(json.dumps({
        "integration": "PASS",
        "status": doc["status"],
        "frames": len(doc["frames"]),
        "valid": len(doc["valid_frames"]),
        "invalid": len(doc["invalid_frames"]),
        "invalid_errors": errors,
        "daemon_metadata_keys": sorted(daemon_meta),
        "worker_args_keys": sorted(evidence["worker_args"]),
        "models": list(doc["summary"]),
        "fixture_is_synthetic": True,
        "out": str(OUT / "seed_validation_result.json"),
    }, indent=1))


if __name__ == "__main__":
    main()
