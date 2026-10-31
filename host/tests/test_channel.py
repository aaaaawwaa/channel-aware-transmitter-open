import numpy as np
import pytest

from siggen.channel import (
    DAC_MAX,
    DAC_MIN,
    NOISE_SIGMA_RAW,
    add_noise,
    multipath_fir,
    noise_samples,
    round_shift_signed,
)


def test_round_shift_signed_is_symmetric_half_away():
    assert round_shift_signed(255, 8) == 1      # 0.996 -> 1
    assert round_shift_signed(127, 8) == 0
    assert round_shift_signed(-255, 8) == -1
    assert round_shift_signed(256, 8) == 1
    assert round_shift_signed(384, 8) == 2      # 1.5 -> 2


def test_unity_tap_is_bit_exact_passthrough():
    # 仅列非饱和样本: -128 超出对称饱和域, 会被钳到 -127(见 multipath_saturates)
    x = [0, 10, -10, 100, -127, 127, 1]
    assert multipath_fir(x, [255, 0, 0, 0], [0, 0, 0, 0]) == x


def test_two_tap_echo_matches_hand_computed():
    # g=[255,128] d=[0,2]: y[n] = x[n] + 0.5*x[n-2]
    x = [0, 0, 0, 40, 0, 0, 0, 0]
    assert multipath_fir(x, [255, 128, 0, 0], [0, 2, 0, 0]) == \
        [0, 0, 0, 40, 0, 20, 0, 0]


def test_multipath_saturates():
    x = [100, 100]
    assert multipath_fir(x, [255, 255, 0, 0], [0, 0, 0, 0])[0] == DAC_MAX
    # 两点同址相加 = 199 -> 饱和 +127
    assert multipath_fir([100], [255, 255, 0, 0], [0, 0, 0, 0])[0] == 127
    # 负向 -199 -> 饱和 -127 (与 rrc_to_dac 对称)
    assert multipath_fir([-100], [255, 255, 0, 0], [0, 0, 0, 0])[0] == -127


def test_multipath_rejects_out_of_range_taps():
    with pytest.raises(ValueError):
        multipath_fir([0], [255, 0], [0, 0, 0])      # 长度不匹配
    with pytest.raises(ValueError):
        multipath_fir([0], [512, 0, 0, 0], [0, 0, 0, 0])   # 增益越界
    with pytest.raises(ValueError):
        multipath_fir([0], [255, 0, 0, 0], [512, 0, 0, 0])  # 延迟越界


def test_noise_sigma_raw_matches_clt():
    # 4 路均匀 [0,255] 之和的标准差 = sqrt(4*(256^2-1)/12) ≈ 147.9 -> 148
    assert NOISE_SIGMA_RAW == 148


def test_noise_is_zero_at_level_zero():
    assert noise_samples(16, 0, 0x00C0FFEE) == [0] * 16


def test_noise_is_deterministic_and_seed_dependent():
    a = noise_samples(32, 50, 0x00C0FFEE)
    b = noise_samples(32, 50, 0x00C0FFEE)
    c = noise_samples(32, 50, 0xDEADBEEF)
    assert a == b and a != c
    assert any(v != 0 for v in a)


def test_noise_rms_grows_with_level_and_matches_sigma():
    for level in (10, 50, 100):
        values = noise_samples(4096, level, 0x00C0FFEE)
        sigma = level * 127 // 100
        expected = sigma
        measured = float(np.sqrt(np.mean(np.square(values))))
        assert measured == pytest.approx(expected, rel=0.15)
    assert float(np.sqrt(np.mean(np.square(noise_samples(4096, 100, 1))))) > \
           float(np.sqrt(np.mean(np.square(noise_samples(4096, 10, 1)))))


def test_add_noise_clamps_to_dac_range():
    out = add_noise([127] * 64, 100, 0x00C0FFEE)
    assert all(DAC_MIN <= v <= DAC_MAX for v in out)
    assert any(v == DAC_MAX for v in out)


from siggen.channel import (
    IMPULSE_MAX_PERIOD,
    IMPULSE_MAX_WIDTH,
    TONE_ROM,
    impulse_samples,
    tone_samples,
)


