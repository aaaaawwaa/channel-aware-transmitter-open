#!/usr/bin/env python3
"""P7 板上验收: 从**真实触摸命中路径**改草稿 -> 应用 -> 事务线程 -> **真寄存器回读** ->
串口 `GET` 读回, 一条链跑完。

它调的是守护进程**同一个** `attach_screen(overlay)` —— 也就是 `pynq-status` 开机走的那条
建面路径(`Lcd.from_overlay` + 放开 TP_RST + 开 GT911 + `BridgeThread` + `attach_panel`)。
所以在它身上通过, 等价于"守护进程起来之后的那套东西是对的"; 前面那些离线测试各测一段,
这条测的是**接起来还对不对**。

跑法(**先停守护进程**, 两个进程同时 `Overlay()` 会把板子卡死):

    sudo systemctl stop pynq-status
    cd /home/xilinx/overlays/awg_merged && sudo python3 p7_e2e.py
    sudo systemctl restart pynq-status

⚠ 面板**每点一下都是真的写到 PL 寄存器**, 所以这个脚本会把 CH1 改掉(最后停在方波
  250 kHz)。跑完想回默认就重启守护进程, 或者用串口 `SET` 改回去。

退出码 0 = 全过。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(ok, what, detail=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", what, ("  | " + detail) if detail else ""))
    if not ok:
        FAILED.append(what)
    return ok


def _render(screen, state, n=3):
    """连续渲 n 帧。

    **n=3 不是随手写的**: 输入是在一帧的**末尾**收的, 而可见性在**开头**设 —— 所以切页
    的那一帧结束时, 屏上机件的可见性仍是旧页的。第三帧才让新页的控件真正"可点"
    (第一次渲染按下的那一帧、第二次松手切了页、第三次可见性才跟上)。
    """
    for _ in range(n):
        screen.render(state)


def _tap(screen, state, w):
    """走**真实命中路径**点一个控件的中心: 按下 + 抬起。"""
    import pynq_lcd_ui as ui
    x0, y0, x1, y1 = w.box
    x, y = (x0 + x1) // 2, (y0 + y1) // 2
    hit = screen.view.ui._hit(x, y)
    if hit is not w:
        return _tap_miss(screen, state, w, x, y, hit)
    screen.view.ui.source = ui.ScriptedInput([(x, y, True), (x, y, False)])
    _render(screen, state)
    return True


def _tap_miss(screen, state, w, x, y, hit):
    """点不到就**报出来再点**, 而不是静默跳过 —— "点了没反应"正是要抓的 bug。

    仍然继续把点发进去: 若底层真的因此改动了, 后面的断言会看出"点到了别的东西"。
    """
    import pynq_lcd_ui as ui
    print("      !! (%d,%d) 命中的是 %s, 不是 %s"
          % (x, y, type(hit).__name__, type(w).__name__))
    screen.view.ui.source = ui.ScriptedInput([(x, y, True), (x, y, False)])
    _render(screen, state)
    return False


def _wait(screen, state, cond, seconds=10.0):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        screen.render(state)
        time.sleep(0.02)
    return cond()


def main():
    from pynq import Clocks, Overlay

    import pynq_status as D
    import ui_model as M

    print("== 建面(与守护进程同一条路径) ==")
    ov = Overlay(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "awg_merged.bit"))
    Clocks.fclk0_mhz = 50.0
    screen = D.attach_screen(ov)
    if screen is None or getattr(screen, "session", None) is None:
        print("FAIL  面板降级成退路屏了 —— 控制面没建起来")
        return 1
    app, view, session = screen.view.app, screen.view, screen.session
    state = D.Status()

    # ---- 0. 开机回读 ----
    ok = _wait(screen, state, lambda: app.connected and app.fresh)
    check(ok, "开机回读: 草稿与设备一致", "connected=%s fresh=%s" % (app.connected, app.fresh))
    check(not app.dirty, "开机时没有待应用参数")
    ch = app.selected_channel

    # ---- 1. 导航: 状态页 -> 波形页(真实命中路径) ----
    check(app.page == M.PAGE_STATUS, "开机停在状态页", app.page)
    nav = [w for w in view.ui.widgets if getattr(w, "name", None) == "波形"
           and getattr(w, "icon", None) == "~"]
    check(len(nav) == 1, "在屏上找到了「波形」导航项")
    if nav:
        _tap(screen, state, nav[0])
    check(app.page == M.PAGE_AWG, "点导航切到了波形页", app.page)

    # ---- 2. 数字键盘: 频率 100000 -> 250000(点 DEL 六下再输) ----
    row = view.awg_rows["freq_hz"]
    _tap(screen, state, row)
    check(view.keypad.visible, "点频率行弹出了键盘", "value=%r" % view.keypad.value)
    keys = dict((b.text, b) for b in view.keypad.buttons)
    for _ in range(len(view.keypad.value)):
        _tap(screen, state, keys["DEL"])
    for d in "250000":
        _tap(screen, state, keys[d])
    check(view.keypad.value == "250000", "键盘里输出了 250000", view.keypad.value)
    _tap(screen, state, view.keypad.ok)
    check(not view.keypad.visible, "确认之后键盘关掉了")
    check(app.draft["awg"][ch]["freq_hz"] == 250000, "草稿频率变成了 250000",
          str(app.draft["awg"][ch]["freq_hz"]))

    # ---- 3. 波形按钮: 点"方波" ----
    i = list(M.WAVES).index("SQUARE")
    check(_tap(screen, state, view.awg_wave_btns[i]), "点到了「方波」按钮")
    check(app.draft["awg"][ch]["wave"] == "SQUARE", "草稿波形变成了 SQUARE",
          app.draft["awg"][ch]["wave"])
    check(app.dirty, "改了草稿 -> 有待应用参数")

    # ---- 4. 应用设置 -> 事务线程 -> 真寄存器回读 ----
    apply_btn = [b for b in view.ui.widgets if getattr(b, "text", None) == "应用设置"]
    check(len(apply_btn) == 1, "在屏上找到了「应用设置」")
    if apply_btn:
        _tap(screen, state, apply_btn[0])
    ok = _wait(screen, state, lambda: not app.pending)
    check(ok, "事务在超时前结束", "pending=%s" % app.pending)
    check(app.last_result == M.OK, "应用结果 = OK", "%s / %s" % (app.last_result, app.last_message))
    check(not app.dirty, "应用成功后草稿与设备一致")

    # ---- 5. **真寄存器**回读(不是影子状态) ----
    regs = session.ctl.read_registers()["awg"][ch]
    check(regs["freq_hz"] == 250000 and regs["wave"] == "SQUARE",
          "PL 寄存器里就是 250000 / SQUARE", str(regs))

    # ---- 6. 串口侧 GET 读回一致(同一个 AwgCtl: 面板与串口共用) ----
    line = session.ctl.handle("GET CH1")
    check("FREQ 250000" in line and "WAVE SQUARE" in line,
          "串口 GET CH1 读回一致", line.strip())

    print("")
    if FAILED:
        print("**%d 项 FAIL**: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
