"""Targeted tests for task68 independent-TX truth import and batch isolation."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import evaluate  # noqa: E402
from ml_training.real_campaign.importer import (  # noqa: E402
    REASON_DIGEST_MISMATCH, REASON_TRUTH_IDENTITY_MISMATCH, REASON_TRUTH_MISSING,
    TASK68_TRUTH_SCHEMA, parse_task68_frames,
)


def _write_frame(run, stem, capture_id, rx_mode="BPSK", index=0,
                 invalid_iq=False, metadata_iq_sha=None,
                 model_label="QPSK"):
    run.mkdir(parents=True, exist_ok=True)
    raw = bytearray(16384)
    if index:
        raw[-2:] = struct.pack("<h", index)
    if invalid_iq:
        raw = raw[:100]
    raw = bytes(raw)
    iq = run / (stem + ".iq")
    iq.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    meta = {
        "capture_id": capture_id,
        "request_id": 7 + index,
        "iq_sha256": metadata_iq_sha if metadata_iq_sha is not None else digest,
        "format": "s16_iq_le",
        "samples": 4096,
        "sample_rate_hz": 60_000_000,
        "decimation": 1,
        "model_result": {"label": model_label},
        "demod_result": {"submitted": {"config": {
            "mode": rx_mode, "symbol_rate_hz": 3_000_000, "shaping": "RECT"}}},
    }
    (run / (stem + ".json")).write_text(json.dumps(meta), encoding="utf-8")
    return digest


def _tx(mode, seed=1):
    tx = {
        "mode": mode,
        "sample_rate_hz": 60_000_000,
        "symbol_rate_hz": 3_000_000,
        "shaping": "RECT",
        "amplitude": 60,
        "rolloff": "0.35",
    }
    if mode != "OFF":
        tx["seed"] = seed
    return tx


def _truth_entry(capture_id, request_id, digest, mode, seed=1):
    return {
        "frame_identity": {"capture_id": capture_id, "request_id": request_id,
                           "iq_sha256": digest},
        "tx": _tx(mode, seed),
    }


def _write_truth(root, entries):
    truth = {"schema": TASK68_TRUTH_SCHEMA,
             "truth_source": "independent_tx_config",
             "frames": entries}
    path = Path(root) / "tx_truth.json"
    path.write_text(json.dumps(truth), encoding="utf-8")
    return path


def _tmpdir():
    return tempfile.TemporaryDirectory(
        dir=ROOT / "ml_training" / "real_campaign" / "tests")


def test_two_frame_batch_import_keeps_per_frame_identity_and_source():
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        d0 = _write_frame(run, "s00-on-00", "rx-batch-0", "BPSK", 0)
        d1 = _write_frame(run, "s01-on-01", "rx-batch-1", "QPSK", 1)
        truth = _write_truth(tmp, [
            _truth_entry("rx-batch-0", 7, d0, "BPSK", 1),
            _truth_entry("rx-batch-1", 8, d1, "QPSK", 37),
        ])
        result = parse_task68_frames(run, truth)
        assert len(result["records"]) == 2
        by_capture = {r["frame_identity"]["capture_id"]: r for r in result["records"]}
        assert set(by_capture) == {"rx-batch-0", "rx-batch-1"}
        assert by_capture["rx-batch-0"]["status"] == "OK"
        assert by_capture["rx-batch-1"]["status"] == "OK"
        assert by_capture["rx-batch-0"]["truth_class"] == "BPSK"
        assert by_capture["rx-batch-1"]["truth_class"] == "QPSK"
        assert by_capture["rx-batch-0"]["truth_source"] == "independent_tx_config"
        assert by_capture["rx-batch-1"]["truth_source"] == "independent_tx_config"
        assert by_capture["rx-batch-0"]["frame_id"] != by_capture["rx-batch-1"]["frame_id"]
        assert by_capture["rx-batch-1"]["tx_truth"]["seed"] == 37


@pytest.mark.parametrize("mode,seed", [("QPSK", 37), ("QAM16", 73), ("QAM64", 127)])
def test_non_bpsk_tx_with_bpsk_rx_demod_is_evaluable(mode, seed):
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        digest = _write_frame(run, "s00-on-00", "rx-nonbpsk", "BPSK", 0)
        truth = _write_truth(tmp, [
            _truth_entry("rx-nonbpsk", 7, digest, mode, seed)])
        result = parse_task68_frames(run, truth)
        record = result["records"][0]
        assert record["status"] == "OK"
        assert record["truth_evaluable"] is True
        assert record["truth_class"] == mode
        assert record["truth_source"] == "independent_tx_config"
        # RX BPSK config is retained by design; it is diagnostic, not a veto.
        diag = record["rx_expected_mode_diagnostic"]
        assert diag["rx_expected_mode"] == "BPSK"
        assert diag["tx_mode"] == mode
        assert diag["mismatch"] is True
        assert not record["invalid_reasons"]


def test_missing_independent_truth_is_unevaluable_and_prediction_is_not_truth():
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        _write_frame(run, "s00-on-00", "rx-no-truth", "BPSK", 0,
                     model_label="QPSK")
        truth = _write_truth(tmp, [])
        result = parse_task68_frames(run, truth)
        record = result["records"][0]
        assert record["status"] == "UNEVALUABLE"
        assert record["truth_class"] is None
        assert record["tx_truth"] is None
        assert REASON_TRUTH_MISSING in record["invalid_reasons"]
        assert record["non_truth_labels"]["model_result_label"] == "QPSK"


def test_identity_mismatch_is_not_valid_statistics():
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        digest = _write_frame(run, "s00-on-00", "rx-identity", "BPSK", 0)
        entry = _truth_entry("rx-identity", 7, digest, "BPSK", 1)
        entry["tx"]["seed"] = 37  # still independent
        # Deliberately stale identity digest: must not select a valid truth entry.
        entry["frame_identity"]["iq_sha256"] = "0" * 64
        truth = _write_truth(tmp, [entry])
        result = parse_task68_frames(run, truth)
        record = result["records"][0]
        assert record["status"] == "UNEVALUABLE"
        assert record["truth_evaluable"] is False
        assert REASON_TRUTH_IDENTITY_MISMATCH in record["invalid_reasons"]


def test_metadata_digest_mismatch_is_invalid_and_not_counted():
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        digest = _write_frame(run, "s00-on-00", "rx-digest", "BPSK", 0,
                              metadata_iq_sha="f" * 64)
        truth = _write_truth(tmp, [
            _truth_entry("rx-digest", 7, digest, "BPSK", 1)])
        result = parse_task68_frames(run, truth)
        record = result["records"][0]
        assert record["status"] == "INVALID"
        assert record["truth_evaluable"] is False
        assert REASON_DIGEST_MISMATCH in record["invalid_reasons"]


def test_invalid_off_is_not_counted_as_correct_rejection():
    with _tmpdir() as tmp:
        run = Path(tmp) / "run"
        digest = _write_frame(run, "s00-off-00", "rx-invalid-off", "BPSK", 0,
                              invalid_iq=True)
        truth = _write_truth(tmp, [
            _truth_entry("rx-invalid-off", 7, digest, "OFF")])
        result = parse_task68_frames(run, truth)
        record = result["records"][0]
        assert record["kind"] == "quality"
        assert record["truth_class"] == "OFF"
        assert record["status"] == "INVALID"
        assert record["truth_evaluable"] is False
        evaluated = evaluate.evaluate([record], predictor=object())
        assert evaluated["quality"]["total"] == 0
        assert evaluated["quality"].get("rejected", 0) == 0
        assert evaluated["frames_skipped"]
