#!/usr/bin/env python3
"""触摸屏的板上自检 / 标定工具。

它做三件事:
  1. 复用 MIO50/51 到 I2C0、放开 TP_RST、探测 GT911 的地址(0x5D 或 0x14);
  2. 在屏幕上画一个跟随手指的十字 + 原始/屏幕坐标读数, 用来**定性**看换算对不对;
  3. 一个"点我"按钮, 按下去跑一次 8 灯自检 —— 证明"触摸 → UI → 硬件"整条链路是通的。

`--calib` 是**定量**标定: 在已知屏幕位置画 5 个十字靶, 逐个等手指点稳, 用最小二乘解出
swap/flip 与两轴的 raw_min/raw_max, 并打印残差。**别用"手工点四角看读数"来标定** ——
手指够不到边框(实测角上 raw 差出 20~50), 人也判断不了自己落在哪个像素上, 那样标出来的
量程会带着几十像素的系统误差; 靶子是我们画出来的, 屏幕坐标才是真值。

⚠ **触摸只能读一次**: GT911 的状态寄存器是"读走才清零"的, 所以读点必须统一走 UI 的
输入源。这里用一个 `RecordingTouch` 包一层(UI 每帧问一次, 顺便把结果记下来给屏幕显示);
如果在外面再单独读一次, 第二次永远读到"没有新数据", 按钮就再也点不动了。

跑法(先停守护进程 —— 它占着 PL 与帧缓冲):
    systemctl stop pynq-status
    cd /home/xilinx/overlays/ledbtn
    sudo env XILINX_XRT=/usr PYTHONPATH=/home/xilinx python3 -u touch_demo.py
    sudo env XILINX_XRT=/usr PYTHONPATH=/home/xilinx python3 -u touch_demo.py --calib
"""
import os
import sys
import time

from pynq import Overlay

import pynq_lcd_ui as ui
from pynq_lcd import Lcd
from pynq_touch import CALIB_TARGETS, Touch, TouchInput, fit_calibration

OVERLAY = os.environ.get("LEDBTN_OVERLAY", "/home/xilinx/overlays/ledbtn/ledbtn.bit")
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 30.0
FRAME = 1.0 / 30


class RecordingTouch:
    """UI 的输入源, 顺便记住最近一次读到的点(只读一次, 见模块开头)。"""

    def __init__(self, touch):
        self.inner = TouchInput(touch) if touch is not None else ui.NullInput()
        self.last = None
        self.errors = 0

    def poll(self):
        self.last = self.inner.poll()
        self.errors = getattr(self.inner, "errors", 0)
        return self.last

    def close(self):
        self.inner.close()


class LedSelfTest:
    """S1 那个 8 灯自检; 这里让屏幕按钮也能触发。"""

    def __init__(self, overlay):
        self.gpio = overlay.axi_gpio_led
        self.runs = 0

    def run(self):
        self.runs += 1
        for i in range(8):
            self.gpio.channel1.write(1 << i, 0xFF)
            time.sleep(0.06)
        self.gpio.channel1.write(0, 0xFF)


def probe_reset(overlay):
    """在 TP_RST 低/高两种状态下各读一次 I2C 引脚电平。

    这是为了把"线被拉住"归因清楚: 如果 GT911 在复位期间按住 SCL/SDA, 那么复位放开后
    两个脚就会回到高电平 —— 那之前所有"线是低的"的测量都只是"器件还没被放开"。
    结束时**把 TP_RST 留在高电平**(GPIO 的值在进程退出后仍保持), 后续进程可以直接扫描。
    """
    from pynq_touch import diagnose_pins

    def rst(up):
        overlay.axi_gpio_fb.channel2.write(0b10 if up else 0b00, 0b10)

    for up in (False, True):
        rst(up)
        time.sleep(0.05)
        got = diagnose_pins()
        print("TP_RST=%d: %s" % (int(up), {k: "SCL=%d SDA=%d" % v for k, v in got.items()}))
    rst(True)
    print("(已把 TP_RST 留在高电平, 后面直接跑 pynq_touch.py scan 即可)")


def _rst_from(overlay):
    """TP_RST 是 `axi_gpio_fb` 通道2 的 bit1(见 create_bd.tcl / led_btn.xdc 的 R15)。"""
    return lambda up: overlay.axi_gpio_fb.channel2.write(0b10 if up else 0x00, 0b10)


