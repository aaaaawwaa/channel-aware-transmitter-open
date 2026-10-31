#!/usr/bin/env python
"""Build the small synthetic fixtures used by ``ml_training/evaluation/tests``.

Run once from the project root (or any cwd):

    .\\ml_training\\.venv\\Scripts\\python.exe ml_training\\evaluation\\tests\\fixtures\\_build_fixtures.py

Everything written here is *fixture* data: tiny hand-made npz datasets, a random
untrained ``model.json`` with the contractual shapes, and a fake real-evidence tree
(synthetic IQ bytes with real-evidence-shaped JSON).  No real evidence is copied,
changed or moved by this builder.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[3]

CLASS_ORDER = ["BPSK", "QPSK", "QAM16", "QAM64"]
GATE_KINDS = ["OFF", "NOISE", "TONE", "CLIP"]
SAMPLES = 4096
FRAME_BYTES = SAMPLES * 4


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def frame_sha(x):
    return sha_bytes(np.ascontiguousarray(x).astype("<i2").tobytes())


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def write_iq(path, iq):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.ascontiguousarray(iq).astype("<i2").tobytes())
    return sha_bytes(path.read_bytes())


# ---------------------------------------------------------------------------
# 1. tiny simulated dataset (contract-shaped npz + manifest)
# ---------------------------------------------------------------------------
def build_sim_dataset():
    root = HERE / "sim-small"
    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    rng = np.random.default_rng(20261020)
    files = []
    splits = {
        "train": {"seed_families": ["train-a"], "snr": {"kind": "uniform", "low": -6.0, "high": 18.0},
                  "sessions": []},
        "val": {"seed_families": ["val-a"], "snr": {"kind": "uniform", "low": -6.0, "high": 18.0},
                "sessions": []},
        "test": {"seed_families": ["test-a"], "snr_db_grid": [0.0, 10.0], "sessions": []},
        "gate_cal": {"families": ["gate-cal-a"], "frames_per_kind": 3, "sessions": []},
        "gate_test": {"families": ["gate-test-a"], "frames_per_kind": 3, "sessions": []},
    }

    def base_arrays(n, labels, split, session_id, seed_family, snr):
        clipped = np.zeros(n, dtype=bool)
        return OrderedLike({
            "frame_id": np.asarray(["%s-%02d" % (session_id, index) for index in range(n)]),
            "session_id": np.asarray([session_id] * n),
            "label": np.asarray([labels[index] for index in range(n)]),
            "seed_family": np.asarray([seed_family] * n),
            "symbol_seed": np.arange(1000, 1000 + n, dtype=np.uint64),
            "noise_seed": np.arange(2000, 2000 + n, dtype=np.uint64),
            "snr_db": np.asarray(snr, dtype=np.float32),
            "amplitude_codes": np.asarray([600] * n, dtype=np.int16),
            "phase_rad": np.zeros(n, dtype=np.float32),
            "cfo_hz": np.zeros(n, dtype=np.float32),
            "window_offset": np.zeros(n, dtype=np.int16),
            "noise_sigma_codes": np.asarray([10.0] * n, dtype=np.float32),
            "clipped": clipped,
        })

    for label_index, label in enumerate(CLASS_ORDER):
        session_id = "test-%s-test-a" % label
        arrays = base_arrays(3, [label] * 3, "test", session_id, "test-a",
                             [0.0, 10.0, 10.0])
        x = rng.integers(-500, 500, size=(3, SAMPLES, 2), dtype=np.int16)
        # third frame is a clipped frame: excluded from 4-class metrics by contract
        x[2, 100, 0] = 2047
        arrays["clipped"] = np.asarray([False, False, True])
        arrays["X"] = x
        arrays["iq_sha256"] = np.asarray([frame_sha(row) for row in x])
        path = root / "frames" / ("test-%s.npz" % label)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, **arrays)
        files.append({
            "path": "frames/test-%s.npz" % label, "sha256": sha_bytes(path.read_bytes()),
            "split": "test", "label": label, "count": 3, "clipped_count": 1,
        })
        splits["test"]["sessions"].append({
            "session_id": session_id, "label": label, "seed_family": "test-a",
            "frames": 2, "snr_db_grid": [0.0, 10.0],
        })

    for kind in GATE_KINDS:
        session_id = "gate-test-%s-gate-test-a" % kind
        arrays = base_arrays(3, [kind] * 3, "gate_test", session_id, "gate-test-a",
                             [0.0, 0.0, 0.0])
        if kind == "OFF":
            x = rng.integers(-20, 20, size=(3, SAMPLES, 2), dtype=np.int16)
        elif kind == "NOISE":
            x = rng.integers(-300, 300, size=(3, SAMPLES, 2), dtype=np.int16)
        elif kind == "TONE":
            n = np.arange(SAMPLES)
            tone = (800.0 * np.sin(2.0 * np.pi * 0.001 * n)).astype(np.int16)
            rows = []
            for _ in range(3):
                row = np.stack([tone, tone], axis=1)
                rows.append(row + rng.integers(-5, 6, size=row.shape).astype(np.int16))
            x = np.stack(rows).astype(np.int16)
        else:  # CLIP: quantization overflow, kept and flagged by contract
            x = rng.integers(1800, 3000, size=(3, SAMPLES, 2), dtype=np.int16)
            arrays["clipped"] = np.asarray([True, True, True])
        arrays["X"] = x
        arrays["iq_sha256"] = np.asarray([frame_sha(row) for row in x])
        path = root / "gate" / ("gate_test-%s.npz" % kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, **arrays)
        files.append({
            "path": "gate/gate_test-%s.npz" % kind, "sha256": sha_bytes(path.read_bytes()),
            "split": "gate_test", "label": kind, "count": 3,
            "clipped_count": 3 if kind == "CLIP" else 0,
        })
        splits["gate_test"]["sessions"].append({
            "session_id": session_id, "kind": kind, "label": kind,
            "seed_family": "gate-test-a", "frames": 3, "snr_db_grid": None,
        })

    manifest = {
        "schema": "ml-modrec-synthetic-v1",
        "dataset_id": "sim-small",
        "created_utc": "2026-10-20T00:00:00Z",
        "config_path": "ml_training/config/first_round.json",
        "config_sha256": sha_bytes((PROJECT_ROOT / "ml_training" / "config" / "first_round.json").read_bytes())
        if (PROJECT_ROOT / "ml_training" / "config" / "first_round.json").is_file() else "0" * 64,
        "numpy_version": np.__version__,
        "waveform": {"fs_hz": 60000000, "symbol_rate_hz": 3000000, "sps": 20,
                     "shaping": "RECT", "samples": SAMPLES, "n_symbols": 207},
        "classes": CLASS_ORDER,
        "gate_kinds": GATE_KINDS,
        "splits": splits,
        "files": files,
        "summary": {"total_frames": sum(item["count"] for item in files),
                    "clipped_frames": sum(item["clipped_count"] for item in files),
                    "cross_split_duplicate_iq": 0, "intra_split_duplicate_iq": 0,
                    "note": "fixture dataset for evaluation tests; NOT the training dataset"},
    }
    write_json(root / "manifest.json", manifest)
    print("wrote fixture sim dataset: %s" % root)


class OrderedLike(dict):
    """Plain dict wrapper (kept for readability of the array construction code)."""


# ---------------------------------------------------------------------------
# 2. random untrained model.json with contractual shapes
# ---------------------------------------------------------------------------
def build_fake_model():
    root = HERE / "fake_model"
    rng = np.random.default_rng(7)
    weights = {}

    def add(name, shape):
        weights[name] = {
            "shape": list(shape),
            "values": [float(value) for value in rng.normal(0.0, 0.05, size=shape).reshape(-1)],
        }

    add("conv1.weight", (8, 2, 7)); add("conv1.bias", (8,))
    add("conv2.weight", (16, 8, 5)); add("conv2.bias", (16,))
    add("conv3.weight", (16, 16, 3)); add("conv3.bias", (16,))
    add("head.weight", (4, 16)); add("head.bias", (4,))
    model = {
        "schema_version": 1,
        "network": "tiny-iq-cnn-v1",
        "variant": "modrec4",
        "preprocessing": "dc-complex-rms-v1",
        "classes": CLASS_ORDER,
        "input_shape": [2, SAMPLES],
        "supported_domain": {"input_fs_hz": 60000000, "decim": [1],
                             "symbol_rates_hz": [3000000], "shaping": ["RECT"]},
        "thresholds": {"max_softmax": 0.90, "margin": 0.10, "min_complex_rms": 20.0},
        "weights": weights,
        "params": 1628,
        "macs": 0,
        "training": {"manifest_sha256": "fixture", "config_sha256": "fixture",
                     "best_epoch": 0, "val_macro_f1": 0.0, "seed": 0},
        "status": "experimental",
        "board_compat": "fixture: untrained random weights, evaluation-test only",
        "fixture_note": "Random untrained weights with contractual shapes. This is NOT a "
                        "trained model and must never be used for reported metrics.",
    }
    write_json(root / "model.json", model)
    write_json(root / "metadata.json", {
        "schema": "ml-modrec-artifact-fixture-v1",
        "note": "evaluation test fixture - random untrained weights",
        "model_json_sha256": sha_bytes((root / "model.json").read_bytes()),
    })
    print("wrote fake model: %s" % root)


# ---------------------------------------------------------------------------
# 3. fake real-evidence tree (real-evidence-shaped JSON, synthetic IQ bytes)
# ---------------------------------------------------------------------------
BASE_CONFIG = {"mode": "BPSK", "symbol_rate_hz": 3000000, "shaping": "RECT",
               "reference_prbs7_seed": 1}


def frame_json(*, truth, scenario, physical, config=None, clipping_count=0,
               output_clipped=False, json_sha=None, actual_sha, board_label,
               model_label=None, include_trainval=True, include_truth=True):
    result_label = model_label if model_label is not None else board_label
    document = {
        "capture_status": {"state": "done", "physical": True,
                           "capture_id": "fixture-capture-01"},
        "analysis": {"clipping_count": clipping_count, "output_clipped": output_clipped,
                     "valid": True, "complex_rms": 500.0, "no_energy": False},
        "model_result": {"quality": "OK" if result_label == "BPSK" else "UNKNOWN",
                         "label": result_label, "rejected": result_label == "UNKNOWN",
                         "reason": "" if result_label == "BPSK" else "fixture_reason"},
        "demod_result": {"submitted": {"config": dict(config or BASE_CONFIG),
                                       "revision": 1, "decim": 1, "rx_len": 4096,
                                       "input_fs_hz": 60000000},
                         "result": {"label": "BPSK" if truth == "normal" else "UNKNOWN"}},
        "iq_sha256": json_sha if json_sha is not None else actual_sha,
    }
    if include_trainval:
        trainval = {"tag": "fixture", "scenario": scenario, "physical": physical,
                    "label": board_label, "reason": "fixture_reason", "rail": False,
                    "reference_sync": truth == "normal",
                    "ber": 0.0 if truth == "normal" else None,
                    "clipping_count": clipping_count}
        if include_truth:
            trainval["truth"] = truth
        document["trainval"] = trainval
    return document


def build_real_evidence():
    root = HERE / "real-evidence"
    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    canonical = root / "campaign5-val-followup" / "download"
    campaign4 = root / "campaign4" / "download"
    rng = np.random.default_rng(11)

    def clean_iq(scale=600):
        return rng.integers(-scale, scale, size=(SAMPLES, 2), dtype=np.int16)

    def write_session(session, split, frames):
        session_dir = canonical / session
        session_dir.mkdir(parents=True, exist_ok=True)
        slots = []
        normal = 0
        off = 0
        for position, frame in enumerate(frames):
            iq_sha = write_iq(session_dir / (frame["tag"] + ".iq"), frame["iq"])
            frame["iq_sha"] = iq_sha
            document = frame_json(
                truth=frame["truth"], scenario=frame["truth"], physical=frame["truth"] == "normal",
                config=frame.get("config"), clipping_count=frame.get("clipping_count", 0),
                output_clipped=frame.get("output_clipped", False),
                json_sha=frame.get("json_sha", iq_sha), actual_sha=iq_sha,
                board_label=frame.get("board_label", "CLEAR"),
                model_label=frame.get("model_label"),
                include_trainval=frame.get("include_trainval", True),
                include_truth=frame.get("include_truth", True),
            )
            write_json(session_dir / (frame["tag"] + ".json"), document)
            normal += 1 if frame["truth"] == "normal" else 0
            off += 1 if frame["truth"] == "off" else 0
            slots.append({
                "position": position, "tag": frame["tag"], "scenario": frame["truth"],
                "iq": frame["tag"] + ".iq", "metadata": frame["tag"] + ".json",
                "iq_sha256": iq_sha, "clipping_count": frame.get("clipping_count", 0),
                "label": frame.get("board_label", "CLEAR"), "rail": False,
                "physical": frame["truth"] == "normal",
            })
        write_json(session_dir / "session-manifest.json", {
            "schema": "rf-environment-session-manifest-v1",
            "fixture": True, "collected": True, "physical": True,
            "not_for_formal_acceptance": True,
            "frame_count": len(slots), "normal_count": normal, "off_count": off,
            "session": {"session_id": session, "split": split, "index": 1, "seed": 1},
            "slots": slots,
            "release_id": "fixture-release",
            "environment_version": "fixture-environment",
            "domain": {"lo_hz": 80000000, "sample_rate_hz": 60000000,
                       "attenuation_mdB": 46000},
        })
        return session_dir

    # --- train-01: clean normal + clipped normal + off ---
    clean_train = clean_iq()
    train01 = [
        {"tag": "normal-01", "truth": "normal", "iq": clean_train,
         "board_label": "UNKNOWN", "model_label": "BPSK"},
        {"tag": "normal-02", "truth": "normal", "iq": clean_iq(),
         "clipping_count": 3, "output_clipped": True},
        {"tag": "off-01", "truth": "off", "iq": rng.integers(-30, 30, size=(SAMPLES, 2),
                                                            dtype=np.int16)},
    ]
    write_session("train-01", "train", train01)

    # --- train-02: duplicate of train-01 normal-01 (same bytes), truth-missing frame,
    #     config-mismatch frame, unique off ---
    train02 = [
        {"tag": "normal-01", "truth": "normal", "iq": clean_train.copy()},
        {"tag": "normal-02", "truth": None, "iq": clean_iq(), "include_truth": False,
         "board_label": "BPSK"},
        {"tag": "normal-03", "truth": "normal", "iq": clean_iq(),
         "config": {"mode": "QPSK", "symbol_rate_hz": 3000000, "shaping": "RECT"}},
        {"tag": "off-01", "truth": "off", "iq": rng.integers(-25, 25, size=(SAMPLES, 2),
                                                             dtype=np.int16)},
    ]
    # truth-missing frame: keep normal/off truth absent but trainval block present
    for frame in train02:
        if frame["truth"] is None:
            frame["truth"] = "normal"  # only used for scenario/physical rendering
            frame["include_truth"] = False
    write_session("train-02", "train", train02)

    # --- val-01: counted normal, a frame whose JSON top-level hash is wrong, counted off ---
    val01 = [
        {"tag": "normal-01", "truth": "normal", "iq": clean_iq()},
        {"tag": "normal-02", "truth": "normal", "iq": clean_iq(),
         "json_sha": "0" * 64},
        {"tag": "off-01", "truth": "off", "iq": rng.integers(-20, 20, size=(SAMPLES, 2),
                                                             dtype=np.int16)},
    ]
    write_session("val-01", "val", val01)

    # --- campaign4: duplicate of train-01 normal-01 + one unique non-canonical frame ---
    c4_train = campaign4 / "train-01"
    c4_train.mkdir(parents=True, exist_ok=True)
    c4_sha = write_iq(c4_train / "normal-01.iq", clean_train)
    write_json(c4_train / "normal-01.json", frame_json(
        truth="normal", scenario="normal", physical=True, actual_sha=c4_sha,
        board_label="UNKNOWN"))
    c4_val = campaign4 / "val-01"
    c4_val.mkdir(parents=True, exist_ok=True)
    unique = clean_iq()
    unique_sha = write_iq(c4_val / "normal-01.iq", unique)
    write_json(c4_val / "normal-01.json", frame_json(
        truth="normal", scenario="normal", physical=True, actual_sha=unique_sha,
        board_label="PULSE"))
    print("wrote fake real evidence: %s" % root)


def main():
    build_sim_dataset()
    build_fake_model()
    build_real_evidence()
    print("fixtures ready under %s" % HERE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
