"""Checkpoint provenance and independent evaluation regressions."""
import hashlib
import json

import pytest

from host.tests.test_rf_ml_training import evaluation_case


def _bind(case):
    import torch
    from host.tests.test_rf_ml_dataset import frame, manifest
    model, path, root = case
    data = json.loads(path.read_text())
    for index, split in ((10, "train"), (11, "val")):
        f = frame(root, index, session=split, split=split)
        extra = manifest(f)
        data["sessions"].extend(extra["sessions"])
        data["frames"].extend(extra["frames"])
    path.write_text(json.dumps(data))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = dict(training_manifest_sha256=digest, initializer_sha256=None,
                      ancestors=[], ancestry_complete=True)
    ckpt = torch.load(model / "model.pt", weights_only=True)
    ckpt["provenance"] = provenance
    torch.save(ckpt, model / "model.pt")
    metadata = json.loads((model / "metadata.json").read_text())
    metadata.update(manifest=str(path), manifest_sha256=digest, provenance=provenance,
                    model_checkpoint_sha256=hashlib.sha256((model / "model.pt").read_bytes()).hexdigest())
    (model / "metadata.json").write_text(json.dumps(metadata))
    return metadata


def test_evaluate_refuses_checkpoint_swap_after_training_receipt(evaluation_case):
    import torch
    from scripts.rf_ml.evaluate import evaluate
    _bind(evaluation_case)
    model, path, root = evaluation_case
    checkpoint = torch.load(model / "model.pt", weights_only=True)
    checkpoint["model_state_dict"]["head.bias"][0] += 1
    torch.save(checkpoint, model / "model.pt")
    with pytest.raises(ValueError, match="checkpoint.*SHA256"):
        evaluate(model, path, root)


@pytest.mark.parametrize("overlap", ["session", "iq", "seed"])
def test_evaluate_rejects_test_seen_by_initializer_ancestor(evaluation_case, overlap):
    import torch
    from host.tests.test_rf_ml_dataset import manifest
    from scripts.rf_ml.evaluate import evaluate
    metadata = _bind(evaluation_case)
    model, path, root = evaluation_case
    original = json.loads(path.read_text())["frames"][0]
    original["split"] = "train"
    if overlap != "session":
        original["session_id"] = "ancestor-session"
    if overlap != "seed":
        original["tx_reference"]["seed_family"] = "ancestor-seed"
    if overlap != "iq":
        raw = bytearray((root / original["iq_path"]).read_bytes())
        raw[:2] = b"\x00\x00"
        original["iq_path"] = "ancestor.iq"
        (root / original["iq_path"]).write_bytes(raw)
        original["iq_sha256"] = hashlib.sha256(raw).hexdigest()
    ancestor = root / "ancestor.json"
    ancestor.write_text(json.dumps(manifest(original)))
    metadata["provenance"]["ancestors"] = [dict(
        manifest=str(ancestor), manifest_sha256=hashlib.sha256(ancestor.read_bytes()).hexdigest(),
        checkpoint_sha256="b" * 64)]
    metadata["provenance"]["initializer_sha256"] = "b" * 64
    ckpt = torch.load(model / "model.pt", weights_only=True)
    ckpt["provenance"] = metadata["provenance"]
    torch.save(ckpt, model / "model.pt")
    metadata["model_checkpoint_sha256"] = hashlib.sha256((model / "model.pt").read_bytes()).hexdigest()
    (model / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="ancestor.*leaks"):
        evaluate(model, path, root)


def test_historical_path_only_receipt_never_claims_complete_lineage(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    _bind(evaluation_case)
    model, path, root = evaluation_case
    metadata = json.loads((model / "metadata.json").read_text())
    metadata.pop("provenance")
    metadata.pop("model_checkpoint_sha256")
    (model / "metadata.json").write_text(json.dumps(metadata))
    assert evaluate(model, path, root)["lineage_verified"] is False


def test_bound_original_checkpoint_has_verified_lineage(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    _bind(evaluation_case)
    assert evaluate(*evaluation_case)["lineage_verified"] is True


def test_bound_checkpoint_rejects_metadata_provenance_swap(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    metadata = _bind(evaluation_case)
    model, path, root = evaluation_case
    metadata["provenance"]["ancestry_complete"] = False
    (model / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="provenance"):
        evaluate(model, path, root)


def test_clean_diagnostic_does_not_replace_official_rejected_denominator(evaluation_case):
    from scripts.rf_ml.evaluate import evaluate
    report = evaluate(*evaluation_case)
    assert report["per_class_recall"]["TONE"] == .5
    diagnostic = report["quality_usable_diagnostic"]
    assert diagnostic["per_class_recall"]["TONE"] == 1.0
    assert diagnostic["known_frames"] == 1
    assert diagnostic["used_for_acceptance"] is False


@pytest.fixture
def tiny_training_manifest(tmp_path):
    import numpy as np
    from host.tests.test_rf_ml_dataset import frame, manifest
    root = tmp_path / "train-iq"
    root.mkdir()
    records = []
    for split in ("train", "val", "test"):
        for label in ("TONE", "OTHER", "OFF"):
            index = len(records) + 20
            f = frame(root, index, session=f"{split}-{label}", split=split, label=label)
            if label == "OTHER":
                f["other_kind"] = "NOISE"
            if label == "OFF":
                f["tx_reference"] = None
            peak = 2 if label == "OFF" else 100
            iq = np.random.default_rng(index).integers(-peak, peak + 1, (4096, 2), dtype=np.int16)
            raw = iq.astype("<i2").tobytes()
            (root / f["iq_path"]).write_bytes(raw)
            f["iq_sha256"] = hashlib.sha256(raw).hexdigest()
            records.append(f)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest(*records)))
    return path, root


def test_train_binds_receipt_and_records_initializer_hash(tiny_training_manifest, tmp_path):
    import torch
    from scripts.rf_ml.train import train, validate_provenance
    path, root = tiny_training_manifest
    original = tmp_path / "original"
    metadata = train(path, original, root=root, max_epochs=1)
    checkpoint = torch.load(original / "model.pt", weights_only=True)
    assert validate_provenance(metadata, checkpoint, original / "model.pt")
    assert set(metadata["versions"]) == {"python", "numpy", "torch"}
    fine = tmp_path / "fine"
    child = train(path, fine, initialize=original / "model.pt", root=root, max_epochs=1)
    assert child["provenance"]["initializer_sha256"] == metadata["model_checkpoint_sha256"]
    assert child["provenance"]["ancestry_complete"] is True
    assert len(child["provenance"]["ancestors"]) == 1


def test_train_refuses_initializer_checkpoint_swap(tiny_training_manifest, tmp_path):
    import torch
    from scripts.rf_ml.train import train
    path, root = tiny_training_manifest
    original = tmp_path / "original"
    train(path, original, root=root, max_epochs=1)
    ckpt = torch.load(original / "model.pt", weights_only=True)
    ckpt["model_state_dict"]["head.bias"][0] += 1
    torch.save(ckpt, original / "model.pt")
    with pytest.raises(ValueError, match="checkpoint.*SHA256"):
        train(path, tmp_path / "child", initialize=original / "model.pt", root=root, max_epochs=1)
