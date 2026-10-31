"""Train the 4-class feature baseline and TinyIQCNN; val-only checkpoint and gate selection.

Design rules (see ``ml_training/docs/CONTRACTS.md``):
* the independent test split is never read here;
* the best CNN checkpoint is selected on validation macro-F1 only;
* gate thresholds are selected on validation + ``gate_cal`` populations only;
* every artefact (``model.json``/``model.pt``/``metadata.json``/``curves.json``/
  ``thresholds.json``) records the exact config, manifest and RNG that produced it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common import config as config_mod  # noqa: E402
from ml_training.common.dataset import load_manifest, load_split, sha256_file, verify_hashes  # noqa: E402
from ml_training.common.gate import energy_floor_from_off, softmax  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model import baseline as baseline_mod  # noqa: E402
from ml_training.model.cnn import (build_network, macs_for_length, parameter_count,  # noqa: E402
                                   state_to_weights, weights_to_document)
from ml_training.model.features import FEATURE_SET, extract_batch  # noqa: E402
from ml_training.model.numpy_runtime import (CLASSES, NETWORK, PREPROCESSING, SCHEMA_VERSION,  # noqa: E402
                                             VARIANT, load_model)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path):
    return sha256_file(path)


def _json_write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _softmax_rows(logits):
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def preprocess_frames(iq_frames):
    """Stack of int16 (N,4096,2) -> float32 (N,2,4096) plus raw complex RMS array."""
    frames = np.empty((len(iq_frames), 2, 4096), dtype=np.float32)
    rms = np.empty((len(iq_frames),), dtype=np.float64)
    rejected = []
    for index in range(len(iq_frames)):
        x, gate = preprocess_iq(iq_frames[index])
        if not gate["ok"]:
            rejected.append({"index": index, "reason": gate["reason"]})
            rms[index] = 0.0
            continue
        frames[index] = x
        rms[index] = gate["rms"]
    if rejected:
        raise ValueError("frames rejected by pre-processing: %r" % (rejected[:5],))
    return frames, rms


def confusion_matrix(truth, predicted, num_classes):
    matrix = np.zeros((num_classes, num_classes), dtype=np.int64)
    for expected, actual in zip(truth, predicted):
        matrix[int(expected), int(actual)] += 1
    return matrix


def macro_f1(matrix):
    """Per-class precision/recall/F1 and their unweighted mean."""
    matrix = np.asarray(matrix, dtype=np.float64)
    recall, precision, f1 = [], [], []
    for index in range(matrix.shape[0]):
        row = matrix[index].sum()
        column = matrix[:, index].sum()
        recall.append(float(matrix[index, index] / row) if row else 0.0)
        precision.append(float(matrix[index, index] / column) if column else 0.0)
    for r, p in zip(recall, precision):
        f1.append(2.0 * p * r / (p + r) if (p + r) else 0.0)
    return float(np.mean(f1)), recall, precision


def train_baseline(frames_train, targets_train, frames_val, targets_val, settings, seed,
                   min_complex_rms):
    """Feature baseline with L2 chosen on validation accuracy only."""
    features_train = extract_batch(frames_train)
    features_val = extract_batch(frames_val)
    best = {"l2": None, "val_accuracy": -1.0, "parameters": None}
    started = time.perf_counter()
    for l2 in settings["l2_grid"]:
        parameters = baseline_mod.train_logistic(
            features_train, targets_train, len(CLASSES), l2=float(l2),
            epochs=int(settings["epochs"]), lr=float(settings["lr"]), seed=seed)
        probabilities = baseline_mod._predict_matrix(parameters, features_val)
        accuracy = float(np.mean(probabilities.argmax(axis=1) == targets_val))
        if accuracy > best["val_accuracy"] + 1e-12:
            best = {"l2": float(l2), "val_accuracy": accuracy, "parameters": parameters}
    elapsed = time.perf_counter() - started
    parameters = best["parameters"]
    thresholds = {"max_softmax": 0.0, "margin": 0.0, "min_complex_rms": float(min_complex_rms)}
    training = {
        "l2_grid": [float(value) for value in settings["l2_grid"]],
        "l2": best["l2"], "epochs": int(settings["epochs"]), "lr": float(settings["lr"]),
        "seed": int(seed), "val_accuracy": best["val_accuracy"],
        "train_frames": int(len(features_train)), "val_frames": int(len(features_val)),
        "elapsed_seconds": elapsed,
        "selection": "max val accuracy over l2 grid; argmax decision (no confidence gate)",
    }
    return parameters, training, features_train, features_val


def _predict_logits(net, frames, batch_size=256):
    import torch

    net.eval()
    outputs = []
    with torch.no_grad():
        for start in range(0, len(frames), batch_size):
            batch = torch.from_numpy(frames[start:start + batch_size])
            outputs.append(net(batch).numpy())
    return np.concatenate(outputs, axis=0)


def train_cnn(frames_train, targets_train, frames_val, targets_val, settings, seed):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    threads = int(settings.get("threads", 1))
    torch.set_num_threads(max(1, threads))
    net = build_network(len(CLASSES))
    optimiser = torch.optim.AdamW(net.parameters(), lr=float(settings["lr"]),
                                  weight_decay=float(settings["weight_decay"]))
    criterion = torch.nn.CrossEntropyLoss()
    dataset = TensorDataset(torch.from_numpy(frames_train).float(),
                            torch.from_numpy(targets_train.astype(np.int64)))
    loader = DataLoader(dataset, batch_size=int(settings["batch_size"]), shuffle=True,
                        num_workers=0,
                        generator=torch.Generator().manual_seed(int(seed) + 1))
    frames_val_t = torch.from_numpy(frames_val).float()
    history = []
    best = {"val_macro_f1": -1.0, "epoch": 0, "state": None, "val_accuracy": 0.0}
    started = time.perf_counter()
    epoch = 0
    for epoch in range(1, int(settings["max_epochs"]) + 1):
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
        matrix = confusion_matrix(targets_val, val_predicted, len(CLASSES))
        f1, recall, precision = macro_f1(matrix)
        accuracy = float(np.mean(val_predicted == targets_val))
        history.append({"epoch": epoch, "train_loss": loss_sum / max(total, 1),
                        "train_accuracy": correct / max(total, 1),
                        "val_accuracy": accuracy, "val_macro_f1": f1,
                        "val_recall": recall, "val_precision": precision,
                        "elapsed_seconds": time.perf_counter() - started})
        if f1 > best["val_macro_f1"] + 1e-12:
            best = {"val_macro_f1": f1, "epoch": epoch, "val_accuracy": accuracy,
                    "state": {key: value.detach().cpu().clone()
                              for key, value in net.state_dict().items()}}
        if epoch - best["epoch"] >= int(settings["patience"]):
            break
    elapsed = time.perf_counter() - started
    if best["state"] is None:
        raise RuntimeError("training produced no checkpoint")
    net.load_state_dict(best["state"])
    val_probabilities = _softmax_rows(_predict_logits(net, frames_val_t.numpy()))
    return {"net": net, "best": best, "history": history,
            "train_seconds": elapsed, "epochs_run": epoch,
            "val_probabilities": val_probabilities}


def _preprocess_gate_populations(dataset_dir, manifest):
    """Pre-process gate_cal frames once and keep per-kind (scores input, gate) records."""
    frames = load_split(dataset_dir, manifest, "gate_cal",
                        kinds=list(config_mod.GATE_KINDS), include_clipped=True)
    populations = {kind: {"frames": [], "gate": [], "rms": []}
                   for kind in config_mod.GATE_KINDS}
    for index in range(len(frames["X"])):
        kind = str(frames["label"][index])
        x, gate = preprocess_iq(frames["X"][index])
        populations[kind]["frames"].append(x)
        populations[kind]["gate"].append(gate)
        populations[kind]["rms"].append(gate["rms"])
    return populations


def _compute_gate_scores(net, gate_populations, energy_floor):
    import torch

    net.eval()
    gate_scores = {}
    for kind in config_mod.GATE_KINDS:
        scores, pre_rejected = [], []
        valid_frames, valid_positions = [], []
        for position, (x, gate, rms) in enumerate(zip(gate_populations[kind]["frames"],
                                                      gate_populations[kind]["gate"],
                                                      gate_populations[kind]["rms"])):
            if not gate["ok"] or rms < energy_floor:
                scores.append(None)
                pre_rejected.append(True)
            else:
                scores.append(None)
                pre_rejected.append(False)
                valid_frames.append(x)
                valid_positions.append(position)
        if valid_frames:
            with torch.no_grad():
                logits = net(torch.from_numpy(np.stack(valid_frames)).float()).numpy()
            probabilities = _softmax_rows(logits)
            for offset, position in enumerate(valid_positions):
                scores[position] = probabilities[offset]
        gate_scores[kind] = {"scores": scores, "pre_rejected": pre_rejected}
    return gate_scores


def select_thresholds_from_scores(val_probabilities, val_targets, gate_scores,
                                  gate_settings, energy_floor):
    from ml_training.common.gate import apply_gate

    def rejection_rate(kind, max_softmax, margin):
        records = gate_scores[kind]
        rejected = 0
        for score, pre_rejected in zip(records["scores"], records["pre_rejected"]):
            if pre_rejected:
                rejected += 1
                continue
            decision = apply_gate(score, CLASSES, {"max_softmax": max_softmax,
                                                   "margin": margin})
            rejected += int(decision["label"] == "UNKNOWN")
        return rejected / max(len(records["scores"]), 1)

    best = None
    for max_softmax in gate_settings["max_softmax_grid"]:
        for margin in gate_settings["margin_grid"]:
            correct = 0
            thresholds = {"max_softmax": float(max_softmax), "margin": float(margin)}
            for index in range(len(val_targets)):
                decision = apply_gate(val_probabilities[index], CLASSES, thresholds)
                if decision["label"] == CLASSES[int(val_targets[index])]:
                    correct += 1
            known_accuracy = correct / max(len(val_targets), 1)
            clip_rejection = rejection_rate("CLIP", max_softmax, margin)
            off_rejection = rejection_rate("OFF", max_softmax, margin)
            noise_rejection = rejection_rate("NOISE", max_softmax, margin)
            tone_rejection = rejection_rate("TONE", max_softmax, margin)
            mean_non_clip = float(np.mean([off_rejection, noise_rejection, tone_rejection]))
            score = (known_accuracy + mean_non_clip + clip_rejection) / 3.0
            candidate = {"max_softmax": float(max_softmax), "margin": float(margin),
                         "score": score, "known_val_accuracy": known_accuracy,
                         "clip_rejection": clip_rejection, "off_rejection": off_rejection,
                         "noise_rejection": noise_rejection, "tone_rejection": tone_rejection}
            if best is None or score > best["score"] + 1e-12:
                best = candidate
    best["energy_floor"] = float(energy_floor)
    return best


def export_cnn_model(out_dir, net, thresholds, metadata_stub):
    weights = state_to_weights(net.state_dict())
    document = {
        "schema_version": SCHEMA_VERSION,
        "network": NETWORK,
        "variant": VARIANT,
        "preprocessing": PREPROCESSING,
        "classes": list(CLASSES),
        "input_shape": [2, 4096],
        "supported_domain": {"input_fs_hz": 60000000, "decim": [1],
                             "symbol_rates_hz": [3000000], "shaping": ["RECT"]},
        "thresholds": {key: float(value) for key, value in thresholds.items()},
        "weights": weights_to_document(weights),
        "params": int(metadata_stub["params"]),
        "macs": int(metadata_stub["macs"]),
        "training": dict(metadata_stub["training"]),
        "status": "experimental",
        "board_compat": ("4-class offline variant: a stock board runtime enforces a fixed "
                         "6-class contract, so board deployment needs a contract extension; "
                         "not done in this phase"),
    }
    return _json_write(Path(out_dir) / "model.json", document)


def measure_inference(net, numpy_model, frames, warmup=20, repeats=200):
    import torch

    repeats = min(repeats, len(frames))
    for index in range(min(warmup, len(frames))):
        numpy_model.forward(frames[index])
    timings_numpy = []
    for index in range(repeats):
        started = time.perf_counter()
        numpy_model.forward(frames[index % len(frames)])
        timings_numpy.append((time.perf_counter() - started) * 1000.0)
    net.eval()
    with torch.no_grad():
        for index in range(min(warmup, len(frames))):
            net(torch.from_numpy(frames[index][None]).float())
        timings_torch = []
        for index in range(repeats):
            started = time.perf_counter()
            net(torch.from_numpy(frames[index % len(frames)][None]).float())
            timings_torch.append((time.perf_counter() - started) * 1000.0)

    def summary(values):
        values = np.asarray(values, dtype=np.float64)
        return {"median_ms": float(np.median(values)),
                "mean_ms": float(values.mean()),
                "p95_ms": float(np.percentile(values, 95)),
                "repeats": int(values.size)}

    return {"numpy_per_frame": summary(timings_numpy),
            "torch_per_frame": summary(timings_torch),
            "warmup": int(min(warmup, len(frames))), "host_only": True}


def run_training(config_path=None, dataset_dir=None, out_dir=None, variant="formal",
                 seed_override=None, max_epochs_override=None):
    config, config_sha = config_mod.load_config(config_path)
    settings = config_mod.model_settings(config, variant)
    baseline_settings = config_mod.baseline_settings(config, variant)
    gate_settings = dict(config["gate"])
    if seed_override is not None:
        settings["seed"] = int(seed_override)
    if max_epochs_override is not None:
        settings["max_epochs"] = int(max_epochs_override)
    seed = int(settings["seed"])
    out_dir = Path(out_dir) if out_dir else PACKAGE_ROOT / "artifacts"
    dataset_dir = Path(dataset_dir) if dataset_dir else config_mod.dataset_dir(config, variant)
    manifest = load_manifest(dataset_dir)
    if manifest.get("config_sha256") != config_sha:
        raise ValueError("dataset config_sha256 does not match the supplied config file")
    integrity = verify_hashes(dataset_dir, manifest)
    if not integrity["ok"]:
        raise ValueError("dataset npz hash mismatch: %r" % (integrity["mismatches"][:3],))
    manifest_sha = _sha(dataset_dir / "manifest.json")
    if manifest.get("classes") != list(CLASSES):
        raise ValueError("dataset classes do not match model classes")

    started = time.perf_counter()
    train_data = load_split(dataset_dir, manifest, "train", labels=list(CLASSES))
    val_data = load_split(dataset_dir, manifest, "val", labels=list(CLASSES))
    targets_train = np.asarray([CLASSES.index(str(label)) for label in train_data["label"]],
                               dtype=np.int64)
    targets_val = np.asarray([CLASSES.index(str(label)) for label in val_data["label"]],
                             dtype=np.int64)
    frames_train, rms_train = preprocess_frames(train_data["X"])
    frames_val, rms_val = preprocess_frames(val_data["X"])
    gate_populations = _preprocess_gate_populations(dataset_dir, manifest)
    off_rms = [rms for kind in ("OFF",) for rms in gate_populations[kind]["rms"]
               if rms > 0.0]
    energy_floor = energy_floor_from_off(
        off_rms, quantile=float(gate_settings["energy_floor_quantile_on_off_cal"]),
        headroom_db=3.0)

    baseline_parameters, baseline_training, features_train, features_val = train_baseline(
        frames_train, targets_train, frames_val, targets_val, baseline_settings, seed,
        energy_floor)
    baseline_dir = out_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_sha = baseline_mod.save_baseline(
        baseline_dir / "model.json", baseline_parameters, CLASSES,
        {"max_softmax": 0.0, "margin": 0.0, "min_complex_rms": float(energy_floor)},
        baseline_training)
    baseline_size = (baseline_dir / "model.json").stat().st_size

    cnn = train_cnn(frames_train, targets_train, frames_val, targets_val, settings, seed)
    net = cnn["net"]
    gate_scores = _compute_gate_scores(net, gate_populations, energy_floor)
    selection = select_thresholds_from_scores(cnn["val_probabilities"], targets_val,
                                              gate_scores, gate_settings, energy_floor)
    thresholds = {"max_softmax": selection["max_softmax"], "margin": selection["margin"],
                  "min_complex_rms": float(energy_floor)}

    cnn_dir = out_dir / "cnn"
    cnn_dir.mkdir(parents=True, exist_ok=True)
    import torch

    params = parameter_count(net)
    macs = macs_for_length(4096, len(CLASSES))
    training_stub = {
        "params": params, "macs": macs,
        "training": {"manifest_sha256": manifest_sha, "config_sha256": config_sha,
                     "best_epoch": int(cnn["best"]["epoch"]),
                     "val_macro_f1": float(cnn["best"]["val_macro_f1"]),
                     "seed": seed},
    }
    model_sha, model_size = export_cnn_model(cnn_dir, net, thresholds, training_stub)
    torch.save({"model_state_dict": cnn["best"]["state"],
                "classes": list(CLASSES), "preprocessing": PREPROCESSING,
                "thresholds": thresholds, "seed": seed,
                "config_sha256": config_sha, "manifest_sha256": manifest_sha},
               cnn_dir / "model.pt")
    checkpoint_sha = _sha(cnn_dir / "model.pt")
    checkpoint_size = (cnn_dir / "model.pt").stat().st_size

    # torch <-> pure numpy parity before trusting the exported model.
    numpy_model = load_model(cnn_dir / "model.json")
    parity_frames = frames_val[:min(200, len(frames_val))]
    torch_logits = _predict_logits(net, torch.from_numpy(parity_frames).float().numpy())
    numpy_logits = np.stack([numpy_model.forward(frame) for frame in parity_frames], axis=0)
    parity_max = float(np.max(np.abs(torch_logits - numpy_logits)))
    parity_argmax = float(np.mean(torch_logits.argmax(axis=1) == numpy_logits.argmax(axis=1)))
    if parity_max > 1e-3:
        raise ValueError("torch/numpy logit parity too large: %.6g" % (parity_max,))

    host_info = {
        "platform": platform.platform(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", ""),
        "cpu_count": os.cpu_count(),
        "torch_threads": int(torch.get_num_threads()),
    }
    inference_timing = measure_inference(net, numpy_model, frames_val)
    total_seconds = time.perf_counter() - started

    curves_sha, curves_size = _json_write(cnn_dir / "curves.json", cnn["history"])
    _json_write(cnn_dir / "thresholds.json", {
        "selected": thresholds,
        "selection": selection,
        "energy_floor_db_note": "min_complex_rms is a raw-code complex RMS floor",
        "calibration_populations": "val known frames + gate_cal OFF/NOISE/TONE/CLIP only",
    })
    baseline_meta_sha, baseline_meta_size = _json_write(baseline_dir / "metadata.json", {
        "kind": "feature-baseline", "feature_set": FEATURE_SET,
        "classes": list(CLASSES), "params": int(
            np.asarray(baseline_parameters["W"]).size + np.asarray(baseline_parameters["b"]).size),
        "training": baseline_training,
        "thresholds": {"max_softmax": 0.0, "margin": 0.0, "min_complex_rms": float(energy_floor)},
        "model_json_sha256": baseline_sha, "model_json_bytes": baseline_size,
        "config_path": str(config_path or config_mod.DEFAULT_CONFIG),
        "config_sha256": config_sha, "dataset": str(dataset_dir),
        "manifest_sha256": manifest_sha, "created_utc": _utc_now(),
        "real_data_used": False, "test_split_used_for_tuning": False,
        "not_run": ["ARM/board feature-baseline latency", "real multi-class accuracy"],
    })
    metadata = {
        "model": "tiny_iq_cnn", "variant": VARIANT, "class_order": list(CLASSES),
        "preprocessing": PREPROCESSING, "input_shape": [2, 4096],
        "params": int(params), "macs": int(macs),
        "trainable_parameters": int(params),
        "dataset": str(dataset_dir), "manifest_sha256": manifest_sha,
        "config_path": str(config_path or config_mod.DEFAULT_CONFIG),
        "config_sha256": config_sha, "variant_name": variant,
        "splits_used": ["train", "val", "gate_cal"],
        "test_split_used_for_tuning": False, "real_data_used": False,
        "train_frames": int(len(frames_train)), "val_frames": int(len(frames_val)),
        "dataset_integrity": integrity,
        "rng": {"seed": seed, "python_random_seed": seed, "numpy_seed": seed,
                "torch_seed": seed, "dataloader_seed": seed + 1,
                "num_workers": 0, "threads": int(settings.get("threads", 1))},
        "cnn": {"best_epoch": int(cnn["best"]["epoch"]),
                "best_val_macro_f1": float(cnn["best"]["val_macro_f1"]),
                "best_val_accuracy": float(cnn["best"]["val_accuracy"]),
                "epochs_run": int(cnn["epochs_run"]),
                "max_epochs": int(settings["max_epochs"]),
                "patience": int(settings["patience"]),
                "batch_size": int(settings["batch_size"]),
                "lr": float(settings["lr"]), "weight_decay": float(settings["weight_decay"]),
                "train_seconds": float(cnn["train_seconds"]),
                "total_pipeline_seconds": float(total_seconds)},
        "baseline": {"l2": baseline_training["l2"],
                     "val_accuracy": baseline_training["val_accuracy"],
                     "feature_set": FEATURE_SET,
                     "model_json_sha256": baseline_sha,
                     "model_json_bytes": baseline_size,
                     "metadata_json_sha256": baseline_meta_sha},
        "thresholds": thresholds, "threshold_selection": selection,
        "energy_floor_complex_rms": float(energy_floor),
        "torch_numpy_parity": {"max_abs_logit_diff": parity_max,
                               "argmax_agreement": parity_argmax,
                               "compared_frames": int(len(parity_frames))},
        "inference_timing_host": inference_timing,
        "host": host_info,
        "artefacts": {
            "model_json": {"path": str(cnn_dir / "model.json"), "sha256": model_sha,
                           "bytes": model_size},
            "model_pt": {"path": str(cnn_dir / "model.pt"), "sha256": checkpoint_sha,
                         "bytes": checkpoint_size},
            "curves_json": {"path": str(cnn_dir / "curves.json"), "sha256": curves_sha,
                            "bytes": curves_size},
        },
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__,
                     "torch": torch.__version__},
        "created_utc": _utc_now(),
        "not_run": ["ARM/board latency and parity", "board deployment",
                    "real multi-class accuracy", "hardware-specific impairments",
                    "RRC / other symbol rates", "large hyper-parameter search"],
        "note": ("host CPU numbers only; they are NOT ARM board numbers. The independent "
                 "test split is evaluated separately by ml_training/evaluation."),
    }
    metadata_sha, metadata_size = _json_write(cnn_dir / "metadata.json", metadata)
    print(json.dumps({"variant": variant, "train_frames": len(frames_train),
                      "val_frames": len(frames_val),
                      "params": int(params), "macs": int(macs),
                      "best_epoch": int(cnn["best"]["epoch"]),
                      "val_macro_f1": float(cnn["best"]["val_macro_f1"]),
                      "val_accuracy": float(cnn["best"]["val_accuracy"]),
                      "thresholds": thresholds,
                      "train_seconds": float(cnn["train_seconds"]),
                      "total_seconds": float(total_seconds),
                      "model_json_bytes": model_size,
                      "model_json_sha256": model_sha,
                      "metadata_json_sha256": metadata_sha,
                      "metadata_json_bytes": metadata_size}, indent=2))
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--variant", default="formal", choices=list(config_mod.VARIANTS))
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--max-epochs", type=int, default=None)
    args = parser.parse_args(argv)
    run_training(config_path=args.config, dataset_dir=args.dataset, out_dir=args.out,
                 variant=args.variant, seed_override=args.seed,
                 max_epochs_override=args.max_epochs)


if __name__ == "__main__":
    main()
