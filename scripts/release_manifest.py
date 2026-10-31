"""Shared release artifact hashing helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_input_hash(root: Path) -> str:
    rtl = root / "src" / "rtl"
    # 合流之后, 显示/触摸那 4 个 RTL 文件仍在 `pynq_overlay/rtl/`(那份独立工程自己也要构建),
    # 但**它们是本设计的一部分** —— 不在哈希里就会出现"改了 RTL 却拿到旧位流"的老坑。
    lcd_rtl = root / "pynq_overlay" / "rtl"
    # 合流后 AD9361 那一半也是本设计的一部分: RTL 留在 `rf_overlay/rtl/`(那份独立工程
    # 自己也要构建), 引脚约束直接复用 `rf_overlay/constraints/rf.xdc`(由 create_bd.tcl
    # 加进 constrs_1)。不把它们算进哈希, 改了 RF 侧就会拿到旧位流 —— 与 lcd_rtl 同一条理。
    #
    # 注: `rf_overlay/rtl/sine_rom.v` 与 `src/rtl/sine_rom.v` 目前逐字节相同。两份都在哈希里,
    # 所以任何一份漂移都会改哈希; 而"两份漂移"本身是要测的 (见 host/tests/test_rf_merge.py)。
    rf_rtl = root / "rf_overlay" / "rtl"
    rf_constr = root / "rf_overlay" / "constraints"
    files = (
        sorted(rtl.glob("*.v"))
        + sorted(rtl.glob("*.vh"))
        + sorted(lcd_rtl.glob("*.v"))
        + sorted(rf_rtl.glob("*.v"))
        + sorted((root / "src" / "constraints").glob("*.xdc"))
        + sorted(rf_constr.glob("*.xdc"))
        + [
            root / "scripts" / "create_bd.tcl",
            root / "scripts" / "ps7_props.tcl",
            root / "tools" / "rebuild_bitstream.tcl",
            root / "tools" / "check_bd_bram.tcl",
            root / "tools" / "synth1_read_awgtop.tcl",
            root / "tools" / "write_bitstream_pre.tcl",
            # 时钟组直接决定实现结果(AXI↔pclk 是否当真时序去展), 漏了同样会拿到旧位流
            root / "tools" / "impl1_clock_groups.tcl",
        ]
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def artifact_record(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
