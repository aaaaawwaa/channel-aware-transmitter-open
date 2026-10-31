"""Tests for the shared quality gate and dataset loader contract."""
from __future__ import annotations

import json

import numpy as np

from ml_training.common.dataset import load_manifest, load_split, verify_hashes
from ml_training.common.gate import (apply_gate, decide, energy_floor_from_off,
                                     softmax, top_scores)


def test_softmax_and_top_scores():
    scores = softmax([1.0, 2.0, 3.0, 0.0])
    assert abs(float(scores.sum()) - 1.0) < 1e-12
    index, best, second = top_scores(scores)
    assert index == 2 and best > second


def test_decide_confidence_and_margin():
    thresholds = {"max_softmax": 0.8, "margin": 0.1}
    confident = np.array([0.9, 0.05, 0.03, 0.02])
    result = decide(confident, thresholds)
    assert result["label"] is None and result["reason"] == ""
    uncertain = np.array([0.45, 0.4, 0.1, 0.05])
    result = decide(uncertain, thresholds)
    assert result["label"] == "UNKNOWN" and result["reason"] == "confidence"
    close = np.array([0.86, 0.80, 0.02, 0.01])
    result = decide(close, thresholds)
    assert result["reason"] == "margin"
    assert apply_gate(confident, ["A", "B", "C", "D"], thresholds)["label"] == "A"


def test_energy_floor_from_off_population():
    floor = energy_floor_from_off([1.0, 2.0, 3.0, 4.0], quantile=0.95)
    expected = np.percentile([1.0, 2.0, 3.0, 4.0], 95.0) * 10 ** (3.0 / 20.0)
    assert abs(floor - expected) < 1e-12


def _write_dataset(root, count=3):
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for split, label in (("train", "BPSK"), ("val", "QPSK"), ("test", "QAM16")):
        path = root / ("frames/%s-%s.npz" % (split, label))
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = np.zeros((count, 4096, 2), dtype=np.int16)
        frames[:, :, 0] = 100
        arrays = {
            "X": frames,
            "frame_id": np.array(["%s-%d" % (split, index) for index in range(count)]),
            "session_id": np.array([split] * count),
            "label": np.array([label] * count),
            "seed_family": np.array([split + "-a"] * count),
            "symbol_seed": np.arange(count, dtype=np.uint64) + 1,
            "noise_seed": np.arange(count, dtype=np.uint64) + 100,
            "snr_db": np.full(count, 10.0, dtype=np.float32),
            "amplitude_codes": np.full(count, 100, dtype=np.int16),
            "phase_rad": np.zeros(count, dtype=np.float32),
            "cfo_hz": np.zeros(count, dtype=np.float32),
            "window_offset": np.zeros(count, dtype=np.int16),
            "noise_sigma_codes": np.ones(count, dtype=np.float32),
            "iq_sha256": np.array(["0" * 64] * count),
            "clipped": np.zeros(count, dtype=bool),
        }
        np.savez(path, **arrays)
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": str(path.relative_to(root)).replace("\\", "/"),
                      "sha256": digest, "split": split, "label": label,
                      "count": count, "clipped_count": 0})
    manifest = {"schema": "ml-modrec-synthetic-v1", "dataset_id": "unit",
                "classes": ["BPSK", "QPSK", "QAM16", "QAM64"],
                "gate_kinds": ["OFF", "NOISE", "TONE", "CLIP"],
                "files": files, "splits": {}}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_dataset_loader_and_hashes(tmp_path):
    _write_dataset(tmp_path)
    manifest = load_manifest(tmp_path)
    integrity = verify_hashes(tmp_path, manifest)
    assert integrity["ok"] and integrity["checked"] == 3
    train = load_split(tmp_path, manifest, "train")
    assert train["X"].shape == (3, 4096, 2)
    assert train["X"].dtype == np.int16
    assert set(train["label"].tolist()) == {"BPSK"}
    assert train["_dropped"] == 0
    test = load_split(tmp_path, manifest, "test", labels=["QAM16"])
    assert test["X"].shape[0] == 3
