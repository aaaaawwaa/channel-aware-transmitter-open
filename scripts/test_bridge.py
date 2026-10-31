#!/usr/bin/env python3
"""AC820 bridge protocol test: drive the full command set over serial and
assert exact responses. Usage: python scripts/test_bridge.py [COMx]
Stage-2 user-buffer checks: python scripts/test_bridge.py --user-buffer COMx
Requires: bitstream + bridge elf running (cli-anything-vivado sdk run)."""
import sys
import time

import serial

try:
    from siggen.protocol import (crc8_atm as _protocol_crc8_atm,
                                 crc16_ccitt as _protocol_crc16_ccitt,
                                 user_chunk_cmd as _protocol_user_chunk_cmd,
                                 user_commit_cmd as _protocol_user_commit_cmd)
except ImportError:
    _protocol_crc8_atm = None
    _protocol_crc16_ccitt = None
    _protocol_user_chunk_cmd = None
    _protocol_user_commit_cmd = None

_ARGS = sys.argv[1:]
USER_BUFFER_TEST = "--user-buffer" in _ARGS
if USER_BUFFER_TEST:
    _ports = [arg for arg in _ARGS if arg != "--user-buffer"]
    if len(_ports) != 1 or not _ports[0]:
        raise SystemExit("usage: python scripts/test_bridge.py --user-buffer COMx")
    PORT = _ports[0]
elif len(_ARGS) > 1:
    raise SystemExit("usage: python scripts/test_bridge.py [COMx]")
else:
    PORT = _ARGS[0] if _ARGS else "COM5"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.5)
time.sleep(0.3)
_serial_pending = bytearray()


def read_line(timeout):
    deadline = time.monotonic() + timeout
    while True:
        newline = _serial_pending.find(b"\n")
        if newline >= 0:
            line = bytes(_serial_pending[:newline])
            del _serial_pending[:newline + 1]
            return line.decode(errors="replace").strip()
        if time.monotonic() >= deadline:
            return None
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            _serial_pending.extend(chunk)


