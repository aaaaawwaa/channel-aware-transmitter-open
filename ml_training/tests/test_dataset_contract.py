"""Contract checks for the delivered synthetic datasets (skip until they exist).

These are Lead-side integration checks on top of the data generator's own
independent reference check: structure, counts, split isolation and summary
consistency of the manifest/npz files actually on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ml_training.common import config as config_mod
from ml_training.common.dataset import load_manifest, verify_hashes

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATASETS = PACKAGE_ROOT / "datasets"
EXPECTED_ARRAYS = {
    "X": np.int16, "frame_id": np.str_, "session_id": np.str_, "label": np.str_,
    "seed_family": np.str_, "symbol_seed": np.uint64, "noise_seed": np.uint64,
    "snr_db": np.float32, "amplitude_codes": np.int16, "phase_rad": np.float32,
    "cfo_hz": np.float32, "window_offset": np.int16, "noise_sigma_codes": np.float32,
    "iq_sha256": np.str_, "clipped": np.bool_,
}


def _read_all(dataset_dir, manifest):
    frames = {}
    for entry in manifest["files"]:
        path = dataset_dir / entry["path"]
        with np.load(path, allow_pickle=False) as archive:
            frames[entry["path"]] = {key: np.asarray(archive[key]) for key in archive.files}
    return frames


@pytest.mark.parametrize("dataset_id", ["sim-smoke", "sim-v1"])
def test_dataset_contract(dataset_id):
    dataset_dir = DATASETS / dataset_id
    if not (dataset_dir / "manifest.json").is_file():
        pytest.skip("dataset not generated yet: " + str(dataset_dir))
    config, config_sha = config_mod.load_config()
    manifest = load_manifest(dataset_dir)
    assert manifest["config_sha256"] == config_sha
    assert manifest["classes"] == list(config_mod.CLASS_ORDER)
    assert manifest["gate_kinds"] == list(config_mod.GATE_KINDS)
    waveform = manifest["waveform"]
    assert waveform["fs_hz"] == 60_000_000 and waveform["symbol_rate_hz"] == 3_000_000
    assert waveform["sps"] == 20 and waveform["shaping"] == "RECT"
    assert waveform["samples"] == 4096 and waveform["n_symbols"] == 207
    integrity = verify_hashes(dataset_dir, manifest)
    assert integrity["ok"], integrity["mismatches"][:3]

    frames = _read_all(dataset_dir, manifest)
    for path, arrays in frames.items():
        entry = next(item for item in manifest["files"] if item["path"] == path)
        split = entry["split"]
        for key, dtype in EXPECTED_ARRAYS.items():
            assert key in arrays, (path, key)
            if dtype is np.str_:
                assert arrays[key].dtype.kind == "U", (path, key, arrays[key].dtype)
            else:
                assert arrays[key].dtype == dtype, (path, key, arrays[key].dtype)
        assert arrays["X"].shape[1:] == (4096, 2), path
        assert arrays["X"].min() >= -2048 and arrays["X"].max() <= 2047, path
        # Clipping flag is the generator's overflow flag: True implies rail codes.
        # A rail code can also occur with no overflow (a sample rounds exactly to
        # +/-2047); the frozen preprocess gate rejects rail codes either way, and the
        # main train/val/test splits are required to contain none of them.
        rails = np.any((arrays["X"] == -2048) | (arrays["X"] == 2047), axis=(1, 2))
        assert np.all(arrays["clipped"] <= rails), path
        if split in ("train", "val", "test"):
            assert not np.any(rails & ~arrays["clipped"]), (path, "rail without clip flag")
        # Per-frame hash column must match the stored frame bytes.
        import hashlib
        for index in range(min(len(arrays["X"]), 5)):
            digest = hashlib.sha256(np.ascontiguousarray(arrays["X"][index]).tobytes()).hexdigest()
            assert arrays["iq_sha256"][index] == digest, (path, index)

    # Split isolation: no IQ frame may repeat across splits (and generator promises
    # no repeats inside one split either).
    seen = {}
    for path, arrays in frames.items():
        entry = next(item for item in manifest["files"] if item["path"] == path)
        for digest in arrays["iq_sha256"]:
            if digest in seen:
                assert seen[digest] == entry["split"], (path, entry["split"], seen[digest])
                raise AssertionError("duplicate IQ inside split %s: %s" % (entry["split"], path))
            seen[digest] = entry["split"]
    families = {}
    for split in ("train", "val", "test"):
        families[split] = set()
        for entry in manifest["files"]:
            if entry["split"] != split:
                continue
            families[split].update(frames[entry["path"]]["seed_family"].tolist())
    assert not (families["train"] & families["val"])
    assert not (families["train"] & families["test"])
    assert not (families["val"] & families["test"])

    # Counts and SNR coverage.
    variant = "smoke" if dataset_id == "sim-smoke" else "formal"
    split_plan = config_mod.dataset_settings(config, variant)["splits"]
    if dataset_id == "sim-v1":
        train_plan, val_plan = split_plan["train"], split_plan["val"]
        for label in config_mod.CLASS_ORDER:
            train_count = sum(entry["count"] for entry in manifest["files"]
                              if entry["split"] == "train" and entry["label"] == label)
            val_count = sum(entry["count"] for entry in manifest["files"]
                            if entry["split"] == "val" and entry["label"] == label)
            test_count = sum(entry["count"] for entry in manifest["files"]
                             if entry["split"] == "test" and entry["label"] == label)
            assert train_count == train_plan["sessions_per_label"] * train_plan["frames_per_session"], label
            assert val_count == val_plan["sessions_per_label"] * val_plan["frames_per_session"], label
            assert test_count == (len(split_plan["test"]["snr_db_grid"])
                                  * split_plan["test"]["frames_per_class_per_snr"]), label
    for kind in config_mod.GATE_KINDS:
        for split in ("gate_cal", "gate_test"):
            count = sum(entry["count"] for entry in manifest["files"]
                        if entry["split"] == split and entry["label"] == kind)
            assert count > 0
            if dataset_id == "sim-v1":
                assert count == split_plan[split]["frames_per_kind"] * 1, (split, kind, count)
    # Train/val SNR stays in the configured range; test covers the full grid.
    for path, arrays in frames.items():
        entry = next(item for item in manifest["files"] if item["path"] == path)
        if entry["split"] in ("train", "val"):
            assert arrays["snr_db"].min() >= -6.0 - 1e-6
            assert arrays["snr_db"].max() <= 18.0 + 1e-6
        if entry["split"] == "test" and dataset_id == "sim-v1":
            grid = np.asarray(split_plan["test"]["snr_db_grid"], dtype=np.float32)
            assert np.allclose(np.unique(arrays["snr_db"]), grid)
    summary = manifest["summary"]
    total = sum(entry["count"] for entry in manifest["files"])
    assert summary["total_frames"] == total
    # Clipping must stay rare enough for usable training/valuation.
    usable = sum(entry["count"] - entry["clipped_count"] for entry in manifest["files"]
                 if entry["split"] in ("train", "val"))
    expected_usable = sum(entry["count"] - entry["clipped_count"]
                          for entry in manifest["files"]
                          if entry["split"] in ("train", "val"))
    if dataset_id == "sim-v1":
        planned = 4 * (split_plan["train"]["sessions_per_label"]
                       * split_plan["train"]["frames_per_session"]
                       + split_plan["val"]["sessions_per_label"]
                       * split_plan["val"]["frames_per_session"])
        assert usable >= 0.995 * planned, (usable, planned)
