"""Tests for the synthetic dataset generator (ml_training/data/generate.py).

Covers the frozen contract §3-§7: shapes/dtypes, bit mapping vs the product
mapper, per-frame independent bits, quantization (ties-to-even + saturation),
amplitude safety cap, SNR residual, gate construction, split isolation, duplicate
freedom, manifest fields and byte-level determinism.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

DATA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from data_test_support import BASE_CONFIG_PATH, REPO_ROOT, read_manifest, read_npz  # noqa: E402

import generate  # noqa: E402

BASE_CONFIG = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
HEADROOM = float(BASE_CONFIG["dataset"]["impairment"]["clipping_headroom_sigma"])


def load_product_mapper():
    path = REPO_ROOT / "pynq_overlay" / "sw" / "rf_iq_signal.py"
    spec = importlib.util.spec_from_file_location("_product_rf_iq_signal_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.map_bits_to_symbol


# ---------------------------------------------------------------------------
# dataset-level contract checks on the tiny generated dataset
# ---------------------------------------------------------------------------
def test_manifest_required_fields(tiny_dataset):
    manifest = tiny_dataset["manifest"]
    for key in ("schema", "dataset_id", "created_utc", "config_path", "config_sha256",
                "numpy_version", "waveform", "classes", "gate_kinds", "splits", "files", "summary"):
        assert key in manifest, key
    assert manifest["schema"] == "ml-modrec-synthetic-v1"
    assert manifest["dataset_id"] == "sim-smoke"
    assert manifest["classes"] == ["BPSK", "QPSK", "QAM16", "QAM64"]
    assert manifest["gate_kinds"] == ["OFF", "NOISE", "TONE", "CLIP"]
    assert manifest["waveform"] == {
        "fs_hz": 60_000_000, "symbol_rate_hz": 3_000_000, "sps": 20,
        "shaping": "RECT", "samples": 4096, "n_symbols": 207,
    }
    assert manifest["config_sha256"] == generate.sha256_file(tiny_dataset["config_path"])
    assert manifest["generator"]["peak_normalisation"] == "complex"
    assert manifest["generator"]["clipping_headroom_sigma"] == HEADROOM
    assert manifest["generator"]["safety_coefficients"] == {
        "BPSK": 1.0, "QPSK": 1.0, "QAM16": 5.0 / 9.0, "QAM64": 3.0 / 7.0,
    }


def test_summary_counts_and_files(tiny_dataset):
    manifest = tiny_dataset["manifest"]
    summary = manifest["summary"]
    assert summary["total_frames"] == 96
    assert summary["per_split"] == {"train": 24, "val": 16, "test": 24, "gate_cal": 16, "gate_test": 16}
    assert [entry["path"] for entry in manifest["files"]] == [
        "frames/train-BPSK.npz", "frames/train-QPSK.npz", "frames/train-QAM16.npz", "frames/train-QAM64.npz",
        "frames/val-BPSK.npz", "frames/val-QPSK.npz", "frames/val-QAM16.npz", "frames/val-QAM64.npz",
        "frames/test-BPSK.npz", "frames/test-QPSK.npz", "frames/test-QAM16.npz", "frames/test-QAM64.npz",
        "gate/gate_cal-OFF.npz", "gate/gate_cal-NOISE.npz", "gate/gate_cal-TONE.npz", "gate/gate_cal-CLIP.npz",
        "gate/gate_test-OFF.npz", "gate/gate_test-NOISE.npz", "gate/gate_test-TONE.npz", "gate/gate_test-CLIP.npz",
    ]
    assert sum(entry["count"] for entry in manifest["files"]) == summary["total_frames"]
    assert sum(entry["clipped_count"] for entry in manifest["files"]) == summary["clipped_frames"]
    assert summary["cross_split_duplicate_iq"] == 0
    assert summary["intra_split_duplicate_iq"] == 0


@pytest.mark.parametrize("split,label", [("train", "BPSK"), ("test", "QAM64"),
                                         ("gate_cal", "OFF"), ("gate_test", "CLIP")])
def test_npz_arrays_dtypes_and_rails(tiny_dataset, split, label):
    manifest = tiny_dataset["manifest"]
    entry = next(item for item in manifest["files"]
                 if item["split"] == split and item["label"] == label)
    arrays = read_npz(tiny_dataset["dir"], entry["path"])
    assert arrays["X"].dtype == np.int16
    assert arrays["X"].shape[1:] == (4096, 2)
    assert arrays["frame_id"].dtype.kind == "U"
    assert arrays["session_id"].dtype.kind == "U"
    assert arrays["label"].dtype.kind == "U"
    assert arrays["seed_family"].dtype.kind == "U"
    assert arrays["iq_sha256"].dtype.kind == "U"
    for name in ("symbol_seed", "noise_seed"):
        assert arrays[name].dtype == np.uint64
    for name in ("snr_db", "phase_rad", "cfo_hz", "noise_sigma_codes"):
        assert arrays[name].dtype == np.float32
    for name in ("amplitude_codes", "window_offset"):
        assert arrays[name].dtype == np.int16
    assert arrays["clipped"].dtype == np.bool_
    assert arrays["X"].min() >= -2048 and arrays["X"].max() <= 2047
    rails = np.any((arrays["X"] == -2048) | (arrays["X"] == 2047), axis=(1, 2))
    assert np.array_equal(rails, arrays["clipped"])
    for index in range(min(3, len(arrays["X"]))):
        digest = hashlib.sha256(
            np.ascontiguousarray(arrays["X"][index], dtype="<i2").tobytes()).hexdigest()
        assert arrays["iq_sha256"][index] == digest
    assert entry["count"] == int(arrays["X"].shape[0])


def test_frame_metadata_and_split_isolation(tiny_dataset):
    manifest = tiny_dataset["manifest"]
    frames = {}
    for entry in manifest["files"]:
        frames[entry["path"]] = (entry, read_npz(tiny_dataset["dir"], entry["path"]))
    seen_digests = {}
    families = {}
    for path, (entry, arrays) in frames.items():
        split_families = families.setdefault(entry["split"], set())
        for index in range(len(arrays["X"])):
            frame_id = str(arrays["frame_id"][index])
            session_id = str(arrays["session_id"][index])
            assert frame_id.startswith(session_id + "-")
            assert arrays["label"][index] == entry["label"]
            split_families.add(str(arrays["seed_family"][index]))
            digest = str(arrays["iq_sha256"][index])
            assert digest not in seen_digests, ("duplicate frame", path, index)
            seen_digests[digest] = (entry["split"], frame_id)
    assert not (families["train"] & families["val"])
    assert not (families["train"] & families["test"])
    assert not (families["val"] & families["test"])
    # test SNR grid is exact per session
    for path, (entry, arrays) in frames.items():
        if entry["split"] != "test":
            continue
        assert sorted(np.unique(arrays["snr_db"]).tolist()) == [0.0, 10.0]


def test_gate_kind_construction(tiny_dataset):
    manifest = tiny_dataset["manifest"]
    config = json.loads(tiny_dataset["config_path"].read_text(encoding="utf-8"))
    impairment = config["dataset"]["impairment"]
    off_low, off_high = impairment["gate_off_sigma_codes_uniform"]
    noise_low, noise_high = impairment["gate_noise_sigma_codes_uniform"]
    tone_cfg = impairment["gate_tone"]
    clip_low, clip_high = impairment["gate_clip_amplitude_codes_uniform"]
    for kind in generate.GATE_KINDS:
        entry = next(item for item in manifest["files"] if item["label"] == kind)
        arrays = read_npz(tiny_dataset["dir"], entry["path"])
        assert np.all(np.isnan(arrays["snr_db"]))
        if kind in ("OFF", "NOISE"):
            low, high = ((off_low, off_high) if kind == "OFF" else (noise_low, noise_high))
            assert np.all(arrays["amplitude_codes"] == 0)
            assert np.all(arrays["phase_rad"] == 0.0)
            assert np.all(arrays["cfo_hz"] == 0.0)
            assert np.all(arrays["noise_sigma_codes"] >= low - 1e-4)
            assert np.all(arrays["noise_sigma_codes"] <= high + 1e-4)
        elif kind == "TONE":
            assert np.all(arrays["noise_sigma_codes"] == 0.0)
            assert np.all(arrays["amplitude_codes"] >= tone_cfg["amplitude_codes_uniform"][0])
            assert np.all(arrays["amplitude_codes"] <= tone_cfg["amplitude_codes_uniform"][1])
            assert np.all(arrays["cfo_hz"] >= tone_cfg["freq_hz_uniform"][0] - 1e-3)
            assert np.all(arrays["cfo_hz"] <= tone_cfg["freq_hz_uniform"][1] + 1e-3)
        else:  # CLIP
            assert np.all(arrays["clipped"])
            assert np.all(arrays["noise_sigma_codes"] == 0.0)
            assert np.all(arrays["amplitude_codes"] >= clip_low)
            assert np.all(arrays["amplitude_codes"] <= clip_high)
            rails = np.any((arrays["X"] == -2048) | (arrays["X"] == 2047), axis=(1, 2))
            assert np.all(rails)


# ---------------------------------------------------------------------------
# generator primitives
# ---------------------------------------------------------------------------
def test_bit_mapping_matches_product_for_every_pattern():
    product = load_product_mapper()
    patterns = 0
    for mode, bits_per_symbol in generate.BITS_PER_SYMBOL.items():
        for bits in __import__("itertools").product((0, 1), repeat=bits_per_symbol):
            expected = product(mode, bits)
            actual = generate.map_bits_to_symbol(mode, bits)
            assert tuple(float(value) for value in actual) == tuple(float(value) for value in expected), (mode, bits)
            patterns += 1
    assert patterns == 2 + 4 + 16 + 64


def test_vectorised_mapping_matches_scalar_mapping():
    rng = np.random.default_rng(7)
    for mode, bits_per_symbol in generate.BITS_PER_SYMBOL.items():
        n_symbols = 13
        bits = rng.integers(0, 2, size=n_symbols * bits_per_symbol).astype(np.uint8)
        vectorised = generate.symbols_from_bits(mode, bits, n_symbols)
        scalar = np.array([generate.map_bits_to_symbol(mode, bits[index * bits_per_symbol:(index + 1) * bits_per_symbol])
                           for index in range(n_symbols)], dtype=np.float64)
        assert np.array_equal(vectorised, scalar), mode


def test_bits_are_independent_iid_per_frame():
    seed_a, seed_b = 1234, 9876
    bits_a = generate.bits_from_seed(seed_a, "QAM64", 207)
    bits_b = generate.bits_from_seed(seed_b, "QAM64", 207)
    assert not np.array_equal(bits_a, bits_b)
    # no PRBS-style shift relation between frames (cross-correlation of the bits
    # at any non-zero circular shift must not reproduce the other frame)
    for shift in (1, 7, 64, 138, 500, 1000):
        assert not np.array_equal(bits_a, np.roll(bits_b, shift))
    # i.i.d. uniform sanity (loose bound: ~0.5 mean over 1242 bits)
    assert abs(float(bits_a.mean()) - 0.5) < 0.05
    # different symbol seeds give different frames
    frames = {generate.sha256_bytes(generate.bits_from_seed(seed, "BPSK", 207).tobytes())
              for seed in range(50)}
    assert len(frames) == 50


def test_quantization_ties_to_even_and_saturation():
    probe = np.array([[0.5, 1.5], [2.5, -0.5], [-1.5, -2.5], [3.5, -3.5]])
    X, clipped = generate.quantize_with_clip(probe)
    assert X.tolist() == [[0, 2], [2, 0], [-2, -2], [4, -4]]
    assert clipped is False
    X, clipped = generate.quantize_with_clip(np.array([[2047.5, -2048.5], [5000.0, -5000.0]]))
    assert X.tolist() == [[2047, -2048], [2047, -2048]]
    assert clipped is True
    assert X.dtype == np.int16


@pytest.mark.parametrize("mode", list(generate.CLASS_ORDER))
def test_frame_synthesis_peak_snr_and_cap(mode):
    config = generate.load_config(BASE_CONFIG_PATH)
    waveform = config["dataset"]["waveform"]
    impairment = config["dataset"]["impairment"]
    for snr_db in (-6.0, 0.0, 10.0, 20.0):
        cap = generate.safe_amplitude_cap(generate.SAFETY_C[mode], snr_db, HEADROOM)
        assert cap >= 64
        assert cap == int(math.floor(2046.0 / (
            1.0 + HEADROOM * math.sqrt(generate.SAFETY_C[mode] / (2.0 * generate.snr_linear(snr_db))))))
        amplitude = min(1200, cap)
        frame = generate.build_class_frame(
            mode=mode, symbol_seed=11, noise_seed=22, snr_db=snr_db,
            amplitude_codes=amplitude, phase_rad=0.5, cfo_hz=12345.0,
            window_offset=7, waveform=waveform,
        )
        assert frame["X"].shape == (4096, 2)
        assert frame["X"].dtype == np.int16
        clean = frame["clean"]
        envelope = np.abs(clean)
        assert float(np.max(envelope)) <= amplitude + 1e-9
        assert abs(float(frame["noise_sigma_codes"])
                   - math.sqrt(float(np.mean(envelope ** 2)) / (2.0 * generate.snr_linear(snr_db)))) < 1e-3
        # measured residual variance (noise + quantization noise) matches sigma
        clean_2d = np.stack([clean.real, clean.imag], axis=1)
        residual = frame["X"].astype(np.float64) - clean_2d
        measured = float(np.mean(residual * residual))
        expected = float(frame["noise_sigma_codes"]) ** 2 + 1.0 / 12.0
        assert 0.75 <= measured / expected <= 1.25, (mode, snr_db, measured, expected)


def test_peak_normalisation_is_complex_envelope():
    config = generate.load_config(BASE_CONFIG_PATH)
    waveform = config["dataset"]["waveform"]
    for mode in generate.CLASS_ORDER:
        bits = generate.bits_from_seed(99, mode, int(waveform["n_symbols"]))
        symbols = generate.symbols_from_bits(mode, bits, int(waveform["n_symbols"]))
        wave = generate.rect_waveform(symbols, int(waveform["sps"]))
        scaled, peak = generate.peak_normalise(wave, 600)
        envelope = np.sqrt(scaled[:, 0] ** 2 + scaled[:, 1] ** 2)
        assert abs(float(np.max(envelope)) - 600.0) < 1e-9, mode
        assert peak > 0.0


def test_bits_per_symbol_and_n_symbols_frozen():
    assert generate.BITS_PER_SYMBOL == {"BPSK": 1, "QPSK": 2, "QAM16": 4, "QAM64": 6}
    assert int(math.ceil((4096 + 19) / 20)) + 1 == 207


# ---------------------------------------------------------------------------
# formal plan / counts from the real frozen config
# ---------------------------------------------------------------------------
def test_formal_plan_counts_and_grid():
    config = generate.load_config(BASE_CONFIG_PATH)
    generate.validate_config(config)
    plan = generate.variant_plan(config, "formal")
    rows = []
    for entry in plan["files"]:
        rows.append((entry["split"], entry["label"], sum(session["frames"] for session in entry["sessions"])))
    counts = {}
    for split, label, count in rows:
        counts.setdefault(split, {})[label] = count
    splits = config["dataset"]["formal"]["splits"]
    for label in generate.CLASS_ORDER:
        assert counts["train"][label] == splits["train"]["sessions_per_label"] * splits["train"]["frames_per_session"] == 1200
        assert counts["val"][label] == splits["val"]["sessions_per_label"] * splits["val"]["frames_per_session"] == 300
        assert counts["test"][label] == (len(splits["test"]["snr_db_grid"])
                                         * splits["test"]["frames_per_class_per_snr"]) == 600
    for kind in generate.GATE_KINDS:
        assert counts["gate_cal"][kind] == splits["gate_cal"]["frames_per_kind"] == 100
        assert counts["gate_test"][kind] == splits["gate_test"]["frames_per_kind"] == 100
    assert sum(sum(kind.values()) for kind in counts.values()) == 9200
    # families are disjoint across splits
    families = {}
    for split in ("train", "val", "test", "gate_cal", "gate_test"):
        key = "families" if split in ("gate_cal", "gate_test") else "seed_families"
        families[split] = set(splits[split][key])
    split_names = list(families)
    for left in range(len(split_names)):
        for right in range(left + 1, len(split_names)):
            assert not (families[split_names[left]] & families[split_names[right]])
    # test sessions cover every (label, snr) cell once
    test_sessions = [session for entry in plan["files"] if entry["split"] == "test"
                     for session in entry["sessions"]]
    assert len(test_sessions) == 48
    assert sorted(float(session["fixed_snr_db"]) for session in test_sessions
                  if session["label"] == "BPSK") == [float(value) for value in splits["test"]["snr_db_grid"]]


# ---------------------------------------------------------------------------
# determinism / CLI
# ---------------------------------------------------------------------------
def test_generation_is_deterministic(tiny_config_path, tmp_path):
    config_path = tiny_config_path
    first = tmp_path / "run1" / "sim-smoke"
    second = tmp_path / "run2" / "sim-smoke"
    result_a = generate.generate_dataset(config_path, "smoke", first)
    result_b = generate.generate_dataset(config_path, "smoke", second)
    assert [entry["sha256"] for entry in result_a["manifest"]["files"]] == \
           [entry["sha256"] for entry in result_b["manifest"]["files"]]
    assert result_a["manifest_sha256"] != "" and result_b["manifest_sha256"] != ""
    for entry in result_a["manifest"]["files"]:
        assert generate.sha256_file(first / entry["path"]) == generate.sha256_file(second / entry["path"])


def test_cli_rejects_output_dir_name_mismatch(tiny_config_path, tmp_path, capsys):
    wrong = tmp_path / "not-sim-smoke"
    exit_code = generate.main(["--config", str(tiny_config_path), "--variant", "smoke", "--out", str(wrong)])
    assert exit_code == 2
    assert not (wrong / "manifest.json").is_file()
    assert "CONFIG ERROR" in capsys.readouterr().err


def test_cli_end_to_end(tiny_config_path, tmp_path):
    out_dir = tmp_path / "cli" / "sim-smoke"
    assert generate.main(["--config", str(tiny_config_path), "--variant", "smoke", "--out", str(out_dir)]) == 0
    manifest = read_manifest(out_dir)
    assert manifest["summary"]["total_frames"] == 96
