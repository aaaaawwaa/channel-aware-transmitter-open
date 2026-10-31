"""Analyse the two round-2 improvement candidates on dev-replay data.

A) Feature-guard arbitration: keep the frozen CNN as the primary 4-class classifier and
   let the existing interpretive feature baseline arbitrate only the BPSK/QPSK confusion
   (a calibrated, small, parameter-free-at-inference rule; no retraining, no new data).
B) Spectral quality gate: reject tone-like / noise-like inputs with one interpretable
   spectral rule calibrated on gate_cal + known validation frames; report the rejection
   vs known-false-rejection trade-off (no "reject everything" shortcut).

Dev data only: round-1 validation + round-1 gate_cal + the three real sessions.
Round-1 test and the new round-2 test set are not used.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ML = ROOT / "ml_training"
ANALYSIS = ML / "round2" / "analysis"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
SPECTRAL_NAMES = ("top1_frac", "top5_frac", "top9_frac", "band_frac_3m",
                  "peak_to_median_db", "spectral_entropy")


def load_dev():
    data = np.load(ANALYSIS / "features_dev.npz", allow_pickle=False)
    return {key: data[key] for key in data.files}


def arbitration(dev, real):
    labels = np.asarray(dev["label"])
    known = np.isin(labels, CLASSES)
    truth = np.asarray([CLASSES.index(label) for label in labels[known]])
    cnn = dev["cnn_probs"][known]
    base = dev["base_probs"][known]
    cnn_label = cnn.argmax(axis=1)
    base_label = base.argmax(axis=1)
    # only the BPSK/QPSK pair is arbitrated
    pair = np.where(np.isin(cnn_label, (0, 1)))[0]
    bpsk = CLASSES.index("BPSK")
    qpsk = CLASSES.index("QPSK")
    margin_bpsk = base[:, bpsk] - base[:, qpsk]
    grid = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    rows = []
    for threshold in grid:
        predicted = cnn_label.copy()
        override = pair[np.abs(margin_bpsk[pair]) > threshold]
        predicted[override] = np.where(margin_bpsk[override] > 0, bpsk, qpsk)
        accuracy = float(np.mean(predicted == truth))
        overrides = int(len(override))
        override_correct = float(np.mean(predicted[override] == truth[override])) if overrides else 0.0
        changed_from_correct = int(np.sum((predicted != cnn_label) & (cnn_label == truth)))
        changed_to_correct = int(np.sum((predicted != cnn_label) & (predicted == truth)))
        rows.append({"threshold": threshold, "val_accuracy": accuracy, "overrides": overrides,
                     "override_accuracy": override_correct,
                     "changed_to_correct": changed_to_correct,
                     "changed_from_correct": changed_from_correct})
    best = max(rows, key=lambda row: row["val_accuracy"])
    # evaluation on real dev replay with the same rule
    real_rows = []
    for row in real:
        cnn_label_real = int(np.argmax(row["cnn_probs"]))
        base_probs = np.asarray(row["base_probs"])
        predicted = cnn_label_real
        if cnn_label_real in (0, 1):
            margin = base_probs[0] - base_probs[1]
            if abs(margin) > best["threshold"]:
                predicted = 0 if margin > 0 else 1
        real_rows.append({"session": row["session"], "frame": row["frame"],
                          "truth": row["truth"],
                          "cnn": CLASSES[cnn_label_real], "predicted": CLASSES[predicted]})
    by_session = {}
    for session in ("train-01", "train-02", "val-01"):
        rows_session = [item for item in real_rows if item["session"] == session
                        and item["truth"] == "normal"]
        by_session[session] = {
            "n": len(rows_session),
            "bpsk": sum(1 for item in rows_session if item["predicted"] == "BPSK"),
            "plain_bpsk": sum(1 for item in rows_session if item["cnn"] == "BPSK"),
        }
    return {"grid": rows, "selected": best, "real_dev_replay": by_session,
            "real_frames": real_rows}


def spectral_gate(dev):
    kind = np.asarray(dev["label"])
    split = np.asarray(dev["split"])
    known = np.isin(kind, CLASSES) & (split == "val")
    spectral = dev["spectral"]
    features = dev["features"]
    cnn = dev["cnn_probs"]
    result = {"known_frames": int(known.sum())}
    # ---- tone rule: top-k fraction
    tone = split == "gate_cal"
    tone &= np.asarray([label == "TONE" for label in kind])
    noise = split == "gate_cal"
    noise &= np.asarray([label == "NOISE" for label in kind])
    result["tone_frames"] = int(tone.sum())
    result["noise_frames"] = int(noise.sum())
    # candidate tone features with a threshold: which feature gives cleanest separation?
    tone_rows = []
    for name, index in (("top1_frac", 0), ("top5_frac", 1), ("top9_frac", 2),
                        ("peak_to_median_db", 4)):
        values_known = spectral[known, index]
        values_tone = spectral[tone, index]
        thresholds = np.unique(np.round(np.percentile(values_known, np.arange(50, 101, 2)), 6))
        best = None
        for threshold in thresholds:
            known_rej = float(np.mean(values_known > threshold))
            tone_rej = float(np.mean(values_tone > threshold))
            row = {"threshold": float(threshold), "tone_rejection": tone_rej,
                   "known_false_rejection": known_rej}
            if tone_rej >= 0.99 and (best is None or known_rej < best["known_false_rejection"]):
                best = row
        if best is None:
            best = {"threshold": None, "tone_rejection": float(np.mean(values_tone > thresholds.max())),
                    "known_false_rejection": float(np.mean(values_known > thresholds.max()))}
        tone_rows.append({"feature": name, **best})
    result["tone_rule_candidates"] = tone_rows
    # ---- noise rule: band fraction (low) and/or amplitude kurtosis (high)
    noise_rows = []
    band = spectral[:, 3]
    kurt = features[:, 0]
    for feature, values_known, values_noise, direction in (
            ("band_frac_3m", band[known], band[noise], "low"),
            ("amp_kurtosis", kurt[known], kurt[noise], "high")):
        candidates = []
        thresholds = np.unique(np.round(np.percentile(values_known, np.arange(0, 51, 2)), 6))
        for threshold in thresholds:
            if direction == "low":
                known_rej = float(np.mean(values_known < threshold))
                noise_rej = float(np.mean(values_noise < threshold))
            else:
                known_rej = float(np.mean(values_known > threshold))
                noise_rej = float(np.mean(values_noise > threshold))
            candidates.append({"threshold": float(threshold), "noise_rejection": noise_rej,
                               "known_false_rejection": known_rej})
        noise_rows.append({"feature": feature, "curve": candidates})
    result["noise_rule_candidates"] = noise_rows
    return result


def main():
    dev = load_dev()
    real = json.loads((ANALYSIS / "real_dev_features.json").read_text(encoding="utf-8"))
    arb = arbitration(dev, real)
    gate = spectral_gate(dev)
    document = {"schema": "ml-modrec-round2-candidate-analysis-v1",
                "arbitration": arb, "spectral_gate": gate}
    (ANALYSIS / "candidate_analysis.json").write_text(json.dumps(document, indent=1),
                                                     encoding="utf-8")
    print("=== arbitration ===")
    print("selected:", json.dumps(arb["selected"]))
    print("real:", json.dumps(arb["real_dev_replay"]))
    print("grid:")
    for row in arb["grid"]:
        print("  threshold %.1f val_acc %.4f overrides %4d override_acc %.3f change +%d/-%d" % (
            row["threshold"], row["val_accuracy"], row["overrides"], row["override_accuracy"],
            row["changed_to_correct"], row["changed_from_correct"]))
    print("=== spectral tone rule candidates ===")
    for row in gate["tone_rule_candidates"]:
        print("  ", json.dumps(row))
    print("=== spectral noise rule (selected points) ===")
    for row in gate["noise_rule_candidates"]:
        curve = row["curve"]
        for target in (0.01, 0.02, 0.05):
            feasible = [item for item in curve if item["known_false_rejection"] <= target]
            if feasible:
                best = max(feasible, key=lambda item: item["noise_rejection"])
                print("  %s target_FR<=%.0f%%: threshold=%.4f noise_rej=%.3f known_FR=%.4f" % (
                    row["feature"], target * 100, best["threshold"], best["noise_rejection"],
                    best["known_false_rejection"]))
    print("wrote", ANALYSIS / "candidate_analysis.json")


if __name__ == "__main__":
    main()
