#!/usr/bin/env python3
"""临时联调工具:示波器实测 vs GUI 预览(同时间窗口)形状对比。

用法: python tools/scope_compare.py [COMx]
依赖: 板卡在线 + 示波器 USB-TMC + host/siggen(import 前把 host 加入 sys.path)。
输出: 示波器/GUI 波形 CSV + 对比 PNG + 量化指标(包络相关/过零/峰值)。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pyvisa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
SCOPE = "<VISA_RESOURCE>"


def main():
    import serial

    # visa32.dll 在 ResourceManager() 时才加载: 必须先建立 VISA 会话,
    # 再 import pyqtgraph(siggen.preview), 否则 Qt DLL 冲突导致 VI_ERROR_LIBRARY_NFOUND
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(SCOPE)
    inst.timeout = 5000
    from siggen.preview import make_plot_data, PERIODS  # noqa: F401

    ser = serial.Serial(PORT, 115200, timeout=0.5)
    time.sleep(0.3)
    ser.reset_input_buffer()

    def raw(cmd):
        ser.write((cmd + "\r\n").encode())
        time.sleep(0.05)
        ser.reset_input_buffer()

    def line(cmd):
        ser.write((cmd + "\r\n").encode())
        parts = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                parts.append(chunk)
                if b"\n" in chunk:
                    break
        return b"".join(parts).decode(errors="replace").strip()

    # 复位调制状态
    for ch in ("CH1", "CH2"):
        raw(f"SET {ch} MODE OFF")
        raw(f"SET {ch} MODWAVE SINE")
        raw(f"SET {ch} MODFREQ 10")
        raw(f"SET {ch} AMDEPTH 100")
        raw(f"SET {ch} FMDEV 0")
        raw(f"SET {ch} PMDEV 0")

    def scope_acq(tim_scal, chan_scal):
        inst.write(":STOP")
        inst.write(":WAV:SOUR CHAN1")
        inst.write(":WAV:FORM BYTE")
        inst.write(":WAV:POIN:MODE NORM")
        inst.write(":MEAS:SOUR CHAN1")
        inst.write(":TIM:SCAL %.9E" % tim_scal)
        inst.write(":CHAN1:SCAL %.9E" % chan_scal)
        inst.write(":RUN")
        time.sleep(0.8)
        inst.write(":STOP")

    def wav_read():
        xinc = float(inst.query(":WAV:XINC?"))
        data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
        inst.write(":RUN")
        y = (np.array(data, dtype=np.uint8).astype(float) - 128.0) / 128.0
        return xinc, y

    # 场景: AM 载波 10kHz + 调制 1kHz 深度 100% AMPL 100
    # GUI 自适应窗口 = 10 载波周期 = 1ms = 1 完整调制周期; 示波器同窗口 100us/div
    cmds = [
        "SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100", "SET CH1 BASE 0",
        "SET CH1 MODE AM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10000",
        "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 0",
    ]
    for c in cmds:
        r = line(c)
        if r != "OK":
            print(f"board rejected {c}: {r}")
            return 1
    print("board set:", line("GET CH1"))

    scope_acq(100e-6, 2.0)
    xinc, y_scope = wav_read()

    # GUI 参考波形(自适应窗口 1ms)
    mod = {"mode": "AM", "mwave": "SINE", "mfreq": 1000.0,
           "am_depth": 100, "fm_dev": 0, "pm_dev": 0}
    t_ref, y_ref = make_plot_data("SINE", 10000, 100, 100, n=600, mod=mod)
    total_s = t_ref[-1] + t_ref[1] - t_ref[0]

    t_scope = np.arange(len(y_scope)) * xinc
    win = t_scope <= total_s
    t_scope, y_scope = t_scope[win], y_scope[win]

    # 包络(FFT 希尔伯特瞬时幅度)对比。
    # 关键: 示波器包络点密度远高于 GUI(62500 vs 600 点), 必须先重采样到
    # GUI 时间轴再对齐; 调制相位任意, 用环形偏移(周期=1 调制周期)找最佳相关。
    def hilbert_env(x):
        n = len(x)
        h = np.zeros(n)
        h[0] = 1
        h[1:(n + 1) // 2] = 2
        if n % 2 == 0:
            h[n // 2] = 1
        return np.abs(np.fft.ifft(np.fft.fft(x) * h))

    env_scope = np.interp(t_ref, t_scope, hilbert_env(y_scope))
    env_gui = hilbert_env(y_ref)
    best = (0, -1)
    for sh in range(-len(env_gui) + 1, len(env_gui)):
        e = np.roll(env_scope, sh)
        c = float(np.corrcoef(e, env_gui)[0, 1])
        if c > best[1]:
            best = (sh, c)
    env_al = np.roll(env_scope, best[0])

    print("== AM 对比 ==")
    print(f"scope pts={len(y_scope)} xinc={xinc*1e6:.3f}us  gui pts={len(y_ref)}")
    print(f"scope Vpp(归一化)={y_scope.max()-y_scope.min():.3f}  gui={y_ref.max()-y_ref.min():.3f}")
    print(f"环形对齐偏移={best[0]}点 相关={best[1]:.3f}")
    # 峰归一化对比形状(量级基准不同: 示波器 ±0.46=满摆幅, GUI ±1=满摆幅)
    es_n = env_al / env_al.max()
    eg_n = env_gui / env_gui.max()
    err = np.abs(es_n - eg_n)
    print(f"峰归一化平均绝对误差={err.mean():.3f}  最大={err.max():.3f}")
    bad = np.where(err > 0.15)[0]
    if len(bad):
        print(f"误差>0.15 的位置占比={len(bad)/len(err)*100:.0f}%  时间区间={t_ref[bad.min()]*1e6:.0f}~{t_ref[bad.max()]*1e6:.0f}us")
    print(f"判定: {'形状一致(相关>0.95)' if best[1] > 0.95 else '形状存在差异!'}")
    shift = best[0]

    # 保存 CSV + PNG
    np.savetxt(ROOT / "tools" / "cmp_scope_am.csv",
               np.column_stack([t_scope, y_scope]), delimiter=",",
               header="t_s,y_scope", comments="")
    np.savetxt(ROOT / "tools" / "cmp_gui_am.csv",
               np.column_stack([t_ref, y_ref]), delimiter=",",
               header="t_g,y_ref", comments="")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(12, 7))
        ax[0].plot(t_scope * 1e6, y_scope, label="scope", lw=0.8)
        ax[0].plot(t_ref * 1e6, y_ref, label="gui", lw=1.2, alpha=0.8)
        ax[0].set_title("AM 10kHz+1kHz 100%: 同窗口对比")
        ax[0].legend()
        ax[1].plot(t_ref * 1e6, env_gui, label="gui env")
        ax[1].plot(t_ref * 1e6, env_al, label="scope env (aligned)")
        ax[1].legend()
        fig.tight_layout()
        fig.savefig(ROOT / "tools" / "cmp_am.png", dpi=100)
        print("PNG:", ROOT / "tools" / "cmp_am.png")
    except ImportError:
        pass

    inst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
