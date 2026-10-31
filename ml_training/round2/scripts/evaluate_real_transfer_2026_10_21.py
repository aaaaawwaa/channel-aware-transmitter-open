"""Frozen round-2 pipeline transfer test on the 2026-10-21 80 MHz narrowband evidence.

Scope of this test (see task constraints):
* read-only: no retraining, no re-calibration, no device access, no product-runtime change;
* the frozen round-2 solution is the round-1 4-class CNN + the round-2 interpretable baseline
  arbitration + spectral gate, with all thresholds already frozen before this data existed;
* the evidence directory ``evidence/rf-teaching/s2-46db-80mhz-narrowband-2026-10-21/raw``
  contains one new physical normal frame and three physical narrowband/interference frames
  captured on 2026-10-21 15:48 (after the 2026-10-20 round-2 freeze);
* the 4-class clean metric is only meaningful for the normal frame, and it is a single frame;
  the three narrowband frames are a separate stress test and are never counted as clean
  4-class accuracy;
* environment labels (e.g. ``UNKNOWN``, ``NARROWBAND``) are NOT modulation truth.

The script writes the raw frozen results JSON **before** any markdown summary, so a failure is
preserved as-is.  It writes only under ``ml_training/round2/evaluation/reports``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402
from ml_training.round2.features import spectral_features  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402

ML = ROOT / "ml_training"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "s2-46db-80mhz-narrowband-2026-10-21"
RAW = EVIDENCE / "raw"
REPORTS = ML / "round2" / "evaluation" / "reports"
RAW_RESULTS = REPORTS / "real_transfer_2026-10-21_raw.json"
REPORT_MD = REPORTS / "real_transfer_2026-10-21.md"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
TAGS = ("nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband")
DEV_ROOTS = (
    ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / "campaign5-val-followup",
    ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / "campaign4",
    ROOT / "evidence" / "rf-teaching" / "s2" / "46db-trainval-campaign3-2026-10-18",
)
EXPECTED_FROZEN = {
    ML / "artifacts" / "cnn" / "model.json":
        "25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc",
    ML / "artifacts" / "baseline" / "model.json":
        "caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014",
}
EXPECTED_RULE_SCHEMA = "ml-modrec-round2-rule-config-v1"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def mtime_iso(path):
    return datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone().isoformat(
        timespec="seconds")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def decode_frame(path):
    raw = Path(path).read_bytes()
    array = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    return raw, array


def frame_before(cnn, iq):
    decision = cnn.predict_iq(iq)
    return {"label": decision.get("label"), "reason": decision.get("reason", ""),
            "score": decision.get("score"), "margin": decision.get("margin"),
            "scores": decision.get("scores"), "rms": decision.get("rms")}


def frame_after(predictor, iq):
    decision = predictor.decide(iq)
    classification = predictor.classify(iq)
    return {
        "label": decision["label"], "reason": decision["reason"],
        "score": decision["score"], "margin": decision["margin"],
        "scores": decision["scores"], "rms": decision["rms"],
        "arbitration_applied": decision["arbitration_applied"],
        "arbitration": decision.get("arbitration"),
        "baseline_scores": decision.get("baseline_scores"),
        "spectral": decision.get("spectral"),
        "spectral_trigger": decision.get("spectral_trigger"),
        "pure_class_label": classification.get("class_label"),
        "pure_class_index": classification.get("class_index"),
        "pure_arbitration_applied": classification.get("arbitration_applied"),
    }


def diagnostics(iq):
    x, gate = preprocess_iq(iq)
    values = {"gate_ok": bool(gate.get("ok")), "gate_reason": gate.get("reason", ""),
              "rms": float(gate.get("rms", 0.0)), "clipped": bool(gate.get("clipped", False))}
    if gate.get("ok"):
        z = x[0].astype(np.float64) + 1j * x[1].astype(np.float64)
        values["phase_deg"] = float(np.degrees(np.angle(np.mean(z ** 2)) / 2.0))
        values["spectral"] = {key: float(value)
                              for key, value in spectral_features(x).items()}
    return values


def verify_sha256sums():
    sums_path = EVIDENCE / "SHA256SUMS"
    entries = 0
    mismatches = []
    if not sums_path.is_file():
        return {"available": False, "entries": 0, "mismatches": ["SHA256SUMS missing"]}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, relative = line.split(maxsplit=1)
        path = EVIDENCE / relative.replace("/", os.sep)
        entries += 1
        if not path.is_file():
            mismatches.append({"path": relative, "issue": "missing"})
            continue
        actual = sha256_file(path)
        if actual != digest:
            mismatches.append({"path": relative, "expected": digest, "actual": actual})
    return {"available": True, "entries": entries, "mismatches": mismatches,
            "all_ok": not mismatches}


def development_hash_audit(frame_hashes):
    dev_hashes = set()
    for root in DEV_ROOTS:
        for path in root.rglob("*.iq"):
            dev_hashes.add(sha256_file(path))
    overlap = sorted(set(frame_hashes) & dev_hashes)
    capture_mtime = (RAW / (TAGS[0] + ".iq")).stat().st_mtime
    references = {"pre_existing": [], "created_after_capture": []}
    needles = [str(EVIDENCE.relative_to(ROOT)).replace("\\", "/")]
    needles += list(frame_hashes)
    needles += [digest[:16] for digest in frame_hashes]
    for path in sorted(ML.rglob("*")):
        if not path.is_file() or ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in (".py", ".json", ".md", ".txt", ".cfg", ".toml"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle and needle in text:
                record = {"path": str(path.relative_to(ROOT)).replace("\\", "/"),
                          "needle": needle, "mtime": mtime_iso(path)}
                bucket = ("pre_existing" if path.stat().st_mtime < capture_mtime
                          else "created_after_capture")
                references[bucket].append(record)
                break
    pre_existing = references["pre_existing"]
    frozen_mtimes = {
        "cnn_model_json": mtime_iso(ML / "artifacts" / "cnn" / "model.json"),
        "baseline_model_json": mtime_iso(ML / "artifacts" / "baseline" / "model.json"),
        "round2_rule_config": mtime_iso(ML / "round2" / "artifacts" / "rule_config.json"),
        "evidence_raw_frame": mtime_iso(RAW / (TAGS[0] + ".iq")),
    }
    clean = not overlap and not pre_existing
    return {
        "development_real_iq_hashes": len(dev_hashes),
        "development_roots": [str(root.relative_to(ROOT)).replace("\\", "/")
                              for root in DEV_ROOTS],
        "overlap": overlap,
        "overlap_count": len(overlap),
        "ml_training_references_before_capture": pre_existing,
        "pre_existing_reference_count": len(pre_existing),
        "ml_training_references_created_after_capture": references["created_after_capture"],
        "created_after_capture_count": len(references["created_after_capture"]),
        "reference_scan_note": ("files whose mtime is earlier than the new evidence capture are "
                                "pre-existing development references; the new evaluation "
                                "script/report themselves naturally mention the evidence path"),
        "mtimes": frozen_mtimes,
        "conclusion": ("new capture after the frozen artifacts; no hash overlap with the "
                       "round-1/round-2 development replay frames and no pre-existing "
                       "ml_training reference" if clean else
                       "OVERLAP OR PRE-EXISTING REFERENCE FOUND - investigate before claiming "
                       "new data"),
    }


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    for path, expected in EXPECTED_FROZEN.items():
        if not path.is_file():
            raise SystemExit("missing frozen artifact: %s" % path)
        actual = sha256_file(path)
        if actual != expected:
            raise SystemExit("frozen artifact changed: %s (%s != %s)"
                             % (path, actual, expected))
    rule_path = ML / "round2" / "artifacts" / "rule_config.json"
    rule_doc = load_json(rule_path)
    if rule_doc.get("schema") != EXPECTED_RULE_SCHEMA:
        raise SystemExit("unexpected rule config schema")
    manifest = load_json(RAW / "SUMMARY.json")
    analysis = load_json(EVIDENCE / "analysis" / "fourframe-analysis.json")
    summary_by_tag = {entry["tag"]: entry for entry in manifest["frames"]}
    sha_audit = verify_sha256sums()

    frozen_cnn = load_model(ML / "artifacts" / "cnn" / "model.json")
    predictor = Round2Predictor()

    frames = []
    frame_hashes = []
    for tag in TAGS:
        iq_path = RAW / (tag + ".iq")
        meta = load_json(RAW / (tag + ".json"))
        env = load_json(RAW / (tag + ".environment.json")).get("environment", {})
        raw, array = decode_frame(iq_path)
        digest = sha256_bytes(raw)
        frame_hashes.append(digest)
        summary = summary_by_tag.get(tag, {})
        declared = (analysis.get("frames", {}).get(tag, {}) or {}).get("declared", {})
        demod = meta.get("demod_result") or {}
        submitted = demod.get("submitted") or {}
        demod_result = demod.get("result") or {}
        iq = array.astype(np.int16)
        before = frame_before(frozen_cnn, iq)
        after = frame_after(predictor, iq)
        record = {
            "tag": tag,
            "kind": "normal" if tag.endswith("normal") else "narrowband",
            "files": {"iq": str(iq_path.relative_to(ROOT)).replace("\\", "/"),
                      "metadata": str((RAW / (tag + ".json")).relative_to(ROOT)).replace("\\", "/"),
                      "environment": str((RAW / (tag + ".environment.json"))
                                         .relative_to(ROOT)).replace("\\", "/")},
            "format": {
                "bytes": len(raw), "dtype": "little-endian signed int16",
                "shape": list(array.shape), "samples": int(array.shape[0]),
                "min_code": int(array.min()), "max_code": int(array.max()),
                "all_zero": bool(np.all(array == 0)),
                "rail_count": int(np.sum((array <= -2048) | (array >= 2047))),
                "expected_bytes": 16384,
                "format_ok": bool(len(raw) == 16384 and array.shape == (4096, 2)
                                  and array.min() >= -2048 and array.max() <= 2047),
            },
            "hashes": {
                "iq_sha256": digest,
                "metadata_iq_sha256": meta.get("iq_sha256"),
                "summary_iq_sha256": summary.get("iq_sha256"),
                "metadata_match": digest == meta.get("iq_sha256"),
                "summary_match": digest == summary.get("iq_sha256"),
            },
            "identity": {
                "capture_id": (meta.get("capture_status") or {}).get("capture_id"),
                "release_id": meta.get("release_id"),
                "capture_state": (meta.get("capture_status") or {}).get("state"),
                "physical": (meta.get("capture_status") or {}).get("physical"),
                "op": meta.get("op"),
                "session": summary.get("session"),
                "egress_safe": summary.get("egress_safe"),
                "environment_label": env.get("label"),
                "environment_reason": env.get("reason"),
            },
            "truth": {
                "tx_config": submitted.get("config"),
                "tx_config_ok": (
                    (submitted.get("config") or {}).get("mode") == "BPSK"
                    and (submitted.get("config") or {}).get("symbol_rate_hz") == 3_000_000
                    and (submitted.get("config") or {}).get("shaping") == "RECT"),
                "demod_reason": demod_result.get("reason"),
                "demod_carrier_sync": demod_result.get("carrier_sync"),
                "demod_reference_sync": demod_result.get("reference_sync"),
                "demod_ber": demod_result.get("ber"),
                "demod_compared_bits": demod_result.get("compared_bits"),
                "declared_scenario": declared.get("truth"),
                "declared_tone_amplitude": declared.get("tone_amplitude"),
                "declared_tone_frequency_hz": declared.get("tone_frequency_requested_hz"),
                "environment_label_is_not_truth": True,
                "truth_conclusion": (
                    "BPSK / 3 MSym/s / RECT from the submitted TX config"
                    + ("; normal scenario declared and demod BER=0 with sync"
                       if tag.endswith("normal")
                       else "; narrowband scenario declared (BPSK plus injected tone); "
                            "interference stress test, not clean 4-class data")),
            },
            "diagnostics": diagnostics(iq),
            "before": before,
            "after": after,
            "board_runtime_context": {
                "note": ("the raw metadata was produced by the board runtime 6-class model, "
                         "not by the frozen offline 4-class pipeline; shown as context only"),
                "label": (meta.get("model_result") or {}).get("label"),
                "model_sha256": (meta.get("model_result") or {}).get("model_sha256"),
                "rejected": (meta.get("model_result") or {}).get("rejected"),
            },
        }
        frames.append(record)

    audit = development_hash_audit(frame_hashes)
    normal = [item for item in frames if item["kind"] == "normal"]
    narrow = [item for item in frames if item["kind"] == "narrowband"]
    normal_ok = [item for item in normal
                 if item["after"]["label"] == "BPSK"
                 and item["after"]["reason"] == ""
                 and item["truth"]["truth_conclusion"].startswith("BPSK")]
    result = {
        "schema": "ml-modrec-real-transfer-frozen-v1",
        "generated_utc": utc_now(),
        "scope": ("frozen round-2 offline pipeline transfer test on new physical 2026-10-21 "
                  "frames; read-only; no retraining/recalibration/device/product change"),
        "frozen_pipeline": {
            "cnn_model_json": {"path": "ml_training/artifacts/cnn/model.json",
                               "sha256": sha256_file(ML / "artifacts" / "cnn" / "model.json")},
            "baseline_model_json": {"path": "ml_training/artifacts/baseline/model.json",
                                    "sha256": sha256_file(ML / "artifacts" / "baseline" /
                                                          "model.json")},
            "rule_config": {"path": "ml_training/round2/artifacts/rule_config.json",
                            "sha256": sha256_file(rule_path),
                            "schema": rule_doc.get("schema"),
                            "arbitration_threshold":
                                rule_doc["arbitration"]["threshold"],
                            "tone_rule": rule_doc["spectral_gate"]["tone"],
                            "noise_rule": rule_doc["spectral_gate"]["noise"],
                            "frozen_gate": rule_doc["frozen_round1"]["gate"]},
            "pipeline": "round-1 4-class TinyIQCNN + round-2 feature arbitration + spectral gate",
        },
        "data": {
            "evidence_root": str(EVIDENCE.relative_to(ROOT)).replace("\\", "/"),
            "summary": str((RAW / "SUMMARY.json").relative_to(ROOT)).replace("\\", "/"),
            "release_id": manifest.get("release_id"),
            "frames": len(frames),
            "sha256sums": sha_audit,
            "capture_script_note": "不读取执行命令，仅使用 raw 原始帧与其 sidecar",
        },
        "development_use_audit": audit,
        "frames": frames,
        "summary": {
            "normal": {
                "frames": len(normal),
                "clean_4class_accuracy": (len(normal_ok) / len(normal)) if normal else None,
                "note": ("single normal frame; a per-frame pass is not an accuracy estimate "
                         "and does not establish real four-class accuracy"),
                "before_labels": [item["before"]["label"] for item in normal],
                "after_labels": [item["after"]["label"] for item in normal],
                "after_reasons": [item["after"]["reason"] for item in normal],
            },
            "narrowband_stress": {
                "frames": len(narrow),
                "counted_as_clean_4class": False,
                "interpretation": ("stress only: underlying TX is BPSK plus an injected 6 MHz "
                                   "tone; any accepted known-class label here is a stress "
                                   "false acceptance, not clean accuracy"),
                "before_labels": [item["before"]["label"] for item in narrow],
                "after_labels": [item["after"]["label"] for item in narrow],
                "after_reasons": [item["after"]["reason"] for item in narrow],
            },
        },
        "discipline": {
            "retrained": False, "recalibrated": False, "device_accessed": False,
            "product_runtime_modified": False,
            "results_saved_before_any_improvement": True,
            "dev_replay_note": ("these frames are new and were not used in development; if "
                                "they are later used to design an improvement they become "
                                "development replay data and must no longer be called unseen"),
        },
    }
    RAW_RESULTS.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("[transfer] raw frozen results written first:", RAW_RESULTS)
    print(json.dumps(result["summary"], indent=1))
    for item in frames:
        print("%-18s truth=%-9s before=%-6s(%s) after=%-8s(%s) arb=%s env=%s"
              % (item["tag"], item["truth"]["declared_scenario"], item["before"]["label"],
                 item["before"]["reason"] or "-", item["after"]["label"],
                 item["after"]["reason"] or "-", item["after"]["arbitration_applied"],
                 item["identity"]["environment_label"]))
    print("[transfer] development_use_audit:", json.dumps(audit["conclusion"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
