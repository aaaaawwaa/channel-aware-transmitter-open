#!/usr/bin/env python3
"""Independent symbol/payload verification for the amplitude-60 TX set.

Reconstructs each class from the documented PRBS7 and constellation tables, then
checks the on-disk TX payload and 4096 reference window exactly.  Does not import
the classifier or a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[2]
DEFAULT_OUT = PACKAGE / "outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.fourclass_test_vectors import independent_check as base_checker  # noqa: E402


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def expected_codes(mode, seed, amplitude):
    bps = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}[mode]
    bits = base_checker.prbs7_bits(seed, 127 * bps)
    symbols = np.asarray(
        [base_checker.map_symbol(mode, bits[i * bps:(i + 1) * bps])
         for i in range(127)], dtype=np.float64)
    peak = float(np.max(np.abs(symbols)))
    return np.rint(symbols * (float(amplitude) / peak)).astype(np.int16), peak, bits


def verify(out_dir=DEFAULT_OUT):
    out_dir = Path(out_dir).resolve()
    manifest = load_json(out_dir / "manifest.json")
    amplitude = manifest["signal"]["amplitude"]
    checks = []
    status = "PASS"
    for vector in manifest["vectors"]:
        mode = vector["truth_class"]
        seed = vector["seed"]
        codes, peak, bits = expected_codes(mode, seed, amplitude)
        expected_tx = np.repeat(codes, manifest["signal"]["samples_per_symbol"], axis=0)
        tx_path = PACKAGE / vector["tx_payload"]["path"]
        ref_path = PACKAGE / vector["reference_4096"]["path"]
        tx = np.frombuffer(tx_path.read_bytes(), dtype="<i2").reshape(-1, 2)
        ref = np.frombuffer(ref_path.read_bytes(), dtype="<i2").reshape(-1, 2)
        expected_ref = np.tile(expected_tx, (2, 1))[:4096]
        truth_path = PACKAGE / vector["independent_truth"]["path"]
        truth = load_json(truth_path)
        row = {
            "mode": mode,
            "seed": seed,
            "amplitude": amplitude,
            "tx_samples": int(tx.shape[0]),
            "reference_samples": int(ref.shape[0]),
            "tx_sha256": sha256_bytes(tx_path.read_bytes()),
            "reference_sha256": sha256_bytes(ref_path.read_bytes()),
            "truth_sha256": sha256_file(truth_path),
            "tx_sha256_matches_manifest": sha256_bytes(tx_path.read_bytes()) == vector["tx_payload"]["sha256"],
            "reference_sha256_matches_manifest": sha256_bytes(ref_path.read_bytes()) == vector["reference_4096"]["sha256"],
            "truth_sha256_matches_manifest": sha256_file(truth_path) == vector["independent_truth"]["sha256"],
            "tx_exact_match": bool(np.array_equal(tx, expected_tx)),
            "reference_exact_match": bool(np.array_equal(ref, expected_ref)),
            "truth_codes_match": bool(np.array_equal(
                np.asarray(truth["scaled_code_iq"], dtype=np.int16), codes)),
            "truth_bits_match": bool(truth["bits"] == bits),
            "expected_peak_component": peak,
            "max_abs_code": int(np.max(np.abs(tx.astype(np.int32)))),
            "no_clipping_code": bool(int(tx.min()) > -2048 and int(tx.max()) < 2047),
            "tx_bytes": int(tx_path.stat().st_size),
            "reference_bytes": int(ref_path.stat().st_size),
            "old_amp1800_sha256": (vector.get("old_amp1800_60msps") or {}).get("sha256"),
            "old_amp1800_same_sha256": (vector.get("old_amp1800_60msps") or {}).get("same_sha256"),
            "byte_equality_with_amp1800_claimed": False,
        }
        row["status"] = "PASS" if all(row[k] for k in (
            "tx_sha256_matches_manifest", "reference_sha256_matches_manifest",
            "truth_sha256_matches_manifest", "tx_exact_match",
            "reference_exact_match", "truth_codes_match", "truth_bits_match",
            "no_clipping_code")) and not row["old_amp1800_same_sha256"] else "FAIL"
        if row["status"] != "PASS":
            status = "FAIL"
        checks.append(row)
    doc = {
        "schema": "ml-fourclass-hardware-amp60-verification-v1",
        "offline_generated_tx_payload": True,
        "real_capture": False,
        "independent_of_classifier": True,
        "model_predictions_used_as_truth": False,
        "checks": checks,
        "status": status,
        "construction": manifest["reference_window"],
        "note": ("Independent PRBS7 + axis-table reconstruction; amplitude-60 payloads are "
                 "not byte-equal to amplitude-1800 vectors, and real RF IQ is not required to "
                 "be byte-equal to the ideal payload."),
    }
    out = out_dir / "verification.json"
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"out": str(out), "status": status,
            "modes": [{"mode": c["mode"], "status": c["status"],
                       "tx_exact": c["tx_exact_match"],
                       "ref_exact": c["reference_exact_match"]}
                      for c in checks]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = verify(args.out_dir)
    print(json.dumps(result, indent=1))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
