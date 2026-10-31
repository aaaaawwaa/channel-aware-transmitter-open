#!/usr/bin/env python
"""Independent reference check for the synthetic modulation dataset.

This module is intentionally **independent** of ``ml_training/data/generate.py``:
it does not import any generator helper.  It re-implements the frozen waveform /
impairment contract (CONTRACTS §3-§7) from scratch, rebuilds every frame from the
recorded npz metadata, and cross-checks the bit mapping against the frozen
product contract ``pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol``.

Checks performed (all hard unless marked):

* manifest schema/fields, config identity (sha256), frozen waveform block;
* split/session plan matches the frozen config, seed families are disjoint
  across splits;
* every npz file sha256 matches the manifest, required arrays/dtypes/shapes are
  present, file counts / clipped counts match;
* every frame: ``iq_sha256`` matches the stored bytes; frame/session/label/family
  metadata and the per-split parameter ranges are consistent;
* **exact per-frame reconstruction** (quantised bytes) from the recorded
  ``symbol_seed`` / ``noise_seed`` / params, for all 9200 formal frames;
* per-frame clipping flag consistency; saturation/rail-code verification for
  gate CLIP frames; SNR residual (noise variance + quantisation noise) check;
* amplitude clipping-safety cap check using the analytic c coefficients;
* cross-split and intra-split duplicate IQ detection, clipped statistics;
* exhaustive bit-pattern mapping vs the product mapper (2/4/16/64 patterns);
* noiseless / no-CFO subset: component peak == amplitude_codes, ``np.rint``
  ties-to-even behaviour, saturation behaviour;
* analytic constellation mean-power coefficients vs measured values.

CLI::

    python ml_training/data/reference_check.py \
        --dataset ml_training/datasets/sim-v1 \
        --out ml_training/data/reports/reference-check.json

Exit code 0 = PASS, 1 = FAIL (report still written).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPORT_SCHEMA = "ml-modrec-reference-check-v1"
CLASS_ORDER = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
BITS_PER_SYMBOL = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}
AXIS2 = (1.0, -1.0)
AXIS4 = (3.0, 1.0, -3.0, -1.0)
AXIS8 = (7.0, 5.0, 1.0, 3.0, -7.0, -5.0, -1.0, -3.0)
# Analytic mean-power coefficients c = E|x|^2 / amplitude_codes^2 under the
# frozen **complex-envelope peak** normalisation (CONTRACTS §4, revised
# 2026-10-20): BPSK 1, QPSK 1, QAM16 5/9, QAM64 3/7.
DECLARED_C = {
    "BPSK": 1.0,
    "QPSK": 1.0,
    "QAM16": 5.0 / 9.0,
    "QAM64": 3.0 / 7.0,
}
CLIPPING_HEADROOM_SIGMA_FALLBACK = None  # must be read from config, never assumed
MAX_CODE = 2047
MIN_CODE = -2048
SAFETY_CEILING = 2046
MAX_WINDOW_OFFSET = 19
CLASS_SPLITS = ("train", "val", "test")
GATE_SPLITS = ("gate_cal", "gate_test")
SPLIT_ORDER = CLASS_SPLITS + GATE_SPLITS

REQUIRED_NPZ_ARRAYS = (
    "X", "frame_id", "session_id", "label", "seed_family", "symbol_seed",
    "noise_seed", "snr_db", "amplitude_codes", "phase_rad", "cfo_hz",
    "window_offset", "noise_sigma_codes", "iq_sha256", "clipped",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failure(collector: dict, detail: str, limit: int = 8) -> None:
    collector["count"] += 1
    if len(collector["examples"]) < limit:
        collector["examples"].append(detail)


def _new_collector() -> dict:
    return {"count": 0, "examples": []}


def _collector_check(name: str, collector: dict, summary: str, extra: dict | None = None) -> dict:
    item = {
        "name": name,
        "passed": collector["count"] == 0,
        "summary": summary,
        "failures": collector["count"],
        "failure_examples": collector["examples"],
    }
    if extra:
        item.update(extra)
    return item


def snr_linear(snr_db: float) -> float:
    return 10.0 ** (float(snr_db) / 10.0)


def safe_amplitude_cap(mode: str, snr_db: float, headroom_sigma: float) -> int:
    """CONTRACTS §5 cap; headroom sigma is read from the frozen config."""
    return int(math.floor(SAFETY_CEILING / (1.0 + float(headroom_sigma)
                                            * math.sqrt(DECLARED_C[mode] / (2.0 * snr_linear(snr_db))))))


# ---------------------------------------------------------------------------
# independent constellation mapping (same contract, separate implementation)
# ---------------------------------------------------------------------------
def map_bits_to_symbol(mode: str, bits) -> tuple:
    """MSB-first bit -> (I, Q), frozen contract §4."""
    bits = tuple(int(bit) for bit in bits)
    bps = BITS_PER_SYMBOL[mode]
    if len(bits) != bps:
        raise ValueError("%s expects %d bits" % (mode, bps))
    if mode == "BPSK":
        return (AXIS2[bits[0]], 0.0)
    if mode == "QPSK":
        return (AXIS2[bits[0]], AXIS2[bits[1]])
    if mode == "QAM16":
        return (AXIS4[(bits[0] << 1) | bits[1]], AXIS4[(bits[2] << 1) | bits[3]])
    if mode == "QAM64":
        return (
            AXIS8[(bits[0] << 2) | (bits[1] << 1) | bits[2]],
            AXIS8[(bits[3] << 2) | (bits[4] << 1) | bits[5]],
        )
    raise ValueError("unknown constellation mode: %r" % (mode,))


def expected_symbol_table(mode: str) -> dict:
    """Literal expected table per mode (independent of the formula above)."""
    bps = BITS_PER_SYMBOL[mode]
    table = {}
    for bits in itertools.product((0, 1), repeat=bps):
        table[bits] = map_bits_to_symbol(mode, bits)
    return table


def bits_for_frame(symbol_seed: int, mode: str, n_symbols: int) -> np.ndarray:
    generator = np.random.default_rng(int(symbol_seed))
    return generator.integers(0, 2, size=int(n_symbols) * BITS_PER_SYMBOL[mode]).astype(np.uint8)


def symbols_from_bits(mode: str, bits: np.ndarray, n_symbols: int) -> np.ndarray:
    """Vectorised mapping implemented independently from the generator."""
    bps = BITS_PER_SYMBOL[mode]
    groups = np.asarray(bits, dtype=np.uint8).reshape(int(n_symbols), bps)
    axis2 = np.asarray(AXIS2, dtype=np.float64)
    axis4 = np.asarray(AXIS4, dtype=np.float64)
    axis8 = np.asarray(AXIS8, dtype=np.float64)
    if mode == "BPSK":
        return np.stack([axis2[groups[:, 0]], np.zeros(int(n_symbols))], axis=1)
    if mode == "QPSK":
        return np.stack([axis2[groups[:, 0]], axis2[groups[:, 1]]], axis=1)
    if mode == "QAM16":
        i_index = (groups[:, 0].astype(np.int64) << 1) | groups[:, 1]
        q_index = (groups[:, 2].astype(np.int64) << 1) | groups[:, 3]
        return np.stack([axis4[i_index], axis4[q_index]], axis=1)
    if mode == "QAM64":
        i_index = ((groups[:, 0].astype(np.int64) << 2)
                   | (groups[:, 1].astype(np.int64) << 1) | groups[:, 2])
        q_index = ((groups[:, 3].astype(np.int64) << 2)
                   | (groups[:, 4].astype(np.int64) << 1) | groups[:, 5])
        return np.stack([axis8[i_index], axis8[q_index]], axis=1)
    raise ValueError("unknown constellation mode: %r" % (mode,))


def quantize_reference(rx: np.ndarray) -> tuple:
    """np.rint ties-to-even + signed-12-bit saturation (returns rounded too)."""
    rounded = np.rint(np.asarray(rx, dtype=np.float64))
    clipped = bool(np.any(rounded > MAX_CODE) or np.any(rounded < MIN_CODE))
    X = np.clip(rounded, MIN_CODE, MAX_CODE).astype(np.int16)
    return X, clipped, rounded


# ---------------------------------------------------------------------------
# independent frame reconstruction
# ---------------------------------------------------------------------------
def _symbol_waveform(mode: str, symbol_seed: int, n_symbols: int, sps: int) -> tuple:
    bits = bits_for_frame(symbol_seed, mode, n_symbols)
    symbols = symbols_from_bits(mode, bits, n_symbols)
    return np.repeat(symbols, int(sps), axis=0), symbols


def _peak_normalise(wave: np.ndarray, amplitude_codes: int, normalisation: str) -> np.ndarray:
    if normalisation == "component":
        peak = float(np.max(np.abs(wave)))
    elif normalisation == "complex":
        peak = float(np.max(np.sqrt(wave[:, 0] * wave[:, 0] + wave[:, 1] * wave[:, 1])))
    else:
        raise ValueError("unknown peak normalisation %r" % (normalisation,))
    if peak <= 0.0:
        raise RuntimeError("degenerate waveform")
    return wave * (float(amplitude_codes) / peak)


def _peak_value(X: np.ndarray, normalisation: str) -> int:
    """Quantised frame peak under the declared normalisation convention."""
    if normalisation == "component":
        return int(np.max(np.abs(X)))
    envelope = np.sqrt(X[:, 0].astype(np.float64) ** 2 + X[:, 1].astype(np.float64) ** 2)
    return int(round(float(np.max(envelope))))


def _rotate(seg: np.ndarray, phase_rad: float, cfo_hz: float, fs_hz: float) -> np.ndarray:
    samples = int(seg.shape[0])
    n = np.arange(samples, dtype=np.float64)
    clean = (seg[:, 0] + 1j * seg[:, 1]) * np.exp(1j * float(phase_rad))
    clean = clean * np.exp(1j * (2.0 * np.pi * float(cfo_hz) * n / float(fs_hz)))
    return clean


def reconstruct_class_frame(record: dict, waveform: dict, normalisation: str) -> dict:
    """Rebuild one 4-class frame from recorded seeds/params (contract §5 order)."""
    mode = record["mode"]
    sps = int(waveform["sps"])
    samples = int(waveform["samples"])
    n_symbols = int(waveform["n_symbols"])
    fs_hz = float(waveform["fs_hz"])

    wave, symbols = _symbol_waveform(mode, record["symbol_seed"], n_symbols, sps)
    wave = _peak_normalise(wave, int(record["amplitude_codes"]), normalisation)
    offset = int(record["window_offset"])
    seg = wave[offset:offset + samples]
    clean = _rotate(seg, float(record["phase_rad"]), float(record["cfo_hz"]), fs_hz)
    p_sig = float(np.mean(clean.real * clean.real + clean.imag * clean.imag))
    sigma_formula = float(np.float32(math.sqrt(p_sig / (2.0 * snr_linear(float(record["snr_db"]))))))
    # Noise realisation is rebuilt from the *recorded* sigma (same float32 value the
    # generator used); sigma_formula is compared against it as a separate check.
    noise = (np.random.default_rng(int(record["noise_seed"])).standard_normal((samples, 2))
             * float(record["noise_sigma_codes"]))
    rx = np.empty((samples, 2), dtype=np.float64)
    rx[:, 0] = clean.real + noise[:, 0]
    rx[:, 1] = clean.imag + noise[:, 1]
    X, clipped, rounded = quantize_reference(rx)
    return {
        "X": X, "clipped": clipped, "rounded": rounded,
        "clean": clean, "p_sig": p_sig, "sigma_formula": sigma_formula,
        "symbols": symbols,
    }


def reconstruct_gate_frame(record: dict, waveform: dict, normalisation: str) -> dict:
    """Rebuild one gate frame (kind-specific construction, see generate.py docs)."""
    kind = record["kind"]
    samples = int(waveform["samples"])
    sps = int(waveform["sps"])
    n_symbols = int(waveform["n_symbols"])
    fs_hz = float(waveform["fs_hz"])
    sigma = float(record["noise_sigma_codes"])
    if kind in ("OFF", "NOISE"):
        noise = np.random.default_rng(int(record["noise_seed"])).standard_normal((samples, 2)) * sigma
        X, clipped, rounded = quantize_reference(noise)
        return {"X": X, "clipped": clipped, "rounded": rounded}
    if kind == "TONE":
        n = np.arange(samples, dtype=np.float64)
        tone = float(record["amplitude_codes"]) * np.exp(
            1j * (2.0 * np.pi * float(record["cfo_hz"]) * n / float(fs_hz) + float(record["phase_rad"])))
        rx = np.stack([tone.real, tone.imag], axis=1)
        X, clipped, rounded = quantize_reference(rx)
        return {"X": X, "clipped": clipped, "rounded": rounded}
    if kind == "CLIP":
        wave, _symbols = _symbol_waveform("BPSK", record["symbol_seed"], n_symbols, sps)
        wave = _peak_normalise(wave, int(record["amplitude_codes"]), normalisation)
        offset = int(record["window_offset"])
        seg = wave[offset:offset + samples]
        # zero phase / zero CFO / no AWGN by construction (documented gate contract)
        rx = np.stack([seg[:, 0], seg[:, 1]], axis=1)
        X, clipped, rounded = quantize_reference(rx)
        return {"X": X, "clipped": clipped, "rounded": rounded}
    raise ValueError("unknown gate kind: %r" % (kind,))


# ---------------------------------------------------------------------------
# product mapper import (read-only, mapping cross-check only)
# ---------------------------------------------------------------------------
def load_product_mapper(repo_root: Path, product_rel: str = "pynq_overlay/sw/rf_iq_signal.py"):
    path = repo_root / product_rel
    if not path.is_file():
        return None, "missing: %s" % (path,)
    try:
        spec = importlib.util.spec_from_file_location("_rf_iq_signal_product_reference", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.map_bits_to_symbol, None
    except Exception as error:  # pragma: no cover - defensive
        return None, "import failed: %r" % (error,)


# ---------------------------------------------------------------------------
# manifest / config planning helpers
# ---------------------------------------------------------------------------
def expected_session_map(config: dict, variant: str) -> dict:
    """Independent re-derivation of the frozen split/session plan."""
    splits = config["dataset"][variant]["splits"]
    sessions = {}
    for split in ("train", "val"):
        cfg = splits[split]
        for label in CLASS_ORDER:
            for family in cfg["seed_families"]:
                session_id = "%s-%s-%s" % (split, label, family)
                sessions[session_id] = {
                    "split": split, "label": label, "kind": None, "seed_family": family,
                    "frames": int(cfg["frames_per_session"]), "snr_db": None,
                }
    test_cfg = splits["test"]
    grid = [float(value) for value in test_cfg["snr_db_grid"]]
    families = list(test_cfg["seed_families"])
    for label_index, label in enumerate(CLASS_ORDER):
        for snr_index, snr_db in enumerate(grid):
            family = families[(label_index * len(grid) + snr_index) % len(families)]
            session_id = "test-%s-snr%+.0f-%s" % (label, snr_db, family)
            sessions[session_id] = {
                "split": "test", "label": label, "kind": None, "seed_family": family,
                "frames": int(test_cfg["frames_per_class_per_snr"]), "snr_db": float(snr_db),
            }
    for split in GATE_SPLITS:
        cfg = splits[split]
        families = list(cfg["families"])
        frames_per_kind = int(cfg["frames_per_kind"])
        counts = [frames_per_kind // len(families)
                  + (1 if index < frames_per_kind % len(families) else 0)
                  for index in range(len(families))]
        for kind in GATE_KINDS:
            for family_index, family in enumerate(families):
                if counts[family_index] == 0:
                    continue
                session_id = "%s-%s-%s" % (split, kind, family)
                sessions[session_id] = {
                    "split": split, "label": kind, "kind": kind, "seed_family": family,
                    "frames": int(counts[family_index]), "snr_db": None,
                }
    return sessions


def expected_file_plan(config: dict, variant: str) -> list:
    plans = []
    for split in CLASS_SPLITS:
        for label in CLASS_ORDER:
            plans.append({"path": "frames/%s-%s.npz" % (split, label), "split": split,
                          "label": label, "kind": None})
    for split in GATE_SPLITS:
        for kind in GATE_KINDS:
            plans.append({"path": "gate/%s-%s.npz" % (split, kind), "split": split,
                          "label": kind, "kind": kind})
    return plans


def determine_variant(config: dict, manifest: dict) -> str | None:
    variant = manifest.get("generator", {}).get("variant")
    if variant in ("formal", "smoke"):
        return variant
    for candidate in ("formal", "smoke"):
        if config["dataset"].get(candidate, {}).get("dataset_id") == manifest.get("dataset_id"):
            return candidate
    return None


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_manifest_top(ctx: dict) -> dict:
    manifest = ctx["manifest"]
    collector = _new_collector()
    for key in ("schema", "dataset_id", "created_utc", "config_path", "config_sha256",
                "numpy_version", "waveform", "classes", "gate_kinds", "splits", "files", "summary"):
        if key not in manifest:
            _failure(collector, "missing top-level field %r" % (key,))
    if manifest.get("schema") != "ml-modrec-synthetic-v1":
        _failure(collector, "schema=%r" % (manifest.get("schema"),))
    if manifest.get("dataset_id") != ctx["dataset_dir"].name:
        _failure(collector, "dataset_id %r != directory name %r"
                 % (manifest.get("dataset_id"), ctx["dataset_dir"].name))
    if list(manifest.get("classes", [])) != list(CLASS_ORDER):
        _failure(collector, "classes=%r" % (manifest.get("classes"),))
    if list(manifest.get("gate_kinds", [])) != list(GATE_KINDS):
        _failure(collector, "gate_kinds=%r" % (manifest.get("gate_kinds"),))
    return _collector_check("manifest_top_level", collector,
                            "required frozen top-level manifest fields present",
                            {"manifest_sha256": ctx["manifest_sha256"]})


def check_config_identity(ctx: dict) -> dict:
    collector = _new_collector()
    manifest = ctx["manifest"]
    config_path = ctx["config_path"]
    extra = {"config_path": config_path.as_posix() if config_path else None}
    if config_path is None or not config_path.is_file():
        _failure(collector, "config file not found: %r" % (str(config_path),))
        return _collector_check("config_identity", collector, "config sha256/identity", extra)
    actual_sha = sha256_file(config_path)
    extra["config_sha256"] = actual_sha
    if manifest.get("config_sha256") != actual_sha:
        _failure(collector, "manifest config_sha256 %r != file sha256 %r"
                 % (manifest.get("config_sha256"), actual_sha))
    declared_path = manifest.get("config_path")
    if declared_path:
        try:
            declared = (ctx["repo_root"] / declared_path).resolve()
        except OSError:
            declared = None
        if declared is not None and declared != config_path.resolve():
            _failure(collector, "manifest config_path %r resolves to %r, expected %r"
                     % (declared_path, declared, config_path.resolve()))
    if ctx["config"] is None:
        _failure(collector, "config JSON could not be loaded")
    return _collector_check("config_identity", collector,
                            "config file exists and sha256 matches manifest", extra)


def check_waveform_frozen(ctx: dict) -> dict:
    collector = _new_collector()
    manifest = ctx["manifest"]
    config = ctx["config"]
    extra = {}
    if config is not None:
        expected = config["dataset"]["waveform"]
        actual = manifest.get("waveform", {})
        extra["waveform"] = actual
        for key in ("fs_hz", "symbol_rate_hz", "sps", "shaping", "samples", "n_symbols"):
            if actual.get(key) != expected.get(key):
                _failure(collector, "manifest.waveform.%s=%r != config %r"
                         % (key, actual.get(key), expected.get(key)))
        if int(actual.get("n_symbols", 0)) != int(
                math.ceil((int(actual.get("samples", 0)) + MAX_WINDOW_OFFSET) / int(actual.get("sps", 1)))) + 1:
            _failure(collector, "n_symbols does not follow ceil((samples+max_offset)/sps)+1")
        if int(actual.get("samples", 0)) + MAX_WINDOW_OFFSET > int(actual.get("n_symbols", 0)) * int(actual.get("sps", 1)):
            _failure(collector, "waveform shorter than samples+max window offset")
        # clipping headroom is frozen in config and must be echoed by the manifest
        headroom = config.get("dataset", {}).get("impairment", {}).get("clipping_headroom_sigma")
        extra["clipping_headroom_sigma"] = headroom
        if not (isinstance(headroom, (int, float)) and not isinstance(headroom, bool) and headroom > 0):
            _failure(collector, "config impairment.clipping_headroom_sigma missing/invalid")
        declared = manifest.get("generator", {}).get("clipping_headroom_sigma")
        if declared is None:
            _failure(collector, "manifest generator.clipping_headroom_sigma missing")
        elif headroom is not None and abs(float(declared) - float(headroom)) > 1e-12:
            _failure(collector, "manifest clipping_headroom_sigma %r != config %r"
                     % (declared, headroom))
        # the receive-simulation peak normalisation must be declared in the manifest
        normalisation = manifest.get("generator", {}).get("peak_normalisation")
        extra["peak_normalisation"] = normalisation
        if normalisation not in ("component", "complex"):
            _failure(collector, "manifest generator.peak_normalisation %r missing/invalid"
                     % (normalisation,))
    return _collector_check("waveform_frozen", collector,
                            "waveform block and clipping headroom match frozen contract/config", extra)


def check_split_plan(ctx: dict) -> dict:
    collector = _new_collector()
    config = ctx["config"]
    manifest = ctx["manifest"]
    variant = ctx["variant"]
    extra = {}
    if config is None or variant is None:
        _failure(collector, "cannot resolve config variant")
        return _collector_check("split_plan", collector, "split/session plan", extra)
    expected_sessions = expected_session_map(config, variant)
    expected_files = expected_file_plan(config, variant)
    extra["expected_total_frames"] = int(sum(item["frames"] for item in expected_sessions.values()))

    manifest_splits = manifest.get("splits", {})
    if set(manifest_splits) != set(SPLIT_ORDER):
        _failure(collector, "manifest splits %r != %r" % (sorted(manifest_splits), sorted(SPLIT_ORDER)))
    families_by_split = {}
    for split in SPLIT_ORDER:
        meta = manifest_splits.get(split, {})
        declared_families = list(meta.get("seed_families", []))
        families_by_split[split] = set(declared_families)
        config_splits = config["dataset"][variant]["splits"][split]
        config_families = list(config_splits.get("seed_families", config_splits.get("families", [])))
        if declared_families != config_families:
            _failure(collector, "%s seed_families %r != config %r"
                     % (split, declared_families, config_families))
        sessions = meta.get("sessions", [])
        seen_ids = set()
        for session in sessions:
            session_id = session.get("session_id")
            if session_id in seen_ids:
                _failure(collector, "duplicate manifest session %r" % (session_id,))
            seen_ids.add(session_id)
            expected = expected_sessions.get(session_id)
            if expected is None:
                _failure(collector, "manifest session %r not part of frozen plan" % (session_id,))
                continue
            if expected["split"] != split:
                _failure(collector, "session %r listed under split %r" % (session_id, split))
            for key, exp_key in (("label", "label"), ("seed_family", "seed_family"), ("frames", "frames")):
                if session.get(key) != expected[exp_key]:
                    _failure(collector, "session %r .%s=%r != expected %r"
                             % (session_id, key, session.get(key), expected[exp_key]))
            if expected["kind"] is not None and session.get("kind") != expected["kind"]:
                _failure(collector, "session %r .kind=%r != expected %r"
                         % (session_id, session.get("kind"), expected["kind"]))
            if expected["snr_db"] is not None:
                grid = session.get("snr_db_grid")
                if grid != [expected["snr_db"]]:
                    _failure(collector, "session %r snr_db_grid=%r != expected [%r]"
                             % (session_id, grid, expected["snr_db"]))
        expected_ids = {sid for sid, item in expected_sessions.items() if item["split"] == split}
        missing = expected_ids - seen_ids
        if missing:
            _failure(collector, "%s missing %d planned sessions, e.g. %r"
                     % (split, len(missing), sorted(missing)[:3]))
        # split-level snr declaration must match the frozen config block
        if split in ("train", "val"):
            if meta.get("snr") != config_splits.get("snr"):
                _failure(collector, "%s snr block %r != config %r"
                         % (split, meta.get("snr"), config_splits.get("snr")))
        elif split == "test":
            declared_grid = list(meta.get("snr_db_grid", []))
            config_grid = [float(value) for value in config_splits.get("snr_db_grid", [])]
            if declared_grid != config_grid:
                _failure(collector, "test snr_db_grid %r != config %r"
                         % (declared_grid, config_grid))
    # families must be pairwise disjoint across splits
    for left_index, left in enumerate(SPLIT_ORDER):
        for right in SPLIT_ORDER[left_index + 1:]:
            overlap = families_by_split.get(left, set()) & families_by_split.get(right, set())
            if overlap:
                _failure(collector, "seed families shared by %s and %s: %r"
                         % (left, right, sorted(overlap)))
    files = manifest.get("files", [])
    declared_paths = [entry.get("path") for entry in files]
    expected_paths = [plan["path"] for plan in expected_files]
    if declared_paths != expected_paths:
        _failure(collector, "manifest files order/set %r != frozen plan %r"
                 % (declared_paths, expected_paths))
    return _collector_check("split_plan", collector,
                            "split/session/file plan matches frozen config; families disjoint", extra)


def validate_npz_arrays(arrays: dict, samples: int) -> list:
    problems = []
    for name in REQUIRED_NPZ_ARRAYS:
        if name not in arrays:
            problems.append("missing array %r" % (name,))
    if problems:
        return problems
    x = arrays["X"]
    if x.dtype != np.dtype(np.int16):
        problems.append("X dtype %r != int16" % (x.dtype,))
    if x.ndim != 3 or x.shape[1:] != (samples, 2):
        problems.append("X shape %r != (N, %d, 2)" % (tuple(x.shape), samples))
    for name in ("symbol_seed", "noise_seed"):
        if arrays[name].dtype != np.dtype(np.uint64):
            problems.append("%s dtype %r != uint64" % (name, arrays[name].dtype))
    for name in ("snr_db", "phase_rad", "cfo_hz", "noise_sigma_codes"):
        if arrays[name].dtype != np.dtype(np.float32):
            problems.append("%s dtype %r != float32" % (name, arrays[name].dtype))
    for name in ("amplitude_codes", "window_offset"):
        if arrays[name].dtype != np.dtype(np.int16):
            problems.append("%s dtype %r != int16" % (name, arrays[name].dtype))
    if arrays["clipped"].dtype != np.dtype(bool):
        problems.append("clipped dtype %r != bool" % (arrays["clipped"].dtype,))
    for name in ("frame_id", "session_id", "label", "seed_family", "iq_sha256"):
        if arrays[name].dtype.kind != "U":
            problems.append("%s dtype %r is not unicode" % (name, arrays[name].dtype))
    length = x.shape[0] if x.ndim >= 1 else -1
    for name in REQUIRED_NPZ_ARRAYS:
        array = arrays[name]
        if array.shape[0] != length:
            problems.append("%s length %d != %d" % (name, array.shape[0], length))
    return problems


def scan_frames(ctx: dict) -> dict:
    """Single pass over every frame: reconstruction + all per-frame checks."""
    manifest = ctx["manifest"]
    waveform = manifest["waveform"]
    samples = int(waveform["samples"])
    config = ctx["config"]
    impairment = config["dataset"]["impairment"] if config else {}
    variant = ctx["variant"]
    expected_sessions = ctx["expected_sessions"]
    splits_cfg = config["dataset"][variant]["splits"] if (config and variant) else {}

    collectors = {
        "file_sha256": _new_collector(),
        "npz_arrays": _new_collector(),
        "frame_metadata": _new_collector(),
        "reconstruction": _new_collector(),
        "iq_sha256": _new_collector(),
        "clipping_flags": _new_collector(),
        "param_ranges": _new_collector(),
        "amplitude_safety": _new_collector(),
        "snr_residual": _new_collector(),
        "snr_grid": _new_collector(),
        "seed_uniqueness": _new_collector(),
        "gate_clip_rails": _new_collector(),
        "rotated_component_bound": _new_collector(),
    }
    stats = {
        "frames": 0,
        "clipped": 0,
        "reconstruction_checked": 0,
        "reconstruction_failures": 0,
        "snr_residual_checked": 0,
        "snr_residual_max_rel_dev": 0.0,
        "snr_sigma_formula_max_abs_diff": 0.0,
        "residual_var_ratio_min": float("inf"),
        "residual_var_ratio_max": 0.0,
        "clipped_by_split": {split: 0 for split in SPLIT_ORDER},
        "clipped_by_class": {label: 0 for label in CLASS_ORDER},
        "frames_by_class": {label: 0 for label in CLASS_ORDER},
        "gate_clipped_by_kind": {kind: 0 for kind in GATE_KINDS},
        "computed_iq": [],
        "symbol_seeds": [],
        "noise_seeds": [],
        "c_measured_sum": {label: 0.0 for label in CLASS_ORDER},
        "c_measured_count": {label: 0 for label in CLASS_ORDER},
    }

    session_progress = {}
    manifest_splits = manifest.get("splits", {})
    declared_clipped_summary = manifest.get("summary", {}).get("clipped_frames_by_split", {})
    headroom_sigma = ctx.get("headroom_sigma")
    if headroom_sigma is None:
        _failure(collectors["amplitude_safety"],
                 "config impairment.clipping_headroom_sigma unavailable; cap check impossible")
    normalisation = ctx.get("normalisation", "complex")

    for entry in manifest["files"]:
        path = ctx["dataset_dir"] / entry["path"]
        split = entry["split"]
        label = entry["label"]
        split_cfg = splits_cfg.get(split, {})
        if not path.is_file():
            _failure(collectors["file_sha256"], "missing npz file %r" % (entry["path"],))
            continue
        actual_sha = sha256_file(path)
        if entry.get("sha256") != actual_sha:
            _failure(collectors["file_sha256"],
                     "%s sha256 %r != manifest %r" % (entry["path"], actual_sha, entry.get("sha256")))
        with np.load(path, allow_pickle=False) as npz:
            arrays = {name: npz[name] for name in npz.files}
        problems = validate_npz_arrays(arrays, samples)
        for problem in problems:
            _failure(collectors["npz_arrays"], "%s: %s" % (entry["path"], problem))
        if problems:
            continue
        count = int(arrays["X"].shape[0])
        if entry.get("count") != count:
            _failure(collectors["npz_arrays"], "%s count %r != npz %d"
                     % (entry["path"], entry.get("count"), count))
        stored_clipped = int(np.count_nonzero(arrays["clipped"]))
        if entry.get("clipped_count") != stored_clipped:
            _failure(collectors["npz_arrays"], "%s clipped_count %r != npz %d"
                     % (entry["path"], entry.get("clipped_count"), stored_clipped))

        is_class_file = split in CLASS_SPLITS
        for index in range(count):
            frame_id = str(arrays["frame_id"][index])
            session_id = str(arrays["session_id"][index])
            frame_label = str(arrays["label"][index])
            seed_family = str(arrays["seed_family"][index])
            stats["frames"] += 1

            # ---- metadata -------------------------------------------------
            session = expected_sessions.get(session_id)
            if session is None:
                _failure(collectors["frame_metadata"], "%s unknown session %r" % (frame_id, session_id))
                continue
            progress_key = session_id
            next_index = session_progress.get(progress_key, 0)
            if next_index >= session["frames"]:
                _failure(collectors["frame_metadata"], "%s session %r has too many frames"
                         % (frame_id, session_id))
            elif frame_id != "%s-%04d" % (session_id, next_index):
                _failure(collectors["frame_metadata"],
                         "%s expected frame_id %r (order/index contract)"
                         % (frame_id, "%s-%04d" % (session_id, next_index)))
            session_progress[progress_key] = next_index + 1
            if session["split"] != split:
                _failure(collectors["frame_metadata"], "%s session split mismatch" % (frame_id,))
            if frame_label != (label if is_class_file else session["label"]):
                _failure(collectors["frame_metadata"], "%s label %r != file label %r"
                         % (frame_id, frame_label, label))
            if seed_family != session["seed_family"]:
                _failure(collectors["frame_metadata"], "%s seed_family %r != session %r"
                         % (frame_id, seed_family, session["seed_family"]))
            if is_class_file and session["kind"] is not None:
                _failure(collectors["frame_metadata"], "%s class frame has gate session %r"
                         % (frame_id, session_id))

            # ---- exact reconstruction ------------------------------------
            record = {
                "mode": session["label"] if is_class_file else session["kind"],
                "kind": session["kind"],
                "symbol_seed": int(arrays["symbol_seed"][index]),
                "noise_seed": int(arrays["noise_seed"][index]),
                "snr_db": float(arrays["snr_db"][index]),
                "amplitude_codes": int(arrays["amplitude_codes"][index]),
                "phase_rad": float(arrays["phase_rad"][index]),
                "cfo_hz": float(arrays["cfo_hz"][index]),
                "window_offset": int(arrays["window_offset"][index]),
                "noise_sigma_codes": float(arrays["noise_sigma_codes"][index]),
            }
            if is_class_file:
                rebuilt = reconstruct_class_frame(record, waveform, normalisation)
            else:
                rebuilt = reconstruct_gate_frame(record, waveform, normalisation)
            stats["reconstruction_checked"] += 1
            stored_X = np.asarray(arrays["X"][index], dtype=np.int16)
            if not np.array_equal(stored_X, rebuilt["X"]):
                difference = np.abs(stored_X.astype(np.int64) - rebuilt["X"].astype(np.int64))
                _failure(collectors["reconstruction"],
                         "%s byte mismatch: %d samples differ, max abs diff %d"
                         % (frame_id, int(np.count_nonzero(difference)), int(difference.max())))
                stats["reconstruction_failures"] += 1
            # clipping flag consistency (derived from pre-saturation rounded values)
            stored_flag = bool(arrays["clipped"][index])
            if stored_flag != bool(rebuilt["clipped"]):
                _failure(collectors["clipping_flags"], "%s clipped flag %r != reference %r"
                         % (frame_id, stored_flag, rebuilt["clipped"]))
            # after normalisation + rotation the component magnitude must never
            # exceed amplitude_codes (complex-envelope bound; tolerance 1 code)
            if rebuilt.get("clean") is not None and record["amplitude_codes"] > 0:
                component_peak = float(max(np.max(np.abs(rebuilt["clean"].real)),
                                           np.max(np.abs(rebuilt["clean"].imag))))
                if component_peak > float(record["amplitude_codes"]) + 1.0:
                    _failure(collectors["rotated_component_bound"],
                             "%s rotated component peak %.6f > amplitude %d + 1"
                             % (frame_id, component_peak, record["amplitude_codes"]))

            # ---- self sha256 ---------------------------------------------
            computed_digest = sha256_bytes(np.ascontiguousarray(stored_X, dtype="<i2").tobytes())
            if str(arrays["iq_sha256"][index]) != computed_digest:
                _failure(collectors["iq_sha256"], "%s iq_sha256 mismatch" % (frame_id,))
            stats["computed_iq"].append((computed_digest, split, frame_id))

            # ---- seeds / clipped stats ------------------------------------
            stats["symbol_seeds"].append(record["symbol_seed"])
            stats["noise_seeds"].append(record["noise_seed"])
            if is_class_file:
                stats["frames_by_class"][session["label"]] += 1
                stats["clipped_by_class"][session["label"]] += 1 if stored_flag else 0
                if stored_flag:
                    stats["clipped_by_split"][split] += 1
            else:
                stats["gate_clipped_by_kind"][session["kind"]] += 1 if stored_flag else 0
                if stored_flag:
                    stats["clipped_by_split"][split] += 1
            if stored_flag:
                stats["clipped"] += 1

            # ---- parameter ranges ----------------------------------------
            if not math.isfinite(record["snr_db"]):
                if is_class_file:
                    _failure(collectors["param_ranges"], "%s non-finite snr_db" % (frame_id,))
            if not math.isfinite(record["phase_rad"]):
                _failure(collectors["param_ranges"], "%s non-finite phase_rad" % (frame_id,))
            if not math.isfinite(record["cfo_hz"]):
                _failure(collectors["param_ranges"], "%s non-finite cfo_hz" % (frame_id,))
            if record["window_offset"] < 0 or record["window_offset"] > MAX_WINDOW_OFFSET:
                _failure(collectors["param_ranges"], "%s window_offset %d out of [0,19]"
                         % (frame_id, record["window_offset"]))
            if record["noise_sigma_codes"] < 0.0 or not math.isfinite(record["noise_sigma_codes"]):
                _failure(collectors["param_ranges"], "%s invalid noise_sigma_codes %r"
                         % (frame_id, record["noise_sigma_codes"]))
            if not (0.0 <= record["phase_rad"] <= 2.0 * math.pi):
                _failure(collectors["param_ranges"], "%s phase_rad %r outside [0,2pi]"
                         % (frame_id, record["phase_rad"]))
            if is_class_file:
                # snr range / fixed grid
                split_cfg_snr = split_cfg.get("snr") if split in ("train", "val") else None
                if split == "test":
                    if abs(record["snr_db"] - session["snr_db"]) > 1e-4:
                        _failure(collectors["snr_grid"], "%s snr_db %r != grid %r"
                                 % (frame_id, record["snr_db"], session["snr_db"]))
                elif split_cfg_snr:
                    low, high = float(split_cfg_snr["low"]), float(split_cfg_snr["high"])
                    if not (low - 1e-6 <= record["snr_db"] <= high + 1e-6):
                        _failure(collectors["snr_grid"], "%s snr_db %r outside %r..%r"
                                 % (frame_id, record["snr_db"], low, high))
                amp_low, amp_high = impairment.get("amplitude_codes_uniform", [150, 1200])
                if not (int(amp_low) <= record["amplitude_codes"] <= int(amp_high)):
                    _failure(collectors["param_ranges"],
                             "%s amplitude_codes %d outside drawn range [%d,%d]"
                             % (frame_id, record["amplitude_codes"], amp_low, amp_high))
                cap = safe_amplitude_cap(session["label"], record["snr_db"], ctx["headroom_sigma"])
                if record["amplitude_codes"] > cap:
                    _failure(collectors["amplitude_safety"],
                             "%s amplitude %d > frozen safety cap %d"
                             % (frame_id, record["amplitude_codes"], cap))
                if cap < 64:
                    _failure(collectors["amplitude_safety"],
                             "%s safety cap %d < 64 (configuration error)" % (frame_id, cap))
                cfo_low, cfo_high = impairment.get("cfo_hz_uniform", [-20000.0, 20000.0])
                if not (float(cfo_low) - 1e-3 <= record["cfo_hz"] <= float(cfo_high) + 1e-3):
                    _failure(collectors["param_ranges"], "%s cfo_hz %r outside %r..%r"
                             % (frame_id, record["cfo_hz"], cfo_low, cfo_high))
                # sigma formula must reproduce the stored float32 sigma exactly
                sigma_diff = abs(rebuilt["sigma_formula"] - record["noise_sigma_codes"])
                stats["snr_sigma_formula_max_abs_diff"] = max(
                    stats["snr_sigma_formula_max_abs_diff"], sigma_diff)
                if not np.isclose(np.float32(rebuilt["sigma_formula"]),
                                  np.float32(record["noise_sigma_codes"]), rtol=1e-6, atol=0.0):
                    _failure(collectors["param_ranges"],
                             "%s noise_sigma_codes %r != formula %r"
                             % (frame_id, record["noise_sigma_codes"], rebuilt["sigma_formula"]))
                # SNR residual (skip saturated frames: saturation breaks the residual)
                clean = rebuilt["clean"]
                if not stored_flag and record["noise_sigma_codes"] > 0.0:
                    sigma = float(record["noise_sigma_codes"])
                    residual = stored_X.astype(np.float64) - np.stack(
                        [clean.real, clean.imag], axis=1)
                    variance = float(np.mean(residual * residual))
                    expected = sigma * sigma + 1.0 / 12.0
                    ratio = variance / expected if expected > 0 else 0.0
                    stats["snr_residual_checked"] += 1
                    stats["residual_var_ratio_min"] = min(stats["residual_var_ratio_min"], ratio)
                    stats["residual_var_ratio_max"] = max(stats["residual_var_ratio_max"], ratio)
                    deviation = abs(variance - expected) / expected
                    stats["snr_residual_max_rel_dev"] = max(stats["snr_residual_max_rel_dev"], deviation)
                    if not (0.75 <= ratio <= 1.25):
                        _failure(collectors["snr_residual"],
                                 "%s residual variance ratio %.3f (sigma=%.3f, var=%.3f, expected=%.3f)"
                                 % (frame_id, ratio, sigma, variance, expected))
                # measured constellation power coefficient (exact, from rebuilt clean)
                amplitude = float(record["amplitude_codes"])
                c_measured = float(np.mean(clean.real * clean.real + clean.imag * clean.imag)) / (amplitude * amplitude)
                stats["c_measured_sum"][session["label"]] += c_measured
                stats["c_measured_count"][session["label"]] += 1
            else:
                # gate parameter ranges from config
                kind = session["kind"]
                if not math.isnan(record["snr_db"]):
                    _failure(collectors["param_ranges"], "%s gate snr_db should be NaN" % (frame_id,))
                if kind in ("OFF", "NOISE"):
                    low, high = (impairment.get("gate_off_sigma_codes_uniform", [0.5, 30.0])
                                 if kind == "OFF" else impairment.get("gate_noise_sigma_codes_uniform", [50.0, 600.0]))
                    if not (float(low) - 1e-4 <= record["noise_sigma_codes"] <= float(high) + 1e-4):
                        _failure(collectors["param_ranges"], "%s sigma %r outside %r..%r"
                                 % (frame_id, record["noise_sigma_codes"], low, high))
                    if record["amplitude_codes"] != 0:
                        _failure(collectors["param_ranges"], "%s OFF/NOISE amplitude != 0" % (frame_id,))
                elif kind == "TONE":
                    tone_cfg = impairment.get("gate_tone", {})
                    amp_low, amp_high = tone_cfg.get("amplitude_codes_uniform", [200, 1200])
                    f_low, f_high = tone_cfg.get("freq_hz_uniform", [-5e6, 5e6])
                    if not (int(amp_low) <= record["amplitude_codes"] <= int(amp_high)):
                        _failure(collectors["param_ranges"], "%s TONE amplitude %d outside range"
                                 % (frame_id, record["amplitude_codes"]))
                    if not (float(f_low) - 1e-3 <= record["cfo_hz"] <= float(f_high) + 1e-3):
                        _failure(collectors["param_ranges"], "%s TONE freq %r outside range"
                                 % (frame_id, record["cfo_hz"]))
                    if record["noise_sigma_codes"] != 0.0:
                        _failure(collectors["param_ranges"], "%s TONE sigma != 0" % (frame_id,))
                elif kind == "CLIP":
                    clip_low, clip_high = impairment.get("gate_clip_amplitude_codes_uniform", [2200, 3500])
                    if not (int(clip_low) <= record["amplitude_codes"] <= int(clip_high)):
                        _failure(collectors["param_ranges"], "%s CLIP amplitude %d outside range"
                                 % (frame_id, record["amplitude_codes"]))
                    if record["noise_sigma_codes"] != 0.0:
                        _failure(collectors["param_ranges"], "%s CLIP sigma != 0" % (frame_id,))
                    if record["phase_rad"] != 0.0 or record["cfo_hz"] != 0.0:
                        _failure(collectors["param_ranges"], "%s CLIP phase/CFO must be zero" % (frame_id,))
                    # saturation / rail-code verification
                    pre = rebuilt["rounded"]
                    out_of_range = int(np.count_nonzero(pre > MAX_CODE) + np.count_nonzero(pre < MIN_CODE))
                    rails = int(np.count_nonzero(stored_X == MAX_CODE) + np.count_nonzero(stored_X == MIN_CODE))
                    if not stored_flag:
                        _failure(collectors["gate_clip_rails"], "%s CLIP frame not flagged clipped" % (frame_id,))
                    if out_of_range == 0:
                        _failure(collectors["gate_clip_rails"], "%s CLIP pre-saturation values in range" % (frame_id,))
                    if rails == 0:
                        _failure(collectors["gate_clip_rails"], "%s CLIP frame has no rail codes" % (frame_id,))
        del arrays

    # session frame-count completeness
    for session_id, expected in expected_sessions.items():
        got = session_progress.get(session_id, 0)
        if got != expected["frames"]:
            _failure(collectors["frame_metadata"], "session %r has %d frames, expected %d"
                     % (session_id, got, expected["frames"]))
    if stats["snr_residual_checked"] == 0:
        stats["residual_var_ratio_min"] = 0.0

    # seed uniqueness (independent i.i.d. per frame must not reuse RNG streams)
    for name, values in (("symbol_seed", stats["symbol_seeds"]), ("noise_seed", stats["noise_seeds"])):
        unique = len(set(values))
        if unique != len(values):
            _failure(collectors["seed_uniqueness"], "%s has %d duplicates over %d frames"
                     % (name, len(values) - unique, len(values)))
    del stats["symbol_seeds"], stats["noise_seeds"]

    # duplicates from computed bytes (source of truth)
    frames = stats["computed_iq"]
    del stats["computed_iq"]
    seen = {}
    cross = 0
    intra = 0
    for digest, split, frame_id in frames:
        previous = seen.get(digest)
        if previous is None:
            seen[digest] = (split, frame_id)
        else:
            if previous[0] != split:
                cross += 1
            else:
                intra += 1
    stats["duplicate_iq"] = {"cross_split": cross, "intra_split": intra,
                             "unique_frames": len(seen), "frames_hashed": len(frames)}
    declared = manifest.get("summary", {})
    if declared.get("cross_split_duplicate_iq") != cross:
        _failure(collectors["seed_uniqueness"],
                 "manifest cross_split_duplicate_iq %r != computed %d"
                 % (declared.get("cross_split_duplicate_iq"), cross))
    if declared.get("intra_split_duplicate_iq") != intra:
        _failure(collectors["seed_uniqueness"],
                 "manifest intra_split_duplicate_iq %r != computed %d"
                 % (declared.get("intra_split_duplicate_iq"), intra))
    ctx["computed"] = {
        "clip_split": stats["clipped_by_split"],
        "clipped_total": stats["clipped"],
        "clipped_by_class": stats["clipped_by_class"],
        "gate_clipped_by_kind": stats["gate_clipped_by_kind"],
        "file_counts": {},
    }
    ctx["stats"] = stats
    return collectors


def check_clipped_summary(ctx: dict) -> dict:
    collector = _new_collector()
    manifest = ctx["manifest"]
    computed = ctx.get("computed", {})
    summary = manifest.get("summary", {})
    extra = {"computed_clipped_total": computed.get("clipped_total"),
             "computed_clipped_by_split": computed.get("clip_split")}
    if summary.get("clipped_frames") != computed.get("clipped_total"):
        _failure(collector, "manifest clipped_frames %r != computed %r"
                 % (summary.get("clipped_frames"), computed.get("clipped_total")))
    declared_by_split = summary.get("clipped_frames_by_split", {})
    for split in SPLIT_ORDER:
        if declared_by_split.get(split) != computed.get("clip_split", {}).get(split):
            _failure(collector, "%s clipped count %r != computed %r"
                     % (split, declared_by_split.get(split), computed.get("clip_split", {}).get(split)))
    return _collector_check("clipped_statistics", collector,
                            "clipped counts match the stored frames", extra)


def check_summary_counts(ctx: dict) -> dict:
    collector = _new_collector()
    manifest = ctx["manifest"]
    config = ctx["config"]
    variant = ctx["variant"]
    summary = manifest.get("summary", {})
    extra = {}
    if config is None or variant is None:
        _failure(collector, "cannot resolve variant/config")
        return _collector_check("summary_counts", collector, "frame-count summary", extra)
    expected_sessions = expected_session_map(config, variant)
    expected_per_split = {}
    for session in expected_sessions.values():
        expected_per_split[session["split"]] = expected_per_split.get(session["split"], 0) + session["frames"]
    expected_total = sum(expected_per_split.values())
    extra["expected_per_split"] = expected_per_split
    extra["expected_total"] = expected_total
    if summary.get("total_frames") != expected_total:
        _failure(collector, "summary.total_frames %r != %d" % (summary.get("total_frames"), expected_total))
    per_split = summary.get("per_split", {})
    if per_split != expected_per_split:
        _failure(collector, "summary.per_split %r != %r" % (per_split, expected_per_split))
    field_frames = sum(int(entry.get("count", -1)) for entry in manifest.get("files", []))
    if field_frames != expected_total:
        _failure(collector, "sum(files[].count) %d != %d" % (field_frames, expected_total))
    # per-file entries: split/label/count from the frozen plan
    expected_files = {plan["path"]: plan for plan in expected_file_plan(config, variant)}
    declared_paths = set()
    for entry in manifest.get("files", []):
        declared_paths.add(entry.get("path"))
        plan = expected_files.get(entry.get("path"))
        if plan is None:
            _failure(collector, "unexpected file entry %r" % (entry.get("path"),))
            continue
        if entry.get("split") != plan["split"] or entry.get("label") != plan["label"]:
            _failure(collector, "%s split/label %r/%r != %r/%r"
                     % (entry.get("path"), entry.get("split"), entry.get("label"),
                        plan["split"], plan["label"]))
        expected_count = sum(session["frames"] for session in expected_sessions.values()
                             if session["split"] == plan["split"]
                             and (session["label"] == plan["label"]))
        if entry.get("count") != expected_count:
            _failure(collector, "%s count %r != %d"
                     % (entry.get("path"), entry.get("count"), expected_count))
    missing = set(expected_files) - declared_paths
    if missing:
        _failure(collector, "missing file entries: %r" % (sorted(missing),))
    # unexpected npz on disk
    for pattern in ("frames/*.npz", "gate/*.npz"):
        for path in (ctx["dataset_dir"]).glob(pattern):
            rel = path.relative_to(ctx["dataset_dir"]).as_posix()
            if rel not in declared_paths:
                _failure(collector, "unlisted npz on disk: %r" % (rel,))
    return _collector_check("summary_counts", collector,
                            "frame counts, per-split totals and file entries match frozen plan", extra)


def check_mapping(ctx: dict) -> dict:
    collector = _new_collector()
    extra = {"patterns_checked": 0, "product_mapper": ctx.get("product_status")}
    product_mapper = ctx.get("product_mapper")
    for mode in CLASS_ORDER:
        table = expected_symbol_table(mode)
        for bits, expected in table.items():
            actual = map_bits_to_symbol(mode, bits)
            if tuple(actual) != tuple(expected):
                _failure(collector, "%s bits %r local mapping %r != %r"
                         % (mode, bits, actual, expected))
            if product_mapper is not None:
                product_value = tuple(float(value) for value in product_mapper(mode, bits))
                if tuple(float(value) for value in actual) != product_value:
                    _failure(collector, "%s bits %r mapping %r != product %r"
                             % (mode, bits, actual, product_value))
                extra["patterns_checked"] += 1
    return _collector_check("bit_mapping_vs_contract_and_product", collector,
                            "all bit patterns match the frozen contract/product mapper", extra)


def check_noiseless_quantization(ctx: dict) -> dict:
    collector = _new_collector()
    waveform = ctx["manifest"]["waveform"]
    detail = {}
    # exhaustive expected rint results (ties-to-even), saturating
    probe = np.array([[0.5, 1.5], [2.5, -0.5], [-1.5, -2.5], [3.5, -3.5],
                      [2047.5, -2048.5], [3000.0, -3000.0]])
    expected = np.array([[0, 2], [2, 0], [-2, -2], [4, -4], [2047, -2048], [2047, -2048]],
                        dtype=np.int16)
    X, clipped, _rounded = quantize_reference(probe)
    if not np.array_equal(X, expected):
        _failure(collector, "ties-to-even/saturation probe X=%r != expected %r" % (X.tolist(), expected.tolist()))
    if not clipped:
        _failure(collector, "saturation probe did not set clipped=True")
    if _rounded[4, 0] != 2048 or _rounded[5, 0] != 3000:
        _failure(collector, "pre-saturation rounding of probe unexpected: %r" % (_rounded.tolist(),))

    # noiseless / no-CFO subset: complex-envelope peak == amplitude_codes, and
    # after an arbitrary receiver phase/CFO rotation the component peak stays
    # <= amplitude_codes (complex-envelope bound, +1 code tolerance).
    normalisation = ctx.get("normalisation", "complex")
    detail["peak_checked"] = 0
    for mode in CLASS_ORDER:
        for amplitude in (150, 600, 1200, 2047):
            for seed_index in range(3):
                symbol_seed = int.from_bytes(
                    hashlib.sha256(("noiseless|%s|%d|%d" % (mode, amplitude, seed_index)).encode()).digest()[:8],
                    "big")
                wave, _symbols = _symbol_waveform(mode, symbol_seed, int(waveform["n_symbols"]),
                                                  int(waveform["sps"]))
                wave = _peak_normalise(wave, amplitude, normalisation)
                envelope_peak = float(np.max(np.sqrt(wave[:, 0] * wave[:, 0]
                                                     + wave[:, 1] * wave[:, 1])))
                if abs(envelope_peak - float(amplitude)) > 1e-9 * max(1.0, float(amplitude)):
                    _failure(collector, "%s amplitude %d clean max|I+jQ| %.9f != amplitude"
                             % (mode, amplitude, envelope_peak))
                seg = wave[0:int(waveform["samples"])]
                X_clean, clipped_clean, _rounded = quantize_reference(
                    np.stack([seg[:, 0], seg[:, 1]], axis=1))
                peak = _peak_value(X_clean, normalisation)
                # per-component rounding can lift the quantised envelope by ~1-2 codes
                if abs(peak - int(amplitude)) > 2:
                    _failure(collector, "%s amplitude %d noiseless quantised peak %d != amplitude"
                             % (mode, amplitude, peak))
                if clipped_clean:
                    _failure(collector, "%s amplitude %d noiseless frame clipped" % (mode, amplitude))
                # receiver rotation must not push a component beyond the envelope peak
                for phase, cfo in ((1.234, 12345.0), (4.567, -20000.0), (0.0, 20000.0)):
                    n = np.arange(int(waveform["samples"]), dtype=np.float64)
                    rotated = ((seg[:, 0] + 1j * seg[:, 1]) * np.exp(1j * phase)
                               * np.exp(1j * (2.0 * np.pi * cfo * n / float(waveform["fs_hz"]))))
                    component_peak = float(max(np.max(np.abs(rotated.real)),
                                               np.max(np.abs(rotated.imag))))
                    if component_peak > float(amplitude) + 1.0:
                        _failure(collector, "%s amplitude %d rotated component peak %.6f > %d+1"
                                 % (mode, amplitude, component_peak, amplitude))
                detail["peak_checked"] += 1
    # offset window subset: quantised noiseless window equals rint of the clean floats
    for mode in CLASS_ORDER:
        symbol_seed = int.from_bytes(hashlib.sha256(("offset|%s" % (mode,)).encode()).digest()[:8], "big")
        wave, _symbols = _symbol_waveform(mode, symbol_seed, int(waveform["n_symbols"]), int(waveform["sps"]))
        wave = _peak_normalise(wave, 777, normalisation)
        for offset in (0, 7, MAX_WINDOW_OFFSET):
            seg = wave[offset:offset + int(waveform["samples"])]
            X_expected, clipped_expected, _rounded = quantize_reference(
                np.stack([seg[:, 0], seg[:, 1]], axis=1))
            if clipped_expected:
                _failure(collector, "%s offset %d noiseless frame clipped" % (mode, offset))
            detail.setdefault("offset_checked", 0)
            detail["offset_checked"] += 1
    return _collector_check("noiseless_quantization", collector,
                            "clean max|I+jQ| == amplitude_codes; rotated component peak <= amplitude; "
                            "np.rint ties-to-even correct", detail)


def check_constellation_power(ctx: dict) -> dict:
    collector = _new_collector()
    waveform = ctx["manifest"]["waveform"]
    samples = int(waveform["samples"])
    sps = int(waveform["sps"])
    normalisation = ctx.get("normalisation", "complex")
    extra = {"declared": {label: float(value) for label, value in DECLARED_C.items()},
             "normalisation": normalisation}
    # Independently derive the analytic c = E|x|^2 / A^2 from the axis tables
    # under the frozen complex-envelope peak normalisation:
    #   complex envelope peak^2 = sum over active axes of max(axis)^2
    #   c = (sum over active axes of mean(axis^2)) / envelope_peak^2
    # which for the symmetric I/Q tables reduces to mean(axis^2)/max(axis)^2.
    axis_power = {}
    for mode, table, active_axes in (
        ("BPSK", AXIS2, 1),
        ("QPSK", AXIS2, 2),
        ("QAM16", AXIS4, 2),
        ("QAM64", AXIS8, 2),
    ):
        mean_square = sum(value * value for value in table) / len(table)
        peak_square = max(abs(value) for value in table) ** 2
        envelope_peak_square = active_axes * peak_square
        axis_power[mode] = float(active_axes * mean_square / envelope_peak_square)
    extra["derived"] = dict(axis_power)
    for mode in CLASS_ORDER:
        if abs(axis_power[mode] - DECLARED_C[mode]) > 1e-12:
            _failure(collector, "%s derived c %.12f != frozen contract c %.12f"
                     % (mode, axis_power[mode], DECLARED_C[mode]))

    # ---- synthetic noiseless probes: measured mean power vs analytic c -------
    amplitude = 600.0
    probe_seeds = 256
    sums = {mode: 0.0 for mode in CLASS_ORDER}
    counts = {mode: 0 for mode in CLASS_ORDER}
    for mode in CLASS_ORDER:
        for index in range(probe_seeds):
            symbol_seed = int.from_bytes(
                hashlib.sha256(("power|%s|%d" % (mode, index)).encode()).digest()[:8], "big")
            wave, symbols = _symbol_waveform(mode, symbol_seed, int(waveform["n_symbols"]), sps)
            if normalisation == "complex":
                peak = float(np.max(np.sqrt(wave[:, 0] * wave[:, 0] + wave[:, 1] * wave[:, 1])))
            else:
                peak = float(np.max(np.abs(wave)))
            scaled = wave * (amplitude / peak)
            seg = scaled[0:samples]
            measured = float(np.mean(seg[:, 0] * seg[:, 0] + seg[:, 1] * seg[:, 1])) / (amplitude * amplitude)
            # independent expected value from the actual symbol draws / window layout
            full_symbols = samples // sps
            remainder = samples - full_symbols * sps
            energy = float(np.sum(symbols[:full_symbols, 0] * symbols[:full_symbols, 0]
                                  + symbols[:full_symbols, 1] * symbols[:full_symbols, 1])
                           * sps)
            if remainder:
                last = full_symbols
                energy += float(remainder * (symbols[last, 0] * symbols[last, 0]
                                             + symbols[last, 1] * symbols[last, 1]))
            expected = energy / (samples * peak * peak)
            if abs(measured - expected) > 1e-9 * max(expected, 1.0):
                _failure(collector, "%s probe %d measured c %.12f != symbol-derived %.12f"
                         % (mode, index, measured, expected))
            sums[mode] += measured
            counts[mode] += 1
    probe_means = {}
    for mode in CLASS_ORDER:
        probe_means[mode] = sums[mode] / counts[mode]
        if abs(probe_means[mode] - DECLARED_C[mode]) > 0.02 * DECLARED_C[mode] + 1e-9:
            _failure(collector, "%s synthetic mean c %.6f != analytic %.6f"
                     % (mode, probe_means[mode], DECLARED_C[mode]))
    extra["synthetic_mean"] = {label: round(value, 8) for label, value in probe_means.items()}

    # ---- dataset aggregate: measured from the reconstructed clean windows -----
    dataset_means = {}
    for mode in CLASS_ORDER:
        total = ctx["stats"]["c_measured_sum"][mode]
        count = ctx["stats"]["c_measured_count"][mode]
        if count == 0:
            _failure(collector, "%s: no dataset frames to measure c" % (mode,))
            continue
        dataset_means[mode] = total / count
        if abs(dataset_means[mode] - DECLARED_C[mode]) > 0.02 * DECLARED_C[mode] + 1e-9:
            _failure(collector, "%s dataset mean c %.6f != analytic %.6f"
                     % (mode, dataset_means[mode], DECLARED_C[mode]))
    extra["dataset_mean"] = {label: round(value, 8) for label, value in dataset_means.items()}
    return _collector_check("constellation_power_coefficients", collector,
                            "analytic c matches measured (synthetic probes + dataset aggregate)", extra)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run_check(dataset_dir: Path, config_path: Path | None = None,
              product_path: Path | None = None, out_json: Path | None = None,
              out_md: Path | None = None) -> dict:
    started = time.perf_counter()
    dataset_dir = Path(dataset_dir)
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("manifest.json not found in %s" % (dataset_dir,))
    manifest_sha = sha256_file(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    config = None
    resolved_config_path = None
    if config_path is not None:
        resolved_config_path = Path(config_path)
    elif manifest.get("config_path"):
        resolved_config_path = repo_root / manifest["config_path"]
    if resolved_config_path is not None and resolved_config_path.is_file():
        with open(resolved_config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)

    variant = determine_variant(config, manifest) if config is not None else None
    expected_sessions = expected_session_map(config, variant) if (config and variant) else {}
    headroom_sigma = None
    if config is not None:
        raw_headroom = config.get("dataset", {}).get("impairment", {}).get("clipping_headroom_sigma")
        if isinstance(raw_headroom, (int, float)) and not isinstance(raw_headroom, bool):
            headroom_sigma = float(raw_headroom)

    product_mapper, product_status = load_product_mapper(repo_root)
    if product_path is not None:
        product_mapper, product_status = load_product_mapper(Path(product_path).parent, Path(product_path).name)
    normalisation = manifest.get("generator", {}).get("peak_normalisation")
    if normalisation not in ("component", "complex"):
        normalisation = None

    ctx = {
        "dataset_dir": dataset_dir,
        "repo_root": repo_root,
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "config": config,
        "config_path": resolved_config_path,
        "variant": variant,
        "expected_sessions": expected_sessions,
        "headroom_sigma": headroom_sigma,
        "normalisation": normalisation,
        "product_mapper": product_mapper,
        "product_status": product_status,
        "stats": {},
        "computed": {},
    }

    checks = []
    checks.append(check_manifest_top(ctx))
    checks.append(check_config_identity(ctx))
    warnings = []
    if not expected_sessions:
        warnings.append("could not resolve config/variant; split/session plan checks degraded")
    checks.append(check_waveform_frozen(ctx))
    checks.append(check_split_plan(ctx))

    collectors = scan_frames(ctx)
    checks.append(_collector_check("npz_file_sha256", collectors["file_sha256"],
                                   "every npz sha256 matches the manifest"))
    checks.append(_collector_check("npz_arrays", collectors["npz_arrays"],
                                   "required arrays, dtypes and shapes present"))
    checks.append(_collector_check("frame_metadata", collectors["frame_metadata"],
                                   "frame_id/session_id/label/seed_family metadata consistent"))
    checks.append(_collector_check("exact_frame_reconstruction", collectors["reconstruction"],
                                   "every frame rebuilt byte-for-byte from recorded seeds/params",
                                   {"frames_checked": ctx["stats"].get("reconstruction_checked", 0)}))
    checks.append(_collector_check("iq_sha256", collectors["iq_sha256"],
                                   "per-frame iq_sha256 matches stored bytes"))
    checks.append(_collector_check("clipping_flags", collectors["clipping_flags"],
                                   "clipped flag matches pre-saturation out-of-range detection"))
    checks.append(_collector_check("parameter_ranges", collectors["param_ranges"],
                                   "per-frame parameters within frozen ranges / sigma formula exact"))
    checks.append(_collector_check("amplitude_safety_cap", collectors["amplitude_safety"],
                                   "amplitude_codes respects the frozen clipping-safety cap"))
    checks.append(_collector_check("snr_residual", collectors["snr_residual"],
                                   "measured residual noise variance matches sigma (quantisation aware)"))
    checks.append(_collector_check("snr_grid", collectors["snr_grid"],
                                   "test snr grid exact; train/val snr within uniform range"))
    checks.append(_collector_check("seed_uniqueness_and_duplicates", collectors["seed_uniqueness"],
                                   "RNG seeds unique per frame; no cross/intra split duplicate IQ",
                                   {"duplicate_iq": ctx["stats"].get("duplicate_iq")}))
    checks.append(_collector_check("gate_clip_rails", collectors["gate_clip_rails"],
                                   "gate CLIP frames saturated, flagged and contain rail codes"))
    checks.append(_collector_check("rotated_component_bound", collectors["rotated_component_bound"],
                                   "after receiver phase/CFO rotation max(|I|,|Q|) <= amplitude_codes "
                                   "everywhere (complex-envelope bound, +1 code)"))
    checks.append(check_clipped_summary(ctx))
    checks.append(check_summary_counts(ctx))
    checks.append(check_mapping(ctx))
    checks.append(check_noiseless_quantization(ctx))
    checks.append(check_constellation_power(ctx))

    passed = all(check["passed"] for check in checks)
    stats = ctx["stats"]
    report = {
        "schema": REPORT_SCHEMA,
        "tool": "ml_training/data/reference_check.py",
        "dataset_dir": dataset_dir.as_posix(),
        "dataset_id": manifest.get("dataset_id"),
        "variant": variant,
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": manifest_sha,
        "config_path": resolved_config_path.as_posix() if resolved_config_path else None,
        "config_sha256": sha256_file(resolved_config_path) if (resolved_config_path and resolved_config_path.is_file()) else None,
        "product_mapper": product_status or "pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol",
        "passed": bool(passed),
        "checks_total": len(checks),
        "checks_passed": sum(1 for check in checks if check["passed"]),
        "checks": checks,
        "warnings": warnings,
        "stats": {
            "frames_checked": stats.get("frames", 0),
            "clipping_headroom_sigma": ctx.get("headroom_sigma"),
            "peak_normalisation": ctx.get("normalisation"),
            "clipped_frames": stats.get("clipped", 0),
            "clipped_by_split": stats.get("clipped_by_split", {}),
            "clipped_by_class": stats.get("clipped_by_class", {}),
            "frames_by_class": stats.get("frames_by_class", {}),
            "clipped_ratio_by_class": {
                label: round(stats["clipped_by_class"][label] / frames, 6)
                if (frames := stats["frames_by_class"][label]) else 0.0
                for label in CLASS_ORDER
            },
            "gate_clipped_by_kind": stats.get("gate_clipped_by_kind", {}),
            "reconstruction_checked": stats.get("reconstruction_checked", 0),
            "snr_residual_checked": stats.get("snr_residual_checked", 0),
            "snr_residual_max_rel_dev": round(stats.get("snr_residual_max_rel_dev", 0.0), 6),
            "snr_residual_var_ratio_min": round(stats.get("residual_var_ratio_min", 0.0), 6),
            "snr_residual_var_ratio_max": round(stats.get("residual_var_ratio_max", 0.0), 6),
            "sigma_formula_max_abs_diff": stats.get("snr_sigma_formula_max_abs_diff", 0.0),
            "duplicate_iq": stats.get("duplicate_iq"),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "generated_utc": now_utc(),
        "numpy_version": np.__version__,
        "not_run": [],
    }
    if out_json is not None:
        out_json = Path(out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        with open(out_json, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, sort_keys=False)
            handle.write("\n")
    if out_md is not None:
        write_markdown_report(report, Path(out_md))
    return report


def write_markdown_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Independent reference check: %s" % (report["dataset_id"],),
        "",
        "Overall: **%s** (%d/%d checks passed)" % (
            "PASS" if report["passed"] else "FAIL",
            report["checks_passed"], report["checks_total"]),
        "",
        "| field | value |",
        "|---|---|",
        "| dataset dir | `%s` |" % report["dataset_dir"],
        "| manifest sha256 | `%s` |" % report["manifest_sha256"],
        "| config | `%s` |" % (report["config_path"],),
        "| config sha256 | `%s` |" % (report["config_sha256"],),
        "| frames checked | %d |" % report["stats"]["frames_checked"],
        "| clipped frames | %d |" % report["stats"]["clipped_frames"],
        "| exact reconstructions | %d |" % report["stats"]["reconstruction_checked"],
        "| elapsed | %.2f s |" % report["elapsed_sec"],
        "",
        "## Checks",
        "",
        "| check | result | detail / failures |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        detail = check.get("summary", "")
        if not check["passed"]:
            detail += " — failures: %d; e.g. %s" % (
                check["failures"], "; ".join(check["failure_examples"][:3]))
        lines.append("| `%s` | %s | %s |" % (check["name"], status, detail))
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend("- %s" % warning for warning in report["warnings"])
    lines.extend(["", "## Numeric stats", "", "```json",
                  json.dumps(report["stats"], indent=2, sort_keys=False), "```", ""])
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent reference check for the synthetic dataset.")
    parser.add_argument("--dataset", required=True, type=Path,
                        help="dataset directory containing manifest.json")
    parser.add_argument("--config", type=Path, default=None,
                        help="frozen config (default: manifest config_path resolved from repo root)")
    parser.add_argument("--product", type=Path, default=None,
                        help="product rf_iq_signal.py path for the mapping cross-check")
    parser.add_argument("--out", type=Path, default=Path("ml_training/data/reports/reference-check.json"))
    parser.add_argument("--md", type=Path, default=Path("ml_training/data/reports/reference-check.md"))
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        report = run_check(args.dataset, config_path=args.config, product_path=args.product,
                           out_json=args.out, out_md=args.md)
    except FileNotFoundError as error:
        print("[reference_check] %s" % (error,), file=sys.stderr)
        return 1
    print("[reference_check] dataset: %s" % report["dataset_dir"], flush=True)
    for check in report["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        line = "[reference_check] %-38s %s" % (check["name"], status)
        if not check["passed"]:
            line += " (failures=%d) e.g. %s" % (
                check["failures"], "; ".join(check["failure_examples"][:2]))
        print(line, flush=True)
    print("[reference_check] stats: %s" % json.dumps(report["stats"], sort_keys=False), flush=True)
    print("[reference_check] OVERALL: %s (%d/%d) in %.2fs"
          % ("PASS" if report["passed"] else "FAIL", report["checks_passed"],
             report["checks_total"], report["elapsed_sec"]), flush=True)
    print("[reference_check] json: %s" % (args.out,), flush=True)
    print("[reference_check] markdown: %s" % (args.md,), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
