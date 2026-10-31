#!/usr/bin/env python3
"""Evaluate parity cases for baseline and optimized adapters.

Separate process per mode avoids flat-module name collisions:

  python eval_cases.py --mode baseline  --candidate <release> --out results/cases_baseline.json
  python eval_cases.py --mode optimized --candidate <release> --out results/cases_optimized.json

Cases cover:
* the four candidate full-path frames (normal + narrowband stress);
* all 43 frozen arm_probe frames (clean, quality rejection, narrowband stress);
* synthetic fixtures (valid four-class, invalid length/sample rate);
* direct input-domain and threshold-boundary counterexamples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
ARM = ROOT / "ml_training" / "arm_probe"
REAL_FIXTURES = ROOT / "ml_training" / "real_campaign" / "fixtures" / "synthetic"
FROZEN_CONTEXT = {"format": "s16_iq_le", "samples": 4096, "sample_rate_hz": 60_000_000,
                  "decimation": 1, "symbol_rate_hz": 3_000_000, "shaping": "RECT"}


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _read_iq(path):
    return np.fromfile(path, dtype="<i2").reshape(4096, 2)


def _selected(result):
    modulation = result["modulation"]
    return {
        "status": result["status"],
        "input_domain_status": result["input_domain"]["status"],
        "input_domain_reason": result["input_domain"]["reason"],
        "modulation": {
            "class": modulation["class"],
            "final_label": modulation["final_label"],
            "final_label_source": modulation["final_label_source"],
            "accepted": modulation["accepted"],
            "reason": modulation["reason"],
            "scores4": modulation["scores4"],
            "cnn_raw_scores4": modulation["cnn_raw_scores4"],
            "cnn_logits4": modulation["cnn_logits4"],
            "cnn_raw_top1": modulation["cnn_raw_top1"],
            "arbitration_applied": modulation["arbitration_applied"],
            "arbitration_source": modulation["arbitration_source"],
            "arbitration": modulation["arbitration"],
            "baseline_scores4": modulation["baseline_scores4"],
            "spectral": modulation["spectral"],
            "spectral_trigger": modulation["spectral_trigger"],
            "decision_stage": modulation["decision_stage"],
        },
        "pure_class_hint": result.get("pure_class_hint"),
        "passthrough_sha256": _json_digest(result["passthrough"]),
        "contract_sha256": _json_digest(result["contract"]),
        "provenance_sha256": _json_digest(result["provenance"]),
    }


def candidate_cases(candidate, frames_dir, tags):
    sys.path.insert(0, str(Path(candidate).resolve()))
    import rf_modrec4_fullpath
    cases = []
    for tag in tags:
        frame, envelope, environment = rf_modrec4_fullpath._load_frame(frames_dir, tag)
        context = {"format": "s16_iq_le", "samples": frame.samples,
                   "sample_rate_hz": frame.sample_rate_hz,
                   "decimation": frame.decimation,
                   "symbol_rate_hz": envelope.submitted.config.symbol_rate_hz,
                   "shaping": envelope.submitted.config.shaping}
        demod = {"algorithm_version": envelope.algorithm_version,
                 "carrier_sync": envelope.result.carrier_sync,
                 "timing_sync": envelope.result.timing_sync,
                 "reference_sync": envelope.result.reference_sync}
        six_class = {"label": "QPSK", "scores": [0.01, 0.02, 0.90, 0.03, 0.01, 0.03]}
        cases.append({"case_id": "candidate/%s" % tag, "category": "candidate",
                      "iq": _read_iq(Path(frames_dir) / (tag + ".iq")),
                      "context": context, "environment": environment,
                      "demod": demod, "six_class": six_class})
    return cases


def arm_cases(arm_dir):
    manifest = json.loads((Path(arm_dir) / "input_manifest.json").read_text(encoding="utf-8"))
    cases = []
    for entry in manifest["frames"]:
        group = entry["group"]
        category = ("clean" if group.startswith("clean_") else
                    "rejection" if group.startswith("gate_") else
                    "stress" if group.startswith("stress_") else "other")
        cases.append({"case_id": "arm/%s" % entry["id"], "category": "arm_" + category,
                      "iq": _read_iq(Path(arm_dir) / entry["path"]),
                      "context": dict(FROZEN_CONTEXT), "environment": None,
                      "demod": None, "six_class": None})
    return cases


def fixture_cases(fixture_dir):
    fixture_dir = Path(fixture_dir)
    cases = []
    for path in sorted((fixture_dir / "valid").glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        cases.append({"case_id": "fixture/valid/" + path.stem,
                      "category": "fixture_valid", "iq": _read_iq(path.with_suffix(".iq")),
                      "context": meta["context"], "environment": None,
                      "demod": None, "six_class": None})
    for name in ("wrong_length", "wrong_sample_rate"):
        path = fixture_dir / "invalid" / (name + ".json")
        meta = json.loads(path.read_text(encoding="utf-8"))
        iq_path = path.with_suffix(".iq")
        raw = iq_path.read_bytes()
        try:
            iq = np.frombuffer(raw, dtype="<i2").reshape(4096, 2)
        except ValueError:
            iq = np.frombuffer(raw, dtype="<i2")
        cases.append({"case_id": "fixture/invalid/" + name, "category": "fixture_invalid",
                      "iq": iq, "context": meta["context"], "environment": None,
                      "demod": None, "six_class": None})
    return cases


def boundary_cases(candidate, frames_dir):
    sys.path.insert(0, str(Path(candidate).resolve()))
    import rf_modrec4_fullpath
    frame, envelope, _ = rf_modrec4_fullpath._load_frame(frames_dir, "nf-00-normal")
    base = _read_iq(Path(frames_dir) / "nf-00-normal.iq")
    valid = dict(FROZEN_CONTEXT)
    cases = []

    def add(case_id, iq, context, category="boundary"):
        cases.append({"case_id": case_id, "category": category, "iq": iq,
                      "context": context, "environment": None, "demod": None,
                      "six_class": None})

    add("boundary/missing_context", base, None)
    context = dict(valid); context["sample_rate_hz"] = 30_000_000
    add("boundary/sample_rate_30m", base, context)
    context = dict(valid); context["decimation"] = 2
    add("boundary/decimation_2", base, context)
    context = dict(valid); context["shaping"] = "RRC"
    add("boundary/shaping_rrc", base, context)
    context = dict(valid); context["samples"] = 2048
    add("boundary/samples_2048", base, context)
    add("boundary/dtype_float32", base.astype(np.float32), valid)
    add("boundary/dtype_big_endian", base.astype(">i2"), valid)
    invalid_range = base.copy(); invalid_range[0, 0] = 3000
    add("boundary/range_3000", invalid_range, valid)
    rails = base.copy(); rails[0, 0] = 2047; rails[1, 0] = -2048
    add("boundary/rails", rails, valid)
    for scale in (0.001, 0.01, 0.05, 0.2, 0.5, 0.9, 1.1, 2.0):
        scaled = np.clip(np.rint(base.astype(np.float64) * scale),
                         -2048, 2047).astype(np.int16)
        add("boundary/scale_%g" % scale, scaled, valid)
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "optimized"), required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--frames-dir", required=True, type=Path)
    parser.add_argument("--candidate-tags", nargs="+", default=[
        "nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arm-dir", type=Path, default=ARM)
    parser.add_argument("--fixtures-dir", type=Path, default=REAL_FIXTURES)
    args = parser.parse_args(argv)
    release = args.candidate.resolve()
    sys.path.insert(0, str(PACKAGE))
    sys.path.insert(0, str(release))
    sys.dont_write_bytecode = True
    if args.mode == "baseline":
        import rf_modrec4_adapter as adapter_mod
    else:
        import optimized_adapter as adapter_mod
    import merged_release
    manifest = merged_release.verify_release(release, require_software=True)
    artifact = manifest[merged_release.MODREC4_MODEL_KEY]
    adapter = adapter_mod.ModRec4Adapter.from_release(release, artifact)
    cases = (candidate_cases(release, args.frames_dir, args.candidate_tags)
             + arm_cases(args.arm_dir) + fixture_cases(args.fixtures_dir)
             + boundary_cases(release, args.frames_dir))
    outputs = []
    for case in cases:
        result = adapter.evaluate_iq(
            case["iq"], context=case["context"], environment=case["environment"],
            demod=case["demod"], six_class=case["six_class"],
            frame_id=case["case_id"])
        selected = _selected(result)
        selected.update({"case_id": case["case_id"], "category": case["category"]})
        outputs.append(selected)
    document = {"schema": "ml-performance-parity-cases-v1", "mode": args.mode,
                "candidate": str(release.relative_to(ROOT)).replace("\\", "/"),
                "counts": {"total": len(outputs),
                           "by_category": {category: len([item for item in outputs
                                                          if item["category"] == category])
                                           for category in sorted({item["category"]
                                                                   for item in outputs})}},
                "cases": outputs}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "counts": document["counts"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
