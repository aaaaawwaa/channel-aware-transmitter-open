"""Declaration-time controls for the synthetic RX corpus generator.

Every test here exists to catch one specific wrong thing the generator could do:
invent a label from the received signal, publish a short frame, pad a short frame with
zeros, silently clip, hand out one session of augmentations as three independent
sessions, reuse a PRBS seed family across splits, or overwrite an existing corpus.
"""
import hashlib
import inspect
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CORPUS_CELL = "BPSK-2M-RECT"


@pytest.fixture
def synth():
    import importlib

    try:
        return importlib.import_module("scripts.rf_ml.synthetic")
    except ModuleNotFoundError:
        pytest.fail("Task7 synthetic generator has not been implemented")


@pytest.fixture
def dataset():
    import importlib

    try:
        return importlib.import_module("scripts.rf_ml.dataset")
    except ModuleNotFoundError:
        pytest.fail("Task6 manifest validator has not been implemented")


def codes(payload):
    return np.frombuffer(payload, dtype="<i2").reshape(-1, 2)


def plain_transform(**overrides):
    """One explicit channel realisation, with every knob named."""
    transform = {"phase_rad": 0.0, "shift_samples": 0, "iq_swap": False,
                 "conjugate": False, "freq_offset_hz": 0.0,
                 "noise_sigma_codes": 0.0, "channel_taps": None, "noise_seed": 0}
    transform.update(overrides)
    return transform


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """One small but complete corpus: 3 sessions x 10 frames of a single cell."""
    import importlib

    synthetic = importlib.import_module("scripts.rf_ml.synthetic")
    out = tmp_path_factory.mktemp("synthetic-corpus") / "data-synthetic"
    summary = synthetic.generate(out, splits=("train",),
                                 cells=(synthetic.cell_by_key(CORPUS_CELL),),
                                 sessions=3, frames=10, augment_per_session=2)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    return {"out": out, "manifest": manifest, "summary": summary}


def test_complete_frames_are_exactly_4096_samples_within_signed_12_bit(synth):
    for key in ("TONE", "BPSK-2M-RECT", "QAM64-5M-RRC0.35", "OTHER-CHIRP", "OFF"):
        cell = synth.cell_by_key(key)
        payload = synth.synthesize(cell, seed=7, transform=plain_transform(), amplitude=150)
        assert len(payload) == 4096 * 4
        assert len(payload) == synth.SAMPLES * 4
        values = codes(payload)
        assert values.shape == (synth.SAMPLES, 2)
        assert values.min() >= -2048 and values.max() <= 2047


def test_signal_classes_reach_the_declared_peak_and_never_rail(synth):
    for key in ("TONE", "BPSK-2M-RECT", "QAM16-3M-RRC0.35", "OTHER-NOISE", "OTHER-SQUARE", "OTHER-MIXED"):
        cell = synth.cell_by_key(key)
        values = codes(synth.synthesize(cell, seed=11,
                                        transform=plain_transform(noise_sigma_codes=2.0),
                                        amplitude=150))
        assert np.abs(values).max() == 150
        assert not np.any((values == 2047) | (values == -2048))


def test_off_is_a_low_energy_background_and_is_never_normalised_up(synth):
    off = codes(synth.synthesize(synth.cell_by_key("OFF"), seed=3,
                                 transform=plain_transform(), amplitude=150))
    tone = codes(synth.synthesize(synth.cell_by_key("TONE"), seed=3,
                                  transform=plain_transform(), amplitude=150))
    assert np.abs(off).max() <= max(synth.OFF_NOISE_PEAK)
    assert np.abs(off).max() < np.abs(tone).max() / 4
    # a few LSBs of floor, not a normalised signal
    assert np.sqrt(np.mean(off.astype(float) ** 2)) <= 3.0


