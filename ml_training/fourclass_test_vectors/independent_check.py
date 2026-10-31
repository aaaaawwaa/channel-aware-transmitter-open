#!/usr/bin/env python3
"""Independent PRBS/constellation check for the SYNTHETIC vectors.

This checker does not import the generator, the classifier or any model artefact.
It re-implements the documented PRBS7 shift register and the frozen MSB-first
axis tables, rebuilds the expected 6 MSPS samples, and compares the exact
s16_iq_le bytes of both representations.  Therefore a model prediction cannot be
used as signal truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_DATA = PACKAGE / "data"
DEFAULT_RESULTS = PACKAGE / "results"


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Independent PRBS7 implementation: bit6 output, bit6 XOR bit5 feedback, seed 1..127.
def prbs7_bits(seed, count):
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError("PRBS seed must be 1..127")
    state = seed
    out = []
    for _ in range(int(count)):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def map_symbol(mode, bits):
    axis2 = (1, -1)
    axis4 = (3, 1, -3, -1)
    axis8 = (7, 5, 1, 3, -7, -5, -1, -3)
    bits = tuple(int(bit) for bit in bits)
    if mode == "BPSK":
        return (axis2[bits[0]], 0)
    if mode == "QPSK":
        return (axis2[bits[0]], axis2[bits[1]])
    if mode == "QAM16":
        return (axis4[(bits[0] << 1) | bits[1]],
                axis4[(bits[2] << 1) | bits[3]])
    if mode == "QAM64":
        return (axis8[(bits[0] << 2) | (bits[1] << 1) | bits[2]],
                axis8[(bits[3] << 2) | (bits[4] << 1) | bits[5]])
    raise ValueError("unknown mode %r" % (mode,))


def expected_rates(config, mode):
    signal = config["signal"]
    bits_per_symbol = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}[mode]
    n_symbols = int(signal["prbs_symbols_per_period"])
    bits = prbs7_bits(int(config["vectors"][[row["mode"] for row in config["vectors"]].index(mode)]["seed"]),
                      n_symbols * bits_per_symbol)
    symbols = np.asarray(
        [map_symbol(mode, bits[i * bits_per_symbol:(i + 1) * bits_per_symbol])
         for i in range(n_symbols)], dtype=np.float64)
    # Product normalisation: component peak == amplitude_codes.
    peak = float(np.max(np.abs(symbols)))
    scaled = symbols * (float(signal["amplitude_codes"]) / peak)
    six_expected = np.rint(scaled).astype(np.int16)
    six_expected = np.repeat(six_expected, 2, axis=0)
    sixty_period = np.repeat(six_expected, int(config["rates"]["sixty_msps"]["upsample_factor_from_six_msps"]),
                             axis=0)
    sixty_expected = np.tile(sixty_period,
                             (int(config["rates"]["sixty_msps"]["periods_available"]), 1))[
        :int(config["rates"]["sixty_msps"]["samples"])]
    raw_six = six_expected.astype("<i2").tobytes()
    raw_sixty = np.ascontiguousarray(sixty_expected, dtype="<i2").tobytes()
    return {
        "six": six_expected,
        "sixty": sixty_expected,
        "six_bytes": raw_six,
        "sixty_bytes": raw_sixty,
        "symbols_raw": symbols,
        "scaled_symbols": scaled,
        "peak_component": peak,
        "bits": bits,
    }


def check(data_dir=DEFAULT_DATA, results_dir=DEFAULT_RESULTS):
    data_dir = Path(data_dir).resolve()
    manifest = load_json(data_dir / "manifest.json")
    config = load_json(PACKAGE / "config" / "vectors.json")
    results = {
        "schema": "ml-fourclass-test-vectors-independent-check-v1",
        "synthetic": True,
        "checker_independent_of_classifier": True,
        "model_labels_used_as_truth": False,
        "checks": [],
        "status": "PASS",
    }
    for vector in manifest["vectors"]:
        mode = vector["truth_class"]
        expected = expected_rates(config, mode)
        six_path = data_dir / vector["rates"]["six_msps"]["path"]
        sixty_path = data_dir / vector["rates"]["sixty_msps"]["path"]
        six_actual_bytes = six_path.read_bytes()
        sixty_actual_bytes = sixty_path.read_bytes()
        six_actual = np.frombuffer(six_actual_bytes, dtype="<i2").reshape(-1, 2)
        sixty_actual = np.frombuffer(sixty_actual_bytes, dtype="<i2").reshape(-1, 2)
        six_expected = expected["six"]
        sixty_expected = expected["sixty"]
        checks = {
            "mode": mode,
            "truth_class": mode,
            "expected_symbols": int(expected["symbols_raw"].shape[0]),
            "expected_peak_component": expected["peak_component"],
            "six_msps_path": vector["rates"]["six_msps"]["path"],
            "sixty_msps_path": vector["rates"]["sixty_msps"]["path"],
            "six_msps_samples": int(six_actual.shape[0]),
            "sixty_msps_samples": int(sixty_actual.shape[0]),
            "six_msps_exact": bool(np.array_equal(six_actual, six_expected)),
            "sixty_msps_exact": bool(np.array_equal(sixty_actual, sixty_expected)),
            "six_msps_sha256_matches_manifest":
                bool(sha256_bytes(six_actual_bytes) == vector["rates"]["six_msps"]["sha256"]),
            "sixty_msps_sha256_matches_manifest":
                bool(sha256_bytes(sixty_actual_bytes) == vector["rates"]["sixty_msps"]["sha256"]),
            "rates_are_distinct": bool(
                vector["rates"]["six_msps"]["sha256"] != vector["rates"]["sixty_msps"]["sha256"]),
            "no_clipping_code": bool(
                int(six_actual.min()) > -2048 and int(six_actual.max()) < 2047
                and int(sixty_actual.min()) > -2048 and int(sixty_actual.max()) < 2047),
            "six_msps_unique_points": int(len({tuple(row) for row in six_actual.tolist()})),
            "sixty_msps_unique_points": int(len({tuple(row) for row in sixty_actual.tolist()})),
            "six_msps_expected_sha256": sha256_bytes(expected["six_bytes"]),
            "sixty_msps_expected_sha256": sha256_bytes(expected["sixty_bytes"]),
            "six_msps_actual_sha256": sha256_bytes(six_actual_bytes),
            "sixty_msps_actual_sha256": sha256_bytes(sixty_actual_bytes),
        }
        checks["status"] = "PASS" if all(
            checks[key] for key in (
                "six_msps_exact", "sixty_msps_exact",
                "six_msps_sha256_matches_manifest", "sixty_msps_sha256_matches_manifest",
                "rates_are_distinct", "no_clipping_code")) else "FAIL"
        if checks["status"] != "PASS":
            results["status"] = "FAIL"
        results["checks"].append(checks)
    results["frames_checked"] = len(results["checks"])
    results["all_six_msps_exact"] = all(c["six_msps_exact"] for c in results["checks"])
    results["all_sixty_msps_exact"] = all(c["sixty_msps_exact"] for c in results["checks"])
    results["note"] = ("Independent PRBS7 + MSB-first axis-table reconstruction; no classifier, "
                       "model artefact or generator import is used by this checker.")
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "independent_check.json"
    out.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": results["status"], "out": str(out), "checks": results["checks"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    result = check(args.data_dir, args.results_dir)
    print(json.dumps({"status": result["status"], "out": result["out"],
                      "modes": [{"mode": c["mode"], "six_msps_exact": c["six_msps_exact"],
                                 "sixty_msps_exact": c["sixty_msps_exact"]}
                                for c in result["checks"]]}, indent=1))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
