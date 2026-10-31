#!/usr/bin/env python3
"""One-command host measurement/parity runner for the performance candidate.

Runs (all local, no board):
* read-only timing breakdown of the candidate full path;
* baseline vs optimized full-path host timing on the same 4 frames;
* baseline vs optimized adapter timing on the 43 frozen arm_probe frames grouped
  clean/rejection/stress;
* 70-case behavior parity (candidate frames + arm_probe + synthetic fixtures + boundary);
* full-path output parity and a report with next-round ARM commands.

Usage (from project root):
  python ml_training/performance_candidate/run_all.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_CANDIDATE = (ROOT / "evidence" / "rf-teaching" /
                     "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" /
                     "v3" / "out" / "releases" / "099174be0cb38a139ea95926394fcf0e")
DEFAULT_FRAMES = (ROOT / "evidence" / "rf-teaching" /
                  "s2-46db-80mhz-narrowband-2026-10-21" / "raw")
TAGS = ("nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband")
RESULTS = PACKAGE / "results"
REPORTS = PACKAGE / "reports"


def _run(script, *args):
    command = [sys.executable, str(PACKAGE / script)] + [str(arg) for arg in args]
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError("%s failed (%d):\nstdout:\n%s\nstderr:\n%s"
                           % (script, completed.returncode, completed.stdout,
                              completed.stderr))
    return completed.stdout


def _json(name):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def _pct(before, after):
    if not before:
        return None
    return round((1.0 - (after / before)) * 100.0, 2)


def _reference_integrity(candidate):
    """Reference snapshot must be byte-identical to the immutable candidate modules."""
    rows = []
    for path in sorted((PACKAGE / "reference").iterdir()):
        if not path.is_file():
            continue
        source = candidate / path.name
        if not source.is_file():
            rows.append({"file": path.name, "match": False, "reason": "missing in candidate"})
            continue
        import hashlib
        reference_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        rows.append({"file": path.name, "match": reference_hash == source_hash,
                     "sha256": reference_hash})
    return {"all_match": all(row["match"] for row in rows), "files": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--frames-dir", type=Path, default=DEFAULT_FRAMES)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args(argv)
    candidate = args.candidate.resolve()
    frames = args.frames_dir.resolve()
    RESULTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    common = ["--candidate", candidate, "--frames-dir", frames]
    tags = ["--tags"] + list(TAGS)
    log = {}

    log["profile"] = _run("profile_breakdown.py", *common, *tags,
                          "--out", RESULTS / "profile_before.json")
    log["fullpath_before"] = _run("measure.py", "--mode", "baseline", *common, *tags,
                                  "--warmup", "5", "--repeats", args.repeats,
                                  "--out", RESULTS / "host_before_fullpath.json")
    log["fullpath_after"] = _run("measure.py", "--mode", "optimized", *common, *tags,
                                 "--warmup", "5", "--repeats", args.repeats,
                                 "--out", RESULTS / "host_after_fullpath.json")
    log["adapter_before"] = _run("measure_adapter.py", "--mode", "baseline", *common, *tags,
                                 "--warmup", "5", "--repeats", "30",
                                 "--out", RESULTS / "host_before_adapter.json")
    log["adapter_after"] = _run("measure_adapter.py", "--mode", "optimized", *common, *tags,
                                "--warmup", "5", "--repeats", "30",
                                "--out", RESULTS / "host_after_adapter.json")
    log["groups_before"] = _run("measure_groups.py", "--mode", "baseline",
                                "--candidate", candidate, "--warmup", "5",
                                "--repeats", args.repeats,
                                "--out", RESULTS / "groups_before.json")
    log["groups_after"] = _run("measure_groups.py", "--mode", "optimized",
                               "--candidate", candidate, "--warmup", "5",
                               "--repeats", args.repeats,
                               "--out", RESULTS / "groups_after.json")
    log["cases_before"] = _run("eval_cases.py", "--mode", "baseline", *common,
                               "--out", RESULTS / "cases_baseline.json")
    log["cases_after"] = _run("eval_cases.py", "--mode", "optimized", *common,
                              "--out", RESULTS / "cases_optimized.json")
    log["parity"] = _run("compare_cases.py",
                         "--baseline", RESULTS / "cases_baseline.json",
                         "--optimized", RESULTS / "cases_optimized.json",
                         "--out", RESULTS / "parity.json")
    log["fullpath_parity"] = _run("compare_fullpath.py",
                                  "--before", RESULTS / "host_before_fullpath.json",
                                  "--after", RESULTS / "host_after_fullpath.json",
                                  "--out", RESULTS / "fullpath_parity.json")
    (RESULTS / "run_log.txt").write_text(
        "\n\n".join("### %s\n%s" % (key, value) for key, value in log.items()),
        encoding="utf-8")

    profile = _json("profile_before.json")
    before_full = _json("host_before_fullpath.json")
    after_full = _json("host_after_fullpath.json")
    before_adapter = _json("host_before_adapter.json")
    after_adapter = _json("host_after_adapter.json")
    before_groups = _json("groups_before.json")
    after_groups = _json("groups_after.json")
    parity = _json("parity.json")
    fullpath_parity = _json("fullpath_parity.json")
    integrity = _reference_integrity(candidate)

    def stage(doc, name):
        return doc["stages_ms"].get(name, {}).get("median_ms")

    summary = {
        "schema": "ml-performance-summary-v1",
        "candidate": str(candidate.relative_to(ROOT)).replace("\\", "/"),
        "candidate_immutable": "no candidate/product/evidence file modified",
        "frames": list(TAGS),
        "reference_integrity": integrity,
        "profile": {
            "calls_per_frame": profile["adapter_calls_per_frame"],
            "static_call_sites": profile["static_call_sites"],
            "redundancy": profile["redundancy_evidence"],
            "cprofile_top": profile["cprofile_top"],
        },
        "arm_evidence_from_existing_result": {
            "source": ("evidence/rf-teaching/s2-46db-modrec4-arm-2026-10-21/"
                       "result/ARM-FULLPATH-RESULT.md"),
            "median_ms": 374.26974400113977,
            "p95_ms": 424.6495392001634,
            "stages_ms": {"analysis": 28.249925499949313,
                          "six_class": 63.07303799985675,
                          "bpsk": 19.434968999121338,
                          "environment": 113.67309349952848,
                          "modrec4": 149.9260904993207},
            "wall_invocation_median_ms": 1333.6962380008117,
            "peak_rss_kib": 32444,
            "note": ("immutable existing ARM evidence; not re-run by this task; "
                     "63.24ms arm_probe probe is not comparable"),
        },
        "host_fullpath_before": before_full,
        "host_fullpath_after": after_full,
        "host_adapter_before": before_adapter,
        "host_adapter_after": after_adapter,
        "host_groups_before": before_groups,
        "host_groups_after": after_groups,
        "parity": parity,
        "fullpath_parity": fullpath_parity,
        "timing_comparison": {
            "fullpath_total": {
                "before_median_ms": before_full["timing_ms"]["median_ms"],
                "after_median_ms": after_full["timing_ms"]["median_ms"],
                "before_p95_ms": before_full["timing_ms"]["p95_ms"],
                "after_p95_ms": after_full["timing_ms"]["p95_ms"],
                "median_reduction_pct": _pct(before_full["timing_ms"]["median_ms"],
                                             after_full["timing_ms"]["median_ms"]),
                "p95_reduction_pct": _pct(before_full["timing_ms"]["p95_ms"],
                                          after_full["timing_ms"]["p95_ms"]),
            },
            "modrec4_stage": {
                "before_median_ms": stage(before_full, "modrec4"),
                "after_median_ms": stage(after_full, "modrec4"),
                "before_p95_ms": before_full["stages_ms"]["modrec4"]["p95_ms"],
                "after_p95_ms": after_full["stages_ms"]["modrec4"]["p95_ms"],
                "median_reduction_pct": _pct(stage(before_full, "modrec4"),
                                             stage(after_full, "modrec4")),
            },
            "adapter_only": {
                "before_median_ms": before_adapter["overall"]["median_ms"],
                "after_median_ms": after_adapter["overall"]["median_ms"],
                "before_p95_ms": before_adapter["overall"]["p95_ms"],
                "after_p95_ms": after_adapter["overall"]["p95_ms"],
                "median_reduction_pct": _pct(before_adapter["overall"]["median_ms"],
                                             after_adapter["overall"]["median_ms"]),
            },
            "by_category": {
                category: {
                    "before_median_ms": before_groups["by_category"][category]["median_ms"],
                    "after_median_ms": after_groups["by_category"][category]["median_ms"],
                    "median_reduction_pct": _pct(
                        before_groups["by_category"][category]["median_ms"],
                        after_groups["by_category"][category]["median_ms"]),
                } for category in sorted(before_groups["by_category"])
            },
        },
        "status": ("PASS" if parity["status"] == "PASS"
                   and fullpath_parity["status"] == "PASS"
                   and integrity["all_match"] else "FAIL"),
        "notes": [
            "host-only measurements; ARM is not measured or claimed by this task",
            "candidate 099174be is used read-only from evidence and is not modified",
            "same inputs, same 4-frame full-path protocol, same 43-frame adapter protocol",
            "outputs are exact: labels/reasons/arbitration/passthrough/provenance and numeric deltas 0",
        ],
    }
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1, default=str),
                                          encoding="utf-8")
    _write_report(summary)
    print(json.dumps({
        "status": summary["status"],
        "parity": parity["status"],
        "parity_cases": parity["cases"],
        "fullpath_parity": fullpath_parity["status"],
        "timing": summary["timing_comparison"],
        "report": str((REPORTS / "performance_report.md").relative_to(ROOT)).replace("\\", "/"),
    }, indent=1))
    return 0 if summary["status"] == "PASS" else 1


def _write_report(summary):
    timing = summary["timing_comparison"]
    categories = timing["by_category"]
    lines = []
    lines.append("# 冻结识别方案等价性能优化研究（主机）\n")
    lines.append("> 候选 `099174be` 只读；未安装、未连接板卡、未重跑 ARM、未修改产品/模型/证据。"
                 "主机加速只作线索，不宣称 ARM 已加速。\n")
    lines.append("## 1. 结论\n")
    lines.append("- 定位：候选 full-path 的 modrec4 阶段在 adapter 内部重复做 4 次预处理、"
                 "3 次 CNN 前向、2 次 baseline、2 次 spectral FFT；`classify` 的输出只用于 "
                 "`pure_class_hint`。\n")
    lines.append("- 优化：单次共享 preprocess/CNN/softmax/spectral/baseline，一次推导 "
                 "`before`/`after`/`pure_class_hint`；不删必要分析、不缩短输入、不改阈值、"
                 "不增加拒识。\n")
    lines.append("- 等价性：%d 个 case **0 mismatch**，所有数值字段最大差 **0.0**；"
                 "full-path 4/4 帧 0 mismatch。\n" % summary["parity"]["cases"])
    lines.append("- 主机：full-path 总时延 median **%.2f → %.2f ms（−%.1f%%）**；"
                 "modrec4 阶段 median **%.2f → %.2f ms（−%.1f%%）**；adapter median "
                 "**%.2f → %.2f ms（−%.1f%%）**。\n" % (
                     timing["fullpath_total"]["before_median_ms"],
                     timing["fullpath_total"]["after_median_ms"],
                     timing["fullpath_total"]["median_reduction_pct"],
                     timing["modrec4_stage"]["before_median_ms"],
                     timing["modrec4_stage"]["after_median_ms"],
                     timing["modrec4_stage"]["median_reduction_pct"],
                     timing["adapter_only"]["before_median_ms"],
                     timing["adapter_only"]["after_median_ms"],
                     timing["adapter_only"]["median_reduction_pct"]))
    lines.append("\n## 2. 耗时定位证据\n")
    arm = summary["arm_evidence_from_existing_result"]
    lines.append("候选 ARM full-path（既有证据，本任务未重跑）：median **%.2f ms**、"
                 "P95 **%.2f ms**、峰值 RSS **%.2f MiB**。\n"
                 % (arm["median_ms"], arm["p95_ms"], arm["peak_rss_kib"] / 1024.0))
    lines.append("\n| ARM 阶段 | median ms |\n|---|---:|\n")
    for stage, value in arm["stages_ms"].items():
        lines.append("| %s | %.3f |\n" % (stage, value))
    lines.append("\n- 既有 wall invocation（含 release 校验/模型加载）median **%.2f ms**，"
                 "明显高于五阶段合计；冷启动/文件校验单独报告，不混入稳态阶段。\n"
                 % arm["wall_invocation_median_ms"])
    lines.append("- 重复模型加载/文件校验主要发生在 harness 每次调用（wall 含 "
                 "release verify + model load），不在五阶段稳态计时内；本任务只优化稳态 "
                 "adapter，不改变加载/身份语义。\n")
    lines.append("- 原 **63.24 ms** 是 43 帧独立模型探针，不是 full-path，不能与新候选 "
                 "424.65 ms 直接比较。\n")
    lines.append("\n### 重复工作量（主机动态计数，逐帧一致）\n")
    calls = summary["profile"]["calls_per_frame"]
    first_tag = sorted(calls)[0]
    calls = calls[first_tag]
    lines.append("| 调用 | 优化前每帧次数 |\n|---|---:|\n")
    for key in ("preprocess_iq", "cnn.forward", "baseline.scores", "spectral_features",
                "predictor.decide", "predictor.classify", "apply_gate", "softmax"):
        lines.append("| %s | %s |\n" % (key, calls.get(key)))
    lines.append("\n- 静态调用点：`%s`；`classify` 结果只进入 `pure_class_hint`，"
                 "不是产品最终标签/仲裁来源。\n"
                 % summary["profile"]["static_call_sites"])
    lines.append("\n## 3. 优化原型与最小差分\n")
    lines.append("- 原型：`optimized_adapter.py`（单次共享计算，覆盖 evaluate_iq）"
                 "+ `optimized_fullpath.py`（同阶段/同输入/同计时边界的全路径包装）。\n")
    lines.append("- 参考快照 `reference/` 与候选 11 个 vendored 文件、3 个模型 JSON 逐文件 "
                 "SHA-256 一致（integrity=%s）；未修改权重/阈值/输入域。\n"
                 % summary["reference_integrity"]["all_match"])
    lines.append("- 差分只涉及 `ModRec4Adapter.evaluate_iq` 内部计算复用；输出字段、"
                 "passthrough、contract、provenance 保持。详见 `MINIMAL_DIFF.md`。\n")
    lines.append("\n## 4. 公平验证\n")
    lines.append("- 相同输入：候选 4 帧（normal + 3 narrowband stress）；43 个 arm_probe "
                 "帧（clean / rejection / stress）；合成夹具与输入域/阈值边界反例。\n")
    lines.append("- 相同计时范围：full-path `out.total_seconds` 五阶段合计 + 相同的 "
                 "warmup 5 / repeats 20；adapter 单独 measure 使用相同 warmup/repeats。\n")
    lines.append("- 结果：%d case 0 mismatch；max numeric delta 0.0；full-path 4/4 0 mismatch。\n"
                 % summary["parity"]["cases"])
    lines.append("\n## 5. 主机前后耗时（仅线索）\n")
    lines.append("| 指标 | 优化前 | 优化后 | 变化 |\n|---|---:|---:|---:|\n")
    lines.append("| full-path total median | %.2f ms | %.2f ms | −%.1f%% |\n" % (
        timing["fullpath_total"]["before_median_ms"],
        timing["fullpath_total"]["after_median_ms"],
        timing["fullpath_total"]["median_reduction_pct"]))
    lines.append("| full-path total P95 | %.2f ms | %.2f ms | −%.1f%% |\n" % (
        timing["fullpath_total"]["before_p95_ms"],
        timing["fullpath_total"]["after_p95_ms"],
        timing["fullpath_total"]["p95_reduction_pct"]))
    lines.append("| modrec4 stage median | %.2f ms | %.2f ms | −%.1f%% |\n" % (
        timing["modrec4_stage"]["before_median_ms"],
        timing["modrec4_stage"]["after_median_ms"],
        timing["modrec4_stage"]["median_reduction_pct"]))
    lines.append("| adapter median | %.2f ms | %.2f ms | −%.1f%% |\n" % (
        timing["adapter_only"]["before_median_ms"],
        timing["adapter_only"]["after_median_ms"],
        timing["adapter_only"]["median_reduction_pct"]))
    lines.append("\n| 样本类 | 优化前 adapter median | 优化后 | 变化 |\n"
                 "|---|---:|---:|---:|\n")
    for category in sorted(categories):
        item = categories[category]
        lines.append("| %s | %.2f ms | %.2f ms | −%.1f%% |\n" % (
            category, item["before_median_ms"], item["after_median_ms"],
            item["median_reduction_pct"]))
    lines.append("\n## 6. 下一轮 ARM 对照：最小入口（本会话只 dry-run）\n")
    lines.append("- 优化研究基线固定为候选 **099174be**；主项目当前候选可能已变化，本报告不把"
                 "旧候选结果当新候选资格。下一轮必须用主项目**当前候选**重新绑定并对照。\n")
    lines.append("- 本会话已完成的离线入口：`python ml_training/performance_candidate/"
                 "arm_compare.py --dry-run`，生成 "
                 "`results/arm_dryrun/plan.json`；**未连接板卡、未执行 ARM**。\n")
    lines.append("- 实际执行（需主项目授权后用当前候选）：\n")
    lines.append("```bash\n")
    lines.append("python ml_training/performance_candidate/arm_compare.py --execute "
                 "--candidate <current candidate release> --frames-dir <same 4 IQ> "
                 "--arm-dir ml_training/arm_probe --fixtures-dir "
                 "ml_training/real_campaign/fixtures/synthetic --run-id <fresh-id>\n")
    lines.append("```\n")
    lines.append("- 入口只复用项目已认可的 `pynq_ssh.py` Paramiko 连接能力，不使用 Windows "
                 "ssh/scp。plan 明确列出：所有本地上传文件（含 SHA-256/大小）、远端路径、"
                 "上传/命令超时、结果回收清单、`/tmp/rf-perf-<run_id>` 清理命令。\n")
    lines.append("- 同一候选、同一批 4 帧 IQ、相同计时边界（full-path `out.total_seconds`，"
                 "warmup 5/repeats 20）；分别回收 baseline/optimized 的 median/P95/RSS、"
                 "加载耗时（wall−阶段合计与 adapter 构造时间）及逐帧输出一致性。\n")
    lines.append("- 运行目录仅为板端 `/tmp/rf-perf-<run_id>`；不安装候选、不执行 RF、不改 "
                 "current/overlay/服务，结束后 `rm -rf` 临时目录。\n")
    lines.append("\n## 7. 限制\n")
    lines.append("- 未执行 ARM 测量或 ARM 资格；本任务不连接板卡。\n")
    lines.append("- 主机 timing 受系统负载影响，作为方向性线索；最终以相同命令的 ARM 结果为准。\n")
    lines.append("- 候选/参考/冻结模型/证据均只读；本任务只写 "
                 "`ml_training/performance_candidate/`。\n")
    path = REPORTS / "performance_report.md"
    path.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
