"""Data integrity and leakage controls for RX training manifests."""
import copy
import hashlib
import importlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def api():
    class Api:
        def __getattr__(self, name):
            try:
                return getattr(importlib.import_module("scripts.rf_ml.dataset"), name)
            except ModuleNotFoundError:
                pytest.fail("Task6 manifest validator has not been implemented")
    return Api()


def frame(root, number=1, session="s1", split="train", label="TONE"):
    iq = struct.pack("<8192h", *([number, -number] * 4096))
    name = "frame-%d.iq" % number
    (root / name).write_bytes(iq)
    return {
        "capture_id": "c%d" % number, "session_id": session, "split": split,
        "origin": "physical", "label": label, "other_kind": None,
        "iq_path": name, "iq_sha256": hashlib.sha256(iq).hexdigest(),
        "format": "s16_iq_le", "samples": 4096, "release_id": "a" * 32,
        "configuration": {"sample_rate_hz": 60000000, "ratio": 0,
                          "lo_hz": 80000000, "bandwidth_hz": 30000000},
        "tx_reference": {"seed_family": "seed%d" % number,
                         "symbol_rate_hz": 2000000, "shaping": "RECT"},
        "agc": {"mode": "slow_attack", "gain_db": None},
        "captured_at_utc": "2026-10-02T01:00:01Z", "completed_monotonic_s": 42.0,
        "complete": True, "clipping_count": 0, "exclusion_reasons": [],
        "augmentation_parent_id": None,
    }


def manifest(*frames):
    sessions = {}
    for item in frames:
        sessions.setdefault(item["session_id"], {
            "session_id": item["session_id"], "split": item["split"],
            "origin": item["origin"], "split_frozen_at_utc": "2026-10-02T01:00:00Z",
        })
    return {"schema_version": 1, "sessions": list(sessions.values()), "frames": list(frames)}


def test_pilot_validates_integrity_but_cannot_claim_production_coverage(api, tmp_path):
    data = manifest(frame(tmp_path))
    result = api.validate_manifest(data, tmp_path)
    assert result.valid
    assert result.coverage_missing
    assert not result.production_ready
    assert not api.validate_manifest(data, tmp_path, production=True).valid


