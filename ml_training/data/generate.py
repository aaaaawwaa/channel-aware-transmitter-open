#!/usr/bin/env python
"""Synthetic 4-class modulation dataset generator.

Contract: ``ml_training/docs/CONTRACTS.md`` sections 3-7 (frozen).  This module is
owned by the data-gen work scope (``ml_training/data/**`` -> ``ml_training/datasets/**``).

Frozen waveform / impairment model (see contract):

* ``fs = 60_000_000`` Hz, ``Rs = 3_000_000`` Hz, ``sps = 20``, RECT shaping,
  ``samples = 4096``, ``n_symbols = 207`` (waveform length 4140 samples).
* Bits per frame are **independent i.i.d. uniform** draws from
  ``np.random.default_rng(symbol_seed).integers(0, 2)`` -- no PRBS shifting or
  slicing (CONTRACTS §4).
* Bit -> symbol mapping / bit order is bit-identical to the frozen product
  contract ``pynq_overlay/sw/rf_iq_signal.py:map_bits_to_symbol`` (MSB-first,
  non-Gray axis tables).  See :func:`map_bits_to_symbol`.
* Amplitude: frame-internal **component peak** normalisation to
  ``amplitude_codes`` (``max(max|I|, max|Q|) == amplitude_codes``, matching the
  contract's parenthetical and the product ``iq * amplitude / peak`` semantics),
  then the clipping-safety cap from CONTRACTS §5.
* Impairments in frozen order (§5): window offset -> initial phase -> CFO -> AWGN.
* Quantisation: ``np.rint`` (ties-to-even); values outside signed 12-bit range are
  **saturated** to ``[-2048, 2047]`` and the frame is flagged ``clipped=True``
  (kept, never silently dropped; CONTRACTS §3 + §8 require the saturated edge
  values for the product clipping gate to be observable).

All synthesis uses the *stored* (float32-rounded) values of ``snr_db``,
``phase_rad``, ``cfo_hz`` and ``noise_sigma_codes`` so that the independent
reference checker can rebuild every frame byte-for-byte from the npz metadata.

The output ``.npz`` files are written by a deterministic writer (fixed zip
timestamps, fixed array order) so ``npz sha256`` is reproducible across runs on
the same interpreter/zlib.

CLI::

    python ml_training/data/generate.py \
        --config ml_training/config/first_round.json \
        --variant formal \
        --out ml_training/datasets/sim-v1
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = "ml-modrec-synthetic-v1"
GENERATOR_VERSION = "ml_training-data-generator-v1"
REPORT_SCHEMA = "ml-modrec-generation-report-v1"
# Frame peak-normalisation convention actually used by this generator (frozen
# contract §4, Lead decision 2026-10-20): complex-envelope peak normalisation,
# i.e. max|I+jQ| == amplitude_codes.  This is a deliberate *receive-simulation*
# choice: the receiver initial phase / CFO rotation is applied after the
# normalisation, and component-peak normalisation would let the rotation push
# two-axis constellations to sqrt(2)*A and cause artificial clipping.  The
# product TX `generate_digital_frame` keeps its own component-peak semantics and
# is not modified.  Recorded in manifest.generator.peak_normalisation so the
# independent reference checker can rebuild frames without importing this module.
PEAK_NORMALISATION = "complex"

CLASS_ORDER = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
BITS_PER_SYMBOL = {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}

# Frozen constellation axis tables (contract §4, non-Gray, product RTL source).
AXIS2 = (1.0, -1.0)
AXIS4 = (3.0, 1.0, -3.0, -1.0)
AXIS8 = (7.0, 5.0, 1.0, 3.0, -7.0, -5.0, -1.0, -3.0)

# Clipping headroom in sigma units is frozen in the config
# (``dataset.impairment.clipping_headroom_sigma``, currently 5.5) and is read
# from config at run time -- never hard-coded here.
# The amplitude cap is:
#   amplitude = min(drawn, floor(2046 / (1 + headroom * sqrt(c / (2 * snr_lin)))))
# Analytic c = E|x|^2 / amplitude_codes^2 under **complex-envelope peak**
# normalisation (CONTRACTS §4, revised 2026-10-20): BPSK 1, QPSK 1,
# QAM16 5/9, QAM64 3/7.
SAFETY_C = {
    "BPSK": 1.0,
    "QPSK": 1.0,
    "QAM16": 5.0 / 9.0,
    "QAM64": 3.0 / 7.0,
}
MAX_CODE = 2047
MIN_CODE = -2048
SAFETY_CEILING = 2046

# npz array order (frozen; contract §7 table).
NPZ_ARRAY_ORDER = (
    "X",
    "frame_id",
    "session_id",
    "label",
    "seed_family",
    "symbol_seed",
    "noise_seed",
    "snr_db",
    "amplitude_codes",
    "phase_rad",
    "cfo_hz",
    "window_offset",
    "noise_sigma_codes",
    "iq_sha256",
    "clipped",
)

SPLIT_FRAME_ORDER = ("train", "val", "test")
GATE_SPLIT_ORDER = ("gate_cal", "gate_test")


class ConfigError(RuntimeError):
    """Raised when the frozen configuration or contract is violated."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed64(*parts) -> int:
    """Deterministic uint64 seed derived from arbitrary parts.

    Uses SHA-256 (never Python's salted ``hash()``) so the value is stable across
    processes and runs.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x1f")
    return int.from_bytes(digest.digest()[:8], "big")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(condition, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def snr_linear(snr_db: float) -> float:
    return 10.0 ** (float(snr_db) / 10.0)


def safe_amplitude_cap(c: float, snr_db: float, headroom_sigma: float) -> int:
    """CONTRACTS §5 clipping-safety amplitude cap (integer floor)."""
    value = SAFETY_CEILING / (1.0 + float(headroom_sigma) * math.sqrt(c / (2.0 * snr_linear(snr_db))))
    return int(math.floor(value))


# ---------------------------------------------------------------------------
# constellation mapping (must stay bit-identical to the frozen product code)
# ---------------------------------------------------------------------------
def map_bits_to_symbol(mode: str, bits) -> tuple:
    """Scalar MSB-first bit -> (I, Q) mapping, contract §4 / product contract."""
    bits = tuple(bits)
    bps = BITS_PER_SYMBOL[mode]
    if len(bits) != bps:
        raise ValueError("%s expects %d bits, got %d" % (mode, bps, len(bits)))
    if any(bit not in (0, 1) for bit in bits):
        raise ValueError("constellation bits must be 0 or 1")
    if mode == "BPSK":
        return (AXIS2[bits[0]], 0.0)
    if mode == "QPSK":
        return (AXIS2[bits[0]], AXIS2[bits[1]])
    if mode == "QAM16":
        return (AXIS4[(bits[0] << 1) | bits[1]], AXIS4[(bits[2] << 1) | bits[3]])
    if mode == "QAM64":
        i_val = (bits[0] << 2) | (bits[1] << 1) | bits[2]
        q_val = (bits[3] << 2) | (bits[4] << 1) | bits[5]
        return (AXIS8[i_val], AXIS8[q_val])
    raise ValueError("unknown constellation mode: %r" % (mode,))


def bits_from_seed(symbol_seed: int, mode: str, n_symbols: int) -> np.ndarray:
    """Independent i.i.d. uniform bits for one frame (contract §4).

    Exactly ``np.random.default_rng(symbol_seed).integers(0, 2)`` with an explicit
    length, MSB-first grouping per symbol.
    """
    bps = BITS_PER_SYMBOL[mode]
    generator = np.random.default_rng(int(symbol_seed))
    return generator.integers(0, 2, size=int(n_symbols) * bps).astype(np.uint8)


def symbols_from_bits(mode: str, bits: np.ndarray, n_symbols: int) -> np.ndarray:
    """Vectorised bit -> (n_symbols, 2) float64 constellation symbols."""
    bps = BITS_PER_SYMBOL[mode]
    flat = np.asarray(bits, dtype=np.uint8).reshape(int(n_symbols), bps)
    table2 = np.asarray(AXIS2, dtype=np.float64)
    if mode == "BPSK":
        i_axis = table2[flat[:, 0]]
        q_axis = np.zeros(int(n_symbols), dtype=np.float64)
    elif mode == "QPSK":
        i_axis = table2[flat[:, 0]]
        q_axis = table2[flat[:, 1]]
    elif mode == "QAM16":
        table4 = np.asarray(AXIS4, dtype=np.float64)
        i_axis = table4[(flat[:, 0].astype(np.int64) << 1) | flat[:, 1]]
        q_axis = table4[(flat[:, 2].astype(np.int64) << 1) | flat[:, 3]]
    elif mode == "QAM64":
        table8 = np.asarray(AXIS8, dtype=np.float64)
        i_axis = table8[
            (flat[:, 0].astype(np.int64) << 2)
            | (flat[:, 1].astype(np.int64) << 1)
            | flat[:, 2]
        ]
        q_axis = table8[
            (flat[:, 3].astype(np.int64) << 2)
            | (flat[:, 4].astype(np.int64) << 1)
            | flat[:, 5]
        ]
    else:
        raise ValueError("unknown constellation mode: %r" % (mode,))
    return np.stack([i_axis, q_axis], axis=1)


def rect_waveform(symbols: np.ndarray, sps: int) -> np.ndarray:
    """RECT shaping: each sample repeated ``sps`` times (contract §4)."""
    return np.repeat(np.asarray(symbols, dtype=np.float64), int(sps), axis=0)


def peak_normalise(wave: np.ndarray, amplitude_codes: int,
                   normalisation: str = PEAK_NORMALISATION) -> tuple:
    """Frame-internal peak normalisation, contract §4.

    ``normalisation="component"``: max(max|I|, max|Q|) == amplitude_codes.
    ``normalisation="complex"``: max|I+jQ| == amplitude_codes (product semantics).

    Returns ``(wave_scaled, peak_float)``.
    """
    if normalisation == "component":
        peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    elif normalisation == "complex":
        envelope = np.sqrt(wave[:, 0] * wave[:, 0] + wave[:, 1] * wave[:, 1])
        peak = float(np.max(envelope)) if envelope.size else 0.0
    else:
        raise ConfigError("unknown peak normalisation %r" % (normalisation,))
    if peak <= 0.0:
        raise RuntimeError("degenerate all-zero waveform cannot be peak normalised")
    return wave * (float(amplitude_codes) / peak), peak


def quantize_with_clip(rx: np.ndarray) -> tuple:
    """np.rint ties-to-even quantisation + signed 12-bit saturation.

    Returns ``(X_int16, clipped_bool)``.  ``clipped`` is True iff any rounded
    value fell outside ``[-2048, 2047]``; the stored values are saturated to
    signed 12-bit range (the frame is kept, not dropped or silently truncated).
    """
    rounded = np.rint(np.asarray(rx, dtype=np.float64))
    clipped = bool(np.any(rounded > MAX_CODE) or np.any(rounded < MIN_CODE))
    saturated = np.clip(rounded, MIN_CODE, MAX_CODE)
    return saturated.astype(np.int16), clipped


def iq_sha256(X: np.ndarray) -> str:
    return sha256_bytes(np.ascontiguousarray(X, dtype="<i2").tobytes())


# ---------------------------------------------------------------------------
# frame synthesis (frozen impairment order, contract §5)
# ---------------------------------------------------------------------------
def _clean_rotated(seg: np.ndarray, phase_rad: float, cfo_hz: float, fs_hz: float) -> np.ndarray:
    samples = int(seg.shape[0])
    n = np.arange(samples, dtype=np.float64)
    clean = (seg[:, 0] + 1j * seg[:, 1]) * np.exp(1j * float(phase_rad))
    clean = clean * np.exp(1j * (2.0 * np.pi * float(cfo_hz) * n / float(fs_hz)))
    return clean


def build_class_frame(
    mode: str,
    symbol_seed: int,
    noise_seed: int,
    snr_db: float,
    amplitude_codes: int,
    phase_rad: float,
    cfo_hz: float,
    window_offset: int,
    waveform: dict,
) -> dict:
    """Synthesise one 4-class frame exactly per CONTRACTS §4/§5."""
    sps = int(waveform["sps"])
    samples = int(waveform["samples"])
    n_symbols = int(waveform["n_symbols"])
    fs_hz = float(waveform["fs_hz"])

    bits = bits_from_seed(symbol_seed, mode, n_symbols)
    symbols = symbols_from_bits(mode, bits, n_symbols)
    wave = rect_waveform(symbols, sps)
    wave, _peak = peak_normalise(wave, amplitude_codes, PEAK_NORMALISATION)
    offset = int(window_offset)
    seg = wave[offset:offset + samples]
    if seg.shape[0] != samples:  # defensive: offset range must fit 207*20
        raise RuntimeError("window offset exceeds waveform length")

    clean = _clean_rotated(seg, phase_rad, cfo_hz, fs_hz)
    p_sig = float(np.mean(clean.real * clean.real + clean.imag * clean.imag))
    sigma64 = math.sqrt(p_sig / (2.0 * snr_linear(snr_db)))
    sigma = float(np.float32(sigma64))
    noise = np.random.default_rng(int(noise_seed)).standard_normal((samples, 2)) * sigma

    rx = np.empty((samples, 2), dtype=np.float64)
    rx[:, 0] = clean.real + noise[:, 0]
    rx[:, 1] = clean.imag + noise[:, 1]
    X, clipped = quantize_with_clip(rx)
    return {
        "X": X,
        "clipped": clipped,
        "noise_sigma_codes": np.float32(sigma),
        "p_sig": p_sig,
        "clean": clean,
        "bits": bits,
        "symbols": symbols,
        "peak": _peak,
    }


def build_gate_frame(kind: str, symbol_seed: int, noise_seed: int, params: dict, waveform: dict) -> dict:
    """Synthesise one quality-gate frame.

    Kind construction is *not* the 4-class impairment model; it is defined here
    and documented in NOTES.md / the generation report:

    * OFF   : AWGN only, per-component sigma ~ U[0.5, 30] codes.
    * NOISE : AWGN only, per-component sigma ~ U[50, 600] codes.
    * TONE  : single complex tone ``A*exp(j*(2*pi*f*n/fs + phase))`` with
              ``A ~ U[200,1200]``, ``f ~ U[-5e6, +5e6]`` Hz, no AWGN;
              for TONE the ``cfo_hz`` metadata field carries the tone frequency.
    * CLIP  : BPSK symbols peak-normalised to ``A ~ U[2200,3500]`` codes with
              zero phase/CFO and zero AWGN, so at least one stored component is
              saturated at +/-2047/-2048 and ``clipped=True`` is guaranteed.
    """
    samples = int(waveform["samples"])
    sps = int(waveform["sps"])
    n_symbols = int(waveform["n_symbols"])
    fs_hz = float(waveform["fs_hz"])

    if kind in ("OFF", "NOISE"):
        sigma = float(np.float32(params["sigma"]))
        noise = np.random.default_rng(int(noise_seed)).standard_normal((samples, 2)) * sigma
        X, clipped = quantize_with_clip(noise)
        return {"X": X, "clipped": clipped, "noise_sigma_codes": np.float32(sigma)}

    if kind == "TONE":
        amplitude = int(params["amplitude_codes"])
        freq = float(params["tone_freq_hz"])
        phase = float(params["phase_rad"])
        n = np.arange(samples, dtype=np.float64)
        tone = float(amplitude) * np.exp(1j * (2.0 * np.pi * freq * n / float(fs_hz) + phase))
        rx = np.stack([tone.real, tone.imag], axis=1)
        X, clipped = quantize_with_clip(rx)
        return {"X": X, "clipped": clipped, "noise_sigma_codes": np.float32(0.0)}

    if kind == "CLIP":
        amplitude = int(params["amplitude_codes"])
        bits = bits_from_seed(symbol_seed, "BPSK", n_symbols)
        symbols = symbols_from_bits("BPSK", bits, n_symbols)
        wave = rect_waveform(symbols, sps)
        wave, _peak = peak_normalise(wave, amplitude, PEAK_NORMALISATION)
        offset = int(params["window_offset"])
        seg = wave[offset:offset + samples]
        if seg.shape[0] != samples:
            raise RuntimeError("window offset exceeds waveform length")
        # Deliberately no phase/CFO/AWGN so |I| == amplitude_codes > 2047 for every
        # sample and saturation (clipped=True) is mathematically guaranteed.
        rx = np.stack([seg[:, 0], seg[:, 1]], axis=1)
        X, clipped = quantize_with_clip(rx)
        if not clipped:
            raise RuntimeError("CLIP gate frame failed to saturate; construction bug")
        return {"X": X, "clipped": clipped, "noise_sigma_codes": np.float32(0.0)}

    raise ValueError("unknown gate kind: %r" % (kind,))


# ---------------------------------------------------------------------------
# configuration / planning
# ---------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_config(config: dict) -> None:
    _require(config.get("schema") == "ml-modrec-first-round-config-v1", "unexpected config schema")
    dataset = config.get("dataset", {})
    waveform = dataset.get("waveform", {})
    for key, expected in (
        ("fs_hz", 60_000_000),
        ("symbol_rate_hz", 3_000_000),
        ("sps", 20),
        ("shaping", "RECT"),
        ("samples", 4096),
        ("n_symbols", 207),
    ):
        _require(waveform.get(key) == expected, "waveform.%s must be %r (frozen contract)" % (key, expected))
    _require(tuple(waveform.get("classes", ())) == CLASS_ORDER, "waveform.classes must be %r" % (CLASS_ORDER,))
    _require(tuple(waveform.get("gate_kinds", ())) == GATE_KINDS, "waveform.gate_kinds must be %r" % (GATE_KINDS,))
    _require(waveform["n_symbols"] == int(math.ceil((waveform["samples"] + 19) / waveform["sps"])) + 1,
             "n_symbols does not follow contract formula")
    _require(waveform["samples"] + 19 <= waveform["n_symbols"] * waveform["sps"],
             "waveform too short for the maximum window offset")
    _require(config.get("preprocessing") == "dc-complex-rms-v1", "preprocessing must be dc-complex-rms-v1")
    for variant in ("formal", "smoke"):
        _require(variant in dataset, "config.dataset.%s missing" % variant)
        _require("splits" in dataset[variant], "config.dataset.%s.splits missing" % variant)
    _require("impairment" in dataset, "config.dataset.impairment missing")
    headroom = dataset["impairment"].get("clipping_headroom_sigma")
    _require(isinstance(headroom, (int, float)) and not isinstance(headroom, bool) and headroom > 0,
             "dataset.impairment.clipping_headroom_sigma must be a positive number (read from config)")


def variant_plan(config: dict, variant: str) -> dict:
    """Expand the frozen config into concrete sessions/files (deterministic order)."""
    dataset = config["dataset"]
    waveform = dataset["waveform"]
    impairment = dataset["impairment"]
    splits = dataset[variant]["splits"]
    headroom_sigma = float(impairment["clipping_headroom_sigma"])
    amp_low, amp_high = impairment["amplitude_codes_uniform"]
    off_low, off_high = impairment["gate_off_sigma_codes_uniform"]
    noise_low, noise_high = impairment["gate_noise_sigma_codes_uniform"]
    tone_amp_low, tone_amp_high = impairment["gate_tone"]["amplitude_codes_uniform"]
    tone_f_low, tone_f_high = impairment["gate_tone"]["freq_hz_uniform"]
    clip_low, clip_high = impairment["gate_clip_amplitude_codes_uniform"]
    offset_low, offset_high = impairment["window_offset_samples_uniform_int"]
    phase_low, phase_high = impairment["initial_phase_rad_uniform"]
    cfo_low, cfo_high = impairment["cfo_hz_uniform"]

    files = []  # list of dicts: split, name(label|kind), sessions, path
    layer = {
        "amp_low": int(amp_low),
        "amp_high": int(amp_high),
        "off_low": float(off_low),
        "off_high": float(off_high),
        "noise_low": float(noise_low),
        "noise_high": float(noise_high),
        "tone_amp_low": int(tone_amp_low),
        "tone_amp_high": int(tone_amp_high),
        "tone_f_low": float(tone_f_low),
        "tone_f_high": float(tone_f_high),
        "clip_low": int(clip_low),
        "clip_high": int(clip_high),
        "offset_low": int(offset_low),
        "offset_high": int(offset_high),
        "phase_low": float(phase_low),
        "phase_high": float(phase_high),
        "cfo_low": float(cfo_low),
        "cfo_high": float(cfo_high),
        "headroom_sigma": headroom_sigma,
    }

    # ---- train / val / test -------------------------------------------------
    for split in SPLIT_FRAME_ORDER:
        cfg = splits[split]
        sessions_by_label = {}
        if split in ("train", "val"):
            families = list(cfg["seed_families"])
            _require(len(families) == int(cfg["sessions_per_label"]),
                     "%s seed_families must match sessions_per_label" % split)
            for label in CLASS_ORDER:
                sessions = []
                for index, family in enumerate(families):
                    sessions.append({
                        "session_id": "%s-%s-%s" % (split, label, family),
                        "label": label,
                        "kind": None,
                        "seed_family": family,
                        "frames": int(cfg["frames_per_session"]),
                        "fixed_snr_db": None,
                        "session_index": index,
                    })
                sessions_by_label[label] = sessions
        elif split == "test":
            families = list(cfg["seed_families"])
            grid = [float(value) for value in cfg["snr_db_grid"]]
            frames_per_cell = int(cfg["frames_per_class_per_snr"])
            for label_index, label in enumerate(CLASS_ORDER):
                sessions = []
                for snr_index, snr_db in enumerate(grid):
                    family = families[(label_index * len(grid) + snr_index) % len(families)]
                    sessions.append({
                        "session_id": "test-%s-snr%+.0f-%s" % (label, snr_db, family),
                        "label": label,
                        "kind": None,
                        "seed_family": family,
                        "frames": frames_per_cell,
                        "fixed_snr_db": float(snr_db),
                        "session_index": snr_index,
                    })
                sessions_by_label[label] = sessions
        else:  # pragma: no cover - SPLIT_FRAME_ORDER is frozen
            raise ConfigError("unexpected split %r" % (split,))

        for label in CLASS_ORDER:
            files.append({
                "split": split,
                "name": label,
                "label": label,
                "kind": None,
                "sessions": sessions_by_label[label],
                "path": "frames/%s-%s.npz" % (split, label),
            })

    # ---- gate_cal / gate_test ----------------------------------------------
    for split in GATE_SPLIT_ORDER:
        cfg = splits[split]
        families = list(cfg["families"])
        frames_per_kind = int(cfg["frames_per_kind"])
        # Distribute exactly frames_per_kind frames over the declared seed-family
        # sessions (formal: 2 families x 50 = 100; smoke: 1 family x 10 = 10).
        family_count = len(families)
        counts = [frames_per_kind // family_count
                  + (1 if index < frames_per_kind % family_count else 0)
                  for index in range(family_count)]
        _require(sum(counts) == frames_per_kind,
                 "%s family distribution does not sum to frames_per_kind" % split)
        for kind in GATE_KINDS:
            sessions = []
            for family_index, family in enumerate(families):
                if counts[family_index] == 0:
                    continue
                sessions.append({
                    "session_id": "%s-%s-%s" % (split, kind, family),
                    "label": kind,
                    "kind": kind,
                    "seed_family": family,
                    "frames": int(counts[family_index]),
                    "fixed_snr_db": None,
                    "session_index": family_index,
                })
            files.append({
                "split": split,
                "name": kind,
                "label": kind,
                "kind": kind,
                "sessions": sessions,
                "path": "gate/%s-%s.npz" % (split, kind),
            })
    return {"files": files, "layer": layer, "waveform": waveform, "variant": variant,
            "dataset_id": dataset[variant]["dataset_id"], "splits_cfg": splits,
            "impairment": impairment, "headroom_sigma": headroom_sigma}


# ---------------------------------------------------------------------------
# per-frame parameter derivation
# ---------------------------------------------------------------------------
def derive_frame(
    datum: dict,
    split: str,
    session: dict,
    frame_index: int,
    variant: str,
    dataset_id: str,
    layer: dict,
    waveform: dict,
) -> dict:
    """Derive one frame's seeds/parameters and synthesise it.

    Draw order is frozen and documented in NOTES.md so the dataset is
    reproducible from config + code alone (recorded parameters are the primary
    source used by the independent reference checker).
    """
    kind = session["kind"]
    label = session["label"]
    family = session["seed_family"]
    session_id = session["session_id"]
    material = (SCHEMA, variant, dataset_id, split, session_id, frame_index)
    symbol_seed = seed64(*material, "symbol")
    noise_seed = seed64(*material, "noise")
    prng = np.random.default_rng(seed64(*material, "params"))

    if split in SPLIT_FRAME_ORDER:  # 4-class frame
        mode = label
        if session["fixed_snr_db"] is not None:
            snr_stored = np.float32(session["fixed_snr_db"])
        else:
            snr_cfg = datum["splits_cfg"][split]["snr"]
            snr_stored = np.float32(prng.uniform(float(snr_cfg["low"]), float(snr_cfg["high"])))
        amplitude_draw = int(prng.integers(layer["amp_low"], layer["amp_high"] + 1))
        cap = safe_amplitude_cap(SAFETY_C[mode], float(snr_stored), layer["headroom_sigma"])
        if cap < 64:
            raise ConfigError(
                "clipping-safety cap %d < 64 for %s at %.2f dB: configuration error" % (cap, mode, float(snr_stored))
            )
        amplitude_codes = min(amplitude_draw, cap)
        phase_rad = float(np.float32(prng.uniform(layer["phase_low"], layer["phase_high"])))
        cfo_hz = float(np.float32(prng.uniform(layer["cfo_low"], layer["cfo_high"])))
        window_offset = int(prng.integers(layer["offset_low"], layer["offset_high"] + 1))
        frame = build_class_frame(
            mode=mode,
            symbol_seed=symbol_seed,
            noise_seed=noise_seed,
            snr_db=float(snr_stored),
            amplitude_codes=amplitude_codes,
            phase_rad=phase_rad,
            cfo_hz=cfo_hz,
            window_offset=window_offset,
            waveform=waveform,
        )
        return {
            "X": frame["X"],
            "clipped": frame["clipped"],
            "symbol_seed": symbol_seed,
            "noise_seed": noise_seed,
            "snr_db": snr_stored,
            "amplitude_codes": amplitude_codes,
            "phase_rad": np.float32(phase_rad),
            "cfo_hz": np.float32(cfo_hz),
            "window_offset": window_offset,
            "noise_sigma_codes": frame["noise_sigma_codes"],
            "label": label,
            "kind": None,
            "session_id": session_id,
            "seed_family": family,
            "frame_id": "%s-%04d" % (session_id, frame_index),
            "amplitude_draw": amplitude_draw,
            "cap": cap,
        }

    # ---- gate frames --------------------------------------------------------
    if kind == "OFF":
        sigma = float(np.float32(prng.uniform(layer["off_low"], layer["off_high"])))
        frame = build_gate_frame("OFF", symbol_seed, noise_seed, {"sigma": sigma}, waveform)
        params = {
            "snr_db": np.float32(np.nan), "amplitude_codes": 0,
            "phase_rad": np.float32(0.0), "cfo_hz": np.float32(0.0),
            "window_offset": 0,
        }
    elif kind == "NOISE":
        sigma = float(np.float32(prng.uniform(layer["noise_low"], layer["noise_high"])))
        frame = build_gate_frame("NOISE", symbol_seed, noise_seed, {"sigma": sigma}, waveform)
        params = {
            "snr_db": np.float32(np.nan), "amplitude_codes": 0,
            "phase_rad": np.float32(0.0), "cfo_hz": np.float32(0.0),
            "window_offset": 0,
        }
    elif kind == "TONE":
        amplitude = int(prng.integers(layer["tone_amp_low"], layer["tone_amp_high"] + 1))
        freq = float(np.float32(prng.uniform(layer["tone_f_low"], layer["tone_f_high"])))
        phase = float(np.float32(prng.uniform(layer["phase_low"], layer["phase_high"])))
        frame = build_gate_frame(
            "TONE", symbol_seed, noise_seed,
            {"amplitude_codes": amplitude, "tone_freq_hz": freq, "phase_rad": phase}, waveform)
        params = {
            "snr_db": np.float32(np.nan), "amplitude_codes": amplitude,
            "phase_rad": np.float32(phase), "cfo_hz": np.float32(freq),
            "window_offset": 0,
        }
    elif kind == "CLIP":
        amplitude = int(prng.integers(layer["clip_low"], layer["clip_high"] + 1))
        window_offset = int(prng.integers(layer["offset_low"], layer["offset_high"] + 1))
        frame = build_gate_frame(
            "CLIP", symbol_seed, noise_seed,
            {"amplitude_codes": amplitude, "window_offset": window_offset}, waveform)
        params = {
            "snr_db": np.float32(np.nan), "amplitude_codes": amplitude,
            "phase_rad": np.float32(0.0), "cfo_hz": np.float32(0.0),
            "window_offset": window_offset,
        }
    else:  # pragma: no cover - frozen gate kinds
        raise ConfigError("unknown gate kind %r" % (kind,))

    return {
        "X": frame["X"],
        "clipped": frame["clipped"],
        "symbol_seed": symbol_seed,
        "noise_seed": noise_seed,
        "label": kind,
        "kind": kind,
        "session_id": session_id,
        "seed_family": family,
        "frame_id": "%s-%04d" % (session_id, frame_index),
        "noise_sigma_codes": frame["noise_sigma_codes"],
        **params,
    }


# ---------------------------------------------------------------------------
# deterministic npz writer
# ---------------------------------------------------------------------------
def write_npz_deterministic(path: Path, arrays: dict) -> None:
    """Write a valid ``.npz`` with fixed zip timestamps / order.

    ``np.savez`` stores the current wall-clock time in the zip headers, which
    would make the file SHA-256 non-reproducible.  This writer keeps every frame
    byte-identical while still producing a standard numpy-readable archive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buffers = []
    for name in NPZ_ARRAY_ORDER:
        if name not in arrays:
            raise ConfigError("missing npz array %r" % (name,))
        buffer = io.BytesIO()
        np.lib.format.write_array(buffer, np.ascontiguousarray(arrays[name]), allow_pickle=False)
        buffers.append((name + ".npy", buffer.getvalue()))
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for member_name, payload in buffers:
            info = zipfile.ZipInfo(member_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)