def test_tone_rom_matches_rtl_sine_rom_file():
    """TONE_ROM 必须逐项等于 src/rtl/sine_rom.v 里**实际的表**。

    旧版重算了定义 TONE_ROM 的同一表达式(与 RTL 无关), 因此 host 与 RTL 分叉时
    它照样通过 —— 本用例改为直接解析 RTL 文件。ROM 由 tools/gen_sine.py 生成,
    故顺带钉住"文件内容与该生成公式一致"。
    """
    import math
    import re
    from pathlib import Path

    rom_path = Path(__file__).parents[2] / "src" / "rtl" / "sine_rom.v"
    text = rom_path.read_text(encoding="ascii")
    entries = {
        int(m.group(1)): int(m.group(2), 16)
        for m in re.finditer(r"mem\[\s*(\d+)\]\s*=\s*8'h([0-9A-Fa-f]{2});", text)
    }
    assert len(entries) == 256, "expected 256 ROM entries in %s" % rom_path
    rtl_table = [entries[i] for i in range(256)]
    assert TONE_ROM == rtl_table
    # 生成器公式: 文件内容与 tools/gen_sine.py 的输出一致
    generated = [round(127 * math.sin(2.0 * math.pi * i / 256)) & 0xFF for i in range(256)]
    assert rtl_table == generated


def test_tone_zero_amplitude_is_silent():
    assert tone_samples(64, 100000, 0, 50_000_000) == [0] * 64


def test_tone_amplitude_scales_against_full_scale():
    """ampl 是"百分比满幅"(与噪声等级同约定), 不是 DAC LSB 数。

    峰值 = round(ampl × 127 / 100)。ampl=40 -> round(50.8) = 51。
    写成 LSB 数会得到 40, 那是把约定记错了 —— 本用例专门钉住这一点。
    """
    out = tone_samples(4096, 100000, 40, 50_000_000)
    assert max(out) == 51 and min(out) == -51


def test_tone_frequency_sets_accumulator_step():
    """fs = 2^24 Hz 使 fword == freq_hz, 便于手算。

    累加器索引必须与实现一致: 第 n 拍的 ROM 数据来自 acc[n-1]
    (RTL 的同步读使 dout 落后 acc 一拍), 且 n==0 时 acc 取 0。
    这是模型与 RTL 最容易错开一位的地方, 所以单独钉一个用例。
    """
    fword = 1_000_000
    mask = (1 << 24) - 1
    expected = []
    for n in range(8):
        acc = 0 if n == 0 else (fword * (n - 1)) & mask
        value = TONE_ROM[(acc >> 16) & 0xFF]
        expected.append(value - 256 if value >= 128 else value)
    assert tone_samples(8, fword, 100, 1 << 24) == expected
    # fword=1e6 时 addr 序列为 0,0,15,30...: 前两拍都落在 ROM[0],
    # 首个非零样点在 n=2。若误用 acc[n] 则 n=1 就会非零, 此断言会失败。
    assert expected[0] == 0 and expected[1] == 0
    assert expected[2] == TONE_ROM[15]


