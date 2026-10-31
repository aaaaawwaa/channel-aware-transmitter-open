#!/usr/bin/env python3
"""Generate the amplitude-60, 60 MSPS actual-TX payload set (offline only).

Reuses the existing product generator ``pynq_overlay/sw/rf_iq_signal.py`` via the
existing four-class helper.  Each class produces:

* a 2540-sample finite TX payload (127 PRBS symbols x 20 samples/symbol);
* a 4096-sample software reference window built as
  ``tile(tx_payload, 2)[:4096]`` (one full period + a 1556-sample prefix);
* an independently reconstructed symbol truth file.

No device, RF, training or model change is performed.  Amplitude-60 payloads are
explicitly not byte-identical to the older amplitude-1800 vectors.
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
FOURCLASS = PACKAGE.parent
ROOT = FOURCLASS.parents[1]
DEFAULT_CONFIG = PACKAGE / "config" / "hardware_amp60.json"
DEFAULT_OUT = PACKAGE / "outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.fourclass_test_vectors import generator as base_generator  # noqa: E402
from ml_training.fourclass_test_vectors import independent_check as base_checker  # noqa: E402


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_iq(path, values):
    raw = np.ascontiguousarray(values, dtype="<i2").tobytes()
    path.write_bytes(raw)
    return raw


def _independent_symbols(mode, seed, amplitude, sps):
    bps = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}[mode]
    bits = base_checker.prbs7_bits(seed, 127 * bps)
    symbols = np.asarray(
        [base_checker.map_symbol(mode, bits[i * bps:(i + 1) * bps])
         for i in range(127)], dtype=np.float64)
    peak = float(np.max(np.abs(symbols)))
    scaled = symbols * (float(amplitude) / peak)
    codes = np.rint(scaled).astype(np.int16)
    return {
        "bits": [int(bit) for bit in bits],
        "symbols": symbols.tolist(),
        "scaled_code_iq": codes.tolist(),
        "peak_component": peak,
        "bits_per_symbol": bps,
        "expected_tx": np.repeat(codes, int(sps), axis=0),
    }


def generate(config_path=DEFAULT_CONFIG, out_dir=DEFAULT_OUT):
    config_path = Path(config_path).resolve()
    out_dir = Path(out_dir).resolve()
    config = load_json(config_path)
    if [row["mode"] for row in config["vectors"]] != ["BPSK", "QPSK", "QAM16", "QAM64"]:
        raise SystemExit("vector order must be BPSK/QPSK/QAM16/QAM64")
    signal = config["signal"]
    if signal.get("amplitude") != 60:
        raise SystemExit("amplitude must be 60 for hardware_amp60")
    if signal.get("sample_rate_hz") != 60_000_000:
        raise SystemExit("sample_rate_hz must be 60,000,000")
    if signal.get("symbol_rate_hz") != 3_000_000:
        raise SystemExit("symbol_rate_hz must be 3,000,000")
    if signal.get("shaping") != "RECT":
        raise SystemExit("shaping must be RECT")
    if signal.get("differential_encoding") is not False:
        raise SystemExit("differential encoding must be off")
    if signal.get("rolloff") != "0.35":
        raise SystemExit("rolloff must match the final runner: 0.35")
    tx_spec = config["tx_payload"]
    ref_spec = config["reference_window"]
    if tx_spec["samples"] != 2540 or ref_spec["samples"] != 4096:
        raise SystemExit("expected 2540 TX samples and 4096 reference samples")
    product = base_generator.load_product_generator()
    tx_dir = out_dir / "tx_payload"
    ref_dir = out_dir / "reference_4096"
    truth_dir = out_dir / "truth"
    for path in (tx_dir, ref_dir, truth_dir):
        path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "ml-fourclass-hardware-amp60-manifest-v1",
        "offline_generated_tx_payload": True,
        "real_capture": False,
        "counts_as_real_capture": False,
        "training_use": False,
        "byte_equality_with_amp1800_vectors_claimed": False,
        "real_rf_iq_byte_equality_required": False,
        "warning": ("Offline-generated actual-TX payload candidates only. Amplitude-60 "
                    "payloads are not byte-identical to the existing amplitude-1800 vectors; "
                    "real RF received IQ is not expected to be byte-identical to the ideal "
                    "payload."),
        "config_path": str(config_path.relative_to(PACKAGE)).replace("\\", "/"),
        "config_sha256": sha256_file(config_path),
        "source_generator_path": str(
            (ROOT / "pynq_overlay" / "sw" / "rf_iq_signal.py").relative_to(ROOT)
        ).replace("\\", "/"),
        "source_generator_sha256": sha256_file(ROOT / "pynq_overlay" / "sw" / "rf_iq_signal.py"),
        "runner_config_evidence": config["runner_config_evidence"],
        "signal": signal,
        "tx_payload": tx_spec,
        "reference_window": ref_spec,
        "mapping": config["mapping"],
        "truth_source": config["truth"],
        "vectors": [],
    }
    for row in config["vectors"]:
        mode = row["mode"]
        seed = row["seed"]
        meta, payload = product.generate_digital_frame({
            "mode": mode,
            "filter": signal["shaping"],
            "rolloff": signal["rolloff"],
            "seed": seed,
            "symbol_rate_hz": signal["symbol_rate_hz"],
            "sample_rate_hz": signal["sample_rate_hz"],
            "amplitude": signal["amplitude"],
            "impairment": None,
        })
        if meta.get("samples") != tx_spec["samples"]:
            raise RuntimeError("product generator returned %r TX samples" % meta.get("samples"))
        tx_values = np.frombuffer(payload, dtype="<i2").reshape(-1, 2).copy()
        if tx_values.shape[0] != tx_spec["samples"]:
            raise RuntimeError("decoded TX sample count mismatch")
        reference = np.tile(tx_values, (2, 1))[:ref_spec["samples"]]
        independent = _independent_symbols(mode, seed, signal["amplitude"],
                                           signal["samples_per_symbol"])
        exact_tx = np.array_equal(tx_values, independent["expected_tx"])
        expected_reference = np.tile(independent["expected_tx"], (2, 1))[
            :ref_spec["samples"]]
        exact_ref = np.array_equal(reference, expected_reference)
        if not exact_tx or not exact_ref:
            raise RuntimeError("%s independent symbol reconstruction mismatch" % mode)
        for values, label in ((tx_values, "tx"), (reference, "reference")):
            if int(values.min()) <= -2048 or int(values.max()) >= 2047:
                raise RuntimeError("%s %s hits clipping code" % (mode, label))
        tx_path = tx_dir / (mode + ".iq")
        ref_path = ref_dir / (mode + ".iq")
        tx_raw = _write_iq(tx_path, tx_values)
        ref_raw = _write_iq(ref_path, reference)
        truth = {
            "mode": mode,
            "seed": seed,
            "amplitude": signal["amplitude"],
            "amplitude_normalisation": signal["amplitude_normalisation"],
            "bits_per_symbol": independent["bits_per_symbol"],
            "bits": independent["bits"],
            "symbols": independent["symbols"],
            "scaled_code_iq": independent["scaled_code_iq"],
            "component_peak": independent["peak_component"],
            "tx_period_samples": tx_spec["samples"],
            "reference_samples": ref_spec["samples"],
            "independent_of_classifier": True,
            "model_predictions_are_not_truth": True,
        }
        truth_path = truth_dir / (mode + ".symbols.json")
        truth_path.write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
        old_path = FOURCLASS / "data" / "synthetic_60msps" / (mode + ".iq")
        old_record = None
        if old_path.is_file():
            old_record = {
                "path": str(old_path.relative_to(FOURCLASS)).replace("\\", "/"),
                "sha256": sha256_file(old_path),
                "samples": old_path.stat().st_size // 4,
                "amplitude": 1800,
                "same_sha256": sha256_file(old_path) == sha256_bytes(ref_raw),
                "byte_equality_claimed": False,
            }
        vector = {
            "truth_class": mode,
            "seed": seed,
            "tx_payload": {
                "path": str(tx_path.relative_to(PACKAGE)).replace("\\", "/"),
                "samples": int(tx_values.shape[0]),
                "bytes": len(tx_raw),
                "format": signal["format"],
                "sample_rate_hz": signal["sample_rate_hz"],
                "samples_per_symbol": signal["samples_per_symbol"],
                "tx_period_symbols": int(signal["prbs_symbols_per_period"]),
                "wrap": bool(meta.get("wrap", True)),
                "sha256": sha256_bytes(tx_raw),
                "min_code": int(tx_values.min()),
                "max_code": int(tx_values.max()),
                "max_abs_code": int(np.max(np.abs(tx_values.astype(np.int32)))),
                "duration_us": round(tx_values.shape[0] / signal["sample_rate_hz"] * 1e6, 9),
            },
            "reference_4096": {
                "path": str(ref_path.relative_to(PACKAGE)).replace("\\", "/"),
                "samples": int(reference.shape[0]),
                "bytes": len(ref_raw),
                "sha256": sha256_bytes(ref_raw),
                "construction": ref_spec["construction"],
                "prefix_samples": int(reference.shape[0] - tx_values.shape[0]),
                "periods_in_window": round(reference.shape[0] / tx_values.shape[0], 10),
                "derived_from_tx_payload_sha256": sha256_bytes(tx_raw),
                "format": signal["format"],
                "sample_rate_hz": signal["sample_rate_hz"],
                "duration_us": round(reference.shape[0] / signal["sample_rate_hz"] * 1e6, 9),
            },
            "independent_truth": {
                "path": str(truth_path.relative_to(PACKAGE)).replace("\\", "/"),
                "sha256": sha256_file(truth_path),
                "symbol_count": len(independent["symbols"]),
                "bits_count": len(independent["bits"]),
                "exact_tx_payload_match": bool(exact_tx),
                "exact_reference_match": bool(exact_ref),
            },
            "old_amp1800_60msps": old_record,
        }
        if old_record and old_record["same_sha256"]:
            raise RuntimeError("amplitude-60 vector unexpectedly byte-equal to amp-1800")
        manifest["vectors"].append(vector)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    return {"manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "modes": [v["truth_class"] for v in manifest["vectors"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = generate(args.config, args.out_dir)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
