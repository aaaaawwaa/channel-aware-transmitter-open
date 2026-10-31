#!/usr/bin/env python3
"""一键构建 bitstream 并生成可追溯发布 manifest。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from scripts.release_manifest import artifact_record, normalized_input_hash, sha256_file
except ModuleNotFoundError:
    from release_manifest import artifact_record, normalized_input_hash, sha256_file

ROOT = Path(__file__).resolve().parent.parent
RTL = ROOT / "src" / "rtl"
XPR = ROOT / "awg" / "awg.xpr"
HDF = ROOT / "awg" / "host_bd.hdf"
OOC_RUN = ROOT / "awg" / "awg.runs" / "host_bd_awg_top_0_0_synth_1"
OOC_DCP = OOC_RUN / "awg_top.dcp"
BIT = ROOT / "awg" / "awg.runs" / "impl_1" / "host_bd_wrapper.bit"
TIMING = ROOT / "awg" / "awg.runs" / "impl_1" / "host_bd_wrapper_timing_summary_routed.rpt"
MANIFEST = ROOT / "build_manifest.json"
REBUILD_TCL = ROOT / "tools" / "rebuild_bitstream.tcl"
EXPORT_HDF_TCL = ROOT / "scripts" / "export_hdf.tcl"
BOOTSTRAP_TCL = ROOT / "scripts" / "bootstrap_project.tcl"
VIVADO = os.environ.get("VIVADO_BIN", r"<LOCAL_PATH>2025.2\2025.2\Vivado\bin\vivado.bat")
PART = "xc7z020clg484-2"


def hash_rtl() -> str:
    return normalized_input_hash(ROOT)


def load_manifest() -> dict:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _tool_version() -> str | None:
    try:
        result = subprocess.run(
            [VIVADO, "-version"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return (result.stdout + result.stderr).strip() or None


def needs_rebuild(manifest: dict, force: bool) -> tuple[bool, str]:
    source_hash = hash_rtl()
    if force:
        return True, "--force 强制重建"
    if manifest.get("manifest_version") != 2:
        return True, "manifest 版本过期"
    if manifest.get("rtl_hash") != source_hash:
        return True, f"RTL 源文件哈希变化({manifest.get('rtl_hash', '无')[:12]} -> {source_hash[:12]})"
    if not BIT.exists():
        return True, "bitstream 不存在"
    if not OOC_DCP.exists():
        return True, "OOC dcp 不存在"
    expected_hash = manifest.get("bitstream_sha256")
    if not isinstance(expected_hash, str) or expected_hash != sha256_file(BIT):
        return True, "bitstream 内容哈希缺失或变化"
    return False, "up-to-date(源与 bitstream 一致)"


def _run_vivado(source: Path, *args: str) -> str:
    result = subprocess.run(
        [VIVADO, "-mode", "batch", "-source", str(source), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(output)
    return output


def _ensure_project() -> None:
    if XPR.exists():
        return
    print("[build] awg.xpr 不存在，执行 tracked bootstrap")
    _run_vivado(BOOTSTRAP_TCL)
    if not XPR.exists():
        raise RuntimeError(f"bootstrap 未生成工程: {XPR}")


def rebuild() -> None:
    t0 = time.time()
    print("[build] 开始完整重建(约 10-20 分钟)...")
    _ensure_project()
    try:
        output = _run_vivado(REBUILD_TCL)
        hdf_output = _run_vivado(EXPORT_HDF_TCL, "-tclargs", str(XPR))
    except RuntimeError as exc:
        print("[build] 失败")
        print(str(exc)[-4000:])
        sys.exit(1)
    for line in (output + hdf_output).splitlines():
        if any(key in line for key in (
            "ERROR", "OOC-OK", "SYNTH-OK", "BITSTREAM-OK", "HDF-EXPORTED",
            "TARGETS-REGENERATED", "BD-REGENERATED", "OOC-RUN-CREATED",
            "SYNTH1-PRE-INJECTED", "OOC-RESTORED",
        )):
            print("  ", line.strip()[:120])
    print(f"[build] 完成, 用时 {(time.time() - t0) / 60:.1f} 分钟")


def _run_vivado_stream(source: Path, log, *, mode: str | None = None,
                       args: tuple[str, ...] = ()) -> None:
    """Run one Vivado phase without retaining its full output in memory."""
    env = os.environ.copy()
    if mode is None:
        env.pop("REBUILD_MODE", None)
    else:
        env["REBUILD_MODE"] = mode
    command = [
        VIVADO, "-mode", "batch", "-source", str(source), *args,
        "-notrace", "-nolog", "-nojournal",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    recent: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        log.write(line)
        log.flush()
        recent.append(line)
        if len(recent) > 160:
            recent.pop(0)
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(
            f"Vivado phase failed (mode={mode or 'export'}, rc={returncode})\n"
            + "".join(recent)
        )


def rebuild_low_memory() -> None:
    """Run synthesis and implementation in separate Vivado processes."""
    t0 = time.time()
    log_path = ROOT / "awg" / "build_low_memory.log"
    print("[build] 开始低内存分阶段重建...")
    _ensure_project()
    try:
        with log_path.open("w", encoding="utf-8", newline="") as log:
            log.write("=== low-memory build ===\n")
            log.write("=== prep: BD/OOC ===\n")
            _run_vivado_stream(REBUILD_TCL, log, mode="prep")
            log.write("=== export: HDF ===\n")
            _run_vivado_stream(
                EXPORT_HDF_TCL,
                log,
                args=("-tclargs", str(XPR)),
            )
            log.write("=== synth: main synthesis/GATE2 ===\n")
            _run_vivado_stream(REBUILD_TCL, log, mode="synth")
            log.write("=== impl: implementation/bitstream ===\n")
            _run_vivado_stream(REBUILD_TCL, log, mode="impl")
            log.write("=== restore: OOC checkpoint ===\n")
            _run_vivado_stream(REBUILD_TCL, log, mode="restore")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        required = (
            "BD-REGENERATED",
            "OOC-OK",
            "SYNTH-OK",
            "GATE2-OK",
            "BITSTREAM-OK",
            "OOC-RESTORED",
            "HDF-EXPORTED",
        )
        missing = [marker for marker in required if marker not in text]
        if missing:
            raise RuntimeError(
                "低内存构建日志缺少门禁/产物标记: " + ", ".join(missing)
            )
        print(f"[build] 低内存构建日志: {log_path}")
        print(f"[build] 完成, 用时 {(time.time() - t0) / 60:.1f} 分钟")
    except (OSError, RuntimeError) as exc:
        print("[build] 低内存分阶段构建失败")
        print(str(exc)[-4000:])
        sys.exit(1)


def _timing_summary() -> dict:
    if not TIMING.exists():
        return {}
    text = TIMING.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "WNS(ns)" in line and "WHS(ns)" in line and index + 2 < len(lines):
            fields = lines[index + 2].split()
            if len(fields) >= 12:
                try:
                    return {
                        "wns_ns": float(fields[0]),
                        "tns_ns": float(fields[1]),
                        "setup_failing_endpoints": int(fields[2]),
                        "whs_ns": float(fields[4]),
                        "ths_ns": float(fields[5]),
                        "hold_failing_endpoints": int(fields[6]),
                        "wpws_ns": float(fields[8]),
                        "pw_failing_endpoints": int(fields[10]),
                    }
                except ValueError:
                    pass
    return {}


def _manifest() -> dict:
    artifacts = {"bitstream": artifact_record(BIT, ROOT)}
    if HDF.exists():
        artifacts["hdf"] = artifact_record(HDF, ROOT)
    sdk_elf = os.environ.get("SDK_ELF")
    if sdk_elf:
        elf = Path(sdk_elf).resolve()
        if not elf.is_file() or ROOT not in elf.parents:
            raise RuntimeError("SDK_ELF 必须指向工程目录内的 ELF 文件")
        artifacts["elf"] = artifact_record(elf, ROOT)
    return {
        "manifest_version": 2,
        "git_sha": _git_sha(),
        "part": PART,
        "rtl_hash": hash_rtl(),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "toolchain": {"vivado": _tool_version(), "python": sys.version},
        "bitstream": BIT.relative_to(ROOT).as_posix(),
        "bitstream_sha256": sha256_file(BIT),
        "bitstream_mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(BIT.stat().st_mtime)),
        "artifacts": artifacts,
        "timing": _timing_summary(),
        "commands": {
            "build": "python scripts/build_bitstream.py",
            "check": "python scripts/check_bitstream.py",
        },
    }


def main() -> None:
    force = "--force" in sys.argv
    low_memory = "--low-memory" in sys.argv
    manifest = load_manifest()
    need, reason = needs_rebuild(manifest, force)
    if need:
        print(f"[build] 需要重建: {reason}")
        if low_memory:
            rebuild_low_memory()
        else:
            rebuild()
        if not BIT.exists():
            print(f"[build] 失败: bitstream 未生成: {BIT}")
            sys.exit(1)
        MANIFEST.write_text(json.dumps(_manifest(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[build] manifest 已更新: {MANIFEST}")
    else:
        print(f"[build] 跳过: {reason}")
    print(f"[build] bitstream: {BIT}")
    print("[build] 下载前请运行: python scripts/check_bitstream.py")


if __name__ == "__main__":
    main()
