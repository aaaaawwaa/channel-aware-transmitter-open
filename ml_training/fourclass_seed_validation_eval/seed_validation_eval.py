#!/usr/bin/env python3
"""Frozen four-model comparison entry for the new-seed 10-frame validation batch.

The four locked models are diagnostic comparison versions only, NOT approved releases.
Each raw IQ frame is validated independently for:
  * actual IQ SHA-256 / input domain / frame identity;
  * actual runner TX-load record versus the independent truth row (mode/seed/amplitude/
    payload SHA/length); OFF frames require off_tx_ok and no TX source selected;
then the same raw IQ is evaluated by original, A, C0 and C1 through Round2Predictor.

Truth is never filled from model prediction or RX expected_mode.  Missing data yields
NO_FRAMES and exits 0; no waiting/looping.  The 14 old frames and transforms are not
read here.  This evaluator never trains, changes thresholds or touches a device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
DATASET = (ROOT / "evidence" / "rf-teaching" /
           "s2-46db-fourclass-seed-validation-2026-10-22")
DEFAULT_RAW = DATASET / "raw"
DEFAULT_TRUTH = DATASET / "truth.json"
DEFAULT_PLAN = DATASET / "plan.json"
MODEL_MANIFEST = BASE / "models_manifest.json"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
WORKER_ARG_KEYS = ("mode", "seed", "amplitude", "filter", "rolloff",
                   "sample_rate_hz", "symbol_rate_hz", "waveform")
TX_METADATA_KEYS = ("samples", "bytes", "format", "direction", "wrap")
DIGITAL_SAMPLES = 2540
DIGITAL_BYTES = 10160

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import domain  # noqa: E402
from ml_training.real_campaign.importer import parse_task68_frames  # noqa: E402
from ml_training.round2.predictor import Round2Predictor  # noqa: E402


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jwrite(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def norm_label(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def field_requirements():
    return {
        "schema": "ml-fourclass-seed-validation-eval-v1",
        "frames_root": ("raw/ from the new-seed validation download; one subdir per frame "
                        "with <label>.iq + <label>.json, optionally records.json"),
        "truth_file": "truth.json (independent PRBS7/constellation rows; model not truth)",
        "plan_file": "plan.json (frame order, physical/ON/OFF, expected TX load)",
        "per_frame_metadata_required": [
            "capture_id", "request_id", "iq_sha256", "release_id",
            "input_domain_status=VALID", "input_domain observed fields",
            "tx_load_evidence.load_ok=true (ON/OFF actual record)",
            "tx_load_evidence.worker_args: mode/seed/amplitude/filter/rolloff/"
            "sample_rate_hz/symbol_rate_hz/waveform (source parameters)",
            "tx_load_evidence.tx_load_metadata: daemon protocol samples/bytes/"
            "format/direction/wrap (NOT source parameters)",
            "tx_load_evidence.payload_sha256/payload_length (ON vs independent truth)",
            "off_tx_ok=true + pre/post tx_source_selected=false (OFF)",
            "modrec4/frame_identity with capture_id/request_id/release_id/iq_sha256",
        ],
        "truth_policy": [
            "truth class/mode/seed/amplitude/payload SHA come from independent truth/plan;",
            "never from modrec4 prediction, RX expected_mode or environment labels",
        ],
        "no_frames": "status=NO_FRAMES, exit 0, no waiting",
        "models": "original, A, C0, C1 locked in models_manifest.json by path and SHA",
        "invalid_frames": "audit failures are reported separately and never counted",
    }


def load_plan_truth(plan_path, truth_path):
    plan = load_json(plan_path)
    truth = load_json(truth_path)
    plan_rows = list(plan.get("order") or plan.get("frames") or [])
    truth_by_index = {int(row["frame_index"]): row
                      for row in (truth.get("rows") or truth.get("frames") or [])}
    return plan, truth, plan_rows, truth_by_index


def find_plan_row(plan_rows, label):
    target = norm_label(label)
    for row in plan_rows:
        if norm_label(row.get("label")) == target:
            return row
    for row in plan_rows:
        if target.endswith(norm_label(row.get("label"))) or norm_label(row.get("label")).endswith(target):
            return row
    return None


def read_frame_files(iq_path):
    """Return (metadata, record, metadata_path)."""
    meta_path = iq_path.with_suffix(".json")
    records_path = iq_path.parent / "records.json"
    record = None
    if records_path.is_file():
        try:
            records = load_json(records_path)
            if isinstance(records, list):
                for candidate in records:
                    if not isinstance(candidate, dict):
                        continue
                    if (Path(candidate.get("iq_file") or "").name == iq_path.name
                            or Path(candidate.get("metadata_file") or "").name == meta_path.name):
                        record = candidate
                        break
                if record is None and len(records) == 1:
                    record = records[0]
        except Exception:
            record = None
    if not meta_path.is_file() and record is not None:
        metadata_file = Path(record.get("metadata_file") or "")
        if metadata_file.is_file():
            meta_path = metadata_file
        elif (iq_path.parent / metadata_file.name).is_file():
            meta_path = iq_path.parent / metadata_file.name
    meta = load_json(meta_path) if meta_path.is_file() else (record or {})
    return meta, record or {}, meta_path


def extract_modrec4(meta, record):
    return (record.get("modrec4") or meta.get("modrec4_result")
            or meta.get("modrec4") or {})


def extract_identity(meta, record):
    mod = extract_modrec4(meta, record)
    fi = mod.get("frame_identity") or record.get("frame_identity") or meta.get("frame_identity") or {}
    return {
        "capture_id": meta.get("capture_id") or record.get("capture_id") or fi.get("capture_id"),
        "request_id": meta.get("request_id", record.get("request_id", fi.get("request_id"))),
        "release_id": meta.get("release_id") or record.get("release_id") or fi.get("release_id"),
        "connection_generation": meta.get("connection_generation",
                                           record.get("connection_generation",
                                                      fi.get("connection_generation"))),
        "config_revision": meta.get("config_revision", record.get("config_revision",
                                                                   fi.get("config_revision"))),
        "iq_sha256": meta.get("iq_sha256") or record.get("iq_sha256") or fi.get("iq_sha256"),
    }


def extract_input_domain(meta, record):
    mod = extract_modrec4(meta, record)
    block = (mod.get("input_domain") or record.get("input_domain")
             or meta.get("input_domain") or {})
    status = (meta.get("input_domain_status") or record.get("input_domain_status")
              or block.get("status"))
    observed = (meta.get("input_domain_observed") or meta.get("observed_input")
                or block.get("observed") or record.get("input_domain_observed"))
    if observed is None:
        observed = {key: meta.get(key) for key in domain.CONTEXT_FIELDS}
    return {"status": status, "observed": observed, "check": domain.validate_context(observed)}


def extract_actual_load(meta, record):
    ev = record.get("tx_load_evidence") or meta.get("tx_load_evidence")
    if not isinstance(ev, dict):
        return None
    return ev


def extract_board(meta, record):
    mod = extract_modrec4(meta, record)
    modulation = mod.get("modulation") or {}
    return {
        "final_label": modulation.get("final_label"),
        "accepted": modulation.get("accepted"),
        "reason": modulation.get("reason"),
        "scores4": modulation.get("scores4"),
        "source": "board modrec4; diagnostic only, never truth",
    }


def _audit_load_evidence(actual_load, expected_load, truth, off):
    """Validate actual runner evidence; never synthesize missing actual values."""
    errors = []
    if not isinstance(actual_load, dict):
        return errors + ["tx_load_evidence_missing"], None
    if actual_load.get("load_ok") is not True:
        errors.append("tx_load_not_ok")
    worker_args = actual_load.get("worker_args")
    if not isinstance(worker_args, dict):
        errors.append("worker_args_missing")
        worker_args = {}
    tx_metadata = actual_load.get("tx_load_metadata")
    if not isinstance(tx_metadata, dict):
        errors.append("tx_load_metadata_missing")
        tx_metadata = {}
    expected_load = expected_load or {}
    for key in WORKER_ARG_KEYS:
        if key in expected_load and worker_args.get(key) != expected_load[key]:
            errors.append("worker_arg:%s" % key)
    # tx_load_metadata is the daemon protocol metadata, NOT the waveform source args.
    expected_samples = int(truth["tx_samples"]) if truth is not None else DIGITAL_SAMPLES
    expected_bytes = int(truth["tx_bytes"]) if truth is not None else DIGITAL_BYTES
    for key in TX_METADATA_KEYS:
        if key not in tx_metadata:
            errors.append("tx_load_metadata_key:%s" % key)
    if tx_metadata.get("samples") != expected_samples:
        errors.append("tx_load_metadata:samples")
    if tx_metadata.get("bytes") != expected_bytes:
        errors.append("tx_load_metadata:bytes")
    if tx_metadata.get("format") != "s16_iq_le":
        errors.append("tx_load_metadata:format")
    if tx_metadata.get("direction") != "tx":
        errors.append("tx_load_metadata:direction")
    if tx_metadata.get("wrap") is not True:
        errors.append("tx_load_metadata:wrap")
    if truth is not None:
        for key in ("mode", "seed", "amplitude"):
            if worker_args.get(key) != truth.get(key):
                errors.append("worker_arg_truth:%s" % key)
        if actual_load.get("payload_sha256") != truth.get("tx_payload_sha256"):
            errors.append("tx_payload_sha_mismatch")
        if actual_load.get("payload_length") != truth.get("tx_bytes"):
            errors.append("tx_payload_length_mismatch")
    return errors, actual_load


def audit_frame(iq_path, meta, record, plan_row, truth_by_index, plan):
    errors = []
    actual = domain.load_iq(iq_path)
    if not actual["ok"]:
        errors.append("iq:" + str(actual["reason"]))
    digest = actual.get("digest")
    meta_sha = meta.get("iq_sha256") or record.get("iq_sha256")
    if digest and meta_sha and digest != meta_sha:
        errors.append("iq_sha256_metadata_mismatch")
    identity = extract_identity(meta, record)
    if not identity.get("capture_id"):
        errors.append("missing_capture_id")
    if identity.get("iq_sha256") and digest and identity["iq_sha256"] != digest:
        errors.append("iq_sha256_identity_mismatch")
    expected_release = plan.get("release")
    if expected_release and identity.get("release_id") != expected_release:
        errors.append("release_id_mismatch")
    input_domain = extract_input_domain(meta, record)
    if input_domain["status"] != "VALID":
        errors.append("input_domain_not_valid")
    if not input_domain["check"].get("ok"):
        errors.append("input_domain:" + str(input_domain["check"].get("reason")))
    board = extract_board(meta, record)
    actual_load = extract_actual_load(meta, record)
    truth = None
    truth_class = None
    truth_source = None
    actual_tx = None
    if plan_row is None:
        errors.append("plan_frame_not_found")
    else:
        truth_class = plan_row.get("truth_class")
        if plan_row.get("physical") is True:
            truth = truth_by_index.get(int(plan_row["frame_index"]))
            if truth is None:
                errors.append("truth_row_missing")
            load_errors, actual_tx = _audit_load_evidence(
                actual_load, plan_row.get("expected_load") or {}, truth, off=False)
            errors.extend(load_errors)
            if truth is not None:
                truth_source = truth.get("truth_source")
        else:
            truth_class = "OFF"
            # The runner records a TX-load evidence object for OFF too (the load
            # command is validated by check_frame.py); it is NOT an actual RF TX.
            load_errors, actual_tx = _audit_load_evidence(
                actual_load, plan_row.get("expected_load") or {}, None, off=True)
            errors.extend(load_errors)
            if record.get("off_tx_ok") is not True and meta.get("off_tx_ok") is not True:
                errors.append("off_tx_ok_missing_or_false")
            for key in ("pre_tx_source_selected", "post_tx_source_selected"):
                if record.get(key, meta.get(key)) is not False:
                    errors.append("off_%s_not_false" % key)
            truth_source = "runner off_tx_ok + no TX source selected; no model truth"
    return {
        "valid": not errors,
        "errors": errors,
        "iq_sha256": digest,
        "identity": identity,
        "input_domain": input_domain,
        "actual_tx": actual_tx,
        "truth": truth,
        "truth_class": truth_class,
        "truth_source": truth_source,
        "board": board,
        "plan_row": plan_row,
        "iq_path": str(iq_path),
    }


def build_truth_side(valid_frames, out_dir, truth_file, plan_file):
    entries = []
    for frame in valid_frames:
        audit = frame["audit"]
        identity = audit["identity"]
        tx = audit["truth"] or {}
        plan = audit["plan_row"] or {}
        tx_config = {
            "mode": audit["truth_class"],
            "sample_rate_hz": 60_000_000,
            "symbol_rate_hz": 3_000_000,
            "shaping": "RECT",
            "amplitude": 60,
            "rolloff": "0.35",
        }
        if audit["truth_class"] != "OFF":
            tx_config.update({
                "seed": tx.get("seed"),
                "sample_rate_hz": tx.get("sample_rate_hz", 60_000_000),
                "symbol_rate_hz": tx.get("symbol_rate_hz", 3_000_000),
                "shaping": tx.get("filter", "RECT"),
                "amplitude": tx.get("amplitude", 60),
                "rolloff": tx.get("rolloff", "0.35"),
            })
        entries.append({
            "frame_identity": {
                "capture_id": identity.get("capture_id"),
                "request_id": identity.get("request_id"),
                "iq_sha256": audit["iq_sha256"],
                "release_id": identity.get("release_id"),
                "connection_generation": identity.get("connection_generation"),
                "config_revision": identity.get("config_revision"),
            },
            "tx": tx_config,
            "_provenance": {
                "frame_index": plan.get("frame_index"),
                "label": plan.get("label"),
                "truth_source": audit["truth_source"],
                "actual_tx_payload_sha256": (audit["actual_tx"] or {}).get("payload_sha256"),
                "truth_payload_sha256": (audit["truth"] or {}).get("tx_payload_sha256"),
                "truth_file": str(truth_file),
                "plan_file": str(plan_file),
            },
        })
    document = {
        "schema": "ml-task68-tx-truth-v1",
        "truth_source": "independent_tx_config",
        "source": "new-seed plan/truth + validated actual TX load record + IQ SHA",
        "config": {"sample_rate_hz": 60_000_000, "symbol_rate_hz": 3_000_000,
                   "shaping": "RECT", "amplitude": 60, "rolloff": "0.35"},
        "frames": entries,
    }
    path = Path(out_dir) / "validated_truth_side.json"
    jwrite(path, document)
    return path


def load_models():
    manifest = load_json(MODEL_MANIFEST)
    baseline = manifest["baseline"]
    if sha_file(ROOT / baseline["path"]) != baseline["sha256"]:
        raise ValueError("locked baseline hash mismatch")
    predictors = {}
    locks = {}
    for name, model in manifest["models"].items():
        cnn_path = ROOT / model["cnn_path"]
        rules_path = ROOT / model["rules_path"]
        if not cnn_path.is_file() or sha_file(cnn_path) != model["cnn_sha256"]:
            raise ValueError("locked CNN hash mismatch: " + name)
        if not rules_path.is_file() or sha_file(rules_path) != model["rules_sha256"]:
            raise ValueError("locked rules hash mismatch: " + name)
        predictors[name] = Round2Predictor(rules_path=rules_path)
        locks[name] = {"cnn_path": model["cnn_path"], "cnn_sha256": model["cnn_sha256"],
                       "rules_path": model["rules_path"],
                       "rules_sha256": model["rules_sha256"]}
    return predictors, locks, manifest


def evaluate_models(predictors, records_by_key, valid_frames):
    per_frame = []
    for frame in valid_frames:
        audit = frame["audit"]
        identity = audit["identity"]
        key = (identity.get("capture_id"), audit["iq_sha256"])
        record = records_by_key.get(key)
        if record is None or record.get("status") != "OK":
            frame["audit"]["valid"] = False
            frame["audit"]["errors"].append("truth_import:" + str(
                (record or {}).get("invalid_reasons") or "missing_record"))
            continue
        iq = domain.load_iq(frame["iq_path"])["iq"]
        truth_class = record.get("truth_class")
        models = {}
        for name, predictor in predictors.items():
            decision = predictor.decide(iq)
            label = decision.get("label")
            accepted = label in CLASSES
            correct = (label == truth_class) if truth_class in CLASSES else (label == "UNKNOWN")
            models[name] = {
                "label": label,
                "accepted": accepted,
                "reason": decision.get("reason"),
                "correct": bool(correct),
                "misclassified": bool(truth_class in CLASSES and accepted and label != truth_class),
                "rejected": bool(not accepted),
                "scores": decision.get("scores"),
                "qam16_score": (decision.get("scores") or [None] * 4)[2],
                "qam64_score": (decision.get("scores") or [None] * 4)[3],
            }
        per_frame.append({
            "frame": frame["frame"],
            "label": (audit["plan_row"] or {}).get("label"),
            "frame_index": (audit["plan_row"] or {}).get("frame_index"),
            "truth_class": truth_class,
            "truth_source": audit["truth_source"],
            "iq_sha256": audit["iq_sha256"],
            "identity": audit["identity"],
            "input_domain": audit["input_domain"],
            "actual_tx": audit["actual_tx"],
            "board_modrec4": audit["board"],
            "models": models,
        })
    summary = {}
    for name in predictors:
        per_class = {}
        for label in CLASSES:
            rows = [r for r in per_frame if r["truth_class"] == label]
            per_class[label] = {
                "n": len(rows),
                "correct": sum(1 for r in rows if r["models"][name]["correct"]),
                "misclassified": sum(1 for r in rows if r["models"][name]["misclassified"]),
                "rejected": sum(1 for r in rows if r["models"][name]["rejected"]),
                "scores": [{"frame": r["frame"], "label": r["models"][name]["label"],
                            "qam16_score": r["models"][name]["qam16_score"],
                            "qam64_score": r["models"][name]["qam64_score"]}
                           for r in rows],
            }
        off = [r for r in per_frame if r["truth_class"] == "OFF"]
        summary[name] = {
            "per_class": per_class,
            "off": {
                "n": len(off),
                "false_accept": sum(1 for r in off if r["models"][name]["accepted"]),
                "correct_rejection": sum(1 for r in off if r["models"][name]["rejected"]),
            },
            "total_correct": sum(1 for r in per_frame if r["models"][name]["correct"]),
            "total_on": sum(1 for r in per_frame if r["truth_class"] in CLASSES),
        }
    return per_frame, summary


def write_markdown(path, doc):
    lines = ["# New-seed 10-frame frozen four-model comparison\n",
             "\nStatus: `%s`\n" % doc["status"],
             "\nLocked models are diagnostic comparison versions only; not approved for release.\n",
             "\n## Validation\n",
             "| frame | truth | valid | errors | actual TX | board label |\n",
             "|---|---|---|---|---|---|\n"]
    for frame in doc.get("frames", []):
        audit = frame.get("audit", {})
        lines.append("| %s | %s | %s | %s | %s | %s |\n" % (
            frame.get("frame"), audit.get("truth_class"), audit.get("valid"),
            ";".join(audit.get("errors") or []),
            (audit.get("actual_tx") or {}).get("payload_sha256", "")[:12],
            (audit.get("board") or {}).get("final_label")))
    lines.append("\n## Model summary\n")
    lines.append("| model | BPSK c/m/r | QPSK c/m/r | QAM16 c/m/r | QAM64 c/m/r | OFF false-accept/reject |\n")
    lines.append("|---|---|---|---|---|---|\n")
    for name, summary in doc.get("summary", {}).items():
        def fmt(label):
            row = summary["per_class"][label]
            return "%d/%d/%d" % (row["correct"], row["misclassified"], row["rejected"])
        off = summary["off"]
        lines.append("| %s | %s | %s | %s | %s | %d/%d |\n" % (
            name, fmt("BPSK"), fmt("QPSK"), fmt("QAM16"), fmt("QAM64"),
            off["false_accept"], off["correct_rejection"]))
    lines.append("\n## Per-frame model labels / scores\n")
    lines.append("| frame | truth | " + " | ".join(doc.get("model_names", [])) + " |\n")
    lines.append("|---|---|" + "---|" * len(doc.get("model_names", [])) + "\n")
    for frame in doc.get("valid_frames", []):
        cells = []
        for name in doc.get("model_names", []):
            m = frame["models"][name]
            cells.append("%s (q16 %.3f/q64 %.3f)" % (
                m["label"], m["qam16_score"] or 0.0, m["qam64_score"] or 0.0))
        lines.append("| %s | %s | %s |\n" % (frame["frame"], frame["truth_class"],
                                              " | ".join(cells)))
    lines.append("\n## Boundaries\n")
    lines.append("- New batch is independent diagnostic data, not TRAIN, not an acceptance result.\n")
    lines.append("- Truth only from plan/truth + validated actual TX load; model/RX labels are not truth.\n")
    lines.append("- Invalid frames are excluded from all model counts.\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def run(frames_root=DEFAULT_RAW, truth_file=DEFAULT_TRUTH, plan_file=DEFAULT_PLAN,
        out_dir=RESULTS):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = field_requirements()
    jwrite(out_dir / "field_requirements.json", fields)
    if not Path(frames_root).is_dir():
        doc = {"schema": "ml-fourclass-seed-validation-eval-v1", "status": "NO_FRAMES",
               "frames_root": str(frames_root), "truth_file": str(truth_file),
               "plan_file": str(plan_file), "frames": [], "summary": {},
               "field_requirements": fields,
               "note": "raw/ directory absent; no fabricated data and no waiting"}
        jwrite(out_dir / "seed_validation_result.json", doc)
        write_markdown(out_dir / "seed_validation_result.md", doc)
        return doc
    iq_paths = sorted(Path(frames_root).rglob("*.iq"))
    if not iq_paths:
        doc = {"schema": "ml-fourclass-seed-validation-eval-v1", "status": "NO_FRAMES",
               "frames_root": str(frames_root), "frames": [], "summary": {},
               "field_requirements": fields,
               "note": "no .iq files; no fabricated data and no waiting"}
        jwrite(out_dir / "seed_validation_result.json", doc)
        write_markdown(out_dir / "seed_validation_result.md", doc)
        return doc
    if not Path(truth_file).is_file() or not Path(plan_file).is_file():
        doc = {"schema": "ml-fourclass-seed-validation-eval-v1", "status": "NO_FRAMES",
               "frames_root": str(frames_root), "frames": [], "summary": {},
               "truth_file": str(truth_file), "plan_file": str(plan_file),
               "field_requirements": fields,
               "missing_required": [str(path) for path in (truth_file, plan_file)
                                     if not Path(path).is_file()],
               "note": "truth/plan missing; no truth is fabricated and no waiting"}
        jwrite(out_dir / "seed_validation_result.json", doc)
        write_markdown(out_dir / "seed_validation_result.md", doc)
        return doc
    plan, truth_doc, plan_rows, truth_by_index = load_plan_truth(plan_file, truth_file)
    frames = []
    for iq_path in iq_paths:
        meta, record, meta_path = read_frame_files(iq_path)
        label = iq_path.parent.name if iq_path.parent != Path(frames_root) else iq_path.stem
        plan_row = find_plan_row(plan_rows, label)
        audit = audit_frame(iq_path, meta, record, plan_row, truth_by_index, plan)
        frames.append({
            "frame": iq_path.stem,
            "iq_path": str(iq_path),
            "metadata_path": str(meta_path),
            "audit": audit,
        })
    valid_frames = [f for f in frames if f["audit"]["valid"]]
    if not valid_frames:
        doc = {"schema": "ml-fourclass-seed-validation-eval-v1",
               "status": "NO_VALID_FRAMES", "frames_root": str(frames_root),
               "truth_file": str(truth_file), "plan_file": str(plan_file),
               "frames": frames, "summary": {}, "field_requirements": fields,
               "note": "frames present but none passed identity/TX-load/input-domain validation"}
        jwrite(out_dir / "seed_validation_result.json", doc)
        write_markdown(out_dir / "seed_validation_result.md", doc)
        return doc
    truth_side = build_truth_side(valid_frames, out_dir, truth_file, plan_file)
    imported = parse_task68_frames(frames_root, truth_side,
                                   source_id="seed-validation", split="TEST")
    records_by_key = {}
    for record in imported.get("records", []):
        identity = record.get("frame_identity") or {}
        records_by_key[(identity.get("capture_id"), record.get("iq_sha256"))] = record
    predictors, locks, model_manifest = load_models()
    per_frame, summary = evaluate_models(predictors, records_by_key, valid_frames)
    invalid = [f for f in frames if not f["audit"]["valid"]]
    status = "COMPLETED" if not invalid else "PARTIAL"
    doc = {
        "schema": "ml-fourclass-seed-validation-eval-v1",
        "status": status,
        "frames_root": str(frames_root),
        "truth_file": str(truth_file),
        "plan_file": str(plan_file),
        "truth_side": str(truth_side),
        "truth_side_sha256": sha_file(truth_side),
        "model_manifest": str(MODEL_MANIFEST),
        "model_manifest_sha256": sha_file(MODEL_MANIFEST),
        "model_locks": locks,
        "release_status": "DIAGNOSTIC_COMPARISON_ONLY_NOT_APPROVED_FOR_RELEASE",
        "model_names": list(predictors),
        "frames": frames,
        "invalid_frames": invalid,
        "valid_frames": per_frame,
        "summary": summary,
        "field_requirements": fields,
        "boundaries": {
            "training": False, "threshold_changes": False, "device_access": False,
            "real_batch_added_to_training": False,
            "real_batch_is_acceptance": False,
            "truth_from_model_or_rx_expected_mode": False,
            "invalid_frames_counted": False,
        },
        "note": ("new-seed batch is independent diagnostic data only; locked models are "
                 "not approved for release; no tuning on this batch"),
    }
    jwrite(out_dir / "seed_validation_result.json", doc)
    write_markdown(out_dir / "seed_validation_result.md", doc)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--truth-file", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--plan-file", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--out-dir", type=Path, default=RESULTS)
    parser.add_argument("--print-fields", action="store_true")
    args = parser.parse_args(argv)
    if args.print_fields:
        print(json.dumps(field_requirements(), indent=1))
        return 0
    doc = run(args.frames_root, args.truth_file, args.plan_file, args.out_dir)
    print(json.dumps({
        "status": doc["status"],
        "frames": len(doc.get("frames", [])),
        "invalid": len(doc.get("invalid_frames", [])),
        "summary": {name: {"total_correct": row["total_correct"],
                           "total_on": row["total_on"],
                           "off": row["off"]}
                    for name, row in doc.get("summary", {}).items()},
        "out": str(Path(args.out_dir) / "seed_validation_result.json"),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
