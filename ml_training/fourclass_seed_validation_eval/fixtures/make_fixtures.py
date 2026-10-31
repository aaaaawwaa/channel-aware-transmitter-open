#!/usr/bin/env python3
"""Build a tiny SYNTHETIC_FIXTURE dataset for the new-seed evaluator interface.

NOT real RF data.  Two valid frames (one BPSK ON, one OFF) and one deliberately
invalid frame validate the entry's identity/truth/TX-load/invalid handling.  No
large simulated dataset is created.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent
OUT = BASE / "fixture_run"
RAW = OUT / "raw"
RELEASE = "4776ba0bf6cabac383934f0437358595"
AMP60 = ROOT / "ml_training" / "fourclass_test_vectors" / "hardware_amp60"
BPSK_REF = AMP60 / "outputs" / "reference_4096" / "BPSK.iq"
BPSK_PAYLOAD_SHA = "38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jwrite(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def base_metadata(label, capture_id, iq_bytes):
    digest = hashlib.sha256(iq_bytes).hexdigest()
    observed = {"format": "s16_iq_le", "samples": 4096,
                "sample_rate_hz": 60000000, "decimation": 1,
                "symbol_rate_hz": 3000000, "shaping": "RECT"}
    return {
        "SYNTHETIC_FIXTURE": True,
        "fixture_note": "not real RF; interface validation only",
        "capture_id": capture_id,
        "request_id": 1,
        "release_id": RELEASE,
        "connection_generation": 8,
        "config_revision": 2,
        "iq_sha256": digest,
        "input_domain_status": "VALID",
        "input_domain_observed": observed,
        "format": "s16_iq_le",
        "samples": 4096,
        "sample_rate_hz": 60000000,
        "decimation": 1,
        "demod_result": {"submitted": {"config": {
            "mode": "BPSK", "symbol_rate_hz": 3000000, "shaping": "RECT"}}},
        "modrec4_result": {
            "status": "OK",
            "input_domain": {"status": "VALID", "observed": observed},
            "frame_identity": {"capture_id": capture_id, "request_id": 1,
                               "release_id": RELEASE, "iq_sha256": digest},
            "modulation": {"final_label": "UNKNOWN", "accepted": False,
                           "reason": "fixture_board_result_not_truth"},
        },
    }


def load_evidence(mode, seed, payload_sha, payload_length):
    """Mirror the production evidence shape.

    tx_load_metadata is the daemon protocol metadata from build_tx_waveform
    (samples/bytes/format/direction/wrap).  The waveform source parameters live in
    worker_args, not in tx_load_metadata.
    """
    return {
        "load_ok": True,
        "worker_args": {
            "mode": mode, "seed": seed, "amplitude": 60, "filter": "RECT",
            "rolloff": "0.35", "sample_rate_hz": 60000000,
            "symbol_rate_hz": 3000000, "waveform": "digital",
        },
        "tx_load_metadata": {
            "samples": 2540, "bytes": 10160, "format": "s16_iq_le",
            "direction": "tx", "wrap": True,
        },
        "payload_sha256": payload_sha,
        "payload_length": payload_length,
    }


def write_frame(label, iq_bytes, mode, seed, payload_sha, payload_length,
                off=False, invalid=False):
    frame_dir = RAW / label
    frame_dir.mkdir(parents=True, exist_ok=True)
    iq_path = frame_dir / (label + ".iq")
    iq_path.write_bytes(iq_bytes)
    meta = base_metadata(label, "fixture-" + label, iq_bytes)
    meta["SYNTHETIC_FIXTURE"] = True
    meta["fixture_invalid"] = bool(invalid)
    meta["off_tx_ok"] = bool(off)
    meta["pre_tx_source_selected"] = False
    meta["post_tx_source_selected"] = False
    ev = load_evidence(mode, seed, payload_sha, payload_length)
    meta["tx_load_evidence"] = ev
    if off:
        meta["modrec4_result"]["modulation"].update(
            {"final_label": "UNKNOWN", "accepted": False, "reason": "fixture_off"})
    record = {
        "iq_file": iq_path.name,
        "metadata_file": label + ".json",
        "iq_sha256": meta["iq_sha256"],
        "release_id": RELEASE,
        "capture_id": meta["capture_id"],
        "request_id": 1,
        "off_tx_ok": bool(off),
        "pre_tx_source_selected": False,
        "post_tx_source_selected": False,
        "tx_load_evidence": ev,
        "modrec4": meta["modrec4_result"],
    }
    jwrite(frame_dir / (label + ".json"), meta)
    jwrite(frame_dir / "records.json", [record])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not BPSK_REF.is_file():
        raise SystemExit("fixture source missing: " + str(BPSK_REF))
    # ON BPSK: existing frozen ideal BPSK reference used as a tiny fixture IQ.
    bpsk = BPSK_REF.read_bytes()
    write_frame("02-bpsk-2", bpsk, "BPSK", 1, BPSK_PAYLOAD_SHA, 10160)
    # OFF: 4096 zero pairs (valid domain; gate no_energy is a model rejection).
    off = b"\x00\x00\x00\x00" * 4096
    write_frame("01-off-first", off, "BPSK", 1, BPSK_PAYLOAD_SHA, 10160, off=True)
    # Invalid IQ length; metadata/load/identity are otherwise plausible.
    bad = b"\x00\x00" * 50
    write_frame("03-invalid-digest", bad, "BPSK", 2,
                "0" * 64, 10160, invalid=True)
    observed = {"format": "s16_iq_le", "samples": 4096,
                "sample_rate_hz": 60000000, "decimation": 1,
                "symbol_rate_hz": 3000000, "shaping": "RECT"}
    expected = {"mode": "BPSK", "seed": 1, "amplitude": 60, "filter": "RECT",
                "rolloff": "0.35", "sample_rate_hz": 60000000,
                "symbol_rate_hz": 3000000, "waveform": "digital"}
    plan = {
        "schema": "SYNTHETIC_FIXTURE_plan",
        "dataset_id": "synthetic_fixture",
        "release": RELEASE,
        "order": [
            {"frame_index": 1, "label": "01-off-first", "physical": False,
             "truth_class": "OFF", "expected_load": expected},
            {"frame_index": 2, "label": "02-bpsk-2", "physical": True,
             "truth_class": "BPSK", "expected_load": expected},
            {"frame_index": 3, "label": "03-invalid-digest", "physical": True,
             "truth_class": "BPSK", "expected_load": {**expected, "seed": 2}},
        ],
    }
    truth = {
        "schema": "SYNTHETIC_FIXTURE_truth",
        "dataset_id": "synthetic_fixture",
        "training_use": False,
        "real_capture": False,
        "rows": [
            {"frame_index": 2, "mode": "BPSK", "seed": 1, "amplitude": 60,
             "filter": "RECT", "rolloff": "0.35", "symbol_rate_hz": 3000000,
             "sample_rate_hz": 60000000, "tx_samples": 2540, "tx_bytes": 10160,
             "tx_payload_sha256": BPSK_PAYLOAD_SHA, "truth_class": "BPSK",
             "truth_source": "SYNTHETIC_FIXTURE independent truth; model is not truth"},
            {"frame_index": 3, "mode": "BPSK", "seed": 2, "amplitude": 60,
             "filter": "RECT", "rolloff": "0.35", "symbol_rate_hz": 3000000,
             "sample_rate_hz": 60000000, "tx_samples": 2540, "tx_bytes": 10160,
             "tx_payload_sha256": "0" * 64, "truth_class": "BPSK",
             "truth_source": "SYNTHETIC_FIXTURE invalid-frame truth"},
        ],
    }
    jwrite(OUT / "fixture_plan.json", plan)
    jwrite(OUT / "fixture_truth.json", truth)
    jwrite(OUT / "README.json", {
        "SYNTHETIC_FIXTURE": True,
        "not_real_rf": True,
        "purpose": "small interface validation only",
        "valid_frames": 2, "invalid_frames": 1,
    })
    print(json.dumps({"fixture_root": str(OUT), "raw": str(RAW)}, indent=1))


if __name__ == "__main__":
    main()