def _draw_target(canvas, sx, sy, color, arm=26, radius=13):
    canvas.rect(sx - arm, sy, sx + arm, sy, color)
    canvas.rect(sx, sy - arm, sx, sy + arm, color)
    canvas.circle(sx, sy, radius, color, fill=False)


def report_calibration(pairs, out=print):
    """拟合 + 校验 + 打印可直接粘的常数; 返回 `(参数, 最大误差像素)`。

    校验走的是**端到端**路径(参数 → `Touch.to_screen` → 与靶位比), 而不是回到拟合残差 ——
    残差小但 swap/flip 选错的组合是存在的, 只有走完整换算才知道换算对不对。
    """
    params = fit_calibration(pairs)
    check = Touch(None, 0, **params)
    worst = 0
    for raw, want in pairs:
        got = check.to_screen(*raw)
        err = max(abs(got[0] - want[0]), abs(got[1] - want[1]))
        worst = max(worst, err)
        out("  校验 raw=%s -> %s (靶 %s) 误差 %d px" % (raw, got, want, err))
    out("最大误差 %d px" % worst)
    out("粘进 pynq_touch.py 当默认值: swap_xy=%s, flip_x=%s, flip_y=%s,"
        % (params["swap_xy"], params["flip_x"], params["flip_y"]))
    out("    raw_x_min=%d, raw_x_max=%d, raw_y_min=%d, raw_y_max=%d"
        % (round(params["raw_x_min"]), round(params["raw_x_max"]),
           round(params["raw_y_min"]), round(params["raw_y_max"])))
    return params, worst


