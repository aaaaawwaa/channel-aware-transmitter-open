"""Acceptance checks for the warm-start continuation of the truncated 40-epoch trial.

These tests only read the saved continuation artefacts (they do not retrain anything) and
verify the provenance/limitations that make this diagnostic trustworthy.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ML = ROOT / "ml_training"
TRIAL = ML / "artifacts" / "trials" / "formal-40ep" / "cnn"
OUT = ML / "round2" / "artifacts" / "training-resume" / "formal-40ep-continued" / "cnn"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def metadata():
    return json.loads((OUT / "metadata.json").read_text(encoding="utf-8"))


def test_continuation_is_a_val_only_diagnostic(metadata):
    assert metadata["kind"] == "warm-start-continuation-of-truncated-trial"
    assert metadata["splits_used"] == ["train", "val", "gate_cal"]
    assert metadata["test_split_used_for_tuning"] is False
    assert metadata["real_data_used"] is False
    assert metadata["test_evaluated"] is False
    assert metadata["params"] == 1628
    assert metadata["macs"] == 1278016


def test_warm_start_provenance(metadata):
    warm = metadata["warm_start"]
    assert warm["checkpoint_sha256"] == sha256(TRIAL / "model.pt")
    assert warm["trial_best_epoch"] == 39
    assert abs(warm["trial_best_val_macro_f1"] - 0.700489223920575) < 1e-12
    assert warm["continued_epochs_run"] == 40
    assert warm["optimiser_state_resumed"] is False
    assert warm["loader_state_resumed"] is False
    binding = metadata["binding"]
    assert binding["trial_config_sha256"] != binding["current_config_sha256"]
    assert binding["trial_manifest_sha256"] != binding["current_manifest_sha256"]
    assert binding["npz_arrays_unchanged_by_refreeze"] is True


def test_continuation_reaches_official_val_level(metadata):
    comparison = metadata["comparison_on_val"]
    trial = comparison["trial_40ep_best_epoch_39"]
    continued = comparison["continued_best_epoch_76"]
    official = comparison["round1_final_80ep_best_epoch_76"]
    assert abs(trial["macro_f1"] - 0.700489223920575) < 1e-9
    assert continued["macro_f1"] > trial["macro_f1"] + 0.10
    # warm-start continuation should land at the same val level as the official from-scratch run
    assert abs(continued["macro_f1"] - official["macro_f1"]) < 0.01
    assert abs(continued["accuracy"] - official["accuracy"]) < 0.01
    assert metadata["continued_training"]["best_epoch_absolute"] == 76
    assert abs(metadata["continued_training"]["best_val_macro_f1"]
               - 0.8537868889263236) < 1e-9


def test_artefacts_and_curves(metadata):
    for key in ("model_json", "model_pt"):
        record = metadata["artefacts"][key]
        path = ROOT / record["path"]
        assert path.is_file()
        assert sha256(path) == record["sha256"]
        assert path.stat().st_size == record["bytes"]
    curves = json.loads((OUT / "curves.json").read_text(encoding="utf-8"))
    assert len(curves) == 80
    assert [entry["epoch"] for entry in curves] == list(range(1, 81))
    best = max(curves, key=lambda entry: entry["val_macro_f1"])
    assert best["epoch"] == 76
    assert abs(best["val_macro_f1"] - 0.8537868889263236) < 1e-9
    assert metadata["torch_numpy_parity"]["max_abs_logit_diff"] < 1e-3
    assert metadata["torch_numpy_parity"]["argmax_agreement"] == 1.0
    assert abs(metadata["thresholds"]["min_complex_rms"] - 57.05962767972466) < 1e-9