def test_empty_template_is_inspectable_not_production_ready(api, tmp_path):
    data = api.empty_manifest()
    result = api.validate_manifest(data, tmp_path)
    assert result.valid and not result.production_ready
    assert len(result.coverage_missing) == 90
    schema = json.loads((ROOT / "scripts/rf_ml/schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == data["schema_version"]
    frame_schema = schema["properties"]["frames"]["items"]
    tx_schema = frame_schema["properties"]["tx_reference"]["anyOf"][0]
    assert tx_schema["properties"]["symbol_rate_hz"]["minimum"] == 1
    non_off_tx = frame_schema["allOf"][1]["else"]["properties"]["tx_reference"]
    assert non_off_tx["properties"]["symbol_rate_hz"]["minimum"] == 1


@pytest.mark.parametrize("change,code", [
    (lambda f: f.pop("configuration"), "configuration"),
    (lambda f: f["configuration"].update(ratio=5), "ratio"),
    (lambda f: f.update(iq_sha256="0" * 64), "hash"),
    (lambda f: f.update(iq_path="../escape.iq"), "path"),
    (lambda f: f.update(samples=4095), "bytes"),
    (lambda f: f.update(complete=False), "exclusion"),
    (lambda f: f.update(clipping_count=1), "clipping"),
    (lambda f: f.update(captured_at_utc="2026-10-02T00:00:00Z"), "frozen"),
    (lambda f: f.update(completed_monotonic_s=float("nan")), "monotonic"),
    (lambda f: f.update(label="UNKNOWN"), "label"),
    (lambda f: f.update(origin="unknown"), "origin"),
    (lambda f: f["agc"].pop("mode"), "agc"),
    (lambda f: f["tx_reference"].pop("seed_family"), "seed"),
    (lambda f: f.update(release_id=""), "release"),
    (lambda f: f.update(token="<SET_LOCALLY>"), "unknown"),
])
def test_invalid_frame_rejected(api, tmp_path, change, code):
    item = frame(tmp_path)
    data = manifest(item)
    change(item)
    result = api.validate_manifest(data, tmp_path)
    assert not result.valid
    assert any(code in error.lower() for error in result.errors), result.errors


def test_same_session_cannot_cross_split(api, tmp_path):
    a, b = frame(tmp_path), frame(tmp_path, 2, split="test")
    result = api.validate_manifest(manifest(a, b), tmp_path)
    assert not result.valid
    assert any("session" in x and "split" in x for x in result.errors)


def test_seed_family_cannot_cross_split(api, tmp_path):
    a, b = frame(tmp_path), frame(tmp_path, 2, session="s2", split="test")
    b["tx_reference"]["seed_family"] = a["tx_reference"]["seed_family"]
    assert not api.validate_manifest(manifest(a, b), tmp_path).valid


def test_augmentation_cannot_change_session_or_split(api, tmp_path):
    a, b = frame(tmp_path), frame(tmp_path, 2, session="s2", split="test")
    b["augmentation_parent_id"] = a["capture_id"]
    assert not api.validate_manifest(manifest(a, b), tmp_path).valid


def test_duplicate_capture_and_duplicate_iq_rejected(api, tmp_path):
    a, b = frame(tmp_path), frame(tmp_path, 2, session="s2")
    b["iq_path"], b["iq_sha256"] = a["iq_path"], a["iq_sha256"]
    result = api.validate_manifest(manifest(a, b), tmp_path)
    assert any("duplicate IQ" in x for x in result.errors)
    b["capture_id"] = a["capture_id"]
    assert any("duplicate capture" in x for x in api.validate_manifest(manifest(a, b), tmp_path).errors)


def test_signed_12bit_payload_range_is_verified_even_with_matching_hash(api, tmp_path):
    item = frame(tmp_path)
    payload = struct.pack("<8192h", *([2048, 0] * 4096))
    (tmp_path / item["iq_path"]).write_bytes(payload)
    item["iq_sha256"] = hashlib.sha256(payload).hexdigest()
    assert any("12-bit" in x for x in api.validate_manifest(manifest(item), tmp_path).errors)


def test_missing_and_symlink_outside_dataset_are_refused(api, tmp_path):
    item = frame(tmp_path)
    (tmp_path / item["iq_path"]).unlink()
    assert not api.validate_manifest(manifest(item), tmp_path).valid


def test_excluded_diagnostic_retained_off_excluded_from_training(api, tmp_path):
    a, off, clipped = frame(tmp_path), frame(tmp_path, 2, label="OFF"), frame(tmp_path, 3)
    off["tx_reference"] = None
    clipped.update(clipping_count=1, exclusion_reasons=["clipping"])
    payload = struct.pack("<h", 2047) + (tmp_path / clipped["iq_path"]).read_bytes()[2:]
    (tmp_path / clipped["iq_path"]).write_bytes(payload)
    clipped["iq_sha256"] = hashlib.sha256(payload).hexdigest()
    data = manifest(a, off, clipped)
    assert api.validate_manifest(data, tmp_path).valid
    assert [f["capture_id"] for f in api.training_frames(data, "train")] == ["c1"]
    assert len(data["frames"]) == 3


def test_coverage_requires_ten_frames_per_each_of_three_sessions(api, tmp_path):
    frames = [frame(tmp_path, n, session="s%d" % (n // 10), label="BPSK") for n in range(1, 31)]
    result = api.validate_manifest(manifest(*frames), tmp_path)
    row = next(x for x in result.coverage_missing if x["label"] == "BPSK" and x["split"] == "train"
               and x["symbol_rate_hz"] == 2000000 and x["shaping"] == "RECT")
    assert row["qualified_sessions"] == 2  # 9/10/10/1 frames cannot pass as 3x10.


def test_synthetic_does_not_count_as_physical_production_coverage(api, tmp_path):
    frames = [frame(tmp_path, n, session="s%d" % ((n-1)//10), label="TONE") for n in range(1, 31)]
    for f in frames:
        f["origin"] = "synthetic"
    result = api.validate_manifest(manifest(*frames), tmp_path)
    row = next(x for x in result.coverage_missing if x["label"] == "TONE" and x["split"] == "train")
    assert row["qualified_sessions"] == 0
    synthetic = api.validate_manifest(manifest(*frames), tmp_path, coverage_origin="synthetic")
    assert not any(x["label"] == "TONE" and x["split"] == "train" for x in synthetic.coverage_missing)


def test_cli_empty_manifest_production_exits_nonzero(api, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(api.empty_manifest()))
    r = subprocess.run([sys.executable, "-m", "scripts.rf_ml.dataset", "validate", str(path), "--production"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 1
    assert len(json.loads(r.stdout)["coverage_missing"]) == 90


@pytest.mark.parametrize("data", [None, [], {}, {"schema_version": True, "sessions": {}, "frames": {}},
                                  {"schema_version": 1, "sessions": [None], "frames": [None]}])
def test_malformed_manifest_refused_without_crashing(api, tmp_path, data):
    assert not api.validate_manifest(data, tmp_path).valid


def test_full_matrix_proves_production_mode_has_a_success_path(api, tmp_path):
    records = []
    base = frame(tmp_path)
    cells = [("TONE", None, None, None), ("OFF", None, None, None)]
    cells += [("OTHER", k, None, None) for k in ("NOISE", "CHIRP", "SQUARE", "MIXED")]
    cells += [(label, None, rate, shape) for label in ("BPSK", "QPSK", "QAM16", "QAM64")
              for rate in (2000000, 3000000, 5000000) for shape in ("RECT", "RRC0.35")]
    for split in ("train", "val", "test"):
        for ci, (label, kind, rate, shape) in enumerate(cells):
            for session_no in range(3):
                session = "%s-cell%d-session%d" % (split, ci, session_no)
                for _ in range(10):
                    number = len(records) + 1
                    item = copy.deepcopy(base)
                    item.update(session_id=session, split=split, label=label)
                    name = "coverage-%d.iq" % number
                    payload = struct.pack("<4h", number % 1024, number // 1024, 5, -5) + bytes(16376)
                    (tmp_path / name).write_bytes(payload)
                    item.update(capture_id="c%d" % number, iq_path=name,
                                iq_sha256=hashlib.sha256(payload).hexdigest(), other_kind=kind)
                    item["tx_reference"] = None if label == "OFF" else {
                        "seed_family": session, "symbol_rate_hz": rate, "shaping": shape}
                    records.append(item)
    data = manifest(*records)
    result = api.validate_manifest(data, tmp_path, production=True)
    assert result.valid and result.production_ready and not result.coverage_missing, result.errors
    assert result.valid_frames == 2700
    # Synthetic matrix coverage is inspectable but cannot release production.
    for item in records:
        item["origin"] = "synthetic"
    synthetic_data = manifest(*records)
    result = api.validate_manifest(synthetic_data, tmp_path, coverage_origin="synthetic")
    assert result.valid and not result.coverage_missing and not result.production_ready
    result = api.validate_manifest(synthetic_data, tmp_path, production=True, coverage_origin="synthetic")
    assert not result.valid and not result.production_ready
    for item in records:
        item["origin"] = "physical"
    # Losing one session's tenth frame must expose the precise missing cell.
    data["frames"].pop()
    result = api.validate_manifest(data, tmp_path, production=True)
    assert not result.valid and len(result.coverage_missing) == 1
    assert result.coverage_missing[0]["qualified_sessions"] == 2


@pytest.mark.parametrize("value", [[], True, -1, "2000000"])
def test_tone_tx_reference_metadata_is_still_typed(api, tmp_path, value):
    item = frame(tmp_path)
    item["tx_reference"]["symbol_rate_hz"] = value
    assert not api.validate_manifest(manifest(item), tmp_path).valid


def test_extreme_number_is_refused_without_overflow(api, tmp_path):
    item = frame(tmp_path)
    item["completed_monotonic_s"] = 10 ** 400
    assert not api.validate_manifest(manifest(item), tmp_path).valid


@pytest.mark.parametrize("label", ["TONE", "OTHER", "BPSK"])
@pytest.mark.parametrize("field,value", [
    ("symbol_rate_hz", 2000000.0),
    ("symbol_rate_hz", float("inf")),
    ("symbol_rate_hz", 10 ** 400),
    ("shaping", []),
    ("shaping", True),
    ("shaping", " "),
    ("shaping", "x" * 257),
])
def test_tx_reference_metadata_types_apply_to_all_labels(api, tmp_path, label, field, value):
    item = frame(tmp_path, label=label)
    if label == "OTHER":
        item["other_kind"] = "NOISE"
    item["tx_reference"][field] = value
    assert not api.validate_manifest(manifest(item), tmp_path).valid


@pytest.mark.parametrize("value", [10 ** 400, float("nan"), float("inf"), True])
def test_invalid_agc_number_is_refused_without_crashing(api, tmp_path, value):
    item = frame(tmp_path)
    item["agc"]["gain_db"] = value
    assert not api.validate_manifest(manifest(item), tmp_path).valid


def test_training_selection_refuses_held_out_test_split(api, tmp_path):
    item = frame(tmp_path, split="test")
    data = manifest(item)
    assert api.validate_manifest(data, tmp_path).valid
    with pytest.raises(ValueError, match="train or val"):
        api.training_frames(data, "test")


def test_usable_augmentation_cannot_revive_excluded_parent(api, tmp_path):
    parent, child = frame(tmp_path), frame(tmp_path, 2)
    parent.update(complete=False, exclusion_reasons=["incomplete"])
    child["augmentation_parent_id"] = parent["capture_id"]
    result = api.validate_manifest(manifest(parent, child), tmp_path)
    assert not result.valid
    assert any("augmentation" in error and "excluded" in error for error in result.errors)


def test_malformed_augmentation_capture_id_is_refused_without_crashing(api, tmp_path):
    parent, child = frame(tmp_path), frame(tmp_path, 2)
    child.update(capture_id=[], augmentation_parent_id=parent["capture_id"])
    assert not api.validate_manifest(manifest(parent, child), tmp_path).valid


def test_excluded_augmentation_diagnostic_can_retain_excluded_parent(api, tmp_path):
    parent, child = frame(tmp_path), frame(tmp_path, 2)
    for item in (parent, child):
        item.update(complete=False, exclusion_reasons=["incomplete"])
    child["augmentation_parent_id"] = parent["capture_id"]
    data = manifest(parent, child)
    assert api.validate_manifest(data, tmp_path).valid
    assert not api.training_frames(data, "train")
