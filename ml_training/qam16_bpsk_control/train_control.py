#!/usr/bin/env python3
"""Controlled BPSK-preservation experiment: C0 no augmentation vs C1 QAM-only conjugate.

Both candidates use [raw, second-copy] with the SAME total and per-class counts:
C0: second copy = raw (no augmentation)
C1: second copy = raw for BPSK/QPSK, conjugate for QAM16/QAM64 only.

Same seed, network, optimizer, frozen preprocessing and frozen decision thresholds.
Only train split changes; val/test/gate_test are untouched.  No real frames are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DEFAULT_CONFIG = ROOT / "ml_training" / "config" / "first_round.json"
DEFAULT_DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
FROZEN_RULES = ROOT / "ml_training" / "round2" / "artifacts" / "rule_config.json"
CLASS_ORDER = ("BPSK", "QPSK", "QAM16", "QAM64")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common import config as config_mod  # noqa: E402
from ml_training.common.dataset import load_manifest, load_split, sha256_file, verify_hashes  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.cnn import (build_network, macs_for_length,  # noqa: E402
                                   parameter_count)
from ml_training.round2.predictor import load_rules  # noqa: E402
from ml_training.training.train import (confusion_matrix, export_cnn_model,  # noqa: E402
                                        macro_f1, _predict_logits)


def _json_write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _conj_iq(iq):
    out = np.asarray(iq).copy()
    out[..., 1] = -out[..., 1]
    return out


def _build_train_data(raw, targets, candidate):
    """Return augmented raw data and exact per-class counts."""
    if candidate not in ("C0", "C1"):
        raise ValueError("candidate must be C0 or C1")
    copy = np.asarray(raw).copy()
    if candidate == "C1":
        qam_mask = np.isin(targets, [CLASS_ORDER.index("QAM16"),
                                     CLASS_ORDER.index("QAM64")])
        copy[qam_mask] = _conj_iq(raw[qam_mask])
    aug_raw = np.concatenate([raw, copy], axis=0)
    aug_targets = np.concatenate([targets, targets], axis=0)
    before = {label: int(np.sum(targets == index))
              for index, label in enumerate(CLASS_ORDER)}
    after = {label: int(np.sum(aug_targets == index))
             for index, label in enumerate(CLASS_ORDER)}
    assert len(aug_raw) == 2 * len(raw)
    assert sum(before.values()) == len(raw) and sum(after.values()) == len(aug_raw)
    for label in CLASS_ORDER:
        assert after[label] == 2 * before[label]
    info = {
        "candidate": candidate,
        "train_data_rule": ("[raw, raw]" if candidate == "C0"
                            else "[raw, copy]; copy=raw for BPSK/QPSK, "
                                 "conjugate for QAM16/QAM64 only"),
        "raw_frames": int(len(raw)),
        "augmented_frames": int(len(aug_raw)),
        "per_class_raw": before,
        "per_class_augmented": after,
        "total_equal": True,
        "per_class_equal": True,
        "real_data_used": False,
    }
    return aug_raw, aug_targets, info


def _preprocess_stack(raw):
    frames = np.empty((len(raw), 2, 4096), dtype=np.float32)
    rms = np.empty((len(raw),), dtype=np.float64)
    for index in range(len(raw)):
        x, gate = preprocess_iq(raw[index])
        if not gate["ok"]:
            raise ValueError("training frame rejected by preprocessing: %r" % (gate,))
        frames[index] = x
        rms[index] = gate["rms"]
    return frames, rms


def _save_checkpoint(path, net, optimiser, epoch, best, history, loader_generator,
                     seed, candidate):
    import torch

    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    torch.save({
        "candidate": candidate,
        "epoch": int(epoch),
        "model_state_dict": {k: v.detach().cpu() for k, v in net.state_dict().items()},
        "optimizer_state_dict": optimiser.state_dict(),
        "best": {"epoch": int(best["epoch"]),
                 "val_macro_f1": float(best["val_macro_f1"]),
                 "val_accuracy": float(best["val_accuracy"]),
                 "state": {k: v.detach().cpu()
                           for k, v in best["state"].items()}},
        "history": list(history),
        "loader_generator_state": loader_generator.get_state(),
        "seed": int(seed),
    }, tmp)
    tmp.replace(path)


def _load_checkpoint(path, net, optimiser, loader_generator):
    import torch

    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    net.load_state_dict(checkpoint["model_state_dict"])
    optimiser.load_state_dict(checkpoint["optimizer_state_dict"])
    loader_generator.set_state(checkpoint["loader_generator_state"])
    best = checkpoint["best"]
    best["state"] = {k: v.clone() for k, v in best["state"].items()}
    return checkpoint["epoch"], best, checkpoint["history"]


def train_custom(frames_train, targets_train, frames_val, targets_val, settings, seed,
                 candidate, out_dir, budget_seconds, resume):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    torch.set_num_threads(max(1, int(settings.get("threads", 1))))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    net = build_network(4)
    optimiser = torch.optim.AdamW(net.parameters(), lr=float(settings["lr"]),
                                  weight_decay=float(settings["weight_decay"]))
    criterion = torch.nn.CrossEntropyLoss()
    dataset = TensorDataset(torch.from_numpy(frames_train).float(),
                            torch.from_numpy(targets_train.astype(np.int64)))
    loader_generator = torch.Generator().manual_seed(int(seed) + 1)
    loader = DataLoader(dataset, batch_size=int(settings["batch_size"]), shuffle=True,
                        num_workers=0, generator=loader_generator)
    frames_val_t = torch.from_numpy(frames_val).float()
    history = []
    best = {"val_macro_f1": -1.0, "epoch": 0, "val_accuracy": 0.0, "state": None}
    start_epoch = 1
    started = time.perf_counter()
    last_path = Path(out_dir) / "checkpoint_last.pt"
    best_path = Path(out_dir) / "checkpoint_best.pt"
    if resume and last_path.is_file():
        start_epoch, best, history = _load_checkpoint(last_path, net, optimiser,
                                                      loader_generator)
        start_epoch += 1
        print("resumed %s from epoch %d" % (candidate, start_epoch - 1), flush=True)
    epoch = start_epoch - 1
    timed_out = False
    for epoch in range(start_epoch, int(settings["max_epochs"]) + 1):
        elapsed = time.perf_counter() - started
        if elapsed > float(budget_seconds):
            timed_out = True
            print("budget reached before epoch %d; checkpoint saved" % epoch, flush=True)
            break
        net.train()
        loss_sum, correct, total = 0.0, 0, 0
        for inputs, labels in loader:
            optimiser.zero_grad()
            outputs = net(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimiser.step()
            loss_sum += float(loss.detach()) * inputs.size(0)
            correct += int((outputs.argmax(dim=1) == labels).sum())
            total += inputs.size(0)
        val_logits = _predict_logits(net, frames_val_t.numpy())
        val_predicted = val_logits.argmax(axis=1)
        matrix = confusion_matrix(targets_val, val_predicted, 4)
        f1, recall, precision = macro_f1(matrix)
        accuracy = float(np.mean(val_predicted == targets_val))
        history.append({"epoch": epoch, "train_loss": loss_sum / max(total, 1),
                        "train_accuracy": correct / max(total, 1),
                        "val_accuracy": accuracy, "val_macro_f1": f1,
                        "val_recall": recall, "val_precision": precision,
                        "elapsed_seconds": time.perf_counter() - started})
        if f1 > best["val_macro_f1"] + 1e-12:
            best = {"val_macro_f1": f1, "epoch": epoch, "val_accuracy": accuracy,
                    "state": {k: v.detach().cpu().clone()
                              for k, v in net.state_dict().items()}}
            _save_checkpoint(best_path, net, optimiser, epoch, best, history,
                             loader_generator, seed, candidate)
        _save_checkpoint(last_path, net, optimiser, epoch, best, history,
                         loader_generator, seed, candidate)
        print(json.dumps({"candidate": candidate, "epoch": epoch,
                          "train_loss": history[-1]["train_loss"],
                          "val_macro_f1": f1, "val_accuracy": accuracy,
                          "elapsed_seconds": history[-1]["elapsed_seconds"]}),
              flush=True)
        if epoch - best["epoch"] >= int(settings["patience"]):
            break
    elapsed = time.perf_counter() - started
    if best["state"] is None:
        raise RuntimeError("training produced no best checkpoint")
    net.load_state_dict(best["state"])
    return {"net": net, "best": best, "history": history,
            "train_seconds": elapsed, "epochs_run": epoch,
            "timed_out": timed_out, "checkpoint_last": str(last_path),
            "checkpoint_best": str(best_path)}


def candidate_rules(candidate_dir, model_path, model_sha):
    frozen_doc, _ = load_rules(FROZEN_RULES)
    document = json.loads(json.dumps(frozen_doc))
    document["frozen_round1"]["cnn"] = {
        "path": str(Path(model_path).relative_to(ROOT)).replace("\\", "/"),
        "sha256": model_sha,
    }
    document["candidate"] = {
        "experiment": "qam16_bpsk_control",
        "candidate": Path(candidate_dir).name,
        "frozen_rule_source": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "frozen_rule_source_sha256": sha256_file(FROZEN_RULES),
        "thresholds_recalibrated": False,
        "decision_thresholds": "copied unchanged from frozen round-2 rule config",
    }
    path = Path(candidate_dir) / "rules.json"
    _json_write(path, document)
    return path


def run(candidate, resume=False, budget_seconds=900.0):
    config, config_sha = config_mod.load_config(DEFAULT_CONFIG)
    settings = config_mod.model_settings(config, "formal")
    seed = int(settings["seed"])
    manifest = load_manifest(DEFAULT_DATASET)
    if manifest.get("config_sha256") != config_sha:
        raise ValueError("dataset config hash mismatch")
    integrity = verify_hashes(DEFAULT_DATASET, manifest)
    if not integrity["ok"]:
        raise ValueError("dataset integrity failure: %r" % (integrity["mismatches"][:2],))
    train_data = load_split(DEFAULT_DATASET, manifest, "train",
                            labels=list(CLASS_ORDER))
    val_data = load_split(DEFAULT_DATASET, manifest, "val",
                          labels=list(CLASS_ORDER))
    targets_train = np.asarray([CLASS_ORDER.index(str(label))
                                for label in train_data["label"]], dtype=np.int64)
    targets_val = np.asarray([CLASS_ORDER.index(str(label))
                              for label in val_data["label"]], dtype=np.int64)
    raw_train = np.asarray(train_data["X"], dtype=np.int16)
    raw_val = np.asarray(val_data["X"], dtype=np.int16)
    aug_raw, aug_targets, aug_info = _build_train_data(raw_train, targets_train, candidate)
    frames_train, _ = _preprocess_stack(aug_raw)
    frames_val, _ = _preprocess_stack(raw_val)
    candidate_dir = RESULTS / candidate
    candidate_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    train = train_custom(frames_train, aug_targets, frames_val, targets_val, settings,
                         seed, candidate, candidate_dir, budget_seconds, resume)
    wall = time.time() - started
    frozen_rules_doc, _ = load_rules(FROZEN_RULES)
    thresholds = dict(frozen_rules_doc["frozen_round1"]["gate"])
    params = parameter_count(train["net"])
    macs = macs_for_length(4096, 4)
    stub = {"params": params, "macs": macs,
            "training": {"manifest_sha256": sha256_file(DEFAULT_DATASET / "manifest.json"),
                         "config_sha256": config_sha,
                         "best_epoch": int(train["best"]["epoch"]),
                         "val_macro_f1": float(train["best"]["val_macro_f1"]),
                         "seed": seed, "candidate": candidate,
                         "train_data_rule": aug_info["train_data_rule"]}}
    model_sha, model_size = export_cnn_model(candidate_dir, train["net"], thresholds, stub)
    import torch
    torch.save({"model_state_dict": train["best"]["state"],
                "classes": list(CLASS_ORDER),
                "preprocessing": config_mod.PREPROCESSING,
                "thresholds": thresholds, "seed": seed, "candidate": candidate,
                "train_data_rule": aug_info["train_data_rule"]},
               candidate_dir / "model.pt")
    rules_path = candidate_rules(candidate_dir, candidate_dir / "model.json", model_sha)
    curves_sha = _json_write(candidate_dir / "curves.json", train["history"])
    metadata = {
        "schema": "ml-qam16-bpsk-control-candidate-v1",
        "candidate": candidate,
        "experiment": "qam16_bpsk_control",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_artifacts_modified": False,
        "real_data_used_for_training": False,
        "architecture": "tiny-iq-cnn-v1",
        "preprocessing": config_mod.PREPROCESSING,
        "dataset": str(DEFAULT_DATASET.relative_to(ROOT)).replace("\\", "/"),
        "dataset_manifest_sha256": sha256_file(DEFAULT_DATASET / "manifest.json"),
        "dataset_config_sha256": config_sha,
        "seed": seed,
        "settings": {key: value for key, value in settings.items()
                     if key in ("lr", "weight_decay", "batch_size", "max_epochs",
                                "patience", "threads")},
        "train_data": aug_info,
        "thresholds": thresholds,
        "thresholds_recalibrated": False,
        "threshold_source": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "train_frames": int(len(frames_train)),
        "val_frames": int(len(frames_val)),
        "best_epoch": int(train["best"]["epoch"]),
        "best_val_macro_f1": float(train["best"]["val_macro_f1"]),
        "epochs_run": int(train["epochs_run"]),
        "train_seconds": float(train["train_seconds"]),
        "wall_seconds": float(wall),
        "timed_out": bool(train["timed_out"]),
        "params": int(params), "macs": int(macs),
        "artefacts": {
            "model_json_sha256": model_sha, "model_json_bytes": int(model_size),
            "model_pt_sha256": sha256_file(candidate_dir / "model.pt"),
            "rules_sha256": sha256_file(rules_path),
            "curves_sha256": curves_sha,
            "checkpoint_best_sha256": sha256_file(train["checkpoint_best"]),
            "checkpoint_last_sha256": sha256_file(train["checkpoint_last"]),
        },
        "resume_command": (
            "ml_training\\.venv\\Scripts\\python.exe -B "
            "ml_training\\qam16_bpsk_control\\train_control.py --candidate %s "
            "--resume --budget-seconds %d" % (candidate, int(budget_seconds))),
        "note": ("controlled experiment only; not frozen/deployed; no real data"),
    }
    metadata_sha = _json_write(candidate_dir / "metadata.json", metadata)
    print(json.dumps({"candidate": candidate, "model_sha256": model_sha,
                      "metadata_sha256": metadata_sha,
                      "train_seconds": train["train_seconds"],
                      "best_epoch": train["best"]["epoch"],
                      "val_macro_f1": train["best"]["val_macro_f1"],
                      "timed_out": train["timed_out"]}, indent=2))
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=("C0", "C1"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--budget-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    run(args.candidate, resume=args.resume, budget_seconds=args.budget_seconds)


if __name__ == "__main__":
    main()
