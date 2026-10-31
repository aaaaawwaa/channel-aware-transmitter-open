#!/usr/bin/env python3
"""Render a dependency-free SVG constellation preview and point table.

Only numpy/XML text is used; no plotting library is required.  The preview is
generated from the 6 MSPS SYNTHETIC vectors and is marked SYNTHETIC.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
DEFAULT_DATA = PACKAGE / "data"
MODES = ("BPSK", "QPSK", "QAM16", "QAM64")
COLORS = {"BPSK": "#1565c0", "QPSK": "#2e7d32", "QAM16": "#ad1457", "QAM64": "#6a1b9a"}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _panel(svg, mode, values, x0, y0, width, height):
    i_vals = values[:, 0].astype(np.float64)
    q_vals = values[:, 1].astype(np.float64)
    limit = max(2000.0, float(np.max(np.abs(values))))
    def px(i):
        return x0 + 35 + (i + limit) / (2.0 * limit) * (width - 70)
    def py(q):
        return y0 + height - 35 - (q + limit) / (2.0 * limit) * (height - 70)
    svg.append('<rect x="%d" y="%d" width="%d" height="%d" fill="#fafafa" stroke="#555" />'
               % (x0, y0, width, height))
    svg.append('<text x="%d" y="%d" font-family="sans-serif" font-size="16" fill="#111">%s</text>'
               % (x0 + 10, y0 + 24, mode))
    # axes
    svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#bbb"/>'
               % (px(-limit), py(0), px(limit), py(0)))
    svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#bbb"/>'
               % (px(0), py(-limit), px(0), py(limit)))
    # full ideal lattice, faint
    axes = {"BPSK": ([1, -1], [0]),
            "QPSK": ([1, -1], [1, -1]),
            "QAM16": ([3, 1, -3, -1], [3, 1, -3, -1]),
            "QAM64": ([7, 5, 1, 3, -7, -5, -1, -3],
                      [7, 5, 1, 3, -7, -5, -1, -3])}[mode]
    scale = 1800.0 / max(max(abs(v) for v in axes[0]), max(abs(v) for v in axes[1]))
    for i in axes[0]:
        for q in axes[1]:
            svg.append('<circle cx="%.1f" cy="%.1f" r="5" fill="none" stroke="#bbb" '
                       'stroke-width="1"/>' % (px(i * scale), py(q * scale)))
    # actual unique points
    unique = sorted({(int(i), int(q)) for i, q in values.tolist()})
    colour = COLORS[mode]
    for i, q in unique:
        svg.append('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s"/>'
                   % (px(i), py(q), colour))
    svg.append('<text x="%d" y="%d" font-family="monospace" font-size="11" fill="#555">'
               "code I/Q, amp=1800, %d unique</text>"
               % (x0 + 10, y0 + height - 12, len(unique)))


def render(data_dir=DEFAULT_DATA):
    data_dir = Path(data_dir).resolve()
    manifest = _load_json(data_dir / "manifest.json")
    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="820" '
           'viewBox="0 0 1000 820">',
           '<rect width="1000" height="820" fill="white"/>',
           '<text x="30" y="34" font-family="sans-serif" font-size="21" fill="#111">'
           'SYNTHETIC four-class test vectors — 6 MSPS constellation preview</text>',
           '<text x="30" y="56" font-family="sans-serif" font-size="13" fill="#b00020">'
           'SYNTHETIC ONLY; not real captures, real accuracy or product qualification.</text>']
    csv = ["mode,kind,index,i_code,q_code,i_normalized,q_normalized"]
    for index, mode in enumerate(MODES):
        vector = next(v for v in manifest["vectors"] if v["truth_class"] == mode)
        path = data_dir / vector["rates"]["six_msps"]["path"]
        values = np.frombuffer(path.read_bytes(), dtype="<i2").reshape(-1, 2)
        x0 = 25 + (index % 2) * 485
        y0 = 80 + (index // 2) * 365
        _panel(svg, mode, values, x0, y0, 455, 335)
        for point_index, (i, q) in enumerate(values.tolist()):
            csv.append("%s,actual_sample,%d,%d,%d,%.9f,%.9f" % (
                mode, point_index, i, q, i / 1800.0, q / 1800.0))
    svg.append("</svg>")
    out_svg = data_dir / "constellation_preview.svg"
    out_svg.write_text("\n".join(svg) + "\n", encoding="utf-8")
    out_csv = data_dir / "constellation_points.csv"
    out_csv.write_text("\n".join(csv) + "\n", encoding="utf-8")
    return {"svg": str(out_svg), "csv": str(out_csv),
            "modes": list(MODES), "synthetic": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args(argv)
    print(json.dumps(render(args.data_dir), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
