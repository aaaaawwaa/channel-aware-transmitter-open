"""Adapter tests: the evaluation tools must call the real model modules.

Two layers are tested:

* deterministic wiring tests with an injected fake ``ml_training.model.*`` module
  (works even before the Lead's modules exist), and
* integration tests that call the real Lead modules/artefacts when present
  (``pytest.mark.skipif`` otherwise).
"""
from __future__ import annotations

import importlib.util
import sys
import types

import numpy as np
import pytest

from ml_training.evaluation import _common as C


def _fake_package(monkeypatch):
    package = types.ModuleType("ml_training.model")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "ml_training.model", package)


class _DummyModel(object):
    def __init__(self):
        self.iq_shapes = []

    def predict_iq(self, iq):
        self.iq_shapes.append(tuple(np.asarray(iq).shape))
        assert np.asarray(iq).dtype == np.int16
        return {"label": "BPSK", "reason": "", "score": 0.9, "margin": 0.8,
                "scores": [0.9, 0.05, 0.03, 0.02],
                "gate": {"ok": True, "reason": ""}}


def test_cnn_adapter_calls_load_model_and_normalises(monkeypatch, fake_model):
    _fake_package(monkeypatch)
    runtime = types.ModuleType("ml_training.model.numpy_runtime")
    runtime.load_model = lambda path: _DummyModel()
    monkeypatch.setitem(sys.modules, "ml_training.model.numpy_runtime", runtime)

    predictor = C.load_cnn_predictor(fake_model)
    assert predictor.info["classes"] == C.CLASS_ORDER
    assert predictor.info["kind"] == "cnn"
    output = predictor.predict_iq(np.zeros((C.SAMPLES, 2), dtype=np.int16))
    assert output["label"] == "BPSK"
    assert output["scores"] == [0.9, 0.05, 0.03, 0.02]
    assert output["reason"] == ""


def test_cnn_adapter_retries_with_the_other_target(monkeypatch, fake_model):
    _fake_package(monkeypatch)
    calls = []

    def load_model(path):
        calls.append(path)
        if str(path).endswith("model.json"):
            raise FileNotFoundError("loader wants the artifact directory")
        return _DummyModel()

    runtime = types.ModuleType("ml_training.model.numpy_runtime")
    runtime.load_model = load_model
    monkeypatch.setitem(sys.modules, "ml_training.model.numpy_runtime", runtime)

    predictor = C.load_cnn_predictor(fake_model)
    assert predictor.predict_iq(np.zeros((C.SAMPLES, 2), dtype=np.int16))["label"] == "BPSK"
    assert calls[0].endswith("model.json")
    assert calls[-1] == str(fake_model)


def test_baseline_adapter_supports_object_method(monkeypatch, fake_model):
    _fake_package(monkeypatch)

    class _Baseline(object):
        def predict_iq(self, iq):
            return {"label": "QPSK", "scores": [0.1, 0.8, 0.05, 0.05], "reason": ""}

    baseline = types.ModuleType("ml_training.model.baseline")
    baseline.load_baseline = lambda path: _Baseline()
    monkeypatch.setitem(sys.modules, "ml_training.model.baseline", baseline)

    predictor = C.load_baseline_predictor(fake_model)
    output = predictor.predict_iq(np.zeros((C.SAMPLES, 2), dtype=np.int16))
    assert output["label"] == "QPSK"
    assert output["score"] == pytest.approx(0.8)
    assert output["margin"] == pytest.approx(0.7)


def test_baseline_adapter_supports_module_level_predict_iq(monkeypatch, fake_model):
    _fake_package(monkeypatch)

    def module_predict_iq(model, iq):
        assert model == {"fixture": True}
        return {"label": "QAM16", "scores": [0.05, 0.05, 0.8, 0.1], "reason": ""}

    baseline = types.ModuleType("ml_training.model.baseline")
    baseline.load_baseline = lambda path: {"fixture": True}
    baseline.predict_iq = module_predict_iq
    monkeypatch.setitem(sys.modules, "ml_training.model.baseline", baseline)

    predictor = C.load_baseline_predictor(fake_model)
    assert predictor.predict_iq(np.zeros((C.SAMPLES, 2), dtype=np.int16))["label"] == "QAM16"


def test_missing_artefact_raises(tmp_path):
    with pytest.raises(C.ArtifactMissing):
        C.load_cnn_predictor(tmp_path / "does-not-exist")


def test_missing_runtime_module_raises(monkeypatch, fake_model):
    _fake_package(monkeypatch)  # package present, runtime module missing
    monkeypatch.delitem(sys.modules, "ml_training.model.numpy_runtime", raising=False)
    with pytest.raises(C.ArtifactMissing):
        C.load_cnn_predictor(fake_model)


def _find_spec(dotted):
    try:
        return importlib.util.find_spec(dotted)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def test_real_numpy_runtime_loads_fixture_model_when_present(sim_small, fake_model):
    if _find_spec("ml_training.model.numpy_runtime") is None:
        pytest.skip("Lead's ml_training/model/numpy_runtime.py is not present yet")
    manifest = C.load_manifest(sim_small)
    frames = C.load_split(sim_small, manifest, "test", labels=C.CLASS_ORDER)
    predictor = C.load_cnn_predictor(fake_model)
    output = predictor.predict_iq(frames["X"][0])
    assert output["label"] in C.CLASS_ORDER + [C.UNKNOWN]
    assert output["scores"] is not None and len(output["scores"]) == 4


def test_real_artifacts_are_consumable_when_present():
    from conftest import ROOT
    model_json = ROOT / "ml_training" / "artifacts" / "cnn" / "model.json"
    if not model_json.is_file() or _find_spec("ml_training.model.numpy_runtime") is None:
        pytest.skip("trained CNN artefact / runtime not produced yet")
    predictor = C.load_cnn_predictor(model_json.parent)
    rng = np.random.default_rng(3)
    iq = rng.integers(-400, 400, size=(C.SAMPLES, 2), dtype=np.int16)
    output = predictor.predict_iq(iq)
    assert output["scores"] is not None and len(output["scores"]) == 4
    assert output["label"] in C.CLASS_ORDER + [C.UNKNOWN]
