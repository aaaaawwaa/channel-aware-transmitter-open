#!/usr/bin/env python3
"""Verify the fixed replay package against its recorded original/A outputs.

This is a host-side consistency check; the same IQ and expected outputs can be
replayed on ARM.  It is not performance acceptance and does not change any error.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from build_package import (  # noqa: E402
    A_DIR, ORIGINAL_MODEL, CLASSES, path_of, output_of, sha_bytes, sha_file,
)
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def close_scores(left, right, tol=1e-12):
    if left is None or right is None:
        return left == right
    if len(left) != len(right):
        return False
    return all(math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
               for a, b in zip(left, right))


def compare_model(name, recorded, actual):
    errors = []
    for key in ("label", "reason", "accepted"):
        if recorded.get(key) != actual.get(key):
            errors.append("%s.%s: recorded=%r actual=%r" %
                          (name, key, recorded.get(key), actual.get(key)))
    if not close_scores(recorded.get("scores"), actual.get("scores")):
        errors.append("%s.scores differ" % name)
    rpath = recorded.get("path") or {}
    apath = actual.get("path") or {}
    for key in ("stage", "reason", "cnn_label", "baseline_label",
                "arbitration_applied", "final"):
        if rpath.get(key) != apath.get(key):
            errors.append("%s.path.%s: recorded=%r actual=%r" %
                          (name, key, rpath.get(key), apath.get(key)))
    if not close_scores(rpath.get("scores"), apath.get("scores")):
        errors.append("%s.path.scores differ" % name)
    if not close_scores(rpath.get("baseline_scores"), apath.get("baseline_scores")):
        errors.append("%s.path.baseline_scores differ" % name)
    if rpath.get("arbitration_margin_bpsk_minus_qpsk") is not None and \
            apath.get("arbitration_margin_bpsk_minus_qpsk") is not None:
        if not math.isclose(float(rpath["arbitration_margin_bpsk_minus_qpsk"]),
                            float(apath["arbitration_margin_bpsk_minus_qpsk"]),
                            rel_tol=1e-12, abs_tol=1e-12):
            errors.append("%s.path.arbitration_margin differs" % name)
    return errors


def main():
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    if sha_file(ORIGINAL_MODEL) != manifest["models"]["original"]["model_sha256"]:
        raise SystemExit("original model hash changed")
    if sha_file(A_DIR / "model.json") != manifest["models"]["A"]["model_sha256"]:
        raise SystemExit("A model hash changed")
    if sha_file(A_DIR / "rules.json") != manifest["models"]["A"]["rules_sha256"]:
        raise SystemExit("A rules hash changed")
    original = Round2Predictor()
    candidate = Round2Predictor(rules_path=A_DIR / "rules.json")
    results = []
    all_errors = []
    for frame in manifest["frames"]:
        frame_dir = BASE / "frames" / frame["frame_id"]
        recorded = json.loads((frame_dir / "frame.json").read_text(encoding="utf-8"))
        raw = (frame_dir / recorded["iq_file"]).read_bytes()
        errors = []
        if sha_bytes(raw) != recorded["iq_sha256"]:
            errors.append("iq_sha256 mismatch")
        if len(raw) != recorded["iq_bytes"]:
            errors.append("iq_bytes mismatch")
        import numpy as np
        iq = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).copy()
        errors.extend(compare_model("original", recorded["original"],
                                    output_of(original, iq)))
        errors.extend(compare_model("A", recorded["A"],
                                    output_of(candidate, iq)))
        # Provenance: synthetic frames must still match the recorded sim-v1 split/index;
        # the real frame must still match the recorded task80 file.
        if recorded["synthetic"]:
            from ml_training.common.dataset import load_manifest, load_split
            data = load_split(ROOT / "ml_training" / "datasets" / "sim-v1",
                              load_manifest(ROOT / "ml_training" / "datasets" / "sim-v1"),
                              recorded["source"]["split"], labels=list(CLASSES))
            source = np.asarray(data["X"][recorded["source"]["index"]],
                                dtype="<i2").tobytes()
            if sha_bytes(source) != recorded["iq_sha256"]:
                errors.append("sim-v1 source frame mismatch")
        else:
            source_path = ROOT / recorded["source"]["real_path"]
            if not source_path.is_file():
                errors.append("real source missing")
            elif sha_bytes(source_path.read_bytes()) != recorded["iq_sha256"]:
                errors.append("real source frame mismatch")
        result = {"frame_id": frame["frame_id"], "category": frame["category"],
                  "synthetic": recorded["synthetic"], "errors": errors,
                  "status": "PASS" if not errors else "FAIL"}
        results.append(result)
        all_errors.extend(errors)
    doc = {
        "schema": "ml-candidate-a-replay-verification-v1",
        "package": str(BASE),
        "status": "PASS" if not all_errors else "FAIL",
        "frames": results,
        "errors": all_errors,
        "boundaries": {
            "host_only": True,
            "arm_not_run": True,
            "performance_acceptance": False,
            "errors_unchanged": True,
        },
    }
    (BASE / "verification.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": doc["status"],
        "frames": len(results),
        "errors": all_errors,
        "verification": str(BASE / "verification.json"),
    }, indent=1))
    if all_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
