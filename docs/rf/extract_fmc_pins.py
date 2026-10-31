#!/usr/bin/env python3
"""从厂商引脚表提取 FMC 信号 → FPGA 引脚映射, 并可选地用 Vivado 查每个引脚的 Bank。

为什么需要它: `compatibility.md` 的 Bank 分布结论是**算出来的**, 不是抄来的。
厂商的 `FMC接口.xlsx` 只给 FMC 引脚号 → FPGA 引脚号; Bank 归属要去 Vivado 问
`get_package_pins`。两者拼起来才是完整的兼容表。

用法(在工程根目录):
    python docs/rf/extract_fmc_pins.py --xlsx "<LOCAL_PATH>引脚信息/FMC接口.xlsx"

会打印 FMC→FPGA 映射与 Bank 直方图。加 `--emit-tcl` 会输出一段 Vivado 批处理脚本,
跑完 `bankq.tcl` 后把 `PIN <引脚> BANK <n>` 行喂回 `--banks <文件>` 即可得到完整表。
"""
import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NSMAP = {"m": NS[1:-1]}


def shared_strings(z):
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.findall("m:si", NSMAP):
        out.append("".join(t.text or "" for t in si.iter(NS + "t")))
    return out


def read_rows(path):
    """把第一个 worksheet 读成 [ [cell, ...], ... ](跳过空行)。"""
    z = zipfile.ZipFile(path)
    ss = shared_strings(z)
    sheets = sorted(n for n in z.namelist()
                    if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not sheets:
        raise SystemExit("没有找到 worksheet: %s" % path)
    root = ET.fromstring(z.read(sheets[0]))
    rows = []
    for row in root.findall(".//m:row", NSMAP):
        cells = []
        for c in row.findall("m:c", NSMAP):
            v = c.find("m:v", NSMAP)
            if v is None:
                continue
            cells.append(ss[int(v.text)] if c.get("t") == "s" else v.text)
        if cells:
            rows.append(cells)
    return rows


def parse_fmc_pairs(rows):
    """FMC 表是 4 列: FMC号 | FPGA引脚 | FMC号 | FPGA引脚(表头除外)。"""
    pairs = []
    for cells in rows:
        if len(cells) == 4 and cells[0] != "FMC引脚号":
            pairs.append((cells[0].strip(), cells[1].strip()))
            pairs.append((cells[2].strip(), cells[3].strip()))
    return pairs


def parse_banks(path):
    banks = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 4 and parts[0] == "PIN":
                banks[parts[1]] = parts[3]
    return banks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, help="厂商 FMC接口.xlsx 路径")
    ap.add_argument("--banks", help="Vivado 查询输出(每行 'PIN <引脚> BANK <n>')")
    ap.add_argument("--emit-tcl", action="store_true", help="输出 bankq.tcl")
    args = ap.parse_args()

    if not os.path.exists(args.xlsx):
        raise SystemExit("找不到 %s" % args.xlsx)

    pairs = parse_fmc_pairs(read_rows(args.xlsx))
    print("FMC 信号引脚对: %d" % len(pairs))

    if args.emit_tcl:
        pins = sorted({fpga for _, fpga in pairs})
        print("\n# 存成 bankq.tcl, 用 Vivado 2025.2 跑:")
        print("#   vivado -mode batch -nojournal -nolog -source bankq.tcl | grep '^PIN' > banks.txt")
        print("create_project -in_memory -part xc7z020clg484-2")
        print("link_design -part xc7z020clg484-2")
        print("foreach p {%s} {" % " ".join(pins))
        print('  puts "PIN $p BANK [get_property BANK '
              '[get_package_pins -quiet $p]]"')
        print("}")
        return 0

    banks = parse_banks(args.banks) if args.banks else {}
    if not banks:
        print("\n(未给 --banks, 只列映射)")
        for fmc, fpga in pairs:
            print("%-5s %-5s" % (fmc, fpga))
        return 0

    hist = {}
    print("\n%-5s %-5s %s" % ("FMC", "FPGA", "BANK"))
    for fmc, fpga in pairs:
        b = banks.get(fpga, "?")
        hist[b] = hist.get(b, 0) + 1
        print("%-5s %-5s %s" % (fmc, fpga, b))
    print("\nBank 直方图: %s" % hist)
    return 0


if __name__ == "__main__":
    sys.exit(main())