#!/usr/bin/env python3
"""Generate deterministic SYNTHETIC four-class test vectors.

The waveform is **not** a new generator framework.  The 6 MSPS representation is
produced by the existing product function
``pynq_overlay/sw/rf_iq_signal.generate_digital_frame`` (PRBS7, frozen
constellation mapping, RECT pulse shaping, s16_iq_le packing).  The 60 MSPS,
4096-complex-sample representation is the exact 10x sample-and-hold of that same
quantized 6 MSPS period, repeated to cover the frozen receiver window.  Thus both
rates share one baseband signal and are recorded separately.

No model prediction is used to choose or label a vector: truth comes from the
configuration plus the independent checker.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
PRODUCT_GENERATOR = ROOT / "pynq_overlay" / "sw" / "rf_iq_signal.py"
DEFAULT_CONFIG = PACKAGE / "config" / "vectors.json"
DEFAULT_OUT = PACKAGE / "data"


def sha256_bytes(data):
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_product_generator():
    if not PRODUCT_GENERATOR.is_file():
        raise SystemExit("existing signal generator not found: %s" % PRODUCT_GENERATOR)
    spec = importlib.util.spec_from_file_location("rf_iq_signal", PRODUCT_GENERATOR)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load existing signal generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_config(config):
    if config.get("schema") != "ml-fourclass-test-vectors-config-v1":
        raise SystemExit("invalid config schema")
    order = list(config.get("mode_order") or [])
    vectors = list(config.get("vectors") or [])
    if order != ["BPSK", "QPSK", "QAM16", "QAM64"]:
        raise SystemExit("mode_order must be BPSK/QPSK/QAM16/QAM64")
    if [row.get("mode") for row in vectors] != order:
        raise SystemExit("vectors must follow mode_order")
    signal = config["signal"]
    if signal.get("symbol_rate_hz") != 3_000_000:
        raise SystemExit("symbol_rate_hz must be 3,000,000")
    if signal.get("shaping") != "RECT":
        raise SystemExit("shaping must be RECT")
    if signal.get("differential_encoding") is not False:
        raise SystemExit("differential_encoding must be false")
    amplitude = signal.get("amplitude_codes")
    if type(amplitude) is not int or not 1 <= amplitude <= 2046:
        raise SystemExit("amplitude_codes must be 1..2046 to avoid the clipping code")
    for row in vectors:
        seed = row.get("seed")
        if type(seed) is not int or not 1 <= seed <= 127:
            raise SystemExit("PRBS seed must be 1..127: %r" % (seed,))
    rates = config["rates"]
    if rates["six_msps"]["sample_rate_hz"] != 6_000_000:
        raise SystemExit("six_msps sample_rate_hz must be 6,000,000")
    if rates["six_msps"]["samples_per_symbol"] != 2:
        raise SystemExit("six_msps samples_per_symbol must be 2")
    if rates["sixty_msps"]["sample_rate_hz"] != 60_000_000:
        raise SystemExit("sixty_msps sample_rate_hz must be 60,000,000")
    if rates["sixty_msps"]["samples_per_symbol"] != 20:
        raise SystemExit("sixty_msps samples_per_symbol must be 20")
    if rates["sixty_msps"]["samples"] != 4096:
        raise SystemExit("sixty_msps samples must be 4096")


def _derive_sixty(six_values, factor, repeats, samples):
    period = np.repeat(np.asarray(six_values, dtype=np.int16), factor, axis=0)
    if period.shape != (254 * factor, 2):
        raise RuntimeError("unexpected 6 MSPS period shape: %r" % (period.shape,))
    tiled = np.tile(period, (repeats, 1))
    return np.ascontiguousarray(tiled[:samples], dtype=np.int16)


def _rate_record(path, values, sample_rate_hz, sps, extra=None, base_dir=PACKAGE):
    raw = values.astype("<i2", copy=False).tobytes()
    record = {
        "path": str(Path(path).relative_to(Path(base_dir))).replace("\\", "/"),
        "format": "s16_iq_le",
        "sample_rate_hz": int(sample_rate_hz),
        "samples_per_symbol": int(sps),
        "samples": int(values.shape[0]),
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "min_code": int(values.min()),
        "max_code": int(values.max()),
        "max_abs_code": int(np.max(np.abs(values.astype(np.int32)))),
        "iq_order": "I,Q",
        "complex_sample_bytes": 4,
        "duration_us": round(values.shape[0] / float(sample_rate_hz) * 1e6, 9),
    }
    if extra:
        record.update(extra)
    return record


def generate(config_path=DEFAULT_CONFIG, out_dir=DEFAULT_OUT, product=None):
    config_path = Path(config_path).resolve()
    out_dir = Path(out_dir).resolve()
    config = load_json(config_path)
    validate_config(config)
    product = product or load_product_generator()
    signal = config["signal"]
    six_rate = config["rates"]["six_msps"]
    sixty_rate = config["rates"]["sixty_msps"]
    six_dir = out_dir / "synthetic_6msps"
    sixty_dir = out_dir / "synthetic_60msps"
    six_dir.mkdir(parents=True, exist_ok=True)
    sixty_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "ml-fourclass-test-vectors-manifest-v1",
        "synthetic": True,
        "real_capture": False,
        "counts_as_real_capture": False,
        "real_accuracy_applicable": False,
        "product_qualification": False,
        "warning": ("SYNTHETIC offline test vectors only. Do not count these frames as real "
                    "captures, real accuracy, or product qualification."),
        "config_path": str(config_path.relative_to(PACKAGE)).replace("\\", "/"),
        "config_sha256": sha256_file(config_path),
        "source_generator_path": str(PRODUCT_GENERATOR.relative_to(ROOT)).replace("\\", "/"),
        "source_generator_sha256": sha256_file(PRODUCT_GENERATOR),
        "source_generator_functions": [
            "prbs7_bits", "map_bits_to_symbol", "generate_digital_frame", "pack_iq"
        ],
        "signal": signal,
        "rates": {
            "six_msps": six_rate,
            "sixty_msps": sixty_rate,
            "rates_must_not_mix": True,
            "note": ("6 MSPS is the future Pluto/GNU Radio transmit representation; 60 MSPS is "
                     "the frozen model offline domain. They are recorded separately and are not "
                     "interchangeable."),
        },
        "constellation_mapping": config["constellation_mapping"],
        "truth_source": config["truth"],
        "vectors": [],
    }
    for row in config["vectors"]:
        mode = row["mode"]
        seed = row["seed"]
        args = {
            "mode": mode,
            "filter": signal["shaping"],
            "rolloff": signal["rolloff"],
            "seed": seed,
            "symbol_rate_hz": int(signal["symbol_rate_hz"]),
            "sample_rate_hz": int(six_rate["sample_rate_hz"]),
            "amplitude": int(signal["amplitude_codes"]),
            "impairment": None,
        }
        meta, payload = product.generate_digital_frame(args)
        if meta.get("format") != "s16_iq_le":
            raise RuntimeError("product generator returned unexpected format")
        if meta.get("samples") != six_rate["samples"]:
            raise RuntimeError("product generator returned %r samples, config expects %r"
                               % (meta.get("samples"), six_rate["samples"]))
        six_values = np.frombuffer(payload, dtype="<i2").reshape(-1, 2).copy()
        six_path = six_dir / (mode + ".iq")
        six_path.write_bytes(payload)
        sixty_values = _derive_sixty(
            six_values,
            factor=sixty_rate["upsample_factor_from_six_msps"],
            repeats=sixty_rate["periods_available"],
            samples=sixty_rate["samples"],
        )
        sixty_path = sixty_dir / (mode + ".iq")
        sixty_raw = sixty_values.astype("<i2", copy=False).tobytes()
        sixty_path.write_bytes(sixty_raw)
        for label, values in (("six_msps", six_values), ("sixty_msps", sixty_values)):
            if int(values.min()) <= -2048 or int(values.max()) >= 2047:
                raise RuntimeError("%s %s hits a clipping code" % (mode, label))
        six_record = _rate_record(
            six_path, six_values, six_rate["sample_rate_hz"],
            six_rate["samples_per_symbol"],
            {"prbs_period_symbols": six_rate["period_symbols"],
             "wraps_after_period": True},
            base_dir=out_dir,
        )
        sixty_record = _rate_record(
            sixty_path, sixty_values, sixty_rate["sample_rate_hz"],
            sixty_rate["samples_per_symbol"],
            {"window_offset_samples": sixty_rate["window_offset_samples"],
             "upsample_factor_from_six_msps": sixty_rate["upsample_factor_from_six_msps"],
             "six_msps_period_samples": sixty_rate["six_msps_period_samples"],
             "derived_from_6msps_sha256": six_record["sha256"],
             "derivation": "10x sample-and-hold of the 6 MSPS period, repeated to cover 4096 samples; first 4096 retained"},
            base_dir=out_dir,
        )
        manifest["vectors"].append({
            "id": "synthetic-" + mode.lower(),
            "truth_class": mode,
            "synthetic": True,
            "real_capture": False,
            "prbs_seed": int(seed),
            "prbs_symbols_per_period": int(signal["prbs_symbols_per_period"]),
            "differential_encoding": False,
            "amplitude_codes": int(signal["amplitude_codes"]),
            "amplitude_normalisation": signal["amplitude_normalisation"],
            "rates": {"six_msps": six_record, "sixty_msps": sixty_record},
        })
    # Prove that both representations have independent, separately recorded SHA-256.
    manifest["sha256_note"] = ("all file SHA-256 are over the exact on-disk s16_iq_le bytes; "
                               "the 60 MSPS hash is not the 6 MSPS hash")
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    return {"manifest": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
            "vectors": [v["truth_class"] for v in manifest["vectors"]]}


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
