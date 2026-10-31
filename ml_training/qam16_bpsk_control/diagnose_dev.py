#!/usr/bin/env python3
"""Post-selection development diagnosis on the 14 real frames (not acceptance).

It is run after selection.json exists; it never feeds selection or training.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DATA = ROOT / "evidence" / "rf-teaching" / "s2-46db-fourclass-real-rx-2026-10-22" / "run"
TRUTH = (ROOT / "ml_training" / "fourclass_qam16_diagnosis" / "results" /
         "tx_truth_side.json")
PRIOR_A = ROOT / "ml_training" / "qam16_domain_repair" / "results" / "candidate_A"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if not (RESULTS / "selection.json").is_file():
        raise SystemExit("selection.json must exist before dev diagnosis")
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    truth_by_key = {(e["frame_identity"]["capture_id"],
                     e["frame_identity"]["iq_sha256"]): e for e in truth["frames"]}
    predictors = {"frozen_original": Round2Predictor()}
    if (PRIOR_A / "rules.json").is_file():
        predictors["A_experimental_baseline"] = Round2Predictor(
            rules_path=PRIOR_A / "rules.json")
    for name in ("C0", "C1"):
        predictors[name] = Round2Predictor(rules_path=RESULTS / name / "rules.json")
    frames = []
    for meta_path in sorted(DATA.glob("s*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        iq_path = meta_path.with_suffix(".iq")
        if not iq_path.is_file():
            continue
        frame = np.frombuffer(iq_path.read_bytes(), dtype="<i2").reshape(-1, 2).copy()
        digest = sha(iq_path)
        entry = truth_by_key.get((meta.get("capture_id"), digest))
        if entry is None:
            continue
        tx = entry["tx"]
        truth_class = tx["mode"] if tx["mode"] != "OFF" else "OFF"
        row = {"frame": meta_path.stem, "truth": truth_class,
               "capture_id": meta.get("capture_id"), "iq_sha256": digest}
        for name, predictor in predictors.items():
            d = predictor.decide(frame)
            row[name] = {"pred": d.get("label"), "reason": d.get("reason"),
                         "accepted": d.get("label") in CLASSES,
                         "scores": d.get("scores")}
        frames.append(row)
    by_predictor = {}
    for name in predictors:
        on = [r for r in frames if r["truth"] in CLASSES]
        qam16 = [r for r in frames if r["truth"] == "QAM16"]
        off = [r for r in frames if r["truth"] == "OFF"]
        by_predictor[name] = {
            "on_correct": sum(1 for r in on if r[name]["pred"] == r["truth"]),
            "on_n": len(on),
            "qam16_correct": sum(1 for r in qam16 if r[name]["pred"] == r["truth"]),
            "qam16_n": len(qam16),
            "off_rejected": sum(1 for r in off if r[name]["pred"] == "UNKNOWN"),
            "off_n": len(off),
            "classes": {
                label: {
                    "n": sum(1 for r in frames if r["truth"] == label),
                    "correct": sum(1 for r in frames if r["truth"] == label
                                   and r[name]["pred"] == label),
                } for label in CLASSES},
        }
    output = {
        "schema": "ml-qam16-bpsk-control-dev-diagnosis-v1",
        "truth_source": str(TRUTH),
        "truth_sha256": sha(TRUTH),
        "selection_json_sha256": sha(RESULTS / "selection.json"),
        "dev_diagnosis_only": True,
        "not_training": True,
        "not_acceptance": True,
        "frames": frames,
        "summary_by_predictor": by_predictor,
        "selected_candidate": json.loads(
            (RESULTS / "selection.json").read_text(encoding="utf-8")).get("selected"),
    }
    raw = json.dumps(output, indent=2, ensure_ascii=False).encode("utf-8")
    (RESULTS / "dev_diagnosis.json").write_bytes(raw)
    print(json.dumps({
        "dev_diagnosis_sha256": hashlib.sha256(raw).hexdigest(),
        "selected": output["selected_candidate"],
        "qam16": {name: [r[name]["pred"] for r in frames if r["truth"] == "QAM16"]
                  for name in predictors},
        "summary": {name: {
            "on": "%d/%d" % (v["on_correct"], v["on_n"]),
            "qam16": "%d/%d" % (v["qam16_correct"], v["qam16_n"]),
            "off": "%d/%d" % (v["off_rejected"], v["off_n"]),
        } for name, v in by_predictor.items()},
    }, indent=1))


if __name__ == "__main__":
    main()