def test_tone_fword_uses_firmware_integer_rounding():
    """频率字必须与固件 sdk/src/main.c 的整数式 (f<<24 + fs/2)//fs 一致。

    不能用 Python round(): round() 是 banker's rounding(半值取偶), 固件是半值向上,
    在恰好 .5 边界两者差 1 LSB。50MHz 下 fs 约分后分母(390625)为奇数、不存在 .5
    边界, 两式处处相同 —— 故本用例用 fs=2^25 构造边界(f=100001 时 round() 得 50000,
    固件式得 50001), 并需要够长的序列让这一位差异真正改变 ROM 地址。
    """
    from siggen.channel import TONE_ROM, _round_div_signed
    freq, sample_rate, ampl, count = 100001, 1 << 25, 100, 70000
    mask = (1 << 24) - 1

    def reference(rounding):
        if rounding == "firmware":
            fword = (freq * (1 << 24) + sample_rate // 2) // sample_rate
        else:
            fword = int(round(freq * (1 << 24) / float(sample_rate)))
        out = []
        for n in range(count):
            acc = 0 if n == 0 else (fword * (n - 1)) & mask
            value = TONE_ROM[(acc >> 16) & 0xFF]
            signed = value - 256 if value >= 128 else value
            out.append(_round_div_signed(ampl * signed, 100))
        return out

    firmware, bank_rounded = reference("firmware"), reference("round")
    assert firmware != bank_rounded, "case must straddle a .5 boundary to be discriminating"
    assert tone_samples(count, freq, ampl, sample_rate) == firmware


def test_impulse_is_zero_between_pulses():
    out = impulse_samples(40, 10, 2, 50)
    assert out[0] != 0 and out[1] != 0
    assert out[2:10] == [0] * 8
    assert out[10] != 0 and out[11] != 0
    assert out[12:20] == [0] * 8


def test_impulse_amplitude_is_signed_and_scaled():
    positive = impulse_samples(4, 100, 4, 50)
    negative = impulse_samples(4, 100, 4, -50)
    assert all(v > 0 for v in positive[:4])
    assert all(v < 0 for v in negative[:4])
    assert positive[0] == -negative[0]
    assert 60 <= positive[0] <= 64   # round(127*50/100) = 64


def test_impulse_rejects_out_of_range():
    with pytest.raises(ValueError):
        impulse_samples(4, 0, 1, 10)
    with pytest.raises(ValueError):
        impulse_samples(4, IMPULSE_MAX_PERIOD + 1, 1, 10)
    with pytest.raises(ValueError):
        impulse_samples(4, 10, IMPULSE_MAX_WIDTH + 1, 10)
    with pytest.raises(ValueError):
        impulse_samples(4, 10, 1, 101)


from siggen.channel import (          # noqa: E402  (B 组: 复数 I/Q 损伤模型)
    IQ_GAIN_UNITY,
    SINCOS_AMP,
    SINCOS_N,
    apply_iq_impair,
    iq_impair_matrix,
    sincos_table,
)


def test_sincos_table_matches_rtl_file():
    """SINCOS 表必须逐项等于 src/rtl/sincos_rom.v 里**实际的表**。

    重算定义式的写法是自证的(与 RTL 分叉了也照样通过), 所以这里解析真实文件 ——
    这也是它能抓住"改了生成器但忘了重新生成 .v"的唯一方式。
    """
    import re
    from pathlib import Path

    rom_path = Path(__file__).parents[2] / "src" / "rtl" / "sincos_rom.v"
    text = rom_path.read_text(encoding="utf-8")

    def parse(name):
        found = {}
        for m in re.finditer(
                r"%s\[\s*(\d+)\]\s*=\s*16'sh([0-9A-Fa-f]{4});" % name, text):
            raw = int(m.group(2), 16)
            found[int(m.group(1))] = raw - 65536 if raw >= 32768 else raw
        return found

    cos_rom, sin_rom = parse("cos_mem"), parse("sin_mem")
    assert len(cos_rom) == SINCOS_N and len(sin_rom) == SINCOS_N, \
        "expected %d entries per table in %s" % (SINCOS_N, rom_path)
    cos_host, sin_host = sincos_table()
    assert cos_host == [cos_rom[i] for i in range(SINCOS_N)]
    assert sin_host == [sin_rom[i] for i in range(SINCOS_N)]
    # 象限锚点: 表生成错了必然违反
    assert cos_host[0] == SINCOS_AMP and sin_host[0] == 0
    assert cos_host[256] == 0 and sin_host[256] == SINCOS_AMP
    assert cos_host[512] == -SINCOS_AMP


def test_iq_impair_matrix_identity():
    # 无相位偏移 / 单位增益 / 无正交误差 -> 单位阵(Q2.14 的 1.0 = 16384)
    a, b, c, d = iq_impair_matrix(0, IQ_GAIN_UNITY, 0)
    assert (a, b, c, d) == (IQ_GAIN_UNITY, 0, 0, IQ_GAIN_UNITY)


def test_iq_impair_matrix_pure_phase():
    # phase_off = 9000 (0.01 度单位) = 90 度 -> [[0,-1],[1,0]]
    a, b, c, d = iq_impair_matrix(9000, IQ_GAIN_UNITY, 0)
    assert abs(a) <= 2 and abs(d) <= 2          # 对角项 ~0(仅剩量化)
    assert abs(b + IQ_GAIN_UNITY) <= 2          # B ~ -16384
    assert abs(c - IQ_GAIN_UNITY) <= 2          # C ~ +16384
    assert a == d and b == -c                   # 正交结构


def test_iq_impair_matrix_gain_imbalance_shifts_determinant():
    # 增益失配只缩放 I 轴 -> 行列式 = gain/16384, 偏离单位阵
    a, b, c, d = iq_impair_matrix(0, IQ_GAIN_UNITY // 2, 0)
    det = a * d - b * c
    want = IQ_GAIN_UNITY * IQ_GAIN_UNITY // 2
    assert abs(det - want) <= 2 * IQ_GAIN_UNITY   # 量化容差


def test_apply_iq_impair_identity_is_bit_exact():
    # 单位阵 + 无频偏 -> 数值逐位不变(这是旁路之外的"不变"保证)
    # 仅取非饱和样本: -128 超出对称饱和域
    i_in = [0, 10, -10, 100, -127, 127, 1]
    q_in = [0, -7, 7, -100, 127, -127, 0]
    out_i, out_q = apply_iq_impair(
        i_in, q_in, IQ_GAIN_UNITY, 0, 0, IQ_GAIN_UNITY, fword=0)
    assert out_i == i_in and out_q == q_in


def test_apply_iq_impair_rotation_matches_hand_calc():
    """fword = 2^32/4 -> 每拍 +90 度, 输入 (100, 0) 应走出 0/90/180/270 的阶梯。

    索引约定: sincos_rom 是**同步**读, 样本与 ROM 输出在同一个时钟沿配对,
    故第 n 个样本用 n·fword 的地址(n=0 -> addr 0, n=1 -> 256, ...),
    **不是** tone_samples 那种 n−1。见 apply_iq_impair 的说明。
    """
    fword = (1 << 32) // 4
    out_i, out_q = apply_iq_impair(
        [100] * 6, [0] * 6, IQ_GAIN_UNITY, 0, 0, IQ_GAIN_UNITY, fword=fword)
    assert out_i[0] == 100 and out_q[0] == 0
    assert out_i[1] == 0 and out_q[1] == 100
    assert out_i[2] == -100 and out_q[2] == 0
    assert out_i[3] == 0 and out_q[3] == -100
    assert out_i[4] == 100 and out_q[4] == 0


def test_apply_iq_impair_saturates():
    big = 32767
    out_i, out_q = apply_iq_impair([127], [127], big, big, big, big, fword=0)
    assert -127 <= out_i[0] <= 127
    assert -127 <= out_q[0] <= 127


def test_apply_iq_impair_frequency_offset_accumulates():
    """频偏下相位每拍累加, 地址取 acc[31:22] -> 1024 点分辨率 2*pi/1024。

    fword = 2^22 恰好每拍前进一个 ROM 地址, 于是输出相位逐拍前进 2*pi/1024。
    判据只做单调性, 不做绝对比对(避免与手算精度纠缠)。
    """
    step = 1 << 22
    out_i, out_q = apply_iq_impair(
        [100] * 8, [0] * 8, IQ_GAIN_UNITY, 0, 0, IQ_GAIN_UNITY, fword=step)
    sines = [out_q[k] for k in range(1, 6)]
    assert sines == sorted(sines), "phase must advance monotonically"
    assert sines[-1] > sines[0], "phase must actually advance"
    assert out_i[0] == 100 and out_q[0] == 0


def test_apply_iq_impair_rejects_out_of_range_coefficients():
    with pytest.raises(ValueError):
        apply_iq_impair([1], [1], 32768, 0, 0, IQ_GAIN_UNITY)
    with pytest.raises(ValueError):
        apply_iq_impair([1], [1], IQ_GAIN_UNITY, 0, 0, -32769)