@pytest.mark.parametrize("key,label,rate,shape,kind", [
    ("TONE", "TONE", None, None, None),
    ("BPSK-5M-RRC0.35", "BPSK", 5000000, "RRC0.35", None),
    ("QAM16-3M-RECT", "QAM16", 3000000, "RECT", None),
    ("OTHER-SQUARE", "OTHER", None, None, "SQUARE"),
    ("OFF", "OFF", None, None, None),
])
def test_label_is_derived_from_the_generation_configuration(synth, key, label, rate, shape, kind):
    cell = synth.cell_by_key(key)
    assert synth.label_for_cell(cell) == label
    assert cell.label == label
    assert cell.symbol_rate_hz == rate and cell.shaping == shape and cell.other_kind == kind


def test_generator_cannot_be_told_a_label_it_did_not_generate(synth):
    cell = synth.cell_by_key("QPSK-2M-RECT")
    names = set(inspect.signature(synth.synthesize).parameters)
    assert not any("label" in name or "split" in name or "session" in name for name in names)
    with pytest.raises(TypeError):
        synth.synthesize(cell, seed=1, transform=plain_transform(), amplitude=150,
                         label="QAM64")
    with pytest.raises(ValueError):
        synth.synthesize(cell, seed=1, transform=plain_transform(label="QAM64"),
                         amplitude=150)


def test_channel_transform_takes_only_numeric_channel_parameters(synth):
    names = set(inspect.signature(synth.channel_transform).parameters)
    assert names == {"iq", "params"}
    values = codes(synth.synthesize(synth.cell_by_key("QPSK-2M-RECT"), seed=5,
                                    transform=plain_transform(), amplitude=150)).astype(float)
    with pytest.raises(ValueError):
        synth.channel_transform(values, {"label": "BPSK"})


def test_channel_transforms_change_the_waveform_without_changing_the_label(synth):
    cell = synth.cell_by_key("QAM64-3M-RRC0.35")
    variants = [
        plain_transform(phase_rad=1.1, shift_samples=13, freq_offset_hz=15000.0,
                        noise_sigma_codes=2.0, noise_seed=1),
        plain_transform(phase_rad=-2.0, shift_samples=63, iq_swap=True, conjugate=True,
                        freq_offset_hz=-20000.0, noise_sigma_codes=3.0, noise_seed=2,
                        channel_taps=[{"gain_q8": 230, "delay_samples": 0},
                                      {"gain_q8": 64, "delay_samples": 3},
                                      {"gain_q8": 24, "delay_samples": 11},
                                      {"gain_q8": 0, "delay_samples": 0}]),
    ]
    payloads = [synth.synthesize(cell, seed=5, transform=transform, amplitude=150)
                for transform in variants]
    assert payloads[0] != payloads[1]
    assert synth.label_for_cell(cell) == "QAM64"


def test_a_short_source_is_refused_instead_of_zero_padded(synth):
    with pytest.raises(ValueError):
        synth.receive_window(np.zeros((0, 2)), 0)
    window = synth.receive_window(np.arange(8, dtype=float).reshape(4, 2), 2)
    assert window.shape == (synth.SAMPLES, 2)
    assert tuple(window[0]) == (4.0, 5.0) and tuple(window[2]) == (0.0, 1.0)


def test_published_bytes_are_exactly_the_contract_pack_iq_of_their_codes(synth):
    import rf_iq_signal

    payload = synth.synthesize(synth.cell_by_key("QAM64-2M-RECT"), seed=9,
                               transform=plain_transform(), amplitude=150)
    values = codes(payload).astype(float)
    assert payload == rf_iq_signal.pack_iq(values)


def test_out_of_range_amplitude_is_refused_rather_than_clipped(synth):
    cell = synth.cell_by_key("BPSK-2M-RECT")
    for amplitude in (0, -1, 2048, 4096, True, 1.5):
        with pytest.raises(ValueError):
            synth.synthesize(cell, seed=1, transform=plain_transform(), amplitude=amplitude)


