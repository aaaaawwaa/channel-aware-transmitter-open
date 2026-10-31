#!/usr/bin/env python3
"""AC820 5" TFT (800x480 RGB565) DDR 帧缓冲的 Python 像素 API。

数据通路:
    Python 写 numpy 数组 -> DDR 帧缓冲 -> PL 的 fb_axi_reader 经 S_AXI_HP0 读回
    -> lcd_fb 的三行缓冲 -> lcd_timing 输出 -> 面板

对应硬件:
    overlay.axi_gpio_fb 通道1 (32b) = 帧缓冲物理基址
    overlay.axi_gpio_fb 通道2 bit0  = 源选择, 1=帧缓冲, 0=PL 内置彩条

**行距不是 800 像素**: 读口按固定的 2048 字节行距走(每行 1600 B 的 AXI 突发必须
落在同一个 4 KB 窗口里, 详见 fb_axi_reader), 所以要分配 1024 像素宽的缓冲, 只填
前 800 列。浪费约 0.9 MiB, 换来读口零配置。

**缓存**: 默认分配的 CMA 缓冲对 CPU 是 cacheable 的, 写完必须 flush() 才能保证 PL
看到(见 Lcd.flush)。不确定后端是否 cacheable 时也照调不误 —— 不可缓存时它是空操作。

用法(守护进程 pynq-status 在跑时它占着 PL, 先 systemctl stop pynq-status):
    from pynq_lcd import Lcd, rgb565, COLOR_BARS
    lcd = Lcd.from_bitfile("/home/xilinx/overlays/ledbtn/ledbtn.bit")
    lcd.fill(rgb565(0, 0, 0))
    for x in range(800):
        lcd.array[:, x] = COLOR_BARS[(x // 100) % 8]
    lcd.show()

链路自检(读 PL 里的只读抽头, 判断读口拿到的像素是不是写进去的那些):
    python3 pynq_lcd.py --check
"""
import json
import sys
import time

import numpy as np

WIDTH, HEIGHT = 800, 480
STRIDE_PIX = 1024                      # 2048 B 行距 / 2 B 每像素
_BUF_SHAPE = (HEIGHT, STRIDE_PIX)


def rgb565(r, g, b):
    """8-8-8 分量 -> RGB565。分量按位截断(不做四舍五入), 与常见 TFT 一致。"""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# PL 内置彩条(lcd_timing 的 8 条竖条), 用于"帧缓冲关掉时应当看到什么"的基准。
COLOR_BARS = [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]

# 自检抽头的两个只读 AXI GPIO(BD 里的 axi_gpio_dbg / axi_gpio_dbg2)。位域见
# lcd_fb.v 的 debug bus 段: dbg0 全是读口域名, dbg1 是显示域名, dbg2/dbg3 是
# "读口刚读完的那一行"和它收到的像素之和。
DBG_IPS = ("axi_gpio_dbg", "axi_gpio_dbg2")


def _decode_ring(d0, d1):
    return {
        "reader_line": d0 & 0x1FF,
        "tag": [(d0 >> 9) & 0x1FF, (d0 >> 18) & 0x1FF, d1 & 0x1FF],
        "reader_out_of_page": bool((d0 >> 27) & 1),
        "base_misaligned": bool((d0 >> 28) & 1),
        "tag_bad": bool((d1 >> 27) & 1),
        "bad_row": (d1 >> 9) & 0x1FF,
        "bad_got": (d1 >> 18) & 0x1FF,
        "display_stall": bool((d1 >> 28) & 1),
    }


