#!/usr/bin/env python3
"""task68 real four-class acceptance entry (read-only, no device/training).

Usage::

    python ml_training/real_campaign/task68.py \
        --frames-root evidence/.../task68/run \
        --truth-file evidence/.../task68/tx_truth.json \
        --out-dir ml_training/real_campaign/results/task68

With no frames/truth present it exits successfully with ``status=NO_FRAMES`` and
prints the required field contract; it does not fabricate data or wait.

Truth is imported only from the independent TX truth file and matched by frame
identity; RX expected_mode/model/environment labels are never used as truth.
ON classification and OFF rejection are reported separately; invalid frames are
excluded from both numerators and denominators.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.real_campaign import domain, evaluate  # noqa: E402
from ml_training.real_campaign.importer import (  # noqa: E402
    REASON_TRUTH_CONFIG_INCOMPLETE, REASON_TRUTH_FILE_MISSING,
    REASON_TRUTH_IDENTITY_MISMATCH, REASON_TRUTH_MISSING, REASON_TRUTH_NOT_INDEPENDENT,
    REASON_TRUTH_SCHEMA_INVALID,
    REASON_TRUTH_UNSUPPORTED, TASK68_TRUTH_SCHEMA, parse_task68_frames,
)
from ml_training.round2.predictor import CLASSES, Round2Predictor  # noqa: E402

DEFAULT_OUT = PACKAGE / "results" / "task68"
FIELD_REQUIREMENTS = PACKAGE / "docs" / "task68_truth_fields.md"
REASON_FIELDS = {
    REASON_TRUTH_FILE_MISSING: "independent TX truth file is missing",
    REASON_TRUTH_SCHEMA_INVALID: "independent TX truth file does not match the schema",
    REASON_TRUTH_MISSING: "no independent TX truth entry matches this frame identity",
    REASON_TRUTH_NOT_INDEPENDENT: "truth source is not independent (model/RX/environment)",
    REASON_TRUTH_CONFIG_INCOMPLETE: "TX truth lacks mode/seed/config fields",
    REASON_TRUTH_IDENTITY_MISMATCH: "TX truth frame identity does not match the frame",
    REASON_TRUTH_UNSUPPORTED: "TX truth mode is not a supported class/OFF",
}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _field_spec():
    return {
        "schema": TASK68_TRUTH_SCHEMA,
        "truth_file_required": True,
        "truth_source_required": "independent_tx_config",
        "frame_identity_fields_required": ["capture_id", "iq_sha256"],
        "tx_fields_required": ["mode", "seed", "sample_rate_hz", "symbol_rate_hz",
                               "shaping", "amplitude", "rolloff"],
        "mode_values": list(CLASSES) + ["OFF"],
        "rules": [
            "truth is tx.mode/seed/config matched to frame_identity; RX expected_mode is diagnostic only",
            "RX expected_mode may differ for non-BPSK TX (runner keeps BPSK demod config); it does not veto truth",
            "model_result/environment labels are never truth",
            "missing or unsafe independent truth => status UNEVALUABLE",
            "invalid IQ/OFF => status INVALID; not counted as a correct rejection",
            "ON classification and OFF rejection are reported separately",
        ],
        "doc": str(FIELD_REQUIREMENTS.relative_to(ROOT)).replace("\\", "/"),
    }


def _board_host_compare(record):
    host = record.get("evaluation") or {}
    board = ((record.get("board_original_results") or {}).get("modrec4_result")
             or {}).get("modulation") or {}
    if not board:
        return {"board_available": False, "host": host, "board": None,
                "differences": None,
                "reason": "metadata has no board modrec4 result"}
    out = {
        "board_available": True,
        "host": {
            "final_label": host.get("final_label"),
            "accepted": host.get("accepted"),
            "reason": host.get("reason"),
            "scores": host.get("scores"),
        },
        "board": {
            "final_label": board.get("final_label"),
            "accepted": board.get("accepted"),
            "reason": board.get("reason"),
            "scores4": board.get("scores4"),
            "logits4": board.get("logits4"),
        },
        "differences": {
            "class_match": host.get("final_label") == board.get("final_label"),
            "acceptance_match": bool(host.get("accepted")) == bool(board.get("accepted")),
            "reason_match": (host.get("reason") or "") == (board.get("reason") or ""),
            "scores_max_abs_delta": None,
            "host_logits_available": False,
            "board_logits_preserved": board.get("logits4") is not None,
        },
    }
    hs, bs = host.get("scores", host.get("cnn_scores")), board.get("scores4")
    if hs is not None and bs is not None and len(hs) == len(bs):
        out["differences"]["scores_max_abs_delta"] = max(
            abs(float(a) - float(b)) for a, b in zip(hs, bs))
    return out


def _row(record):
    host = record.get("evaluation") or {}
    tx = record.get("tx_truth") or {}
    return {
        "frame_id": record.get("frame_id"),
        "status": record.get("status"),
        "kind": record.get("kind"),
        "truth_class": record.get("truth_class"),
        "truth_source": record.get("truth_source"),
        "truth_evaluable": record.get("truth_evaluable"),
        "invalid_reasons": record.get("invalid_reasons"),
        "frame_identity": record.get("frame_identity"),
        "iq_sha256": record.get("iq_sha256"),
        "metadata_iq_sha256": record.get("metadata_iq_sha256"),
        "digest_match": record.get("digest_match"),
        "input_domain": {
            "context": record.get("context"),
            "check": record.get("context_check"),
            "status": record.get("domain_status"),
            "reason": record.get("domain_reason"),
            "observed": record.get("observed_input"),
        },
        "tx_truth": {
            "source": tx.get("source"),
            "mode": tx.get("mode"),
            "seed": tx.get("seed"),
            "config": tx.get("config"),
            "truth_file": tx.get("truth_file"),
            "truth_sha256": tx.get("truth_sha256"),
        } if tx else None,
        "host": {
            "final_label": host.get("final_label"),
            "accepted": host.get("accepted"),
            "reason": host.get("reason"),
            "scores": host.get("scores", host.get("cnn_scores")),
            "cnn_scores": host.get("cnn_scores"),
            "rms": host.get("rms"),
        } if host else None,
        "board_host": _board_host_compare(record) if record.get("evaluation") else {
            "board_available": bool((record.get("board_original_results") or {}).get(
                "modrec4_result")),
            "note": "host evaluation was not run for this frame",
        },
    }


def _write_fields(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = _field_spec()
    (out_dir / "field_requirements.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def _write_md(path, doc):
    lines = [
        "# task68 四类真实接收：冻结评估接入\n",
        "\n## 状态\n",
        "- `%s`\n" % doc["status"],
        "- ON clean / OFF quality 分开统计；invalid 帧不进入正确拒识。\n",
        "\n## 真值规则\n",
        "- 四类 truth 仅来自独立 TX `mode/seed/config` 与帧身份匹配；"
        "RX expected_mode 仅作诊断，不否决、不作真值。\n",
        "- 缺少独立真值 -> `UNEVALUABLE`；无效 IQ/OFF -> `INVALID`，不算正确拒识。\n",
        "\n## 逐帧\n",
        "| frame | status | kind | truth | TX mode/seed | host | reason | board match |\n",
        "|---|---|---|---|---|---|---|---|\n",
    ]
    for row in doc.get("frames", []):
        host = row.get("host") or {}
        tx = row.get("tx_truth") or {}
        bh = row.get("board_host") or {}
        diffs = bh.get("differences") or {}
        match = (diffs.get("class_match") if isinstance(diffs, dict) else None)
        lines.append("| %s | %s | %s | %s | %s/%s | %s | %s | %s |\n" % (
            row.get("frame_id"), row.get("status"), row.get("kind"),
            row.get("truth_class"), tx.get("mode"), tx.get("seed"),
            host.get("final_label"), host.get("reason"), match))
    lines.append("\n## 汇总\n")
    lines.append("```json\n%s\n```\n" % json.dumps(doc.get("summary") or {},
                                                    indent=1, sort_keys=True))
    lines.append("\n## 边界\n")
    lines.append("- 本入口只处理真实接收数据；不加 TRAIN、不调模型/阈值、不连接设备。\n")
    lines.append("- 不是四类总体准确率或统计 BER 验收；同类真实帧不足时保持 NOT_RUN。\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def run(frames_root=None, truth_file=None, out_dir=DEFAULT_OUT, source_id="task68",
        split="TEST"):
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    field_doc = _write_fields(out_dir)
    if not frames_root and not truth_file:
        doc = {"schema": "ml-task68-result-v1", "status": "NO_FRAMES",
               "frames_root": None, "truth_file": None,
               "frames": [], "summary": {}, "field_requirements": field_doc,
               "note": "no task68 frames/truth supplied; entry and field contract are ready"}
        (out_dir / "task68_result.json").write_text(
            json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        _write_md(out_dir / "task68_result.md", doc)
        return doc
    imported = parse_task68_frames(frames_root, truth_file, source_id=source_id,
                                   split=split)
    records = imported["records"]
    if not records:
        doc = {"schema": "ml-task68-result-v1", "status": "NO_FRAMES",
               "frames_root": str(frames_root), "truth_file": imported.get("truth_file"),
               "frames": [], "summary": {}, "field_requirements": field_doc,
               "import_error": imported.get("truth_error"),
               "note": "frames root / truth file absent or empty; no fabricated data"}
        (out_dir / "task68_result.json").write_text(
            json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        _write_md(out_dir / "task68_result.md", doc)
        return doc
    predictor = Round2Predictor()
    evaluated = evaluate.evaluate(records, predictor=predictor, synthetic=False)
    frames = [_row(record) for record in records]
    clean = evaluated.get("clean") or {}
    quality = evaluated.get("quality") or {}
    invalid = [r for r in records if r["status"] == "INVALID"]
    unevaluable = [r for r in records if r["status"] == "UNEVALUABLE"]
    quality_invalid = [r for r in invalid if r.get("kind") == "quality"]
    summary = {
        "records": len(records),
        "evaluated": evaluated.get("frames_evaluated", 0),
        "invalid": len(invalid),
        "unevaluable": len(unevaluable),
        "clean": {
            "valid": clean.get("frames", 0),
            "invalid": len([r for r in invalid if r.get("kind") == "clean"]),
            "counts_by_truth": clean.get("counts_by_class"),
            "present_classes": clean.get("present_classes"),
            "missing_classes": clean.get("missing_classes"),
            "correct": clean.get("correct"),
            "wrong": clean.get("wrong"),
            "accepted": clean.get("accepted"),
            "rejected": clean.get("rejected"),
            "four_class_overall_accuracy": clean.get("four_class_overall_accuracy"),
            "note": "ON/clean only; missing classes stay NOT_RUN",
        },
        "quality": {
            "valid": quality.get("total", 0),
            "invalid_excluded": len(quality_invalid),
            "rejected": quality.get("rejected"),
            "false_accept": quality.get("false_accept"),
            "reasons": quality.get("reasons"),
            "note": "OFF/quality only; INVALID frames are excluded and never counted as correct rejection",
        },
        "status": "COMPLETED" if not invalid and not unevaluable else "PARTIAL",
        "model": evaluated.get("predictor"),
        "truth_rule": ("independent TX mode/seed/config matched to frame identity; RX "
                       "expected_mode is diagnostic only and never truth"),
    }
    doc = {
        "schema": "ml-task68-result-v1",
        "status": summary["status"],
        "frames_root": str(Path(frames_root).resolve()),
        "truth_file": imported.get("truth_file"),
        "truth_sha256": imported.get("truth_sha256"),
        "field_requirements": field_doc,
        "frames": frames,
        "summary": summary,
        "evaluate_raw": {key: evaluated.get(key) for key in (
            "frames_evaluated", "frames_skipped", "clean", "quality", "stress")},
        "note": ("Task68 real four-class acceptance only; ON classification and OFF rejection "
                 "separate; invalid not counted as correct rejection; no training."),
    }
    (out_dir / "task68_result.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _write_md(out_dir / "task68_result.md", doc)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-root", type=Path, default=None)
    parser.add_argument("--truth-file", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source-id", default="task68")
    parser.add_argument("--split", default="TEST")
    parser.add_argument("--print-fields", action="store_true")
    args = parser.parse_args(argv)
    if args.print_fields:
        print(json.dumps(_field_spec(), indent=1))
        return 0
    doc = run(args.frames_root, args.truth_file, args.out_dir, args.source_id,
              args.split)
    print(json.dumps({"status": doc["status"],
                      "out": str(Path(args.out_dir).resolve() / "task68_result.json"),
                      "frames": len(doc.get("frames", [])),
                      "summary": doc.get("summary")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
