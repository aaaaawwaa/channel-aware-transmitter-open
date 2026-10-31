"""Synthetic 4096-point RX windows: the constellation contract plus a channel model.

Labels are decided by the GENERATION CONFIGURATION and by nothing else: a cell names a
constellation mode / an OTHER kind / a tone / a background floor, 'label_for_cell()'
re-derives the label from those fields at import time, and neither 'synthesize()' nor
'channel_transform()' can be handed a label, a split or a session -- an unknown channel
key is refused rather than ignored.  A receive window is therefore never relabelled from
what came out of the channel model, and the channel model itself never reads an RX
result (there is no RX here at all).

One session is one generation configuration: one cell, one amplitude condition, one PRBS
seed family.  Same-frame augmentations are recorded with augmentation_parent_id and are
NEVER counted as independent coverage by scripts/rf_ml/dataset.py.

OFF is a background floor of a few LSBs.  It is never RMS-normalised into something that
looks like a sixth signal class.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import dataset as manifest_contract

ROOT = Path(__file__).resolve().parents[2]
SW_DIR = ROOT / "pynq_overlay" / "sw"
if str(SW_DIR) not in sys.path:
    # Appended, never prepended: this directory must not shadow a stdlib module.
    sys.path.append(str(SW_DIR))

import rf_iq_signal          # noqa: E402  (the frozen constellation/shaper contract)
import rf_tx_waveform        # noqa: E402  (the frozen AWG waveform builders)

SCHEMA_VERSION = 1
SAMPLES = 4096
SAMPLE_RATE_HZ = 60000000
RATIO = 0
LO_HZ = 80000000
BANDWIDTH_HZ = 30000000
SPLITS = ("train", "val", "test")
RATES = (2000000, 3000000, 5000000)
SHAPINGS = ("RECT", "RRC0.35")
DELIVERY_MODES = ("BPSK", "QPSK", "QAM16")
HISTORICAL_MODES = ("QAM64",)  # legacy ML datasets only; not delivered capability
DIGITAL_MODES = DELIVERY_MODES + HISTORICAL_MODES
OTHER_KINDS = ("NOISE", "CHIRP", "SQUARE", "MIXED")
SESSIONS_PER_CELL = 3
FRAMES_PER_SESSION = 10
AMPLITUDE_CONDITIONS = (150, 120, 100)
OFF_NOISE_PEAK = (1, 2)
FREQ_OFFSET_LIMIT_HZ = 20000
DEFAULT_SEED = 20261002
GENESIS = b"scripts/rf_ml/synthetic.py synthetic RX generator v1"
SYNTHETIC_RELEASE_ID = hashlib.sha256(GENESIS).hexdigest()[:32]

SPLIT_SEED_POOLS = {"train": (11, 13, 17, 19, 23),
                    "val": (29, 31, 37, 41, 43),
                    "test": (47, 53, 59, 61, 67)}
SPLIT_FAMILIES = {"train": ("synth-train-a", "synth-train-b"),
                  "val": ("synth-val-a", "synth-val-b"),
                  "test": ("synth-test-a", "synth-test-b")}

CHANNEL_TRANSFORM_KEYS = frozenset((
    "phase_rad", "shift_samples", "iq_swap", "conjugate", "freq_offset_hz",
    "noise_sigma_codes", "channel_taps", "noise_seed"))
CHANNEL_TAP_KEYS = frozenset(("gain_q8", "delay_samples"))


@dataclass(frozen=True)
class Cell:
    """One generation configuration.  The label is derived, never supplied."""

    key: str
    label: str
    other_kind: str | None = None
    mode: str | None = None
    symbol_rate_hz: int | None = None
    shaping: str | None = None

    @property
    def digital(self):
        return self.mode is not None

    def tx_reference(self, seed_family):
        """The TX reference a manifest record for this cell must carry."""
        if self.label == "OFF":
            return None
        return {"seed_family": seed_family, "symbol_rate_hz": self.symbol_rate_hz,
                "shaping": self.shaping}


def _build_cells():
    cells = [Cell("TONE", "TONE")]
    for mode in DIGITAL_MODES:
        for rate in RATES:
            for shaping in SHAPINGS:
                cells.append(Cell("%s-%dM-%s" % (mode, rate // 1000000, shaping),
                                  mode, None, mode, rate, shaping))
    for kind in OTHER_KINDS:
        cells.append(Cell("OTHER-%s" % kind, "OTHER", kind))
    cells.append(Cell("OFF", "OFF"))
    return tuple(cells)


CELLS = _build_cells()
_CELL_BY_KEY = {cell.key: cell for cell in CELLS}


def label_for_cell(cell):
    """Derive a cell's label from its generation fields alone."""
    if not isinstance(cell, Cell):
        raise ValueError("label_for_cell takes a Cell")
    if cell.mode is not None:
        return cell.mode
    if cell.other_kind is not None:
        return "OTHER"
    return cell.key