def calibrate(overlay, lcd, out=print):
    """五点标定: 屏幕坐标是真值(靶子是我们画的), 拟合交给 `fit_calibration`。

    每个靶要求**连续采到若干点**才认账; 中途抬手就丢弃重来 —— 免得把两次按下的读数
    (手指在屏幕上滑动过)混在一起当中值。
    """
    touch = Touch.open(rst=_rst_from(overlay))
    out("GT911 @ 0x%02X, product_id=%r" % (touch.addr, touch.product_id()))

    canvas = ui.Canvas(lcd)
    lcd.enable()
    pairs = []
    for i, (sx, sy) in enumerate(CALIB_TARGETS):
        buf, streak = [], 0
        done = False
        # 第一个靶等久一点: 人可能还没看到屏幕就开始等了
        deadline = time.monotonic() + (180.0 if i == 0 else 60.0)
        while time.monotonic() < deadline:
            try:
                points = touch.read_points()
                streak = 0                     # 只统计**连续**失败: 偶发一次不算事
            except Exception as exc:           # noqa: BLE001
                streak += 1
                if streak > 30:
                    out("读点连续失败 %d 次, 放弃: %s" % (streak, exc))
                    canvas.clear(ui.BLACK)
                    canvas.text(24, 200, "触摸连续读失败, 标定中止", size=22, color=ui.RED)
                    canvas.text(24, 236, str(exc)[:60], size=16, color=ui.GREY)
                    lcd.flush()
                    return 1
                points = []
            if points:
                buf.append((points[0][0], points[0][1]))
                if len(buf) >= 8:
                    done = True
                    break
            else:
                buf = []
            canvas.clear(ui.BLACK)
            canvas.text(24, 12, "五点标定: 用手指点十字中心", size=22, color=ui.WHITE)
            for j, (tx, ty) in enumerate(CALIB_TARGETS):
                if j < i:
                    _draw_target(canvas, tx, ty, ui.GREEN)
                elif j == i:
                    _draw_target(canvas, tx, ty, ui.RED)
                    canvas.circle(tx, ty, 4, ui.RED)
                else:
                    _draw_target(canvas, tx, ty, ui.DIM)
            canvas.text(24, 430, "第 %d/5 个靶, 屏幕坐标 (%d, %d)" % (i + 1, sx, sy),
                        size=18, color=ui.WHITE)
            canvas.text(24, 452, "当前读数: %s" % (("%d, %d" % buf[-1]) if buf else "—"),
                        size=18, color=ui.GREY)
            lcd.flush()
            time.sleep(0.02)
        if not done:
            canvas.clear(ui.BLACK)
            canvas.text(24, 200, "第 %d 个靶超时(没等到稳定按下的点)" % (i + 1),
                        size=22, color=ui.RED)
            lcd.flush()
            out("靶 %d 超时, 标定中止" % (i + 1))
            return 1
        buf.sort()
        raw = (buf[len(buf) // 2][0], buf[len(buf) // 2][1])
        pairs.append((raw, (sx, sy)))
        out("靶 %d: 屏幕 (%d, %d) <- raw %s" % (i + 1, sx, sy, raw))

    params, worst = report_calibration(pairs, out=out)

    canvas.clear(ui.BLACK)
    canvas.text(24, 12, "标定完成", size=24, color=ui.GREEN)
    canvas.text(24, 56, "最大误差 %d px" % worst, size=20, color=ui.WHITE)
    canvas.text(24, 96, "swap=%s flip=%s/%s" % (params["swap_xy"], params["flip_x"],
                                                params["flip_y"]),
                size=18, color=ui.GREY)
    canvas.text(24, 124, "x %d..%d  y %d..%d"
                % (round(params["raw_x_min"]), round(params["raw_x_max"]),
                   round(params["raw_y_min"]), round(params["raw_y_max"])),
                size=18, color=ui.GREY)
    canvas.text(24, 164, "常数已打到控制台, 退出后守护进程会自动恢复状态页",
                size=16, color=ui.DIM)
    lcd.flush()
    return 0


def main():
    overlay = Overlay(OVERLAY)
    lcd = Lcd.from_overlay(overlay)
    leds = LedSelfTest(overlay)
    rst = _rst_from(overlay)

    print("复用 MIO50/51 到 I2C0, 放开复位, 探测 GT911 ...")
    touch = None
    try:
        touch = Touch.open(rst=rst)
        print("  找到 GT911 @ 0x%02X, product_id=%r" % (touch.addr, touch.product_id()))
    except Exception as exc:                      # noqa: BLE001
        print("  触摸没起来: %s" % exc)
        print("  屏幕仍会显示提示 —— 三个前提见 pynq_touch.py 模块注释")

    source = RecordingTouch(touch)
    screen = ui.Ui(lcd, source=source, background=0x0861, shadow=True)
    screen.add(ui.Label(24, 16, "触摸自检 / 标定", size=28, fg=ui.WHITE))
    state = screen.add(ui.Label(24, 60,
                                "GT911 @ 0x%02X" % touch.addr if touch else "未找到触摸",
                                size=18, fg=ui.GREEN if touch else ui.RED))
    raw = screen.add(ui.Label(24, 100, "原始: —", size=20, fg=ui.WHITE))
    scr = screen.add(ui.Label(24, 136, "屏幕: —", size=20, fg=ui.WHITE))
    screen.add(ui.Label(24, 172, "点在四角核对读数; 下面按钮跑一次 8 灯自检",
                        size=16, fg=ui.GREY))
    screen.add(ui.Button(24, 214, 280, 64, "点我: 8 灯自检",
                         on_tap=lambda b: leds.run(), size=18))
    count = screen.add(ui.Label(324, 234, "按钮触发 0 次", size=16, fg=ui.GREY))
    cross = screen.add(ui.Crosshair(lcd.array.shape[1], lcd.array.shape[0]))

    last_printed = None
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        point = source.last                    # 上一帧 UI 读到的点(只读一次)
        rawpoint = getattr(source.inner, "last_raw", None)
        cross.point = point[:2] if point else None
        if point:
            raw.text = "原始: %s" % ("%d, %d" % rawpoint if rawpoint else "—")
            scr.text = "屏幕: %d, %d" % (point[0], point[1])
        else:
            raw.text = "原始: —(没触摸)" if touch else "原始: —"
            scr.text = "屏幕: —"
        # 同时打到控制台: 标定要的是"手指在哪 -> 读数是多少", 这份数据得能远程看到
        if point and (last_printed is None
                      or abs(point[0] - last_printed[0]) + abs(point[1] - last_printed[1]) > 8):
            print("触摸 raw=%s -> screen=%s" % (rawpoint, point))
            last_printed = point
        elif not point and last_printed is not None:
            print("抬起")
            last_printed = None
        count.text = "按钮触发 %d 次" % leds.runs
        if touch is None and source.errors:
            state.text = "触摸读失败 %d 次" % source.errors
            state.fg = ui.RED
        screen.step()
        time.sleep(FRAME)

    print("结束: 按钮触发 %d 次, 触摸读失败 %d 次" % (leds.runs, source.errors))
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(probe_reset(Overlay(OVERLAY)) or 0)
    if "--calib" in sys.argv:
        _ov = Overlay(OVERLAY)
        sys.exit(calibrate(_ov, Lcd.from_overlay(_ov)) or 0)
    sys.exit(main())
