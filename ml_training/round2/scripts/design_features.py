"""Round-2 feature/decision analysis for the two improvement candidates.

Uses only development replay data:
* round-1 validation split (already used for round-1 model selection) -> dev,
* round-1 gate_cal split (already used for round-1 gate thresholds) -> dev,
* the three real physical sessions (analysed in round 1) -> dev replay.
Round-1 test split and the new round-2 test set are NOT used here.

Computes:
* interpretable + spectral features per frame,
* CNN probabilities and feature-baseline probabilities,
* disagreement statistics for a feature-guard arbitration rule,
* tone / noise separability for a spectral quality gate.

Writes ``ml_training/round2/analysis/features_dev.npz`` (arrays) and
``ml_training/round2/analysis/design_summary.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.baseline import load_baseline  # noqa: E402
from ml_training.model.features import FEATURE_NAMES, extract_features  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402

ML = ROOT / "ml_training"
DATASET = ML / "datasets" / "sim-v1"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / \
    "campaign5-val-followup" / "download"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
FS = 60_000_000.0
BAND_HZ_3M = 3_000_000.0
OUT = ML / "round2" / "analysis"


def spectral_features(x):
    z = x[0].astype(np.float64) + 1j * x[1].astype(np.float64)
    power = np.abs(np.fft.fftshift(np.fft.fft(z))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(z), 1.0 / FS))
    total = power.sum() + 1e-30
    ordered = np.sort(power)[::-1]
    top1 = float(ordered[0] / total)
    top5 = float(ordered[:5].sum() / total)
    top9 = float(ordered[:9].sum() / total)
    band = float(power[np.abs(freqs) <= BAND_HZ_3M].sum() / total)
    median = float(np.median(power)) + 1e-30
    peak_to_median_db = float(10.0 * np.log10(ordered[0] / median))
    probabilities = power / total
    nonzero = probabilities[probabilities > 0]
    entropy = float(-(nonzero * np.log2(nonzero)).sum() / 12.0)  # /log2(4096)
    return np.asarray([top1, top5, top9, band, peak_to_median_db, entropy], dtype=np.float64)


SPECTRAL_NAMES = ("top1_frac", "top5_frac", "top9_frac", "band_frac_3m",
                  "peak_to_median_db", "spectral_entropy")


def cnn_probs(model, iq):
    x, gate = preprocess_iq(iq)
    if not gate["ok"] or gate["rms"] < model.thresholds["min_complex_rms"]:
        return None, None, gate
    logits = model.forward(x)
    exp = np.exp(logits - logits.max())
    return logits, exp / exp.sum(), gate


def baseline_probs(baseline, iq):
    x, gate = preprocess_iq(iq)
    if not gate["ok"]:
        return None
    return baseline.scores(extract_features(x))


def collect_sim(cnn, baseline):
    manifest = load_manifest(DATASET)
    records = {"split": [], "label": [], "snr_db": [], "features": [], "spectral": [],
               "cnn_probs": [], "base_probs": []}
    for split, labels, kind_filter in (
            ("val", list(CLASSES), None),
            ("gate_cal", None, list(GATE_KINDS))):
        data = load_split(DATASET, manifest, split, labels=labels, kinds=kind_filter,
                          include_clipped=True)
        for index in range(len(data["X"])):
            iq = data["X"][index]
            _, probs, gate = cnn_probs(cnn, iq)
            x, prep_gate = preprocess_iq(iq)
            if not prep_gate["ok"]:
                # clipping / no-energy: gate baseline rejection by pre-processing only
                records["features"].append(np.full(len(FEATURE_NAMES), np.nan))
                records["spectral"].append(np.full(len(SPECTRAL_NAMES), np.nan))
                records["cnn_probs"].append(np.full(4, np.nan))
                records["base_probs"].append(np.full(4, np.nan))
            else:
                records["features"].append(extract_features(x))
                records["spectral"].append(spectral_features(x))
                records["cnn_probs"].append(probs if probs is not None else np.full(4, np.nan))
                records["base_probs"].append(baseline_probs(baseline, iq))
            records["split"].append(split)
            records["label"].append(str(data["label"][index]))
            records["snr_db"].append(float(data["snr_db"][index]))
    for key in ("features", "spectral", "cnn_probs", "base_probs"):
        records[key] = np.asarray(records[key], dtype=np.float64)
        if records[key].size and records[key].ndim < 2:
            records[key] = records[key].reshape(len(records["split"]), -1)
    return records


def collect_real(cnn, baseline):
    rows = []
    for session in ("train-01", "train-02", "val-01"):
        for meta_path in sorted((EVIDENCE / session).glob("normal-*.iq")):
            iq = read_frame(meta_path)
            meta = json.loads((EVIDENCE / session / (meta_path.stem + ".json"))
                              .read_text(encoding="utf-8"))
            x, gate = preprocess_iq(iq)
            rows.append({
                "session": session,
                "frame": meta_path.stem,
                "truth": meta.get("trainval", {}).get("truth"),
                "features": extract_features(x).tolist(),
                "spectral": spectral_features(x).tolist(),
                "cnn_probs": cnn_probs(cnn, iq)[1].tolist(),
                "base_probs": baseline_probs(baseline, iq).tolist(),
            })
    return rows


def main():
    cnn = load_model(ML / "artifacts" / "cnn" / "model.json")
    baseline = load_baseline(ML / "artifacts" / "baseline" / "model.json")
    sim = collect_sim(cnn, baseline)
    real = collect_real(cnn, baseline)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT / "features_dev.npz",
        feature_names=np.asarray(FEATURE_NAMES), spectral_names=np.asarray(SPECTRAL_NAMES),
        split=np.asarray(sim["split"]), label=np.asarray(sim["label"]),
        snr_db=np.asarray(sim["snr_db"], dtype=np.float64),
        features=np.asarray(sim["features"], dtype=np.float64),
        spectral=np.asarray(sim["spectral"], dtype=np.float64),
        cnn_probs=np.asarray(sim["cnn_probs"], dtype=np.float64),
        base_probs=np.asarray(sim["base_probs"], dtype=np.float64),
    )
    (OUT / "real_dev_features.json").write_text(json.dumps(real, indent=1), encoding="utf-8")

    labels = np.asarray(sim["label"])
    is_known = np.isin(labels, CLASSES)
    known_index = np.asarray([CLASSES.index(label) for label in labels[is_known]])
    cnn_known = sim["cnn_probs"][is_known]
    base_known = sim["base_probs"][is_known]
    cnn_label = np.argmax(cnn_known, axis=1)
    base_label = np.argmax(base_known, axis=1)
    cnn_conf = cnn_known.max(axis=1)
    base_conf = base_known.max(axis=1)
    agreement = float(np.mean(cnn_label == base_label))
    summary = {
        "val_frames": int(is_known.sum()),
        "cnn_val_accuracy": float(np.mean(cnn_label == known_index)),
        "baseline_val_accuracy": float(np.mean(base_label == known_index)),
        "disagreement_rate": float(1.0 - agreement),
        "disagreement_accuracy": {
            "cnn_correct": float(np.mean((cnn_label != base_label)
                                         & (cnn_label == known_index)
                                         & (base_label != known_index))),
            "baseline_correct": float(np.mean((cnn_label != base_label)
                                              & (base_label == known_index)
                                              & (cnn_label != known_index))),
            "neither_correct": float(np.mean((cnn_label != base_label)
                                             & (cnn_label != known_index)
                                             & (base_label != known_index))),
            "both_same_but_wrong": float(np.mean((cnn_label == base_label)
                                                 & (cnn_label != known_index))),
        },
        "real_dev_replay": real,
    }
    (OUT / "design_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("real_dev_replay",)}, indent=1))
    print("wrote", OUT / "features_dev.npz")
    print("real sessions:")
    for session in ("train-01", "train-02", "val-01"):
        rows = [row for row in real if row["session"] == session]
        print("  ", session, "frames", len(rows),
              "cnn BPSK", sum(1 for row in rows if CLASSES[int(np.argmax(row["cnn_probs"]))] == "BPSK"),
              "baseline BPSK", sum(1 for row in rows
                                   if CLASSES[int(np.argmax(row["base_probs"]))] == "BPSK"))


if __name__ == "__main__":
    main()
