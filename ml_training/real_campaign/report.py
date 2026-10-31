"""Write index files and the real-campaign report (Markdown + JSON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent

NEXT_BATCH_FIELDS = [
    "schema / split",
    "session_id / session_index / seed",
    "release_id / software_contract / active_release",
    "boot_id (or explicitly NOT_DECLARED)",
    "operator / captured_utc",
    "route (board loopback or external SDR -> board RX)",
    "lo_hz / sample_rate_hz / rx_bandwidth_hz / tx_bandwidth_hz",
    "attenuation_mdB / gain settings / phy_mode_1r1t",
    "format=s16_iq_le / samples=4096 / decimation=1",
    "symbol_rate_hz=3000000 / shaping=RECT",
    "modulation class / mode / mapping / PRBS7 seed / differential flags",
    "tx source / tx amplitude / Pluto attenuation (for external SDR)",
    "truth_source (signal config or independent experiment record) + record id",
    "frame_id / capture_id / iq_sha256 / frame_count",
    "scenario (normal/off/narrowband/...) / physical flag",
    "spectral inversion or other RX path convention notes",
    "clipping/rail flags / peak/RMS diagnostics",
]


def _write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1), encoding="utf-8")


def _short_list(records, limit=8):
    return records[:limit]


def write_indexes(package, imported, grouping):
    records = imported["records"]
    index = {
        "schema": "ml-real-campaign-index-v1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": imported["counts"],
        "sources": sorted({record["source_id"] for record in records}),
        "records": records,
    }
    _write_json(package / "index" / "records.json", index)
    _write_json(package / "index" / "sessions.json", {
        "schema": "ml-real-campaign-sessions-v1",
        "session_count": grouping["session_count"],
        "sessions": grouping["sessions"],
    })
    _write_json(package / "index" / "duplicates.json", {
        "schema": "ml-real-campaign-duplicates-v1",
        "duplicate_group_count": grouping["duplicate_group_count"],
        "leakage_group_count": grouping["leakage_group_count"],
        "real_duplicate_group_count": grouping["real_duplicate_group_count"],
        "real_leakage_group_count": grouping["real_leakage_group_count"],
        "duplicate_groups": grouping["duplicate_groups"],
        "leakage_groups": grouping["leakage_groups"],
    })
    _write_json(package / "index" / "truth_policy.json", {
        "schema": "ml-real-campaign-truth-policy-v1",
        "allowed_truth_sources": ["explicit signal config", "independent experiment record",
                                  "synthetic generator (fixtures only)"],
        "forbidden_truth_sources": ["model prediction", "environment label"],
        "modulation_truth_fields": ["demod_result.submitted.config.mode",
                                    "scenario_declaration / trainval.truth (scenario only)"],
        "note": ("model_result and environment labels are imported only as non-truth "
                 "diagnostics; they never become a truth class"),
    })


def _fixture_checks(imported):
    checks = []
    for record in imported["synthetic_records"]:
        expected_status = record.get("expected_import_status")
        expected_reason = record.get("expected_reason")
        actual_status = record["status"]
        reasons = list(record.get("invalid_reasons") or [])
        if record.get("domain_reason"):
            reasons.append(record["domain_reason"])
        if expected_status == "INVALID":
            passed = actual_status == "INVALID" and expected_reason in reasons
        elif expected_status == "OK":
            passed = actual_status == "OK"
        else:
            passed = True
        checks.append({
            "frame_id": record["frame_id"],
            "expected_status": expected_status,
            "expected_reason": expected_reason,
            "actual_status": actual_status,
            "actual_reasons": sorted(set(reasons)),
            "pass": passed,
        })
    return {
        "checks": checks,
        "total": len(checks),
        "passed": len([check for check in checks if check["pass"]]),
        "status": "PASS" if checks and all(check["pass"] for check in checks) else
                  ("NOT_RUN" if not checks else "FAIL"),
    }


def _quality_markdown(quality):
    if quality.get("status") != "PASS":
        return "质量拒识：**NOT_RUN**（%s）\n" % quality.get("reason", "no frames")
    return ("质量拒识：%d/%d 拒识，拒识率 **%.3f**，误接受 %d；原因 %s\n"
            % (quality["rejected"], quality["total"], quality["rejection_rate"],
               quality["false_accept"], quality["reasons"]))


def write_report(package, config, imported, grouping, real_eval, synthetic_eval,
                 fixtures, fixture_checks):
    records = imported["records"]
    real = imported["real_records"]
    clean = real_eval["clean"]
    missing = clean["missing_classes"]
    overall = clean["four_class_overall_accuracy"]
    non_truth = [record for record in real
                 if record.get("non_truth_labels", {}).get("model_result_label")
                 not in (None, record.get("truth_class"))]
    lines = []
    lines.append("# 真实四类数据验收工具报告（真实数据现状）\n")
    lines.append("生成：%s\n" % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    lines.append("> 本报告由 `ml_training/real_campaign/run.py` 一键导入生成；原始 IQ 只读，"
                 "不复制、不修改。合成夹具单独标记并单独评估，不计入真实指标。\n")
    lines.append("## 1. 导入与可用数据\n")
    real_session_count = len([session for session in grouping["sessions"]
                              if not session["synthetic"]])
    synthetic_session_count = len([session for session in grouping["sessions"]
                                   if session["synthetic"]])
    lines.append("- 真实帧：%d（invalid %d）；合成夹具帧：%d。\n"
                 % (len(real), imported["counts"]["real_invalid"],
                    len(imported["synthetic_records"])))
    lines.append("- 真实会话：%d（合成夹具会话：%d）；clean %d / quality %d / stress %d。\n"
                 % (real_session_count, synthetic_session_count, clean["frames"],
                    real_eval["quality"].get("total", 0),
                    real_eval["stress"].get("total", 0)))
    lines.append("- 评估去重：保留每个 IQ digest 的首个记录；因重复副本排除 %d 帧。\n"
                 % real_eval.get("frames_deduplicated", 0))
    lines.append("\n### 真实会话明细\n")
    lines.append("| source | session | split | frames | unique_clean | quality | stress | release | boot | status |\n")
    lines.append("|---|---|---|---:|---:|---:|---:|---|---|---|\n")
    session_rows = {session["session_key"]: session for session in grouping["sessions"]}
    evaluated = {result["session_key"]: result for result in real_eval["per_session"]}
    for session in grouping["sessions"]:
        if session["synthetic"]:
            continue
        result = evaluated.get(session["session_key"])
        if result is not None:
            clean_frames = result["clean"]["frames"]
            quality_frames = result["quality"].get("total", 0)
            stress_frames = result["stress"].get("total", 0)
            status = "EVALUATED"
        else:
            clean_frames = 0
            quality_frames = 0
            stress_frames = 0
            status = "DUPLICATE_ONLY"
        lines.append("| %s | %s | %s | %d | %d | %d | %d | %s | %s | %s |\n" % (
            session["source_id"], session["session_id"], session["split"],
            session["frames"], clean_frames, quality_frames, stress_frames,
            ",".join(session["release_ids"]) or "-",
            session["boot_id"] or "NOT_DECLARED", status))
    duplicate_sessions = sorted({name for group in grouping["duplicate_groups"]
                                 if not all(record["synthetic"]
                                            for record in group["records"])
                                 for name in group["sessions"]})
    if duplicate_sessions:
        lines.append("\n重复采集会话（索引保留，指标按 digest 去重）：%s。\n"
                     % ", ".join(duplicate_sessions))
    lines.append("\n## 2. 真值来源与身份检查\n")
    lines.append("- 调制真值只来自显式信号配置或独立实验/场景记录；"
                 "`model_result`/环境标签仅作非真值诊断记录。\n")
    lines.append("- 被忽略的非真值标签中与真值不同的帧：%d（例如旧六类模型把真实 "
                 "BPSK 帧标为 QPSK，但没有被当作真值）。\n" % len(non_truth))
    lines.append("- 身份：release 按帧记录；boot_id 仅在独立声明存在时记录，"
                 "不补造；缺失明确标为 `NOT_DECLARED`。\n")
    lines.append("\n## 3. 干净信号分类（仅真实可用类别）\n")
    lines.append("- 可用真实类：%s；缺失真实类：**%s**。\n"
                 % (", ".join(clean["present_classes"]) or "none",
                    ", ".join(missing) or "none"))
    lines.append("- 四类总体准确率：**%s**%s。\n" % (
        overall["status"],
        ("" if overall["status"] == "PASS"
         else "（%s）" % overall.get("reason", ""))))
    for label in ("BPSK", "QPSK", "QAM16", "QAM64"):
        item = clean["per_class"].get(label, {})
        if item.get("total"):
            lines.append("- %s：%d/%d 正确，准确率 **%.3f**，拒识 %d，错分 %d。\n"
                         % (label, item["correct"], item["total"], item["accuracy"],
                            item["rejected"], item["wrong"]))
        else:
            lines.append("- %s：**NOT_RUN**（无真实数据）。\n" % label)
    lines.append("- 已知类误拒：%d/%d（%.3f）。\n"
                 % (clean["known_false_rejection"]["count"],
                    clean["known_false_rejection"]["total"],
                    clean["known_false_rejection"]["rate"]))
    lines.append("- DUPLICATE_ONLY 会话是重复下载副本，索引保留但不重复进入指标。\n")
    lines.append("\n### 每会话结果\n")
    lines.append("| source | session | clean帧 | 正确 | 拒识 | 四类总体 |\n")
    lines.append("|---|---|---:|---:|---:|---|\n")
    for result in real_eval["per_session"]:
        session_clean = result["clean"]
        overall_session = session_clean["four_class_overall_accuracy"]
        lines.append("| %s | %s | %d | %d | %d | %s |\n" % (
            result["source_id"], result["session_id"], session_clean["frames"],
            session_clean.get("accuracy", 0) * session_clean["frames"]
            if session_clean.get("accuracy") is not None else 0,
            session_clean["known_false_rejection"]["count"],
            overall_session.get("status", "NOT_RUN")))
    lines.append("\n## 4. 质量拒识与干扰压力（分开报告）\n")
    lines.append("- " + _quality_markdown(real_eval["quality"]))
    stress = real_eval["stress"]
    if stress.get("status") == "PASS":
        lines.append("- 干扰压力：%d 帧（窄带），接受 %d / 拒识 %d；**仅压力测试，"
                     "不计入干净四类准确率**。\n"
                     % (stress["total"], stress["accepted"], stress["rejected"]))
    else:
        lines.append("- 干扰压力：**NOT_RUN**（%s）。\n" % stress.get("reason", ""))
    lines.append("\n## 5. 重复 IQ 与 TRAIN/VAL/TEST 泄漏\n")
    lines.append("- 重复 IQ 组：%d（真实 %d）；跨 split 泄漏组：%d（真实 %d）。\n"
                 % (grouping["duplicate_group_count"],
                    grouping["real_duplicate_group_count"],
                    grouping["leakage_group_count"],
                    grouping["real_leakage_group_count"]))
    lines.append("- 真实跨 split 泄漏：**%s**；指标已按 IQ digest 去重，重复副本不重复计数。\n"
                 % ("PASS" if grouping["real_leakage_group_count"] == 0 else "FAIL"))
    lines.append("- 重复组明细见 `index/duplicates.json`；原始文件保留不变。\n")
    lines.append("\n## 6. 输入域与跳过\n")
    lines.append("- 导入域检查：%d/%d 真实帧域 OK。\n"
                 % (len([record for record in real if record["domain_status"] == "OK"]),
                    len(real)))
    if real_eval["frames_skipped"]:
        lines.append("- 评估跳过：%d。\n" % len(real_eval["frames_skipped"]))
    else:
        lines.append("- 评估跳过：0。\n")
    lines.append("\n## 7. 合成夹具（不是真实采集）\n")
    lines.append("- `synthetic=true`；来源为冻结四类生成器 `sim-smoke`，映射参考检查 "
                 "%s passed，夹具校验 %d/%d PASS。\n"
                 % (fixtures["source_dataset"]["reference_check_checks"],
                    fixture_checks["passed"], fixture_checks["total"]))
    if "synthetic_duplicate_group_count" in fixture_checks:
        lines.append("- 合成夹具重复 IQ 组：%d；跨 split 泄漏组：%d（用于直接测试检测逻辑）。\n"
                     % (fixture_checks["synthetic_duplicate_group_count"],
                        fixture_checks["synthetic_leakage_group_count"]))
    lines.append("- 合成夹具评估单独输出：clean %d 帧，四类总体 %s；不进入真实指标。\n"
                 % (synthetic_eval["clean"]["frames"],
                    synthetic_eval["clean"]["four_class_overall_accuracy"].get("status")))
    if fixture_checks["status"] != "PASS":
        lines.append("- **夹具校验：%s**。\n" % fixture_checks["status"])
    lines.append("\n## 8. 下一批实测数据最小字段清单\n")
    lines.append("详见 `docs/next_batch_fields.md`；核心字段：\n")
    lines.append("```text\n" + "\n".join(NEXT_BATCH_FIELDS) + "\n```\n")
    lines.append("\n## 9. 结论与限制\n")
    lines.append("- 已有真实 BPSK 数据可演示导入、去重、冻结模型评估和逐会话结果；"
                 "本次真实 clean BPSK %d/%d 正确，已知类误拒 %d/%d。\n"
                 % (clean["correct"], clean["frames"],
                    clean["known_false_rejection"]["count"],
                    clean["known_false_rejection"]["total"]))
    lines.append("- **QPSK/QAM16/QAM64 真实类 NOT_RUN**，因此不计算/不宣称四类总体准确率；"
                 "需下一批实测数据补齐。\n")
    lines.append("- 本任务未采集、未发射、未连设备、未改产品；采集路线与参数草案见 "
                 "`docs/collection_prep.md`，正式验收门限不在本工具设定。\n")
    markdown = "".join(lines)
    report_dir = package / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "real_campaign_report.md").write_text(markdown, encoding="utf-8")
    summary = {
        "schema": "ml-real-campaign-report-v1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_sources": [source["source_id"] for source in config["sources"]],
        "import_counts": imported["counts"],
        "grouping": {
            "session_count": grouping["session_count"],
            "duplicate_group_count": grouping["duplicate_group_count"],
            "leakage_group_count": grouping["leakage_group_count"],
            "real_duplicate_group_count": grouping["real_duplicate_group_count"],
            "real_leakage_group_count": grouping["real_leakage_group_count"],
        },
        "real_evaluation": real_eval,
        "synthetic_evaluation": synthetic_eval,
        "fixture_checks": fixture_checks,
        "fixtures": fixtures,
        "missing_real_classes": missing,
        "four_class_overall_accuracy": overall,
        "next_batch_fields": NEXT_BATCH_FIELDS,
        "status": ("PASS" if real_eval["status"] == "PASS"
                   and fixture_checks["status"] == "PASS"
                   and grouping["real_leakage_group_count"] == 0 else "FAIL"),
        "notes": [
            "real QPSK/QAM16/QAM64 are NOT_RUN; no overall four-class real accuracy",
            "synthetic fixtures are not real captures and are evaluated separately",
            "duplicate metrics are deduplicated by IQ sha256",
            "no acquisition, transmission, board or product code change",
        ],
    }
    _write_json(report_dir / "real_campaign_report.json", summary)
    return summary, markdown
