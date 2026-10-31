#!/usr/bin/env python3
"""Run the frozen round-2 evaluation entry on the 60 MSPS SYNTHETIC windows.

Frozen evaluation is the existing ``ml_training.real_campaign`` path
(``Round2Predictor``: frozen round-1 CNN + feature guard + spectral gate; fixed
artefact hashes).  Truth is the declared class in the vector manifest, never the
model output.  The 6 MSPS Pluto representation is explicitly out of the frozen
input domain and is **not** fed to the model.

Labels, rejection reasons, scores and input-domain checks are recorded as they
are; no model, threshold or vector is changed to improve the result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_DATA = PACKAGE / "data"
DEFAULT_RESULTS = PACKAGE / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import domain  # noqa: E402
from ml_training.real_campaign.evaluate import (  # noqa: E402
    FROZEN_BASELINE, FROZEN_CNN, FROZEN_RULES,
)
from ml_training.round2.predictor import CLASSES, Round2Predictor  # noqa: E402

SIXTY_CONTEXT = {
    "format": "s16_iq_le",
    "samples": 4096,
    "sample_rate_hz": 60_000_000,
    "decimation": 1,
    "symbol_rate_hz": 3_000_000,
    "shaping": "RECT",
}
SIX_CONTEXT = {
    "format": "s16_iq_le",
    "samples": 4096,
    "sample_rate_hz": 6_000_000,
    "decimation": 1,
    "symbol_rate_hz": 3_000_000,
    "shaping": "RECT",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _metadata():
    return {
        "frozen_evaluation_entry": "ml_training.real_campaign.evaluate + Round2Predictor",
        "frozen_round2_predictor": True,
        "rule_config": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "rule_config_sha256": sha256_file(FROZEN_RULES),
        "cnn": str(FROZEN_CNN.relative_to(ROOT)).replace("\\", "/"),
        "cnn_sha256": sha256_file(FROZEN_CNN),
        "baseline": str(FROZEN_BASELINE.relative_to(ROOT)).replace("\\", "/"),
        "baseline_sha256": sha256_file(FROZEN_BASELINE),
        "classes": list(CLASSES),
        "prediction_used_as_truth": False,
    }


def evaluate(data_dir=DEFAULT_DATA, results_dir=DEFAULT_RESULTS):
    data_dir = Path(data_dir).resolve()
    results_dir = Path(results_dir).resolve()
    manifest = load_json(data_dir / "manifest.json")
    predictor = Round2Predictor()
    result = {
        "schema": "ml-fourclass-test-vectors-evaluation-v1",
        "synthetic": True,
        "real_capture": False,
        "counts_as_real_capture": False,
        "real_accuracy_applicable": False,
        "product_qualification": False,
        "warning": ("SYNTHETIC offline vectors; not a real capture, real accuracy or product "
                    "qualification result. Predictions are recorded without tuning."),
        "model": _metadata(),
        "input_domains": {
            "sixty_msps": SIXTY_CONTEXT,
            "six_msps": SIX_CONTEXT,
            "domain_must_not_mix": True,
        },
        "predictions": [],
        "six_msps_domain_checks": [],
        "summary": {},
        "status": "FAIL",
    }
    for vector in manifest["vectors"]:
        mode = vector["truth_class"]
        sixty_path = data_dir / vector["rates"]["sixty_msps"]["path"]
        six_path = data_dir / vector["rates"]["six_msps"]["path"]
        context_check = domain.validate_context(SIXTY_CONTEXT)
        iq_check = domain.load_iq(sixty_path)
        row = {
            "id": vector["id"],
            "truth_class": mode,
            "synthetic": True,
            "sixty_msps_path": vector["rates"]["sixty_msps"]["path"],
            "input_context_check": context_check,
            "input_iq_check": {key: value for key, value in iq_check.items()
                               if key != "iq"},
        }
        if not context_check["ok"] or not iq_check["ok"]:
            row.update({"final_label": "UNKNOWN", "accepted": False,
                        "reason": "input_domain_not_evaluated",
                        "model_run": False})
        else:
            decision = predictor.decide(iq_check["iq"])
            row.update({
                "model_run": True,
                "final_label": decision.get("label", "UNKNOWN"),
                "accepted": decision.get("label") in CLASSES,
                "reason": decision.get("reason", ""),
                "score": decision.get("score"),
                "margin": decision.get("margin"),
                "cnn_scores": decision.get("cnn_scores"),
                "scores": decision.get("scores"),
                "arbitration_applied": bool(decision.get("arbitration_applied", False)),
                "arbitration": decision.get("arbitration"),
                "spectral": decision.get("spectral"),
                "gate": decision.get("gate"),
                "rms": decision.get("rms"),
                "correct": decision.get("label") == mode,
            })
            if decision.get("label") != mode:
                result.setdefault("model_errors", []).append({
                    "id": vector["id"],
                    "truth_class": mode,
                    "final_label": decision.get("label", "UNKNOWN"),
                    "reason": decision.get("reason", ""),
                    "accepted": decision.get("label") in CLASSES,
                    "note": "recorded as-is; no model/threshold/vector tuning",
                })
        result["predictions"].append(row)
        # 6 MSPS is intentionally outside the 60 MSPS frozen domain; demonstrate both
        # the declared-context rejection and the actual file-length rejection.
        six_domain = {
            "id": vector["id"],
            "truth_class": mode,
            "six_msps_path": vector["rates"]["six_msps"]["path"],
            "context_check": domain.validate_context(SIX_CONTEXT),
            "file_check": {key: value for key, value in domain.load_iq(six_path).items()
                           if key != "iq"},
            "fed_to_model": False,
            "note": ("6 MSPS is for a future Pluto/GNU Radio transmit interface; the frozen "
                     "model domain is 60 MSPS. Never resample-feed this as if it were a 60 MSPS "
                     "capture."),
        }
        result["six_msps_domain_checks"].append(six_domain)
    predictions = [p for p in result["predictions"] if p.get("model_run")]
    correct = sum(1 for p in predictions if p.get("correct"))
    accepted = sum(1 for p in predictions if p.get("accepted"))
    rejected = sum(1 for p in predictions if not p.get("accepted"))
    result["summary"] = {
        "synthetic_frames": len(predictions),
        "correct": correct,
        "wrong": len(predictions) - correct,
        "accepted": accepted,
        "rejected": rejected,
        "four_class_accuracy_synthetic_only": (correct / len(predictions)) if predictions else None,
        "real_accuracy_applicable": False,
        "model_errors": result.get("model_errors", []),
        "six_msps_files_checked": len(result["six_msps_domain_checks"]),
        "six_msps_all_fed_to_model": False,
        "note": ("Synthetic offline-only metrics; do not transfer to real capture counts, real "
                 "accuracy or product qualification. Misclassifications/rejections are reported "
                 "verbatim and were not tuned."),
    }
    result["status"] = ("PASS" if predictions and all(
        p.get("input_context_check", {}).get("ok")
        and p.get("input_iq_check", {}).get("ok") for p in result["predictions"])
        else "FAIL")
    results_dir.mkdir(parents=True, exist_ok=True)
    out_json = results_dir / "evaluation.json"
    out_json.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out_md = results_dir / "evaluation.md"
    _write_md(out_md, result)
    return {"status": result["status"], "out": str(out_json), "summary": result["summary"],
            "predictions": result["predictions"]}


def _write_md(path, result):
    lines = [
        "# 四类 SYNTHETIC 测试向量：冻结评估结果\n",
        "> **SYNTHETIC**：这些不是真实采集，不计入真实准确率/产品资格；预测未调参。\n",
        "\n## 冻结评估入口\n",
        "- `%s`\n" % result["model"]["frozen_evaluation_entry"],
        "- rules `%s` (`%s`)\n" % (
            result["model"]["rule_config"], result["model"]["rule_config_sha256"][:16] + "…"),
        "- CNN `%s`；baseline `%s`\n" % (
            result["model"]["cnn"], result["model"]["baseline"]),
        "- truth 来自 manifest；**未**用模型预测反填。\n",
        "\n## 60 MSPS 输入（冻结模型域）\n",
        "| truth | predicted | accepted | reason | correct | input-domain |\n"
        "|---|---|---|---|---:|---|\n",
    ]
    for row in result["predictions"]:
        domain_ok = bool(row.get("input_context_check", {}).get("ok")
                         and row.get("input_iq_check", {}).get("ok"))
        lines.append("| %s | %s | %s | %s | %s | %s |\n" % (
            row["truth_class"], row.get("final_label", "UNKNOWN"),
            row.get("accepted"), row.get("reason", ""), row.get("correct"),
            "OK" if domain_ok else "REJECTED"))
    lines.append("\nSynthetic summary: %d frames, correct %d, wrong %d, accepted %d, "
                 "rejected %d.  Four-class accuracy (synthetic only): %s. "
                 "**Not real accuracy.**\n" % (
                     result["summary"]["synthetic_frames"],
                     result["summary"]["correct"], result["summary"]["wrong"],
                     result["summary"]["accepted"], result["summary"]["rejected"],
                     result["summary"]["four_class_accuracy_synthetic_only"]))
    sixty_domain_ok = sum(
        1 for row in result["predictions"]
        if row.get("input_context_check", {}).get("ok")
        and row.get("input_iq_check", {}).get("ok"))
    lines.append("\n60 MSPS input-domain checks: %d/%d OK (context and exact 16,384-byte "
                 "s16_iq_le frame).\n" % (sixty_domain_ok, len(result["predictions"])))
    lines.append("\n## 6 MSPS 表示（未来 Pluto 接口，未喂模型）\n")
    for row in result["six_msps_domain_checks"]:
        lines.append("- %s: context `%s`; file `%s`; fed_to_model=%s\n" % (
            row["truth_class"], row["context_check"].get("reason"),
            row["file_check"].get("reason"), row["fed_to_model"]))
    lines.append("\n## 误判/拒识（如有）\n")
    errors = result.get("model_errors") or []
    if errors:
        for item in errors:
            lines.append("- %s truth=%s predicted=%s accepted=%s reason=%s\n" % (
                item["id"], item["truth_class"], item["final_label"],
                item["accepted"], item["reason"]))
    else:
        lines.append("- 无：4 个 60 MSPS 帧均预测为对应 truth 并被接受。\n")
    lines.append("\n## 边界\n")
    lines.append("- 未调模型/阈值/向量；预测仅作 SYNTHETIC 离线一致性检查。\n")
    lines.append("- 6 MSPS 与 60 MSPS 分别记录；禁止把 6 MSPS 文件直接当 60 MSPS 模型输入。\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    result = evaluate(args.data_dir, args.results_dir)
    print(json.dumps({"status": result["status"], "out": result["out"],
                      "summary": result["summary"]}, indent=1))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
