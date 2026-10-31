#!/usr/bin/env python3
"""One bounded BPSK/QPSK boundary repair candidate for candidate A.

Frozen inputs: installed candidate A and the six-frame replay package are untouched.
Training data: existing sim-v1 TRAIN only.  Validation: existing sim-v1 VAL only.
No test, task80, new capture, threshold, arbitration, gate or preprocessing change.

Single candidate R1:
  - initialize from A's best checkpoint;
  - train on the existing TRAIN plus A's train-only conjugate copy;
  - oversample BPSK and QPSK (weight 2) while retaining QAM16/QAM64 (weight 1);
  - low-LR fine-tune, one seed.

VAL selection is pre-registered:
  - guard: QPSK raw recall >= A_QPSK - 0.01;
           QAM16 raw recall >= A_QAM16 - 0.02;
           QAM64 raw recall >= A_QAM64 - 0.02;
  - maximize BPSK raw recall + 0.5 * QPSK raw recall.
The test split is never loaded here; it is only a post-freeze known-regression
comparison in evaluate_repair.py.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
CANDIDATE_DIR = RESULTS / "candidate_R1"
DATASET = ROOT / "ml_training" / "datasets" / "sim-v1"
A_DIR = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
FROZEN_RULES = ROOT / "ml_training" / "round2" / "artifacts" / "rule_config.json"
SEED = 20261023
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split, sha256_file  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.cnn import build_network, parameter_count, macs_for_length  # noqa: E402
from ml_training.training.train import (confusion_matrix, export_cnn_model,  # noqa: E402
                                        macro_f1, _predict_logits)


def jwrite(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def preprocess_stack(raw):
    frames = np.empty((len(raw), 2, 4096), dtype=np.float32)
    for index in range(len(raw)):
        x, gate = preprocess_iq(raw[index])
        if not gate["ok"]:
            raise ValueError("preprocessing rejected training frame: %r" % (gate,))
        frames[index] = x
    return frames


def recalls_from_logits(logits, targets):
    predicted = logits.argmax(axis=1)
    matrix = confusion_matrix(targets, predicted, 4)
    f1, recall, precision = macro_f1(matrix)
    return {
        "macro_f1": float(f1),
        "accuracy": float(np.mean(predicted == targets)),
        "recall": [float(value) for value in recall],
        "precision": [float(value) for value in precision],
        "predicted_counts": {name: int(np.sum(predicted == index))
                             for index, name in enumerate(CLASSES)},
        "confusion": [[int(value) for value in row] for row in matrix],
    }


def load_a_state():
    import torch
    checkpoint = torch.load(A_DIR / "model.pt", map_location="cpu", weights_only=False)
    net = build_network(4)
    net.load_state_dict(checkpoint["model_state_dict"])
    return net, checkpoint


def candidate_rules(model_path, model_sha):
    rules = json.loads((A_DIR / "rules.json").read_text(encoding="utf-8"))
    rules["frozen_round1"]["cnn"] = {
        "path": str(Path(model_path).relative_to(ROOT)).replace("\\", "/"),
        "sha256": model_sha,
    }
    rules["candidate"] = {
        "experiment": "candidate_a_bpsk_repair",
        "candidate": "R1",
        "initialized_from": "candidate_A/model.pt",
        "initialized_from_sha256": sha256_file(A_DIR / "model.pt"),
        "train_only_change": (
            "fine-tune A on existing TRAIN with BPSK/QPSK oversampling; "
            "no new data, no test/task80"
        ),
        "thresholds_recalibrated": False,
        "decision_thresholds": "copied unchanged from frozen round-2 rule config",
        "frozen_rule_source": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "frozen_rule_source_sha256": sha256_file(FROZEN_RULES),
    }
    path = CANDIDATE_DIR / "rules.json"
    jwrite(path, rules)
    return path


def main():
    import torch
    from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(False)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(DATASET)
    train = load_split(DATASET, manifest, "train", labels=list(CLASSES))
    val = load_split(DATASET, manifest, "val", labels=list(CLASSES))
    targets_train = np.asarray([CLASSES.index(str(label)) for label in train["label"]],
                               dtype=np.int64)
    targets_val = np.asarray([CLASSES.index(str(label)) for label in val["label"]],
                             dtype=np.int64)
    raw_train = np.asarray(train["X"], dtype=np.int16)
    conj_train = raw_train.copy()
    conj_train[..., 1] = -conj_train[..., 1]
    aug_raw = np.concatenate([raw_train, conj_train], axis=0)
    aug_targets = np.concatenate([targets_train, targets_train], axis=0)
    frames_train = preprocess_stack(aug_raw)
    frames_val = preprocess_stack(np.asarray(val["X"], dtype=np.int16))

    a_net, a_checkpoint = load_a_state()
    a_net.eval()
    with torch.no_grad():
        a_val_logits = _predict_logits(a_net, frames_val)
    a_val = recalls_from_logits(a_val_logits, targets_val)
    a_guard = {
        "qpsk_min": a_val["recall"][1] - 0.01,
        "qam16_min": a_val["recall"][2] - 0.02,
        "qam64_min": a_val["recall"][3] - 0.02,
    }

    # One candidate only: A weights, low-LR BPSK/QPSK-boundary fine-tune.
    net = build_network(4)
    net.load_state_dict(a_checkpoint["model_state_dict"])
    optimiser = torch.optim.AdamW(net.parameters(), lr=1e-4, weight_decay=1e-4)
    criterion = torch.nn.CrossEntropyLoss()
    sample_weights = np.asarray([2.0 if label in ("BPSK", "QPSK") else 1.0
                                 for label in aug_targets], dtype=np.float64)
    sampler = WeightedRandomSampler(torch.from_numpy(sample_weights),
                                    num_samples=len(aug_targets), replacement=True,
                                    generator=torch.Generator().manual_seed(SEED + 1))
    loader = DataLoader(TensorDataset(torch.from_numpy(frames_train).float(),
                                      torch.from_numpy(aug_targets)), batch_size=64,
                        sampler=sampler, num_workers=0)
    val_tensor = torch.from_numpy(frames_val).float()
    max_epochs = 20
    patience = 6
    best = None
    history = []
    start = time.perf_counter()
    for epoch in range(1, max_epochs + 1):
        net.train()
        loss_sum = 0.0
        for inputs, labels in loader:
            optimiser.zero_grad()
            these = net(inputs)
            loss = criterion(these, labels)
            loss.backward()
            optimiser.step()
            loss_sum += float(loss.detach()) * inputs.size(0)
        net.eval()
        with torch.no_grad():
            val_logits = _predict_logits(net, val_tensor.numpy())
        metrics = recalls_from_logits(val_logits, targets_val)
        recall = metrics["recall"]
        eligible = (recall[1] >= a_guard["qpsk_min"] and
                    recall[2] >= a_guard["qam16_min"] and
                    recall[3] >= a_guard["qam64_min"])
        score = recall[0] + 0.5 * recall[1]
        row = {
            "epoch": epoch,
            "train_loss": loss_sum / len(frames_train),
            "val_macro_f1": metrics["macro_f1"],
            "val_accuracy": metrics["accuracy"],
            "val_recall": dict(zip(CLASSES, recall)),
            "selection_score": score,
            "guard_eligible": eligible,
            "guard": a_guard,
        }
        history.append(row)
        if eligible and (best is None or score > best["score"] + 1e-12):
            best = {
                "score": score,
                "epoch": epoch,
                "state": {key: value.detach().cpu().clone()
                          for key, value in net.state_dict().items()},
                "metrics": metrics,
            }
            torch.save({
                "candidate": "R1",
                "epoch": epoch,
                "model_state_dict": best["state"],
                "val_metrics": metrics,
                "selection_score": score,
                "seed": SEED,
                "a_guard": a_guard,
            }, CANDIDATE_DIR / "checkpoint_best.pt")
        print(json.dumps({"epoch": epoch, "val_macro_f1": metrics["macro_f1"],
                          "val_recall": row["val_recall"],
                          "score": score, "eligible": eligible,
                          "elapsed_seconds": time.perf_counter() - start}),
              flush=True)
        if best is not None and epoch - best["epoch"] >= patience:
            break
    elapsed = time.perf_counter() - start
    jwrite(CANDIDATE_DIR / "curves.json", history)
    jwrite(CANDIDATE_DIR / "val_selection.json", {
        "candidate": "R1",
        "seed": SEED,
        "selection_rule": (
            "eligible only when QPSK >= A_QPSK-0.01 and QAM16/QAM64 >= A-0.02; "
            "maximize BPSK recall + 0.5*QPSK recall"
        ),
        "A_val_raw": a_val,
        "guard": a_guard,
        "history": history,
        "selected_epoch": None if best is None else best["epoch"],
        "selected_score": None if best is None else best["score"],
        "selected_val_metrics": None if best is None else best["metrics"],
    })
    if best is None:
        status_doc = {
            "schema": "ml-candidate-a-bpsk-repair-v1",
            "candidate": "R1",
            "status": "NEGATIVE_NO_ELIGIBLE_CHECKPOINT",
            "seed": SEED,
            "reason": "no epoch met the pre-registered VAL guard",
            "train_seconds": elapsed,
            "A_val_raw": a_val,
            "guard": a_guard,
            "history": history,
        }
        jwrite(CANDIDATE_DIR / "training_result.json", status_doc)
        print(json.dumps(status_doc, indent=1))
        return

    net.load_state_dict(best["state"])
    frozen = json.loads(FROZEN_RULES.read_text(encoding="utf-8"))
    thresholds = dict(frozen["frozen_round1"]["gate"])
    params = parameter_count(net)
    macs = macs_for_length(4096, 4)
    training_stub = {
        "params": params,
        "macs": macs,
        "training": {
            "dataset": str(DATASET.relative_to(ROOT)).replace("\\", "/"),
            "manifest_sha256": sha256_file(DATASET / "manifest.json"),
            "seed": SEED,
            "candidate": "R1",
            "initialized_from": "candidate_A/model.pt",
            "initialized_from_sha256": sha256_file(A_DIR / "model.pt"),
            "change": "BPSK/QPSK oversampling fine-tune; train only",
            "best_epoch": best["epoch"],
            "val_macro_f1": best["metrics"]["macro_f1"],
            "selection_score": best["score"],
            "selection_rule": "VAL guard + BPSK recall + 0.5*QPSK recall",
        },
    }
    model_sha, model_size = export_cnn_model(CANDIDATE_DIR, net, thresholds,
                                              training_stub)
    torch.save({"model_state_dict": best["state"],
                "classes": list(CLASSES),
                "preprocessing": "dc-complex-rms-v1",
                "thresholds": thresholds,
                "seed": SEED,
                "candidate": "R1",
                "initialized_from": "candidate_A/model.pt",
                "selection_epoch": best["epoch"]},
               CANDIDATE_DIR / "model.pt")
    rule_path = candidate_rules(CANDIDATE_DIR / "model.json", model_sha)
    config_doc = {
        "schema": "ml-candidate-a-bpsk-repair-v1",
        "candidate": "R1",
        "status": "COMPLETED",
        "frozen_A_dir": str(A_DIR.relative_to(ROOT)).replace("\\", "/"),
        "frozen_A_model_sha256": sha256_file(A_DIR / "model.json"),
        "frozen_A_rules_sha256": sha256_file(A_DIR / "rules.json"),
        "train_data": "ml_training/datasets/sim-v1 train only",
        "val_data": "ml_training/datasets/sim-v1 val only; checkpoint selection",
        "test_used_for_training_or_selection": False,
        "task80_used_for_training_or_selection": False,
        "new_data_used": False,
        "model_architecture": "tiny-iq-cnn-v1",
        "preprocessing": "dc-complex-rms-v1",
        "classes": list(CLASSES),
        "seed": SEED,
        "settings": {
            "max_epochs": max_epochs, "patience": patience, "batch_size": 64,
            "lr": 1e-4, "weight_decay": 1e-4, "threads": 4,
            "init": "candidate_A/model.pt",
            "sampler_weights": {"BPSK": 2.0, "QPSK": 2.0, "QAM16": 1.0, "QAM64": 1.0},
            "train_frames_before_repeat": len(raw_train),
            "train_frames": len(aug_raw),
            "val_frames": len(frames_val),
        },
        "train_only_change": [
            "initialize from A best checkpoint",
            "A's existing train-only conjugate copy",
            "BPSK/QPSK sample weight 2 vs QAM weight 1",
            "low-LR fine-tune",
        ],
        "thresholds": thresholds,
        "thresholds_recalibrated": False,
        "arbitration_changed": False,
        "gate_changed": False,
        "preprocessing_changed": False,
        "selection": {
            "rule": "VAL guard then maximize BPSK recall + 0.5*QPSK recall",
            "A_val_raw": a_val,
            "guard": a_guard,
            "selected_epoch": best["epoch"],
            "selected_score": best["score"],
            "selected_val_metrics": best["metrics"],
        },
        "best_epoch": best["epoch"],
        "best_val_macro_f1": best["metrics"]["macro_f1"],
        "train_seconds": elapsed,
        "params": params,
        "macs": macs,
        "artefacts": {
            "model_json_sha256": model_sha,
            "model_pt_sha256": sha256_file(CANDIDATE_DIR / "model.pt"),
            "rules_sha256": sha256_file(rule_path),
            "checkpoint_best_sha256": sha256_file(CANDIDATE_DIR / "checkpoint_best.pt"),
            "curves_sha256": sha256_file(CANDIDATE_DIR / "curves.json"),
            "val_selection_sha256": sha256_file(CANDIDATE_DIR / "val_selection.json"),
        },
        "note": "one repair candidate only; not deployed; A bytes untouched",
    }
    jwrite(CANDIDATE_DIR / "training_result.json", config_doc)
    print(json.dumps({
        "status": "COMPLETED", "candidate": "R1", "best_epoch": best["epoch"],
        "val_macro_f1": best["metrics"]["macro_f1"],
        "val_recall": dict(zip(CLASSES, best["metrics"]["recall"])),
        "model_sha256": model_sha, "rules_sha256": sha256_file(rule_path),
        "dir": str(CANDIDATE_DIR),
    }, indent=1))


if __name__ == "__main__":
    main()
