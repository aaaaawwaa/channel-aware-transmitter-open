#!/usr/bin/env python3
"""Minimal ARM comparison entry for the performance prototype (offline dry-run by default).

Reuses the project-sanctioned Paramiko helper
``~/.claude/skills/ac820-pynq/scripts/pynq_ssh.py``; it never invokes Windows
``ssh``/``scp``.  One candidate, one IQ set, one timing boundary.  The script is
intentionally task-specific, not a general framework.

* Default: ``--dry-run`` -- validate local inputs, expand file list/paths/timeouts and write
  ``results/arm_dryrun/plan.json``.  It does not import Paramiko and does not connect.
* ``--execute`` -- only for the next authorized ARM round; upload to a fresh board
  ``/tmp/rf-perf-<run_id>``, run baseline/optimized measurements and parity checks with
  fixed timeouts, retrieve result JSONs, summarize, then remove the temporary directory.

The optimization study baseline is candidate 099174be.  If the candidate supplied here has
a different release id/adaptor hash, the plan records ``candidate_changed=true``; results are
for that supplied candidate only and must not be reported as a qualified new candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import posixpath
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
STUDY_CANDIDATE = "099174be0cb38a139ea95926394fcf0e"
DEFAULT_CANDIDATE = (ROOT / "evidence" / "rf-teaching" /
                     "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" /
                     "v3" / "out" / "releases" / STUDY_CANDIDATE)
DEFAULT_FRAMES = (ROOT / "evidence" / "rf-teaching" /
                  "s2-46db-80mhz-narrowband-2026-10-21" / "raw")
DEFAULT_ARM = ROOT / "ml_training" / "arm_probe"
DEFAULT_FIXTURES = ROOT / "ml_training" / "real_campaign" / "fixtures" / "synthetic"
TAGS = ("nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband")
PROTOTYPE_FILES = (
    "optimized_adapter.py", "optimized_fullpath.py", "measure.py", "measure_adapter.py",
    "measure_groups.py", "eval_cases.py", "compare_cases.py", "compare_fullpath.py",
)
REFERENCE_DIR = PACKAGE / "reference"
RESULT_FILES = (
    "arm_before_fullpath.json", "arm_after_fullpath.json",
    "arm_before_adapter.json", "arm_after_adapter.json",
    "arm_cases_baseline.json", "arm_cases_optimized.json", "arm_cases_parity.json",
    "arm_groups_before.json", "arm_groups_after.json", "arm_fullpath_parity.json",
)
COMMAND_TIMEOUTS = {
    "measure_fullpath": 900,
    "measure_adapter": 600,
    "measure_groups": 600,
    "eval_cases": 600,
    "compare": 120,
    "cleanup": 60,
}
SFTP_TIMEOUT = 300


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(path, base=ROOT):
    return str(Path(path).resolve().relative_to(Path(base).resolve())).replace("\\", "/")


def _load_candidate(candidate):
    candidate = Path(candidate).resolve()
    manifest_path = candidate / "merged_release_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("candidate manifest missing: %s" % manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release_id = manifest.get("release_id")
    adapter = candidate / "rf_modrec4_adapter.py"
    if not adapter.is_file():
        raise SystemExit("candidate rf_modrec4_adapter.py missing: %s" % adapter)
    return {
        "path": str(candidate),
        "release_id": release_id,
        "adapter_sha256": _sha256(adapter),
        "manifest_sha256": _sha256(manifest_path),
        "study_candidate": STUDY_CANDIDATE,
        "candidate_changed": release_id != STUDY_CANDIDATE,
        "adapter_matches_study": _sha256(adapter) == _sha256(REFERENCE_DIR / "rf_modrec4_adapter.py"),
    }


def _upload_group(local_root, remote_root, files):
    rows = []
    for path in sorted(files):
        path = Path(path)
        if not path.is_file():
            raise SystemExit("missing upload file: %s" % path)
        remote = posixpath.join(remote_root, _rel(path, local_root))
        rows.append({"local": str(path.resolve()), "remote": remote,
                     "size": path.stat().st_size, "sha256": _sha256(path)})
    return rows


def build_plan(args):
    candidate = Path(args.candidate).resolve()
    frames = Path(args.frames_dir).resolve()
    arm = Path(args.arm_dir).resolve()
    fixtures = Path(args.fixtures_dir).resolve()
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    remote_root = "/tmp/rf-perf-%s" % run_id
    plan = {
        "schema": "ml-performance-arm-compare-plan-v1",
        "run_id": run_id,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "optimization_study_baseline": STUDY_CANDIDATE,
        "candidate": _load_candidate(candidate),
        "local_paths": {
            "candidate": str(candidate),
            "frames_dir": str(frames),
            "arm_probe": str(arm),
            "fixtures": str(fixtures),
            "prototype": str(PACKAGE),
        },
        "remote_paths": {
            "root": remote_root,
            "release": posixpath.join(remote_root, "release"),
            "frames": posixpath.join(remote_root, "frames"),
            "performance_candidate": posixpath.join(
                remote_root, "ml_training", "performance_candidate"),
            "arm_probe": posixpath.join(remote_root, "ml_training", "arm_probe"),
            "fixtures": posixpath.join(
                remote_root, "ml_training", "real_campaign", "fixtures", "synthetic"),
        },
        "tags": list(TAGS),
        "timeouts": {
            "connect_seconds": 15,
            "sftp_seconds": SFTP_TIMEOUT,
            **COMMAND_TIMEOUTS,
        },
        "cleanup": "rm -rf %s" % remote_root,
        "no_install_no_rf": True,
        "note": ("dry-run only until --execute; --execute uses the sanctioned pynq_ssh "
                 "helper, no Windows ssh/scp"),
    }
    # File lists, explicit and hashed.
    release_files = [path for path in candidate.rglob("*") if path.is_file()]
    prototype_files = [PACKAGE / name for name in PROTOTYPE_FILES]
    prototype_files += [path for path in REFERENCE_DIR.rglob("*") if path.is_file()]
    frame_files = []
    for tag in TAGS:
        for suffix in (".iq", ".json", ".environment.json"):
            path = frames / (tag + suffix)
            if not path.is_file():
                raise SystemExit("frame input missing: %s" % path)
            frame_files.append(path)
    arm_manifest = arm / "input_manifest.json"
    if not arm_manifest.is_file():
        raise SystemExit("arm_probe input_manifest missing: %s" % arm_manifest)
    arm_files = [arm_manifest]
    arm_manifest_doc = json.loads(arm_manifest.read_text(encoding="utf-8"))
    for entry in arm_manifest_doc["frames"]:
        path = arm / entry["path"]
        if not path.is_file():
            raise SystemExit("arm_probe frame missing: %s" % path)
        arm_files.append(path)
    fixture_files = [path for path in fixtures.rglob("*") if path.is_file()
                     and path.suffix in (".iq", ".json")]
    plan["uploads"] = {
        "release": _upload_group(candidate, plan["remote_paths"]["release"],
                                 release_files),
        "prototype": _upload_group(PACKAGE, plan["remote_paths"]["performance_candidate"],
                                   prototype_files),
        "frames": _upload_group(frames, plan["remote_paths"]["frames"], frame_files),
        "arm_probe": _upload_group(arm, plan["remote_paths"]["arm_probe"], arm_files),
        "fixtures": _upload_group(fixtures, plan["remote_paths"]["fixtures"],
                                  fixture_files),
    }
    rp = plan["remote_paths"]
    py = "PYTHONDONTWRITEBYTECODE=1 python3 -B"
    common = "--candidate %s --frames-dir %s" % (rp["release"], rp["frames"])
    tags = " ".join(TAGS)
    plan["commands"] = [
        {"name": "baseline_fullpath", "timeout_s": COMMAND_TIMEOUTS["measure_fullpath"],
         "command": "cd %s && %s measure.py --mode baseline %s --tags %s --warmup 5 "
                    "--repeats 20 --out arm_before_fullpath.json"
                    % (rp["performance_candidate"], py, common, tags)},
        {"name": "optimized_fullpath", "timeout_s": COMMAND_TIMEOUTS["measure_fullpath"],
         "command": "cd %s && %s measure.py --mode optimized %s --tags %s --warmup 5 "
                    "--repeats 20 --out arm_after_fullpath.json"
                    % (rp["performance_candidate"], py, common, tags)},
        {"name": "fullpath_compare", "timeout_s": COMMAND_TIMEOUTS["compare"],
         "command": "cd %s && %s compare_fullpath.py --before arm_before_fullpath.json "
                    "--after arm_after_fullpath.json --out arm_fullpath_parity.json"
                    % (rp["performance_candidate"], py)},
        {"name": "baseline_adapter", "timeout_s": COMMAND_TIMEOUTS["measure_adapter"],
         "command": "cd %s && %s measure_adapter.py --mode baseline %s --tags %s "
                    "--warmup 5 --repeats 30 --out arm_before_adapter.json"
                    % (rp["performance_candidate"], py, common, tags)},
        {"name": "optimized_adapter", "timeout_s": COMMAND_TIMEOUTS["measure_adapter"],
         "command": "cd %s && %s measure_adapter.py --mode optimized %s --tags %s "
                    "--warmup 5 --repeats 30 --out arm_after_adapter.json"
                    % (rp["performance_candidate"], py, common, tags)},
        {"name": "cases_baseline", "timeout_s": COMMAND_TIMEOUTS["eval_cases"],
         "command": "cd %s && %s eval_cases.py --mode baseline %s --arm-dir %s "
                    "--fixtures-dir %s --out arm_cases_baseline.json"
                    % (rp["performance_candidate"], py, common, rp["arm_probe"],
                       rp["fixtures"])},
        {"name": "cases_optimized", "timeout_s": COMMAND_TIMEOUTS["eval_cases"],
         "command": "cd %s && %s eval_cases.py --mode optimized %s --arm-dir %s "
                    "--fixtures-dir %s --out arm_cases_optimized.json"
                    % (rp["performance_candidate"], py, common, rp["arm_probe"],
                       rp["fixtures"])},
        {"name": "cases_compare", "timeout_s": COMMAND_TIMEOUTS["compare"],
         "command": "cd %s && %s compare_cases.py --baseline arm_cases_baseline.json "
                    "--optimized arm_cases_optimized.json --out arm_cases_parity.json"
                    % (rp["performance_candidate"], py)},
        {"name": "groups_baseline", "timeout_s": COMMAND_TIMEOUTS["measure_groups"],
         "command": "cd %s && %s measure_groups.py --mode baseline --candidate %s "
                    "--arm-dir %s --warmup 5 --repeats 10 --out arm_groups_before.json"
                    % (rp["performance_candidate"], py, rp["release"], rp["arm_probe"])},
        {"name": "groups_optimized", "timeout_s": COMMAND_TIMEOUTS["measure_groups"],
         "command": "cd %s && %s measure_groups.py --mode optimized --candidate %s "
                    "--arm-dir %s --warmup 5 --repeats 10 --out arm_groups_after.json"
                    % (rp["performance_candidate"], py, rp["release"], rp["arm_probe"])},
    ]
    plan["result_files"] = list(RESULT_FILES)
    return plan


def write_plan(plan):
    out = PACKAGE / "results" / "arm_dryrun" / "plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    return out


def _load_helper():
    candidates = []
    env = os.environ.get("PYNQ_SSH_HELPER")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".claude/skills/ac820-pynq/scripts/pynq_ssh.py")
    profile = os.environ.get("USERPROFILE")
    if profile:
        candidates.append(Path(profile) / ".claude/skills/ac820-pynq/scripts/pynq_ssh.py")
    candidates.append(Path("<LOCAL_PATH>"))
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("boardhelper", path)
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            return helper
    raise SystemExit("sanctioned pynq_ssh.py helper not found in: %s" % candidates)


def _mkdirs(sftp, remote_dir):
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def _upload(client, plan):
    sftp = client.open_sftp()
    try:
        sftp.get_channel().settimeout(SFTP_TIMEOUT)
        for group in plan["uploads"].values():
            for row in group:
                _mkdirs(sftp, posixpath.dirname(row["remote"]))
                sftp.put(row["local"], row["remote"], confirm=True)
    finally:
        sftp.close()


def _run_command(client, command, timeout):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def _download(client, plan, local_dir):
    sftp = client.open_sftp()
    try:
        for name in plan["result_files"]:
            remote = posixpath.join(plan["remote_paths"]["performance_candidate"], name)
            local = Path(local_dir) / name
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                sftp.get(remote, str(local))
            except IOError as exc:
                raise SystemExit("result file missing on board: %s (%s)" % (remote, exc))
    finally:
        sftp.close()


def _summary(results_dir, plan):
    def load(name):
        return json.loads((Path(results_dir) / name).read_text(encoding="utf-8"))

    before = load("arm_before_fullpath.json")
    after = load("arm_after_fullpath.json")
    adapter_before = load("arm_before_adapter.json")
    adapter_after = load("arm_after_adapter.json")
    cases = load("arm_cases_parity.json")
    full = load("arm_fullpath_parity.json")
    groups_before = load("arm_groups_before.json")
    groups_after = load("arm_groups_after.json")
    summary = {
        "schema": "ml-performance-arm-compare-summary-v1",
        "run_id": plan["run_id"],
        "optimization_study_baseline": STUDY_CANDIDATE,
        "candidate": plan["candidate"],
        "same_candidate": before.get("release_id") == after.get("release_id")
        and before.get("release_id") == plan["candidate"]["release_id"],
        "same_inputs": before.get("protocol", {}).get("frames") == list(TAGS)
        and after.get("protocol", {}).get("frames") == list(TAGS),
        "timing_boundary": "full-path out.total_seconds; warmup 5, repeats 20",
        "fullpath": {
            "before": before.get("timing_ms"), "after": after.get("timing_ms"),
            "before_wall": before.get("wall_invocation_ms"),
            "after_wall": after.get("wall_invocation_ms"),
            "before_stages": before.get("stages_ms"),
            "after_stages": after.get("stages_ms"),
            "before_rss_kb": before.get("rss_kb"),
            "after_rss_kb": after.get("rss_kb"),
            "parity": {"status": full.get("status"),
                       "mismatches": full.get("mismatch_count")},
        },
        "adapter": {
            "before": adapter_before,
            "after": adapter_after,
            "construct_ms_before": adapter_before.get("adapter_construct_ms"),
            "construct_ms_after": adapter_after.get("adapter_construct_ms"),
        },
        "behavior_parity": {"status": cases.get("status"),
                            "cases": cases.get("cases"),
                            "mismatches": cases.get("mismatch_count")},
        "groups": {"before": groups_before.get("by_category"),
                   "after": groups_after.get("by_category")},
        "load_overhead_ms": {
            "before": (before.get("wall_invocation_ms", {}).get("median_ms")
                       - before.get("timing_ms", {}).get("median_ms")
                       if before.get("wall_invocation_ms") and before.get("timing_ms") else None),
            "after": (after.get("wall_invocation_ms", {}).get("median_ms")
                      - after.get("timing_ms", {}).get("median_ms")
                      if after.get("wall_invocation_ms") and after.get("timing_ms") else None),
        },
        "status": "PASS" if (full.get("status") == "PASS"
                             and cases.get("status") == "PASS") else "FAIL",
        "no_install_no_rf": True,
        "limits": [
            "This is the performance study's ARM comparison; it is not a new candidate qualification.",
            "The optimization baseline is candidate 099174be; if candidate_changed is true, results apply only to the supplied candidate.",
            "No product installation, RF, current/overlay/service change.",
        ],
    }
    out = Path(results_dir) / "arm_compare_summary.json"
    out.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    _write_arm_report(Path(results_dir), summary)
    return summary


def _write_arm_report(results_dir, summary):
    full = summary["fullpath"]
    before, after = full["before"], full["after"]
    before_wall, after_wall = full["before_wall"], full["after_wall"]
    before_rss = (full.get("before_rss_kb") or {}).get("after") or {}
    after_rss = (full.get("after_rss_kb") or {}).get("after") or {}
    lines = [
        "# ARM 对照结果（性能优化原型）\n",
        "> run_id: `%s`；研究基线候选 `%s`；被测候选 `%s`。"
        "本结果不是新候选资格。\n" % (
            summary["run_id"], summary["optimization_study_baseline"],
            summary["candidate"].get("release_id")),
        "\n## 同一性\n",
        "- same_candidate: **%s**；same_inputs: **%s**；candidate_changed: **%s**\n"
        % (summary["same_candidate"], summary["same_inputs"],
           summary["candidate"].get("candidate_changed")),
        "\n## full-path（同 4 帧、同 warmup5/repeats20、同 out.total_seconds）\n",
        "| 指标 | baseline | optimized |\n|---|---:|---:|\n",
        "| median ms | %.3f | %.3f |\n" % (
            before["median_ms"], after["median_ms"]),
        "| P95 ms | %.3f | %.3f |\n" % (before["p95_ms"], after["p95_ms"]),
        "| VmHWM KiB | %s | %s |\n" % (before_rss.get("VmHWM"),
                                        after_rss.get("VmHWM")),
        "| wall median ms | %.3f | %.3f |\n" % (
            before_wall["median_ms"], after_wall["median_ms"]),
        "| load overhead median ms (wall−stage) | %.3f | %.3f |\n" % (
            summary["load_overhead_ms"]["before"], summary["load_overhead_ms"]["after"]),
        "\n## 一致性\n",
        "- full-path parity: **%s**（mismatch=%s）\n" % (
            summary["fullpath"]["parity"]["status"],
            summary["fullpath"]["parity"]["mismatches"]),
        "- behavior parity: **%s**（%s cases, mismatch=%s）\n" % (
            summary["behavior_parity"]["status"], summary["behavior_parity"]["cases"],
            summary["behavior_parity"]["mismatches"]),
        "\n## adapter 加载/目标阶段\n",
        "- adapter construct ms: baseline %s / optimized %s\n" % (
            summary["adapter"]["construct_ms_before"],
            summary["adapter"]["construct_ms_after"]),
        "\n## 分组 timing（adapter-only）\n",
    ]
    for category in sorted((summary.get("groups") or {}).get("before") or {}):
        b = summary["groups"]["before"][category]
        a = summary["groups"]["after"][category]
        lines.append("- %s: median %.3f → %.3f ms, P95 %.3f → %.3f ms\n" % (
            category, b["median_ms"], a["median_ms"], b["p95_ms"], a["p95_ms"]))
    lines.append("\n## 限制\n")
    for item in summary["limits"]:
        lines.append("- %s\n" % item)
    (results_dir / "arm_compare_report.md").write_text("".join(lines), encoding="utf-8")


def execute(plan):
    helper = _load_helper()
    client = helper.connect()
    local_dir = PACKAGE / "results" / "arm_compare" / plan["run_id"]
    local_dir.mkdir(parents=True, exist_ok=True)
    logs = []
    try:
        sftp = client.open_sftp()
        try:
            for remote_dir in plan["remote_paths"].values():
                _mkdirs(sftp, remote_dir)
        finally:
            sftp.close()
        _upload(client, plan)
        for command in plan["commands"]:
            started = time.time()
            rc, out, err = _run_command(client, command["command"], command["timeout_s"])
            logs.append({"name": command["name"], "command": command["command"],
                         "returncode": rc, "seconds": time.time() - started,
                         "stdout_tail": out[-4000:], "stderr_tail": err[-4000:]})
            if rc != 0:
                raise SystemExit("remote command failed: %s rc=%s\n%s\n%s"
                                 % (command["name"], rc, out[-2000:], err[-2000:]))
        _download(client, plan, local_dir)
        summary = _summary(local_dir, plan)
        (local_dir / "run_log.json").write_text(json.dumps(logs, indent=1),
                                                encoding="utf-8")
        return summary
    finally:
        try:
            _run_command(client, plan["cleanup"], COMMAND_TIMEOUTS["cleanup"])
        finally:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--frames-dir", type=Path, default=DEFAULT_FRAMES)
    parser.add_argument("--arm-dir", type=Path, default=DEFAULT_ARM)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--run-id", default=None,
                        help="fresh /tmp directory suffix; default timestamp")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and write plan only")
    mode.add_argument("--execute", action="store_true",
                      help="authorized ARM round: upload, run, retrieve, summarize, cleanup")
    args = parser.parse_args(argv)
    if not args.execute:
        plan = build_plan(args)
        out = write_plan(plan)
        print(json.dumps({
            "mode": "dry-run",
            "connected_board": False,
            "plan": str(out.relative_to(ROOT)).replace("\\", "/"),
            "candidate": plan["candidate"],
            "uploads": {key: {"files": len(rows),
                              "bytes": sum(row["size"] for row in rows)}
                        for key, rows in plan["uploads"].items()},
            "commands": [{"name": c["name"], "timeout_s": c["timeout_s"]}
                         for c in plan["commands"]],
            "remote_root": plan["remote_paths"]["root"],
            "cleanup": plan["cleanup"],
        }, indent=1))
        return 0
    plan = build_plan(args)
    summary = execute(plan)
    print(json.dumps({"mode": "execute", "run_id": plan["run_id"],
                      "status": summary["status"],
                      "candidate": summary["candidate"],
                      "timing": summary["fullpath"],
                      "parity": summary["behavior_parity"]}, indent=1, default=str))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
