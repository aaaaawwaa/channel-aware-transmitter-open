"""Frozen-threshold evaluation: five known classes, OTHER and OFF rejection.

Quality rejects retain their true labels. Historical checkpoints can only be
re-evaluated through an explicit, experimental legacy preprocessing audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np

from . import dataset as manifest_contract
from .train import (CLASS_ORDER, PREPROCESSING, build_network, preprocess,
                    validate_provenance, ancestry_manifests, reject_seen_test)

ROOT = Path(__file__).resolve().parents[2]
KNOWN = CLASS_ORDER[:-1]
UNKNOWN_INDEX = len(CLASS_ORDER)
LEGACY_PREPROCESSING = "dc-shared-component-rms-legacy-v0"
SUPPORTED_DOMAIN = {
    "input_fs_hz": 60000000, "decim": [1],
    "symbol_rates_hz": [2000000, 3000000, 5000000],
    "shaping": ["RECT", "RRC0.35"],
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validated_manifest(path, root):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    origins = {s.get("origin") for s in data.get("sessions", [])}
    if len(origins) != 1 or not origins.issubset(set(manifest_contract.ORIGINS)):
        raise ValueError("manifest must contain one declared origin")
    origin = next(iter(origins))
    result = manifest_contract.validate_manifest(data, root, coverage_origin=origin)
    if not result.valid:
        raise ValueError("manifest invalid: " + json.dumps(result.errors[:8]))
    return data, result, origin


def _lineage(metadata, manifest_path, data, selected):
    """Verify recorded training input; one all-split manifest is legitimate."""
    digest = metadata.get("manifest_sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("model requires a training manifest SHA256")
    if digest == _sha(manifest_path):
        training = data
    elif metadata.get("manifest"):
        path = Path(metadata["manifest"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or _sha(path) != digest:
            raise ValueError("training manifest missing or SHA256 mismatch")
        training, _, _ = _validated_manifest(path, Path(metadata.get("root", path.parent)))
    else:
        # Older/minimal model receipts remain experimental, never production.
        return False
    source = [f for f in training["frames"] if f["split"] in ("train", "val")]
    sessions = {f["session_id"] for f in source}
    hashes = {f["iq_sha256"] for f in source}
    seeds = {f["tx_reference"]["seed_family"] for f in source if f["tx_reference"]}
    for frame in selected:
        seed = frame["tx_reference"]["seed_family"] if frame["tx_reference"] else None
        if frame["session_id"] in sessions or frame["iq_sha256"] in hashes or seed in seeds:
            raise ValueError("evaluation manifest leaks training/validation session, IQ or seed")
    for ancestral_data in ancestry_manifests(metadata.get("provenance", {})):
        reject_seen_test(ancestral_data, selected)
    return {f["split"] for f in source} == {"train", "val"}


def evaluate(model_dir, manifest_path, root, split="test", output_dir=None,
             legacy_preprocessing=False):
    if split != "test":
        raise ValueError("independent evaluation requires the test split")
    model_dir, manifest_path, root = Path(model_dir), Path(manifest_path), Path(root)
    metadata = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
    version = metadata.get("preprocessing")
    legacy = version != PREPROCESSING
    if legacy and (not legacy_preprocessing or version not in (
            None, "per-component DC + shared complex RMS", LEGACY_PREPROCESSING)):
        raise ValueError("checkpoint preprocessing version mismatch; legacy audit requires explicit flag")
    if metadata.get("class_order") != list(CLASS_ORDER):
        raise ValueError("model class order mismatch")
    thresholds = json.loads((model_dir / "thresholds.json").read_text(encoding="utf-8"))
    sel = thresholds["selection"]
    energy_floor_db, pmin, margin = (thresholds["energy_floor_db"], sel["max_softmax"], sel["margin"])
    if (any(type(v) not in (int, float) or not np.isfinite(v) for v in (energy_floor_db, pmin, margin))
            or not 0 <= pmin <= 1 or not 0 <= margin <= 1):
        raise ValueError("invalid frozen thresholds")
    if metadata.get("thresholds") is not None and metadata["thresholds"] != thresholds:
        raise ValueError("thresholds mismatch model metadata")

    data, validation, origin = _validated_manifest(manifest_path, root)
    selected = [f for f in data["frames"] if f["split"] == split]
    if not selected:
        raise ValueError("evaluation manifest has no test frames")
    if any(f["augmentation_parent_id"] for f in selected):
        raise ValueError("test augmentation cannot inflate independent evaluation")
    lineage_verified = _lineage(metadata, manifest_path, data, selected)
    for f in selected:
        cfg = f["configuration"]
        if cfg["sample_rate_hz"] != 60000000 or cfg["ratio"] != 0:
            raise ValueError("evaluation outside model sampling domain")

    import torch
    torch.set_num_threads(1)
    net = build_network()
    checkpoint_path = model_dir / "model.pt"
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    lineage_verified = validate_provenance(metadata, ckpt, checkpoint_path) and lineage_verified
    if ckpt.get("class_order") != list(CLASS_ORDER):
        raise ValueError("checkpoint class order mismatch")
    if not legacy and ckpt.get("preprocessing") != PREPROCESSING:
        raise ValueError("checkpoint preprocessing version mismatch")
    net.load_state_dict(ckpt["model_state_dict"])
    if any(not bool(torch.isfinite(p).all()) for p in net.parameters()):
        raise ValueError("checkpoint contains non-finite weights")
    net.eval()
    conf = np.zeros((7, 7), dtype=np.int64)
    counts = {k: 0 for k in ("known_usable", "known_gate_rejected", "known_to_unknown", "off",
                             "unknown", "unknown_predicted", "off_rejected", "unknown_rejected")}
    valid_sessions = {label: set() for label in CLASS_ORDER + ("OFF",)}
    valid_frames = {label: 0 for label in CLASS_ORDER + ("OFF",)}
    reasons, rows, coverage_records = {}, [], []
    for frame in selected:
        label = frame["label"]
        known = label in KNOWN
        y = CLASS_ORDER.index(label) if label != "OFF" else UNKNOWN_INDEX
        pred, reason, raw_prediction = UNKNOWN_INDEX, "", None
        usable = manifest_contract._usable(frame) and frame["samples"] == 4096
        if not usable:
            reason = ",".join(frame["exclusion_reasons"]) or "incomplete"
            gate = {"ok": False}
        else:
            iq = np.frombuffer((root / frame["iq_path"]).read_bytes(), dtype="<i2").reshape(-1, 2)
            x, gate = preprocess(iq)
            if not gate["ok"]:
                reason = gate["reason"]
            else:
                if legacy:
                    x = x * np.float32(np.sqrt(2))
                    gate = dict(gate, rms=gate["rms"] / np.sqrt(2))
                if 20 * np.log10(gate["rms"] + 1e-30) < energy_floor_db:
                    reason = "below_energy_floor"
                else:
                    with torch.no_grad():
                        probs = torch.softmax(net(torch.from_numpy(x[None, ...])), dim=1).numpy()[0]
                    order = np.argsort(probs)[::-1]
                    raw_prediction = CLASS_ORDER[int(order[0])]
                    if probs[order[0]] < pmin or probs[order[0]] - probs[order[1]] < margin:
                        reason = "below_confidence_threshold"
                    elif order[0] == len(KNOWN):
                        reason = "other_output"
                    else:
                        pred = int(order[0])
        if usable and (gate["ok"] or label == "OFF"):
            valid_sessions[label].add(frame["session_id"])
            valid_frames[label] += 1
            coverage_records.append(frame)
        if known:
            counts["known_usable" if usable and gate["ok"] else "known_gate_rejected"] += 1
            if pred == UNKNOWN_INDEX:
                counts["known_to_unknown"] += 1
        else:
            population = "off" if label == "OFF" else "unknown"
            counts[population] += 1
            if pred == UNKNOWN_INDEX:
                counts[population + "_rejected"] += 1
        conf[y, pred] += 1
        if pred == UNKNOWN_INDEX:
            counts["unknown_predicted"] += 1
            reasons[reason] = reasons.get(reason, 0) + 1
        rows.append({"capture_id": frame["capture_id"], "true": label,
                     "pred": CLASS_ORDER[pred] if pred < UNKNOWN_INDEX else "UNKNOWN",
                     "raw_prediction": raw_prediction, "reason": reason,
                     "quality_usable": bool(usable and gate["ok"])})

    recalls, precisions, f1s = [], [], []
    for c in range(len(KNOWN)):
        r = float(conf[c, c] / conf[c].sum()) if conf[c].sum() else 0.0
        p = float(conf[c, c] / conf[:, c].sum()) if conf[:, c].sum() else 0.0
        recalls.append(r); precisions.append(p)
        f1s.append(2 * r * p / (r + p) if r + p else 0.0)
    f1 = float(np.mean(f1s))
    # Diagnostic only: retain the official denominator and acceptance above.
    quality_conf = np.zeros_like(conf)
    for row in rows:
        if row["quality_usable"]:
            y = CLASS_ORDER.index(row["true"]) if row["true"] != "OFF" else UNKNOWN_INDEX
            p = CLASS_ORDER.index(row["pred"]) if row["pred"] != "UNKNOWN" else UNKNOWN_INDEX
            quality_conf[y, p] += 1
    from .train import _macro_f1
    quality_f1, quality_recall, quality_precision = _macro_f1(quality_conf, len(KNOWN))
    off_rejection = counts["off_rejected"] / counts["off"] if counts["off"] else 0.0
    unknown_rejection = counts["unknown_rejected"] / counts["unknown"] if counts["unknown"] else 0.0
    # Complete signed12 bytes alone do not make a zero-energy known frame a
    # valid coverage example. Recount only original frames passing quality.
    missing = [cell for cell in manifest_contract._coverage(coverage_records, origin)
               if cell["split"] == "test"]
    acceptance = {
        "known_macro_f1_ge_0_90": f1 >= .90,
        "per_class_recall_ge_0_80": all(r >= .80 for r in recalls),
        "unknown_rejection_ge_0_90": counts["unknown"] > 0 and unknown_rejection >= .90,
        "off_rejection_ge_0_90": counts["off"] > 0 and off_rejection >= .90,
        "coverage_complete": not missing,
        "original_sessions_and_frames": all(len(valid_sessions[k]) >= 3 and valid_frames[k] >= 30
                                            for k in CLASS_ORDER + ("OFF",)),
        "independent_lineage_verified": lineage_verified,
        "physical_test": origin == "physical",
        "current_preprocessing": not legacy,
    }
    accepted = all(acceptance.values())
    status = "production" if accepted else "experimental"
    report = {
        "schema_version": 1, "preprocessing": LEGACY_PREPROCESSING if legacy else PREPROCESSING,
        "legacy_preprocessing_audit": legacy, "model_status": status, "status": status,
        "model_capability": accepted, "acceptance": accepted, "acceptance_gates": acceptance,
        "production_ready": accepted, "lineage_verified": lineage_verified,
        "model_dir": str(model_dir), "manifest": str(manifest_path.resolve()),
        "root": str(root.resolve()), "split": split,
        "model_checkpoint_sha256": _sha(checkpoint_path),
        "training_manifest_sha256": metadata["manifest_sha256"],
        "evaluation_manifest_sha256": _sha(manifest_path),
        "origin": "cabled" if origin == "physical" else "synthetic",
        "supported_domain": SUPPORTED_DOMAIN, "coverage_missing": missing,
        "thresholds": sel, "energy_floor_db": energy_floor_db,
        "counts": counts, "gate_reasons": reasons, "confusion_matrix": conf.tolist(),
        "class_order": list(CLASS_ORDER) + ["UNKNOWN"], "macro_f1": f1,
        "per_class_recall": dict(zip(KNOWN, recalls)), "per_class_precision": dict(zip(KNOWN, precisions)),
        "off_rejection_rate": off_rejection, "unknown_rejection_rate": unknown_rejection,
        "known_to_unknown_rate": counts["known_to_unknown"] / max(
            counts["known_usable"] + counts["known_gate_rejected"], 1),
        "quality_usable_diagnostic": {
            "used_for_acceptance": False, "known_frames": int(quality_conf[:len(KNOWN)].sum()),
            "macro_f1": quality_f1, "confusion_matrix": quality_conf.tolist(),
            "per_class_recall": dict(zip(KNOWN, quality_recall)),
            "per_class_precision": dict(zip(KNOWN, quality_precision))},
        "class_sessions": {k: len(valid_sessions[k]) for k in CLASS_ORDER},
        "class_frames": {k: valid_frames[k] for k in CLASS_ORDER},
        "off_sessions": len(valid_sessions["OFF"]), "off_frames": valid_frames["OFF"],
    }
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=False)
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        (out / "per-item.json").write_text(json.dumps({"items": rows}, indent=1, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("macro_f1", "per_class_recall", "off_rejection_rate",
                                             "unknown_rejection_rate", "model_status")}, indent=2))
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--split", default="test", choices=("test",))
    p.add_argument("--output", type=Path)
    p.add_argument("--legacy-preprocessing", action="store_true", help="experimental audit of a historical checkpoint")
    a = p.parse_args(argv)
    evaluate(a.model, a.manifest, a.root, split=a.split, output_dir=a.output,
             legacy_preprocessing=a.legacy_preprocessing)


if __name__ == "__main__":
    main()
