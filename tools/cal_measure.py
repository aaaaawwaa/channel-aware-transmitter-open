#!/usr/bin/env python3
"""电压校准数据采集: 示波器实测各波形/幅度档位的 Vpp/RMS。

用法: python tools/cal_measure.py [COMx]
输出: tools/cal_data.csv + 控制台表格(设置值 vs 实测值, 供校准模型分析)。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pyvisa

ROOT = Path(__file__).resolve().parent.parent
PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
SCOPE = "<VISA_RESOURCE>"

# (名称, 通道, 波形, 频率Hz, 幅度%, 时基s/div, 垂直V/div, 基波BASE%)
CASES = [
    ("SQUARE 10k 100%", 1, "SQUARE", 10000, 100, 20e-6, 2.0, 0),
    ("SINE 10k 100%", 1, "SINE", 10000, 100, 20e-6, 2.0, 0),
    ("SINE 100k 100%", 1, "SINE", 100000, 100, 2e-6, 2.0, 0),
    ("TRIANGLE 10k 100%", 1, "TRIANGLE", 10000, 100, 20e-6, 2.0, 0),
    ("SAW 10k 100%", 1, "SAW", 10000, 100, 20e-6, 2.0, 0),
    # 细粒度幅度扫描(10%~100% 每 10%, 正弦 + 方波)——校准模型拟合用
    ("SINE 10k 90%", 1, "SINE", 10000, 90, 20e-6, 2.0, 0),
    ("SINE 10k 80%", 1, "SINE", 10000, 80, 20e-6, 2.0, 0),
    ("SINE 10k 70%", 1, "SINE", 10000, 70, 20e-6, 1.0, 0),
    ("SINE 10k 60%", 1, "SINE", 10000, 60, 20e-6, 1.0, 0),
    ("SINE 10k 50%", 1, "SINE", 10000, 50, 20e-6, 1.0, 0),
    ("SINE 10k 40%", 1, "SINE", 10000, 40, 20e-6, 1.0, 0),
    ("SINE 10k 30%", 1, "SINE", 10000, 30, 20e-6, 0.5, 0),
    ("SINE 10k 20%", 1, "SINE", 10000, 20, 20e-6, 0.5, 0),
    ("SINE 10k 10%", 1, "SINE", 10000, 10, 20e-6, 0.2, 0),
    ("SQUARE 10k 90%", 1, "SQUARE", 10000, 90, 20e-6, 2.0, 0),
    ("SQUARE 10k 80%", 1, "SQUARE", 10000, 80, 20e-6, 2.0, 0),
    ("SQUARE 10k 70%", 1, "SQUARE", 10000, 70, 20e-6, 1.0, 0),
    ("SQUARE 10k 60%", 1, "SQUARE", 10000, 60, 20e-6, 1.0, 0),
    ("SQUARE 10k 50%", 1, "SQUARE", 10000, 50, 20e-6, 1.0, 0),
    ("SQUARE 10k 40%", 1, "SQUARE", 10000, 40, 20e-6, 1.0, 0),
    ("SQUARE 10k 30%", 1, "SQUARE", 10000, 30, 20e-6, 0.5, 0),
    ("SQUARE 10k 20%", 1, "SQUARE", 10000, 20, 20e-6, 0.5, 0),
    ("SQUARE 10k 10%", 1, "SQUARE", 10000, 10, 20e-6, 0.2, 0),
    # 基波模式(谐波路径, 无谐波): BASE 扫描, AMPL=100 —— "拖动基波条"场景
    ("SINE 10k BASE 90%", 1, "SINE", 10000, 100, 20e-6, 2.0, 90),
    ("SINE 10k BASE 70%", 1, "SINE", 10000, 100, 20e-6, 1.0, 70),
    ("SINE 10k BASE 50%", 1, "SINE", 10000, 100, 20e-6, 1.0, 50),
    ("SINE 10k BASE 30%", 1, "SINE", 10000, 100, 20e-6, 0.5, 30),
    ("SINE 10k BASE 10%", 1, "SINE", 10000, 100, 20e-6, 0.2, 10),
]


def main():
    import serial

    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(SCOPE)
    inst.timeout = 5000
    ser = serial.Serial(PORT, 115200, timeout=0.5)
    time.sleep(0.3)
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

    # 复位两通道(必须清谐波: 残留 HARM 会让 SINE 用例输出谐波, 频率/幅度全错)
    for ch in ("CH1", "CH2"):
        for c in (f"SET {ch} WAVE SINE", f"SET {ch} FREQ 1000", f"SET {ch} AMPL 50",
                  f"SET {ch} HARM1 ORDER 0", f"SET {ch} HARM1 AMPL 0",
                  f"SET {ch} HARM2 ORDER 0", f"SET {ch} HARM2 AMPL 0",
                  f"SET {ch} HARM3 ORDER 0", f"SET {ch} HARM3 AMPL 0",
                  f"SET {ch} HARM4 ORDER 0", f"SET {ch} HARM4 AMPL 0",
                  f"SET {ch} BASE 0", f"SET {ch} MODE OFF"):
            line(c)

    rows = []
    for name, ch, wave, freq, ampl, tim, chan, base in CASES:
        chn = f"CH{ch}"
        cmds = [f"SET {chn} WAVE {wave}", f"SET {chn} FREQ {freq}",
                f"SET {chn} AMPL {ampl}", f"SET {chn} BASE {base}", f"SET {chn} MODE OFF"]
        for c in cmds:
            r = line(c)
            if r != "OK":
                print(f"board rejected {c}: {r}")
                return 1
        src = f"CHAN{ch}"
        inst.write(":STOP")
        inst.write(":MEAS:SOUR " + src)
        inst.write(f":TIM:SCAL {tim:.9E}")
        inst.write(f":CHAN{ch}:SCAL {chan:.9E}")
        inst.write(":RUN")
        time.sleep(0.6)
        inst.write(":STOP")
        time.sleep(0.2)
        vpp = float(inst.query(":MEAS:VPP?"))
        vrms = float(inst.query(":MEAS:VRMS?"))
        fmeas = float(inst.query(":MEAS:FREQ?"))
        rows.append((name, freq, ampl, vpp, vrms, fmeas))
        print(f"{name:24s} 设定 {ampl:3d}%  freq {freq:6d}  ->  实测 Vpp={vpp:6.3f}V  RMS={vrms:6.3f}V  f={fmeas:7.1f}")

    import csv
    with open(ROOT / "tools" / "cal_data.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "set_freq", "set_ampl", "vpp", "vrms", "fmeas"])
        w.writerows(rows)
    print("\nCSV:", ROOT / "tools" / "cal_data.csv")
    inst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
