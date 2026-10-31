#!/usr/bin/env python3
"""量一下**板子上**中文字号的可读下限: 同一串字按几个字号画出来, 存成 PNG 自己看。

开发机与板子的字体集不同(板子上唯一带中文的是 `DroidSansFallbackFull.ttf`), 所以
"在开发机上看着还行"不能作数 —— 实测 11px 的中文在板子上糊成一团(不是缺字形,
`has_glyphs` 照样通过, 是**没有足够的像素**去画这些笔画)。

跑法:  python3 font_probe.py /tmp/font.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from pynq_lcd import HEIGHT, WIDTH, rgb565  # noqa: E402

import pynq_lcd_ui as ui  # noqa: E402

SAMPLES = ("整数范围", "合成示意 | 非测量", "标准差 = 等级 / 100 x 127", "通道 波形")


def main(path="/tmp/font.png"):
    class Buf(object):
        pass

    buf = Buf()
    buf.array = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)
    buf.array[:, :] = rgb565(0x15, 0x1E, 0x2A)
    canvas = ui.Canvas(buf)
    y = 10
    for size in (11, 12, 13, 14, 15, 16, 18, 20):
        canvas.text(10, y, "%2dpx " % size, rgb565(0x5A, 0xE0, 0xC4), size)
        canvas.text(80, y, SAMPLES[0], rgb565(0xEE, 0xF3, 0xF8), size)
        canvas.text(200, y, SAMPLES[1], rgb565(0x96, 0xA5, 0xB8), size)
        canvas.text(430, y, SAMPLES[2], rgb565(0x96, 0xA5, 0xB8), size)
        y += size + 14
    ui.save_png(buf.array, path)
    print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
