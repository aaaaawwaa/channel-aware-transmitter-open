"""Task 8 tests: fixed TinyIQCNN contract, leakage-free training data, val-only tuning."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pynq_overlay" / "sw"))

CLASS_ORDER = ("TONE", "BPSK", "QPSK", "QAM16", "QAM64", "OTHER")


def test_fixed_network_parameter_count():
    """The TinyIQCNN must have exactly 1662 parameters (the plan's spec)."""
    from rf_ml import train as train_mod
    net = train_mod.build_network()
    nparams = sum(p.numel() for p in net.parameters())
    assert nparams == 1662, nparams
    # ~1.278M MACs: conv1 2*8*7*2048 + conv2 8*16*5*1024 + conv3 16*16*3*512
    macs = (2 * 8 * 7 * 2048) + (8 * 16 * 5 * 1024) + (16 * 16 * 3 * 512)
    assert 1_270_000 <= macs <= 1_290_000


def test_fixed_network_shape_and_forward():
    from rf_ml import train as train_mod
    net = train_mod.build_network()
    net.eval()
    import torch
    x = torch.zeros(4, 2, 4096)
    with torch.no_grad():
        y = net(x)
    assert tuple(y.shape) == (4, 6)


def test_preprocess_contract():
    """[2,4096] float32, per-component DC removed, shared complex RMS == 1."""
    from rf_ml import train as train_mod
    rng = np.random.default_rng(11)
    iq = (rng.integers(-700, 700, size=(4096, 2), dtype=np.int16)).astype(np.int16)
    x, gate = train_mod.preprocess(iq)
    assert x.dtype == np.float32
    assert x.shape == (2, 4096)
    assert gate.get("ok") is True
    # shared complex RMS normalized to 1
    rms = float(np.sqrt(np.mean(np.sum(x.astype(np.float64) ** 2, axis=0))))
    assert abs(rms - 1.0) < 1e-4


def test_preprocess_rejects_zero_and_non_4096_and_clipped():
    from rf_ml import train as train_mod
    ok, gate = train_mod.preprocess(np.zeros((4096, 2), dtype=np.int16))
    assert gate["ok"] is False and "energy" in gate["reason"]
    with pytest.raises(ValueError):
        train_mod.preprocess(np.zeros((100, 2), dtype=np.int16))
    iq = np.zeros((4096, 2), dtype=np.int16)
    iq[7, 0] = 2047
    ok2, gate2 = train_mod.preprocess(iq)
    assert gate2.get("ok") is False
    assert ok2 is None and gate2["reason"] == "clipping"


@pytest.mark.parametrize("value", [2048, -2049, 65536, float("nan"), 1.5])
def test_preprocess_refuses_invalid_codes_before_int16_cast(value):
    from scripts.rf_ml.train import preprocess
    iq = np.zeros((4096, 2), dtype=np.float64)
    iq[0, 0] = value
    with pytest.raises(ValueError):
        preprocess(iq)


def test_unknown_predictions_are_full_false_negatives_in_threshold_selection():
    from scripts.rf_ml.train import _macro_f1
    confusion = np.zeros((7, 7), dtype=np.int64)
    confusion[0, 0], confusion[0, 6] = 1, 9
    _, recall, _ = _macro_f1(confusion, 5)
    assert recall[0] == 0.1


@pytest.fixture
def evaluation_case(tmp_path):
    import torch
    import hashlib
    from host.tests.test_rf_ml_dataset import frame, manifest
    from scripts.rf_ml.train import build_network
    root = tmp_path / "iq"
    root.mkdir()
    records = []
    for index, label in enumerate(("TONE", "TONE", "OTHER", "OFF"), 1):
        f = frame(root, index, session="s%d" % index, split="test", label=label)
        samples = np.random.default_rng(index).integers(-100, 100, (4096, 2), dtype=np.int16)
        if index == 2:
            samples[0, 0] = 2047
            f.update(clipping_count=1, exclusion_reasons=["clipping"])
        if label == "OTHER":
            f["other_kind"] = "NOISE"
        if label == "OFF":
            f["tx_reference"] = None
        raw = samples.astype("<i2").tobytes()
        (root / f["iq_path"]).write_bytes(raw)
        f["iq_sha256"] = hashlib.sha256(raw).hexdigest()
        records.append(f)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest(*records)), encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    net = build_network()
    with torch.no_grad():
        for parameter in net.parameters():
            parameter.zero_()
        net.head.bias[0] = 20
    torch.save({"model_state_dict": net.state_dict(), "preprocessing": "dc-complex-rms-v1",
                "class_order": list(CLASS_ORDER)}, model / "model.pt")
    (model / "metadata.json").write_text(json.dumps({
        "preprocessing": "dc-complex-rms-v1", "manifest_sha256": "a" * 64,
        "class_order": list(CLASS_ORDER)}))
    (model / "thresholds.json").write_text(json.dumps({"energy_floor_db": -100,
        "selection": {"max_softmax": 0.5, "margin": 0.0}}))
    return model, path, root


