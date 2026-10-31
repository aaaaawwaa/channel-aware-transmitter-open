#!/usr/bin/env python3
"""P0 金标采集: 把"命令 -> 原始回包"逐条记下来, 供 P1 的 Python 控制库离线对照。

为什么要它: 合流之后控制语义要由 Python 重写一遍(`pynq_overlay/sw/awg_ctl.py`)。
判据有两条, 缺一不可:
  (a) 板上那两套套件(`test_bridge.py` / `scope_verify.py`) —— 端到端, 需要板子与示波器;
  (b) **这份金标** —— 不需要板子、不需要串口, 让 Python 库在没有硬件的环境里也能被验收。
所以采集的是**固件的原始回包原文**(它自己把影子状态序列化给主机看), 不做任何加工。

做法: 先发一遍 `QUIET` 把器件压到确定的默认态(**采集可复现**), 然后逐个"场景"
(几条 SET)之后把 `GETS` 里的状态全部读一遍; 末尾再压一遍 `QUIET`
(**不给后来的人留污染** —— 信道损伤那一级是相加的, 之前被残留的 IMPULSE 坑过一次)。

跑法:
    python scripts/capture_golden.py COM11 docs/golden/command_replies.json
"""
import json
import os
import subprocess
import sys
import time

import serial

BAUD = 115200
TIMEOUT = 0.6

# 压到确定默认态。**顺序有讲究**: 先把模式关掉再改参数(有些参数只在 MODE OFF 时可改),
# 信道损伤先清数值再 COMMIT(开关位走 GPIO 立即生效, 数值走 BRAM 要 COMMIT 才锁存)。
QUIET = [
    "SET CH1 MODE OFF", "SET CH2 MODE OFF",
    # DIGMOD 的 FILTER/ROLLOFF/SEED 只能在 STOPPED 时改, 所以紧跟 MODE OFF 复位成固件默认值
    "SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT", "SET DIGMOD ROLLOFF 0.35",
    "SET DIGMOD SEED 127",
    "SET CH1 WAVE SINE", "SET CH2 WAVE SINE",
    "SET CH1 FREQ 1000", "SET CH2 FREQ 1000",
    "SET CH1 AMPL 50", "SET CH2 AMPL 50",
    "SET CH1 DUTY 50", "SET CH2 DUTY 50",
    "SET CH1 BASE 100", "SET CH2 BASE 100",
    "SET CH2 PHASE 0", "SET CH2 PHASELINK OFF",
    # 谐波要 ORDER 与 AMPL 都清 —— 只清 ORDER 会留下 "H1 0 19" 这种半个复位
    "SET CH1 HARM1 ORDER 0", "SET CH1 HARM1 AMPL 0",
    "SET CH1 HARM2 ORDER 0", "SET CH1 HARM2 AMPL 0",
    "SET CH1 HARM3 ORDER 0", "SET CH1 HARM3 AMPL 0",
    "SET CH1 HARM4 ORDER 0", "SET CH1 HARM4 AMPL 0",
    "SET CH2 HARM1 ORDER 0", "SET CH2 HARM1 AMPL 0",
    "SET CH2 HARM2 ORDER 0", "SET CH2 HARM2 AMPL 0",
    "SET CH2 HARM3 ORDER 0", "SET CH2 HARM3 AMPL 0",
    "SET CH2 HARM4 ORDER 0", "SET CH2 HARM4 AMPL 0",
    "SET CH1 CHAN OFF", "SET CH2 CHAN OFF",
    "SET CH1 NOISE 0", "SET CH2 NOISE 0",
    "SET CH1 TAP CLEAR", "SET CH2 TAP CLEAR",
    "SET CH1 CHAN TONE 0 0", "SET CH2 CHAN TONE 0 0",
    "SET CH1 CHAN IMPULSE 1 1 0", "SET CH2 CHAN IMPULSE 1 1 0",
    "SET CH1 CHAN COMMIT", "SET CH2 CHAN COMMIT",
    "SET IQIMP OFF", "SET IQIMP COMMIT",
    # IQIMP 的四个参数也要压回固件上电默认值 —— 只发 OFF 的话, 金标前半段会把"上一轮留下的
    # PHASE/GAIN/FREQOFF/PHASEERR"当成默认态记下来, 那是**复现不了的历史状态**
    # (P1 的 Python 库一上来就会对不上; 实测正是这样抓到 37 条 mismatch)。
    "SET IQIMP PHASE 0", "SET IQIMP FREQOFF 0", "SET IQIMP GAIN 16384",
    "SET IQIMP PHASEERR 0",
    # DIGMOD 同理: SYMRATE 不在 MODE 之下, 漏了它就会把上一轮的 50000 当默认值
    "SET DIGMOD SYMRATE 10000",
    "SET CH1 MODWAVE SINE", "SET CH2 MODWAVE SINE",
    "SET CH1 MODFREQ 10", "SET CH2 MODFREQ 10",
    "SET CH1 AMDEPTH 100", "SET CH2 AMDEPTH 100",
    "SET CH1 FMDEV 0", "SET CH2 FMDEV 0",
    "SET CH1 PMDEV 0", "SET CH2 PMDEV 0",
]