# ---------------------------------------------------------------------------
# generation driver
# ---------------------------------------------------------------------------
def generate_dataset(config_path: Path, variant: str, out_dir: Path) -> dict:
    started = time.perf_counter()
    started_utc = now_utc()
    config = load_config(config_path)
    validate_config(config)
    datum = variant_plan(config, variant)
    waveform = datum["waveform"]
    layer = datum["layer"]
    dataset_id = datum["dataset_id"]

    if out_dir.name != dataset_id:
        raise ConfigError(
            "output directory name %r must equal config dataset_id %r (contract §7)" % (out_dir.name, dataset_id)
        )
    resolved = out_dir.resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if resolved in (repo_root, repo_root / "ml_training", repo_root / "ml_training" / "data"):
        raise ConfigError("refusing to generate into a source directory: %s" % (resolved,))

    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    (out_dir / "gate").mkdir(parents=True, exist_ok=True)

    planned_paths = {entry["path"] for entry in datum["files"]}
    # remove stale npz from an earlier run that are no longer planned (same dir)
    for pattern in ("frames/*.npz", "gate/*.npz"):
        for stale in out_dir.glob(pattern):
            rel = stale.relative_to(out_dir).as_posix()
            if rel not in planned_paths:
                stale.unlink()

    config_sha256 = sha256_file(config_path)
    files_entries = []
    frame_records = []  # (iq_sha256, split, frame_id) for duplicate scans
    per_split = {split: 0 for split in SPLIT_FRAME_ORDER + GATE_SPLIT_ORDER}
    clipped_by_split = {split: 0 for split in SPLIT_FRAME_ORDER + GATE_SPLIT_ORDER}
    log_lines = [
        "ml_training synthetic dataset generation log",
        "schema: %s" % SCHEMA,
        "generator: %s" % GENERATOR_VERSION,
        "started_utc: %s" % started_utc,
        "config_path: %s" % config_path.as_posix(),
        "config_sha256: %s" % config_sha256,
        "variant: %s" % variant,
        "dataset_id: %s" % dataset_id,
        "numpy_version: %s" % np.__version__,
        "output_dir: %s" % out_dir.as_posix(),
        "",
    ]
    print("[generate] %s -> %s (variant=%s)" % (config_path.as_posix(), out_dir.as_posix(), variant), flush=True)

    total_frames = 0
    for entry in datum["files"]:
        file_started = time.perf_counter()
        arrays = {name: [] for name in NPZ_ARRAY_ORDER if name != "X"}
        x_frames = []
        for session in entry["sessions"]:
            for frame_index in range(int(session["frames"])):
                frame = derive_frame(
                    datum=datum,
                    split=entry["split"],
                    session=session,
                    frame_index=frame_index,
                    variant=variant,
                    dataset_id=dataset_id,
                    layer=layer,
                    waveform=waveform,
                )
                x_frames.append(frame["X"])
                arrays["frame_id"].append(frame["frame_id"])
                arrays["session_id"].append(frame["session_id"])
                arrays["label"].append(frame["label"] if frame["kind"] is None else frame["kind"])
                arrays["seed_family"].append(frame["seed_family"])
                arrays["symbol_seed"].append(np.uint64(frame["symbol_seed"]))
                arrays["noise_seed"].append(np.uint64(frame["noise_seed"]))
                arrays["snr_db"].append(np.float32(frame["snr_db"]))
                arrays["amplitude_codes"].append(np.int16(frame["amplitude_codes"]))
                arrays["phase_rad"].append(np.float32(frame["phase_rad"]))
                arrays["cfo_hz"].append(np.float32(frame["cfo_hz"]))
                arrays["window_offset"].append(np.int16(frame["window_offset"]))
                arrays["noise_sigma_codes"].append(np.float32(frame["noise_sigma_codes"]))
                digest = iq_sha256(frame["X"])
                arrays["iq_sha256"].append(digest)
                arrays["clipped"].append(bool(frame["clipped"]))
                frame_records.append((digest, entry["split"], frame["frame_id"]))
                per_split[entry["split"]] += 1
                if frame["clipped"]:
                    clipped_by_split[entry["split"]] += 1

        X = np.ascontiguousarray(np.stack(x_frames, axis=0).astype(np.int16))
        output_arrays = {"X": X}
        output_arrays["frame_id"] = np.asarray(arrays["frame_id"], dtype=str)
        output_arrays["session_id"] = np.asarray(arrays["session_id"], dtype=str)
        output_arrays["label"] = np.asarray(arrays["label"], dtype=str)
        output_arrays["seed_family"] = np.asarray(arrays["seed_family"], dtype=str)
        output_arrays["symbol_seed"] = np.asarray(arrays["symbol_seed"], dtype=np.uint64)
        output_arrays["noise_seed"] = np.asarray(arrays["noise_seed"], dtype=np.uint64)
        output_arrays["snr_db"] = np.asarray(arrays["snr_db"], dtype=np.float32)
        output_arrays["amplitude_codes"] = np.asarray(arrays["amplitude_codes"], dtype=np.int16)
        output_arrays["phase_rad"] = np.asarray(arrays["phase_rad"], dtype=np.float32)
        output_arrays["cfo_hz"] = np.asarray(arrays["cfo_hz"], dtype=np.float32)
        output_arrays["window_offset"] = np.asarray(arrays["window_offset"], dtype=np.int16)
        output_arrays["noise_sigma_codes"] = np.asarray(arrays["noise_sigma_codes"], dtype=np.float32)
        output_arrays["iq_sha256"] = np.asarray(arrays["iq_sha256"], dtype=str)
        output_arrays["clipped"] = np.asarray(arrays["clipped"], dtype=bool)

        npz_path = out_dir / entry["path"]
        write_npz_deterministic(npz_path, output_arrays)
        npz_sha = sha256_file(npz_path)
        clipped_count = int(output_arrays["clipped"].sum())
        files_entries.append({
            "path": entry["path"],
            "sha256": npz_sha,
            "split": entry["split"],
            "label": entry["name"],
            "count": int(X.shape[0]),
            "clipped_count": clipped_count,
        })
        total_frames += int(X.shape[0])
        elapsed = time.perf_counter() - file_started
        print(
            "[generate] %-24s frames=%4d clipped=%3d sha256=%s (%.2fs)"
            % (entry["path"], X.shape[0], clipped_count, npz_sha[:16], elapsed),
            flush=True,
        )
        log_lines.append(
            "%s frames=%d clipped=%d sha256=%s elapsed_sec=%.3f"
            % (entry["path"], X.shape[0], clipped_count, npz_sha, elapsed)
        )
        del x_frames, output_arrays, arrays

    # ---- duplicate scans ----------------------------------------------------
    cross_duplicates = 0
    intra_duplicates = 0
    seen = {}
    for digest, split, frame_id in frame_records:
        previous = seen.get(digest)
        if previous is None:
            seen[digest] = (split, frame_id)
            continue
        previous_split, previous_frame_id = previous
        if previous_split != split:
            cross_duplicates += 1
        elif previous_frame_id != frame_id:
            intra_duplicates += 1

    clipped_total = int(sum(clipped_by_split.values()))
    summary = {
        "total_frames": int(total_frames),
        "per_split": {split: int(count) for split, count in per_split.items()},
        "clipped_frames": clipped_total,
        "clipped_frames_by_split": {split: int(count) for split, count in clipped_by_split.items()},
        "cross_split_duplicate_iq": int(cross_duplicates),
        "intra_split_duplicate_iq": int(intra_duplicates),
        "unique_iq_frames": int(len(seen)),
    }

    # ---- manifest -----------------------------------------------------------
    split_meta = build_split_metadata(datum)
    elapsed = time.perf_counter() - started
    manifest = {
        "schema": SCHEMA,
        "dataset_id": dataset_id,
        "created_utc": now_utc(),
        "config_path": _display_config_path(config_path, repo_root),
        "config_sha256": config_sha256,
        "numpy_version": np.__version__,
        "waveform": {
            "fs_hz": int(waveform["fs_hz"]),
            "symbol_rate_hz": int(waveform["symbol_rate_hz"]),
            "sps": int(waveform["sps"]),
            "shaping": str(waveform["shaping"]),
            "samples": int(waveform["samples"]),
            "n_symbols": int(waveform["n_symbols"]),
        },
        "classes": list(CLASS_ORDER),
        "gate_kinds": list(GATE_KINDS),
        "splits": split_meta,
        "files": files_entries,
        "summary": summary,
        "generator": {
            "name": GENERATOR_VERSION,
            "variant": variant,
            "started_utc": started_utc,
            "elapsed_sec": round(elapsed, 3),
            "safety_coefficients": {label: float(value) for label, value in SAFETY_C.items()},
            "clipping_headroom_sigma": float(layer["headroom_sigma"]),
            "peak_normalisation": PEAK_NORMALISATION,
            "quantization": "np.rint ties-to-even, saturate to [-2048,2047], clipped flag kept",
        },
    }
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=False)
        handle.write("\n")
    manifest_sha = sha256_file(manifest_path)

    log_lines.extend([
        "",
        "total_frames: %d" % total_frames,
        "clipped_frames: %d" % clipped_total,
        "cross_split_duplicate_iq: %d" % cross_duplicates,
        "intra_split_duplicate_iq: %d" % intra_duplicates,
        "manifest_sha256: %s" % manifest_sha,
        "elapsed_sec: %.3f" % elapsed,
    ])
    with open(out_dir / "generation.log", "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(log_lines) + "\n")

    dataset_bytes = sum(path.stat().st_size for path in out_dir.rglob("*") if path.is_file())
    manifest["dataset_bytes"] = int(dataset_bytes)
    # CONTRACTS §7 requires the generator to produce
    # ml_training/data/reports/generation-report.md.  Only the *formal* run writes
    # it: smoke runs / pytest fixtures / reduced-config runs must never clobber the
    # deliverable report for the formal first round.  Per-run details always go to
    # <dataset>/generation.log and <dataset>/manifest.json.
    if variant == "formal":
        write_generation_report(manifest=manifest, out_dir=out_dir, config_path=config_path,
                                manifest_sha=manifest_sha, elapsed=elapsed, dataset_bytes=dataset_bytes)

    print(
        "[generate] done: %d frames, %d clipped, cross/intra dup %d/%d, %.1f MiB, %.1fs"
        % (total_frames, clipped_total, cross_duplicates, intra_duplicates,
           dataset_bytes / (1024 * 1024), elapsed),
        flush=True,
    )
    return {
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "out_dir": str(out_dir),
        "elapsed_sec": elapsed,
        "dataset_bytes": int(dataset_bytes),
    }


def _display_config_path(config_path: Path, repo_root: Path) -> str:
    try:
        relative = config_path.resolve().relative_to(repo_root)
        return relative.as_posix()
    except ValueError:
        return config_path.as_posix()


def build_split_metadata(datum: dict) -> dict:
    splits_cfg = datum["splits_cfg"]
    meta = {}
    plan_files = datum["files"]
    for split in SPLIT_FRAME_ORDER:
        cfg = splits_cfg[split]
        sessions = []
        for entry in plan_files:
            if entry["split"] != split:
                continue
            for session in entry["sessions"]:
                sessions.append({
                    "session_id": session["session_id"],
                    "label": session["label"],
                    "seed_family": session["seed_family"],
                    "frames": int(session["frames"]),
                    "snr_db_grid": None if session["fixed_snr_db"] is None else [float(session["fixed_snr_db"])],
                })
        meta[split] = {
            "seed_families": list(cfg["seed_families"]),
            "snr": cfg.get("snr"),
            "sessions": sessions,
        }
        if split == "test":
            meta[split]["snr_db_grid"] = [float(value) for value in cfg["snr_db_grid"]]
            meta[split]["frames_per_class_per_snr"] = int(cfg["frames_per_class_per_snr"])
    for split in GATE_SPLIT_ORDER:
        cfg = splits_cfg[split]
        sessions = []
        for entry in plan_files:
            if entry["split"] != split:
                continue
            for session in entry["sessions"]:
                sessions.append({
                    "session_id": session["session_id"],
                    "kind": session["kind"],
                    "label": session["kind"],
                    "seed_family": session["seed_family"],
                    "frames": int(session["frames"]),
                    "snr_db_grid": None,
                })
        meta[split] = {
            "seed_families": list(cfg["families"]),
            "frames_per_kind": int(cfg["frames_per_kind"]),
            "sessions": sessions,
        }
    return meta


def write_generation_report(manifest: dict, out_dir: Path, config_path: Path, manifest_sha: str,
                            elapsed: float, dataset_bytes: int) -> None:
    """Write the mandatory generation report (CONTRACTS §7)."""
    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "generation-report.md"
    summary = manifest["summary"]
    lines = [
        "# Synthetic dataset generation report",
        "",
        "Auto-generated by `ml_training/data/generate.py` (regenerated on every run).",
        "The independent reference check results live in "
        "`ml_training/data/reports/reference-check.json` and are appended to the final",
        "deliverable report after `reference_check.py` runs.",
        "",
        "## Run",
        "",
        "| field | value |",
        "|---|---|",
        "| dataset_id | `%s` |" % manifest["dataset_id"],
        "| variant | `%s` |" % manifest["generator"]["variant"],
        "| output dir | `%s` |" % out_dir.as_posix(),
        "| created_utc | `%s` |" % manifest["created_utc"],
        "| config | `%s` |" % manifest["config_path"],
        "| config_sha256 | `%s` |" % manifest["config_sha256"],
        "| manifest_sha256 | `%s` |" % manifest_sha,
        "| numpy | %s |" % manifest["numpy_version"],
        "| total frames | %d |" % summary["total_frames"],
        "| total bytes | %d (%.1f MiB) |" % (dataset_bytes, dataset_bytes / (1024 * 1024)),
        "| elapsed | %.2f s |" % elapsed,
        "| clipped frames | %d |" % summary["clipped_frames"],
        "| cross-split duplicate IQ | %d |" % summary["cross_split_duplicate_iq"],
        "| intra-split duplicate IQ | %d |" % summary["intra_split_duplicate_iq"],
        "",
        "## Frames per split",
        "",
        "| split | frames | clipped |",
        "|---|---|---|",
    ]
    for split, count in summary["per_split"].items():
        lines.append("| %s | %d | %d |" % (split, count, summary["clipped_frames_by_split"][split]))
    lines.extend([
        "",
        "## Files (npz sha256)",
        "",
        "| path | split | label/kind | frames | clipped | sha256 |",
        "|---|---|---|---|---|---|",
    ])
    for entry in manifest["files"]:
        lines.append("| `%s` | %s | %s | %d | %d | `%s` |" % (
            entry["path"], entry["split"], entry["label"], entry["count"],
            entry["clipped_count"], entry["sha256"]))
    lines.extend([
        "",
        "## Frozen implementation notes",
        "",
        "* fs=60 MSPS, Rs=3 MSym/s, sps=20, RECT, 4096 samples/frame, 207 symbols; "
        "independent i.i.d. bits per frame via `default_rng(symbol_seed).integers(0,2)`.",
        "* Frame-internal peak normalisation (%s): %s == amplitude_codes."
        % (manifest["generator"]["peak_normalisation"],
           "max(max|I|, max|Q|)" if manifest["generator"]["peak_normalisation"] == "component"
           else "max|I+jQ|"),
        "* Impairment order: window offset -> initial phase -> CFO -> complex AWGN "
        "(sigma from measured rotated clean power and stored float32 snr_db).",
        "* Quantisation: np.rint ties-to-even; out-of-12-bit values saturated and flagged "
        "`clipped=True` (frames kept).",
        "* Amplitude cap: min(drawn U[150,1200], floor(2046/(1+%.1f*sqrt(c/(2*snr_lin))))); "
        "c values frozen from CONTRACTS §4; headroom sigma read from config."
        % float(manifest["generator"]["clipping_headroom_sigma"]),
        "* Deterministic npz writer (fixed zip timestamps) => npz sha256 reproducible.",
        "* Gate kinds: OFF=AWGN sigma U[0.5,30]; NOISE=AWGN sigma U[50,600]; "
        "TONE=complex tone A U[200,1200], f U[-5e6,5e6]; CLIP=BPSK peak-normalised to "
        "U[2200,3500] with zero phase/CFO/AWGN (guaranteed saturation).",
        "",
        "## Unmodeled impairments (declared, not simulated)",
        "",
        ", ".join(manifest.get("generator", {}).get("unmodeled", []) or []) or
        "IQ imbalance, DC offset, receive filter/RRC distortion, multipath, phase noise, "
        "AGC transient, hardware frequency response, RF chain non-linearity.",
        "",
    ])
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the ml_training synthetic modulation dataset.")
    parser.add_argument("--config", required=True, type=Path,
                        help="frozen first-round config JSON (ml_training/config/first_round.json)")
    parser.add_argument("--variant", required=True, choices=("formal", "smoke"))
    parser.add_argument("--out", required=True, type=Path,
                        help="dataset directory (name must equal config dataset_id)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = generate_dataset(args.config, args.variant, args.out)
    except ConfigError as error:
        print("[generate] CONFIG ERROR: %s" % (error,), file=sys.stderr)
        return 2
    print("[generate] manifest: %s" % (Path(result["out_dir"]) / "manifest.json",), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