def test_seed_families_and_prbs_seeds_never_cross_splits(synth):
    families = {split: {synth.seed_family(split, index) for index in range(4)}
                for split in synth.SPLITS}
    assert all(families[a].isdisjoint(families[b])
               for a in synth.SPLITS for b in synth.SPLITS if a < b)
    pools = {split: set(synth.SPLIT_SEED_POOLS[split]) for split in synth.SPLITS}
    assert all(pools[a].isdisjoint(pools[b])
               for a in synth.SPLITS for b in synth.SPLITS if a < b)
    for split in synth.SPLITS:
        assert all(1 <= synth.prbs_seed_for(split, index) <= 127 for index in range(7))


def test_generated_corpus_covers_its_cell_as_synthetic_origin(synth, dataset, corpus):
    result = dataset.validate_manifest(corpus["manifest"], corpus["out"],
                                       coverage_origin="synthetic")
    assert result.valid, result.errors
    assert not any(row["label"] == "BPSK" and row["split"] == "train"
                   and row["symbol_rate_hz"] == 2000000 and row["shaping"] == "RECT"
                   for row in result.coverage_missing)
    assert len(result.coverage_missing) == 89
    assert result.valid_frames == 36  # 30 independent frames + 6 same-session augmentations
    assert result.excluded_frames == 0


def test_coverage_cell_error_reappears_when_one_frame_is_lost(synth, dataset, corpus):
    thinned = json.loads(json.dumps(corpus["manifest"]))
    thinned["frames"] = [f for f in thinned["frames"] if f["augmentation_parent_id"] is None]
    thinned["frames"].pop()
    result = dataset.validate_manifest(thinned, corpus["out"], coverage_origin="synthetic")
    row = next(x for x in result.coverage_missing if x["label"] == "BPSK"
               and x["split"] == "train" and x["symbol_rate_hz"] == 2000000
               and x["shaping"] == "RECT")
    assert row["qualified_sessions"] == 2
    assert not dataset.validate_manifest(thinned, corpus["out"],
                                         production=True, coverage_origin="synthetic").valid


def test_augmentation_is_recorded_but_never_counts_as_independent_coverage(synth, dataset, corpus):
    originals = [f for f in corpus["manifest"]["frames"] if f["augmentation_parent_id"] is None]
    augmented = [f for f in corpus["manifest"]["frames"] if f["augmentation_parent_id"]]
    assert len(originals) == 30 and len(augmented) == 6
    by_id = {f["capture_id"]: f for f in originals}
    for frame in augmented:
        parent = by_id[frame["augmentation_parent_id"]]
        assert frame["session_id"] == parent["session_id"]
        assert frame["label"] == parent["label"] and frame["other_kind"] == parent["other_kind"]
        assert frame["iq_sha256"] != parent["iq_sha256"]
    stripped = dict(corpus["manifest"], frames=originals)
    live = dataset.validate_manifest(corpus["manifest"], corpus["out"],
                                     coverage_origin="synthetic")
    bare = dataset.validate_manifest(stripped, corpus["out"], coverage_origin="synthetic")
    assert live.coverage_missing == bare.coverage_missing
    assert bare.valid_frames == 30 and live.valid_frames == 36


