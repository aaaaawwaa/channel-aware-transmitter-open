#!/usr/bin/env python3
"""Offline frozen-model check of the amplitude-60 software reference windows.

Truth comes from the payload manifest's declared class, never from the model.  The
existing frozen round-2 evaluation entry (``Round2Predictor``) is used unchanged;
a rejected or misclassified result is reported as-is.  No threshold, model or
amplitude is adjusted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[2]
DEFAULT_OUT = PACKAGE / "outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import domain as project_domain  # noqa: E402
from ml_training.real_campaign.evaluate import (  # noqa: E402
    FROZEN_BASELINE, FROZEN_CNN, FROZEN_RULES,
)
from ml_training.round2.predictor import CLASSES, Round2Predictor  # noqa: E402

CONTEXT = {
    "format": "s16_iq_le",
    "samples": 4096,
    "sample_rate_hz": 60_000_000,
    "decimation": 1,
    "symbol_rate_hz": 3_000_000,
    "shaping": "RECT",
}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def model_metadata():
    return {
        "frozen_evaluation_entry": "ml_training.real_campaign + Round2Predictor",
        "rules": str(FROZEN_RULES.relative_to(ROOT)).replace("\\", "/"),
        "rules_sha256": sha256_file(FROZEN_RULES),
        "cnn": str(FROZEN_CNN.relative_to(ROOT)).replace("\\", "/"),
        "cnn_sha256": sha256_file(FROZEN_CNN),
        "baseline": str(FROZEN_BASELINE.relative_to(ROOT)).replace("\\", "/"),
        "baseline_sha256": sha256_file(FROZEN_BASELINE),
        "classes": list(CLASSES),
    }


def evaluate(out_dir=DEFAULT_OUT):
    out_dir = Path(out_dir).resolve()
    manifest = load_json(out_dir / "manifest.json")
    predictor = Round2Predictor()
    result = {
        "schema": "ml-fourclass-hardware-amp60-frozen-eval-v1",
        "offline_generated_tx_payload": True,
        "real_capture": False,
        "real_accuracy_applicable": False,
        "statistical_ber_acceptance": False,
        "truth_from_manifest_not_model": True,
        "model": model_metadata(),
        "input_context": CONTEXT,
        "amplitude_codes": manifest["signal"]["amplitude"],
        "results": [],
        "summary": {},
        "status": "COMPLETED",
    }
    for vector in manifest["vectors"]:
        path = PACKAGE / vector["reference_4096"]["path"]
        context_check = project_domain.validate_context(CONTEXT)
        iq_check = project_domain.load_iq(path)
        row = {
            "truth_class": vector["truth_class"],
            "seed": vector["seed"],
            "reference_path": vector["reference_4096"]["path"],
            "reference_sha256": vector["reference_4096"]["sha256"],
            "input_context_check": context_check,
            "input_iq_check": {k: v for k, v in iq_check.items() if k != "iq"},
            "model_run": False,
        }
        if context_check["ok"] and iq_check["ok"]:
            decision = predictor.decide(iq_check["iq"])
            classification = predictor.classify(iq_check["iq"])
            row.update({
                "model_run": True,
                "final_label": decision.get("label", "UNKNOWN"),
                "accepted": decision.get("label") in CLASSES,
                "reason": decision.get("reason", ""),
                "scores": decision.get("scores"),
                "cnn_scores": decision.get("cnn_scores"),
                "score": decision.get("score"),
                "margin": decision.get("margin"),
                "rms": decision.get("rms"),
                "gate": decision.get("gate"),
                "cnn_class_label": classification.get("class_label"),
                "pure_class_hint": classification.get("class_label"),
                "classify_gate": classification.get("gate"),
                "arbitration_applied": classification.get("arbitration_applied"),
                "truth_match": decision.get("label") == vector["truth_class"],
                "not_tuned_note": ("reported as-is; amplitude 60 / thresholds / model were not "
                                   "adjusted"),
            })
        else:
            row["input_domain_reason"] = (context_check.get("reason")
                                          if not context_check["ok"] else iq_check.get("reason"))
        result["results"].append(row)
    evaluated = [r for r in result["results"] if r.get("model_run")]
    result["summary"] = {
        "synthetic_payloads": len(result["results"]),
        "model_evaluated": len(evaluated),
        "accepted": sum(1 for r in evaluated if r.get("accepted")),
        "rejected": sum(1 for r in evaluated if not r.get("accepted")),
        "correct": sum(1 for r in evaluated if r.get("truth_match")),
        "wrong": sum(1 for r in evaluated
                     if r.get("accepted") and not r.get("truth_match")),
        "reasons": {str(r.get("reason", "")): sum(
            1 for x in evaluated if str(x.get("reason", "")) == str(r.get("reason", "")))
            for r in evaluated},
        "note": ("Amplitude-60 offline payload check; results are reported without tuning. "
                 "This is not a real-RF accuracy or statistical BER result."),
    }
    out_json = out_dir / "frozen_eval.json"
    out_json.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _write_md(out_dir / "frozen_eval.md", result)
    return {"out": str(out_json), "summary": result["summary"]}


def _write_md(path, result):
    lines = [
        "# 幅度 60 实际 TX 载荷：冻结模型离线检查\n",
        "> **离线生成载荷**，不是真实 RF 接收；真值来自 manifest，预测未用于调参。"
        "不构成真实准确率或统计 BER 验收。\n",
        "\n## 输入\n",
        "- 60 MSPS / 3 MSym/s / RECT / 4096 complex reference window；amplitude=%s codes\n" % result["amplitude_codes"],
        "- 冻结入口：`%s`\n" % result["model"]["frozen_evaluation_entry"],
        "\n## 结果\n",
        "| truth | final label | accepted | reason | rms | truth match |\n|---|---|---|---|---:|---|\n",
    ]
    for row in result["results"]:
        lines.append("| %s | %s | %s | %s | %s | %s |\n" % (
            row["truth_class"], row.get("final_label", "UNKNOWN"),
            row.get("accepted"), row.get("reason", ""),
            None if row.get("rms") is None else round(row["rms"], 3),
            row.get("truth_match")))
    s = result["summary"]
    lines.append("\n模型评估 %d 帧：accepted %d、rejected %d、correct %d、wrong %d。\n" % (
        s["model_evaluated"], s["accepted"], s["rejected"], s["correct"], s["wrong"]))
    lines.append("\n## 误判/拒识（如有）\n")
    issues = [r for r in result["results"] if r.get("model_run") and not r.get("truth_match")]
    if issues:
        for row in issues:
            lines.append("- %s -> **%s**（accepted=%s, reason=%s, cnn_class=%s）\n" % (
                row["truth_class"], row.get("final_label"), row.get("accepted"),
                row.get("reason"), row.get("cnn_class_label")))
    else:
        lines.append("- 无：四类均与 truth 一致且 accepted。\n")
    lines.append("\n## 边界\n")
    lines.append("- 幅度固定 60；没有为通过而调 amplitude/模型/阈值。\n")
    lines.append("- 若 QAM16/QAM64 因能量门限被拒，保留真实拒识原因，不视为失败重试。\n")
    lines.append("- 仅离线生成载荷检查；真实 RF IQ 不要求与理想载荷字节相等。\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = evaluate(args.out_dir)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
