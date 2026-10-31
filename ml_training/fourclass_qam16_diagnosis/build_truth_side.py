#!/usr/bin/env python3
"""Build the independent TX truth side file for the 14 real four-class frames.

Truth comes only from the runner's recorded TX mode/seed/amplitude plus the
hardware_amp60 payload provenance and the frame metadata identity/IQ SHA.  It does
not use modrec4, RX expected_mode, six-class or environment labels.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evidence" / "rf-teaching" / "s2-46db-fourclass-real-rx-2026-10-22"
RUN = DATA / "run"
OUT = Path(__file__).resolve().parent / "results"
AMP60 = ROOT / "ml_training" / "fourclass_test_vectors" / "hardware_amp60"
TRUTH_SCHEMA = "ml-task68-tx-truth-v1"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build():
    out = OUT
    out.mkdir(parents=True, exist_ok=True)
    campaign = load_json(RUN / "fourclass.json")
    amp_manifest = load_json(AMP60 / "outputs" / "manifest.json")
    amp_vectors = {v["truth_class"]: v for v in amp_manifest["vectors"]}
    records = campaign["records"]
    frames = []
    evidence = []
    for index, record in enumerate(records):
        iq_file = record["iq_file"]
        iq_path = RUN / iq_file
        meta_path = RUN / (Path(iq_file).stem + ".json")
        meta = load_json(meta_path)
        digest = sha256_file(iq_path)
        if digest != record["iq_sha256"] or digest != meta.get("iq_sha256"):
            raise SystemExit("IQ digest mismatch: %s" % iq_file)
        mode = record.get("mode")
        if mode not in ("BPSK", "QPSK", "QAM16", "QAM64", "OFF"):
            raise SystemExit("unsupported runner mode: %r" % mode)
        seed = record.get("tx_seed")
        if mode != "OFF" and type(seed) is not int:
            raise SystemExit("clean frame missing TX seed: %s" % iq_file)
        tx_payload_sha = None
        reference_sha = None
        if mode != "OFF":
            expected = amp_vectors[mode]
            tx_payload_sha = expected["tx_payload"]["sha256"]
            reference_sha = expected["reference_4096"]["sha256"]
            if record.get("truth_tx_payload_sha256") != tx_payload_sha:
                raise SystemExit("runner payload SHA != hardware_amp60 manifest: %s" % iq_file)
        tx = {
            "mode": mode,
            "sample_rate_hz": 60_000_000,
            "symbol_rate_hz": 3_000_000,
            "shaping": "RECT",
            "amplitude": 60,
            "rolloff": "0.35",
        }
        if mode != "OFF":
            tx["seed"] = seed
        entry = {
            "frame_identity": {
                "capture_id": record["capture_id"],
                "request_id": meta.get("request_id"),
                "iq_sha256": digest,
                "release_id": meta.get("release_id"),
                "connection_generation": meta.get("connection_generation"),
                "config_revision": meta.get("config_revision"),
            },
            "tx": tx,
            "_provenance": {
                "runner_record_index": index,
                "runner_file": "evidence/rf-teaching/s2-46db-fourclass-real-rx-2026-10-22/run/fourclass.json",
                "runner_file_sha256": sha256_file(RUN / "fourclass.json"),
                "hardware_amp60_manifest": "ml_training/fourclass_test_vectors/hardware_amp60/outputs/manifest.json",
                "hardware_amp60_manifest_sha256": sha256_file(AMP60 / "outputs" / "manifest.json"),
                "tx_payload_sha256": tx_payload_sha,
                "reference_4096_sha256": reference_sha,
                "runner_truth_source": record.get("truth_source"),
                "truth_rule": "runner-recorded TX mode/seed/amplitude + frame identity/IQ SHA",
            },
        }
        frames.append(entry)
        evidence.append({"index": index, "iq_file": iq_file, "mode": mode,
                         "seed": seed, "capture_id": record["capture_id"],
                         "iq_sha256": digest})
    truth = {
        "schema": TRUTH_SCHEMA,
        "truth_source": "independent_tx_config",
        "source": ("runner fourclass.json TX mode/seed/amplitude + hardware_amp60 "
                   "payload hashes + per-frame identity/IQ SHA"),
        "config": {
            "sample_rate_hz": 60_000_000,
            "symbol_rate_hz": 3_000_000,
            "shaping": "RECT",
            "amplitude": 60,
            "rolloff": "0.35",
        },
        "frames": frames,
    }
    truth_path = out / "tx_truth_side.json"
    truth_path.write_text(json.dumps(truth, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    provenance = {
        "schema": "ml-fourclass-real-rx-truth-provenance-v1",
        "truth_file": str(truth_path),
        "truth_sha256": sha256_file(truth_path),
        "runner_fourclass_sha256": sha256_file(RUN / "fourclass.json"),
        "hardware_amp60_manifest_sha256": sha256_file(AMP60 / "outputs" / "manifest.json"),
        "frames": evidence,
        "rule": ("truth from independent runner TX record and frame identity; capture_id "
                 "repeats across sessions so IQ SHA is part of every key"),
    }
    (out / "truth_provenance.json").write_text(
        json.dumps(provenance, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"truth_file": str(truth_path), "frames": len(frames),
            "truth_sha256": provenance["truth_sha256"]}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1))
