"""Re-check and continue the truncated round-1 40-epoch val-only CNN trial.

Background
----------
Round 1 first trained a 40-epoch budget trial; the best checkpoint was saved at epoch 39
(val macro-F1 0.7005, still improving) because the *initial* epoch budget was 40.  The
budget was then raised to 80 epochs and a fresh 80-epoch run became the round-1 deliverable
(val macro-F1 0.8545, best epoch 76).  The 40-epoch trial was left as a decision backup and
was never continued.

This script continues that trial: it warm-starts from the saved trial checkpoint
(`artifacts/trials/formal-40ep/cnn/model.pt`, best epoch 39) and trains 40 further epochs
under the same frozen configuration (same dataset, seed, AdamW hyper-parameters and
validation-selection rule).  The trial checkpoint stores model weights only (no optimiser or
loader state), so this is a warm start, not a bit-identical continuation of the original
process; that limitation is recorded in the output metadata.

Discipline:
* the independent round-1 test split is NOT read or evaluated here;
* real data is NOT used;
* existing round-1/round-2 artefacts are NOT modified; new outputs go to
  ``ml_training/round2/artifacts/training-resume/formal-40ep-continued/``.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common import config as config_mod  # noqa: E402
from ml_training.common.dataset import (load_manifest, load_split, sha256_file,  # noqa: E402
                                        verify_hashes)
from ml_training.common.gate import energy_floor_from_off  # noqa: E402
from ml_training.model.numpy_runtime import CLASSES, PREPROCESSING, load_model  # noqa: E402
from ml_training.training import train as train_mod  # noqa: E402

ML = ROOT / "ml_training"
CONFIG_PATH = ML / "config" / "first_round.json"
TRIAL_DIR = ML / "artifacts" / "trials" / "formal-40ep" / "cnn"
TRIAL_BEST_EPOCH = 39
TRIAL_EPOCHS_RUN = 40
OUT_DIR = ML / "round2" / "artifacts" / "training-resume" / "formal-40ep-continued"
EXTRA_EPOCHS = 40


def _sha(path):
    return sha256_file(path)


def _utc_now():
    return train_mod._utc_now()


def main():
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    config, config_sha = config_mod.load_config(CONFIG_PATH)
    settings = dict(config_mod.model_settings(config, "formal"))
    settings["max_epochs"] = EXTRA_EPOCHS
    seed = int(settings["seed"])
    dataset_dir = config_mod.dataset_dir(config, "formal")
    manifest = load_manifest(dataset_dir)
    if manifest.get("config_sha256") != config_sha:
        raise SystemExit("dataset config_sha256 does not match first_round.json")
    integrity = verify_hashes(dataset_dir, manifest)
    if not integrity["ok"]:
        raise SystemExit("dataset hash mismatch: %r" % (integrity["mismatches"][:3],))
    manifest_sha = _sha(dataset_dir / "manifest.json")

    print("[resume] loading train/val frames and trial checkpoint...", flush=True)
    train_data = load_split(dataset_dir, manifest, "train", labels=list(CLASSES))
    val_data = load_split(dataset_dir, manifest, "val", labels=list(CLASSES))
    targets_train = np.asarray([CLASSES.index(str(label)) for label in train_data["label"]],
                               dtype=np.int64)
    targets_val = np.asarray([CLASSES.index(str(label)) for label in val_data["label"]],
                             dtype=np.int64)
    frames_train, _ = train_mod.preprocess_frames(train_data["X"])
    frames_val, _ = train_mod.preprocess_frames(val_data["X"])

    trial_ckpt_path = TRIAL_DIR / "model.pt"
    trial_ckpt = torch.load(trial_ckpt_path, map_location="cpu", weights_only=False)
    for label, expected, actual in (
            ("classes", list(CLASSES), trial_ckpt.get("classes")),
            ("preprocessing", PREPROCESSING, trial_ckpt.get("preprocessing")),
            ("seed", seed, trial_ckpt.get("seed"))):
        if expected != actual:
            raise SystemExit("trial checkpoint %s mismatch: %r != %r"
                             % (label, expected, actual))
    # The trial was trained before the round-1 config/manifest re-freeze.  The 20 npz arrays
    # were verified byte-identical across that re-freeze (round-1 report §4); only the
    # max-epoch / gate-grid config fields and therefore the recorded binding hashes changed.
    binding = {
        "trial_config_sha256": trial_ckpt.get("config_sha256"),
        "current_config_sha256": config_sha,
        "trial_manifest_sha256": trial_ckpt.get("manifest_sha256"),
        "current_manifest_sha256": manifest_sha,
        "npz_arrays_unchanged_by_refreeze": True,
        "note": ("trial binding predates the round-1 config re-freeze (only max_epochs and the "
                 "gate grid changed; the 20 dataset npz files are byte-identical). The "
                 "continuation therefore trains on the current frozen dataset and records both "
                 "bindings."),
    }
    if binding["trial_config_sha256"] != config_sha:
        print("[resume] WARNING: trial config binding %s predates current %s (documented)"
              % (binding["trial_config_sha256"], config_sha))
    if trial_ckpt.get("manifest_sha256") != manifest_sha:
        print("[resume] WARNING: trial manifest binding %s predates current %s (documented)"
              % (trial_ckpt.get("manifest_sha256"), manifest_sha))
    trial_metadata = json.loads((TRIAL_DIR / "metadata.json").read_text(encoding="utf-8"))

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    threads = int(settings.get("threads", 1))
    torch.set_num_threads(max(1, threads))
    net = train_mod.build_network(len(CLASSES))
    net.load_state_dict(trial_ckpt["model_state_dict"], strict=True)
    optimiser = torch.optim.AdamW(net.parameters(), lr=float(settings["lr"]),
                                  weight_decay=float(settings["weight_decay"]))
    criterion = torch.nn.CrossEntropyLoss()
    dataset = TensorDataset(torch.from_numpy(frames_train).float(),
                            torch.from_numpy(targets_train.astype(np.int64)))
    loader = DataLoader(dataset, batch_size=int(settings["batch_size"]), shuffle=True,
                        num_workers=0,
                        generator=torch.Generator().manual_seed(seed + 1))
    frames_val_t = torch.from_numpy(frames_val).float()
    trial_total_seconds = float(trial_metadata["cnn"]["train_seconds"])
    history = []
    best = {"val_macro_f1": -1.0, "epoch": TRIAL_EPOCHS_RUN, "state": None,
            "val_accuracy": 0.0}
    started = time.perf_counter()
    epochs_run = TRIAL_EPOCHS_RUN
    for local_epoch in range(1, EXTRA_EPOCHS + 1):
        epoch = TRIAL_EPOCHS_RUN + local_epoch
        epochs_run = epoch
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
        val_logits = train_mod._predict_logits(net, frames_val_t.numpy())
        val_predicted = val_logits.argmax(axis=1)
        matrix = train_mod.confusion_matrix(targets_val, val_predicted, len(CLASSES))
        f1, recall, precision = train_mod.macro_f1(matrix)
        accuracy = float(np.mean(val_predicted == targets_val))
        history.append({"epoch": epoch,
                        "train_loss": loss_sum / max(total, 1),
                        "train_accuracy": correct / max(total, 1),
                        "val_accuracy": accuracy, "val_macro_f1": f1,
                        "val_recall": recall, "val_precision": precision,
                        "elapsed_seconds": trial_total_seconds
                        + (time.perf_counter() - started)})
        print("[resume] epoch %d/80 train_loss %.4f train_acc %.4f val_f1 %.4f"
              % (epoch, history[-1]["train_loss"], history[-1]["train_accuracy"], f1),
              flush=True)
        if f1 > best["val_macro_f1"] + 1e-12:
            best = {"val_macro_f1": f1, "epoch": epoch, "val_accuracy": accuracy,
                    "state": {key: value.detach().cpu().clone()
                              for key, value in net.state_dict().items()}}
        if epoch - best["epoch"] >= int(settings["patience"]):
            print("[resume] early stop at epoch %d (best %d)" % (epoch, best["epoch"]))
            break
    if best["state"] is None:
        raise SystemExit("continuation produced no checkpoint")
    net.load_state_dict(best["state"])
    continuation_seconds = time.perf_counter() - started
    print("[resume] best continued epoch %d, val macro-F1 %.6f"
          % (best["epoch"], best["val_macro_f1"]), flush=True)

    # ---- metrics for trial / continued / final model on the same validation split ----
    def evaluate_state(state_dict):
        probe = train_mod.build_network(len(CLASSES))
        probe.load_state_dict(state_dict, strict=True)
        logits = train_mod._predict_logits(probe, frames_val_t.numpy())
        predicted = logits.argmax(axis=1)
        matrix = train_mod.confusion_matrix(targets_val, predicted, len(CLASSES))
        f1, recall, precision = train_mod.macro_f1(matrix)
        return {"accuracy": float(np.mean(predicted == targets_val)), "macro_f1": f1,
                "recall": recall, "precision": precision,
                "per_class_f1": [float(value) for value in
                                 (2 * p * r / (p + r) if (p + r) else 0.0
                                  for p, r in zip(precision, recall))],
                "confusion": matrix.tolist()}

    trial_state = trial_ckpt["model_state_dict"]
    final_ckpt_path = ML / "artifacts" / "cnn" / "model.pt"
    final_state = torch.load(final_ckpt_path, map_location="cpu",
                             weights_only=False)["model_state_dict"]
    comparison = {
        "trial_40ep_best_epoch_%d" % TRIAL_BEST_EPOCH: evaluate_state(trial_state),
        "continued_best_epoch_%d" % best["epoch"]: evaluate_state(best["state"]),
        "round1_final_80ep_best_epoch_76": evaluate_state(final_state),
    }

    # ---- export continued model (same frozen gate-selection procedure) ----
    cnn_dir = OUT_DIR / "cnn"
    cnn_dir.mkdir(parents=True, exist_ok=True)
    gate_populations = train_mod._preprocess_gate_populations(dataset_dir, manifest)
    off_rms = [rms for rms in gate_populations["OFF"]["rms"] if rms > 0.0]
    energy_floor = energy_floor_from_off(
        off_rms, quantile=float(config["gate"]["energy_floor_quantile_on_off_cal"]),
        headroom_db=3.0)
    gate_scores = train_mod._compute_gate_scores(net, gate_populations, energy_floor)
    val_probabilities = train_mod._softmax_rows(
        train_mod._predict_logits(net, frames_val_t.numpy()))
    selection = train_mod.select_thresholds_from_scores(
        val_probabilities, targets_val, gate_scores, config["gate"], energy_floor)
    thresholds = {"max_softmax": float(selection["max_softmax"]),
                  "margin": float(selection["margin"]),
                  "min_complex_rms": float(energy_floor)}
    params = train_mod.parameter_count(net)
    macs = train_mod.macs_for_length(4096, len(CLASSES))
    training_stub = {
        "params": params, "macs": macs,
        "training": {"manifest_sha256": manifest_sha, "config_sha256": config_sha,
                     "best_epoch": int(best["epoch"]),
                     "val_macro_f1": float(best["val_macro_f1"]),
                     "seed": seed,
                     "warm_start_from": "formal-40ep trial best checkpoint (epoch 39)"},
    }
    model_sha, model_size = train_mod.export_cnn_model(cnn_dir, net, thresholds,
                                                       training_stub)
    trial_sha = _sha(trial_ckpt_path)
    torch.save({"model_state_dict": best["state"], "classes": list(CLASSES),
                "preprocessing": PREPROCESSING, "thresholds": thresholds, "seed": seed,
                "config_sha256": config_sha, "manifest_sha256": manifest_sha,
                "provenance": {
                    "warm_start_from": str(trial_ckpt_path.relative_to(ROOT)).replace("\\", "/"),
                    "warm_start_from_sha256": trial_sha,
                    "warm_start_from_best_epoch": TRIAL_BEST_EPOCH,
                    "warm_start_from_best_val_macro_f1":
                        float(trial_metadata["cnn"]["best_val_macro_f1"]),
                    "continued_epochs_run": int(EXTRA_EPOCHS),
                    "epochs_from_trial_best_to_continued_best":
                        int(best["epoch"] - TRIAL_BEST_EPOCH),
                    "note": ("trial checkpoint stores weights only; optimiser and data-loader "
                             "state were not saved, so this is a warm start, not a bit-identical "
                             "continuation"),
                }}, cnn_dir / "model.pt")
    checkpoint_sha = _sha(cnn_dir / "model.pt")

    # combined curve: trial epochs 1-40 + continued epochs 41-80
    trial_curves = json.loads((TRIAL_DIR / "curves.json").read_text(encoding="utf-8"))
    combined_curves = list(trial_curves) + history
    train_mod._json_write(cnn_dir / "curves.json", combined_curves)
    train_mod._json_write(cnn_dir / "thresholds.json", {
        "selected": thresholds, "selection": selection,
        "note": "gate thresholds re-selected from val known + gate_cal only, same frozen procedure",
    })

    parity_frames = frames_val[:min(50, len(frames_val))]
    numpy_model = load_model(cnn_dir / "model.json")
    torch_logits = train_mod._predict_logits(net, torch.from_numpy(parity_frames).float().numpy())
    numpy_logits = np.stack([numpy_model.forward(frame) for frame in parity_frames], axis=0)
    parity = {"max_abs_logit_diff": float(np.max(np.abs(torch_logits - numpy_logits))),
              "argmax_agreement": float(np.mean(torch_logits.argmax(axis=1)
                                                == numpy_logits.argmax(axis=1)))}

    metadata = {
        "kind": "warm-start-continuation-of-truncated-trial",
        "purpose": ("round-1 40-epoch budget trial was truncated at the initial epoch cap "
                    "(best epoch 39, val macro-F1 0.7005). This run continues it for 40 more "
                    "epochs under the same frozen configuration, as a val-only diagnostic; "
                    "the official round-1 deliverable remains the independently trained "
                    "80-epoch model."),
        "model": "tiny_iq_cnn", "variant": "modrec4", "class_order": list(CLASSES),
        "preprocessing": PREPROCESSING, "input_shape": [2, 4096],
        "params": int(params), "macs": int(macs),
        "dataset": str(dataset_dir), "manifest_sha256": manifest_sha,
        "config_path": str(CONFIG_PATH), "config_sha256": config_sha,
        "splits_used": ["train", "val", "gate_cal"],
        "test_split_used_for_tuning": False, "real_data_used": False,
        "test_evaluated": False,
        "train_frames": int(len(frames_train)), "val_frames": int(len(frames_val)),
        "warm_start": {
            "checkpoint": str(trial_ckpt_path.relative_to(ROOT)).replace("\\", "/"),
            "checkpoint_sha256": trial_sha,
            "trial_best_epoch": TRIAL_BEST_EPOCH,
            "trial_best_val_macro_f1": float(trial_metadata["cnn"]["best_val_macro_f1"]),
            "continued_epochs_run": int(EXTRA_EPOCHS),
            "epochs_from_trial_best_to_continued_best":
                int(best["epoch"] - TRIAL_BEST_EPOCH),
            "max_epochs": TRIAL_EPOCHS_RUN + EXTRA_EPOCHS,
            "optimiser_state_resumed": False,
            "loader_state_resumed": False,
            "note": ("weights-only checkpoint; a fresh AdamW optimiser and the loader "
                     "generator (seed+1) are used, so this is a warm start, not a "
                     "bit-identical resume"),
        },
        "continued_training": {
            "best_epoch_absolute": int(best["epoch"]),
            "best_val_macro_f1": float(best["val_macro_f1"]),
            "best_val_accuracy": float(best["val_accuracy"]),
            "epochs_run_total": int(epochs_run),
            "early_stopped": bool(epochs_run < TRIAL_EPOCHS_RUN + EXTRA_EPOCHS),
            "continuation_seconds": float(continuation_seconds),
            "trial_seconds": float(trial_total_seconds),
            "lr": float(settings["lr"]), "weight_decay": float(settings["weight_decay"]),
            "batch_size": int(settings["batch_size"]), "patience": int(settings["patience"]),
            "threads": int(threads), "seed": seed,
        },
        "comparison_on_val": comparison,
        "thresholds": thresholds, "threshold_selection": selection,
        "energy_floor_complex_rms": float(energy_floor),
        "binding": dict(binding, trial_best_epoch=TRIAL_BEST_EPOCH,
                        trial_best_val_macro_f1=float(
                            trial_metadata["cnn"]["best_val_macro_f1"]),
                        trial_epochs_run=int(trial_metadata["cnn"]["epochs_run"]),
                        trial_train_frames=int(trial_metadata["train_frames"]),
                        trial_val_frames=int(trial_metadata["val_frames"])),
        "torch_numpy_parity": parity,
        "artefacts": {
            "model_json": {"path": str((cnn_dir / "model.json").relative_to(ROOT)).replace("\\", "/"),
                           "sha256": model_sha, "bytes": int(model_size)},
            "model_pt": {"path": str((cnn_dir / "model.pt").relative_to(ROOT)).replace("\\", "/"),
                         "sha256": checkpoint_sha,
                         "bytes": int((cnn_dir / "model.pt").stat().st_size)},
        },
        "created_utc": _utc_now(),
        "not_run": ["ARM/board latency and parity", "board deployment",
                    "round-1 independent test evaluation (deliberately not re-evaluated)",
                    "real multi-class accuracy", "hardware-specific impairments"],
        "note": ("host CPU only; not ARM/board numbers. Val-only diagnostic; not a replacement "
                 "for the official round-1 80-epoch model."),
    }
    train_mod._json_write(cnn_dir / "metadata.json", metadata)
    print(json.dumps({
        "best_epoch": int(best["epoch"]),
        "best_val_macro_f1": float(best["val_macro_f1"]),
        "best_val_accuracy": float(best["val_accuracy"]),
        "early_stopped": metadata["continued_training"]["early_stopped"],
        "comparison": {key: {"accuracy": value["accuracy"],
                             "macro_f1": value["macro_f1"]}
                       for key, value in comparison.items()},
        "model_json_sha256": model_sha,
        "model_pt_sha256": checkpoint_sha,
        "out_dir": str(OUT_DIR),
    }, indent=1))


if __name__ == "__main__":
    main()
