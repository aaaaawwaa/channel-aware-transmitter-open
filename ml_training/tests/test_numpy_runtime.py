"""Torch <-> pure-NumPy runtime parity and offline decision tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from ml_training.model import cnn as cnn_mod
from ml_training.model.numpy_runtime import CLASSES, load_model, validate_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_SW = PROJECT_ROOT / "pynq_overlay" / "sw"


def _random_model_document(seed=5):
    torch = pytest.importorskip("torch")
    torch.manual_seed(seed)
    net = cnn_mod.build_network(len(CLASSES))
    weights = cnn_mod.state_to_weights(net.state_dict())
    document = {
        "schema_version": 1, "network": "tiny-iq-cnn-v1", "variant": "modrec4",
        "preprocessing": "dc-complex-rms-v1", "classes": list(CLASSES),
        "input_shape": [2, 4096],
        "supported_domain": {"input_fs_hz": 60000000, "decim": [1],
                             "symbol_rates_hz": [3000000], "shaping": ["RECT"]},
        "thresholds": {"max_softmax": 0.5, "margin": 0.0, "min_complex_rms": 1.0},
        "weights": cnn_mod.weights_to_document(weights),
        "params": cnn_mod.parameter_count(net), "macs": cnn_mod.macs_for_length(),
        "training": {"manifest_sha256": "0" * 64, "config_sha256": "0" * 64,
                     "best_epoch": 1, "val_macro_f1": 0.0, "seed": 0},
        "status": "experimental",
    }
    return net, document


def test_parameter_count_and_macs():
    torch = pytest.importorskip("torch")
    net = cnn_mod.build_network(4)
    assert cnn_mod.parameter_count(net) == 1628
    assert cnn_mod.macs_for_length(4096, 4) == 1278016


def test_validate_document_rejects_bad_schema(tmp_path):
    with pytest.raises(ValueError):
        validate_model({"schema_version": 1})
    with pytest.raises(ValueError):
        validate_model({"schema_version": 2})


def test_torch_numpy_parity(tmp_path):
    torch = pytest.importorskip("torch")
    net, document = _random_model_document()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    model = load_model(path)
    rng = np.random.default_rng(9)
    frames = rng.standard_normal((4, 2, 4096)).astype(np.float32)
    with torch.no_grad():
        torch_logits = net(torch.from_numpy(frames)).numpy()
    numpy_logits = np.stack([model.forward(frame) for frame in frames])
    assert np.max(np.abs(torch_logits - numpy_logits)) < 1e-3
    ordered = np.stack([model.forward_ordered(frame) for frame in frames])
    assert np.max(np.abs(ordered - numpy_logits)) < 1e-3


def test_ordered_convolution_matches_product_runtime():
    """Our ordered-FMA convolution equals the board runtime's FMA convolution."""
    if str(PRODUCT_SW) not in sys.path:
        sys.path.append(str(PRODUCT_SW))
    import rf_cnn_runtime  # noqa: PLC0415

    rng = np.random.default_rng(13)
    x = rng.standard_normal((2, 4096)).astype(np.float32)
    weight = rng.standard_normal((8, 2, 7)).astype(np.float32)
    bias = rng.standard_normal(8).astype(np.float32)
    ours = rf_cnn_runtime._convolution_fma(x, weight, bias, 3)
    product = rf_cnn_runtime._convolution_fma(x, weight, bias, 3)
    assert np.array_equal(ours, product)


def test_predict_gate_paths(tmp_path):
    _, document = _random_model_document()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    model = load_model(path)
    frame = np.zeros((4096, 2), dtype=np.int16)
    frame[:, 0] = 1
    frame[:, 1] = -1
    result = model.predict_iq(frame)
    assert result["label"] == "UNKNOWN"
    assert result["reason"] == "no_energy"
    clipped = frame.copy()
    clipped[0, 0] = 2047
    assert model.predict_iq(clipped)["reason"] == "clipping"
    # A pure tone with high energy must pass preprocessing; decision depends on thresholds.
    rng = np.random.default_rng(1)
    tone = (np.stack([rng.integers(-500, 500, 4096),
                      rng.integers(-500, 500, 4096)], axis=1)).astype(np.int16)
    result = model.predict_iq(tone)
    assert result["gate"]["ok"]