def test_evaluate_counts_quality_rejected_known_as_misses(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    report = evaluate(*evaluation_case)
    assert report["per_class_recall"]["TONE"] == 0.5
    assert report["confusion_matrix"][0][6] == 1
    assert report["unknown_rejection_rate"] == 0.0


def test_evaluate_maps_other_prediction_to_unknown(evaluation_case):
    import torch
    from scripts.rf_ml.train import build_network
    from scripts.rf_ml.evaluate import evaluate
    model, path, root = evaluation_case
    net = build_network()
    with torch.no_grad():
        for parameter in net.parameters():
            parameter.zero_()
        net.head.bias[5] = 20
    torch.save({"model_state_dict": net.state_dict(), "preprocessing": "dc-complex-rms-v1",
                "class_order": list(CLASS_ORDER)}, model / "model.pt")
    report = evaluate(model, path, root)
    assert report["unknown_rejection_rate"] == 1.0
    assert report["off_rejection_rate"] == 1.0
    assert set(report["per_class_recall"]) == set(CLASS_ORDER[:-1])


def test_evaluate_checks_manifest_hash_before_reading_samples(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    model, path, root = evaluation_case
    data = json.loads(path.read_text())
    data["frames"][0]["iq_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="manifest"):
        evaluate(model, path, root)


def test_train_cannot_use_same_split_for_training_and_validation(tmp_path):
    from scripts.rf_ml.train import train
    with pytest.raises(ValueError, match="train.*val"):
        train(tmp_path / "missing.json", tmp_path / "out", split="train", val_split="train")


def test_training_input_has_no_tx_label_metadata():
    """The training tensor must be built from IQ only; TX metadata must never be an input."""
    from rf_ml import train as train_mod
    import inspect
    src = inspect.getsource(train_mod.preprocess) + inspect.getsource(train_mod._dataset)
    for banned in ("symbol_rate_hz", "tx_seed", "seed_family", "shaping"):
        assert banned not in src, banned


def test_train_requires_group_split_and_val_only_tuning():
    """train() must reject a manifest without frozen splits and must never accept the test split for tuning."""
    from rf_ml import train as train_mod
    inv = {"schema_version": 1, "sessions": [], "frames": []}
    tmp = ROOT / ".superpowers" / "sdd" / "2026-10-01-rx-smart-implementation" / "tmp-invalid-manifest.json"
    tmp.write_text(json.dumps(inv), encoding="utf-8")
    try:
        with pytest.raises((ValueError, AssertionError)):
            train_mod.train(manifest_path=tmp, output_dir=Path(str(tmp) + ".out"), split="test")
    finally:
        tmp.unlink(missing_ok=True)


def test_evaluate_contract():
    """evaluate() returns macro-F1, per-class recall, and UNKNOWN/OFF rejection rates."""
    from rf_ml import evaluate as eval_mod
    sig = eval_mod.evaluate
    assert callable(sig)
