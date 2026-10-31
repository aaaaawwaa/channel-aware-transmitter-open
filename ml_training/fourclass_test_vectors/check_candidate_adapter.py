#!/usr/bin/env python3
"""One read-only candidate-adapter check against the existing SYNTHETIC vectors.

Loads the supplied candidate release with its own released ``merged_release`` and
``ModRec4Adapter``, then evaluates the existing 60 MSPS / 4096-complex-sample
vectors.  It compares class, accept/reject, reason and input-domain behaviour
with the previously recorded frozen evaluation.  No model, threshold, candidate
or vector file is modified; the result is SYNTHETIC-only.

This is intentionally a single check script, not a reusable SDR framework.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
DEFAULT_CANDIDATE = (ROOT / "evidence" / "rf-teaching" /
                     "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" /
                     "v8" / "out" / "releases" /
                     "4adee8ecbdac03e13338b10be7278909")
EXPECTED_CANDIDATE_ID = "4adee8ecbdac03e13338b10be7278909"
DEFAULT_DATA = PACKAGE / "data"
DEFAULT_REFERENCE = PACKAGE / "results" / "evaluation.json"
DEFAULT_OUT = PACKAGE / "results" / "candidate_v8_4adee8ec"

SIXTY_CONTEXT = {
    "format": "s16_iq_le",
    "samples": 4096,
    "sample_rate_hz": 60_000_000,
    "decimation": 1,
    "symbol_rate_hz": 3_000_000,
    "shaping": "RECT",
}
SIX_CONTEXT = dict(SIXTY_CONTEXT, sample_rate_hz=6_000_000)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_candidate(candidate_dir):
    """Import the candidate's own release modules, never the working-tree copies."""
    candidate_dir = Path(candidate_dir).resolve()
    if not candidate_dir.is_dir():
        raise SystemExit("candidate release directory missing: %s" % candidate_dir)
    sys.path.insert(0, str(candidate_dir))
    merged_release = importlib.import_module("merged_release")
    manifest = merged_release.verify_release(candidate_dir, require_software=True)
    if manifest.get("release_id") != EXPECTED_CANDIDATE_ID:
        raise SystemExit("candidate release_id mismatch: expected %s got %s"
                         % (EXPECTED_CANDIDATE_ID, manifest.get("release_id")))
    adapter_mod = importlib.import_module("rf_modrec4_adapter")
    domain_mod = importlib.import_module("rf_modrec4_input_domain")
    artifact = manifest["modrec4_model"]
    adapter = adapter_mod.ModRec4Adapter.from_release(candidate_dir, artifact)
    return {
        "directory": candidate_dir,
        "manifest": manifest,
        "merged_release": merged_release,
        "adapter": adapter,
        "domain": domain_mod,
        "adapter_sha256": sha256_file(candidate_dir / "rf_modrec4_adapter.py"),
    }


def _read_rate(path):
    raw = Path(path).read_bytes()
    import numpy as np
    values = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).copy()
    return raw, values


def _decision_record(result):
    mod = result.get("modulation") or {}
    return {
        "status": result.get("status"),
        "input_domain": result.get("input_domain"),
        "input": result.get("input"),
        "final_label": mod.get("final_label"),
        "accepted": bool(mod.get("accepted")),
        "reason": mod.get("reason", ""),
        "final_label_source": mod.get("final_label_source"),
        "scores4": mod.get("scores4"),
        "cnn_scores4": mod.get("cnn_scores4"),
        "arbitration_applied": bool(mod.get("arbitration_applied")),
        "spectral": mod.get("spectral"),
        "pure_class_hint": result.get("pure_class_hint"),
    }


