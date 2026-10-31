"""Dataset manifest / npz loading tests (CONTRACTS §3, §7)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from ml_training.evaluation import _common as C


def _copy_dataset(source, target):
    target = Path(target) / "sim-small"
    shutil.copytree(source, target)
    return target


def test_load_fixture_dataset_contract(sim_small):
    manifest = C.load_manifest(sim_small, verify_hashes=True)
    assert manifest["schema"] == C.DATASET_SCHEMA
    assert manifest["dataset_id"] == "sim-small"
    assert manifest["classes"] == C.CLASS_ORDER
    assert manifest["gate_kinds"] == C.GATE_KINDS
    assert manifest["_sha256"] == C.sha256_file(sim_small / "manifest.json")


def test_test_split_drops_clipped_frames(sim_small):
    manifest = C.load_manifest(sim_small)
    frames = C.load_split(sim_small, manifest, "test", labels=C.CLASS_ORDER,
                          include_clipped=False, verify_file_hashes=True,
                          verify_frame_hashes=True)
    assert frames["X"].shape == (8, C.SAMPLES, 2)
    assert frames["X"].dtype == np.int16
    assert frames["_dropped_clipped"] == 4
    assert frames["_clipped_flags"].sum() == 0
    assert sorted(set(str(label) for label in frames["label"])) == sorted(C.CLASS_ORDER)
    counts = {label: int(np.sum(frames["label"] == label)) for label in C.CLASS_ORDER}
    assert counts == {label: 2 for label in C.CLASS_ORDER}
    assert set(C.round_snr(value) for value in frames["snr_db"]) == {0.0, 10.0}


def test_gate_test_keeps_clip_frames(sim_small):
    manifest = C.load_manifest(sim_small)
    frames = C.load_split(sim_small, manifest, "gate_test", kinds=C.GATE_KINDS,
                          include_clipped=True, verify_frame_hashes=True)
    assert frames["X"].shape[0] == 12
    assert int(np.sum(frames["_clipped_flags"])) == 3
    for kind in C.GATE_KINDS:
        assert int(np.sum(frames["label"] == kind)) == 3
    clip_rows = frames["X"][frames["label"] == "CLIP"]
    assert C.out_of_signed12(clip_rows)


def test_manifest_dataset_id_must_match_directory(sim_small, tmp_path):
    copied = _copy_dataset(sim_small, tmp_path)
    renamed = copied.parent / "wrong-name"
    copied.rename(renamed)
    with pytest.raises(C.DatasetError):
        C.load_manifest(renamed, verify_hashes=True)


def test_npz_hash_mismatch_is_rejected(sim_small, tmp_path):
    copied = _copy_dataset(sim_small, tmp_path)
    path = copied / "frames" / "test-BPSK.npz"
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: np.asarray(archive[key]) for key in archive.files}
    arrays["X"] = arrays["X"].copy()
    arrays["X"][0, 0, 0] = 123
    np.savez(path, **arrays)  # file changed; manifest sha256 is now stale
    with pytest.raises(C.DatasetError):
        C.load_manifest(copied, verify_hashes=True)


def test_per_frame_iq_sha256_mismatch_is_rejected(sim_small, tmp_path):
    copied = _copy_dataset(sim_small, tmp_path)
    path = copied / "frames" / "test-BPSK.npz"
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: np.asarray(archive[key]) for key in archive.files}
    digests = np.asarray(arrays["iq_sha256"]).copy()
    digests[0] = "f" * 64
    arrays["iq_sha256"] = digests
    np.savez(path, **arrays)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "frames/test-BPSK.npz":
            entry["sha256"] = C.sha256_file(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded = C.load_manifest(copied, verify_hashes=True)  # file hash now matches
    with pytest.raises(C.DatasetError):
        C.load_split(copied, loaded, "test", labels=C.CLASS_ORDER,
                     verify_file_hashes=True, verify_frame_hashes=True)


def test_unknown_split_is_rejected(sim_small):
    manifest = C.load_manifest(sim_small)
    with pytest.raises(C.DatasetError):
        C.load_split(sim_small, manifest, "validation")


def test_missing_manifest_raises(tmp_path):
    with pytest.raises(C.DatasetError):
        C.load_manifest(tmp_path)