def cell_by_key(key):
    try:
        return _CELL_BY_KEY[key]
    except KeyError:
        raise ValueError("unknown synthetic cell: %r" % (key,))


for _cell in CELLS:
    if label_for_cell(_cell) != _cell.label:
        raise RuntimeError("cell %s cannot be labelled %r" % (_cell.key, _cell.label))


def seed_family(split, index):
    """The seed family for one session of one split (never shared across splits)."""
    if split not in SPLIT_FAMILIES:
        raise ValueError("unknown split: %r" % (split,))
    families = SPLIT_FAMILIES[split]
    return families[index % len(families)]


def prbs_seed_for(split, index):
    """A PRBS7 seed from this split's own pool (no seed is shared across splits)."""
    if split not in SPLIT_SEED_POOLS:
        raise ValueError("unknown split: %r" % (split,))
    pool = SPLIT_SEED_POOLS[split]
    return pool[index % len(pool)]


def _mix(*parts):
    """A deterministic non-negative integer from arbitrary parts (never PYTHONHASHSEED)."""
    text = "|".join(str(part) for part in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_amplitude(amplitude):
    if type(amplitude) is not int or not 1 <= amplitude <= 2047:
        raise ValueError("the declared receive peak must be an integer 1..2047")
    return amplitude


def _check_seed(seed):
    if type(seed) is not int or not 1 <= seed <= 127:
        raise ValueError("the PRBS7 seed must be an integer 1..127")
    return seed


def receive_window(source, offset=0, samples=SAMPLES):
    """Cut exactly 'samples' contiguous samples out of a periodic source.

    A source too short to fill the window is refused; it is never zero-padded into a
    frame the transmitter could not have produced.
    """
    array = np.asarray(source, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2 or array.shape[0] < 1:
        raise ValueError("a receive window needs a non-empty (N, 2) periodic source")
    if type(offset) is not int or offset < 0:
        raise ValueError("the window offset must be a non-negative integer")
    period = array.shape[0]
    repeats = int(math.ceil((offset + samples) / float(period)))
    window = np.tile(array, (repeats, 1))[offset:offset + samples]
    if window.shape != (samples, 2):
        raise ValueError("the source could not fill a complete receive window")
    return window


def _check_transform(params):
    if not isinstance(params, dict):
        raise ValueError("the channel transform must be a mapping")
    unknown = sorted(set(params) - CHANNEL_TRANSFORM_KEYS)
    if unknown:
        raise ValueError("the channel transform does not take " + unknown[0])
    checked = {}
    for name in ("phase_rad", "freq_offset_hz", "noise_sigma_codes"):
        value = params.get(name, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("channel transform %s must be a finite number" % name)
        checked[name] = float(value)
    shift = params.get("shift_samples", 0)
    if type(shift) is not int or not 0 <= shift < SAMPLES:
        raise ValueError("channel transform shift_samples must be an integer 0..%d" % (SAMPLES - 1))
    checked["shift_samples"] = shift
    for name in ("iq_swap", "conjugate"):
        value = params.get(name, False)
        if not isinstance(value, bool):
            raise ValueError("channel transform %s must be a bool" % name)
        checked[name] = value
    seed = params.get("noise_seed", 0)
    if type(seed) is not int or seed < 0:
        raise ValueError("channel transform noise_seed must be a non-negative integer")
    checked["noise_seed"] = seed
    if abs(checked["freq_offset_hz"]) > FREQ_OFFSET_LIMIT_HZ:
        raise ValueError("channel transform freq_offset_hz must stay within +/-%d" % FREQ_OFFSET_LIMIT_HZ)
    taps = params.get("channel_taps")
    if taps is not None:
        if not isinstance(taps, (list, tuple)) or not taps:
            raise ValueError("channel transform channel_taps must be a non-empty sequence")
        checked["channel_taps"] = []
        for tap in taps:
            if not isinstance(tap, dict) or set(tap) != CHANNEL_TAP_KEYS:
                raise ValueError("each channel tap must carry gain_q8 and delay_samples only")
            if type(tap["gain_q8"]) is not int or not -256 <= tap["gain_q8"] <= 255:
                raise ValueError("channel tap gain_q8 must be an integer -256..255")
            if type(tap["delay_samples"]) is not int or not 0 <= tap["delay_samples"] <= 511:
                raise ValueError("channel tap delay_samples must be an integer 0..511")
            checked["channel_taps"].append(dict(tap))
    else:
        checked["channel_taps"] = None
    return checked


def channel_transform(iq, params):
    """Apply one receive-side channel realisation to an (N, 2) float IQ window.

    Phase, time origin (a circular shift of THIS window), I/Q swap, conjugation, a
    slight frequency offset, additive noise and the frozen FIR tap model.  Every
    parameter is numeric; there is nothing here that could carry a label, and the
    input is a window that was generated, never a received result.
    """
    checked = _check_transform(params)
    values = np.array(iq, dtype=float, copy=True)
    if values.ndim != 2 or values.shape[1] != 2 or values.shape[0] < 1:
        raise ValueError("the channel transform needs a non-empty (N, 2) IQ window")
    if checked["iq_swap"]:
        values = values[:, ::-1].copy()
    if checked["conjugate"]:
        values[:, 1] = -values[:, 1]
    if checked["channel_taps"]:
        values = rf_iq_signal.apply_channel_impairment(
            values, {"enabled": True, "taps": checked["channel_taps"],
                     "noise_pct": 0, "noise_seed": 0})
    complex_window = values[:, 0] + 1j * values[:, 1]
    index = np.arange(values.shape[0], dtype=float)
    complex_window = complex_window * np.exp(
        1j * (2.0 * math.pi * checked["freq_offset_hz"] * index / SAMPLE_RATE_HZ
              + checked["phase_rad"]))
    values = np.stack((complex_window.real, complex_window.imag), axis=1)
    if checked["shift_samples"]:
        values = np.roll(values, checked["shift_samples"], axis=0)
    if checked["noise_sigma_codes"]:
        generator = np.random.default_rng(_mix("noise", checked["noise_seed"]) & 0xFFFFFFFF)
        values = values + generator.normal(0.0, checked["noise_sigma_codes"], size=values.shape)
    return values


_DIGITAL_CACHE = {}


def _digital_source(cell, seed, amplitude):
    key = (cell.key, seed, amplitude)
    cached = _DIGITAL_CACHE.get(key)
    if cached is None:
        args = {"mode": cell.mode,
                "filter": "RRC" if cell.shaping == "RRC0.35" else "RECT",
                "rolloff": "0.35", "seed": seed, "symbol_rate_hz": cell.symbol_rate_hz,
                "sample_rate_hz": SAMPLE_RATE_HZ, "amplitude": amplitude}
        metadata, payload = rf_iq_signal.generate_digital_frame(
        args, allow_historical_qam64=cell.mode in HISTORICAL_MODES)
        cached = np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(float)
        if cached.shape[0] != metadata["samples"]:
            raise RuntimeError("the constellation contract returned an inconsistent frame")
        _DIGITAL_CACHE[key] = cached
    return cached


def source_iq(cell, seed, amplitude):
    """The generated (pre-channel) periodic source for one cell at one amplitude."""
    _check_seed(seed)
    _check_amplitude(amplitude)
    if cell.digital:
        return _digital_source(cell, seed, amplitude)
    if cell.label == "TONE":
        cycles = random.Random(_mix(cell.key, seed, "tone")).choice((13, 32, 61))
        payload = rf_tx_waveform.build_sine({"cycles": cycles, "amplitude": amplitude})[1]
    elif cell.other_kind == "NOISE":
        generator = np.random.default_rng(_mix(cell.key, seed, "noise") & 0xFFFFFFFF)
        return generator.integers(-amplitude, amplitude + 1, size=(1024, 2)).astype(float)
    elif cell.other_kind == "CHIRP":
        payload = rf_tx_waveform.build_chirp(
            {"cycles": 8, "amplitude": amplitude, "chirp_f1_cycles": 80})[1]
    elif cell.other_kind == "SQUARE":
        payload = rf_tx_waveform.build_square({"cycles": 16, "amplitude": amplitude})[1]
    elif cell.other_kind == "MIXED":
        half = max(1, amplitude // 2)
        payload = rf_tx_waveform.build_multitone(
            {"cycles": 8, "amplitude": half, "tone2_cycles": 48,
             "tone2_amplitude": half})[1]
    else:
        raise ValueError("cell %s has no source" % cell.key)
    return np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(float)


def _pack(values):
    """Quantize through the frozen contract, then pack the exact bytes it would pack."""
    rounded = rf_iq_signal.quantize_iq(values)
    payload = rounded.astype("<i2").tobytes()
    if len(payload) != SAMPLES * 4:
        raise RuntimeError("a synthetic frame must be exactly %d bytes" % (SAMPLES * 4))
    return payload, rounded


def synthesize(cell, *, seed, transform, amplitude):
    """Return one complete 4096-sample s16_iq_le receive window for 'cell'.

    The declared receive peak is the frame's realised peak code: the channel tap model
    supplies relative gain and delay, and the absolute receive level is the amplitude
    condition.  Generation never emits a rail sample and never emits a short frame.
    """
    if not isinstance(cell, Cell):
        raise ValueError("synthesize takes a Cell, not %s" % type(cell).__name__)
    checked = _check_transform(transform)
    peak = _check_amplitude(amplitude)
    _check_seed(seed)
    if cell.label == "OFF":
        # A floor is not a waveform, so no channel is applied; it is still an independent
        # realisation per window, keyed by the frame's own noise seed, so two windows of
        # one session can never be the same bytes.
        realisation = _mix(cell.key, seed, checked["noise_seed"], "floor")
        generator = np.random.default_rng(realisation & 0xFFFFFFFF)
        floor = OFF_NOISE_PEAK[realisation % len(OFF_NOISE_PEAK)]
        window = generator.integers(-floor, floor + 1, size=(SAMPLES, 2)).astype(float)
    else:
        window = receive_window(source_iq(cell, seed, peak), 0)
        window = channel_transform(window, checked)
        observed = float(np.max(np.abs(window)))
        if not math.isfinite(observed) or observed <= 0.0:
            raise ValueError("the generated source is degenerate")
        window = window * (float(peak) / observed)
    payload, rounded = _pack(window)
    if int(np.count_nonzero((rounded <= -2048) | (rounded >= 2047))):
        raise RuntimeError("generation produced a rail sample")
    return payload


def _draw_transform(rng):
    """One channel realisation drawn from a deterministic stream."""
    taps = None
    if rng.random() < 0.5:
        taps = [{"gain_q8": 255, "delay_samples": 0}]
        for _ in range(3):
            taps.append({"gain_q8": rng.choice((0, 16, 32, 64)),
                         "delay_samples": rng.randrange(1, 24)})
    return {"phase_rad": rng.uniform(-math.pi, math.pi),
            "shift_samples": rng.randrange(0, SAMPLES),
            "iq_swap": rng.random() < 0.25,
            "conjugate": rng.random() < 0.25,
            "freq_offset_hz": rng.uniform(-FREQ_OFFSET_LIMIT_HZ, FREQ_OFFSET_LIMIT_HZ),
            "noise_sigma_codes": rng.choice((0.0, 0.5, 1.0, 2.0)),
            "channel_taps": taps,
            "noise_seed": rng.randrange(0, 2 ** 31)}


def scan_duplicate_hashes(source):
    """Return the sorted SHA256 values that appear more than once.

    'source' is either a directory (its .iq files are scanned) or an iterable of paths.
    A reused capture buffer is exactly the failure this catches.
    """
    if isinstance(source, (str, Path)) and Path(source).is_dir():
        paths = sorted(Path(source).rglob("*.iq"))
    else:
        paths = [Path(item) for item in source]
    seen, duplicates = {}, set()
    for path in paths:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if digest in seen:
            duplicates.add(digest)
        else:
            seen[digest] = path
    return sorted(duplicates)


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def generate(output, *, splits=SPLITS, cells=CELLS, sessions=SESSIONS_PER_CELL,
             frames=FRAMES_PER_SESSION, augment_per_session=2, seed=DEFAULT_SEED):
    """Write a complete synthetic corpus (manifest + IQ + companion JSON).

    Refuses to write into a directory that already exists, so a corpus is never
    silently merged with an older one.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError("the output directory already exists: %s" % output)
    splits = tuple(splits)
    cells = tuple(cells)
    for split in splits:
        if split not in SPLITS:
            raise ValueError("unknown split: %r" % (split,))
    for cell in cells:
        if not isinstance(cell, Cell):
            raise ValueError("generate takes Cells, not %s" % type(cell).__name__)
    if not splits or not cells:
        raise ValueError("a corpus needs at least one split and one cell")
    if type(sessions) is not int or sessions < 1:
        raise ValueError("sessions must be a positive integer")
    if type(frames) is not int or frames < 1:
        raise ValueError("frames must be a positive integer")
    if type(augment_per_session) is not int or augment_per_session < 0:
        raise ValueError("augment_per_session must be a non-negative integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")

    (output / "iq").mkdir(parents=True)
    frozen_at = _utc_now()
    started = time.monotonic()
    manifest = {"schema_version": SCHEMA_VERSION, "sessions": [], "frames": []}
    companion = {"generator": "scripts/rf_ml/synthetic.py", "schema_version": SCHEMA_VERSION,
                 "synthetic_release_id": SYNTHETIC_RELEASE_ID, "seed": seed,
                 "split_frozen_at_utc": frozen_at, "cells": [cell.key for cell in cells],
                 "amplitude_conditions": list(AMPLITUDE_CONDITIONS),
                 "off_noise_peak": list(OFF_NOISE_PEAK),
                 "configuration": {"sample_rate_hz": SAMPLE_RATE_HZ, "ratio": RATIO,
                                   "lo_hz": LO_HZ, "bandwidth_hz": BANDWIDTH_HZ},
                 "split_families": {split: list(SPLIT_FAMILIES[split]) for split in splits},
                 "split_seed_pools": {split: list(SPLIT_SEED_POOLS[split]) for split in splits},
                 "sessions": []}
    digest_owner = {}
    counts = {split: {} for split in splits}
    total_bytes = 0

    for split in splits:
        for cell_index, cell in enumerate(cells):
            for session_index in range(sessions):
                amplitude = AMPLITUDE_CONDITIONS[(cell_index + session_index)
                                                 % len(AMPLITUDE_CONDITIONS)]
                family = seed_family(split, cell_index + session_index)
                session_id = "syn-%s-%s-s%02d-amp%03d" % (split, cell.key.lower(),
                                                          session_index, amplitude)
                seeds = [prbs_seed_for(split, session_index + offset)
                         for offset in range(frames)]
                manifest["sessions"].append(
                    {"session_id": session_id, "split": split, "origin": "synthetic",
                     "split_frozen_at_utc": frozen_at})
                record = {"session_id": session_id, "split": split, "cell": cell.key,
                          "seed_family": family, "amplitude": amplitude,
                          "prbs_seeds": list(seeds), "frames": [], "augmentations": []}
                bucket = counts[split].setdefault(cell.key, {"sessions": 0, "frames": 0,
                                                             "augmentations": 0})
                bucket["sessions"] += 1

                def emit(capture_id, frame_seed, params, parent_id):
                    payload = synthesize(cell, seed=frame_seed, transform=params,
                                         amplitude=amplitude)
                    digest = hashlib.sha256(payload).hexdigest()
                    if digest in digest_owner:
                        raise RuntimeError("duplicate synthetic IQ for %s (%s)"
                                           % (capture_id, digest_owner[digest]))
                    digest_owner[digest] = capture_id
                    relative = "iq/%s.iq" % capture_id
                    (output / relative).write_bytes(payload)
                    realised = int(np.max(np.abs(np.frombuffer(payload, dtype="<i2"))))
                    manifest["frames"].append({
                        "capture_id": capture_id, "session_id": session_id, "split": split,
                        "origin": "synthetic", "label": cell.label,
                        "other_kind": cell.other_kind, "iq_path": relative,
                        "iq_sha256": digest, "format": "s16_iq_le", "samples": SAMPLES,
                        "release_id": SYNTHETIC_RELEASE_ID,
                        "configuration": {"sample_rate_hz": SAMPLE_RATE_HZ, "ratio": RATIO,
                                          "lo_hz": LO_HZ, "bandwidth_hz": BANDWIDTH_HZ},
                        "tx_reference": cell.tx_reference(family),
                        "agc": {"mode": "synthetic_peak_%03d" % amplitude, "gain_db": None},
                        "captured_at_utc": _utc_now(),
                        "completed_monotonic_s": round(time.monotonic() - started, 6),
                        "complete": True, "clipping_count": 0, "exclusion_reasons": [],
                        "augmentation_parent_id": parent_id})
                    (record["augmentations"] if parent_id else record["frames"]).append(
                        {"capture_id": capture_id, "seed": frame_seed, "sha256": digest,
                         "transform": params, "realised_peak_code": realised,
                         "augmentation_parent_id": parent_id})

                for frame_index in range(frames):
                    params = _draw_transform(random.Random(
                        _mix(session_id, seed, frame_index, "frame")))
                    emit("%s-f%02d" % (session_id, frame_index),
                         seeds[frame_index % len(seeds)], params, None)
                    bucket["frames"] += 1
                    total_bytes += SAMPLES * 4
                # Validation and test must exercise original grouped windows only;
                # generated augmentations are additional training inputs.
                for augment_index in range(augment_per_session if split == "train" else 0):
                    parent = record["frames"][augment_index % len(record["frames"])]
                    params = _draw_transform(random.Random(
                        _mix(session_id, seed, augment_index, "augment")))
                    emit("%s-a%02d" % (session_id, augment_index), parent["seed"], params,
                         parent["capture_id"])
                    bucket["augmentations"] += 1
                    total_bytes += SAMPLES * 4
                companion["sessions"].append(record)

    require_coverage = (set(cells) == set(CELLS) and set(splits) == set(SPLITS)
                        and sessions >= SESSIONS_PER_CELL and frames >= FRAMES_PER_SESSION)
    result = manifest_contract.validate_manifest(manifest, output,
                                                 coverage_origin="synthetic")
    duplicates = scan_duplicate_hashes(output)
    usable = sum(1 for frame in manifest["frames"] if not frame["exclusion_reasons"])
    summary = {"generator": "scripts/rf_ml/synthetic.py", "schema_version": SCHEMA_VERSION,
               "synthetic_release_id": SYNTHETIC_RELEASE_ID,
               "split_frozen_at_utc": frozen_at, "seed": seed,
               "requested": {"splits": list(splits), "cells": [cell.key for cell in cells],
                             "sessions": sessions, "frames": frames,
                             "augment_per_session": augment_per_session},
               "total_frames": len(manifest["frames"]), "usable_frames": usable,
               "total_bytes": total_bytes, "cells": counts, "duplicate_hashes": duplicates,
               "validation": result.to_dict()}
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "generator.json", companion)
    _write_json(output / "summary.json", summary)
    if duplicates:
        raise RuntimeError("the corpus contains duplicate IQ hashes: %s" % duplicates)
    if require_coverage and result.coverage_missing:
        missing = sorted({(row["split"], row["label"], row["other_kind"],
                           row["symbol_rate_hz"], row["shaping"])
                          for row in result.coverage_missing})
        raise RuntimeError("the synthetic corpus does not cover the frozen matrix: %s"
                           % missing[:8])
    return summary


def _parse_choice_list(text, allowed, name):
    values = [item.strip() for item in str(text).split(",") if item.strip()]
    if not values:
        raise ValueError("no %s was requested" % name)
    unknown = [item for item in values if item not in allowed]
    if unknown:
        raise ValueError("unknown %s: %s" % (name, ", ".join(sorted(unknown))))
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m scripts.rf_ml.synthetic",
        description="Generate the grouped synthetic RX corpus (manifest + IQ files).")
    parser.add_argument("--output", type=Path, required=True,
                        help="new corpus directory; an existing one is never overwritten")
    parser.add_argument("--splits", default=",".join(SPLITS))
    parser.add_argument("--cells", default=",".join(cell.key for cell in CELLS),
                        help="comma separated cell keys (see CELLS in the module)")
    parser.add_argument("--sessions", type=int, default=SESSIONS_PER_CELL)
    parser.add_argument("--frames", type=int, default=FRAMES_PER_SESSION)
    parser.add_argument("--augment-per-session", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    try:
        splits = _parse_choice_list(args.splits, SPLITS, "split")
        cells = [cell_by_key(key) for key in
                 _parse_choice_list(args.cells, set(_CELL_BY_KEY), "cell")]
        summary = generate(args.output, splits=splits, cells=cells, sessions=args.sessions,
                           frames=args.frames,
                           augment_per_session=args.augment_per_session, seed=args.seed)
    except (ValueError, FileExistsError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "output": str(args.output),
                      "total_frames": summary["total_frames"],
                      "duplicate_hashes": summary["duplicate_hashes"],
                      "coverage_missing": len(summary["validation"]["coverage_missing"])},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
