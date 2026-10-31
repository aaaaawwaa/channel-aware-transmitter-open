"""End-to-end training/export integration on a tiny contract-compliant fixture dataset.

This fixture is deliberately NOT the delivered dataset (data-gen owns that); it only
creates a few synthetic frames per class so the whole Lead pipeline (loader ->
preprocess -> baseline -> CNN -> thresholds -> export -> numpy parity) is exercised
without waiting for the formal data.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from ml_training.common import config as config_mod
from ml_training.training import train as train_mod

LEVELS = {"QPSK": (-120, 120), "QAM16": (-150, -50, 50, 150),
          "QAM64": (-175, -125, -75, -25, 25, 75, 125, 175)}


def _mod_frame(mode, rng):
    samples = 4096
    if mode == "BPSK":
        i = rng.choice([-300, 300], size=samples)
        q = rng.normal(scale=20, size=samples)
    elif mode == "QPSK":
        i = rng.choice([-300, 300], size=samples)
        q = rng.choice([-300, 300], size=samples)
    else:
        levels = LEVELS[mode]
        i = rng.choice(levels, size=samples)
        q = rng.choice(levels, size=samples)
    frame = np.stack([i, q], axis=1) + rng.normal(scale=8, size=(samples, 2))
    return np.clip(np.rint(frame), -2048, 2047).astype(np.int16)


def _gate_frame(kind, rng):
    samples = 4096
    if kind == "OFF":
        frame = rng.normal(scale=1.0, size=(samples, 2))
    elif kind == "NOISE":
        frame = rng.normal(scale=400.0, size=(samples, 2))
    elif kind == "TONE":
        n = np.arange(samples)
        tone = 1000 * np.exp(2j * np.pi * 0.1 * n)
        frame = np.stack([tone.real, tone.imag], axis=1)
    elif kind == "CLIP":
        frame = np.full((samples, 2), 5000.0)
    else:
        raise ValueError(kind)
    return np.clip(np.rint(frame), -2048, 2047).astype(np.int16)


def _write_split(root, split, label, frames, start_seed):
    path = root / "frames" / ("%s-%s.npz" % (split, label))
    path.parent.mkdir(parents=True, exist_ok=True)
    count = frames.shape[0]
    arrays = {
        "X": frames,
        "frame_id": np.array(["%s-%s-%04d" % (split, label, index) for index in range(count)]),
        "session_id": np.array(["%s-%s-a" % (split, label)] * count),
        "label": np.array([label] * count),
        "seed_family": np.array(["%s-a" % split] * count),
        "symbol_seed": np.arange(count, dtype=np.uint64) + start_seed,
        "noise_seed": np.arange(count, dtype=np.uint64) + start_seed + 1000,
        "snr_db": np.full(count, 10.0, dtype=np.float32),
        "amplitude_codes": np.full(count, 300, dtype=np.int16),
        "phase_rad": np.zeros(count, dtype=np.float32),
        "cfo_hz": np.zeros(count, dtype=np.float32),
        "window_offset": np.zeros(count, dtype=np.int16),
        "noise_sigma_codes": np.ones(count, dtype=np.float32),
        "iq_sha256": np.array([hashlib.sha256(np.ascontiguousarray(frames[index]).tobytes())
                               .hexdigest() for index in range(count)]),
        "clipped": np.zeros(count, dtype=bool),
    }
    np.savez(path, **arrays)
    return {"path": str(path.relative_to(root)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "split": split,
            "label": label, "count": count, "clipped_count": 0}


def _write_gate(root, split, kind, frames, start_seed):
    path = root / "gate" / ("%s-%s.npz" % (split, kind))
    path.parent.mkdir(parents=True, exist_ok=True)
    count = frames.shape[0]
    clipped = np.asarray([np.any((frame == -2048) | (frame == 2047)) for frame in frames])
    arrays = {
        "X": frames, "frame_id": np.array(["%s-%s-%03d" % (split, kind, index)
                                           for index in range(count)]),
        "session_id": np.array(["%s-%s" % (split, kind)] * count),
        "label": np.array([kind] * count), "seed_family": np.array([split] * count),
        "symbol_seed": np.arange(count, dtype=np.uint64) + start_seed,
        "noise_seed": np.arange(count, dtype=np.uint64) + start_seed + 1000,
        "snr_db": np.full(count, 10.0, dtype=np.float32),
        "amplitude_codes": np.full(count, 300, dtype=np.int16),
        "phase_rad": np.zeros(count, dtype=np.float32),
        "cfo_hz": np.zeros(count, dtype=np.float32),
        "window_offset": np.zeros(count, dtype=np.int16),
        "noise_sigma_codes": np.ones(count, dtype=np.float32),
        "iq_sha256": np.array([hashlib.sha256(np.ascontiguousarray(frame).tobytes())
                               .hexdigest() for frame in frames]),
        "clipped": clipped,
    }
    np.savez(path, **arrays)
    return {"path": str(path.relative_to(root)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "split": split,
            "label": kind, "count": count, "clipped_count": int(clipped.sum())}


@pytest.fixture()
def tiny_dataset(tmp_path):
    _, config_sha = config_mod.load_config()
    rng = np.random.default_rng(2026)
    root = tmp_path / "tiny-dataset"
    entries = []
    for index, label in enumerate(config_mod.CLASS_ORDER):
        train_frames = np.stack([_mod_frame(label, rng) for _ in range(30)])
        val_frames = np.stack([_mod_frame(label, rng) for _ in range(10)])
        test_frames = np.stack([_mod_frame(label, rng) for _ in range(10)])
        entries.append(_write_split(root, "train", label, train_frames, 10 + index))
        entries.append(_write_split(root, "val", label, val_frames, 100 + index))
        entries.append(_write_split(root, "test", label, test_frames, 200 + index))
    for split in ("gate_cal", "gate_test"):
        for offset, kind in enumerate(config_mod.GATE_KINDS):
            frames = np.stack([_gate_frame(kind, rng) for _ in range(10)])
            entries.append(_write_gate(root, split, kind, frames, 300 + offset))
    manifest = {
        "schema": "ml-modrec-synthetic-v1", "dataset_id": "tiny-dataset",
        "created_utc": "2026-10-20T00:00:00Z",
        "config_path": "ml_training/config/first_round.json",
        "config_sha256": config_sha, "numpy_version": np.__version__,
        "waveform": {"fs_hz": 60000000, "symbol_rate_hz": 3000000, "sps": 20,
                     "shaping": "RECT", "samples": 4096, "n_symbols": 207},
        "classes": list(config_mod.CLASS_ORDER),
        "gate_kinds": list(config_mod.GATE_KINDS),
        "splits": {"train": {}, "val": {}, "test": {}, "gate_cal": {}, "gate_test": {}},
        "files": entries,
        "summary": {"total_frames": sum(entry["count"] for entry in entries)},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_tiny_dataset_training_end_to_end(tiny_dataset, tmp_path):
    pytest.importorskip("torch")
    metadata = train_mod.run_training(
        dataset_dir=tiny_dataset, out_dir=tmp_path / "artifacts", variant="smoke",
        max_epochs_override=2)
    assert metadata["params"] == 1628
    assert metadata["macs"] == 1278016
    assert metadata["train_frames"] == 120
    assert metadata["val_frames"] == 40
    assert metadata["test_split_used_for_tuning"] is False
    assert metadata["real_data_used"] is False
    assert metadata["torch_numpy_parity"]["argmax_agreement"] == 1.0
    assert metadata["inference_timing_host"]["numpy_per_frame"]["median_ms"] > 0.0
    for relative in ("cnn/model.json", "cnn/model.pt", "cnn/metadata.json",
                     "cnn/curves.json", "cnn/thresholds.json", "baseline/model.json"):
        assert (tmp_path / "artifacts" / relative).is_file(), relative
    document = json.loads((tmp_path / "artifacts" / "cnn" / "model.json").read_text())
    assert document["classes"] == list(config_mod.CLASS_ORDER)
    assert document["thresholds"]["min_complex_rms"] > 0.0
    assert document["weights"]["head.weight"]["shape"] == [4, 16]