def wait_for_bridge(timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = read_line(min(0.5, deadline - time.monotonic()))
        if line == "AC820-AWG bridge ready":
            return


wait_for_bridge()
ser.reset_input_buffer()
_serial_pending.clear()
ser.reset_output_buffer()


def raw(cmd_text):
    """发送状态复位命令并等待桥确认,兼容刚启动时的欢迎语。"""
    for _ in range(10):
        ser.write((cmd_text + "\r\n").encode())
        line = read_line(0.5)
        if line == "OK":
            return
        if line and line != "AC820-AWG bridge ready":
            ser.reset_input_buffer()
    raise RuntimeError("bridge did not acknowledge %s" % cmd_text)


# 复位到已知状态(脚本可重复运行;桥支持 HARM ORDER 0 禁用)
# 相位联动状态属于 CH2,测试开始前显式清除,避免前一轮板级状态污染
for c in ("SET CH2 PHASE 0", "SET CH2 PHASELINK OFF"):
    raw(c)
raw("SET DIGMOD MODE OFF")
raw("SET DIGMOD SOURCE PRBS7")
raw("SET DIGMOD FILTER RECT")
raw("SET DIGMOD ROLLOFF 0.35")
raw("SET DIGMOD SYMRATE 10000")
raw("SET DIGMOD SEED 127")
# 顺序重要: 必须先归零谐波幅值再设 BASE 100,否则残留 h1_amp 触发归一化
# (100+50=150 -> base=66), 后续 GET 断言会失败。
for ch in ("CH1", "CH2"):
    for c in ("SET %s WAVE SINE" % ch, "SET %s FREQ 1000" % ch,
              "SET %s AMPL 50" % ch,
              "SET %s DUTY 50" % ch,
              "SET %s HARM1 ORDER 0" % ch, "SET %s HARM1 AMPL 0" % ch,
              "SET %s HARM2 ORDER 0" % ch, "SET %s HARM2 AMPL 0" % ch,
              "SET %s HARM3 ORDER 0" % ch, "SET %s HARM3 AMPL 0" % ch,
              "SET %s HARM4 ORDER 0" % ch, "SET %s HARM4 AMPL 0" % ch,
              "SET %s BASE 100" % ch,
              "SET %s MODE OFF" % ch, "SET %s MODWAVE SINE" % ch,
              "SET %s MODFREQ 10" % ch, "SET %s AMDEPTH 100" % ch,
              "SET %s FMDEV 0" % ch, "SET %s PMDEV 0" % ch):
        raw(c)

fails = 0


def send_line(s):
    ser.write((s + "\r\n").encode())
    t0 = time.monotonic()
    line = read_line(2.0)
    while line == "AC820-AWG bridge ready" and time.monotonic() - t0 < 2.0:
        line = read_line(2.0 - (time.monotonic() - t0))
    return line


def cmd(s, expect):
    global fails
    line = send_line(s)
    ok = line == expect
    if not ok:
        fails += 1
    print(("PASS" if ok else "FAIL"), repr(s), "->", repr(line), "(expect", repr(expect) + ")")
    return line


def crc8_atm(data):
    if _protocol_crc8_atm is not None:
        return _protocol_crc8_atm(data)
    crc = 0
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_ccitt(data):
    if _protocol_crc16_ccitt is not None:
        return _protocol_crc16_ccitt(data)
    crc = 0xFFFF
    for byte in bytes(data):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def user_chunk_cmd(offset, data, crc=None):
    payload = bytes(data)
    if crc is None and _protocol_user_chunk_cmd is not None:
        return _protocol_user_chunk_cmd(offset, payload)
    if crc is None:
        crc = crc8_atm(payload)
    return "SET USER CHUNK %d %s %02X" % (offset, payload.hex().upper(), crc)


def user_commit_cmd(bit_length, crc16):
    if _protocol_user_commit_cmd is not None:
        return _protocol_user_commit_cmd(bit_length, crc16)
    return "SET USER COMMIT %d %04X" % (bit_length, crc16)


def parse_userbuf(line):
    if not line:
        return None
    parts = line.split()
    keys = ("VALID", "LENGTH", "CRC", "BANK", "UPLOAD", "LOOP",
            "COMMIT_SEEN", "READY", "DONE", "UNDERFLOW")
    if len(parts) != 21 or parts[0] != "USERBUF" or tuple(parts[1::2]) != keys:
        return None
    try:
        result = {
            "valid": int(parts[2]), "bit_length": int(parts[4]),
            "crc16": int(parts[6], 16), "bank": int(parts[8]),
            "upload": parts[10], "loop": parts[12],
            "commit_seen": int(parts[14]), "ready": int(parts[16]),
            "done": int(parts[18]), "underflow": int(parts[20]),
        }
    except ValueError:
        return None
    if (result["valid"] not in (0, 1) or not 0 <= result["bit_length"] <= 4096 or
            result["bit_length"] % 8 or not 0 <= result["crc16"] <= 0xFFFF or
            result["bank"] not in (0, 1) or
            result["upload"] not in ("IDLE", "RECEIVING") or
            result["loop"] not in ("ON", "OFF") or
            any(result[key] not in (0, 1)
                for key in ("commit_seen", "ready", "done", "underflow"))):
        return None
    return result


def user_status(expect, label):
    global fails
    line = send_line("GET USERBUF")
    status = parse_userbuf(line)
    ok = status is not None and expect(status)
    if not ok:
        fails += 1
    print(("PASS" if ok else "FAIL"), "GET USERBUF", "->", repr(line), "(%s)" % label)
    return status


def active_key(status):
    return tuple(status[key] for key in ("valid", "bit_length", "crc16", "bank"))


# 正常下发(小写验证大小写不敏感)
cmd("set ch1 wave sine", "OK")
cmd("set ch1 freq 100000", "OK")
cmd("set ch1 ampl 80", "OK")
cmd("SET CH2 WAVE SAW", "OK")
cmd("SET CH2 FREQ 2500", "OK")
cmd("SET CH2 AMPL 20", "OK")

# 状态回读
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")

# ---- 脉冲波形与占空比 ----
cmd("SET CH1 WAVE PULSE", "OK")
cmd("SET CH1 DUTY 25", "OK")
cmd("GET CH1", "CH1 WAVE PULSE FREQ 100000 AMPL 80 DUTY 25 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("SET CH1 DUTY 0", "OK")
cmd("GET CH1", "CH1 WAVE PULSE FREQ 100000 AMPL 80 DUTY 0 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("SET CH1 DUTY 100", "OK")
cmd("GET CH1", "CH1 WAVE PULSE FREQ 100000 AMPL 80 DUTY 100 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("SET CH1 DUTY 101", "ERR DUTY OUT OF RANGE")
cmd("SET CH1 DUTY abc", "ERR DUTY OUT OF RANGE")
cmd("SET CH1 DUTY 50", "OK")
cmd("SET CH1 WAVE NOISE", "OK")
cmd("GET CH1", "CH1 WAVE NOISE FREQ 100000 AMPL 80 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("SET CH1 WAVE SINE", "OK")

# ---- 数字调制协议 ----
def digmod_line(mode, rate=10000, filter_name="RECT", rolloff="0.35",
                running=None, seed=127, source="PRBS7"):
    if running is None:
        running = mode != "OFF"
    state = "RUNNING" if running else "STOPPED"
    return "DIGMOD MODE %s SOURCE %s FILTER %s ROLLOFF %s SEED %d SYMRATE %d STATE %s" % (
        mode, source, filter_name, rolloff, seed, rate, state)

cmd("GET DIGMOD", digmod_line("OFF"))
for mode in ("OFF", "BPSK", "QPSK", "QAM4", "QAM16", "QAM64"):
    cmd("SET DIGMOD MODE %s" % mode, "OK")
    cmd("GET DIGMOD", digmod_line(mode))
cmd("SET DIGMOD MODE OFF", "OK")
cmd("SET DIGMOD SYMRATE 100000", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000))
cmd("SET DIGMOD SYMRATE 9", "ERR SYMRATE OUT OF RANGE")
cmd("SET DIGMOD SYMRATE 1000001", "ERR SYMRATE OUT OF RANGE")
cmd("SET DIGMOD SYMRATE abc", "ERR BAD VALUE")
cmd("SET DIGMOD SEED 5", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RECT", "0.35", seed=5))
cmd("SET DIGMOD SEED 0", "ERR SEED OUT OF RANGE")
cmd("SET DIGMOD SEED 128", "ERR SEED OUT OF RANGE")
cmd("SET DIGMOD SEED abc", "ERR SEED OUT OF RANGE")
cmd("SET DIGMOD RESET", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RECT", "0.35", seed=5))
cmd("SET DIGMOD SEED 127", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RECT", "0.35"))
cmd("SET DIGMOD FILTER RRC", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RRC"))
cmd("SET DIGMOD ROLLOFF 0.2", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RRC", "0.2"))
cmd("SET DIGMOD ROLLOFF 0.5", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RRC", "0.5"))
cmd("SET DIGMOD MODE BPSK", "OK")
cmd("GET DIGMOD", digmod_line("BPSK", 100000, "RRC", "0.5"))
cmd("SET DIGMOD MODE OFF", "OK")
cmd("SET DIGMOD MODE QAM16", "OK")
cmd("GET DIGMOD", digmod_line("QAM16", 100000, "RRC", "0.5"))
cmd("SET DIGMOD SEED 63", "ERR DIGMOD RUNNING")
cmd("SET DIGMOD RESET", "ERR DIGMOD RUNNING")
cmd("SET DIGMOD FILTER RECT", "ERR DIGMOD RUNNING")
cmd("GET DIGMOD", digmod_line("QAM16", 100000, "RRC", "0.5"))
cmd("SET DIGMOD ROLLOFF 0.2", "ERR DIGMOD RUNNING")
cmd("GET DIGMOD", digmod_line("QAM16", 100000, "RRC", "0.5"))
cmd("SET DIGMOD SYMRATE 250000", "ERR DIGMOD RUNNING")
cmd("SET DIGMOD MODE OFF", "OK")
cmd("SET DIGMOD FILTER RECT", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RECT", "0.5"))
cmd("SET DIGMOD ROLLOFF 0.35", "OK")
cmd("GET DIGMOD", digmod_line("OFF", 100000, "RECT", "0.35"))
cmd("SET DIGMOD FILTER BOGUS", "ERR BAD VALUE")
cmd("SET DIGMOD FILTER", "ERR BAD VALUE")
cmd("SET DIGMOD FILTER RECT EXTRA", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF 0.10", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF 0.50", "ERR BAD VALUE")
cmd("SET DIGMOD ROLLOFF 0.2 EXTRA", "ERR BAD VALUE")
cmd("SET DIGMOD MODE OFF EXTRA", "ERR BAD VALUE")
cmd("GET DIGMOD", digmod_line("OFF", 100000))

# 非法输入
cmd("SET CH1 FREQ 5", "ERR FREQ OUT OF RANGE")
cmd("SET CH1 FREQ 1000001", "ERR FREQ OUT OF RANGE")
cmd("SET CH1 FREQ abc", "ERR BAD VALUE")
cmd("SET CH1 AMPL 101", "ERR AMPL OUT OF RANGE")
cmd("SET CH1 AMPL abc", "ERR BAD VALUE")
cmd("SET CH1 WAVE BOGUS", "ERR BAD VALUE")
cmd("SET CH3 WAVE SINE", "ERR BAD CHANNEL")
cmd("SET CH1 FOO 5", "ERR BAD FIELD")
cmd("FROB CH1", "ERR UNKNOWN CMD")

# 错误后状态不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")

# ---- 谐波与基波(归一化期望值 = 桥内整数截断,已核算)----
cmd("SET CH1 BASE 100", "OK")
cmd("SET CH1 HARM1 ORDER 3", "OK")
cmd("SET CH1 HARM1 AMPL 30", "OK")      # sum=130 -> base=100*100//130=76, h1=30*100//130=23
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 76 H1 3 23 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
cmd("SET CH1 BASE 80", "OK")            # sum=80+23=103 -> base=80*100//103=77, h1=23*100//103=22
cmd("SET CH1 HARM2 ORDER 5", "OK")      # sum=77+22=99 <=100,不变
cmd("SET CH1 HARM2 AMPL 10", "OK")      # sum=77+22+10=109 -> base=70, h1=20, h2=9
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 70 H1 3 20 H2 5 9 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
# 非法
cmd("SET CH1 BASE 101", "ERR BASE OUT OF RANGE")
cmd("SET CH1 HARM1 ORDER 1", "ERR HARM ORDER OUT OF RANGE")
cmd("SET CH1 HARM1 ORDER 9", "ERR HARM ORDER OUT OF RANGE")
cmd("SET CH1 HARM1 AMPL 101", "ERR HARM AMPL OUT OF RANGE")
cmd("SET CH1 HARM5 ORDER 2", "ERR BAD VALUE")
cmd("SET CH1 HARM1 FOO 2", "ERR BAD FIELD")
# base=0(谐波仍启用)→ 仅谐波输出(非静音;真正的静音用幅度条 ampl=0)
cmd("SET CH1 BASE 0", "OK")             # sum=0+20+9=29 <=100,base=0,harm 不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 0 H1 3 20 H2 5 9 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")
# 恢复:将谐波幅值归零
cmd("SET CH1 BASE 100", "OK")           # sum=100+20+9=129 -> base=77, h1=15, h2=6
cmd("SET CH1 HARM1 AMPL 0", "OK")      # sum=77+0+6=83 <=100,不变
cmd("SET CH1 HARM2 AMPL 0", "OK")      # sum=77+0+0=77 <=100,不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 DUTY 50 BASE 77 H1 3 0 H2 5 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 0 PHASELINK OFF")

# ---- 调制命令(先复位到已知状态) ----
cmd("SET CH1 WAVE SINE", "OK")
cmd("SET CH1 FREQ 1000", "OK")
cmd("SET CH1 AMPL 50", "OK")
cmd("SET CH1 HARM1 ORDER 0", "OK")
cmd("SET CH1 HARM1 AMPL 0", "OK")
cmd("SET CH1 HARM2 ORDER 0", "OK")
cmd("SET CH1 HARM2 AMPL 0", "OK")
cmd("SET CH1 HARM3 ORDER 0", "OK")
cmd("SET CH1 HARM3 AMPL 0", "OK")
cmd("SET CH1 HARM4 ORDER 0", "OK")
cmd("SET CH1 HARM4 AMPL 0", "OK")
cmd("SET CH1 BASE 100", "OK")
cmd("SET CH1 MODE OFF", "OK")
cmd("SET CH1 MODWAVE SINE", "OK")
cmd("SET CH1 MODFREQ 10", "OK")
cmd("SET CH1 AMDEPTH 100", "OK")
cmd("SET CH1 FMDEV 0", "OK")
cmd("SET CH1 PMDEV 0", "OK")

cmd("SET CH1 MODE FM", "OK")
cmd("SET CH1 MODWAVE SQUARE", "OK")
cmd("SET CH1 MODFREQ 100", "OK")          # 10.0Hz
cmd("SET CH1 AMDEPTH 80", "OK")
cmd("SET CH1 FMDEV 10000", "OK")
cmd("SET CH1 PMDEV 64", "OK")
cmd("GET CH1",
    "CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE FM MODWAVE SQUARE MODFREQ 100 AMDEPTH 80 FMDEV 10000 PMDEV 64 PHASE 0 PHASELINK OFF")
cmd("SET CH1 MODE OFF", "OK")
cmd("GET CH1",
    "CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SQUARE MODFREQ 100 AMDEPTH 80 FMDEV 10000 PMDEV 64 PHASE 0 PHASELINK OFF")
# 模式关闭保留其余参数

# 越界
cmd("SET CH1 MODE FOO", "ERR MODE OUT OF RANGE")
cmd("SET CH1 MODE 5", "ERR MODE OUT OF RANGE")
cmd("SET CH1 MODWAVE BOGUS", "ERR MODWAVE OUT OF RANGE")
cmd("SET CH1 MODFREQ 0", "ERR MODFREQ OUT OF RANGE")
cmd("SET CH1 MODFREQ 500001", "ERR MODFREQ OUT OF RANGE")
cmd("SET CH1 MODFREQ abc", "ERR MODFREQ OUT OF RANGE")   # 非数字: 桥内 parse_u32 失败同映射
cmd("SET CH1 AMDEPTH 101", "ERR AMDEPTH OUT OF RANGE")
cmd("SET CH1 FMDEV 500001", "ERR FMDEV OUT OF RANGE")
cmd("SET CH1 PMDEV 512", "ERR PMDEV OUT OF RANGE")
# 错误后调制状态不变
cmd("GET CH1",
    "CH1 WAVE SINE FREQ 1000 AMPL 50 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SQUARE MODFREQ 100 AMDEPTH 80 FMDEV 10000 PMDEV 64 PHASE 0 PHASELINK OFF")

# ---- 相位联动(PHASE 仅 CH2 有效) ----
cmd("SET CH2 PHASE 9000", "OK")            # 90.00°
cmd("SET CH2 PHASELINK ON", "OK")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 9000 PHASELINK ON")
cmd("SET CH2 PHASE 0", "OK")
cmd("SET CH2 PHASE 36000", "OK")
cmd("SET CH2 PHASE 36001", "ERR PHASE OUT OF RANGE")
cmd("SET CH2 PHASE abc", "ERR PHASE OUT OF RANGE")   # parse_u32 失败 -> 范围错误
cmd("SET CH2 PHASELINK MAYBE", "ERR BAD VALUE")
cmd("SET CH1 PHASE 9000", "ERR BAD CHANNEL")         # PHASE 仅 CH2
cmd("SET CH1 PHASELINK ON", "ERR BAD CHANNEL")
# 错误后状态不变
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20 DUTY 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SINE MODFREQ 10 AMDEPTH 100 FMDEV 0 PMDEV 0 PHASE 36000 PHASELINK ON")
cmd("SET CH2 PHASE 0", "OK")               # 复位
cmd("SET CH2 PHASELINK OFF", "OK")

# ---- 通道损伤(CHAN/NOISE/TAP;增益为 9 位有符号 Q8,范围 -256..255) ----
cmd("SET CH1 NOISE 0", "OK")
cmd("SET CH1 TAP CLEAR", "OK")
cmd("SET CH1 TAP 0 255 0", "OK")
cmd("SET CH1 TAP 1 -128 2", "OK")
cmd("SET CH1 CHAN TONE 0 0", "OK")        # 块内自建: GET 断言的字段必须由本块决定, 不能靠上电默认值
cmd("SET CH1 CHAN IMPULSE 1 1 0", "OK")   # period/width 不能写 0: 固件要求 >=1;
                                          # ampl=0 即为"关闭", 是可达且可重复的关闭态
cmd("SET CH1 CHAN ON", "OK")
cmd("SET CH1 CHAN COMMIT", "OK")
cmd("GET CH1 CHAN", "CH1 CHAN ON NOISE 0 TONE 0 0 IMPULSE 1 1 0 "
                    "TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
cmd("SET CH1 CHAN OFF", "OK")
cmd("SET CH1 NOISE 101", "ERR NOISE OUT OF RANGE")
cmd("SET CH1 TAP 0 512 0", "ERR TAP OUT OF RANGE")
cmd("SET CH2 CHAN ON", "OK")
cmd("SET CH2 CHAN OFF", "OK")

# ---- 单音与周期冲激干扰(TONE/IMPULSE;参数走 BRAM 字 12-15, COMMIT 后锁存) ----
# 开关走 GPIO bit9(单音)/bit10(冲激), ampl==0 视同关闭; 数值走 BRAM, GET 回读影子值。
cmd("SET CH1 CHAN TONE 1000000 20", "OK")
cmd("GET CH1 CHAN", "CH1 CHAN OFF NOISE 0 TONE 1000000 20 IMPULSE 1 1 0 "
                    "TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
cmd("SET CH1 CHAN TONE 1000000 0", "OK")          # ampl=0 -> 关掉单音
cmd("GET CH1 CHAN", "CH1 CHAN OFF NOISE 0 TONE 1000000 0 IMPULSE 1 1 0 "
                    "TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
cmd("SET CH1 CHAN TONE 25000001 20", "ERR TONE OUT OF RANGE")
cmd("SET CH1 CHAN TONE -1 20", "ERR TONE OUT OF RANGE")
cmd("SET CH1 CHAN TONE 1000000 101", "ERR TONE OUT OF RANGE")
cmd("SET CH1 CHAN IMPULSE 1000 8 30", "OK")
cmd("SET CH1 CHAN IMPULSE 1000 8 -100", "OK")     # 负幅度必须有符号保号
cmd("GET CH1 CHAN", "CH1 CHAN OFF NOISE 0 TONE 1000000 0 IMPULSE 1000 8 -100 "
                    "TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
cmd("SET CH1 CHAN IMPULSE 0 8 30", "ERR IMPULSE OUT OF RANGE")
cmd("SET CH1 CHAN IMPULSE 1000 0 30", "ERR IMPULSE OUT OF RANGE")
cmd("SET CH1 CHAN IMPULSE 1000 8 101", "ERR IMPULSE OUT OF RANGE")
cmd("SET CH2 NOISE 0", "OK")            # 块内自建: 全脚本没有别处设过 CH2 的噪声/抽头影子,
cmd("SET CH2 TAP CLEAR", "OK")          # 不设就等于依赖上电默认值 —— 先跑过 GUI 就会变
cmd("SET CH2 CHAN TONE 250000 40", "OK")
cmd("SET CH2 CHAN IMPULSE 500 4 50", "OK")
cmd("GET CH2 CHAN", "CH2 CHAN OFF NOISE 0 TONE 250000 40 IMPULSE 500 4 50 "
                    "TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
cmd("SET CH2 CHAN TONE 2500001 40", "OK")         # 越过旧 parse_long 的 1MHz 上限
cmd("GET CH2 CHAN", "CH2 CHAN OFF NOISE 0 TONE 2500001 40 IMPULSE 500 4 50 "
                    "TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")

# ---- 复数 I/Q 损伤(顶层家族 IQIMP; 通道对级, 因此不挂 CHx) ----
# ON/OFF 走 GPIO bit11 立即生效; 四个数值参数写 BRAM 字 8/9/10, 要 IQIMP COMMIT
# 才锁存(chan_cfg 在任一通道 commit 时都会排空这几个字)。
# ⚠ GET 回读的是固件**影子数组**(用户下发的语义值), 与是否已 COMMIT 无关 ——
#   所以每条数值命令后面都能立刻回读。
# ⚠ **块内自建**: IQIMP 影子跨运行持续存在(本脚本前导段只复位波形/调制/DIGMOD),
#   不先设一遍就等于依赖上一次运行留下的值。
cmd("SET IQIMP PHASE 0", "OK")
cmd("SET IQIMP FREQOFF 0", "OK")
cmd("SET IQIMP GAIN 16384", "OK")
cmd("SET IQIMP PHASEERR 0", "OK")
cmd("SET IQIMP COMMIT", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
cmd("SET IQIMP ON", "OK")
cmd("GET IQIMP", "IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
cmd("SET IQIMP ON", "OK")                         # 幂等
cmd("SET IQIMP PHASE 4500", "OK")                 # 45.00 度
cmd("SET IQIMP FREQOFF 1000000", "OK")            # 协议上限(硬件能到 25MHz)
cmd("SET IQIMP GAIN -16384", "OK")                # 负增益: 必须保号
cmd("SET IQIMP PHASEERR -1200", "OK")
cmd("GET IQIMP", "IQIMP ON PHASE 4500 FREQOFF 1000000 GAIN -16384 PHASEERR -1200")
cmd("SET IQIMP COMMIT", "OK")
cmd("SET IQIMP OFF", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 4500 FREQOFF 1000000 GAIN -16384 PHASEERR -1200")
# 越界: 每个参数的两侧边界各一条
cmd("SET IQIMP PHASE 18001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP PHASE -18001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP PHASEERR 18001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP PHASEERR -18001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP FREQOFF 1000001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP FREQOFF -1000001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP GAIN 32768", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP GAIN -32769", "ERR IQIMP OUT OF RANGE")
# 家族名匹配上之后的未知子命令不能让命令静默通过
cmd("SET IQIMP BOGUS 1", "ERR IQIMP OUT OF RANGE")
# 回归护栏: FREQOFF 走 parse_long(上限恰好 1000000), 负数侧同样要过
cmd("SET IQIMP FREQOFF -1000000", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 4500 FREQOFF -1000000 GAIN -16384 PHASEERR -1200")
# 块尾复位到单位阵并关闭: 留在 ON + 非单位阵会把后续数字调制用例的星座旋转掉,
# 与那边 BER=0 的判据直接冲突
cmd("SET IQIMP PHASE 0", "OK")
cmd("SET IQIMP FREQOFF 0", "OK")
cmd("SET IQIMP GAIN 16384", "OK")
cmd("SET IQIMP PHASEERR 0", "OK")
cmd("SET IQIMP COMMIT", "OK")
cmd("SET IQIMP OFF", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")

# ---- 阶段 2: 用户数据双 bank/CRC/状态回归(显式 --user-buffer 才执行) ----
if USER_BUFFER_TEST:
    print("USER BUFFER STAGE-2")
    payload_a = bytes((0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC))
    payload_b = bytes((0xD0, 0xE1, 0xF2, 0x03, 0x14, 0x25))
    crc_a = crc16_ccitt(payload_a)
    crc_b = crc16_ccitt(payload_b)

    # Only a stopped PRBS source may be changed while preparing a user bank.
    raw("SET DIGMOD MODE OFF")
    raw("SET USER ABORT")
    raw("SET USER LOOP OFF")
    raw("SET DIGMOD SOURCE PRBS7")
    raw("SET DIGMOD SYMRATE 10000")

    # USER cannot be selected before the first complete commit.  A persistent
    # committed bank from an earlier run makes this case inapplicable.
    initial = user_status(lambda s: True, "initial USERBUF status")
    if initial is None or initial["valid"] == 0:
        cmd("SET USER CLEAR", "OK")
        cmd(user_chunk_cmd(0, payload_b[:2]), "OK")
        cmd("SET DIGMOD SOURCE USER", "ERR USERBUF NOT READY")
        cmd("GET DIGMOD", digmod_line("OFF", source="PRBS7"))
        cmd("SET USER ABORT", "OK")
    else:
        print("SKIP USER-before-COMMIT check: active bank already exists")

    # A valid upload accepts out-of-order chunks and an identical duplicate,
    # then atomically makes the staged bank active.
    cmd("SET USER CLEAR", "OK")
    user_status(lambda s: s["upload"] == "RECEIVING",
                "CLEAR starts an inactive-bank upload")
    cmd(user_chunk_cmd(4, payload_a[4:]), "OK")
    cmd(user_chunk_cmd(0, payload_a[:4]), "OK")
    cmd(user_chunk_cmd(0, payload_a[:4]), "OK")
    cmd(user_commit_cmd(len(payload_a) * 8, crc_a), "OK")
    active = user_status(
        lambda s: s["valid"] == 1 and s["bit_length"] == len(payload_a) * 8
        and s["crc16"] == crc_a and s["upload"] == "IDLE"
        and s["commit_seen"] == 1 and s["ready"] == 1
        and s["done"] == 0 and s["underflow"] == 0,
        "valid out-of-order upload, duplicate accepted, done/underflow status",
    )
    if active is None:
        active = {key: -1 for key in ("valid", "bit_length", "crc16", "bank", "ready")}
    old_active = active_key(active)

    # A conflicting duplicate is rejected without changing the active bank.
    cmd("SET USER CLEAR", "OK")
    cmd(user_chunk_cmd(0, payload_b[:2]), "OK")
    cmd(user_chunk_cmd(0, bytes((payload_b[0] ^ 0xFF, payload_b[1]))),
        "ERR USER CHUNK CONFLICT")
    user_status(lambda s: active_key(s) == old_active,
                "conflicting duplicate preserves old active bank")

    # A bad per-chunk CRC is rejected and the old committed bank remains active.
    cmd("SET USER ABORT", "OK")
    cmd("SET USER CLEAR", "OK")
    bad_chunk_crc = crc8_atm(payload_b[:2]) ^ 1
    cmd(user_chunk_cmd(0, payload_b[:2], bad_chunk_crc), "ERR USER CHUNK CRC")
    user_status(lambda s: active_key(s) == old_active,
                "chunk CRC failure preserves old active bank")

    # A complete staged payload with a bad commit CRC must not become active.
    cmd("SET USER ABORT", "OK")
    cmd("SET USER CLEAR", "OK")
    cmd(user_chunk_cmd(0, payload_b), "OK")
    cmd(user_commit_cmd(len(payload_b) * 8, crc_b ^ 1), "ERR USER COMMIT FAILED")
    user_status(lambda s: active_key(s) == old_active,
                "commit CRC failure preserves old active bank")

    # Missing bytes make the commit incomplete; this also preserves the bank.
    cmd("SET USER ABORT", "OK")
    cmd("SET USER CLEAR", "OK")
    cmd(user_chunk_cmd(0, payload_b[:2]), "OK")
    cmd(user_commit_cmd(len(payload_b) * 8, crc_b), "ERR USER COMMIT FAILED")
    user_status(lambda s: active_key(s) == old_active,
                "short commit preserves old active bank")

    # A second valid commit flips the active bank only after validation.
    cmd("SET USER ABORT", "OK")
    cmd("SET USER CLEAR", "OK")
    cmd(user_chunk_cmd(0, payload_b), "OK")
    cmd(user_commit_cmd(len(payload_b) * 8, crc_b), "OK")
    active = user_status(
        lambda s: s["valid"] == 1 and s["bit_length"] == len(payload_b) * 8
        and s["crc16"] == crc_b and s["upload"] == "IDLE"
        and s["commit_seen"] == 1 and s["ready"] == 1
        and s["bank"] != old_active[3],
        "second valid commit toggles the active bank",
    )
    if active is not None:
        old_active = active_key(active)

    # SOURCE and LOOP changes are visible through their respective status lines.
    cmd("SET DIGMOD SOURCE USER", "OK")
    cmd("SET USER LOOP ON", "OK")
    cmd("SET DIGMOD MODE QPSK", "OK")
    cmd("GET DIGMOD", digmod_line("QPSK", source="USER"))
    user_status(lambda s: active_key(s) == old_active and s["loop"] == "ON",
                "USER source running with loop enabled")
    cmd("SET DIGMOD MODE OFF", "OK")
    cmd("SET USER LOOP OFF", "OK")
    user_status(lambda s: active_key(s) == old_active and s["loop"] == "OFF",
                "loop disabled status")
    cmd("SET DIGMOD SOURCE PRBS7", "OK")
    cmd("GET DIGMOD", digmod_line("OFF", source="PRBS7"))

print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
sys.exit(0 if fails == 0 else 1)
