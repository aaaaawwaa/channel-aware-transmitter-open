"""Smoke test for the Lead-owned training/export pipeline.

Skipped until the data-gen smoke dataset exists (``ml_training/datasets/sim-smoke``);
it intentionally uses a reduced epoch budget so the whole path (data -> baseline ->
CNN -> thresholds -> export -> numpy parity -> timing) runs in a few seconds.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ml_training.common import config as config_mod
from ml_training.training import train as train_mod

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DATASET = PROJECT_ROOT / "ml_training" / "datasets" / "sim-smoke"


def test_smoke_training_export(tmp_path):
    if not (SMOKE_DATASET / "manifest.json").is_file():
        pytest.skip("smoke dataset not generated yet: " + str(SMOKE_DATASET))
    pytest.importorskip("torch")
    metadata = train_mod.run_training(
        dataset_dir=SMOKE_DATASET, out_dir=tmp_path, variant="smoke",
        max_epochs_override=1)
    assert metadata["params"] == 1628
    assert metadata["test_split_used_for_tuning"] is False
    assert (tmp_path / "cnn" / "model.json").is_file()
    assert (tmp_path / "cnn" / "model.pt").is_file()
    assert (tmp_path / "baseline" / "model.json").is_file()
    model_doc = __import__("json").loads((tmp_path / "cnn" / "model.json").read_text())
    assert model_doc["classes"] == list(config_mod.CLASS_ORDER)
    thresholds = model_doc["thresholds"]
    assert set(thresholds) == {"max_softmax", "margin", "min_complex_rms"}
    assert thresholds["min_complex_rms"] > 0.0
