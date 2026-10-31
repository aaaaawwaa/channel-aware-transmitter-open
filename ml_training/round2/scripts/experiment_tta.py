"""Round-2 candidate experiment: phase-symmetry test-time averaging (TTA) on the round-1 CNN.

Evidence: the round-1 failure session (train-02) shares a carrier phase of ~63 deg and the
CNN's decision surface has a phase-dependent pit near 55-65 deg (rotating a correctly
classified real frame by +60 deg flips it to QPSK).  All four constellations are invariant
under a 90-degree rotation (BPSK under 180), so averaging the model over the exact symmetry
group is a label-preserving, parameter-free change.

This script only measures the candidate on **development replay** data:
* the real physical frames analysed in round 1 (dev replay for round 2),
* the round-1 validation split (already used for round-1 checkpoint/threshold selection).
The round-1 test split is NOT used here and stays untouched as round-1 evidence.
The new round-2 test set will be generated separately with new seed families.

Writes ``ml_training/round2/analysis/tta_experiment.json`` and ``.md``.
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
from ml_training.common.gate import apply_gate  # noqa: E402
from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402

ML = ROOT / "ml_training"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18"
CANONICAL = EVIDENCE / "campaign5-val-followup" / "download"
SESSIONS = ("train-01", "train-02", "val-01")
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
ROTATIONS = (0.0, 90.0, 180.0, 270.0)
OUT = ML / "round2" / "analysis"


def rotate_complex(x, degrees):
    angle = np.deg2rad(degrees)
    cos, sin = np.cos(angle), np.sin(angle)
    out = np.empty_like(x)
    out[0] = x[0] * cos - x[1] * sin
    out[1] = x[0] * sin + x[1] * cos
    return out


def probabilities(model, x, tta):
    if not tta:
        logits = model.forward(x)
        exp = np.exp(logits - logits.max())
        return exp / exp.sum()
    accum = np.zeros(len(CLASSES), dtype=np.float64)
    for degrees in ROTATIONS:
        logits = model.forward(rotate_complex(x, degrees))
        exp = np.exp(logits - logits.max())
        accum += exp / exp.sum()
    return accum / len(ROTATIONS)


def predict(model, iq, tta, thresholds):
    x, gate = preprocess_iq(iq)
    if not gate["ok"]:
        return {"label": "UNKNOWN", "reason": gate["reason"], "scores": None}
    if gate["rms"] < thresholds["min_complex_rms"]:
        return {"label": "UNKNOWN", "reason": "below_energy_floor", "scores": None}
    probs = probabilities(model, x, tta)
    decision = apply_gate(probs, CLASSES, thresholds)
    return {"label": decision["label"], "reason": decision["reason"],
            "scores": [float(v) for v in probs]}


def macro_f1(truth, predicted):
    matrix = np.zeros((4, 4), dtype=np.int64)
    for t, p in zip(truth, predicted):
        matrix[int(t), int(p)] += 1
    f1 = []
    for index in range(4):
        row = matrix[index].sum()
        column = matrix[:, index].sum()
        recall = matrix[index, index] / row if row else 0.0
        precision = matrix[index, index] / column if column else 0.0
        f1.append(2 * precision * recall / (precision + recall) if (precision + recall) else 0.0)
    return float(np.mean(f1)), matrix


def evaluate_val(model, thresholds):
    dataset = ML / "datasets" / "sim-v1"
    manifest = load_manifest(dataset)
    val = load_split(dataset, manifest, "val", labels=list(CLASSES))
    truth = np.asarray([CLASSES.index(str(label)) for label in val["label"]])
    report = {}
    for tta in (False, True):
        predicted, rejected, phase_deg = [], [], []
        for index in range(len(val["X"])):
            x, gate = preprocess_iq(val["X"][index])
            assert gate["ok"]
            z = x[0] + 1j * x[1]
            phase_deg.append(np.degrees(np.angle(np.mean(z ** 2)) / 2.0) % 90.0)
            result = predict(model, val["X"][index], tta, thresholds)
            rejected.append(result["label"] == "UNKNOWN")
            predicted.append(CLASSES.index(result["label"]) if result["label"] in CLASSES else -1)
        predicted = np.asarray(predicted)
        accepted = predicted >= 0
        accuracy_all = float((predicted == truth).mean())
        f1_all, matrix = macro_f1(truth[accepted], predicted[accepted]) if accepted.any() else (0.0, None)
        phase_deg = np.asarray(phase_deg)
        octants = {}
        for low in range(0, 90, 15):
            mask = (phase_deg >= low) & (phase_deg < low + 15)
            if mask.any():
                octants["%d-%d" % (low, low + 15)] = {
                    "n": int(mask.sum()),
                    "accuracy_all": float((predicted[mask] == truth[mask]).mean()),
                }
        report["tta" if tta else "plain"] = {
            "val_frames": int(len(truth)),
            "accuracy_with_gate": accuracy_all,
            "rejected_known": int(np.sum(rejected)),
            "accepted_macro_f1": f1_all,
            "per_phase_deg_octant": octants,
            "matrix": matrix.tolist() if matrix is not None else None,
        }
    return report


def evaluate_real(model, thresholds):
    report = {}
    for session in SESSIONS:
        frames = []
        for meta_path in sorted((CANONICAL / session).glob("normal-*.iq")):
            frame_id = meta_path.stem
            truth_meta = json.loads((CANONICAL / session / (frame_id + ".json"))
                                    .read_text(encoding="utf-8"))
            truth = truth_meta.get("trainval", {}).get("truth")
            iq = read_frame(meta_path)
            x, _ = preprocess_iq(iq)
            phase = float(np.degrees(np.angle(np.mean((x[0] + 1j * x[1]) ** 2)) / 2.0))
            record = {"frame": frame_id, "truth": truth, "phase_deg": phase}
            for tta in (False, True):
                result = predict(model, iq, tta, thresholds)
                record["plain" if not tta else "tta"] = {
                    "label": result["label"], "reason": result["reason"],
                    "scores": result["scores"],
                }
            frames.append(record)
        normal = [r for r in frames if r["truth"] == "normal"]
        report[session] = {
            "normal": len(normal),
            "plain_bpsk": sum(1 for r in normal if r["plain"]["label"] == "BPSK"),
            "tta_bpsk": sum(1 for r in normal if r["tta"]["label"] == "BPSK"),
            "frames": frames,
        }
    return report


def main():
    model = load_model(ML / "artifacts" / "cnn" / "model.json")
    thresholds = dict(model.thresholds)
    val_report = evaluate_val(model, thresholds)
    real_report = evaluate_real(model, thresholds)
    document = {
        "schema": "ml-modrec-round2-tta-experiment-v1",
        "candidate": "phase-symmetry test-time averaging over {0,90,180,270} deg",
        "theory": "all four classes are invariant under 90-deg rotation (BPSK under 180); "
                  "averaging probabilities over the symmetry group removes phase-specific decision pits",
        "model_parameters_changed": 0,
        "model_size_changed_bytes": 0,
        "inference_multiplier": len(ROTATIONS),
        "development_data_only": True,
        "round1_test_used": False,
        "val_dev": val_report,
        "real_dev_replay": real_report,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tta_experiment.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    lines = ["# Round-2 candidate experiment: phase-symmetry TTA", "",
             "Dev-replay only: real round-1 physical frames + round-1 **validation** split.",
             "Round-1 test split not used. Round-2 test set is generated separately with new seeds.",
             "",
             "## Validation split (1200 frames, dev)", "",
             "| variant | accuracy (gate counted) | rejected known | accepted macro-F1 |",
             "|---|---|---|---|"]
    for key in ("plain", "tta"):
        data = val_report[key]
        lines.append("| %s | %.4f | %d | %.4f |" % (
            key, data["accuracy_with_gate"], data["rejected_known"], data["accepted_macro_f1"]))
    lines.append("")
    lines.append("Per phase octant (dev validation): plain vs TTA accuracy")
    lines.append("")
    lines.append("| phase deg | n | plain | TTA |")
    lines.append("|---|---|---|---|")
    for octant, data in val_report["plain"]["per_phase_deg_octant"].items():
        tta_data = val_report["tta"]["per_phase_deg_octant"].get(octant, {})
        lines.append("| %s | %d | %.4f | %.4f |" % (
            octant, data["n"], data["accuracy_all"],
            tta_data.get("accuracy_all", float("nan"))))
    lines.append("")
    lines.append("## Real dev replay (physical BPSK frames)")
    lines.append("")
    lines.append("| session | normal | plain BPSK | TTA BPSK |")
    lines.append("|---|---|---|---|")
    for session, data in real_report.items():
        lines.append("| %s | %d | %d | %d |" % (
            session, data["normal"], data["plain_bpsk"], data["tta_bpsk"]))
    lines.append("")
    lines.append("## train-02 per-frame (the failure session)")
    lines.append("")
    lines.append("| frame | phase | plain label (score) | TTA label (score) |")
    lines.append("|---|---|---|---|")
    for record in real_report["train-02"]["frames"]:
        if record["truth"] != "normal":
            continue
        plain = record["plain"]; tta = record["tta"]
        lines.append("| %s | %.2f | %s (%.3f) | %s (%.3f) |" % (
            record["frame"], record["phase_deg"], plain["label"], max(plain["scores"] or [0.0]),
            tta["label"], max(tta["scores"] or [0.0])))
    (OUT / "tta_experiment.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"val": {key: {k: v for k, v in data.items()
                                     if k in ("accuracy_with_gate", "rejected_known",
                                              "accepted_macro_f1")}
                              for key, data in val_report.items()},
                      "real": {session: {"plain_bpsk": data["plain_bpsk"],
                                         "tta_bpsk": data["tta_bpsk"],
                                         "normal": data["normal"]}
                               for session, data in real_report.items()}}, indent=1))


if __name__ == "__main__":
    main()