class Lcd:
    """帧缓冲 + 控制寄存器。用 from_bitfile() 或 from_overlay() 构造。"""

    def __init__(self, overlay):
        self._overlay = overlay
        self._gpio = overlay.axi_gpio_fb
        self._buf = _allocate(_BUF_SHAPE)
        self._buf[:] = 0
        # 同一个内存的 800 列视图; 直接写它即可, 不必再做裁剪。
        self.array = self._buf[:, :WIDTH]
        self.disable()
        self.flush()

    # ---- 构造 ----

    @classmethod
    def from_bitfile(cls, bitfile):
        from pynq import Overlay
        return cls(Overlay(bitfile))

    @classmethod
    def from_overlay(cls, overlay):
        return cls(overlay)

    # ---- 控制 ----

    @property
    def address(self):
        """帧缓冲的物理地址, 就是写进 GPIO 通道1 的值。"""
        return _device_address(self._buf)

    def flush(self):
        """把 CPU 侧的写回落到 DDR, 供 PL 读取。不可缓存后端上是空操作。"""
        flush = getattr(self._buf, "flush", None)
        if flush is not None:
            flush()

    def enable(self):
        """切到帧缓冲源(画完再调; 切换会让画面从第一行重新开始)。"""
        self._gpio.channel1.write(self.address, 0xFFFFFFFF)
        self._gpio.channel2.write(1, 0x1)

    def disable(self):
        """切回 PL 内置彩条。"""
        self._gpio.channel2.write(0, 0x1)

    def show(self):
        """flush + 打开帧缓冲源, 是"把当前内容显示出来"的一步。"""
        self.flush()
        self.enable()

    # ---- 绘图 ----

    def fill(self, color):
        self.array[:, :] = color

    def clear(self):
        self.fill(0)

    def pixel(self, x, y, color):
        self.array[y, x] = color

    def hline(self, x0, x1, y, color):
        self.array[y, x0:x1] = color

    def vline(self, x, y0, y1, color):
        self.array[y0:y1, x] = color

    def rect(self, x0, y0, x1, y1, color, fill=False):
        """(x0,y0)-(x1,y1) 闭区间。fill=False 只画边框。"""
        if fill:
            self.array[y0:y1 + 1, x0:x1 + 1] = color
            return
        self.array[y0, x0:x1 + 1] = color
        self.array[y1, x0:x1 + 1] = color
        self.array[y0:y1 + 1, x0] = color
        self.array[y0:y1 + 1, x1] = color

    def blit(self, x, y, image):
        """把 (h,w) 的 uint16 图像贴到 (x,y), 越界部分自动裁掉。"""
        image = np.asarray(image, dtype=np.uint16)
        if image.ndim != 2:
            raise ValueError("image must be 2-D, got shape %r" % (image.shape,))
        h, w = image.shape
        x0, x1 = max(x, 0), min(x + w, WIDTH)
        y0, y1 = max(y, 0), min(y + h, HEIGHT)
        if x0 >= x1 or y0 >= y1:
            return
        self.array[y0:y1, x0:x1] = image[y0 - y:y1 - y, x0 - x:x1 - x]

    def color_bars(self):
        """PL 彩条的软件复刻, 用来验证"帧缓冲通路的数据是逐位对的"。"""
        for i, c in enumerate(COLOR_BARS):
            self.array[:, i * 100:(i + 1) * 100] = c

    def ramp(self):
        """每行互不相同的斜坡图案, 供自检使用。

        自检判的是"这一行收到的像素之和对不对", 所以任何两行的和都必须不同 ——
        否则行错位也照样"通过"。这里取 `x + 100*行号`: 第 y 行的像素和是
        `319600 + 80000*y`, 480 行内互不相同。像素值最大 `100*479 + 799 = 48799`,
        不越过 16 位, 求和的单调性才不会被回绕打乱。
        """
        ys = np.arange(HEIGHT, dtype=np.uint16)[:, None]
        xs = np.arange(WIDTH, dtype=np.uint16)[None, :]
        self.array[:, :] = xs + ys * 100

    # ---- 链路自检 ----

    def line_signature(self, line):
        """写进帧缓冲的第 line 行的像素之和, 32 位回绕 —— 与 fb_axi_reader 的算法相同。"""
        return int(self._buf[line, :WIDTH].astype(np.uint32).sum()) & 0xFFFFFFFF

    def _dbg_gpios(self):
        try:
            return [getattr(self._overlay, name) for name in DBG_IPS]
        except AttributeError as exc:
            raise RuntimeError(
                "该 overlay 没有 %s, 位流里没有自检抽头(需要 2026-09-13 之后重建的)"
                % " / ".join(DBG_IPS)) from exc

    def expected_signatures(self):
        """{行号: 该行的像素和} —— ramp 图案下这 480 个值互不相同。"""
        return {y: self.line_signature(y) for y in range(HEIGHT)}

    def _read_faults(self):
        """读一次硬件自己的裁决位。

        这些位都是**粘滞**的(置起后保持到读口/显示侧复位), 所以单次读就够, 不需要
        相干快照; 跨域同步也安全 —— 同步后读到高电平时, 旁边的行号早已稳定若干个
        pclk 周期(见 lcd_fb.v 的 debug bus 注释)。
        """
        ring_gpio = self._dbg_gpios()[0]
        return _decode_ring(ring_gpio.channel1.read(), ring_gpio.channel2.read())

    def _read_signature(self):
        """读口刚读完的那一行的像素之和。"""
        return self._dbg_gpios()[1].channel1.read() & 0xFFFFFFFF

    def _read_signature_line(self):
        """与上面那个签名一起发布的行号 —— 只用于诊断, 不参与判定(见 self_check)。"""
        return self._dbg_gpios()[1].channel2.read() & 0x1FF

    def self_check(self, samples=8000, diagnostics=32):
        """读回只读抽头, 判定帧缓冲链路是否把写进去的像素原样送到了显示侧。

        判据一 —— 签名集合: 读口收到的每一个"行像素之和"都必须是软件写进去的某一行
        的和, 且 480 个和都要出现过。任何一行被改坏, 它的和几乎必然不在这 480 个值里,
        于是立刻现形; 480 个都出现过则说明整屏每一行的像素都原样到达。

        这里**刻意不做行号配对**: 行号与签名是两个独立的 AXI GPIO, 而在这个平台上
        一次 Python 读约 25us, 比一行 31.7us 的显示时间还长, 两次读之间必然跨过行边界,
        配出来的"这一行的和配那一行的号"是假配对 —— 实测把 line 1 配成了 line 0 的和。
        只读签名就没有配对问题: 一个寄存器在一次时钟沿整体更新, 读到的要么是旧值、
        要么是新值, 两者都是合法的行签名。(要行号的话, 得让硬件把两者打进同一个寄存器。)

        判据二 —— 硬件自己的裁决: 显示侧逐行比对行号(tag_bad)、显示侧饿缓冲
        (display_stall)、读口越出帧缓冲的 1 MiB 页(reader_out_of_page)、基址没 4 KiB
        对齐(base_misaligned)。这些位是粘滞的, 采到就说明发生过, 读一次即可。行号与
        环形交接的**相干性由硬件保证**(它在 pclk 域逐行比对), 软件不去用有竞争的读重算。
        """
        expected = set(self.expected_signatures().values())
        stride = max(1, samples // max(diagnostics, 1))
        faults = {}
        observed = set()
        unknown = {}
        taken = 0

        for i in range(samples):
            if i % stride == 0:
                snap = self._read_faults()
                for name in ("reader_out_of_page", "base_misaligned", "tag_bad",
                             "display_stall"):
                    if snap[name]:
                        faults[name] = faults.get(name, 0) + 1
                if snap["tag_bad"]:
                    faults.setdefault("bad_row", snap["bad_row"])
                    faults.setdefault("bad_got", snap["bad_got"])
            sig = self._read_signature()
            taken += 1
            if sig in expected:
                observed.add(sig)
            else:
                entry = unknown.setdefault(sig, {"count": 0, "lines": []})
                entry["count"] += 1
                line = self._read_signature_line()
                if line not in entry["lines"]:
                    entry["lines"].append(line)
            if len(observed) == HEIGHT:
                break

        coverage = len(observed) / float(HEIGHT)
        ok = bool(taken > 0 and not faults and not unknown and len(observed) == HEIGHT)
        return {
            "ok": ok,
            "signature_samples": taken,
            "signature_coverage": round(coverage, 4),
            "lines_verified": len(observed),
            "unknown_signatures": {hex(sig): v for sig, v in sorted(unknown.items())},
            "sticky_faults": faults,
        }

    def close(self):
        self.disable()
        free = getattr(self._buf, "freebuffer", None) or getattr(self._buf, "free", None)
        if free is not None:
            try:
                free()
            except Exception:                        # noqa: BLE001  早停/重复释放
                pass


def _allocate(shape):
    from pynq import allocate
    return allocate(shape=shape, dtype=np.uint16)


def _device_address(buf):
    """PYNQ 各版本对物理地址的叫法不同, 取存在的那个。"""
    for name in ("device_address", "physical_address"):
        addr = getattr(buf, name, None)
        if addr is not None:
            return int(addr)
    raise AttributeError("no device address on %r" % type(buf).__name__)


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="draw a pattern into the TFT framebuffer")
    ap.add_argument("--bitfile", default="/home/xilinx/overlays/ledbtn/ledbtn.bit")
    ap.add_argument("--pattern", default="bars",
                    choices=["bars", "gradient", "grid", "coords", "ramp"])
    ap.add_argument("--check", action="store_true",
                    help="上屏后读回链路自检抽头, 逐行比对像素签名并打印报告")
    ap.add_argument("--samples", type=int, default=8000,
                    help="自检最多读多少次签名(默认 8000; 采到 480 行的和都出现过就提前停)")
    ap.add_argument("--diagnostics", type=int, default=64,
                    help="自检取多少次环形/故障位快照(默认 64)")
    ap.add_argument("--settle", type=float, default=1.0,
                    help="上屏后等多少秒再开始自检, 让读口走满一整屏(默认 1.0)")
    args = ap.parse_args()

    lcd = Lcd.from_bitfile(args.bitfile)
    if args.check:
        # 自检靠行内像素和判"这一行的像素对不对", 所以必须用每行互不相同的图案
        lcd.ramp()
    elif args.pattern == "bars":
        lcd.color_bars()
    elif args.pattern == "gradient":
        xs = np.arange(WIDTH, dtype=np.uint16)
        lcd.array[:, :] = ((xs >> 3) << 11) | (((xs >> 2) & 0x3F) << 5) | (xs >> 3)
    elif args.pattern == "grid":
        lcd.fill(0)
        for x in range(0, WIDTH, 40):
            lcd.vline(x, 0, HEIGHT, 0xFFFF)
        for y in range(0, HEIGHT, 40):
            lcd.hline(0, WIDTH, y, 0xFFFF)
    elif args.pattern == "coords":
        # 每 100 px 一块纯色, 块内左上 3x3 涂白 —— 块的边界就是"像素坐标是否对"的标尺
        for i, c in enumerate(COLOR_BARS):
            lcd.array[:, i * 100:(i + 1) * 100] = c
        for y in range(0, HEIGHT, 100):
            for x in range(0, WIDTH, 100):
                lcd.rect(x, y, x + 2, y + 2, 0xFFFF, fill=True)
    elif args.pattern == "ramp":
        lcd.ramp()
    lcd.show()
    print("framebuffer at 0x%08X, pattern=%s"
          % (lcd.address, "ramp(selfcheck)" if args.check else args.pattern))

    if not args.check:
        return 0

    time.sleep(args.settle)
    # 抽头的映射地址比"结果全对"更早说明位流装错: 名字在、地址不对, 读到的就是别人
    for name in DBG_IPS:
        info = lcd._overlay.ip_dict.get(name)
        print("%s at %s" % (name, "0x%08X" % info["phys_addr"] if info else "MISSING"))
    report = lcd.self_check(samples=args.samples, diagnostics=args.diagnostics)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("SELFCHECK %s" % ("PASS" if report["ok"] else "FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