def check(candidate_dir=DEFAULT_CANDIDATE, data_dir=DEFAULT_DATA,
          reference_path=DEFAULT_REFERENCE, out_dir=DEFAULT_OUT):
    data_dir = Path(data_dir).resolve()
    out_dir = Path(out_dir).resolve()
    manifest = load_json(data_dir / "manifest.json")
    reference = load_json(reference_path)
    loaded = _load_candidate(candidate_dir)
    adapter = loaded["adapter"]
    candidate_manifest = loaded["manifest"]
    vector_rows = []
    comparison = {
        "class_mismatches": [],
        "acceptance_mismatches": [],
        "reason_mismatches": [],
        "input_domain_mismatches": [],
        "six_msps_mismatches": [],
        "numeric_deltas": [],
    }
    ref_by_truth = {row["truth_class"]: row for row in reference["predictions"]}
    for vector in manifest["vectors"]:
        truth = vector["truth_class"]
        _, sixty = _read_rate(data_dir / vector["rates"]["sixty_msps"]["path"])
        result = adapter.evaluate_iq(sixty, context=SIXTY_CONTEXT,
                                     frame_id=vector["id"])
        record = {"truth_class": truth, "vector_id": vector["id"],
                  "candidate": _decision_record(result)}
        vector_rows.append(record)
        ref = ref_by_truth.get(truth)
        if ref is None:
            comparison["class_mismatches"].append(
                {"truth": truth, "issue": "missing reference prediction"})
            continue
        candidate_mod = record["candidate"]
        candidate_domain = candidate_mod.get("input_domain") or {}
        reference_domain_ok = bool(ref.get("input_context_check", {}).get("ok")
                                   and ref.get("input_iq_check", {}).get("ok"))
        candidate_domain_ok = (candidate_mod.get("status") == "OK"
                               and candidate_domain.get("status") == "VALID"
                               and candidate_domain.get("reason") == "")
        if not candidate_domain_ok or candidate_domain_ok != reference_domain_ok:
            comparison["input_domain_mismatches"].append({
                "truth": truth,
                "candidate_domain": candidate_domain,
                "reference_domain_ok": reference_domain_ok,
                "candidate_domain_ok": candidate_domain_ok,
            })
        if candidate_mod["final_label"] != ref.get("final_label"):
            comparison["class_mismatches"].append({
                "truth": truth,
                "candidate": candidate_mod["final_label"],
                "reference": ref.get("final_label"),
            })
        if bool(candidate_mod["accepted"]) != bool(ref.get("accepted")):
            comparison["acceptance_mismatches"].append({
                "truth": truth,
                "candidate": candidate_mod["accepted"],
                "reference": ref.get("accepted"),
            })
        if (candidate_mod.get("reason") or "") != (ref.get("reason") or ""):
            comparison["reason_mismatches"].append({
                "truth": truth,
                "candidate": candidate_mod.get("reason"),
                "reference": ref.get("reason"),
            })
        candidate_scores = candidate_mod.get("scores4")
        reference_scores = ref.get("scores")
        if candidate_scores is not None and reference_scores is not None:
            if len(candidate_scores) != len(reference_scores):
                comparison["numeric_deltas"].append(
                    {"truth": truth, "issue": "score length differs"})
            else:
                delta = max(abs(float(a) - float(b))
                            for a, b in zip(candidate_scores, reference_scores))
                comparison["numeric_deltas"].append(
                    {"truth": truth, "max_abs_score_delta": delta})
        record["reference"] = {
            "final_label": ref.get("final_label"),
            "accepted": ref.get("accepted"),
            "reason": ref.get("reason"),
            "input_domain_ok": reference_domain_ok,
            "scores": reference_scores,
        }
        # The 6 MSPS representation remains outside the frozen 60 MSPS domain.
        _, six = _read_rate(data_dir / vector["rates"]["six_msps"]["path"])
        six_result = adapter.evaluate_iq(six, context=SIX_CONTEXT,
                                         frame_id=vector["id"] + "-6msps")
        six_record = _decision_record(six_result)
        reference_six = next(
            row for row in reference["six_msps_domain_checks"]
            if row["truth_class"] == truth)
        expected_reason = reference_six["context_check"].get("reason")
        if (six_record.get("status") != "UNAVAILABLE"
                or (six_record.get("input_domain") or {}).get("reason") != expected_reason):
            comparison["six_msps_mismatches"].append({
                "truth": truth,
                "candidate_status": six_record.get("status"),
                "candidate_reason": (six_record.get("input_domain") or {}).get("reason"),
                "reference_reason": expected_reason,
            })
        record["six_msps"] = {
            "fed_to_model": six_result.get("status") == "OK",
            "status": six_record.get("status"),
            "reason": (six_record.get("input_domain") or {}).get("reason"),
            "reference_reason": expected_reason,
        }
    expected_reference = {
        "candidate_id": EXPECTED_CANDIDATE_ID,
        "candidate_manifest_release_id": candidate_manifest.get("release_id"),
        "candidate_adapter_sha256": loaded["adapter_sha256"],
        "candidate_manifest_sha256": sha256_file(
            loaded["directory"] / "merged_release_manifest.json"),
        "candidate_modrec4_artifact": candidate_manifest.get("modrec4_model"),
        "reference_evaluation": str(reference_path),
        "reference_model": reference.get("model"),
        "model_artifact_matches_reference": {
            "cnn": (candidate_manifest.get("modrec4_model", {}).get("cnn_sha256")
                    == reference.get("model", {}).get("cnn_sha256")),
            "baseline": (candidate_manifest.get("modrec4_model", {}).get("baseline_sha256")
                         == reference.get("model", {}).get("baseline_sha256")),
            "rules": (candidate_manifest.get("modrec4_model", {}).get("rule_sha256")
                      == reference.get("model", {}).get("rule_config_sha256")),
        },
        "same_truth_source": "manifest truth_class; model prediction never used as truth",
        "synthetic": True,
    }
    status = "PASS" if not any(comparison[key] for key in (
        "class_mismatches", "acceptance_mismatches", "reason_mismatches",
        "input_domain_mismatches", "six_msps_mismatches")) else "DIFFERENCES"
    # Numeric deltas are reported, not silently rounded; class/reason checks are exact.
    doc = {
        "schema": "ml-fourclass-candidate-adapter-check-v1",
        "evidence_kind": "one_read_only_candidate_adapter_check",
        "synthetic": True,
        "real_capture": False,
        "real_accuracy_applicable": False,
        "product_qualification": False,
        "warning": ("SYNTHETIC offline check only; not real RF accuracy, not a product "
                    "qualification, and not an ARM/device result."),
        "candidate": expected_reference,
        "input": {
            "vectors_dir": str(data_dir),
            "sixty_msps_samples": 4096,
            "sixty_msps_context": SIXTY_CONTEXT,
            "six_msps_context": SIX_CONTEXT,
        },
        "rows": vector_rows,
        "comparison": comparison,
        "status": status,
        "status_note": ("PASS means class/acceptance/reason/input-domain behavior exactly "
                        "matches the recorded SYNTHETIC frozen evaluation; numeric deltas "
                        "are listed separately."),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "candidate_check.json"
    out_json.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    _write_md(out_dir / "candidate_check.md", doc)
    return {"status": status, "out": str(out_json),
            "candidate_id": candidate_manifest.get("release_id"),
            "comparison": comparison, "rows": vector_rows}


def _write_md(path, doc):
    lines = [
        "# 新候选 adapter 的 SYNTHETIC 只读对接检查\n",
        "> **SYNTHETIC**：不连接设备、不是真实 RF 准确率或产品资格；候选/向量/模型均未修改。\n",
        "\n## 候选身份\n",
        "- 期望候选 ID：`%s`\n" % doc["candidate"]["candidate_id"],
        "- manifest release_id：`%s`\n" % doc["candidate"]["candidate_manifest_release_id"],
        "- adapter SHA-256：`%s`\n" % doc["candidate"]["candidate_adapter_sha256"],
        "- manifest SHA-256：`%s`\n" % doc["candidate"]["candidate_manifest_sha256"],
        "- 模型 artifact 与参考一致：CNN=%s / baseline=%s / rules=%s\n" % (
            doc["candidate"]["model_artifact_matches_reference"]["cnn"],
            doc["candidate"]["model_artifact_matches_reference"]["baseline"],
            doc["candidate"]["model_artifact_matches_reference"]["rules"]),
        "- 冻结评估参考：`%s`\n" % doc["candidate"]["reference_evaluation"],
        "\n## 结果（60 MSPS / 4096 complex，trust 来自 manifest）\n",
        "| truth | candidate label | accepted | reason | reference label | reference accepted | reference reason | input domain |\n",
        "|---|---|---|---|---|---|---|---|\n",
    ]
    for row in doc["rows"]:
        c = row["candidate"]
        r = row["reference"]
        domain = c.get("input_domain") or {}
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s/%s |\n" % (
            row["truth_class"], c.get("final_label"), c.get("accepted"),
            c.get("reason", ""), r.get("final_label"), r.get("accepted"),
            r.get("reason", ""), domain.get("status"), domain.get("reason")))
    lines.append("\n## 6 MSPS 输入域（未喂模型）\n")
    for row in doc["rows"]:
        s = row["six_msps"]
        lines.append("- %s: status=%s reason=%s reference=%s fed_to_model=%s\n" % (
            row["truth_class"], s["status"], s["reason"], s["reference_reason"],
            s["fed_to_model"]))
    lines.append("\n## 与已有冻结评估比较\n")
    for key in ("class_mismatches", "acceptance_mismatches", "reason_mismatches",
                "input_domain_mismatches", "six_msps_mismatches"):
        lines.append("- %s: %d\n" % (key, len(doc["comparison"][key])))
    deltas = doc["comparison"].get("numeric_deltas", [])
    if deltas:
        lines.append("- score max-abs deltas: %s\n" % json.dumps(deltas))
    lines.append("\n状态：**%s**\n" % doc["status"])
    lines.append("\n## 边界\n")
    lines.append("- 本次只读调用候选 release 自带 `merged_release.verify_release` 与 "
                 "`ModRec4Adapter`；未安装、未连接板卡/Pluto、未重跑 ARM。\n")
    lines.append("- 6 MSPS 向量只用于输入域拒绝验证；未进入模型。\n")
    lines.append("- 结果不改变既有冻结评估，也不是产品资格或真实 RF 准确率。\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = check(args.candidate, args.data_dir, args.reference, args.out_dir)
    print(json.dumps({
        "status": result["status"],
        "candidate_id": result["candidate_id"],
        "out": result["out"],
        "comparison": result["comparison"],
        "rows": [{"truth": row["truth_class"],
                  "candidate": row["candidate"]["final_label"],
                  "accepted": row["candidate"]["accepted"],
                  "reason": row["candidate"]["reason"]}
                 for row in result["rows"]],
    }, indent=1))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
