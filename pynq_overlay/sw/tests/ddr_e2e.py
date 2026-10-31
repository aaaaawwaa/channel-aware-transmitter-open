#!/usr/bin/env python3
"""数据页板上验收: **真实触摸命中路径** -> 面板 -> 环形线程 -> DDR PP -> PL 寄存器判据。

它调守护进程**同一个** `attach_screen(overlay)` —— 也就是 `pynq-status` 开机走的那条建面
路径。所以在它身上通过, 等价于"守护进程起来之后的那套东西是对的"。

判据链(每一段都是硬件级, 不是自证):

  1. **文件表**列得出 `/home/xilinx/waveforms/` 里的两个 `.bin`(点「刷新」)。
  2. 点一行 -> 「入队」-> 队列里有它(`*` 标记)。
  3. `MODE OFF` 下点「装填」-> bank0 的 `valid`/`bit_length` **从 PL 读回**变成 4096 B。
  4. 打开数字调制(QPSK @100kSym/s)后点「启动」-> 播放起来。
  5. 环形喂数: `bank1` 被自动装填 -> `server.stats["switches"]` 增长, 且每次切换后
     `pl_view()` 解出的 DMA 源地址 bank **等于**软件账面的 `active_bank`。
  6. `GET DDR PP` 走**串口桥**回真值(环形线程把快照推给 `AwgCtl`), ACTIVE_BANK 观测到交替。
  7. 收尾: 停止 -> 状态回 IDLE/ERROR 00, **underflow 干净**(从 PL 的 `axi_gpio_8` 读)。
  8. 全程串口桥不挂: 中途 `GET CH1` 照常回。

跑法(**先停守护进程**, 两个进程同时 `Overlay()` 会把板子卡死):

    sudo systemctl stop pynq-status
    cd /home/xilinx/overlays/awg_merged && sudo python3 ddr_e2e.py
    sudo systemctl restart pynq-status

⚠ 会把 CH1/CH2 与数字调制改掉(最后停在 QPSK 播放过一遍的状态)。跑完想回默认就重启守护进程。

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
    """连续渲 n 帧 —— 输入在一帧**末尾**收、可见性在**开头**设, 所以切页要第三帧才可点。"""
    for _ in range(n):
        screen.render(state)


def _tap(screen, state, w):
    import pynq_lcd_ui as ui
    x0, y0, x1, y1 = w.box
    x, y = (x0 + x1) // 2, (y0 + y1) // 2
    hit = screen.view.ui._hit(x, y)
    if hit is not w:
        print("      !! (%d,%d) 命中的是 %s, 不是 %s"
              % (x, y, type(hit).__name__, type(w).__name__))
    screen.view.ui.source = ui.ScriptedInput([(x, y, True), (x, y, False)])
    _render(screen, state)
    return hit is w


def _tap_cell(screen, state, table, row):
    """点文件表某一行(格子本身可点, 不预建控件)。"""
    import pynq_lcd_ui as ui
    box = table._cell_box(row, 0)
    x, y = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    hit = screen.view.ui._hit(x, y)
    if hit is not table:
        print("      !! 表格格子命中的是 %s" % type(hit).__name__)
    screen.view.ui.source = ui.ScriptedInput([(x, y, True), (x, y, False)])
    _render(screen, state)
    return hit is table


def _wait(screen, state, cond, seconds=10.0):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        screen.render(state)
        time.sleep(0.02)
    return cond()


def _chip(view, text):
    for w in view.ui.widgets:
        if getattr(w, "text", None) == text and getattr(w, "box", None):
            return w
    return None


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

    ring = session.ring
    if ring is None:
        print("FAIL  环形线程没起来 —— 数据页会降级成「DDR 不可用」")
        return 1
    print("   ring thread up")

    # ---- 0. 开机回读 ----
    ok = _wait(screen, state, lambda: app.connected and app.fresh)
    check(ok, "开机回读: 草稿与设备一致",
          "connected=%s fresh=%s" % (app.connected, app.fresh))

    # ---- 1. 切到数据页(真实命中路径) ----
    nav = [w for w in view.ui.widgets if getattr(w, "name", None) == "数据"
           and getattr(w, "icon", None) == "="]
    check(len(nav) == 1, "在屏上找到了「数据」导航项", "找到 %d 个" % len(nav))
    if nav:
        _tap(screen, state, nav[0])
    check(app.page == M.PAGE_DDR, "点导航切到了数据页", app.page)

    # ---- 2. 刷新: 列出本地波形文件 ----
    refresh = _chip(view, "刷新")
    check(refresh is not None, "在屏上找到了「刷新」")
    if refresh:
        _tap(screen, state, refresh)
    ok = _wait(screen, state, lambda: len(app.ddr_files) >= 2)
    names = [n for n, _s in app.ddr_files]
    check(ok, "刷新列出了本地文件", repr(names))
    check("waveA.bin" in names and "waveB.bin" in names,
          "两个 .bin 都在表里", repr(names))

    # ---- 3. 点一行 -> 入队 ----
    _tap_cell(screen, state, view.ddr_table, 0)
    check(app.ddr_selected == 0, "点表格第 1 行选中了它", str(app.ddr_selected))
    enq = _chip(view, "入队")
    if enq:
        _tap(screen, state, enq)
    check(app.ddr_queue == ["waveA.bin"], "队列里有了 waveA.bin", repr(app.ddr_queue))

    # ---- 4. MODE OFF 下装填(数据页唯一被 MODE 挡住的动作) ----
    # 先确保设备在 MODE OFF
    if app.running:
        tog = _chip(view, "关闭数字调制")
        if tog:
            _tap(screen, state, tog)
        _wait(screen, state, lambda: not app.running)
    check(not app.running, "装填前设备在 MODE OFF", "actual_mode=%s" % app.actual_mode)

    load = _chip(view, "装填")
    check(load is not None, "在屏上找到了「装填」")
    check(load.enabled, "MODE OFF + 有队列 -> 装填可点", "status=%r" % app.ddr_status)
    if load:
        _tap(screen, state, load)
    ok = _wait(screen, state,
               lambda: (app.pp or {}).get("bank0_len") or (app.pp or {}).get("bank1_len"),
               seconds=15.0)
    check(ok, "装填后 bank 里有数据(从快照读回)",
          "B0=%s B1=%s" % ((app.pp or {}).get("bank0_len"),
                           (app.pp or {}).get("bank1_len")))

    # ---- 5. 打开环形 -> 打开数字调制 -> 启动 ----
    # 环形开关(点面板上的 chip, 走真实命中路径)
    ring_btn = _chip(view, "环形: 关")
    check(ring_btn is not None, "在屏上找到了「环形」开关")
    if ring_btn:
        _tap(screen, state, ring_btn)
    check(app.ddr_ring, "环形打开了", repr(app.ddr_ring))

    # ⚠ 符号率**必须调低**: 两个测试文件只有 4/8 KB, QPSK 100kSym/s 下每块只够播
    #   0.16 s —— 而一次 SD 读+flush 就要几十~200 ms, 环形来不及喂下一块就会 underflow。
    #   10kSym/s 给到 1.6 s/块, 与 p5b 的余量同量级。这条走串口线(面板与串口共用同一个
    #   `ctl`), 与 p5b 的做法一致 —— 它是本页的**前置条件**, 不是被测对象。
    print("   " + session.ctl.handle("SET DIGMOD SYMRATE 10000").strip())
    tog = _chip(view, "启动数字调制")
    check(tog is not None, "在屏上找到了「启动数字调制」")
    if tog:
        _tap(screen, state, tog)
    ok = _wait(screen, state, lambda: app.running)
    check(ok, "数字调制起来了", "actual_mode=%s" % app.actual_mode)

    start = _chip(view, "启动")
    check(start is not None, "在屏上找到了「启动」")
    check(start.enabled, "有数据 + 非 OFF + 未在播 -> 启动可点",
          "status=%r" % app.ddr_status)
    if start:
        _tap(screen, state, start)
    ok = _wait(screen, state, lambda: (app.pp or {}).get("active"), seconds=10.0)
    check(ok, "DDR 播放起来了", "state=%s" % (app.pp or {}).get("state"))

    # ---- 5b. `GET DDR PP` 走**串口桥**回真值 ----
    # 这是计划 Phase 5 验收原文的一条。桥不许碰 `DdrPp`(跨线程), 只能看环形线程推给
    # `AwgCtl` 的快照 —— 所以这条同时验证了那条推送链。修之前这里恒回基线零值
    # (`ACTIVE 0 STATE IDLE`), 环形在播时是假话。
    line = session.ctl.handle("GET DDR PP")
    check("ACTIVE 1" in line, "串口 GET DDR PP 报出真的在播", line.strip())
    check("STATE IDLE" not in line, "串口不再回基线桩", line.strip())

    # ---- 6. 环形: 等 bank1 被自动喂上 + 观察切换 ----
    #
    # 判据用**快照自己**的两个字段: `active_bank`(软件账面, 来自 `status_from_pp`)与
    # `src_bank`(PL 侧从 DMA 源地址解出, 来自 `pl_view()`)—— 两者在 `_publish` 里背靠背
    # 读出来, 天然同拍。不成立就说明"我们切的是账面上的 bank, 硬件读的是别处"。
    # 同时按计划原文, 用**串口** `GET DDR PP` 观测 ACTIVE_BANK 交替。
    print("== 环形喂数(观测 %ds) ==" % 12)
    pairs = {}
    serial_banks = set()
    end = time.time() + 12.0
    while time.time() < end:
        screen.render(state)
        time.sleep(0.02)
        snap = app.pp or {}
        ab = snap.get("active_bank")
        if ab not in (None, "NONE"):
            pairs.setdefault(int(ab), snap.get("src_bank"))
        sl = session.ctl.handle("GET DDR PP")
        if "ACTIVE_BANK " in sl:
            serial_banks.add(sl.split("ACTIVE_BANK ", 1)[1].split()[0])
    snap = app.pp or {}
    check(snap.get("fed", 0) >= 1, "环形自动喂了至少一块", "fed=%s" % snap.get("fed"))
    check((snap.get("bank1_len") or 0) > 0, "bank1 被环形装上了数据",
          "B0=%s B1=%s" % (snap.get("bank0_len"), snap.get("bank1_len")))
    check(len(pairs) >= 2, "两个 bank 都活动过", "见过 %r" % sorted(pairs))
    for ab in sorted(pairs):
        check(pairs[ab] == ab, "PL 侧 DMA 源地址解出的 bank == 账面 active_bank",
              "账面 bank%d / PL 解出 %r" % (ab, pairs[ab]))
    check(len(serial_banks) >= 2, "串口 GET DDR PP 观测到 ACTIVE_BANK 交替",
          "见过 %r" % sorted(serial_banks))

    # ---- 7. 收尾: 停止 -> IDLE / ERROR 00 / underflow 干净 ----
    stop = _chip(view, "停止")
    check(stop is not None, "在屏上找到了「停止」")
    if stop:
        _tap(screen, state, stop)
    ok = _wait(screen, state, lambda: not (app.pp or {}).get("active"), seconds=10.0)
    snap = app.pp or {}
    check(ok, "停止后不再活动", "state=%s" % snap.get("state"))
    check(snap.get("error_code", 1) == 0, "收尾错误码 00",
          "0x%02X" % snap.get("error_code", -1))

    # underflow 是 PL 的硬件裁决位(`axi_gpio_8` bit2)。它**不在** `GET DDR PP` 的回包里
    # —— 早先这里判的是 `"UNDERFLOW" not in line`, 而那条回包压根没有这个字段, 所以是条
    # 恒真的空断言。真值只能从 `DdrPp._user_status()` 读(与 p5b_ringtest 同一条路)。
    uf = ring.pp._user_status()
    check(not uf["underflow"], "underflow 干净(从 PL 读回)", repr(uf))

    # ---- 8. 串口桥全程不挂 ----
    ch = session.ctl.handle("GET CH1")
    check("WAVE" in ch and "FREQ" in ch, "串口 GET CH1 照常回(桥没挂)", ch.strip()[:60])

    print("")
    if FAILED:
        print("**%d 项 FAIL**: %s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())