# 每组场景 = (名字, [命令...]); 之后总是把 GETS 全读一遍
SCENARIOS = [
    ("正弦默认", ["SET CH1 WAVE SINE", "SET CH1 FREQ 100000", "SET CH1 AMPL 80"]),
    ("方波占空比", ["SET CH1 WAVE SQUARE", "SET CH1 DUTY 25"]),
    ("三角/锯齿", ["SET CH1 WAVE TRIANGLE", "SET CH1 FREQ 5000"]),
    ("脉冲占空比", ["SET CH1 WAVE PULSE", "SET CH1 DUTY 12"]),
    ("噪声", ["SET CH1 WAVE NOISE", "SET CH1 FREQ 50000"]),
    ("谐波", ["SET CH1 BASE 60", "SET CH1 HARM1 ORDER 3", "SET CH1 HARM1 AMPL 30",
              "SET CH1 HARM2 ORDER 2", "SET CH1 HARM2 AMPL 15"]),
    ("AM", ["SET CH1 MODE AM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 1000",
            "SET CH1 AMDEPTH 60"]),
    ("FM", ["SET CH1 MODE FM", "SET CH1 MODFREQ 1000", "SET CH1 FMDEV 5000"]),
    ("PM", ["SET CH1 MODE PM", "SET CH1 MODFREQ 500", "SET CH1 PMDEV 300"]),
    ("扫频", ["SET CH1 MODE SWEEP", "SET CH1 MODFREQ 100", "SET CH1 FMDEV 10000"]),
    ("CH2 相位", ["SET CH2 WAVE SINE", "SET CH2 FREQ 100000", "SET CH2 PHASE 9000"]),
    ("CH2 相位联动", ["SET CH2 PHASELINK ON", "SET CH2 PHASE 18000"]),
    ("两通道各自调制", ["SET CH1 MODE FM", "SET CH1 FMDEV 5000",
                        "SET CH2 MODE AM", "SET CH2 AMDEPTH 80"]),
    ("信道噪声", ["SET CH1 NOISE 30", "SET CH1 CHAN ON"]),
    ("信道多径抽头", ["SET CH1 TAP 0 255 0", "SET CH1 TAP 1 255 50",
                      "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"]),
    ("信道单音", ["SET CH1 CHAN TONE 1000000 20", "SET CH1 CHAN ON",
                  "SET CH1 CHAN COMMIT"]),
    ("信道冲激", ["SET CH1 CHAN IMPULSE 1000 8 30", "SET CH1 CHAN ON",
                  "SET CH1 CHAN COMMIT"]),
    ("IQ 单位阵", ["SET IQIMP PHASE 0", "SET IQIMP FREQOFF 0", "SET IQIMP GAIN 0",
                   "SET IQIMP PHASEERR 0", "SET IQIMP ON", "SET IQIMP COMMIT"]),
    ("IQ 相位/增益失配", ["SET IQIMP PHASE 4500", "SET IQIMP GAIN 20000",
                          "SET IQIMP PHASEERR 1200", "SET IQIMP COMMIT"]),
    ("IQ 频偏", ["SET IQIMP FREQOFF 20000", "SET IQIMP COMMIT"]),
    # DIGMOD: FILTER/ROLLOFF/SYMRATE/SEED 只能在 STOPPED 时改, 所以顺序是先参数后 MODE
    ("DIGMOD BPSK/RECT", ["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                          "SET DIGMOD SYMRATE 100000", "SET DIGMOD SEED 5",
                          "SET DIGMOD MODE BPSK"]),
    ("DIGMOD QPSK/RRC 0.2", ["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                             "SET DIGMOD ROLLOFF 0.2", "SET DIGMOD SYMRATE 100000",
                             "SET DIGMOD MODE QPSK"]),
    ("DIGMOD QAM16/RRC 0.35", ["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                               "SET DIGMOD ROLLOFF 0.35", "SET DIGMOD SYMRATE 200000",
                               "SET DIGMOD MODE QAM16"]),
    ("DIGMOD QAM64/RRC 0.5", ["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                              "SET DIGMOD ROLLOFF 0.5", "SET DIGMOD SYMRATE 50000",
                              "SET DIGMOD MODE QAM64"]),
    ("DIGMOD QAM4", ["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                     "SET DIGMOD MODE QAM4"]),
    ("DIGMOD 复位重同步", ["SET DIGMOD MODE OFF", "SET DIGMOD RESET"]),
    # 越界/非法: 错误文案也是契约的一部分
    ("越界: 频率", ["SET CH1 FREQ 1000001"]),
    ("越界: 幅度", ["SET CH1 AMPL 101"]),
    ("越界: 占空比", ["SET CH1 DUTY 101"]),
    ("越界: 相位", ["SET CH2 PHASE 36001"]),
    ("越界: 谐波", ["SET CH1 HARM1 ORDER 21"]),
    ("越界: PM 偏移", ["SET CH1 PMDEV 512"]),
    ("越界: 噪声", ["SET CH1 NOISE 101"]),
    ("越界: 抽头位置", ["SET CH1 TAP 4 255 0"]),
    ("越界: 抽头增益", ["SET CH1 TAP 0 256 0"]),
    ("越界: 符号率", ["SET DIGMOD MODE OFF", "SET DIGMOD SYMRATE 1000001"]),
    ("越界: 滚降", ["SET DIGMOD MODE OFF", "SET DIGMOD ROLLOFF 0.4"]),
    ("越界: seed", ["SET DIGMOD MODE OFF", "SET DIGMOD SEED 200"]),
    ("越界: 模式名", ["SET CH1 MODE ON"]),
    # 名字以"错误"开头的场景本来就该回 ERR, 不计入"非预期"
    ("错误: 未知字段", ["SET CH1 BOGUS 1"]),
    ("错误: 非法波形名", ["SET CH1 WAVE BOGUS"]),
    ("错误: 非数字参数", ["SET CH1 FREQ abc"]),
    ("错误: 不存在的通道", ["SET CH3 FREQ 1000"]),
    ("错误: 缺参数", ["SET CH1 FREQ"]),
    ("错误: 空的 GET", ["GET"]),
]

GETS = [
    "GET CH1", "GET CH2", "GET CH1 CHAN", "GET CH2 CHAN", "GET IQIMP",
    "GET DIGMOD", "GET USERBUF", "GET DDR PP", "GET PLAY", "GET DMA", "GET NET",
]


class Bridge:
    """最小串口客户端: 一行一答。"""

    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=TIMEOUT)
        self.buf = bytearray()
        self.pending = bytearray()
        time.sleep(0.3)

    def line(self, text):
        """发一行, 等一行回来(超时/无回则回 '<无响应>')。"""
        self.ser.reset_input_buffer()
        self.ser.write((text + "\r\n").encode())
        end = time.monotonic() + TIMEOUT
        while time.monotonic() < end:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", "replace").strip()
            if line:
                return line
        return "<无响应>"

    def close(self):
        self.ser.close()


def _md5(path):
    try:
        with open(path, "rb") as fh:
            import hashlib
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return None


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    port, out_path = sys.argv[1], sys.argv[2]
    bridge = Bridge(port)
    if bridge.line("GET CH1") == "<无响应>":
        raise SystemExit("串口上没有桥在应答 —— 先确认位流+固件在跑(见 CLAUDE.md 板级验证流程)")

    cases, unexpected = [], []

    def record(scenario, text):
        reply = bridge.line(text)
        cases.append({"scenario": scenario, "cmd": text, "reply": reply})
        # 越界/错误那几组本来就该回 ERR; 其余地方冒出 ERR 就要报出来
        if reply.startswith("ERR") and not (scenario.startswith("越界")
                                           or scenario.startswith("错误")):
            unexpected.append((scenario, text, reply))
        return reply

    for text in QUIET:
        record("__defaults__", text)
    for name, cmds in SCENARIOS:
        for text in cmds:
            record(name, text)
        for text in GETS:
            record(name, text)
    for text in QUIET:
        record("__restore__", text)
    # 收尾后再读一遍全状态: 这份"规范默认态"要进金标, 让 P1 的库能断言、P3/P4 能对照。
    # (注意 BASE 会被 normalize() 重算: 清谐波后 `BASE 100` 读回来是 70 —— 那是固件的派生结果,
    #  不是残留, Python 库必须照样复现。)
    for text in GETS:
        record("__defaults_state__", text)

    doc = {
        "meta": {
            "port": port,
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "bitstream": "awg/awg.runs/impl_1/host_bd_wrapper.bit",
            "bitstream_md5": _md5("awg/awg.runs/impl_1/host_bd_wrapper.bit"),
            "hwh_md5": _md5("awg/awg.srcs/sources_1/bd/host_bd/hw_handoff/host_bd.hwh"),
            "elf": "sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf",
            "elf_md5": _md5("sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf"),
            "main_c_md5": _md5("sdk/src/main.c"),
        },
        "cases": cases,
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)

    print("采集 %d 条 -> %s" % (len(cases), out_path))
    if unexpected:
        print("!! 有 %d 条非预期 ERR(可能是参数/语法写错):" % len(unexpected))
        for scenario, text, reply in unexpected[:12]:
            print("   [%s] %s -> %s" % (scenario, text, reply))
    else:
        print("默认态/设置命令全部被接受(没有非预期 ERR)")
    bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
