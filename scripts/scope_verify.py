#!/usr/bin/env python3
"""scope_verify.py: 板卡(串口) + 示波器(USB-TMC) 自动闭环验证。

流程: 复位板卡 -> SQUARE 10k 100% 实测 Vpp 作校准常数 -> 逐用例下发 SET,
等 0.8s 后 SCPI 回读 :MEAS:FREQ? / :MEAS:VPP? 比对;谐波用例回读波形
(:WAV:DATA?) 做 numpy FFT 检查 2 次谐波分量;调制用例(AM 包络 / FM、PM
边带 FFT / SWEEP 象限过零频率)回读波形验证调制闭环。

用法: python scripts/scope_verify.py [COMx]
前置: 比特流 + 桥程序已运行,示波器 USB 连接(CH1 探头接 DAC 输出),
      Keysight IO Libraries 已装(pyvisa 默认后端,勿用 '@py')。
"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
from siggen.digital_mod import constellation_points, generate_symbols
from siggen.rrc import matched_filter_symbols, rrc_coefficients, rrc_filter_symbols

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM11"
BAUD = 115200
SCOPE = "<VISA_RESOURCE>"
CAL_WIN = (8.5, 10.5)  # 校准合理性窗口(参考实测 9.56~9.62V)

results = []
ser = None
_serial_pending = bytearray()
inst = None
rm = None


def report(name, ok, detail):
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, "-", detail)


# ---------- 串口 ----------
def open_resources():
    global ser, inst, rm
    import pyvisa
    import serial

    ser = serial.Serial(PORT, BAUD, timeout=0.5)
    time.sleep(0.3)
    wait_for_bridge()
    ser.reset_input_buffer()
    _serial_pending.clear()
    ser.reset_output_buffer()
    raw("SET DIGMOD MODE OFF")
    raw("SET DIGMOD SOURCE PRBS7")
    raw("SET DIGMOD SEED 127")
    rm = pyvisa.ResourceManager()
    inst = ReconnectingScope(rm, SCOPE, timeout=5000)
    # 示波器刚从空闲被唤醒时开会话偶发 VI_ERROR_NCIC, 几秒后自愈
    for _ in range(8):
        try:
            inst.open()
            break
        except Exception as e:
            print("  [scope] open retry: %s" % str(e)[:70])
            time.sleep(5)
    else:
        raise RuntimeError("cannot open scope session")


def read_serial_line(timeout):
    deadline = time.monotonic() + timeout
    while True:
        newline = _serial_pending.find(b"\n")
        if newline >= 0:
            line = bytes(_serial_pending[:newline])
            del _serial_pending[:newline + 1]
            return line.decode(errors="replace").strip()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            _serial_pending.extend(chunk)


def wait_for_bridge(timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = read_serial_line(min(0.5, deadline - time.monotonic()))
        if line == "AC820-AWG bridge ready":
            return


def raw(cmd_text):
    for _ in range(10):
        ser.write((cmd_text + "\r\n").encode())
        line = read_serial_line(0.5)
        if line == "OK":
            return
        if line and line != "AC820-AWG bridge ready":
            ser.reset_input_buffer()
    raise RuntimeError("bridge did not acknowledge %s" % cmd_text)


def serial_line(s):
    ser.write((s + "\r\n").encode())
    t0 = time.monotonic()
    line = read_serial_line(2.0)
    while line == "AC820-AWG bridge ready" and time.monotonic() - t0 < 2.0:
        line = read_serial_line(2.0 - (time.monotonic() - t0))
    return line


def setup_board(cmds):
    for c in cmds:
        r = serial_line(c)
        if r != "OK":
            raise RuntimeError(f"board rejected {c}: {r}")
    time.sleep(0.8)  # DAC 更新 + 示波器重新捕获


# ---------- 示波器 ----------


class ReconnectingScope:
    """USB-TMC 会话在本机会周期性掉线(约 30~90s 一次, 报 VI_ERROR_NCIC, 命令
    无关), 空闲时反而稳定; 恢复约需 10s。整个套件跑几分钟, 所以每次操作都透明
    重连重试, 否则套件必在中途抛异常终止。重连后重跑 scope_setup() 重新固定
    采集参数(幂等)。
    """

    def __init__(self, rm, name, timeout=5000, attempts=6):
        self._rm = rm
        self._name = name
        self._timeout = timeout
        self._attempts = attempts
        self._inst = None

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, v):
        self._timeout = v
        if self._inst is not None:
            self._inst.timeout = v

    def open(self):
        if self._inst is None:
            self._inst = self._rm.open_resource(self._name, open_timeout=10000)
            self._inst.timeout = self._timeout
            try:
                scope_setup()
            except Exception:
                pass

    def _drop(self):
        inst, self._inst = self._inst, None
        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass

    def _run(self, op):
        from pyvisa.errors import VisaIOError

        last = None
        for k in range(self._attempts):
            try:
                self.open()
                return op(self._inst)
            except VisaIOError as e:
                last = e
                self._drop()
                print("  [scope] session lost (%s), reconnecting..." % str(e)[:60])
                time.sleep(min(5.0 * (k + 1), 25.0))
        raise last

    def write(self, cmd):
        return self._run(lambda i: i.write(cmd))

    def query(self, cmd):
        return self._run(lambda i: i.query(cmd))

    def query_binary_values(self, cmd, **kw):
        return self._run(lambda i: i.query_binary_values(cmd, **kw))

    def close(self):
        self._drop()


def scope_setup():
    inst.write(":STOP")
    inst.write(":WAV:SOUR CHAN1")
    inst.write(":WAV:FORM BYTE")
    inst.write(":WAV:POIN:MODE NORM")
    inst.write(":CHAN1:OFFS 0")
    inst.write(":CHAN1:PROB 1")
    inst.write(":CHAN2:DISP ON")
    inst.write(":CHAN2:SCAL 2.0")
    inst.write(":CHAN2:OFFS 0")
    inst.write(":CHAN2:PROB 1")
    inst.write(":MEAS:SOUR CHAN1")


def scope_acq(tim_scal, chan_scal, settle_s=0.8, on_run=None):
    """手动固定时基/垂直刻度(自动刻度在信号切换后收敛慢,会采到不完整周期)。

    on_run 在 :RUN 之后、等待之前调用,供 DDR 播放等一次性信号在采集窗内启动。
    """
    inst.write(":TIM:SCAL %.9E" % tim_scal)
    inst.write(":CHAN1:SCAL %.9E" % chan_scal)
    inst.write(":CHAN1:OFFS 0")
    inst.write(":CHAN1:PROB 1")
    inst.write(":CHAN2:SCAL %.9E" % chan_scal)
    inst.write(":CHAN2:OFFS 0")
    inst.write(":CHAN2:PROB 1")
    inst.write(":RUN")
    if on_run is not None:
        on_run()
    time.sleep(settle_s)  # 采集足够周期
    inst.write(":STOP")  # 冻结当前采集后再测量


def measure():
    try:
        f = float(inst.query(":MEAS:FREQ?"))
        v = float(inst.query(":MEAS:VPP?"))
        return f, v
    except Exception:
        return None, None


def wav_fft():
    """读取 NORM 波形做 FFT,返回 (freqs, |spec|);结尾 :RUN 恢复采集。"""
    xinc = float(inst.query(":WAV:XINC?"))
    # container=bytes 会走 pyvisa struct 路径报 InvalidBinaryFormat, 用 list
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    inst.write(":RUN")
    y = np.array(data, dtype=np.uint8).astype(float)
    y = y - np.mean(y)  # 去 DC(FFT 只需相对比值,BYTE 中心 128 无需换算)
    n = len(y)
    spec = np.abs(np.fft.rfft(y * np.hanning(n)))
    return np.fft.rfftfreq(n, xinc), spec


def scope_wave(ch):
    """读取指定通道 NORM 波形(:STOP 冻结帧内, 与另一通道同一时刻采集)。"""
    inst.write(":WAV:SOUR " + ch)
    time.sleep(0.02)
    xinc = float(inst.query(":WAV:XINC?"))
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    return xinc, np.array(data, dtype=np.uint8).astype(float)


def _rising_zero_crossings(y):
    """用动态迟滞阈值查找上升过零点，抑制低幅度波形的 ADC 量化毛刺。"""
    y = np.asarray(y, dtype=float)
    if y.size < 2 or not np.all(np.isfinite(y)):
        return np.array([], dtype=int)
    lo, hi = np.percentile(y, [5.0, 95.0])
    span = hi - lo
    if not np.isfinite(span) or span <= 0:
        return np.array([], dtype=int)
    midpoint = (lo + hi) / 2.0
    lower = midpoint - 0.1 * span
    upper = midpoint + 0.1 * span
    crossings = []
    armed = False
    for k in range(y.size - 1):
        if not armed and y[k] <= lower:
            armed = True
        elif armed and y[k + 1] >= upper:
            crossings.append(k)
            armed = False
    return np.asarray(crossings, dtype=int)


def phase_from_zero_cross(xinc, y1, y2):
    """两通道同窗波形 -> 相位差(度, CH2 相对 CH1 超前为正, 0..360)。
    上升过零时间差 × 频率 × 360; 取所有过零对的中值(抗毛刺)。"""
    if not np.isfinite(xinc) or xinc <= 0:
        return None

    z1, z2 = _rising_zero_crossings(y1), _rising_zero_crossings(y2)
    if len(z1) < 2 or len(z2) < 2:
        return None
    n = min(len(z1), len(z2))
    per1 = np.median(np.diff(z1)) * xinc
    per2 = np.median(np.diff(z2)) * xinc
    if not np.isfinite(per1) or per1 <= 0:
        return None
    if not np.isfinite(per2) or per2 <= 0:
        return None
    if abs(per1 - per2) > 0.1 * max(per1, per2):
        return None
    d = (z1[:n] - z2[:n]) * xinc
    return (np.median(d % per1)) / per1 * 360.0


def phase_error_deg(phase, expected):
    """返回 0..180 的圆周相位误差, 无效相位返回 None。"""
    if phase is None or not np.isfinite(phase):
        return None
    delta = abs((phase - expected) % 360.0)
    return min(delta, 360.0 - delta)


def frequency_from_zero_cross(xinc, y):
    """由上升过零点间隔估算波形频率, 无法估算时返回 None。"""
    if not np.isfinite(xinc) or xinc <= 0:
        return None
    z = _rising_zero_crossings(y)
    if len(z) < 2:
        return None
    per = np.median(np.diff(z)) * xinc
    if not np.isfinite(per) or per <= 0:
        return None
    return 1.0 / per


def digital_inlier_threshold(symrate):
    return 0.95 if symrate >= 1_000_000 else 0.99


def symbol_period_from_iq_labels(i_labels, q_labels, xinc):
    """由联合 I/Q 标签的稳定运行长度估算一个符号周期。"""
    i_labels = np.asarray(i_labels)
    q_labels = np.asarray(q_labels)
    if (i_labels.ndim != 1 or q_labels.ndim != 1
            or i_labels.size != q_labels.size or i_labels.size == 0
            or not np.isfinite(xinc) or xinc <= 0):
        return None
    changed = np.flatnonzero(
        (i_labels[1:] != i_labels[:-1])
        | (q_labels[1:] != q_labels[:-1])
    ) + 1
    runs = np.diff(np.r_[0, changed, i_labels.size])
    runs = runs[runs > 1]
    if runs.size == 0:
        return None
    typical = np.median(runs)
    stable_runs = runs[runs >= 0.5 * typical]
    return float(np.percentile(stable_runs, 20) * xinc)


RRC_SCOPE_SYMBOLS = 127
RRC_SCOPE_TRANSIENT_SYMBOLS = 8
RRC_SCOPE_DELAY_SAMPLES = 32
RRC_SCOPE_SPS = 4
# match_filter_capture 归一化目标峰值。RRC 匹配滤波增益约 1.8,若把输入
# 峰值拉到 32767 会在 int16 匹配滤波里饱和,把 QAM64 内层电平(±90/±54)
# 压到 ±126,丢失星座幅度信息;降到 2^13 使匹配滤波输出落在线性区。
RRC_SCOPE_MATCH_INPUT_PEAK = 8192
# 多电平星座的 PAM 轴电平(与 iq_constellation_mapper 一致),用于幅度归一化。
RRC_AXIS_LEVELS = {
    "QAM16": (-126, -42, 42, 126),
    "QAM64": (-126, -90, -54, -18, 18, 54, 90, 126),
}
RRC_SCOPE_CASES = (
    ("QPSK RRC 10kSym/s", "QPSK", 10_000, "0.35", 2e-3),
    ("QPSK RRC 100kSym/s", "QPSK", 100_000, "0.35", 200e-6),
    ("QPSK RRC 1MSym/s", "QPSK", 1_000_000, "0.35", 20e-6),
    ("QAM16 RRC 100kSym/s", "QAM16", 100_000, "0.35", 200e-6),
    ("QAM64 RRC 100kSym/s", "QAM64", 100_000, "0.35", 200e-6),
    ("QPSK RRC α=0.2 100kSym/s", "QPSK", 100_000, "0.2", 200e-6),
    ("QPSK RRC α=0.5 100kSym/s", "QPSK", 100_000, "0.5", 200e-6),
    ("BPSK RRC 100kSym/s", "BPSK", 100_000, "0.35", 200e-6),
    ("QAM4 RRC 100kSym/s", "QAM4", 100_000, "0.35", 200e-6),
    ("QAM16 RRC α=0.2 100kSym/s", "QAM16", 100_000, "0.2", 200e-6),
    ("QAM16 RRC α=0.5 100kSym/s", "QAM16", 100_000, "0.5", 200e-6),
    ("QAM64 RRC α=0.2 100kSym/s", "QAM64", 100_000, "0.2", 200e-6),
    ("QAM64 RRC α=0.5 100kSym/s", "QAM64", 100_000, "0.5", 200e-6),
)

# ---- 复数 I/Q 损伤(IQIMP)用例参数 ----
# 走 RRC 双通道路径, 采集条件与 RRC_SCOPE_CASES 里 100kSym/s 那组一致: 127 个符号
# 落在 2ms 窗内(1.27ms 载荷 + RRC 回绕瞬态), 两端各去 IQ_TRIM 个符号。
# 三个损伤量都取到能明确测出的量级: 45° 旋转; 1.22 倍 I 轴增益 + 12° 正交误差
# (使 |相关系数| ≈ sin12° = 0.21, 远高于判据 0.05 的下限);
# 20kHz 频偏在 100kSym/s 下每符号转 72°(< 180°, 相位展开无歧义)。
IQ_MODE = "QPSK"
IQ_RATE = 100_000
IQ_ROLLOFF = "0.35"
IQ_TIM_S = 200e-6
IQ_CHAN_S = 2.0
IQ_TRIM = 8
IQ_PHASE_DEG100 = 4500
IQ_GAIN_Q14 = 20000
IQ_PHASEERR_DEG100 = 1200
IQ_FREQOFF_HZ = 20_000
IQ_ROT_TOL_DEG = 5.0        # 静态相位用例容差(每帧重对齐后实测误差 <0.1 度)


def rrc_scope_cases():
    """Return the deterministic RRC scope cases."""
    return RRC_SCOPE_CASES


def offset_binary_to_signed(samples):
    """Convert offset-binary scope bytes to signed int16 samples."""
    values = np.asarray(samples)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("scope samples must be a finite one-dimensional array")
    if values.size and (np.any(values < 0) or np.any(values > 255)):
        raise ValueError("scope samples must be offset-binary bytes")
    return values.astype(np.int16) - 128


def scope_capture_to_digital(samples):
    return -offset_binary_to_signed(samples)


def center_sample_qpsk_labels(i_samples, q_samples):
    """Convert matched-filter center samples to signed QPSK labels."""
    i_samples = np.asarray(i_samples)
    q_samples = np.asarray(q_samples)
    if (i_samples.ndim != 1 or q_samples.ndim != 1
            or i_samples.size != q_samples.size):
        raise ValueError("I/Q center samples must be equal-length one-dimensional arrays")
    return (np.where(i_samples >= 0, 127, -127).astype(np.int16),
            np.where(q_samples >= 0, 127, -127).astype(np.int16))


def _quantize_to_levels(values, levels):
    values = np.asarray(values, dtype=np.float64)
    levels = np.asarray(levels, dtype=np.float64)
    nearest = np.abs(values[:, None] - levels[None, :]).argmin(axis=1)
    return levels[nearest].astype(np.int16)


def center_sample_labels(i_samples, q_samples, mode):
    """按模式把匹配滤波中心样点量化到最近星座轴电平。"""
    i_samples = np.asarray(i_samples)
    q_samples = np.asarray(q_samples)
    if (i_samples.ndim != 1 or q_samples.ndim != 1
            or i_samples.size != q_samples.size):
        raise ValueError("I/Q center samples must be equal-length one-dimensional arrays")
    if mode == "BPSK":
        # 只有 I 轴有信号: I 符号判决, Q 恒为 0(匹配滤波后 Q 轴恢复为常数零)。
        return (np.where(i_samples >= 0, 127, -127).astype(np.int16),
                np.zeros(i_samples.size, dtype=np.int16))
    if mode in ("QPSK", "QAM4"):
        # QAM4 与 QPSK 是相同的四点映射, 复用符号判决。
        return center_sample_qpsk_labels(i_samples, q_samples)
    if mode == "QAM16":
        axis = np.array((-126, -42, 42, 126), dtype=np.float64)
    elif mode == "QAM64":
        axis = np.array((-126, -90, -54, -18, 18, 54, 90, 126), dtype=np.float64)
    else:
        raise ValueError(f"unsupported RRC constellation mode: {mode}")
    return (_quantize_to_levels(i_samples, axis),
            _quantize_to_levels(q_samples, axis))


def rrc_eye_margin(i_samples, q_samples, mode=None):
    i_samples = np.asarray(i_samples, dtype=np.float64)
    q_samples = np.asarray(q_samples, dtype=np.float64)
    if (i_samples.ndim != 1 or q_samples.ndim != 1
            or i_samples.size != q_samples.size or i_samples.size == 0
            or not np.all(np.isfinite(i_samples))
            or not np.all(np.isfinite(q_samples))):
        raise ValueError("I/Q eye samples must be equal-length finite arrays")
    if mode == "BPSK":
        # BPSK 的 Q 轴恒为 0(无信号), 眼图张开度只看 I 轴。
        return float(np.percentile(np.abs(i_samples), 5.0))
    return min(float(np.percentile(np.abs(i_samples), 5.0)),
               float(np.percentile(np.abs(q_samples), 5.0)))


def _align_qpsk_labels(i_labels, q_labels, seed=0x7f,
                       trim_symbols=RRC_SCOPE_TRANSIENT_SYMBOLS):
    i_labels = np.asarray(i_labels)
    q_labels = np.asarray(q_labels)
    if (i_labels.ndim != 1 or q_labels.ndim != 1
            or i_labels.size != q_labels.size
            or trim_symbols < 0
            or i_labels.size <= 2 * trim_symbols):
        raise ValueError("invalid QPSK labels or transient guard")
    expected_i, expected_q = generate_symbols("QPSK", 127, seed=seed)
    start = int(trim_symbols)
    stop = i_labels.size - int(trim_symbols)
    positions = np.arange(start, stop)
    best = None
    for symbol_offset in range(127):
        indices = (symbol_offset + positions) % 127
        errors = int(np.count_nonzero(i_labels[start:stop] != expected_i[indices]))
        errors += int(np.count_nonzero(q_labels[start:stop] != expected_q[indices]))
        if best is None or errors < best[0]:
            best = (errors, symbol_offset,
                    expected_i[indices], expected_q[indices])
    return best


def rrc_qpsk_ber(i_capture, q_capture, seed=0x7f,
                 delay_samples=RRC_SCOPE_DELAY_SAMPLES,
                 trim_symbols=RRC_SCOPE_TRANSIENT_SYMBOLS):
    """Match exact 4-SPS captures and return the QPSK bit error rate."""
    i_samples = offset_binary_to_signed(i_capture)
    q_samples = offset_binary_to_signed(q_capture)
    if i_samples.size != q_samples.size:
        raise ValueError("I/Q captures must have equal length")
    payload = int(i_samples.size) - int(delay_samples)
    if payload <= 0 or payload % RRC_SCOPE_SPS:
        raise ValueError("captures must have length N*4+32")
    recovered = matched_filter_symbols(
        (i_samples, q_samples), delay_samples=delay_samples)
    i_labels, q_labels = center_sample_qpsk_labels(*recovered)
    aligned = _align_qpsk_labels(
        i_labels, q_labels, seed=seed, trim_symbols=trim_symbols)
    compared = 2 * (i_labels.size - 2 * int(trim_symbols))
    return float(aligned[0]) / compared


def check_rrc_qpsk_capture(i_capture, q_capture, seed=0x7f,
                           delay_samples=RRC_SCOPE_DELAY_SAMPLES,
                           trim_symbols=RRC_SCOPE_TRANSIENT_SYMBOLS):
    """Return whether an offset-binary QPSK capture has BER zero."""
    return rrc_qpsk_ber(
        i_capture, q_capture, seed=seed, delay_samples=delay_samples,
        trim_symbols=trim_symbols) == 0.0


def rrc_expected_waveform_from_symbols(i_symbols, q_symbols, rolloff="0.35"):
    i_symbols = np.asarray(i_symbols, dtype=np.int16)
    q_symbols = np.asarray(q_symbols, dtype=np.int16)
    if (i_symbols.ndim != 1 or q_symbols.ndim != 1
            or i_symbols.size == 0 or i_symbols.size != q_symbols.size):
        raise ValueError("expected I/Q symbols must be equal-length 1-D arrays")
    return np.vstack(rrc_filter_symbols(
        i_symbols, q_symbols, coeffs=rrc_coefficients(alpha=float(rolloff))))


def rrc_expected_waveform(mode, symrate_hz, symbol_count, seed=0x7f,
                          rolloff="0.35"):
    if symrate_hz <= 0 or symbol_count < 1:
        raise ValueError("RRC scope reference requires positive parameters")
    i_sym, q_sym = generate_symbols(mode, symbol_count, seed=seed)
    return rrc_expected_waveform_from_symbols(i_sym, q_sym, rolloff=rolloff)


def match_filter_capture(i_capture, q_capture, symrate_hz, xinc,
                         sample_offset=0.0, rolloff="0.35"):
    i_capture = np.asarray(i_capture, dtype=np.float64)
    q_capture = np.asarray(q_capture, dtype=np.float64)
    if (i_capture.ndim != 1 or q_capture.ndim != 1
            or len(i_capture) != len(q_capture)
            or not np.all(np.isfinite(i_capture))
            or not np.all(np.isfinite(q_capture))):
        raise ValueError("I/Q captures must be equal-length one-dimensional arrays")
    if (i_capture.size and np.all((0 <= i_capture) & (i_capture <= 255))
            and q_capture.size and np.all((0 <= q_capture) & (q_capture <= 255))):
        i_capture = offset_binary_to_signed(i_capture).astype(np.float64)
        q_capture = offset_binary_to_signed(q_capture).astype(np.float64)
    if (len(i_capture) == 0 or symrate_hz <= 0 or xinc <= 0
            or not np.all(np.isfinite(i_capture))
            or not np.all(np.isfinite(q_capture))
            or not np.isfinite(symrate_hz) or not np.isfinite(xinc)):
        raise ValueError("capture and timing parameters must be positive")

    target_xinc = 1.0 / (4.0 * symrate_hz)
    if not np.isfinite(sample_offset) or not 0.0 <= sample_offset < RRC_SCOPE_SPS * target_xinc:
        raise ValueError("sample_offset must be within one symbol period")
    source_time = np.arange(len(i_capture), dtype=np.float64) * xinc
    target_count = int(np.floor((source_time[-1] - sample_offset)
                                / target_xinc)) + 1
    target_count = (RRC_SCOPE_DELAY_SAMPLES + RRC_SCOPE_SPS
                    * ((target_count - RRC_SCOPE_DELAY_SAMPLES) // RRC_SCOPE_SPS))
    if target_count <= RRC_SCOPE_DELAY_SAMPLES:
        return np.zeros(0, dtype=np.int16), np.zeros(0, dtype=np.int16)
    target_time = sample_offset + np.arange(target_count, dtype=np.float64) * target_xinc
    i_grid = np.interp(target_time, source_time, i_capture)
    q_grid = np.interp(target_time, source_time, q_capture)
    i_grid -= float(np.mean(i_grid))
    q_grid -= float(np.mean(q_grid))
    peak = max(float(np.max(np.abs(i_grid))), float(np.max(np.abs(q_grid))))
    if peak == 0.0:
        return np.zeros(0, dtype=np.int16), np.zeros(0, dtype=np.int16)
    i_grid = np.clip(i_grid / peak * RRC_SCOPE_MATCH_INPUT_PEAK,
                     -RRC_SCOPE_MATCH_INPUT_PEAK,
                     RRC_SCOPE_MATCH_INPUT_PEAK).astype(np.int16)
    q_grid = np.clip(q_grid / peak * RRC_SCOPE_MATCH_INPUT_PEAK,
                     -RRC_SCOPE_MATCH_INPUT_PEAK,
                     RRC_SCOPE_MATCH_INPUT_PEAK).astype(np.int16)
    return matched_filter_symbols(
        (i_grid, q_grid), delay_samples=32,
        coeffs=rrc_coefficients(alpha=float(rolloff)))


def rrc_symbol_ber(recovered, expected, trim_symbols=8, symbol_offset=0,
                   allow_polarity_inversion=True, mode="QPSK"):
    recovered_i, recovered_q = recovered
    recovered_i = np.asarray(recovered_i)
    recovered_q = np.asarray(recovered_q)
    expected = np.asarray(expected)
    if (expected.ndim != 2 or expected.shape[0] != 2 or expected.shape[1] < 32
            or (expected.shape[1] - 32) % 4
            or recovered_i.ndim != 1 or recovered_q.ndim != 1
            or len(recovered_i) != len(recovered_q)):
        raise ValueError("invalid RRC reference or recovered arrays")
    if trim_symbols < 0 or not isinstance(symbol_offset, (int, np.integer)):
        raise ValueError("invalid RRC alignment parameters")
    symbol_count = (expected.shape[1] - 32) // 4
    if len(recovered_i) < symbol_count:
        raise ValueError("capture contains fewer symbols than the reference")
    symbol_offset %= 127
    expected_i, expected_q = generate_symbols(mode, 127, seed=0x7f)
    reference_indices = (symbol_offset + np.arange(symbol_count)) % 127
    expected_i = expected_i[reference_indices]
    expected_q = expected_q[reference_indices]
    start = trim_symbols
    stop = symbol_count - trim_symbols
    if stop <= start:
        raise ValueError("capture is shorter than the transient guard")
    recovered_i = recovered_i[:symbol_count].astype(np.float64)
    recovered_q = recovered_q[:symbol_count].astype(np.float64)
    if mode != "QPSK":
        # 匹配滤波输出是幅度归一化到线性区的值,多电平星座需先归一到
        # 参考符号满幅(±126)再量化到最近轴电平(QPSK 走符号判决,免缩放)。
        # ⚠ **必须用 constellation_scale(轴平均幅度), 不能用全局峰值。** 数据相关的
        #   包络过冲会把峰值抬高(实测 peak/轴平均 = 1.06~1.11), 峰值归一化于是整体
        #   压低所有电平, QAM64 外圈(±126)被压到 ±108 判决边界之下而错判成内圈 90。
        #   实测 QAM64 α=0.2 @100kSym/s 连采 20 次: 峰值归一化 3 次报 BER≠0
        #   (0.0045/0.0135, 出错样点 -106.2/-105.6 本该是 -126), 换轴平均后 0 次。
        #   这正是 align_known_sequence(USER/DDR 路径)早已采用的做法 —— 当年只修了
        #   那条路径, 内部 RRC 判据漏了。BPSK/QAM4/QPSK 在 RRC_AXIS_LEVELS 里没有
        #   条目, constellation_scale 返回 None, 仍回落峰值(与旧行为一致)。
        scale = constellation_scale(recovered_i, recovered_q, mode)
        if scale is None or scale <= 0.0:
            scale = max(float(np.max(np.abs(recovered_i))),
                        float(np.max(np.abs(recovered_q))))
        if scale > 0:
            recovered_i = recovered_i * (126.0 / scale)
            recovered_q = recovered_q * (126.0 / scale)
    recovered_i, recovered_q = center_sample_labels(recovered_i, recovered_q, mode)
    direct_errors = np.count_nonzero(recovered_i[start:stop] != expected_i[start:stop])
    direct_errors += np.count_nonzero(recovered_q[start:stop] != expected_q[start:stop])
    if not allow_polarity_inversion:
        return float(direct_errors) / (2 * (stop - start))
    inverted_errors = np.count_nonzero(-recovered_i[start:stop] != expected_i[start:stop])
    inverted_errors += np.count_nonzero(-recovered_q[start:stop] != expected_q[start:stop])
    return float(min(direct_errors, inverted_errors)) / (2 * (stop - start))


def constellation_scale(i_samples, q_samples, mode):
    """把捕获归一化到参考电平 ±126 的尺度(按星座平均幅度估计),无适用星座时返回 None。

    不能取全局峰值: 数据相关的包络过冲会把峰值抬高 1.2~1.5 倍, 归一化后 QAM16 外圈
    均值落到内外圈判决边界(84)之下而被整体错判成内圈(USER UART QAM16 实测 BER 由
    0.004 涨到 0.26)。PAM 轴平均幅度由星座决定, 而均值对少量离群点不敏感。
    """
    axis = RRC_AXIS_LEVELS.get(mode)
    if axis is None:
        return None
    mag = np.concatenate([np.abs(np.asarray(i_samples, dtype=np.float64)),
                          np.abs(np.asarray(q_samples, dtype=np.float64))])
    if mag.size == 0:
        return None
    mean_mag = float(np.mean(mag))
    if not np.isfinite(mean_mag) or mean_mag <= 0.0:
        return None
    return mean_mag * 126.0 / float(np.mean(np.abs(axis)))


def align_known_sequence(recovered, exp_i, exp_q, mode, trim_symbols=8,
                         allow_polarity_inversion=True):
    """对已知符号序列做相位搜索,返回 (ber, symbol_offset, inverted)。

    与 rrc_symbol_ber 不同,参考不是 PRBS 模 127 循环,而是调用方给定的任意
    符号序列(如 USER/DDR payload 的期望符号);搜索每个循环相位,跳过每个
    循环边界处的 RRC 瞬态(trim_symbols),可选尝试整体极性反转。
    """
    recovered_i, recovered_q = recovered
    recovered_i = np.asarray(recovered_i, dtype=np.float64)
    recovered_q = np.asarray(recovered_q, dtype=np.float64)
    exp_i = np.asarray(exp_i, dtype=np.int16)
    exp_q = np.asarray(exp_q, dtype=np.int16)
    if (recovered_i.ndim != 1 or recovered_q.ndim != 1
            or recovered_i.size != recovered_q.size):
        raise ValueError("recovered I/Q must be equal-length 1-D arrays")
    if (exp_i.ndim != 1 or exp_q.ndim != 1 or exp_i.size != exp_q.size
            or exp_i.size == 0):
        raise ValueError("expected I/Q must be equal-length 1-D arrays")
    if trim_symbols < 0 or exp_i.size <= 2 * trim_symbols:
        raise ValueError("expected sequence shorter than the transient guard")
    if recovered_i.size <= 2 * trim_symbols:
        raise ValueError("capture shorter than the transient guard")
    if mode != "QPSK":
        # 匹配滤波输出幅度归一化到线性区,多电平星座需先归一到参考满幅再量化。
        scale = constellation_scale(recovered_i, recovered_q, mode)
        if scale is None:
            scale = max(float(np.max(np.abs(recovered_i))),
                        float(np.max(np.abs(recovered_q))))
        if scale > 0.0:
            recovered_i = recovered_i * (126.0 / scale)
            recovered_q = recovered_q * (126.0 / scale)
    labels_i, labels_q = center_sample_labels(recovered_i, recovered_q, mode)
    period = int(exp_i.size)
    positions = np.arange(labels_i.size)
    polarities = (False, True) if allow_polarity_inversion else (False,)
    best = None
    for offset in range(period):
        idx = (offset + positions) % period
        inlier = (idx >= trim_symbols) & (idx < period - trim_symbols)
        compared = 2 * int(np.count_nonzero(inlier))
        if compared == 0:
            continue
        ref_i = exp_i[idx]
        ref_q = exp_q[idx]
        for inverted in polarities:
            sign = -1 if inverted else 1
            errors = int(np.count_nonzero(labels_i[inlier] != sign * ref_i[inlier]))
            errors += int(np.count_nonzero(labels_q[inlier] != sign * ref_q[inlier]))
            ber = float(errors) / compared
            if best is None or ber < best[0]:
                best = (ber, offset, inverted)
    if best is None:
        raise ValueError("expected sequence has no inlier symbols")
    return best


def _align_rrc_capture(i_capture, q_capture, symrate_hz, xinc, expected,
                       mode="QPSK", rolloff="0.35"):
    target_xinc = 1.0 / (4.0 * symrate_hz)
    best = None
    for timing_offset in np.linspace(
            0.0, RRC_SCOPE_SPS * target_xinc, 32, endpoint=False):
        try:
            recovered = match_filter_capture(
                i_capture, q_capture, symrate_hz, xinc,
                sample_offset=float(timing_offset), rolloff=rolloff,
            )
        except ValueError:
            continue
        if len(recovered[0]) < (expected.shape[1] - 32) // 4:
            continue
        for symbol_offset in range(127):
            ber = rrc_symbol_ber(
                recovered, expected, trim_symbols=8,
                symbol_offset=symbol_offset,
                allow_polarity_inversion=False, mode=mode,
            )
            candidate = (ber, symbol_offset, float(timing_offset), recovered)
            if best is None or candidate[0] < best[0]:
                best = candidate
    return best


def _align_iq_capture(i_capture, q_capture, symrate_hz, xinc, trim_symbols=IQ_TRIM,
                      rolloff=IQ_ROLLOFF, mode=IQ_MODE, seed=0x7f, timing_steps=64):
    """IQIMP 捕获的 (timing, 循环符号偏移) 联合搜索; **旋转不敏感**。

    判据是**差分**相关: d_k = r_k·conj(r_{k-1}) 与期望序列的差分比模。差分同时消掉
    (a) 静态相位偏移带来的**恒定旋转** 和 (b) 频偏带来的**每符号等速率旋转** ——
    所以星座被转着走时照样锁得住。这两点是必须的:
      * 直接复相关 |Σ r_k conj(e_k)| 在静态旋转下可用, 但 72deg/sym 的频偏会把相干
        和转平, 于是一堆错误的符号偏移也能拿到相近的模, 实测锁错;
      * 基于 BER 的对齐在 45 度旋转下失锁(QPSK 判决点正好落在边界上)。

    ⚠ **绝不能"先在 IQIMP OFF 时对齐一次、之后复用这组偏移"。** 板上 PRBS 自由运行
    + 示波器异步触发 ⇒ 每次捕获落在序列里的位置都不同: 实测同一配置连续 6 次捕获的
    符号偏移为 110/107/95/47/100/4, 时间偏移在 0~2.8us 间跳。旧实现复用一次对齐的
    结果, 于是把"捕获起点漂移"误读成"星座整体旋转", 报出 45deg→141.5deg、
    72deg/sym→5.1deg/sym 这种既不等于模型、也不等于旧 RTL 症状的假失败。

    返回 (score, symbol_offset, timing_offset, 对齐后的复数恢复符号, 对齐后的期望符号)。
    """
    ei, eq = generate_symbols(mode, 127, seed=seed)
    e_all = ei.astype(np.float64) + 1j * eq.astype(np.float64)
    span = e_all.size
    d_ref = e_all * np.conj(np.roll(e_all, 1))
    idx = (np.arange(span)[None, :] + np.arange(span)[:, None]) % span
    e_mat = e_all[idx]                 # [s, k] = E[(k + s) % span]
    d_mat = np.conj(d_ref[idx])        # [s, k]
    target_xinc = 1.0 / (4.0 * symrate_hz)
    best = None
    for timing in np.linspace(0.0, 4.0 * target_xinc, timing_steps, endpoint=False):
        rec = match_filter_capture(i_capture, q_capture, symrate_hz, xinc,
                                   sample_offset=float(timing), rolloff=rolloff)
        ri = np.asarray(rec[0], dtype=np.float64)
        rq = np.asarray(rec[1], dtype=np.float64)
        if ri.size < trim_symbols + span:
            continue
        r = ri[trim_symbols:trim_symbols + span] + 1j * rq[trim_symbols:trim_symbols + span]
        d = r[1:] * np.conj(r[:-1])
        metric = np.abs(d_mat[:, 1:] @ d)
        s = int(np.argmax(metric))
        if best is None or metric[s] > best[0]:
            best = (float(metric[s]), s, float(timing), r, e_mat[s])
    if best is None:
        raise ValueError("capture is too short for I/Q alignment")
    return best


def iq_rotation_deg(aligned):
    """对齐后的 (恢复, 期望) -> 链路上的恒定旋转角(度)。"""
    _score, _s, _tim, r, e = aligned
    return float(np.degrees(np.angle(np.vdot(e, r))))


def rrc_spectrum_rolloff(i_capture, q_capture, symrate_hz, xinc):
    i_capture = np.asarray(i_capture, dtype=np.float64)
    q_capture = np.asarray(q_capture, dtype=np.float64)
    if (i_capture.ndim != 1 or q_capture.ndim != 1
            or len(i_capture) != len(q_capture) or len(i_capture) < 8
            or not np.isfinite(symrate_hz) or symrate_hz <= 0
            or not np.isfinite(xinc) or xinc <= 0
            or not np.all(np.isfinite(i_capture))
            or not np.all(np.isfinite(q_capture))):
        return None
    i_capture = i_capture - np.mean(i_capture)
    q_capture = q_capture - np.mean(q_capture)
    window = np.hanning(len(i_capture))
    i_spectrum = np.abs(np.fft.rfft(i_capture * window))
    q_spectrum = np.abs(np.fft.rfft(q_capture * window))
    spectrum = np.hypot(i_spectrum, q_spectrum)
    freqs = np.fft.rfftfreq(len(i_capture), xinc)
    inband = (freqs > 0) & (freqs <= 0.70 * symrate_hz)
    stopband = (freqs >= 1.00 * symrate_hz) & (freqs <= 2.00 * symrate_hz)
    if not np.any(inband) or not np.any(stopband):
        return None
    inband_level = float(np.mean(spectrum[inband]))
    if not np.isfinite(inband_level) or inband_level <= 0:
        return None
    return float(np.mean(spectrum[stopband]) / inband_level)


# ---------- 信道损伤 ----------
# 两点等增益多径: |H(f)| = 2·(g/256)·|cos(π f d / fs)|, 在 f=fs/(2d) 处深零点。
# Q8 单位增益只能取 255(9-bit 有符号表示不了 256), 故增益按 255/256 计。
# d=50 使零点落在 fs/(2d)=500kHz; 探针频率须 <= FREQ_MAX(1MHz)。
CHAN_TAP_GAIN = 255
CHAN_TAP_DELAY = 50                  # 采样点 @50MHz -> 回波 1us
CHAN_SAMPLE_RATE = 50_000_000
CHAN_COMB_FREQS = (100_000, 250_000, 500_000)     # 含 f=fs/(2d) 深零点
CHAN_TEST_AMPL = 40                  # 两抽头等增益峰值 ~2x, 须留饱和裕量(不要 >50)
CHAN_NOISE_LEVELS = (0, 20, 40)      # level 40 时数字域 σ=50.8LSB, 2.5σ 才到饱和边界
CHAN_TONE_FREQ = 200_000          # 单音探针频率(Hz; 窗 200us -> bin 5kHz, 落在 bin 40)
CHAN_TONE_AMPLS = (20, 40, 60)    # 幅度单调性扫描(%满幅)
CHAN_IMP_PERIOD = 2000            # 冲激周期(DAC 采样点 @50MHz -> 40us)
CHAN_IMP_WIDTH = 64               # 冲激宽度(DAC 采样点 -> 1.28us; 必须够宽, 见上)
CHAN_IMP_AMPLS = (20, 40, 60)     # 冲激幅度扫描(%满幅): 规格 §7 要求幅度随 ampl 线性,
                                  # 单点比对证不了这一点(接成常数也会过), 故取 2~3 点判单调

# ---- 干扰源互斥: 每个信道损伤用例必须显式归零自己**不测**的干扰源 ----
# `CHAN ON` 时 chan_impair 把使能的干扰源全部叠加进输出
# (`y = sat(fir_out + noise + tone + impulse, ±127)`); 而 TONE/IMPULSE 在板上的状态来自
# **上一次运行**(实测: test_bridge 结束时留下 `IMPULSE 1000 8 -100`, ampl≠0 即使能),
# 不是开机默认 0。不归零就会把别的干扰当成被测项的量 —— 实测多径 |H(f)| 被抬到 2.195
# (模型 1.895), AWGN level-0 底噪被抬到 10.6 LSB(= 127·√(8/1000), 冲激的 duty×幅度)。
CHAN_QUIET = ("SET CH1 CHAN TONE 0 0", "SET CH1 CHAN IMPULSE 1 1 0")


def multipath_frequency_response(gains, delays, sample_rate_hz, freqs):
    """静态多径幅度响应 |H(f)| = |Σ g_k/256 · exp(-j2π f d_k / fs)|。

    gains 为 Q8 有符号增益(与 chan_impair/fade_fir 一致), delays 为采样点。
    """
    gains = np.asarray(gains, dtype=np.float64) / 256.0
    delays = np.asarray(delays, dtype=np.float64)
    if gains.size == 0 or gains.size != delays.size:
        raise ValueError("gains and delays must be equal-length and non-empty")
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive")
    freqs = np.asarray(freqs, dtype=np.float64)
    return [float(abs(np.sum(gains * np.exp(-2j * np.pi * f * delays / sample_rate_hz))))
            for f in freqs]


def waveform_sigma(samples):
    """捕获波形去均值后的标准差(与纵向刻度同单位, 用于量 AWGN 噪底)。"""
    y = np.asarray(samples, dtype=np.float64)
    if y.size == 0:
        raise ValueError("empty waveform")
    return float(np.std(y - np.mean(y)))


def chan_vpp_volts():
    """CHAN1 的 Vpp(伏)。measure() 不设 :MEAS:SOUR, 必须显式指定通道。"""
    inst.write(":MEAS:SOUR CHAN1")
    return measure()[1]


def reset_board():
    # 顺序重要: 必须先归零谐波幅值再设 BASE。BASE 100 时若残留 h1_amp>0,
    # 桥会归一化(100+50=150 -> base=66), 复位后正弦仅 66/100 摆幅。
    for ch in ("CH1", "CH2"):
        for c in ("SET %s WAVE SINE" % ch, "SET %s FREQ 1000" % ch,
                  "SET %s AMPL 50" % ch,
                  "SET %s DUTY 50" % ch,
                  "SET %s HARM1 ORDER 0" % ch, "SET %s HARM1 AMPL 0" % ch,
                  "SET %s HARM2 ORDER 0" % ch, "SET %s HARM2 AMPL 0" % ch,
                  "SET %s HARM3 ORDER 0" % ch, "SET %s HARM3 AMPL 0" % ch,
                  "SET %s HARM4 ORDER 0" % ch, "SET %s HARM4 AMPL 0" % ch,
                  "SET %s BASE 100" % ch):
            raw(c)
    # 调制复位: 防上次运行的调制状态(AM/FM/PM/SWEEP)泄漏到校准与基础用例
    for ch in ("CH1", "CH2"):
        for c in ("SET %s MODE OFF" % ch, "SET %s MODWAVE SINE" % ch,
                  "SET %s MODFREQ 10" % ch, "SET %s AMDEPTH 100" % ch,
                  "SET %s FMDEV 0" % ch, "SET %s PMDEV 0" % ch):
            raw(c)


def main():
    print("== scope_verify: board=%s scope=%s" % (PORT, SCOPE))
    open_resources()
    reset_board()
    scope_setup()

    # 校准: SQUARE 10kHz 100%(参考数据 9.56~9.62V)
    setup_board(["SET CH1 WAVE SQUARE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100", "SET CH1 BASE 0"])
    scope_acq(20e-6, 2.0)
    f, cal = measure()
    if f is None or cal is None:
        report("calibrate SQUARE 10k 100%", False, "scope not responding")
        print("RESULT: 0/1 PASS (aborted)")
        sys.exit(1)
    cal_ok = CAL_WIN[0] <= cal <= CAL_WIN[1]
    report("calibrate SQUARE 10k 100%", cal_ok,
           "Vpp=%.3fV freq=%.1fHz (window %.2f~%.2f)" % (cal, f, *CAL_WIN))

    # 每个用例显式 SET BASE 0(走纯 dds_core 路径,不受残留谐波状态影响)
    cases = [
        # (名称, SET 命令, 期望频率, 幅度比例, 时基/div, 垂直刻度/div)
        ("SINE 1kHz 100%",      ["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 100", "SET CH1 BASE 0"], 1000, 1.0, 200e-6, 2.0),
        ("SINE 100kHz 80%",     ["SET CH1 WAVE SINE", "SET CH1 FREQ 100000", "SET CH1 AMPL 80", "SET CH1 BASE 0"], 100000, 0.8, 2e-6, 2.0),
        ("SINE 100kHz 20%",     ["SET CH1 WAVE SINE", "SET CH1 FREQ 100000", "SET CH1 AMPL 20", "SET CH1 BASE 0"], 100000, 0.2, 2e-6, 0.5),
        ("SQUARE 10kHz 100%",   ["SET CH1 WAVE SQUARE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100", "SET CH1 BASE 0"], 10000, 1.0, 20e-6, 2.0),
        ("SQUARE 1kHz 50%",     ["SET CH1 WAVE SQUARE", "SET CH1 FREQ 1000", "SET CH1 AMPL 50", "SET CH1 BASE 0"], 1000, 0.5, 200e-6, 1.0),
        ("TRIANGLE 2.5kHz 50%", ["SET CH1 WAVE TRIANGLE", "SET CH1 FREQ 2500", "SET CH1 AMPL 50", "SET CH1 BASE 0"], 2500, 0.5, 100e-6, 1.0),
        ("SAW 2.5kHz 20%",      ["SET CH1 WAVE SAW", "SET CH1 FREQ 2500", "SET CH1 AMPL 20", "SET CH1 BASE 0"], 2500, 0.2, 100e-6, 0.5),
    ]
    for name, cmds, fexp, frac, tim_s, chan_s in cases:
        setup_board(cmds)
        scope_acq(tim_s, chan_s)
        f, v = measure()
        freq_ok = f is not None and abs(f - fexp) <= fexp * 0.05
        vexp = cal * frac
        # 100kHz 以上有 ~3% 带宽滚降(校准文档记录), 容差放宽到 5% 防边缘误报
        vtol = max(vexp * (0.05 if fexp >= 50000 else 0.03), 0.05)
        vpp_ok = v is not None and abs(v - vexp) <= vtol
        report(name, freq_ok and vpp_ok,
               "freq %.1fHz(exp %d +-5%%) Vpp %.3fV(exp %.3f +-%.3f)"
               % (f or -1, fexp, v or -1, vexp, vtol))

    # PULSE 25%: verify carrier frequency, full swing, and physical high-level ratio.
    setup_board(["SET CH1 WAVE PULSE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 DUTY 25", "SET CH1 BASE 0", "SET CH1 MODE OFF"])
    scope_acq(20e-6, 2.0)
    f, v = measure()
    try:
        xinc, pulse = scope_wave("CHAN1")
        lo, hi = float(np.min(pulse)), float(np.max(pulse))
        midpoint = (lo + hi) / 2.0
        physical_high_ratio = float(np.mean(pulse > midpoint))
        freq_ok = f is not None and abs(f - 10000) <= 500
        vpp_ok = v is not None and abs(v - cal) <= max(cal * 0.05, 0.05)
        duty_ok = 0.20 <= physical_high_ratio <= 0.30
        report("PULSE 10kHz 25%", freq_ok and vpp_ok and duty_ok,
               "freq %.1fHz Vpp %.3fV high-time %.1f%%"
               % (f or -1, v or -1, physical_high_ratio * 100.0))
    except Exception as e:
        report("PULSE 10kHz 25%", False, "waveform read failed: %r" % e)

    # NOISE 10kHz: FREQ controls the refresh bandwidth; verify bipolar full-scale statistics.
    setup_board(["SET CH1 WAVE NOISE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0", "SET CH1 MODE OFF"])
    scope_acq(20e-6, 2.0)
    f, v = measure()
    try:
        xinc, noise = scope_wave("CHAN1")
        lo, hi = float(np.min(noise)), float(np.max(noise))
        span = hi - lo
        vpp_ok = v is not None and v > cal * 0.75
        stats_ok = span > 100.0 and float(np.std(noise)) > 20.0
        report("NOISE 10kHz 100%", vpp_ok and stats_ok,
               "Vpp %.3fV span %.0f codes std %.1f"
               % (v or -1, span, float(np.std(noise))))
    except Exception as e:
        report("NOISE 10kHz 100%", False, "waveform read failed: %r" % e)

    # 谐波 FFT: 基波 50% + 2 次谐波 50% -> 输出 0.5*sin(wt)+0.5*sin(2wt)
    # (两正弦叠加峰值 0.88*满摆幅,Vpp 非简单比例,故仅做 FFT 频谱检查)
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 50", "SET CH1 HARM1 ORDER 2", "SET CH1 HARM1 AMPL 50",
                 "SET CH1 HARM2 ORDER 0", "SET CH1 HARM2 AMPL 0",
                 "SET CH1 HARM3 ORDER 0", "SET CH1 HARM3 AMPL 0",
                 "SET CH1 HARM4 ORDER 0", "SET CH1 HARM4 AMPL 0"])
    scope_acq(200e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m1 = (freqs > 500) & (freqs < 1500)
        m2 = (freqs > 1500) & (freqs < 3000)
        f1 = float(freqs[m1][np.argmax(spec[m1])])
        a1 = float(np.max(spec[m1]))
        f2 = float(freqs[m2][np.argmax(spec[m2])])
        a2 = float(np.max(spec[m2]))
        ratio = a2 / a1 if a1 else 0.0
        n = 2 * (len(spec) - 1)
        ok = a2 > 0.3 * a1 and abs(f2 - 2 * f1) / (2 * f1) < 0.1
        report("HARM FFT base50+h2:50", ok,
               "f1=%.0fHz a1=%.1f f2=%.0fHz a2=%.1f ratio=%.2f n=%d" % (f1, a1, f2, a2, ratio, n))
    except Exception as e:
        report("HARM FFT base50+h2:50", False, "waveform read failed: %r" % e)

    # ---- 调制闭环 ----
    # 协议约定: MODFREQ 为 0.1Hz 单位(桥存 mfreq_01, PL 侧 fword = mfreq×562950),
    # 故 1kHz 调制 = MODFREQ 10000, 100Hz = MODFREQ 1000。
    # 先清谐波: HARM 用例残留 HARM1 ORDER 2/AMPL 50, 不清会把载波换成 2 次谐波
    setup_board(["SET CH1 HARM1 ORDER 0", "SET CH1 HARM1 AMPL 0",
                 "SET CH1 HARM2 ORDER 0", "SET CH1 HARM2 AMPL 0",
                 "SET CH1 HARM3 ORDER 0", "SET CH1 HARM3 AMPL 0",
                 "SET CH1 HARM4 ORDER 0", "SET CH1 HARM4 AMPL 0"])

    # AM 包络: 载波 10k + 1kHz/100% 正弦调制 -> 包络 0..100% -> Vpp ≈ cal。
    # 注: AMPL 在 am_stage 之后缩放, AMPL 50 时包络峰值 = 2×50%×AMPL = 50%FS,
    # Vpp 仅 cal/2(实测 4.8V); AMPL 100 时包络峰值 100%FS(饱和) -> Vpp ≈ cal。
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE AM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 0"])
    scope_acq(200e-6, 2.0)
    f, v = measure()
    am_ok = v is not None and abs(v - cal) <= cal * 0.06
    report("AM envelope 10k + 1kHz/100%", am_ok,
           "Vpp=%.3fV (exp ~%.3f +-6%%)" % (v or -1, cal))
    # AM 边带判别: 死 AM 通路(增益卡 12700)输出纯正弦, Vpp 仍 ≈ cal(am_stage
    # 饱和), 包络检查盲区 -> 回读波形 FFT 查 10k±1k 边带(活 AM ratio≈0.41,
    # 死通路 ~0.02-0.04)。Vpp 需全包络窗, 保留 200us/div 采集; FFT 换 400us/div
    # 重新采集(同 FM, 频率分辨率余量更大)。
    scope_acq(400e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m_c = (freqs > 9500) & (freqs < 10500)
        m_s = ((freqs > 8500) & (freqs < 9500)) | ((freqs > 10500) & (freqs < 11500))
        ac = float(np.max(spec[m_c]))
        asb = float(np.max(spec[m_s]))
        am_ok = am_ok and ac > 0 and asb > 0.1 * ac
        report("AM sidebands f0+fm (depth 100)", asb > 0.1 * ac,
               "carrier=%.1f side=%.1f ratio=%.2f" % (ac, asb, asb / ac if ac else 0))
    except Exception as e:
        report("AM sidebands f0+fm (depth 100)", False, "waveform read failed: %r" % e)

    # FM 边带: 载波 10k 100% + 1kHz 正弦, fm_dev 2000 -> f0±fm 边带 (beta=2,
    # J1/J0≈2.58; 实测 carrier=0.18M side=0.47M ratio≈2.6)
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE FM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 2000", "SET CH1 PMDEV 0"])
    scope_acq(400e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m_c = (freqs > 9500) & (freqs < 10500)
        m_s = ((freqs > 8500) & (freqs < 9500)) | ((freqs > 10500) & (freqs < 11500))
        ac = float(np.max(spec[m_c]))
        asb = float(np.max(spec[m_s]))
        ok = ac > 0 and asb > 0.1 * ac
        report("FM sidebands f0+fm (dev 2kHz)", ok,
               "carrier=%.1f side=%.1f ratio=%.2f" % (ac, asb, asb / ac if ac else 0))
    except Exception as e:
        report("FM sidebands f0+fm (dev 2kHz)", False, "waveform read failed: %r" % e)

    # PM 边带: pm_dev 64 = 90 度 -> f0±fm 边带 (beta=pi/2, J1/J0≈1.20)
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE PM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 64"])
    scope_acq(400e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m_c = (freqs > 9500) & (freqs < 10500)
        m_s = ((freqs > 8500) & (freqs < 9500)) | ((freqs > 10500) & (freqs < 11500))
        ac = float(np.max(spec[m_c]))
        asb = float(np.max(spec[m_s]))
        ok = ac > 0 and asb > 0.05 * ac
        report("PM sidebands f0+fm (dev 90)", ok,
               "carrier=%.1f side=%.1f ratio=%.2f" % (ac, asb, asb / ac if ac else 0))
    except Exception as e:
        report("PM sidebands f0+fm (dev 90)", False, "waveform read failed: %r" % e)

    # SWEEP 象限频率: 载波 10k 100%, 三角波 100Hz(MODFREQ 1000), fm_dev 5000
    # -> 瞬时频率 5k..15k。2ms/div(20ms 窗 = 2 个调制周期, 采样 320ns)切成 8 个
    # 2.5ms 象限块; 4 块 = 1 个调制周期, 块 k 与 k+4 同相位。
    # 三角波半周期反对称(相隔 2 块的象限频率互补, f_k + f_{k+2} = 2×f0)。
    # 调制相位与采集窗相对相位任意(acc_m 自由运行), 固定下标是掷硬币: 取 8 象限
    # 中频率最高者 f_max 与最低者 f_min(半周期反对称保证两者覆盖完整扫频幅度),
    # 断言每块均过零(fail-closed, 频块过零数不足时估频不可信)且
    # f_max > f_min + 2000(理论差 >=5k, 实测 ~6k)。
    # 过零计数受 DAC 中间码进位毛刺污染(伪过零成对: 上升沿处 ~1us 双计数、下降沿
    # 处半周期成对; 320ns 采样下捕获率 ~10-15%, 1ms/div 的 160ns 采样下 ~50%,
    # 会把中值拉到污染边界, 故此处用 2ms/div)。用间隔中值(毛刺 <50% 时稳健)估频。
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE SWEEP", "SET CH1 MODWAVE TRIANGLE", "SET CH1 MODFREQ 1000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 5000", "SET CH1 PMDEV 0"])
    scope_acq(2e-3, 2.0)
    try:
        xinc = float(inst.query(":WAV:XINC?"))
        data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
        inst.write(":RUN")
        y = np.array(data, dtype=np.uint8).astype(float)
        mid = np.mean(y)
        n8 = len(y) // 8

        def freq_chunk(a):
            zc = np.where((a[:-1] < mid) & (a[1:] >= mid))[0]
            if len(zc) < 4:
                return 0.0
            sp = np.diff(zc) * xinc
            return 1.0 / np.median(sp)  # 中值周期 -> 频率(抗毛刺伪过零)

        fq = np.array([freq_chunk(y[i * n8:(i + 1) * n8]) for i in range(8)])
        f_max = float(np.max(fq))
        f_min = float(np.min(fq))
        ok = np.all(fq > 0) and (f_max - f_min) > 2000
        report("SWEEP tri +-5kHz (f_max>f_min)", ok,
               "f_max=%.0fHz f_min=%.0fHz" % (f_max, f_min))
    except Exception as e:
        report("SWEEP tri +-5kHz (f_max>f_min)", False, "waveform read failed: %r" % e)

    # ---- 相位联动: CH1/CH2 同频 1kHz, 相位差 0/90/180°, 频率跟随与解除 ----
    # 探头: CH1 接 DAC0 输出、CH2 接 DAC1 输出。:STOP 冻结后分读两通道波形
    # (同一冻结帧, 时间轴对齐), 上升过零时间差换算相位差。
    # RTL 侧 phase_ext2 = phase_acc1 + ch_phase_off(awg_top.v): CH2 相位 = CH1 相位
    # + 偏移。过零时间差换算时 CH2 超前定义为正。
    inst.write(":CHAN2:DISP ON")     # 原有用例只配置 CH1, 相位对比需 CH2 可见
    inst.write(":CHAN2:SCAL 2.0")    # 与 CH1 同刻度, 保证同 ADC 量化

    def frozen():
        """:STOP 冻结 -> 分读两通道波形 -> :RUN 恢复;返回 (xinc, y1, y2)。"""
        inst.write(":STOP")
        try:
            xinc, y1 = scope_wave("CHAN1")
            _, y2 = scope_wave("CHAN2")
            return xinc, y1, y2
        finally:
            inst.write(":RUN")

    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 100", "SET CH1 BASE 0",
                 "SET CH1 MODE OFF", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 10",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 0",
                 "SET CH2 WAVE SINE", "SET CH2 FREQ 1000", "SET CH2 AMPL 100", "SET CH2 BASE 0",
                 "SET CH2 MODE OFF", "SET CH2 MODWAVE SINE", "SET CH2 MODFREQ 10",
                 "SET CH2 AMDEPTH 100", "SET CH2 FMDEV 0", "SET CH2 PMDEV 0",
                 "SET CH2 PHASE 0", "SET CH2 PHASELINK ON"])
    scope_acq(400e-6, 2.0)          # 1kHz: 400us/div = 4ms 窗, 至少 4 个周期
    try:
        xinc, y1, y2 = frozen()
        ph = phase_from_zero_cross(xinc, y1, y2)
        phase_err = phase_error_deg(ph, 0.0)
        report("PHASELINK 0deg", phase_err is not None and phase_err < 5.0,
               "phase=%.1f deg (exp 0)" % (ph if ph is not None else -1))
    except Exception as e:
        report("PHASELINK 0deg", False, "waveform read failed: %r" % e)

    setup_board(["SET CH2 PHASE 9000"])
    scope_acq(400e-6, 2.0)
    try:
        xinc, y1, y2 = frozen()
        ph = phase_from_zero_cross(xinc, y1, y2)
        phase_err = phase_error_deg(ph, 90.0)
        report("PHASELINK 90deg", phase_err is not None and phase_err < 5.0,
               "phase=%.1f deg (exp 90)" % (ph if ph is not None else -1))
    except Exception as e:
        report("PHASELINK 90deg", False, "waveform read failed: %r" % e)

    setup_board(["SET CH2 PHASE 18000"])
    scope_acq(400e-6, 2.0)
    try:
        xinc, y1, y2 = frozen()
        ph = phase_from_zero_cross(xinc, y1, y2)
        phase_err = phase_error_deg(ph, 180.0)
        report("PHASELINK 180deg", phase_err is not None and phase_err < 5.0,
               "phase=%.1f deg (exp 180)" % (ph if ph is not None else -1))
    except Exception as e:
        report("PHASELINK 180deg", False, "waveform read failed: %r" % e)

    # 频率跟随: 改 CH1 频率 -> CH2 同步(相位差 90° 保持)
    setup_board(["SET CH1 FREQ 2000", "SET CH2 PHASE 9000"])
    scope_acq(200e-6, 2.0)          # 2kHz: 200us/div = 2ms 窗, 至少 4 个周期
    try:
        xinc, y1, y2 = frozen()
        ph = phase_from_zero_cross(xinc, y1, y2)
        f1 = frequency_from_zero_cross(xinc, y1)
        f2 = frequency_from_zero_cross(xinc, y2)
        freq_ok = (f1 is not None and f2 is not None
                   and abs(f1 - 2000) <= 100 and abs(f2 - 2000) <= 100
                   and abs(f1 - f2) <= 100)
        phase_err = phase_error_deg(ph, 90.0)
        phase_ok = phase_err is not None and phase_err < 5.0
        report("PHASELINK freq follow 2kHz", freq_ok and phase_ok,
               "freq1=%.1fHz freq2=%.1fHz phase=%.1f deg (exp 2000Hz/90)"
               % (f1 if f1 is not None else -1,
                  f2 if f2 is not None else -1,
                  ph if ph is not None else -1))
    except Exception as e:
        report("PHASELINK freq follow 2kHz", False, "waveform read failed: %r" % e)

    # 解除联动: CH2 独立(改 CH2 频率不跟随, 相位差无意义但不应报错)。
    # 注意: measure() 只读 CHAN1, 此时 CH1 仍为 2kHz -> 必须直接读 CH2 波形估频。
    setup_board(["SET CH2 PHASELINK OFF", "SET CH2 FREQ 3000"])
    scope_acq(100e-6, 2.0)
    try:
        inst.write(":STOP")
        xinc, y2 = scope_wave("CHAN2")
        f2 = frequency_from_zero_cross(xinc, y2)
        f2 = f2 if f2 is not None else 0.0
        report("PHASELINK off ch2 independent",
               f2 > 0 and abs(f2 - 3000) <= 150,
               "ch2 freq=%.1fHz (exp 3000)" % f2)
    except Exception as e:
        report("PHASELINK off ch2 independent", False, "waveform read failed: %r" % e)
    finally:
        inst.write(":RUN")

    # ---- 数字调制双通道时域(DAC0=I, DAC1=Q) ----
    # 示波器 BYTE 原始波形含边沿插值/量化码,不能直接用 len(set(samples)) 判电平数;
    # 改为 1D k-means(固定簇数=期望电平数)聚类后校验: 簇中心即为实际电平, 样本内
    # 距簇心 <=2.5 码的比例应占绝对多数(实测 >=0.999), 相邻簇最小间距须大于实测下限
    # (BPSK 113, QPSK 63, QAM16 20, QAM64 8.8 码), 否则视为簇坍缩/混叠。
    def _cluster_levels(y, count):
        """返回 (sorted_centers, labels, inlier_frac)。"""
        y = np.asarray(y, dtype=float)
        lo, hi = np.percentile(y, [1.0, 99.0])
        centers = np.linspace(lo, hi, count)
        for _ in range(30):
            labels = np.argmin(np.abs(y[:, None] - centers[None, :]), axis=1)
            new = np.array([y[labels == k].mean() if np.any(labels == k)
                            else centers[k] for k in range(count)])
            if np.allclose(new, centers, atol=0.01):
                break
            centers = new
        order = np.argsort(centers)
        centers = centers[order]
        labels = np.argmin(np.abs(y[:, None] - centers[None, :]), axis=1)
        dist = np.abs(y - centers[labels])
        return centers, labels, float(np.mean(dist <= 2.5))

    # (名称, selector, mode, symbol rate, 时基, I 期望电平数, Q 期望电平数)
    DIGMINSEP = {"BPSK": (40.0, 40.0), "QPSK": (20.0, 20.0),
                 "QAM16": (10.0, 10.0), "QAM64": (5.0, 5.0)}
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 50"])
    setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT", "SET DIGMOD SYMRATE 10000"])
    digital_cases = (
        ("BPSK", 1, "BPSK", 10000, 2e-3, 2, 1),
        ("QPSK", 2, "QPSK", 10000, 2e-3, 2, 2),
        ("QAM16", 4, "QAM16", 10000, 2e-3, 4, 4),
        ("QAM64", 5, "QAM64", 10000, 6e-3, 8, 8),
        ("QPSK 100kSym/s", 2, "QPSK", 100000, 200e-6, 2, 2),
        ("QPSK 1MSym/s", 2, "QPSK", 1000000, 20e-6, 2, 2),
    )
    for name, sel, mode, symrate, tim_s, i_n, q_n in digital_cases:
        setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD SYMRATE %d" % symrate,
                     "SET DIGMOD MODE %s" % mode])
        scope_acq(tim_s, 2.0)
        try:
            xinc0, y0 = scope_wave("CHAN1")
            xinc1, y1 = scope_wave("CHAN2")
            i_c, i_l, i_inl = _cluster_levels(y0, i_n)
            q_c, q_l, q_inl = _cluster_levels(y1, q_n)
            i_minsep = float(np.min(np.diff(i_c))) if len(i_c) > 1 else 1e9
            q_minsep = float(np.min(np.diff(q_c))) if len(q_c) > 1 else 1e9
            i_span = float(i_c[-1] - i_c[0])
            q_span = float(q_c[-1] - q_c[0])
            i_sym_err = abs((i_c[0] + i_c[-1]) - 2.0 * 0x80)
            q_sym_err = abs((q_c[0] + q_c[-1]) - 2.0 * 0x80)
            i_minsep_ok = i_minsep > DIGMINSEP[mode][0]
            q_minsep_ok = q_minsep > DIGMINSEP[mode][1]
            inlier_min = digital_inlier_threshold(symrate)
            i_inl_ok = i_inl >= inlier_min
            q_inl_ok = q_inl >= inlier_min
            i_sym_ok = i_span > 0 and i_sym_err <= max(2.0, i_span * 0.25)
            q_sym_ok = ((q_span > 0 and q_sym_err <= max(2.0, q_span * 0.25))
                        or (q_n == 1 and abs(q_c[0] - 0x80) <= 2.0))
            q_center_ok = q_n == 1 and abs(q_c[0] - 0x80) <= 2.0
            xinc = max(xinc0, xinc1)
            symbol_period = symbol_period_from_iq_labels(i_l, q_l, xinc)
            expected_period = 1.0 / symrate
            period_tol = max(2.0 * xinc, expected_period * 0.20)
            period_ok = (symbol_period is not None
                         and abs(symbol_period - expected_period) <= period_tol)
            capture_window = min(len(y0) * xinc0, len(y1) * xinc1)
            window_ok = capture_window >= 64.0 * expected_period
            ok = (i_minsep_ok and q_minsep_ok and i_inl_ok and q_inl_ok
                  and i_sym_ok and q_sym_ok and period_ok and window_ok
                  and (name != "BPSK" or q_center_ok))
            detail = ("sel=%d rate=%d I=%d Q=%d minsepI=%.1f minsepQ=%.1f "
                      "inlI=%.3f inlQ=%.3f symI=%.1f symQ=%.1f "
                      "period=%.1fus exp=%.1fus tol=%.1fus window=%.1fms"
                      % (sel, symrate, i_n, q_n, i_minsep, q_minsep, i_inl, q_inl,
                         i_sym_err, q_sym_err,
                         symbol_period * 1e6 if symbol_period is not None else -1,
                         expected_period * 1e6, period_tol * 1e6,
                         capture_window * 1e3))
            report("%s I/Q 双通道时域" % name, ok, detail)
        except Exception as e:
            report("%s I/Q 双通道时域" % name, False, "waveform read failed: %r" % e)

    # ---- RRC 双通道匹配滤波验收(多星座 × 多滚降) ----
    # 示波器起点相对 PRBS 周期未知，在固定的 4-SPS 时序相位和 127 符号
    # 循环内对齐；匹配滤波中心位置固定为总延迟 32 个采样。
    # 用例列表从 RRC_SCOPE_CASES 派生,避免两处维护。字段:
    # RRC_SCOPE_CASES = (name, mode, rate, rolloff, window);此处固定 chan_s=2.0。
    rrc_cases = tuple((mode, rate, rolloff, window, 2.0)
                      for _, mode, rate, rolloff, window in RRC_SCOPE_CASES)
    for mode, symrate, rolloff, tim_s, chan_s in rrc_cases:
        try:
            setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                         "SET DIGMOD ROLLOFF %s" % rolloff,
                         "SET DIGMOD SYMRATE %d" % symrate,
                         "SET DIGMOD MODE %s" % mode])
            expected = rrc_expected_waveform(mode, symrate, 127, rolloff=rolloff)
            expected_status = (
                "DIGMOD MODE %s SOURCE PRBS7 FILTER RRC ROLLOFF %s "
                "SEED 127 SYMRATE %d STATE RUNNING"
                % (mode, rolloff, symrate)
            )
            status = serial_line("GET DIGMOD")
            status_ok = status == expected_status
            scope_acq(tim_s, chan_s)
            xinc0, y0 = scope_wave("CHAN1")
            xinc1, y1 = scope_wave("CHAN2")
            xinc_ok = np.isclose(xinc0, xinc1, rtol=1e-6, atol=1e-15)
            alignment = (_align_rrc_capture(
                scope_capture_to_digital(y0), scope_capture_to_digital(y1),
                symrate, xinc0, expected, mode=mode, rolloff=rolloff,
            ) if xinc_ok else None)
            if alignment is None:
                center_ok = constellation_ok = eye_ok = ber_ok = False
                ber = None
                symbol_offset = None
                timing_offset = None
                recovered = (np.zeros(0), np.zeros(0))
            else:
                ber, symbol_offset, timing_offset, recovered = alignment
                center_ok = len(recovered[0]) >= 127
                valid = slice(8, 119)
                # 匹配滤波输出是 ±8192 归一化样本,直接符号量化会把 QAM 的
                # 多电平星座幅度信息抹掉(所有正向/负向都落到同一角点)。QAM
                # 与 rrc_symbol_ber 用同一套 constellation_scale(轴平均幅度)归一到
                # 参考满幅再做星座量化;QPSK 走符号判决,幅度不变,不缩放。
                # ⚠ 别退回全局峰值: 包络过冲会把外圈压到判决边界之下(见
                #   rrc_symbol_ber 里的实测记录)。
                ri = np.asarray(recovered[0][valid], dtype=np.float64)
                rq = np.asarray(recovered[1][valid], dtype=np.float64)
                if mode != "QPSK":
                    scale = constellation_scale(ri, rq, mode)
                    if scale is None or scale <= 0.0:
                        scale = max(float(np.max(np.abs(ri))),
                                    float(np.max(np.abs(rq))))
                    if scale > 0.0:
                        ri = ri * (126.0 / scale)
                        rq = rq * (126.0 / scale)
                # 眼图裕度基于归一化后的原始样点(未做星座量化);若用 ±127
                # 符号标签,QPSK 的裕度会退化为常数 127,失去张开度诊断意义。
                eye_margin = rrc_eye_margin(ri, rq, mode=mode)
                eye_ok = np.isfinite(eye_margin) and eye_margin > 0.0
                labels = center_sample_labels(ri, rq, mode)
                points = set(zip(labels[0].tolist(), labels[1].tolist()))
                valid_points = set(map(tuple, constellation_points(mode).tolist()))
                # BPSK 只有 2 个星座点, 其余模式要求至少出现 4 个不同点
                # (证明星座未坍缩), QAM 无需出现全部电平。
                min_points = 2 if mode == "BPSK" else 4
                constellation_ok = points <= valid_points and len(points) >= min_points
                ber_ok = ber == 0.0
            # ⚠ 这个测量值**不是**配置的滚降系数: 它是阻带/带内幅度比(越小频谱越干净)。
            #   绝不能赋给 rolloff —— 那会把报告里的用例名写成 "α=<比值>"。实测 13 条
            #   RRC 标签全部如此(0.003~0.009), 文档据此把失败用例误记成 "α≈0.0085 组",
            #   而真正失败的是 α=0.2。判据用法本身没错(下面 spectrum_ok 就用它)。
            spec_ratio = rrc_spectrum_rolloff(y0, y1, symrate, xinc0)
            spectrum_ok = spec_ratio is not None and spec_ratio < 0.2
            rrc_ok = (status_ok and xinc_ok and center_ok and constellation_ok
                      and eye_ok and ber_ok and spectrum_ok)
            report("RRC %s %dSym/s α=%s matched BER" % (mode, symrate, rolloff), rrc_ok,
                   "status=%s center=%s constellation=%s eye=%s BER=%s "
                   "prbs_offset=%s timing=%.3fus spec_ratio=%s"
                   % ("OK" if status_ok else "BAD", center_ok,
                      constellation_ok, eye_ok,
                      "0" if ber is not None and ber == 0.0 else str(ber),
                      str(symbol_offset),
                      timing_offset * 1e6 if timing_offset is not None else -1.0,
                      "%.3f" % spec_ratio if spec_ratio is not None else "NA"))
        except Exception as e:
            report("RRC %s %dSym/s α=%s matched BER" % (mode, symrate, rolloff), False,
                   "capture or match failed: %r" % e)

        try:
            setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                         "SET CH1 WAVE SINE", "SET CH1 FREQ 1000",
                         "SET CH1 AMPL 50", "SET CH1 BASE 0"])
            scope_acq(2e-3, 2.0)
            xinc, off_wave = scope_wave("CHAN1")
            off_freq = frequency_from_zero_cross(xinc, off_wave)
            off_min, off_max = float(np.min(off_wave)), float(np.max(off_wave))
            off_span = off_max - off_min
            off_ok = (off_freq is not None
                      and abs(off_freq - 1000.0) <= 100.0
                      and off_min < 0x80 < off_max and off_span >= 20.0)
            report("RRC %dSym/s OFF -> RECT 1kHz" % symrate, off_ok,
                   "freq=%.1fHz min=%.0f max=%.0f span=%.0f"
                   % (off_freq if off_freq is not None else -1.0,
                      off_min, off_max, off_span))
        except Exception as e:
            report("RRC %dSym/s OFF -> RECT 1kHz" % symrate, False,
                   "recovery failed: %r" % e)

    # ---- 信道损伤: 两点等增益多径的梳状 |H(f)| ----
    # 基准取 CHAN OFF(逐位透传, 增益 1), 故 测得比 = |H(f)|, 与模拟链路频响无关。
    chan_comb = "CH1 信道两点多径 |H(f)|"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 AMPL %d" % CHAN_TEST_AMPL,
                     "SET CH1 BASE 0", "SET CH1 TAP CLEAR",
                     "SET CH1 NOISE 0"] + list(CHAN_QUIET)
                    + ["SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT"])
        baseline = []
        for freq in CHAN_COMB_FREQS:
            setup_board(["SET CH1 FREQ %d" % freq])
            scope_acq(20e-6, 1.0)
            baseline.append(chan_vpp_volts())
        setup_board(["SET CH1 TAP 0 %d 0" % CHAN_TAP_GAIN,
                     "SET CH1 TAP 1 %d %d" % (CHAN_TAP_GAIN, CHAN_TAP_DELAY)]
                    + list(CHAN_QUIET)
                    + ["SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"])
        measured = []
        for freq in CHAN_COMB_FREQS:
            setup_board(["SET CH1 FREQ %d" % freq])
            scope_acq(20e-6, 1.0)
            measured.append(chan_vpp_volts())
        expected = multipath_frequency_response(
            [CHAN_TAP_GAIN, CHAN_TAP_GAIN, 0, 0], [0, CHAN_TAP_DELAY, 0, 0],
            CHAN_SAMPLE_RATE, CHAN_COMB_FREQS)
        ok = True
        detail = []
        for freq, base, got, want in zip(CHAN_COMB_FREQS, baseline, measured, expected):
            if not base or not got:
                ok = False
                detail.append("%.3gMHz: no reading" % (freq / 1e6))
                continue
            ratio = got / base
            # 深零点附近只用上界判据(残差由抽头取整决定, 不能按相对误差比)
            good = (ratio < 0.10) if want < 0.20 else (abs(ratio - want) <= 0.12 * want)
            ok = ok and good
            detail.append("%.3gMHz measured=%.3f expected=%.3f%s"
                          % (freq / 1e6, ratio, want, "" if good else " FAIL"))
        report(chan_comb, ok, "; ".join(detail))
    except Exception as e:
        report(chan_comb, False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT"])
        except Exception:
            pass

    # ---- 信道损伤: AWGN 噪底 ----
    # CHAN ON + 抽头全清 -> FIR 输出为 0, 输出只剩噪声, 噪底可直接量。
    # 绝对刻度用同纵向刻度的 AMPL 100 正弦作参照(其 Vpp 对应 254 DAC LSB)。
    # 注意只判"相对"行为: noise_gen 的 σ=level/100×127 是**逐样点数字域**量, 经 DAC
    # 零阶保持 sinc + 模拟重建滤波后, 示波器上测到的模拟域 σ 只有它的约 0.67 倍
    # (实测该比值在 level 5..30 上稳定 0.667~0.685, 且与时间基/AMPL 无关), 故不能拿
    # 数字域 σ 直接比对; 细节里回报实测/模型比以便观察这条链路是否有变化。
    chan_noise = "CH1 信道 AWGN 噪底"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 100",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0",
                     "SET CH1 TAP CLEAR"] + list(CHAN_QUIET)
                    + ["SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT"])
        scope_acq(2e-3, 2.0)
        _, ref_bytes = scope_wave("CHAN1")
        ref_span = float(np.max(ref_bytes) - np.min(ref_bytes))
        setup_board(list(CHAN_QUIET)
                    + ["SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"])
        sigma = []
        for level in CHAN_NOISE_LEVELS:
            setup_board(["SET CH1 NOISE %d" % level, "SET CH1 CHAN COMMIT"])
            scope_acq(2e-3, 2.0)
            _, noise_bytes = scope_wave("CHAN1")
            sigma.append(waveform_sigma(noise_bytes) * 254.0 / ref_span)
        model = [level * 127.0 / 100.0 for level in CHAN_NOISE_LEVELS]
        silent_ok = sigma[0] <= 0.20 * sigma[1]          # level 0 必须基本无噪声
        linear_ok = abs(sigma[2] / sigma[1] - model[2] / model[1]) <= 0.25
        report(chan_noise, silent_ok and linear_ok,
               "sigma(LSB)=%s model=%s analog/model at top level=%.2f"
               % ([round(s, 1) for s in sigma], [round(m, 1) for m in model],
                  sigma[2] / model[2]))
    except Exception as e:
        report(chan_noise, False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET CH1 NOISE 0", "SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT"])
        except Exception:
            pass

    # ---- 信道损伤: 窄带单音干扰 ----
    # TAP CLEAR 把四个抽头增益清零 -> fade_fir 输出恒 0, 故单音是唯一 AC 分量,
    # FFT 谱线可直接量。**故意不动 AMPL**: 抽头全 0 时 AMPL 对输出没有影响,
    # 而后面「DIGMOD OFF 恢复标准正弦」用例需要 AWG 信号回来, 动它就会污染后续用例。
    # 判据只判"谱线出现在设定频率 + 幅度随 ampl 单调增长", 不做绝对幅度比对:
    # 经 DAC 零阶保持 + 模拟重建后幅度会被削(与噪声 σ 同一机制)。
    chan_tone = "CH1 信道窄带单音干扰"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0", "SET CH1 TAP CLEAR",
                     "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"]
                    + list(CHAN_QUIET)
                    + ["SET CH1 CHAN TONE %d 0" % CHAN_TONE_FREQ,
                       "SET CH1 CHAN COMMIT"])
        scope_acq(20e-6, 1.0)
        freqs, spec = wav_fft()
        base_peak = float(np.max(spec[(freqs > 150000) & (freqs < 250000)])) if \
            ((freqs > 150000) & (freqs < 250000)).any() else 0.0
        peaks = []
        for ampl in CHAN_TONE_AMPLS:
            setup_board(["SET CH1 CHAN TONE %d %d" % (CHAN_TONE_FREQ, ampl),
                         "SET CH1 CHAN COMMIT"])
            scope_acq(20e-6, 1.0)
            freqs, spec = wav_fft()
            band = (freqs > 150000) & (freqs < 250000)
            idx = np.argmax(spec[band])
            peaks.append((float(freqs[band][idx]), float(spec[band][idx])))
        # 谱线必须在设定频率附近(容差 = 频带 1/10)
        freq_ok = all(abs(f - CHAN_TONE_FREQ) < 10000.0 for f, _ in peaks)
        # 关掉单音时该频带只剩本底。用 max(base_peak, 1.0) 兜底: 8-bit 捕获下
        # 完全静默会使 base_peak 为 0 或极小, 直接做除数会得到无意义的比值。
        amp_ok = peaks[0][1] > 3.0 * max(base_peak, 1.0)
        # 上面那条只证明"有信号", 证明不了"幅度随 ampl 增长"。注释里承诺的
        # 单调性必须真的断言, 否则 ampl 字段接错(例如写成常数)也会通过。
        mono_ok = peaks[1][1] > peaks[0][1] and peaks[2][1] > peaks[1][1]
        report(chan_tone, freq_ok and amp_ok and mono_ok,
               "peaks=%s baseline=%.1f mono=%s"
               % ([(round(f), round(a, 1)) for f, a in peaks], base_peak, mono_ok))
    except Exception as e:
        report(chan_tone, False, "failed: %r" % e)
    finally:
        try:
            # ⚠ **必须把 CHAN 关回去。** 本用例开了 CHAN ON, 而 chan_impair 在
            #   enable=1 时会把 AWG 输入换掉(`sample_out <= enable ? clamped : sample_in`),
            #   抽头又是全 0, 于是输出恒 0 —— 后面「DIGMOD OFF 恢复标准正弦」用例
            #   只发 `SET DIGMOD MODE OFF` 就要求 CHAN1 出现 1kHz 正弦(span>=20),
            #   不关回去它必然读成一条直线而 FAIL。同理, 下一次运行开头的校准用例
            #   (SQUARE 100%)也依赖 CHAN 处于关闭态, 因为 `reset_board()` 并不清 `chan_word`。
            setup_board(["SET CH1 CHAN TONE 0 0", "SET CH1 CHAN OFF",
                         "SET CH1 CHAN COMMIT"])
        except Exception:
            pass

    # ---- 信道损伤: 周期冲激干扰 ----
    # period=2000 样点(40us, 25kHz), width=64 样点(1.28us) ——
    # 必须用宽冲激: 1 个采样点的冲激会被 DAC 零阶保持 + 模拟重建削平,
    # 与噪声 σ 掉到 0.65 倍是同一机制。
    # ⚠ 时基用 200us/div(2ms 窗)而**不是** 2ms/div: 示波器记录长度有限, 长时基会
    # 把采样率压低(2ms/div 下 XINC 约 100 倍), 1.28us 宽的冲激只剩几个示波器采样点,
    # 计数判据会变成噪声。2ms 窗内有约 50 个冲激, 足够统计。
    chan_imp = "CH1 信道周期冲激干扰"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0", "SET CH1 TAP CLEAR",
                     "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"]
                    + list(CHAN_QUIET)
                    + ["SET CH1 CHAN IMPULSE %d %d 0" % (CHAN_IMP_PERIOD, CHAN_IMP_WIDTH),
                       "SET CH1 CHAN COMMIT"])
        scope_acq(200e-6, 1.0)
        _, base = scope_wave("CHAN1")
        base_span = float(np.max(base) - np.min(base))
        # 幅度扫描: 每档测 span(峰峰值), 用最大幅度那一档的捕获做计数/采样充分性判据
        # (信噪比最好)。脉冲是**负向**的, 但 span 取 max-min 与极性无关, 故单调性判据
        # 不受 ACM2108 反相影响, 与下面按数据判极性的计数逻辑并存不矛盾。
        spans = []
        xinc, y = None, None
        for ampl in CHAN_IMP_AMPLS:
            setup_board(["SET CH1 CHAN IMPULSE %d %d %d"
                         % (CHAN_IMP_PERIOD, CHAN_IMP_WIDTH, ampl),
                         "SET CH1 CHAN COMMIT"])
            scope_acq(200e-6, 1.0)
            xinc, y = scope_wave("CHAN1")
            spans.append(float(np.max(y) - np.min(y)))
        y = y - np.mean(y)
        thresh = 8.0
        # ⚠ 极性: ACM2108 模拟链路反相(CLAUDE.md 里 PULSE 用 8'h81 补偿的是同一件事),
        #   而 impulse_gen 输出的是**数字域正脉冲**(规格 `impulse[n] = ±A_imp/100×127`)。
        #   板上实测 ampl=+60 → 捕获 max=+4.5 / min=−67.5, 即负向脉冲; 按"向上穿过 +8"
        #   计数必然得 0。极性由数据判定, 不写死方向。
        sign = -1.0 if int(np.sum(y < -thresh)) > int(np.sum(y > thresh)) else 1.0
        z = sign * y                      # 使脉冲方向为正, 便于统一计数
        crossings = int(np.sum((z[:-1] <= thresh) & (z[1:] > thresh)))
        span = spans[-1]
        # 规格 §7 要求"周期冲激 … 幅度随 ampl 线性": 必须扫多点并断言单调增长,
        # 只比单点(60)与 ampl=0 的话, ampl 字段接成常数也会通过。
        mono_ok = all(spans[i + 1] > spans[i] for i in range(len(spans) - 1))
        # 窗内期望冲激数 = 采集窗时长 / 冲激周期时长。
        # 注意单位: period 是 **DAC 采样点**(50MHz 下 2000 点 = 40us), 不是示波器采样点;
        # 写成 y.size/period 会把两种采样点混在一起(会算出 31 而非 50)。
        window_s = y.size * xinc
        impulse_period_s = float(CHAN_IMP_PERIOD) / CHAN_SAMPLE_RATE
        impulse_width_s = float(CHAN_IMP_WIDTH) / CHAN_SAMPLE_RATE
        expected_count = window_s / impulse_period_s
        # 采样充分性: 冲激宽度必须有足够多的**示波器**采样点, 否则计数无意义。
        # 这是"测到了"与"猜的"之间的分界, 所以并入判据而不是只写进 detail。
        per_impulse_samples = impulse_width_s / xinc
        samples_ok = per_impulse_samples >= 4.0
        count_ok = abs(crossings - expected_count) <= max(0.25 * expected_count, 10.0)
        amp_ok = span > base_span + 20.0
        report(chan_imp, count_ok and amp_ok and samples_ok and mono_ok,
               "rise-crossings=%d (expect ~%d) spans=%s base=%.0f mono=%s "
               "xinc=%.1ens samples/impulse=%.1f"
               % (crossings, round(expected_count),
                  [round(s) for s in spans], base_span, mono_ok,
                  xinc * 1e9, per_impulse_samples))
    except Exception as e:
        report(chan_imp, False, "failed: %r" % e)
    finally:
        try:
            # 同单音用例: **必须 CHAN OFF**, 否则后面「DIGMOD OFF 恢复标准正弦」与
            # 下一次运行的校准用例都会因为 AWG 通路被顶替而读到直线。
            setup_board(["SET CH1 CHAN IMPULSE 1 1 0", "SET CH1 CHAN OFF",
                         "SET CH1 CHAN COMMIT"])
        except Exception:
            pass

    # ==================================================================
    # 复数 I/Q 损伤(顶层家族 IQIMP)
    # 判据范式与前面**不同**: 既有用例的核心判据是 BER == 0, 而这一级的**目的
    # 恰恰是打破链路**, 所以这里一律改成"实测统计量 ≈ 主机模型", 且不拿数字域
    # 理论值做绝对比对(过 DAC 零阶保持 + 模拟重建的量都有模拟域衰减, 与噪声 σ
    # 实测只有数字域 0.65 倍同一机制)。
    #
    # 对齐策略: **每帧重新对齐, 判据用旋转不敏感的差分相关**(见 _align_iq_capture)。
    # ✗ 曾经的写法是"先在 IQIMP OFF 时对齐一次, 记下 (timing, symbol_offset), 之后
    #   复用这组偏移"。它有一个致命前提: 捕获起点落到 PRBS 序列里的位置要固定。
    #   实测**不成立** —— 板上 PRBS 自由运行 + 示波器异步触发, 同一配置连续 6 帧的
    #   符号偏移是 110/107/95/47/100/4, 时间偏移在 0~2.8us 之间跳。复用旧偏移等于把
    #   "序列起点漂移"读成"星座整体旋转", 于是报出 45deg→141.5deg、72deg/sym→
    #   5.1deg/sym 这种既不符合主机模型、也不复现旧 RTL 症状的**假失败**。
    # 差分相关 d_k = r_k·conj(r_{k-1}) 同时消掉恒定旋转与每符号等速率旋转, 所以
    # 静态相位/增益失配/频偏三种损伤下都能锁住正确的符号偏移。
    # ==================================================================
    setup_board(["SET DIGMOD MODE OFF", "SET DIGMOD FILTER RRC",
                 "SET DIGMOD ROLLOFF %s" % IQ_ROLLOFF,
                 "SET DIGMOD SYMRATE %d" % IQ_RATE,
                 "SET DIGMOD MODE %s" % IQ_MODE,
                 "SET IQIMP OFF"])

    def iq_align():
        """采一帧并用差分相关重对齐 -> _align_iq_capture 的返回值。"""
        scope_acq(IQ_TIM_S, IQ_CHAN_S)
        _x, _y0 = scope_wave("CHAN1")
        _x1, _y1 = scope_wave("CHAN2")
        return _align_iq_capture(scope_capture_to_digital(_y0),
                                 scope_capture_to_digital(_y1), IQ_RATE, _x)

    iq_cases = ("I/Q 相位偏移", "I/Q 增益/相位失配", "I/Q 频率偏移")

    # ---- 用例 1: 相位偏移 -> 星座旋转角 == 设定值 ----
    try:
        setup_board(["SET IQIMP PHASE %d" % IQ_PHASE_DEG100,
                     "SET IQIMP FREQOFF 0", "SET IQIMP GAIN 16384",
                     "SET IQIMP PHASEERR 0", "SET IQIMP COMMIT",
                     "SET IQIMP ON"])
        _al = iq_align()
        rot = iq_rotation_deg(_al)
        want = IQ_PHASE_DEG100 / 100.0
        # 容差 5 度: 每帧重对齐后实测与设定值差 <0.1 度, 留 5 度只是防单帧毛刺
        ok = abs(rot - want) <= IQ_ROT_TOL_DEG
        report(iq_cases[0], ok,
               "measured=%.2fdeg model=%.1fdeg (sym_off=%d tim=%.3fus)"
               % (rot, want, _al[1], _al[2] * 1e6))
    except Exception as e:
        report(iq_cases[0], False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET IQIMP OFF"])
        except Exception:
            pass

    # ---- 用例 2: 增益/相位失配 -> 星座二阶统计 ----
    # 判据: 实测的 |相关系数| 与协方差特征值比, 与主机模型(同一矩阵作用在同一
    # 期望符号上)比。**不做绝对比对** —— 模拟域衰减会等比缩放, 但两个统计量都是
    # 尺度无关的比值, 所以可以直接比。
    try:
        from siggen.channel import apply_iq_impair, iq_impair_matrix
        a, b, c, d = iq_impair_matrix(0, IQ_GAIN_Q14, IQ_PHASEERR_DEG100)
        setup_board(["SET IQIMP PHASE 0", "SET IQIMP FREQOFF 0",
                     "SET IQIMP GAIN %d" % IQ_GAIN_Q14,
                     "SET IQIMP PHASEERR %d" % IQ_PHASEERR_DEG100,
                     "SET IQIMP COMMIT", "SET IQIMP ON"])
        _al = iq_align()            # ⚠ 只采一次: 调两次会拿到两份不同的捕获
        r, e = _al[3], _al[4]
        mi, mq = r.real, r.imag
        gi, gq = apply_iq_impair(list(e.real), list(e.imag), a, b, c, d, fword=0)
        gi = np.asarray(gi, dtype=np.float64)
        gq = np.asarray(gq, dtype=np.float64)

        def stats(x, y):
            xc, yc = x - x.mean(), y - y.mean()
            rho = float(np.corrcoef(xc, yc)[0, 1])
            cov = np.cov(np.vstack([xc, yc]))
            ev = np.linalg.eigvalsh(cov)
            return abs(rho), float(ev[1] / ev[0])   # 特征值比 >= 1

        rho_m, ratio_m = stats(mi, mq)
        rho_g, ratio_g = stats(gi, gq)
        # 相关: 单位阵时为 0, 失配时明显非 0; 只判"实测与模型同量级"
        corr_ok = abs(rho_m - rho_g) <= 0.15 and rho_m > 0.05
        ratio_ok = abs(ratio_m - ratio_g) <= 0.35 * max(ratio_g, 1.0)
        report(iq_cases[1], corr_ok and ratio_ok,
               "rho measured=%.3f model=%.3f | eig-ratio measured=%.3f model=%.3f"
               % (rho_m, rho_g, ratio_m, ratio_g))
    except Exception as e:
        report(iq_cases[1], False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET IQIMP OFF"])
        except Exception:
            pass

    # ---- 用例 3: 频率偏移 -> 连续符号上的旋转速率 == 设定 Δf ----
    # 每符号理论旋转 = 360 * Δf / symrate。Δf=20kHz @100kSym/s -> 72 度/符号,
    # 小于 180 度所以相位展开不会歧义。
    try:
        setup_board(["SET IQIMP PHASE 0", "SET IQIMP GAIN 16384",
                     "SET IQIMP PHASEERR 0",
                     "SET IQIMP FREQOFF %d" % IQ_FREQOFF_HZ,
                     "SET IQIMP COMMIT", "SET IQIMP ON"])
        _al = iq_align()
        r, e = _al[3], _al[4]
        phase = np.unwrap(np.angle(r / e))
        n = np.arange(phase.size, dtype=np.float64)
        slope_deg = float(np.degrees(np.polyfit(n, phase, 1)[0]))
        want = 360.0 * IQ_FREQOFF_HZ / IQ_RATE
        ok = abs(slope_deg - want) <= 0.15 * want
        report(iq_cases[2], ok,
               "slope=%.2fdeg/sym model=%.1fdeg/sym" % (slope_deg, want))
    except Exception as e:
        report(iq_cases[2], False, "failed: %r" % e)
    finally:
        try:
            # 必须复位到单位阵 + 关闭: 残留频偏会让其后所有数字调制用例整体旋转
            setup_board(["SET IQIMP PHASE 0", "SET IQIMP FREQOFF 0",
                         "SET IQIMP GAIN 16384", "SET IQIMP PHASEERR 0",
                         "SET IQIMP COMMIT", "SET IQIMP OFF"])
        except Exception:
            pass

    setup_board(["SET DIGMOD MODE OFF"])
    scope_acq(2e-3, 2.0)
    try:
        xinc0, y0 = scope_wave("CHAN1")
        off_freq = frequency_from_zero_cross(xinc0, y0)
        y_min, y_max = min(y0), max(y0)
        span = float(y_max - y_min)
        bipolar_ok = y_min < 0x80 < y_max
        span_ok = span >= 20.0
        freq_ok = off_freq is not None and abs(off_freq - 1000.0) <= 100.0
        off_ok = freq_ok and bipolar_ok and span_ok
        report("DIGMOD OFF 恢复标准正弦", off_ok,
               "freq=%.1fHz min=%d max=%d span=%.0f"
               % (off_freq if off_freq is not None else -1,
                  y_min, y_max, span))
    except Exception as e:
        report("DIGMOD OFF 恢复标准正弦", False, "waveform read failed: %r" % e)

    inst.close()
    npass = sum(1 for r in results if r)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
