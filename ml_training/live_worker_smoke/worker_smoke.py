#!/usr/bin/env python3
"""Single-release, no-hardware worker contract smoke.

Runs in a fresh process for one release.  It loads that release's **actual**
``rf_status_worker.RfStatusWorker`` and calls that worker's real ``_ensure_*``
loading methods.  The daemon/client is replaced by a client that raises on every
attribute access, so no arm/TX/capture/control can occur.  Release verification is
still performed by ``merged_release.verify_release(..., require_software=True)``;
nothing is mocked, no availability is fabricated and no module is copied into the
working tree.

This is the child process; use ``run.py`` as the one-command entry so different
releases always get separate interpreters.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
CONTRACTS = (
    ("demod", "_ensure_demod", "demod_status"),
    ("six_class_model", "_ensure_model", "model_status"),
    ("environment", "_ensure_environment", "environment_status"),
    ("teaching_record", "_ensure_teaching", "teaching_status"),
    ("modrec4", "_ensure_modrec4", "modrec4_status"),
)


class ForbiddenHardwareClient:
    """A client that cannot perform any external operation."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError("hardware client interaction is forbidden: %s" % name)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_release_worker(release):
    release = Path(release).resolve()
    if not release.is_dir():
        raise RuntimeError("release directory missing: %s" % release)
    if str(release) in sys.path:
        sys.path.remove(str(release))
    sys.path.insert(0, str(release))
    merged_release = importlib.import_module("merged_release")
    manifest = merged_release.verify_release(release, require_software=True)
    worker_mod = importlib.import_module("rf_status_worker")
    worker_path = Path(worker_mod.__file__).resolve()
    if worker_path.parent != release:
        raise RuntimeError("worker imported outside release: %s" % worker_path)
    return release, merged_release, manifest, worker_mod, worker_path


def run_one(release, out_dir):
    release = Path(release).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "ml-live-worker-contract-smoke-v1",
        "release_requested": str(release),
        "no_hardware_client": True,
        "device_connection": False,
        "rf_operation": False,
        "tx": False,
        "capture": False,
        "hardware_client_calls": [],
        "release_verification": None,
        "worker": None,
        "contracts": {},
        "unavailable_contracts": [],
        "all_contracts_available": False,
        "status": "ERROR",
        "error": None,
    }
    client = ForbiddenHardwareClient()
    try:
        release, merged_release, manifest, worker_mod, worker_path = \
            _load_release_worker(release)
        result["release_verification"] = {
            "ok": True,
            "release_id": manifest.get("release_id"),
            "build_id": manifest.get("build_id"),
            "software_contract": manifest.get("software_contract"),
            "software_count": len(manifest.get("software") or {}),
            "modrec4_model": manifest.get("modrec4_model"),
            "model_artifact": manifest.get("model_artifact"),
            "worker_sha256": sha256_file(worker_path),
            "merged_release_sha256": sha256_file(release / "merged_release.py"),
        }
        result["worker"] = {"module": worker_mod.__name__,
                            "path": str(worker_path),
                            "in_release": True}
        try:
            worker = worker_mod.RfStatusWorker(
                client=client, release_directory=release)
        except Exception as exc:
            result["error"] = "worker construct: %s: %s" % (type(exc).__name__, exc)
            result["hardware_client_calls"] = list(client.calls)
            raise
        for contract, loader_name, status_name in CONTRACTS:
            row = {"loader": loader_name, "status_method": status_name,
                   "loaded": False, "error": None, "status": None}
            try:
                loader = getattr(worker, loader_name)
                loader()
                status = getattr(worker, status_name)()
                row["loaded"] = True
                row["status"] = status
                if not (isinstance(status, dict) and status.get("available")):
                    result["unavailable_contracts"].append(contract)
            except Exception as exc:
                row["error"] = "%s: %s" % (type(exc).__name__, exc)
                if contract not in result["unavailable_contracts"]:
                    result["unavailable_contracts"].append(contract)
            result["contracts"][contract] = row
        result["all_contracts_available"] = not result["unavailable_contracts"]
        result["hardware_client_calls"] = list(client.calls)
        if result["hardware_client_calls"]:
            result["error"] = ("hardware client was touched: %s"
                              % result["hardware_client_calls"])
        release_id = (result["release_verification"] or {}).get("release_id", "")
        if result["error"]:
            result["status"] = "ERROR"
        elif result["all_contracts_available"]:
            result["status"] = "ALL_AVAILABLE"
        elif str(release_id).startswith("ad37bad4"):
            result["status"] = "BASELINE_UNAVAILABLE"
        else:
            result["status"] = "PARTIAL"
    except Exception as exc:
        if not result["error"]:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["status"] = "ERROR"
        result["traceback"] = traceback.format_exc()
        result["hardware_client_calls"] = list(client.calls)
    out_json = out_dir / "worker_smoke.json"
    out_json.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    _write_md(out_dir / "worker_smoke.md", result)
    return result


def _write_md(path, result):
    lines = [
        "# live worker contract smoke (no hardware)\n",
        "> 无设备、无控制/TX/采集；使用发布内真实 `RfStatusWorker` 的 `_ensure_*` 加载方法；"
        "客户端为禁止外部交互的测试替身，发布校验未跳过。\n",
        "\n## Release\n",
        "- requested: `%s`\n" % result.get("release_requested"),
        "- verified: %s\n" % (result.get("release_verification") or {}).get("ok"),
        "- release_id: `%s`\n" % (result.get("release_verification") or {}).get("release_id"),
        "- contract: `%s`\n" % (result.get("release_verification") or {}).get("software_contract"),
        "- worker: `%s`\n" % (result.get("worker") or {}).get("path"),
        "\n## Contract availability\n",
        "| contract | available | release_id | reason |\n|---|---|---|---|\n",
    ]
    for contract in ("demod", "six_class_model", "environment", "teaching_record", "modrec4"):
        row = (result.get("contracts") or {}).get(contract) or {}
        status = row.get("status") or {}
        lines.append("| %s | %s | %s | %s |\n" % (
            contract, bool(status.get("available")),
            status.get("release_id"), status.get("reason", row.get("error") or "")))
    lines.append("\n- unavailable: %s\n" % (result.get("unavailable_contracts") or []))
    lines.append("- all available: %s\n" % result.get("all_contracts_available"))
    lines.append("- hardware client calls: %s\n" % result.get("hardware_client_calls"))
    lines.append("- status: **%s**\n" % result.get("status"))
    if result.get("error"):
        lines.append("\n## Error\n`%s`\n" % result["error"])
    lines.append("\n## 边界\n")
    lines.append("- 只加载发布合同，不启动 worker、不连接 daemon/设备、不 arm/control、不 TX/采集。\n")
    lines.append("- `available` 全部来自真实 worker 方法；未伪造、未 monkeypatch。\n")
    lines.append("- 不同发布请由 `run.py` 分别启动独立子进程。\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_one(args.release, args.out_dir)
    print(json.dumps({
        "status": result["status"],
        "release_id": (result.get("release_verification") or {}).get("release_id"),
        "unavailable": result["unavailable_contracts"],
        "all_available": result["all_contracts_available"],
        "hardware_client_calls": result["hardware_client_calls"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
