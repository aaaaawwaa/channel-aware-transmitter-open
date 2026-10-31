"""Tests for the independent reference checker (ml_training/data/reference_check.py).

The positive path must accept the generated dataset; every negative case below
mutates a copy of the dataset and must make the checker FAIL (tampered frame,
changed label, cross-split copy, clipped-flag/hash/manifest tampering).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from data_test_support import (  # noqa: E402
    copy_dataset, failing_check_names, read_manifest, read_npz, rewrite_npz,
    write_manifest,
)

import generate  # noqa: E402
import reference_check  # noqa: E402


def run_on(dataset_dir: Path, config_path: Path) -> dict:
    return reference_check.run_check(dataset_dir, config_path=config_path,
                                     out_json=None, out_md=None)


# ---------------------------------------------------------------------------
# positive path
# ---------------------------------------------------------------------------
def test_reference_check_passes_on_generated_dataset(tiny_dataset, tiny_report):
    report = tiny_report
    assert report["passed"] is True, failing_check_names(report)
    assert report["checks_passed"] == report["checks_total"]
    stats = report["stats"]
    assert stats["frames_checked"] == 96
    assert stats["reconstruction_checked"] == 96
    assert stats["duplicate_iq"] == {"cross_split": 0, "intra_split": 0,
                                     "unique_frames": 96, "frames_hashed": 96}
    assert stats["peak_normalisation"] == "complex"
    names = {check["name"] for check in report["checks"]}
    for expected in ("exact_frame_reconstruction", "bit_mapping_vs_contract_and_product",
                     "constellation_power_coefficients", "rotated_component_bound",
                     "gate_clip_rails", "snr_residual", "npz_file_sha256"):
        assert expected in names
    mapping_check = next(check for check in report["checks"]
                         if check["name"] == "bit_mapping_vs_contract_and_product")
    assert mapping_check["patterns_checked"] == 2 + 4 + 16 + 64
    assert "pynq_overlay/sw/rf_iq_signal.py" in report["product_mapper"] or ".py" in report["product_mapper"]


def test_reference_check_writes_json_and_markdown(tiny_dataset, tmp_path):
    json_path = tmp_path / "reports" / "reference-check.json"
    md_path = tmp_path / "reports" / "reference-check.md"
    report = reference_check.run_check(tiny_dataset["dir"], config_path=tiny_dataset["config_path"],
                                       out_json=json_path, out_md=md_path)
    assert report["passed"] is True
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "ml-modrec-reference-check-v1"
    assert loaded["passed"] is True
    assert "PASS" in md_path.read_text(encoding="utf-8")


def test_reference_check_cli_exit_code(tiny_dataset, tmp_path):
    exit_code = reference_check.main([
        "--dataset", str(tiny_dataset["dir"]),
        "--config", str(tiny_dataset["config_path"]),
        "--out", str(tmp_path / "rc.json"),
        "--md", str(tmp_path / "rc.md"),
    ])
    assert exit_code == 0


def test_reference_check_requires_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        reference_check.run_check(tmp_path, config_path=REPO_ROOT / "ml_training" / "config" / "first_round.json")


# ---------------------------------------------------------------------------
# negative cases (each mutation must make the checker fail)
# ---------------------------------------------------------------------------
def test_detects_tampered_frame_bytes(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    rel = "frames/train-BPSK.npz"
    arrays = read_npz(dataset, rel)
    arrays["X"][0, 0, 0] = int(arrays["X"][0, 0, 0]) + 1
    rewrite_npz(dataset, rel, arrays, update_manifest=False)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    failures = failing_check_names(report)
    assert "npz_file_sha256" in failures
    assert "exact_frame_reconstruction" in failures
    assert "iq_sha256" in failures


def test_detects_label_tamper(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    rel = "frames/train-BPSK.npz"
    arrays = read_npz(dataset, rel)
    arrays["label"][0] = "QPSK"
    rewrite_npz(dataset, rel, arrays, update_manifest=True)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    failures = failing_check_names(report)
    assert "frame_metadata" in failures  # frame label no longer matches the frozen plan


def test_detects_cross_split_copy(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    source = read_npz(dataset, "frames/train-BPSK.npz")
    rel = "frames/val-BPSK.npz"
    arrays = read_npz(dataset, rel)
    arrays["X"][0] = source["X"][0]
    arrays["iq_sha256"][0] = source["iq_sha256"][0]
    rewrite_npz(dataset, rel, arrays, update_manifest=True)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    assert report["stats"]["duplicate_iq"]["cross_split"] == 1
    assert "seed_uniqueness_and_duplicates" in failing_check_names(report)


def test_detects_clipped_flag_tamper(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    rel = "gate/gate_test-CLIP.npz"
    arrays = read_npz(dataset, rel)
    arrays["clipped"][0] = False
    rewrite_npz(dataset, rel, arrays, update_manifest=True)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    failures = failing_check_names(report)
    assert "clipping_flags" in failures or "gate_clip_rails" in failures or "clipped_statistics" in failures


def test_detects_iq_sha256_field_tamper(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    rel = "frames/val-QPSK.npz"
    arrays = read_npz(dataset, rel)
    arrays["iq_sha256"][0] = "0" * 64
    rewrite_npz(dataset, rel, arrays, update_manifest=True)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    assert "iq_sha256" in failing_check_names(report)


def test_detects_manifest_clipped_count_tamper(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    manifest = read_manifest(dataset)
    manifest["files"][0]["clipped_count"] = int(manifest["files"][0]["clipped_count"]) + 1
    write_manifest(dataset, manifest)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    failures = failing_check_names(report)
    assert "npz_arrays" in failures or "clipped_statistics" in failures


def test_detects_manifest_config_sha_tamper(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    manifest = read_manifest(dataset)
    manifest["config_sha256"] = "0" * 64
    write_manifest(dataset, manifest)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    assert "config_identity" in failing_check_names(report)


def test_detects_missing_npz_file(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    (dataset / "frames" / "val-BPSK.npz").unlink()
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    assert "npz_file_sha256" in failing_check_names(report)


def test_detects_duplicate_inside_split(tiny_dataset, tmp_path):
    dataset = copy_dataset(tiny_dataset["dir"], tmp_path)
    rel = "frames/train-QPSK.npz"
    arrays = read_npz(dataset, rel)
    arrays["X"][1] = arrays["X"][0]
    arrays["iq_sha256"][1] = arrays["iq_sha256"][0]
    rewrite_npz(dataset, rel, arrays, update_manifest=True)
    report = run_on(dataset, tiny_dataset["config_path"])
    assert report["passed"] is False
    assert report["stats"]["duplicate_iq"]["intra_split"] == 1


def test_mapping_table_is_checked_against_explicit_contract():
    # sanity: the checker's local mapper equals the frozen axis-table contract
    assert reference_check.map_bits_to_symbol("QAM16", (0, 0, 0, 0)) == (3.0, 3.0)
    assert reference_check.map_bits_to_symbol("QAM16", (1, 1, 1, 1)) == (-1.0, -1.0)
    assert reference_check.map_bits_to_symbol("QAM64", (0, 0, 0, 0, 0, 0)) == (7.0, 7.0)
    assert reference_check.map_bits_to_symbol("QAM64", (1, 0, 0, 1, 0, 0)) == (-7.0, -7.0)
    assert reference_check.map_bits_to_symbol("QPSK", (0, 1)) == (1.0, -1.0)
    assert reference_check.map_bits_to_symbol("BPSK", (1,)) == (-1.0, 0.0)