def test_manifest_schema_keys_and_companion_json_record_every_seed(synth, corpus):
    assert set(corpus["manifest"]) == {"schema_version", "sessions", "frames"}
    generator = json.loads((corpus["out"] / "generator.json").read_text(encoding="utf-8"))
    assert generator["split_frozen_at_utc"] == corpus["manifest"]["sessions"][0]["split_frozen_at_utc"]
    assert generator["cells"] == [CORPUS_CELL]
    sessions = generator["sessions"]
    assert len(sessions) == 3
    for session in sessions:
        assert session["split"] == "train"
        assert session["seed_family"] in synth.SPLIT_FAMILIES["train"]
        assert len(session["prbs_seeds"]) == 10
        assert all(1 <= seed <= 127 for seed in session["prbs_seeds"])
        assert session["amplitude"] in synth.AMPLITUDE_CONDITIONS
        assert len(session["frames"]) == 10
        assert all("transform" in frame and "sha256" in frame for frame in session["frames"])
    summary = json.loads((corpus["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_frames"] == 36
    assert summary["duplicate_hashes"] == []


def test_every_published_frame_hash_is_unique(synth, corpus):
    digests = [f["iq_sha256"] for f in corpus["manifest"]["frames"]]
    assert len(set(digests)) == len(digests)
    assert synth.scan_duplicate_hashes(corpus["out"]) == []


def test_cli_refuses_to_overwrite_an_existing_output(synth, tmp_path):
    out = tmp_path / "data-synthetic"
    out.mkdir()
    marker = out / "keep-me.txt"
    marker.write_text("existing", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.rf_ml.synthetic", "--output", str(out),
         "--splits", "train", "--cells", CORPUS_CELL, "--sessions", "1", "--frames", "1",
         "--augment-per-session", "0"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode != 0
    assert "exist" in (result.stdout + result.stderr).lower()
    assert marker.read_text(encoding="utf-8") == "existing"
    assert not (out / "manifest.json").exists()


def test_cli_refuses_an_unknown_cell(synth, tmp_path):
    out = tmp_path / "corpus"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.rf_ml.synthetic", "--output", str(out),
         "--cells", "BPSK-4M-RECT"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode != 0
    assert not out.exists()


def test_cell_matrix_is_exactly_the_dataset_coverage_matrix(synth):
    expected = {"TONE", "OFF", "OTHER-NOISE", "OTHER-CHIRP", "OTHER-SQUARE", "OTHER-MIXED"}
    expected |= {"%s-%dM-%s" % (mode, rate // 1000000, shape)
                 for mode in synth.DIGITAL_MODES for rate in synth.RATES
                 for shape in synth.SHAPINGS}
    keys = {cell.key for cell in synth.CELLS}
    assert keys == expected
    assert len(synth.CELLS) == 30  # 30 cells x 3 splits = the validator's 90 cells


def test_augmentations_are_emitted_only_for_training_split(synth, dataset, tmp_path):
    output = tmp_path / "three-splits"
    summary = synth.generate(output, cells=(synth.cell_by_key(CORPUS_CELL),),
                             sessions=1, frames=2, augment_per_session=2)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    originals = [f for f in manifest["frames"] if f["augmentation_parent_id"] is None]
    augmented = [f for f in manifest["frames"] if f["augmentation_parent_id"] is not None]
    assert len(originals) == 6
    assert len(augmented) == 2
    assert {f["split"] for f in augmented} == {"train"}
    assert summary["total_frames"] == 8
    assert dataset.validate_manifest(manifest, output, coverage_origin="synthetic").valid


def test_delivery_modes_exclude_qam64_but_historical_ml_path_can_generate(synth):
    """QAM64 is historical-only: the product default rejects it."""
    import rf_iq_signal

    assert synth.DELIVERY_MODES == ("BPSK", "QPSK", "QAM16")
    assert synth.HISTORICAL_MODES == ("QAM64",)
    assert synth.DIGITAL_MODES == synth.DELIVERY_MODES + synth.HISTORICAL_MODES
    assert rf_iq_signal.MODES == synth.DELIVERY_MODES

    args = {"mode": "QAM64", "filter": "RECT", "rolloff": "0.35", "seed": 11,
            "symbol_rate_hz": 2000000, "sample_rate_hz": 60000000,
            "amplitude": 150}
    with pytest.raises(ValueError, match="mode must be one of"):
        rf_iq_signal.generate_digital_frame(args)
    metadata, payload = rf_iq_signal.generate_digital_frame(
        args, allow_historical_qam64=True)
    assert metadata["samples"] == 127 * 30
    assert len(payload) == metadata["bytes"]
