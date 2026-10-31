"""静态多径 + 近似 AWGN 的整数算术模型（RTL `fade_fir.v`/`noise_gen.v` 的镜像）。"""

import math

GAIN_W = 9
DELAY_W = 9
TAPS = 4
DEPTH = 512
GAIN_MIN, GAIN_MAX = -256, 255
DELAY_MIN, DELAY_MAX = 0, 511
DAC_MIN, DAC_MAX = -127, 127   # 对称饱和边界, 镜像 awg_top.v rrc_to_dac(非 8-bit -128 下限)
NOISE_SIGMA_RAW = 148          # round(sqrt(4*(256^2-1)/12))
NOISE_SEED = 0x00C0FFEE


def round_shift_signed(value, shift):
    """符号对称四舍五入: value / 2**shift, 与 rrc.round_shift_signed 同法。"""
    value = int(value)
    if shift < 0:
        raise ValueError("shift must be non-negative")
    if value >= 0:
        return (value + (1 << (shift - 1))) >> shift if shift else value
    magnitude = -value
    rounded = (magnitude + (1 << (shift - 1))) >> shift if shift else magnitude
    return -rounded


def _round_div_signed(value, divisor):
    magnitude = abs(int(value))
    rounded = (magnitude + divisor // 2) // divisor
    return rounded if value >= 0 else -rounded


def _clamp(value, low, high):
    return low if value < low else high if value > high else value


def multipath_fir(samples, gains, delays):
    """y[n] = sat(round(Σ g_k·x[n−d_k] / 256), ±127)。gains 为 Q8, delays 为样本数。"""
    gains = list(gains)
    delays = list(delays)
    if len(gains) != TAPS or len(delays) != TAPS:
        raise ValueError("expected %d taps" % TAPS)
    for gain in gains:
        if not GAIN_MIN <= int(gain) <= GAIN_MAX:
            raise ValueError("tap gain out of range: %r" % (gain,))
    for delay in delays:
        if not DELAY_MIN <= int(delay) <= DELAY_MAX:
            raise ValueError("tap delay out of range: %r" % (delay,))
    xs = list(samples)
    out = []
    for index in range(len(xs)):
        acc = 0
        for gain, delay in zip(gains, delays):
            src = index - delay
            if src >= 0:
                acc += int(gain) * xs[src]
        out.append(_clamp(round_shift_signed(acc, 8), DAC_MIN, DAC_MAX))
    return out


def _xorshift32(state):
    state &= 0xFFFFFFFF
    state ^= (state << 13) & 0xFFFFFFFF
    state ^= state >> 17
    state ^= (state << 5) & 0xFFFFFFFF
    return state & 0xFFFFFFFF


def noise_samples(count, level, seed=NOISE_SEED):
    """确定性近似高斯噪声: 4 路 8-bit 均匀求和(CLT) → 去均值 → 按 σ=level·127/100 缩放。"""
    level = int(level)
    if not 0 <= level <= 100:
        raise ValueError("noise level out of range: %r" % (level,))
    sigma = level * 127 // 100
    state = int(seed) & 0xFFFFFFFF or 1
    out = []
    for _ in range(int(count)):
        state = _xorshift32(state)
        uniforms = [(state >> (8 * lane)) & 0xFF for lane in range(4)]
        centered = sum(uniforms) - 510
        out.append(_round_div_signed(centered * sigma, NOISE_SIGMA_RAW))
    return out


def add_noise(samples, level, seed=NOISE_SEED):
    xs = list(samples)
    noise = noise_samples(len(xs), level, seed)
    return [_clamp(int(x) + n, DAC_MIN, DAC_MAX) for x, n in zip(xs, noise)]


TONE_ACC_BITS = 24
# 与 src/rtl/sine_rom.v 同值(由 tools/gen_sine.py 生成)。
TONE_ROM = [round(127 * math.sin(2.0 * math.pi * i / 256)) & 0xFF for i in range(256)]
TONE_AMPL_MIN, TONE_AMPL_MAX = 0, 100
TONE_ROM_DIVISOR = 100
IMPULSE_MAX_PERIOD = 65535
IMPULSE_MAX_WIDTH = 255


def _signed8(value):
    return value - 256 if value >= 128 else value


def tone_samples(count, freq_hz, ampl, sample_rate_hz):
    """单音干扰的整数镜像（RTL `tone_gen.v`）。

    RTL 结构: acc[23:0] 每拍 += fword; 复用既有 sine_rom(该 ROM 是**组合**读,
    `always @(*) dout = mem[addr]`), 一拍延迟来自 tone_gen 自身的 sine_r 寄存器,
    故第 n 拍的 ROM 数据对应 acc[n-1]。随后 prod <= ampl*dout、再除以 100。
    串行延时使 dout 相对 acc 落后一拍，本模型显式编码该约定：
        addr[n] = fword * (n - 1) mod 2^24 >> 16      (n == 0 时取 0)
    """
    count = int(count)
    if count < 0:
        raise ValueError("count must be non-negative")
    ampl = int(ampl)
    if not TONE_AMPL_MIN <= ampl <= TONE_AMPL_MAX:
        raise ValueError("tone amplitude out of range: %r" % (ampl,))
    # 频率字与固件 sdk/src/main.c 的整数式一致: fword = (f<<24 + fs/2) // fs,
    # 即"四舍五入、半值向上"。Python 内建 round() 是 banker's rounding(半值取偶),
    # 两者在恰好 .5 边界会差 1 LSB。50MHz(fs 分母为奇数)下两者处处相同, 但通用
    # 采样率下必须与固件同式, 否则 host 预览与硬件频率字分叉。
    sample_rate = int(sample_rate_hz)
    fword = (int(freq_hz) * (1 << TONE_ACC_BITS) + sample_rate // 2) // sample_rate
    mask = (1 << TONE_ACC_BITS) - 1
    out = []
    for n in range(count):
        acc = 0 if n == 0 else (fword * (n - 1)) & mask
        sine_v = _signed8(TONE_ROM[(acc >> 16) & 0xFF])
        out.append(_round_div_signed(ampl * sine_v, TONE_ROM_DIVISOR))
    return out


def impulse_samples(count, period, width, ampl):
    """周期冲激干扰的整数镜像（RTL `impulse_gen.v`）。

    单个计数器 cnt 在 0..period-1 循环, cnt < width 时输出幅度（幅值 = ampl% × 127/100,
    符号随 ampl）。常幅度在 RTL 中是静态 BRAM 参数, 不在每样点路径上。
    """
    count = int(count)
    period = int(period)
    width = int(width)
    ampl = int(ampl)
    if count < 0:
        raise ValueError("count must be non-negative")
    if not 1 <= period <= IMPULSE_MAX_PERIOD:
        raise ValueError("impulse period out of range: %r" % (period,))
    if not 1 <= width <= IMPULSE_MAX_WIDTH:
        raise ValueError("impulse width out of range: %r" % (width,))
    if not -TONE_AMPL_MAX <= ampl <= TONE_AMPL_MAX:
        raise ValueError("impulse amplitude out of range: %r" % (ampl,))
    level = _round_div_signed(ampl * 127, TONE_ROM_DIVISOR)
    out = []
    for n in range(count):
        cnt = n % period
        out.append(level if cnt < width else 0)
    return out


# ---------- 复数 I/Q 损伤（RTL `iq_impair.v` 的整数镜像） ----------

SINCOS_BITS = 10
SINCOS_N = 1 << SINCOS_BITS            # 1024 点，相位分辨率 2π/1024 ≈ 0.35°
SINCOS_AMP = 32767                     # Q1.15 的 1.0
IQ_COEF_MIN, IQ_COEF_MAX = -32768, 32767   # Q2.14 原始码
IQ_GAIN_UNITY = 16384                  # Q2.14 的 1.0


def sincos_table():
    """1024 项 Q1.15 的 (cos, sin)，与 src/rtl/sincos_rom.v 同值。"""
    cos_v = [round(SINCOS_AMP * math.cos(2.0 * math.pi * i / SINCOS_N))
             for i in range(SINCOS_N)]
    sin_v = [round(SINCOS_AMP * math.sin(2.0 * math.pi * i / SINCOS_N))
             for i in range(SINCOS_N)]
    return cos_v, sin_v


def _q214(value):
    return _clamp(int(round(value * (1 << 14))), IQ_COEF_MIN, IQ_COEF_MAX)


def iq_impair_matrix(phase_off_deg100, gain_q14, phase_err_deg100):
    """主机侧合成静态 2×2：`R(phase_off) × diag(α, 1) × IQerr(phase_err)`。

    **硬件不需要知道用户设的是哪一项** —— 把三项合成一个矩阵是本结构的核心简化，
    所以合成就落在这里。返回 Q2.14 原始码 `(a, b, c, d)`，对应 `[[a, b], [c, d]]`。

    - `phase_off_deg100`：0.01° 单位（与 `SET CH2 PHASE` 同约定），整体旋转
    - `gain_q14`：I 轴增益（16384 = 1.0），表达 I/Q 增益失配
    - `phase_err_deg100`：0.01° 单位，正交相位误差，取标准模型
      `Q' = Q·cos φ − I·sin φ`（Q 不再与 I 严格差 90°）
    """
    th = math.radians(int(phase_off_deg100) / 100.0)
    ph = math.radians(int(phase_err_deg100) / 100.0)
    alpha = int(gain_q14) / float(IQ_GAIN_UNITY)
    ct, st = math.cos(th), math.sin(th)
    cp, sp = math.cos(ph), math.sin(ph)
    # R·D·E 展开（见上式），已用 90°/单位阵/半增益三个用例钉住
    return (_q214(ct * alpha + st * sp), _q214(-st * cp),
            _q214(st * alpha - ct * sp), _q214(ct * cp))


def apply_iq_impair(i_samples, q_samples, a, b, c, d, fword=0):
    """静态 2×2 → 旋转 2×2 的整数镜像（RTL `iq_impair.v`）。

    逐级与 RTL 同法（Q2.14 矩阵系数、Q1.15 的 cos/sin、对称饱和 ±127）：
        stage1: i1 = sat(round_shift_signed(a·i0 + b·q0, 14))
                q1 = sat(round_shift_signed(c·i0 + d·q0, 14))
        stage2: arch = (n·fword mod 2³²) >> 22
                io = sat(round_shift_signed(i1·cos − q1·sin, 15))
                qo = sat(round_shift_signed(i1·sin + q1·cos, 15))

    ⚠ **相位索引约定与 `tone_samples` 不同，故意如此。** `sine_rom` 是**组合**读
    （`always @(*) dout = mem[addr]`），故 tone_gen 的 dout 相对 acc 落后一拍，
    模型写成 `acc[n-1]`。这里的 `sincos_rom` 是**同步**读：ROM 在自己的寄存器上
    锁存 `mem[phase[31:22]]`，而样本也在同一个时钟沿被寄存，于是"同一拍的样本"
    与"同一拍的 ROM 输出"天然配对 —— 第 n 个样本用 `n·fword`，不是 `(n−1)·fword`。
    照抄 tone_samples 的偏移会让整条旋转慢一拍（表现为恒定相位偏置，不报错）。
    """
    for name, v in (("a", a), ("b", b), ("c", c), ("d", d)):
        if not IQ_COEF_MIN <= int(v) <= IQ_COEF_MAX:
            raise ValueError("iq coefficient out of range: %s=%r" % (name, v))
    cos_t, sin_t = sincos_table()
    mask = (1 << 32) - 1
    out_i, out_q = [], []
    for n, (i0, q0) in enumerate(zip(list(i_samples), list(q_samples))):
        i1 = _clamp(round_shift_signed(a * int(i0) + b * int(q0), 14),
                    DAC_MIN, DAC_MAX)
        q1 = _clamp(round_shift_signed(c * int(i0) + d * int(q0), 14),
                    DAC_MIN, DAC_MAX)
        addr = ((int(fword) * n) & mask) >> 22
        co, si = cos_t[addr], sin_t[addr]
        out_i.append(_clamp(round_shift_signed(i1 * co - q1 * si, 15),
                            DAC_MIN, DAC_MAX))
        out_q.append(_clamp(round_shift_signed(i1 * si + q1 * co, 15),
                            DAC_MIN, DAC_MAX))
    return out_i, out_q
