#!/usr/bin/env python3
"""下载前校验发布 manifest 与 bitstream 产物。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    from scripts.release_manifest import normalized_input_hash, sha256_file
except ModuleNotFoundError:
    from release_manifest import normalized_input_hash, sha256_file

ROOT = Path(__file__).resolve().parent.parent
BIT = ROOT / "awg" / "awg.runs" / "impl_1" / "host_bd_wrapper.bit"
MANIFEST = ROOT / "build_manifest.json"


def hash_rtl() -> str:
    return normalized_input_hash(ROOT)


def _load_manifest() -> dict | None:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"STALE: manifest 无法解析: {exc}")
        return None
    if not isinstance(value, dict):
        print("STALE: manifest 顶层必须是 JSON object")
        return None
    return value


def _check_artifact_record(problems: list[str], name: str, record: object) -> None:
    if not isinstance(record, dict):
        problems.append(f"manifest 的 {name} 记录无效")
        return
    path_value = record.get("path")
    expected_hash = record.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        problems.append(f"manifest 的 {name} 缺少 path/sha256")
        return
    path = ROOT / Path(path_value)
    try:
        path.relative_to(ROOT)
    except ValueError:
        problems.append(f"manifest 的 {name} 路径越界")
        return
    if not path.is_file():
        problems.append(f"{name} 缺失: {path}")
        return
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        problems.append(
            f"{name} SHA-256 不一致(manifest {expected_hash[:12]} != 当前 {actual_hash[:12]})"
        )


def main() -> int:
    problems = []
    if not MANIFEST.exists():
        problems.append("manifest 缺失(从未构建或构建信息被删)")
    if not BIT.exists():
        problems.append(f"bitstream 缺失: {BIT}")
    if problems:
        for problem in problems:
            print(f"STALE: {problem}")
        print("请运行: python scripts/build_bitstream.py")
        return 1

    manifest = _load_manifest()
    if manifest is None:
        return 1
    if manifest.get("manifest_version") != 2:
        problems.append("manifest_version 缺失或不受支持(需要 2)")

    src_hash = hash_rtl()
    manifest_hash = manifest.get("rtl_hash")
    if not isinstance(manifest_hash, str) or manifest_hash != src_hash:
        problems.append(
            f"RTL 源已变化(manifest {str(manifest_hash)[:12] if manifest_hash is not None else '?'} != 当前 {src_hash[:12]})"
        )
    expected_path = BIT.relative_to(ROOT).as_posix()
    if manifest.get("bitstream") != expected_path:
        problems.append("manifest 记录的 bitstream 路径与当前不一致")
    expected_bit_hash = manifest.get("bitstream_sha256")
    if not isinstance(expected_bit_hash, str):
        problems.append("manifest 缺少 bitstream_sha256")
    else:
        actual_bit_hash = sha256_file(BIT)
        if expected_bit_hash != actual_bit_hash:
            problems.append(
                "bitstream SHA-256 不一致"
                f"(manifest {expected_bit_hash[:12]} != 当前 {actual_bit_hash[:12]})"
            )

    actual_mtime = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(BIT.stat().st_mtime)
    )
    if manifest.get("bitstream_mtime") != actual_mtime:
        print(
            "NOTE: bitstream mtime 与 manifest 不一致"
            f"({manifest.get('bitstream_mtime', '?')} != {actual_mtime}); 使用 SHA-256 判定内容"
        )

    artifacts = manifest.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, dict):
            problems.append("manifest 的 artifacts 必须是 JSON object")
        else:
            for name in ("hdf", "elf"):
                if name in artifacts and artifacts[name] is not None:
                    _check_artifact_record(problems, name, artifacts[name])

    if problems:
        for problem in problems:
            print(f"STALE: {problem}")
        print("请运行: python scripts/build_bitstream.py 重新构建后重试")
        return 1

    print(f"OK: bitstream 与当前 RTL 源一致(构建于 {manifest.get('built_at', '?')})")
    print(f"    {BIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
