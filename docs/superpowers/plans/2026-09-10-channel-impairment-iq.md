# 信道损伤扩展 B 组（复数 I/Q 损伤）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数字调制支路的 I/Q 对上插入一个真正的复数损伤级（静态 2×2 矩阵 + 频率偏移旋转），覆盖相位偏移、I/Q 增益失配、I/Q 相位失配与频偏四项，并把它们做成运行时可按数值配置、可由示波器闭环量测。

**Architecture:** 新增 `src/rtl/iq_impair.v`（两级级联 2×2 实矩阵，8 个 DSP48，逐级注册 + 平衡加法树）与生成物 `src/rtl/sincos_rom.v`（1024 点 × 16 位 Q2.14 sin/cos，block RAM）。`awg_top.v` 的最终选择改为「先把数字调制的 I/Q 对 mux 出来 → 过 `iq_impair` → 再进最终选择」，标准 AWG 路径零影响、`enable=0` 时组合旁路逐位透传。静态矩阵由「上位机/固件」合成（`R(phase_off) × diag(g,1) × P(phase_err)`），硬件只做矩阵乘。参数落抽头表 BRAM 的字 8-10，使能走 CH1 控制字 bit11（固件 `& 0xffffu` 掩码内），**不改 BD**。

**Tech Stack:** Verilog-2001（纯 RTL，无新 IP）、Vivado 2025.2 XSIM、Zynq-7020 裸机 C（`sdk/src/main.c`）、Python 3（上位机 + 测试）、DSOX1102A 示波器闭环。

**Spec:** `docs/superpowers/specs/2026-09-10-channel-impairment-extension-design.md`

## Global Constraints

- **仿真工具**：`SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"`；TB 流程为
  `"$SIM/xvlog.bat" --sv <files>` → `"$SIM/xelab.bat" <tb> -s <sim>` →
  `"$SIM/xsim.bat" <sim> -runall`；输出必须含 `PASS <模块名>`。
- **上位机测试**：`host/.venv/Scripts/python -m pytest host/tests/`。
- **时序（最高优先级约束）**：当前 **WNS 仅 0.071 ns**。所有新算术必须
  **逐级注册、用显式平衡加法树**，**严禁 DSP48 `PCOUT→PCIN` 级联** ——
  `src/rtl/fade_fir.v:54` 的注释（`// 平衡树, 不串 DSP48`）与 `rrc_filter.v`
  的单周期 22.67 ns 长路径是两次已付代价的教训，本轮 8 个新 DSP48 必须照抄该模式。
  具体做法：乘法的两个操作数都是**寄存器输出**（跨时钟沿），加法结果**再打一拍**，
  这样综合器无法把两级的 DSP48 串成 PCIN 链。
- **`enable=0` 必须逐位透传**：`iq_impair` 的旁路是**组合 mux**（比 `chan_impair`
  的寄存旁路更强），因此 `awg_top` 中 `dac_sel0/1` 在旁路时与旧表达式逐位等价，
  既有 56 项示波器用例与 `awg_top_tb` 的 `dac_sel0 === rrc_i8` 断言都不回归。
- **饱和边界是 ±127**（镜像 `rrc_to_dac` / `fade_fir`），不是 8-bit 的 −128。
- **I 与 Q 必须完全对称处理**（相同级数、相同寄存器），否则会引入人为 I/Q 相位失配。
- **不改 BD**：`scripts/create_bd.tcl`、`scripts/ps7_props.tcl` 不动 →
  正常 `python scripts/build_bitstream.py`（**不要** `--force`），不需要重新生成 BSP。
  （`tools/rebuild_bitstream.tcl:45` 与 `scripts/create_bd.tcl:21` 都是
  `glob src/rtl/*.v`，新增 RTL 自动进网表；`awg_top` 端口列表不变，BD 模块引用不变。）
- **固件控制字可达性**：`write_chan_ctrl()`（`sdk/src/main.c:646-648`）用
  `& 0xffffu` 把控制字截断到低 16 位，因此只有 **bits 9-15** 从 PS 可达。
  `iq_impair_en` 占用 **CH1 控制字 bit11**（规格 §3）。不改该掩码。
- **时钟域**：`iq_impair` 与 `chan_cfg`、`chan_impair` 同在 `sys_clk`（50 MHz）。
- **提交信息**：Conventional Commits 风格英文主题行，正文说明"为什么"，
  结尾 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。
- **与 A 组（`tone_gen`/`impulse_gen`）互不依赖**：A 组用 BRAM 字 12-15，
  B 组用字 8-10；本计划在 A 组未实施时也必须能独立跑通。

### 两条必须写进代码注释的规格判决

1. **Q2.14 而不是 Q1.15**（规格 §1）：I/Q 增益失配要求 `α = 1+ε > 1`，Q1.15 上限
   0.99997 表示不了。Q2.14 范围 ±1.99994、分辨率 6.1e-5（对应 −84 dB 镜像抑制本底，
   远低于 8 位信号的 −50 dB 量化地板，故不是瓶颈），且恰好两个系数装一个 32 位字。
2. **三项静态损伤由主机合成为一个矩阵**（规格「已确认决策」末条）：硬件只做矩阵乘，
   **不需要知道用户设的是哪一项**。协议层面 `SET IQIMP PHASE/GAIN/PHASEERR`
   是三个高层参数，但 PS 固件本身就是"主机侧"的一部分（它必须把结果写进 BRAM 字 8/9），
   因此矩阵合成在 `sdk/src/main.c` 里用**相同的整数算法**实现，
   `host/siggen/channel.py::iq_impair_matrix()` 是该算法的纯函数镜像 + 验证模型
   —— 与既有 `multipath_fir()` 镜像 `fade_fir.v`、`noise_samples()` 镜像 `noise_gen.v`
   是同一套做法。两边必须**逐位一致**，靠本计划的单测 + TB golden 钉死。

### 数值模型（本计划反复引用，先写在前面）

```
静态级:  [I1]   [A B][I0]      A,B,C,D ∈ Q2.14 有符号 16 位
         [Q1] = [C D][Q0]      即 x1 = sat127( round((A·x0 + B·y0) / 2^14) )
旋转级:  [Io]   [cosθ -sinθ][I1]     θ[n] = n·fword mod 2^32, 地址 = θ[n][31:22]
         [Qo] = [sinθ  cosθ][Q1]     即 io = sat127( round((cos·x1 - sin·y1) / 2^14) )
```

- `sincos_rom` 的表值也是 **Q2.14**（幅度 16384，不是 32767），这样两级用同一个
  `>>14` 与同一个 `shift_sat14()` 函数，两级之间不需要任何尺度换算。
- 矩阵合成（固件与 Python 两侧同式，Q2.14 整数）：
  `c=cos, s=sin`；`P(ε) = [[cos(ε/2), -sin(ε/2)], [-sin(ε/2), cos(ε/2)]]`（**对称非正交**，
  使 I/Q 轴夹角变成 90°−ε，并让 `E[I·Q] = -σ²·sin ε` —— 这是规格 §7 判据要量的量）；
  `D(g) = diag(g, 1)`；`R(φ) = [[cos φ, -sin φ],[sin φ, cos φ]]`；`M = R · D · P`。
  中间的每次乘法都用 `mul_q14(x, y) = round(x·y / 2^14)` 收拢回 Q2.14，
  最后把 A/B/C/D 钳到 `[-32768, 32767]`。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `host/siggen/channel.py` | **修改**。新增 `SINCOS_SIN/COS`（Q2.14 表）、`sincos_q14()`、`mul_q14()`、`iq_impair_matrix()`、`apply_iq_impair()`、`iq_coef_words()`、`hz_to_fword32()` —— RTL 与固件的整数镜像 |
| `host/tests/test_channel.py` | **修改**。新增复数级模型单测（先写失败测试） |
| `tools/gen_sincos.py` | **新建**。生成 `src/rtl/sincos_rom.v` 与 `sdk/src/sincos_table.h`，自带自检 |
| `src/rtl/sincos_rom.v` | **新建（生成物）**。1024 × (16-bit sin, 16-bit cos) Q2.14，`ram_style="block"` |
| `sdk/src/sincos_table.h` | **新建（生成物）**。同上两表，供固件做矩阵合成 |
| `src/tb/sincos_rom_tb.v` | **新建**。四点精确值 + 全表 `sin²+cos²` 界 |
| `src/rtl/iq_impair.v` | **新建**。复数级：静态 2×2 → 旋转 2×2，5 级寄存 + 组合末级，组合旁路 |
| `src/tb/iq_impair_tb.v` | **新建**。旁路逐位、单位矩阵、定向矩阵、90°/拍旋转、host golden |
| `src/rtl/chan_cfg.v` | **修改**。排空字组由 4 字扩到 7 字（`base+0..3` 再 `8,9,10`），新增 `coef_ab`/`coef_cd`/`fword` 输出 |
| `src/tb/chan_cfg_tb.v` | **修改**。新增字 8-10 锁存用例；排空周期数放宽 |
| `src/rtl/reg_bridge.v` | **修改**。新增 `ch1_iq_en = r17[11]` |
| `src/rtl/awg_top.v` | **修改**。最终选择改造：`dm_i/dm_q/dm_active` + 单实例 `iq_impair` |
| `src/tb/awg_top_tb.v` | **修改**。新增 `iq_impair` 接线 / 旁路 / 单位矩阵延时用例 |
| `sdk/src/main.c` | **修改**。`SET IQIMP ...` / `GET IQIMP`、矩阵合成、字 8-10 写入、bit11 使能 |
| `host/siggen/protocol.py` | **修改**。`IQIMP` 家族命令构造器与状态解析 |
| `host/tests/test_protocol.py` | **修改**。构造器与解析单测 |
| `scripts/test_bridge.py` | **修改**。`IQIMP` 命令断言 |
| `scripts/scope_verify.py` | **修改**。三个 I/Q 示波器用例 + 星座统计助手 |
| `scripts/release_manifest.py` | **修改**。`tools/gen_sincos.py` 计入归一化输入哈希 |
| `README.md` / `CLAUDE.md` | **修改**。协议说明、计数与经验条目 |

### Task 1: 上位机复数级模型（矩阵合成 + RTL 镜像）

先把整数模型写出来，后面 RTL 的 TB 用它做 golden，示波器用例用它做模型比对。
**这是 TDD 的第一步。**

**Files:**
- Modify: `host/siggen/channel.py`
- Test: `host/tests/test_channel.py`

**Interfaces:**
- Consumes: `host/siggen/channel.py` 既有 `round_shift_signed()`、`_round_div_signed()`、`_clamp()`、`DAC_MIN`/`DAC_MAX`
- Produces:
  - `IQ_COEF_MIN, IQ_COEF_MAX = -32768, 32767`
  - `IQ_COEF_UNITY = 16384`（Q2.14 的 1.0）
  - `SINCOS_N = 1024`、`SINCOS_AMPL = 16384`、`SINCOS_SIN: list[int]`、`SINCOS_COS: list[int]`
  - `IQ_SAMPLE_RATE_HZ = 50_000_000`
  - `sincos_q14(deg_x100, want_cos)` → `int`（表值 + 线性插值，结果自动落在 ±16384 内）
  - `mul_q14(a, b)` → `int`
  - `iq_impair_matrix(phase_off_deg_x100=0, gain_q14=IQ_COEF_UNITY, phase_err_deg_x100=0)` → `(a, b, c, d)`
  - `apply_iq_impair(i_samples, q_samples, a, b, c, d, fword=0)` → `(list[int], list[int])`
  - `iq_coef_words(a, b, c, d)` → `(word_ab, word_cd)`
  - `hz_to_fword32(hz)` → `int`（32 位无符号相位增量）

- [ ] **Step 1: 写失败测试**

追加到 `host/tests/test_channel.py`（文件顶部既有 `import numpy as np` / `import pytest`，再补 `import re` 与 `from pathlib import Path`）：

```python
import re
from pathlib import Path

from siggen.channel import (
    IQ_COEF_MAX,
    IQ_COEF_MIN,
    IQ_COEF_UNITY,
    SINCOS_AMPL,
    SINCOS_COS,
    SINCOS_N,
    SINCOS_SIN,
    apply_iq_impair,
    hz_to_fword32,
    iq_coef_words,
    iq_impair_matrix,
    mul_q14,
    sincos_q14,
)


def test_sincos_tables_are_q14_and_quadrature():
    import math
    assert SINCOS_N == 1024 and SINCOS_AMPL == 16384
    assert SINCOS_SIN[0] == 0 and SINCOS_COS[0] == SINCOS_AMPL
    assert SINCOS_SIN[SINCOS_N // 4] == SINCOS_AMPL and SINCOS_COS[SINCOS_N // 4] == 0
    assert SINCOS_SIN[SINCOS_N // 2] == 0 and SINCOS_COS[SINCOS_N // 2] == -SINCOS_AMPL
    assert SINCOS_SIN[3 * SINCOS_N // 4] == -SINCOS_AMPL
    assert SINCOS_SIN == [round(16384 * math.sin(2.0 * math.pi * i / 1024)) for i in range(1024)]
    assert SINCOS_COS == [round(16384 * math.cos(2.0 * math.pi * i / 1024)) for i in range(1024)]


def test_sincos_q14_interpolates_and_wraps_angles():
    assert sincos_q14(0, False) == 0
    assert sincos_q14(0, True) == 16384
    assert sincos_q14(9000, False) == 16384          # 90 度
    assert sincos_q14(9000, True) == 0
    assert sincos_q14(4500, True) == sincos_q14(4500, False)   # 45 度: cos == sin
    assert sincos_q14(36000, False) == sincos_q14(0, False)    # 整周回绕
    assert sincos_q14(-9000, False) == -16384                  # 负角
    assert sincos_q14(100, True) < 16384 and sincos_q14(100, True) > sincos_q14(200, True)


def test_mul_q14_rounds_half_away_from_zero():
    assert mul_q14(16384, 16384) == 16384        # 1.0 * 1.0
    assert mul_q14(8192, 16384) == 8192          # 0.5 * 1.0
    assert mul_q14(-8192, 16384) == -8192
    assert mul_q14(16384, -16384) == -16384
    assert mul_q14(-8192, -8192) == 4096


def test_iq_impair_matrix_identity_is_unity():
    assert iq_impair_matrix() == (IQ_COEF_UNITY, 0, 0, IQ_COEF_UNITY)
    assert iq_impair_matrix(0, IQ_COEF_UNITY, 0) == (16384, 0, 0, 16384)


def test_iq_impair_matrix_phase_offset_is_rotation():
    a, b, c, d = iq_impair_matrix(phase_off_deg_x100=9000)
    assert (a, b, c, d) == (0, -16384, 16384, 0)     # +90 度旋转


def test_iq_impair_matrix_gain_scales_i_axis_only():
    a, b, c, d = iq_impair_matrix(gain_q14=20480)    # 1.25
    assert (a, b, c, d) == (20480, 0, 0, 16384)


def test_iq_impair_matrix_phase_error_is_symmetric_non_orthogonal():
    import math
    a, b, c, d = iq_impair_matrix(phase_err_deg_x100=2000)     # 20 度
    # P(e) = [[cos(e/2), -sin(e/2)], [-sin(e/2), cos(e/2)]]
    assert abs(a - round(16384 * math.cos(math.radians(10.0)))) <= 2
    assert abs(b + round(16384 * math.sin(math.radians(10.0)))) <= 2
    assert c == b
    assert abs(d - a) <= 2
    assert a != d or b != 0      # 非正交, 不是旋转


def test_iq_impair_matrix_clamps_to_q14():
    a, b, c, d = iq_impair_matrix(0, IQ_COEF_MAX, 0)
    assert all(IQ_COEF_MIN <= v <= IQ_COEF_MAX for v in (a, b, c, d))


def test_apply_iq_impair_identity_is_passthrough():
    i = [0, 10, -10, 100, -127, 127, 1, 0]
    q = [0, -5, 5, -100, 127, -127, -1, 0]
    gi, gq = apply_iq_impair(i, q, IQ_COEF_UNITY, 0, 0, IQ_COEF_UNITY)
    assert gi == i and gq == q


def test_apply_iq_impair_direct_matrix_matches_hand_computed():
    # A=D=16384, B=8192, C=-8192, fword=0 -> i1 = i + q/2, q1 = q - i/2
    gi, gq = apply_iq_impair([100], [50], 16384, 8192, -8192, 16384)
    assert (gi[0], gq[0]) == (125, 0)
    gi, gq = apply_iq_impair([127], [-128], 16384, 8192, -8192, 16384)
    assert (gi[0], gq[0]) == (63, -127)          # q1 = -192 -> 对称饱和 -127


def test_apply_iq_impair_rotation_quarter_turn_per_sample():
    # fword = 2^30 -> 地址每拍 +256 -> 每拍转 90 度; 单位矩阵输入恒定
    gi, gq = apply_iq_impair([100] * 5, [50] * 5, 16384, 0, 0, 16384, fword=1 << 30)
    assert list(zip(gi, gq)) == [(100, 50), (-50, 100), (-100, -50), (50, -100), (100, 50)]


def test_iq_coef_words_packs_low_then_high():
    assert iq_coef_words(0x4000, 0, 0, 0x4000) == (0x00004000, 0x40000000)
    assert iq_coef_words(-8192, 8192, 0, -1) == (0x2000E000, 0xFFFF0000)


def test_hz_to_fword32_matches_full_scale():
    assert hz_to_fword32(50_000_000) == 0x100000000 % (1 << 32)
    assert hz_to_fword32(25_000_000) == 0x80000000
    assert hz_to_fword32(0) == 0
    assert hz_to_fword32(-25_000_000) == 0x80000000
    assert hz_to_fword32(1_000_000) == round(1_000_000 * 2 ** 32 / 50_000_000)


def test_sincos_rom_verilog_matches_python_table():
    """Task 2 生成 src/rtl/sincos_rom.v; 生成物与 channel.py 的表必须逐值相同。"""
    rom = Path(__file__).resolve().parents[2] / "src" / "rtl" / "sincos_rom.v"
    if not rom.exists():
        pytest.skip("src/rtl/sincos_rom.v 尚未生成(Task 2)")
    text = rom.read_text(encoding="utf-8")
    words = [int(m, 16) for m in
             re.findall(r"mem\[\s*\d+\]\s*=\s*32'h([0-9A-Fa-f]{8})", text)]
    assert len(words) == SINCOS_N
    def sext16(v):
        return v - 65536 if v & 0x8000 else v
    assert [sext16(w >> 16) for w in words] == SINCOS_SIN
    assert [sext16(w & 0xFFFF) for w in words] == SINCOS_COS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_channel.py -v`
Expected: FAIL — `ImportError: cannot import name 'IQ_COEF_MAX' from 'siggen.channel'`

- [ ] **Step 3: 实现最简模型**

追加到 `host/siggen/channel.py` 末尾：

```python
import math

# ---- 复数 I/Q 损伤级(iq_impair.v)的整数镜像 ----
IQ_COEF_MIN, IQ_COEF_MAX = -32768, 32767     # Q2.14 有符号 16 位
IQ_COEF_UNITY = 16384                        # Q2.14 的 1.0(不是 32768/Q1.15)
IQ_SAMPLE_RATE_HZ = 50_000_000
SINCOS_N = 1024                              # sincos_rom 深度, 对应 θ[31:22]
SINCOS_AMPL = 16384                          # Q2.14 幅度(旋转级与静态级共用 >>14)
# 与 src/rtl/sincos_rom.v 同值(由 tools/gen_sincos.py 生成)。
SINCOS_SIN = [round(SINCOS_AMPL * math.sin(2.0 * math.pi * i / SINCOS_N))
              for i in range(SINCOS_N)]
SINCOS_COS = [round(SINCOS_AMPL * math.cos(2.0 * math.pi * i / SINCOS_N))
              for i in range(SINCOS_N)]


def sincos_q14(deg_x100, want_cos):
    """0.01° 单位角度 -> Q2.14 sin/cos, 1024 点表 + 线性插值。

    这是 sdk/src/main.c::iqimp_sincos_q14 的逐位镜像(固件用 sdk/src/sincos_table.h
    的同值表)。整周 36000 个单位, 表索引按 num = pos * 1024 映射。
    """
    pos = int(deg_x100) % 36000
    if pos < 0:
        pos += 36000
    num = pos * SINCOS_N
    i0 = num // 36000
    frac = (num % 36000) * SINCOS_N // 36000
    table = SINCOS_COS if want_cos else SINCOS_SIN
    a = table[i0 % SINCOS_N]
    b = table[(i0 + 1) % SINCOS_N]
    return _round_div_signed(a * (SINCOS_N - frac) + b * frac, SINCOS_N)


def mul_q14(a, b):
    """Q2.14 × Q2.14 -> Q2.14, 四舍五入(远离零), 镜像 iqimp_mul_q14()。"""
    return _round_div_signed(int(a) * int(b), IQ_COEF_UNITY)


def iq_impair_matrix(phase_off_deg_x100=0, gain_q14=IQ_COEF_UNITY,
                     phase_err_deg_x100=0):
    """R(phase_off) × diag(g, 1) × P(phase_err) -> (A, B, C, D), Q2.14 有符号。

    硬件只做矩阵乘, 不知道这三项; 本函数与 sdk/src/main.c::iqimp_compose()
    是同一算法的两个实现, 必须逐位一致(靠 host golden TB 与上板比对钉死)。
    P 取**对称非正交**形式 [[cos(e/2), -sin(e/2)], [-sin(e/2), cos(e/2)]],
    使 I/Q 轴夹角为 90°-e, 且输入各向同性时 E[I·Q] = -σ²·sin(e)
    —— 这是规格 §7 里"星座二阶统计"要判的量。
    """
    half = int(phase_err_deg_x100) // 2 if int(phase_err_deg_x100) >= 0 \
        else -((-int(phase_err_deg_x100)) // 2)
    c1 = sincos_q14(phase_off_deg_x100, True)
    s1 = sincos_q14(phase_off_deg_x100, False)
    c2 = sincos_q14(half, True)
    s2 = sincos_q14(half, False)
    g = int(gain_q14)
    # D × P
    dp00 = mul_q14(g, c2)
    dp01 = -mul_q14(g, s2)
    dp10 = -mul_q14(IQ_COEF_UNITY, s2)
    dp11 = mul_q14(IQ_COEF_UNITY, c2)
    # R × (D × P)
    a = mul_q14(c1, dp00) - mul_q14(s1, dp10)
    b = mul_q14(c1, dp01) - mul_q14(s1, dp11)
    c = mul_q14(s1, dp00) + mul_q14(c1, dp10)
    d = mul_q14(s1, dp01) + mul_q14(c1, dp11)
    return tuple(_clamp(v, IQ_COEF_MIN, IQ_COEF_MAX) for v in (a, b, c, d))


def apply_iq_impair(i_samples, q_samples, a, b, c, d, fword=0):
    """RTL `iq_impair.v` 的整数镜像。

    6 级寄存但**逐值模型只看配对关系**: 第 n 拍的输入对配第 n 拍的地址
    θ[n] = n·fword mod 2^32(地址在 RTL 中先打一拍给 ROM、ROM 输出再打一拍,
    恰好与静态级的 3 拍对齐 —— 见 iq_impair.v 的注释)。
    饱和边界对称 ±127(镜像 rrc_to_dac), 舍入为远离零的四舍五入。
    """
    i_samples = list(i_samples)
    q_samples = list(q_samples)
    if len(i_samples) != len(q_samples):
        raise ValueError("I/Q 长度必须相同")
    fword = int(fword) & 0xFFFFFFFF
    theta = 0
    out_i, out_q = [], []
    for x0, y0 in zip(i_samples, q_samples):
        x1 = _clamp(round_shift_signed(int(a) * x0 + int(b) * y0, 14),
                    DAC_MIN, DAC_MAX)
        y1 = _clamp(round_shift_signed(int(c) * x0 + int(d) * y0, 14),
                    DAC_MIN, DAC_MAX)
        addr = (theta >> 22) & (SINCOS_N - 1)
        cs = SINCOS_COS[addr]
        sn = SINCOS_SIN[addr]
        out_i.append(_clamp(round_shift_signed(cs * x1 - sn * y1, 14),
                            DAC_MIN, DAC_MAX))
        out_q.append(_clamp(round_shift_signed(sn * x1 + cs * y1, 14),
                            DAC_MIN, DAC_MAX))
        theta = (theta + fword) & 0xFFFFFFFF
    return out_i, out_q


def iq_coef_words(a, b, c, d):
    """(A,B,C,D) -> BRAM 字 8 / 字 9 的 32 位打包(低半字在前)。"""
    word_ab = ((int(b) & 0xFFFF) << 16) | (int(a) & 0xFFFF)
    word_cd = ((int(d) & 0xFFFF) << 16) | (int(c) & 0xFFFF)
    return word_ab, word_cd


def hz_to_fword32(hz):
    """频率偏移 Hz -> 32 位相位增量 round(f/fs × 2^32), 镜像固件 hz_to_fword32()。"""
    return _round_div_signed(int(hz) * (1 << 32), IQ_SAMPLE_RATE_HZ) & 0xFFFFFFFF
```

- [ ] **Step 4: 跑测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_channel.py -v`
Expected: PASS（既有 9 项 + 新增 15 项；`test_sincos_rom_verilog_matches_python_table`
此时会 SKIP，Task 2 生成 ROM 后自动生效）

- [ ] **Step 5: 跑全量上位机测试**

Run: `host/.venv/Scripts/python -m pytest host/tests/`
Expected: 全绿（既有 685 项 + 新增）

- [ ] **Step 6: 提交**

```bash
git add host/siggen/channel.py host/tests/test_channel.py
git commit -m "feat(host): integer model for the complex I/Q impairment stage

Mirrors the RTL the testbenches will be checked against, same as
multipath_fir and noise_samples. The static three impairments compose
into one 2x2 matrix on the reference side, so the hardware never learns
which one the user picked -- that is the whole point of the structure.

Coefficients are Q2.14 rather than Q1.15 because I/Q gain imbalance
needs alpha = 1+epsilon > 1 and Q1.15 tops out at 0.99997. The sine table
is Q2.14 as well so both stages share the same >>14 shift and the same
saturating rounder.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: `tools/gen_sincos.py` + `sincos_rom.v` + `sincos_table.h`

**Files:**
- Create: `tools/gen_sincos.py`
- Create: `src/rtl/sincos_rom.v`（生成物）
- Create: `sdk/src/sincos_table.h`（生成物）
- Test: `src/tb/sincos_rom_tb.v`、`host/tests/test_channel.py`（Task 1 已写好的 ROM 交叉校验用例）

**Interfaces:**
- Consumes: 无
- Produces:
  - 模块 `sincos_rom`，端口 `input clk; input [9:0] addr; output reg signed [15:0] sin_out, cos_out`
    （**同步读，无复位**，否则综合不进 BRAM）
  - `sdk/src/sincos_table.h` 里的 `SINCOS_SIN[1024]` / `SINCOS_COS[1024]`（`const short`，Q2.14）

- [ ] **Step 1: 写生成器**

创建 `tools/gen_sincos.py`（照抄 `tools/gen_sine.py` 的结构：自检 + 头注释 + 固定输出路径）：

```python
#!/usr/bin/env python3
"""Generate the 1024-point Q2.14 sin/cos ROM (src/rtl/sincos_rom.v) and the
matching C table header (sdk/src/sincos_table.h) used by the firmware's
matrix composition.

Self-checks: quarter-wave extrema, zero crossings, sin^2+cos^2 constant.
Both outputs come from one table so the RTL rotation and the firmware's
coefficient synthesis can never drift apart.
"""
import math

N = 1024
AMPL = 16384          # Q2.14 full scale (32768 is not representable in int16)
V_OUT = "src/rtl/sincos_rom.v"
C_OUT = "sdk/src/sincos_table.h"

sin_values = [round(AMPL * math.sin(2.0 * math.pi * i / N)) for i in range(N)]
cos_values = [round(AMPL * math.cos(2.0 * math.pi * i / N)) for i in range(N)]

# ---- Verilog ROM ----
lines = [
    "// Auto-generated by tools/gen_sincos.py - do not edit manually",
    "`timescale 1ns / 1ps",
    "// 1024 x (sin Q2.14 | cos Q2.14)。block RAM(32 Kbit ≈ 1 RAMB36) ——",
    "// LUTRAM 会吃掉约 16K LUT, 不可接受, 故 ram_style 必须为 \"block\"。",
    "// 读口**无复位**: 带复位的读会被综合器踢出 BRAM。",
    "module sincos_rom (",
    "    input  wire        clk,",
    "    input  wire [9:0]  addr,",
    "    output reg  signed [15:0] sin_out,",
    "    output reg  signed [15:0] cos_out",
    ");",
    "    (* ram_style = \"block\" *) reg [31:0] mem [0:1023];",
    "    initial begin",
]
for i in range(0, N, 4):
    row = "        " + " ".join(
        "mem[%3d] = 32'h%04X%04X;" % (j, sin_values[j] & 0xFFFF, cos_values[j] & 0xFFFF)
        for j in range(i, i + 4)
    )
    lines.append(row)
lines += [
    "    end",
    "    always @(posedge clk) begin",
    "        sin_out <= mem[addr][31:16];",
    "        cos_out <= mem[addr][15:0];",
    "    end",
    "endmodule",
]
with open(V_OUT, "w") as f:
    f.write("\n".join(lines) + "\n")

# ---- 固件表头 ----
with open(C_OUT, "w") as f:
    f.write("/* Auto-generated by tools/gen_sincos.py - do not edit manually */\n")
    f.write("#ifndef SINCOS_TABLE_H\n#define SINCOS_TABLE_H\n\n")
    f.write("/* 1024 x Q2.14 有符号正弦/余弦, 与 src/rtl/sincos_rom.v 同值。*/\n")
    for name, vals in (("SINCOS_SIN", sin_values), ("SINCOS_COS", cos_values)):
        f.write("static const short %s[1024] = {\n" % name)
        for i in range(0, N, 8):
            f.write("    " + ", ".join("%6d" % v for v in vals[i:i + 8]) + ",\n")
        f.write("};\n\n")
    f.write("#endif /* SINCOS_TABLE_H */\n")

# ---- self-check ----
assert sin_values[0] == 0 and sin_values[N // 2] == 0, "sine zero crossing wrong"
assert cos_values[0] == AMPL and cos_values[N // 2] == -AMPL, "cosine peak wrong"
assert sin_values[N // 4] == AMPL and cos_values[N // 4] == 0, "quadrature wrong"
assert sin_values[3 * N // 4] == -AMPL, "negative peak wrong"
worst = max(abs(s * s + c * c - AMPL * AMPL)
            for s, c in zip(sin_values, cos_values))
assert worst < 24000, "sin^2+cos^2 drift too large: %d" % worst
print("OK: %s + %s written, %d entries, self-check passed (worst=%d)"
      % (V_OUT, C_OUT, N, worst))
```

- [ ] **Step 2: 生成并自检**

```bash
cd "<LOCAL_PATH>"
host/.venv/Scripts/python tools/gen_sincos.py
```
Expected: `OK: src/rtl/sincos_rom.v + sdk/src/sincos_table.h written, 1024 entries, self-check passed (worst=...)`

- [ ] **Step 3: 生成物与 Python 表交叉校验**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_channel.py -k sincos -v
```
Expected: PASS —— 此前 SKIP 的 `test_sincos_rom_verilog_matches_python_table` 现在跑并且通过
（若失败说明生成器的取整/象限与 `channel.py` 不一致，**改生成器或改表，不要改测试**）

- [ ] **Step 4: 写 ROM 的 TB**

创建 `src/tb/sincos_rom_tb.v`：

```verilog
`timescale 1ns / 1ps
// sincos_rom TB: 1024 x Q2.14 同步读 ROM(block RAM)。
// 校验四点精确值、同步读一拍延时、全表 sin^2+cos^2 的量化误差上界。
module sincos_rom_tb;
    reg clk = 0;
    reg [9:0] addr = 10'd0;
    wire signed [15:0] sin_out, cos_out;

    sincos_rom uut (.clk(clk), .addr(addr), .sin_out(sin_out), .cos_out(cos_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    integer i, worst, diff;

    initial begin
        $dumpfile("sincos_rom_tb.vcd"); $dumpvars(0, sincos_rom_tb);
        repeat (4) @(posedge clk);

        // 同步读: 给地址后等一拍数据才有效
        addr = 10'd0;   @(posedge clk); #1;
        addr = 10'd256; @(posedge clk); #1;
        chk("addr 256: sin = +16384 (90deg)", sin_out === 16'sd16384);
        chk("addr 256: cos = 0",              cos_out === 16'sd0);
        addr = 10'd512; @(posedge clk); #1;
        chk("addr 512: sin = 0",              sin_out === 16'sd0);
        chk("addr 512: cos = -16384 (180deg)", cos_out === -16'sd16384);
        addr = 10'd768; @(posedge clk); #1;
        chk("addr 768: sin = -16384 (270deg)", sin_out === -16'sd16384);
        chk("addr 768: cos = 0",               cos_out === 16'sd0);
        addr = 10'd0;   @(posedge clk); #1;
        chk("addr 0: sin = 0",                 sin_out === 16'sd0);
        chk("addr 0: cos = +16384 (0deg)",     cos_out === 16'sd16384);

        // 全表: 幅度不超 ±16384, 且 sin^2+cos^2 恒定
        worst = 0;
        for (i = 0; i < 1024; i = i + 1) begin
            addr = i[9:0];
            @(posedge clk); #1;
            if (sin_out > 16'sd16384 || sin_out < -16'sd16384 ||
                cos_out > 16'sd16384 || cos_out < -16'sd16384) begin
                $display("FAIL addr %0d out of Q2.14 range s=%0d c=%0d",
                         i, sin_out, cos_out);
                errs = errs + 1;
            end
            diff = sin_out * sin_out + cos_out * cos_out - 268435456;
            if (diff < 0) diff = -diff;
            if (diff > worst) worst = diff;
        end
        // 量化上界: 2*16384*0.5*(|sin|+|cos|) <= 16384*1.4143 ≈ 23170
        chk("sin^2+cos^2 within quantisation bound", worst < 24000);

        if (errs == 0) $display("PASS sincos_rom");
        else           $display("FAIL sincos_rom (%0d errors)", errs);
        $finish;
    end

    initial begin
        #5_000_000;
        $display("FAIL sincos_rom (timeout)");
        $finish;
    end
endmodule
```

- [ ] **Step 5: 跑 TB**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/sincos_rom.v src/tb/sincos_rom_tb.v
"$SIM/xelab.bat" sincos_rom_tb -s sincos_sim
"$SIM/xsim.bat" sincos_sim -runall
```
Expected: 10 个 `PASS ...` 后 `PASS sincos_rom`

- [ ] **Step 6: 提交**

```bash
git add tools/gen_sincos.py src/rtl/sincos_rom.v sdk/src/sincos_table.h src/tb/sincos_rom_tb.v
git commit -m "feat(rtl): generated Q2.14 sin/cos ROM for the I/Q rotation

One 1024-point table feeds both the RTL rotation and the firmware's
matrix synthesis, so the hardware angle and the coefficient the PS
computes can never drift apart. ram_style=block is mandatory: the
1024x32 array would otherwise land in LUTRAM and burn roughly 16K LUTs.

The read port deliberately has no reset -- a synchronous reset on the
read would push the array out of the BRAM and back into logic.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task 3: `iq_impair.v` + TB

**Files:**
- Create: `src/rtl/iq_impair.v`
- Test: `src/tb/iq_impair_tb.v`
- Consumes: Task 2 的 `src/rtl/sincos_rom.v`、Task 1 的 `apply_iq_impair`/`iq_coef_words`

**Interfaces:**
- Produces: 模块 `iq_impair`，端口（顺序即声明顺序）
  `input clk, rst_n, enable; input [7:0] i_in, q_in;`
  `input [31:0] coef_ab, coef_cd, fword; output [7:0] i_out, q_out`
  - `coef_ab = {B[31:16], A[15:0]}`、`coef_cd = {D[31:16], C[15:0]}`（Q2.14 有符号）
  - `enable=1` 时端到端 **5 级寄存**（输入第 n 拍的样点在采样后第 4 拍出现在 `i_out`，
    即 TB 中 `ov[n+4]` 对应输入 `x[n]`）；`enable=0` 时 **组合** 逐位透传。

- [ ] **Step 1: 写 `iq_impair.v`**

创建 `src/rtl/iq_impair.v`：

```verilog
`timescale 1ns / 1ps
// 复数 I/Q 损伤级: 静态 2x2 实矩阵 -> 频率偏移旋转 2x2。单实例, 放在
// awg_top 的数字调制支路(rrc_active ? rrc_i8 : dig_i)与最终输出选择之间。
//
// 数值:
//   静态: x1 = sat127(round((A*x0 + B*y0) / 2^14))   A..D 为 Q2.14 有符号 16 位
//   旋转: theta += fword(32 位, 每拍); 地址 = theta[31:22] -> sincos_rom(1024 点 Q2.14)
//         io = sat127(round((cos*x1 - sin*y1) / 2^14))
//         qo = sat127(round((sin*x1 + cos*y1) / 2^14))
//   饱和对称 ±127(镜像 rrc_to_dac), 不是 8-bit 的 -128。
//   为什么 Q2.14 而不是 Q1.15: I/Q 增益失配要求 alpha = 1+eps > 1, Q1.15 上限
//   0.99997 表示不了; Q2.14 范围 ±1.99994、分辨率 6.1e-5(约 -84 dB 镜像本底),
//   远低于 8 位信号自身的量化地板, 不是瓶颈, 且两个系数恰好装一个 32 位字。
//
// 时序(最高优先级约束, 当前 WNS 仅 0.071 ns):
//   5 级寄存 pa1/pb1/pc1/pd1 -> si2/sq2 -> i1/q1 -> pc4/ps4/rc4/rs4 -> io5/qo5,
//   末级舍入饱和是组合函数(只做常量移位 + 比较, 路径极短)。
//   每个乘法的两个操作数都是**寄存器输出**, 加法结果再打一拍:
//   综合器无法把两级 DSP48 串成 PCOUT->PCIN 链。fade_fir.v:54 与 rrc_filter.v
//   都因串行级联崩过时序, 不要"优化"掉这些寄存器。
//
// ROM 对齐: theta 与地址在第 1 级寄存, ROM 数据第 2 级出现, 第 3 级再补一拍
//   cos_q/sin_q —— 恰好与静态级的 i1/q1(同为第 3 级)对齐, 于是第 n 个输入样点
//   配第 n 个角度 theta[n] = n*fword。主机模型 apply_iq_impair() 因此可以写成
//   无偏移的逐点配对。
//
// I 与 Q 走完全对称的通路(同样的级数、同样的位宽), 否则会引入人为 I/Q 相位失配。
// enable=0 时是**组合**旁路(i_out = i_in), 比 chan_impair 的寄存旁路更强:
//   awg_top 的 dac_sel0/1 在旁路时与改造前逐位等价, 既有断言不回归。
module iq_impair (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [7:0]  i_in,
    input  wire [7:0]  q_in,
    input  wire [31:0] coef_ab,     // B[31:16] | A[15:0], Q2.14 有符号
    input  wire [31:0] coef_cd,     // D[31:16] | C[15:0], Q2.14 有符号
    input  wire [31:0] fword,       // 频率偏移相位增量(每拍)
    output wire [7:0]  i_out,
    output wire [7:0]  q_out
);
    wire signed [15:0] coef_a = coef_ab[15:0];
    wire signed [15:0] coef_b = coef_ab[31:16];
    wire signed [15:0] coef_c = coef_cd[15:0];
    wire signed [15:0] coef_d = coef_cd[31:16];

    // Q2.14 乘积(25-bit 有符号) -> 远离零的四舍五入(加半个 LSB) -> >>14 -> ±127
    function signed [7:0] shift_sat14;
        input signed [24:0] v;
        reg signed [24:0] m;
        reg signed [24:0] r;
        begin
            if (v >= 0) r = (v + 25'sd8192) >>> 14;
            else begin m = -v; r = -((m + 25'sd8192) >>> 14); end
            if (r > 25'sd127)       shift_sat14 = 8'sd127;
            else if (r < -25'sd127) shift_sat14 = -8'sd127;
            else                    shift_sat14 = r[7:0];
        end
    endfunction

    // ---- 第 1 级: 静态级 4 个乘法 + 相位累加器 / ROM 地址 ----
    reg signed [23:0] pa1, pb1, pc1, pd1;
    reg [31:0] theta;
    reg [9:0]  rom_addr;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            pa1 <= 24'sd0; pb1 <= 24'sd0; pc1 <= 24'sd0; pd1 <= 24'sd0;
            theta <= 32'd0; rom_addr <= 10'd0;
        end else begin
            pa1 <= coef_a * $signed(i_in);
            pb1 <= coef_b * $signed(q_in);
            pc1 <= coef_c * $signed(i_in);
            pd1 <= coef_d * $signed(q_in);
            theta    <= theta + fword;
            rom_addr <= theta[31:22];      // 本拍的角度, 不是更新后的值
        end

    wire signed [15:0] sin_r, cos_r;
    sincos_rom u_rom (.clk(clk), .addr(rom_addr),
                      .sin_out(sin_r), .cos_out(cos_r));

    // ---- 第 2 级: 静态级平衡加法树(不串 DSP48); ROM 数据本拍有效 ----
    reg signed [24:0] si2, sq2;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin si2 <= 25'sd0; sq2 <= 25'sd0; end
        else begin si2 <= pa1 + pb1; sq2 <= pc1 + pd1; end

    // ---- 第 3 级: 静态级舍入饱和; ROM 数据再打一拍与 i1/q1 对齐 ----
    reg signed [7:0] i1, q1;
    reg signed [15:0] cos_q, sin_q;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            i1 <= 8'sd0; q1 <= 8'sd0; cos_q <= 16'sd0; sin_q <= 16'sd0;
        end else begin
            i1 <= shift_sat14(si2);
            q1 <= shift_sat14(sq2);
            cos_q <= cos_r;
            sin_q <= sin_r;
        end

    // ---- 第 4 级: 旋转级 4 个乘法 ----
    reg signed [23:0] pc4, ps4, rc4, rs4;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            pc4 <= 24'sd0; ps4 <= 24'sd0; rc4 <= 24'sd0; rs4 <= 24'sd0;
        end else begin
            pc4 <= cos_q * i1;      //  cos*I1
            ps4 <= sin_q * q1;      //  sin*Q1
            rc4 <= sin_q * i1;      //  sin*I1
            rs4 <= cos_q * q1;      //  cos*Q1
        end

    // ---- 第 5 级: 旋转级平衡加减(不串 DSP48) ----
    reg signed [24:0] io5, qo5;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin io5 <= 25'sd0; qo5 <= 25'sd0; end
        else begin io5 <= pc4 - ps4; qo5 <= rc4 + rs4; end

    // ---- 末级: 舍入饱和 + 组合旁路 ----
    wire signed [7:0] rot_i = shift_sat14(io5);
    wire signed [7:0] rot_q = shift_sat14(qo5);
    assign i_out = enable ? rot_i : i_in;
    assign q_out = enable ? rot_q : q_in;
endmodule
```

- [ ] **Step 2: 写 TB（golden 先留占位）**

创建 `src/tb/iq_impair_tb.v`。golden 由 Task 1 的模型算出，**Step 4 的命令会给出确切数值**：

```verilog
`timescale 1ns / 1ps
// iq_impair TB:
//  1) enable=0 组合逐位透传
//  2) 单位矩阵 + fword=0: 输出 == 输入(固定延时)
//  3) fword=2^30: 每拍转 90 度(地址每拍 +256)
//  4) host golden: host/siggen/channel.py::apply_iq_impair 的 32 点逐值比对
// 延时判定一律用 **lag 搜索**(必须唯一匹配), 不靠人工数寄存器。
module iq_impair_tb;
    reg clk = 0, rst_n = 0, enable = 0;
    reg [7:0]  i_in = 8'd0, q_in = 8'd0;
    reg [31:0] coef_ab = 32'h0000_4000;   // A=16384, B=0
    reg [31:0] coef_cd = 32'h4000_0000;   // C=0,     D=16384
    reg [31:0] fword = 32'd0;
    wire [7:0] i_out, q_out;

    iq_impair uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                   .i_in(i_in), .q_in(q_in),
                   .coef_ab(coef_ab), .coef_cd(coef_cd), .fword(fword),
                   .i_out(i_out), .q_out(q_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    integer j, n, k, p, lag, bad, matches, found;

    reg [7:0] xv_i [0:39];
    reg [7:0] xv_q [0:39];
    integer   g_i  [0:31];
    integer   g_q  [0:31];
    reg [7:0] ov_i [0:39];
    reg [7:0] ov_q [0:39];

    initial begin : vec
        xv_i[0]=0;    xv_i[1]=100;   xv_i[2]=-50;  xv_i[3]=20;   xv_i[4]=0;
        xv_i[5]=0;    xv_i[6]=-70;   xv_i[7]=0;    xv_i[8]=0;    xv_i[9]=0;
        xv_i[10]=127; xv_i[11]=-128; xv_i[12]=64;  xv_i[13]=-64; xv_i[14]=33;
        xv_i[15]=-33; xv_i[16]=10;   xv_i[17]=-10; xv_i[18]=5;   xv_i[19]=-5;
        xv_i[20]=1;   xv_i[21]=-1;   xv_i[22]=90;  xv_i[23]=-90; xv_i[24]=45;
        xv_i[25]=-45; xv_i[26]=60;   xv_i[27]=-60; xv_i[28]=70;  xv_i[29]=-70;
        xv_i[30]=80;  xv_i[31]=-80;  xv_i[32]=0;   xv_i[33]=0;   xv_i[34]=0;
        xv_i[35]=0;   xv_i[36]=0;    xv_i[37]=0;   xv_i[38]=0;   xv_i[39]=0;

        xv_q[0]=0;    xv_q[1]=-40;   xv_q[2]=60;   xv_q[3]=-20;  xv_q[4]=0;
        xv_q[5]=0;    xv_q[6]=80;    xv_q[7]=0;    xv_q[8]=0;    xv_q[9]=0;
        xv_q[10]=-128; xv_q[11]=127; xv_q[12]=-64; xv_q[13]=64;  xv_q[14]=-33;
        xv_q[15]=33;  xv_q[16]=-10;  xv_q[17]=10;  xv_q[18]=-5;  xv_q[19]=5;
        xv_q[20]=-1;  xv_q[21]=1;    xv_q[22]=-90; xv_q[23]=90;  xv_q[24]=-45;
        xv_q[25]=45;  xv_q[26]=-60;  xv_q[27]=60;  xv_q[28]=-70; xv_q[29]=70;
        xv_q[30]=-80; xv_q[31]=80;   xv_q[32]=0;   xv_q[33]=0;   xv_q[34]=0;
        xv_q[35]=0;   xv_q[36]=0;    xv_q[37]=0;   xv_q[38]=0;   xv_q[39]=0;

        // host golden: apply_iq_impair(xv_i, xv_q, 15384, -4096, 4096, 16000,
        //                              fword=0x04000000) 的前 32 点
        // 由 Step 4 的命令填入(先把占位值全部替换掉, 否则本用例必失败):
        for (n = 0; n < 32; n = n + 1) begin g_i[n] = 0; g_q[n] = 0; end
    end

    initial begin
        $dumpfile("iq_impair_tb.vcd"); $dumpvars(0, iq_impair_tb);
        #100 rst_n = 1;
        repeat (4) @(posedge clk);

        // ---- 1) enable=0: 组合逐位透传 ----
        enable = 1'b0;
        bad = 0;
        for (k = 0; k < 8; k = k + 1) begin
            @(negedge clk);
            i_in = (k * 29 - 80) & 8'hFF;
            q_in = (60 - k * 17) & 8'hFF;
            #1;
            if (i_out !== i_in || q_out !== q_in) bad = bad + 1;
            @(posedge clk);
        end
        chk("enable=0 combinational bit-exact bypass", bad == 0);

        // ---- 2) 单位矩阵 + fword=0: 输出 == 输入(固定延时) ----
        rst_n = 1'b0; enable = 1'b0; fword = 32'd0;
        coef_ab = 32'h0000_4000; coef_cd = 32'h4000_0000;
        repeat (4) @(posedge clk);
        enable = 1'b1;
        @(negedge clk); rst_n = 1'b1;
        for (j = 0; j < 40; j = j + 1) begin
            i_in = xv_i[j]; q_in = xv_q[j];
            @(posedge clk); #1;
            ov_i[j] = i_out; ov_q[j] = q_out;
        end
        matches = 0; found = 0;
        for (lag = 1; lag <= 12; lag = lag + 1) begin
            bad = 0;
            for (n = 0; n + lag < 40; n = n + 1) begin
                if (ov_i[n + lag] !== xv_i[n]) bad = bad + 1;
                if (ov_q[n + lag] !== xv_q[n]) bad = bad + 1;
            end
            if (bad == 0) begin matches = matches + 1; found = lag; end
        end
        // 索引约定: xv[j] 在第 j 拍驱动, ov[j] 在第 j 拍后采到, 故索引差 = 4
        // (输入第 n 拍的样点在第 n+5 拍有效, 而 ov 的下标比"有效拍"少 1)。
        // 只允许唯一一个 lag 匹配, 避免把"恰好某个延时下也吻合"当成通过。
        chk("identity matrix: unique fixed-lag passthrough", matches == 1);
        chk("identity matrix latency index gap is 4", found == 4);

        // ---- 3) fword = 2^30: 地址每拍 +256 -> 每拍转 90 度 ----
        rst_n = 1'b0; enable = 1'b0;
        coef_ab = 32'h0000_4000; coef_cd = 32'h4000_0000; fword = 32'h4000_0000;
        repeat (4) @(posedge clk);
        enable = 1'b1;
        @(negedge clk); rst_n = 1'b1;
        i_in = 8'd100; q_in = 8'd50;
        repeat (6) @(posedge clk);          // 让 5 级流水灌满并越过起始相位
        for (k = 0; k < 8; k = k + 1) begin
            @(posedge clk); #1;
            ov_i[k] = i_out; ov_q[k] = q_out;
        end
        // 期望循环: (100,50) -> (-50,100) -> (-100,-50) -> (50,-100)
        // 8-bit 补码: -50=206, -100=156
        matches = 0;
        for (p = 0; p < 4; p = p + 1) begin
            bad = 0;
            for (k = 0; k < 8; k = k + 1) begin
                case ((k + p) % 4)
                    0: begin if (ov_i[k] !== 8'd100 || ov_q[k] !== 8'd50)  bad = bad + 1; end
                    1: begin if (ov_i[k] !== 8'd206 || ov_q[k] !== 8'd100) bad = bad + 1; end
                    2: begin if (ov_i[k] !== 8'd156 || ov_q[k] !== 8'd206) bad = bad + 1; end
                    default: begin
                        if (ov_i[k] !== 8'd50 || ov_q[k] !== 8'd156) bad = bad + 1;
                    end
                endcase
            end
            if (bad == 0) matches = matches + 1;
        end
        chk("fword=2^30 rotates 90 deg per sample", matches >= 1);

        // ---- 4) host golden ----
        rst_n = 1'b0; enable = 1'b0;
        coef_ab = 32'hF000_3C18;   // B=-4096, A=15384
        coef_cd = 32'h3E80_1000;   // D=16000, C=4096
        fword   = 32'h0400_0000;
        repeat (4) @(posedge clk);
        enable = 1'b1;
        @(negedge clk); rst_n = 1'b1;
        for (j = 0; j < 40; j = j + 1) begin
            i_in = xv_i[j]; q_in = xv_q[j];
            @(posedge clk); #1;
            ov_i[j] = i_out; ov_q[j] = q_out;
        end
        matches = 0; found = 0;
        for (lag = 1; lag <= 12; lag = lag + 1) begin
            bad = 0;
            for (n = 0; n < 32; n = n + 1) begin
                if ($signed(ov_i[n + lag]) !== g_i[n]) bad = bad + 1;
                if ($signed(ov_q[n + lag]) !== g_q[n]) bad = bad + 1;
            end
            if (bad == 0) begin matches = matches + 1; found = lag; end
        end
        chk("host golden 64/64 unique lag=4", matches == 1 && found == 4);

        if (errs == 0) $display("PASS iq_impair");
        else           $display("FAIL iq_impair (%0d errors)", errs);
        $finish;
    end

    initial begin
        #1_000_000;
        $display("FAIL iq_impair (timeout)");
        $finish;
    end
endmodule
```

- [ ] **Step 3: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/iq_impair.v src/rtl/sincos_rom.v src/tb/iq_impair_tb.v
```
Expected: Step 2 之前会报 `module 'iq_impair' not found`；Step 2 之后（golden 仍是占位 0）
会编译通过但 `FAIL host golden 64/64 unique lag=4` 与 `FAIL iq_impair (2 errors)`

- [ ] **Step 4: 用模型算 golden 并填回 TB**

```bash
cd "<LOCAL_PATH>"
host/.venv/Scripts/python -c "
import sys; sys.path.insert(0,'host')
from siggen.channel import apply_iq_impair, iq_coef_words
i = [0,100,-50,20,0,0,-70,0,0,0,127,-128,64,-64,33,-33,10,-10,5,-5,1,-1,90,-90,45,-45,60,-60,70,-70,80,-80]
q = [0,-40,60,-20,0,0,80,0,0,0,-128,127,-64,64,-33,33,-10,10,-5,5,-1,1,-90,90,-45,45,-60,60,-70,70,-80,80]
gi, gq = apply_iq_impair(i, q, 15384, -4096, 4096, 16000, fword=0x04000000)
print(iq_coef_words(15384, -4096, 4096, 16000))
print('g_i:', ', '.join('g_i[%d]=%d;' % (k, v) for k, v in enumerate(gi)))
print('g_q:', ', '.join('g_q[%d]=%d;' % (k, v) for k, v in enumerate(gq)))
"
```
把第三条/第四条输出替换掉 TB 里的 `for (n = 0; n < 32; n = n + 1) begin g_i[n] = 0; g_q[n] = 0; end`
（用逐条 `g_i[n]=...;` 赋值展开）。第一条输出必须与 TB 里的
`coef_ab = 32'hF000_3C18; coef_cd = 32'h3E80_1000;` 一致；不一致就按输出改 TB 的常量。

- [ ] **Step 5: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/iq_impair.v src/rtl/sincos_rom.v src/tb/iq_impair_tb.v
"$SIM/xelab.bat" iq_impair_tb -s iqimp_sim
"$SIM/xsim.bat" iqimp_sim -runall
```
Expected: 5 个 `PASS ...` 后 `PASS iq_impair`

- [ ] **Step 6: 提交**

```bash
git add src/rtl/iq_impair.v src/tb/iq_impair_tb.v
git commit -m "feat(rtl): complex I/Q impairment stage

Two cascaded 2x2 real matrices -- a static one carrying phase offset,
gain imbalance and IQ phase imbalance, and a rotating one for frequency
offset -- in front of the final output mux, so it only ever touches the
digital-modulation pair and the standard AWG path stays bit-exact.

Every product is registered before the adders and every adder result is
registered again, so the synthesiser cannot chain DSP48s through
PCOUT/PCIN. That is the failure mode that already cost this project two
timing closures. The bypass is a combinational mux rather than a
registered one, which keeps dac_sel0/1 bit-identical to the old
expression when the stage is off.

Coefficients are Q2.14 because gain imbalance needs alpha = 1+eps > 1,
which Q1.15 cannot represent.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task 4: `chan_cfg.v` 排空字 8-10 + TB

**Files:**
- Modify: `src/rtl/chan_cfg.v`
- Test: `src/tb/chan_cfg_tb.v`

**Interfaces:**
- Produces: `chan_cfg` 新增输出
  `output reg [31:0] coef_ab, coef_cd, fword;`
  —— 字 8（`B[31:16] | A[15:0]`）、字 9（`D[31:16] | C[15:0]`）、字 10（相位增量），
  均为 Q2.14/32 位原始码。**任意一次 COMMIT（CH1 或 CH2）都会一并锁存这三个字**，
  字 11 与 12-15 本模块不排空。

- [ ] **Step 1: 改 TB（先让新用例失败）**

替换 `src/tb/chan_cfg_tb.v` 为下面这版（新增字 8-10 的用例、把排空等待放宽到 7 个字、
并加一条"字 11 从不被寻址"的监视）:

```verilog
`timescale 1ns / 1ps
// chan_cfg TB:
// - 伪 BRAM 为 **同步** 读模型(R12): doutb 经一拍寄存, 绝不用组合 case,
//   否则会掩盖真硬件上一拍的地址/数据错位。
// - 单位增益为 255(R2, 9-bit 有符号满幅), 不是 256。
// - 每通道排空 7 个字: CH1 = {0,1,2,3,8,9,10}, CH2 = {4,5,6,7,8,9,10}。
// 覆盖: 未 commit 全 0 / CH1 单独 commit / CH2 单独 commit / 两通道同时 commit 都不丢 /
//       字 8-10 在任意一次 commit 时一并锁存 / 预留字 11 永不被寻址。
module chan_cfg_tb;
    reg clk = 0, rst_n = 0;
    reg ch1_commit = 0, ch2_commit = 0;
    wire [4:0]  tap_b_addr;
    wire        tap_b_en;
    reg  [31:0] tap_b_rdata = 0;

    wire [35:0] ch1_gain, ch1_delay, ch2_gain, ch2_delay;
    wire [31:0] coef_ab, coef_cd, fword;

    chan_cfg uut (
        .clk(clk), .rst_n(rst_n),
        .ch1_commit(ch1_commit), .ch2_commit(ch2_commit),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay),
        .coef_ab(coef_ab), .coef_cd(coef_cd), .fword(fword));

    always #10 clk = ~clk;

    // 32-bit x 16 伪 BRAM; 字 = delay[17:9] | gain[8:0]。
    // 同步读: 第 N 拍给地址, 数据第 N+1 拍出现在 tap_b_rdata。
    reg [31:0] mem [0:15];
    integer i;
    initial begin
        for (i = 0; i < 16; i = i + 1) mem[i] = 32'd0;
        // CH1 tap0..3 = gain 255, delay 0..3
        mem[0] = {9'd0, 9'sd255};
        mem[1] = {9'd1, 9'sd255};
        mem[2] = {9'd2, 9'sd255};
        mem[3] = {9'd3, 9'sd255};
        // CH2 tap0..3 = gain 128, delay 4..7
        mem[4] = {9'd4, 9'sd128};
        mem[5] = {9'd5, 9'sd128};
        mem[6] = {9'd6, 9'sd128};
        mem[7] = {9'd7, 9'sd128};
        // 复数 I/Q 级: 单位矩阵 A=D=16384(Q2.14), B=C=0, fword=0
        mem[8]  = 32'h0000_4000;
        mem[9]  = 32'h4000_0000;
        mem[10] = 32'd0;
        mem[11] = 32'hDEAD_BEEF;   // 预留字, 不得被排空
    end
    always @(posedge clk) tap_b_rdata <= mem[tap_b_addr];

    // 字 11 永不被寻址
    reg saw_word11 = 0;
    always @(posedge clk) if (tap_b_en && tap_b_addr == 5'd11) saw_word11 = 1;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    task wait_cycles; input integer n; integer j;
        begin for (j = 0; j < n; j = j + 1) @(posedge clk); #1; end
    endtask

    `define G(v,k) v[k*9 +: 9]
    `define D(v,k) v[k*9 +: 9]

    initial begin
        #100 rst_n = 1;
        repeat (4) @(posedge clk);
        #1;
        // 任何 commit 之前: 全部为 0
        chk("idle ch1 zero", ch1_gain == 36'd0 && ch1_delay == 36'd0);
        chk("idle ch2 zero", ch2_gain == 36'd0 && ch2_delay == 36'd0);
        chk("idle iq params zero", coef_ab == 32'd0 && coef_cd == 32'd0 && fword == 32'd0);

        // Phase A: CH1 单独 commit (7 个字 = 21 拍, 留裕量)
        @(negedge clk); ch1_commit = 1;
        wait_cycles(40);
        chk("ch1 t0 gain",  `G(ch1_gain,0) == 9'sd255);
        chk("ch1 t0 delay", `D(ch1_delay,0) == 9'd0);
        chk("ch1 t1 delay", `D(ch1_delay,1) == 9'd1);
        chk("ch1 t2 gain",  `G(ch1_gain,2) == 9'sd255);
        chk("ch1 t3 delay", `D(ch1_delay,3) == 9'd3);
        chk("ch2 untouched A", ch2_gain == 36'd0 && ch2_delay == 36'd0);
        chk("ch1 commit latches iq word 8",  coef_ab == 32'h0000_4000);
        chk("ch1 commit latches iq word 9",  coef_cd == 32'h4000_0000);
        chk("ch1 commit latches iq word 10", fword == 32'd0);

        // Phase B: CH2 单独 commit
        @(negedge clk); ch2_commit = 1;
        wait_cycles(40);
        chk("ch2 t0 gain",  `G(ch2_gain,0) == 9'sd128);
        chk("ch2 t0 delay", `D(ch2_delay,0) == 9'd4);
        chk("ch2 t3 gain",  `G(ch2_gain,3) == 9'sd128);
        chk("ch2 t3 delay", `D(ch2_delay,3) == 9'd7);
        chk("ch1 unchanged B", `G(ch1_gain,0) == 9'sd255 && `D(ch1_delay,3) == 9'd3);

        // Phase C: 两通道同时 commit, 且内容变更以证明两路都真正刷新
        mem[0] = {9'd10, 9'sd255}; mem[1] = {9'd11, 9'sd255};
        mem[2] = {9'd12, 9'sd255}; mem[3] = {9'd13, 9'sd255};
        mem[4] = {9'd20, 9'sd129}; mem[5] = {9'd21, 9'sd129};
        mem[6] = {9'd22, 9'sd129}; mem[7] = {9'd23, 9'sd129};
        // 换一组 I/Q 系数, 证明字 8-10 真的被重新锁存(而不是保持旧值)
        mem[8]  = 32'hF000_3C18;   // B=-4096, A=15384
        mem[9]  = 32'h3E80_1000;   // D=16000, C=4096
        mem[10] = 32'h0400_0000;
        @(negedge clk); ch1_commit = 0; ch2_commit = 0;
        wait_cycles(80);
        chk("both ch1 t0 delay", `D(ch1_delay,0) == 9'd10);
        chk("both ch1 t3 delay", `D(ch1_delay,3) == 9'd13);
        chk("both ch1 gain",     `G(ch1_gain,0) == 9'sd255);
        chk("both ch2 t0 delay", `D(ch2_delay,0) == 9'd20);
        chk("both ch2 t3 delay", `D(ch2_delay,3) == 9'd23);
        chk("both ch2 gain",     `G(ch2_gain,0) == 9'sd129);
        chk("both refresh iq word 8",  coef_ab == 32'hF000_3C18);
        chk("both refresh iq word 9",  coef_cd == 32'h3E80_1000);
        chk("both refresh iq word 10", fword == 32'h0400_0000);
        chk("reserved word 11 never addressed", saw_word11 == 1'b0);

        if (errs == 0) $display("PASS chan_cfg");
        else $display("FAIL chan_cfg (%0d errors)", errs);
        $finish;
    end

    initial begin
        #400000;
        $display("FAIL chan_cfg (timeout)");
        $finish;
    end
endmodule
```

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
```
Expected: 编译失败 —— `chan_cfg` 没有 `coef_ab` / `coef_cd` / `fword` 端口

- [ ] **Step 3: 改 `chan_cfg.v`**

用下面这版整体替换（关键改动：`step` 扩到 0..6、`word_next` 地址表、
`S_CAP` 按 step 分派锁存、三个新输出在复位块中清零）:

```verilog
`timescale 1ns / 1ps
// 信道抽头表读取/锁存 FSM。
// CH1 commit 排空 {0,1,2,3, 8,9,10}; CH2 commit 排空 {4,5,6,7, 8,9,10}。
//   字 0-3 / 4-7  每通道抽头: gain[8:0] | delay[17:9]
//   字 8/9/10     **通道对级**复数 I/Q 级参数: coef_ab / coef_cd / fword
// 字 8-10 在**任意一次 COMMIT** 时一并锁存 —— 它们是静态系数, 不存在需要原子生效
// 的状态机时序, torn-update 风险与抽头字同级(主机写齐再 commit 即可), 因此不新增
// 专门的 IQ commit 命令, 也不占 epoch 位。字 11 与 12-15 本模块不排空。
//
// chX_commit 是 PS 侧同步后的 epoch 翻转位: 观察到翻转(边沿)即置该通道 pending。
// 两通道 pending 独立维护, 逐个排空(两路同时到达时 CH1 优先), 因此同时到达的
// CH2 commit 不会被丢弃; 排空完成的那一拍若又出现新边沿, pending 保持。
// 首个 commit 之前所有输出保持 0(FIR/矩阵未配置即全零)。
//
// BRAM 端口 B 为 **同步** 读(R12): 第 N 拍给出 tap_b_addr/tap_b_en, BRAM 于
// 第 N+1 拍采样地址, 数据第 N+1 拍才出现在 tap_b_rdata, 故第 N+2 拍锁存。
// 状态机: IDLE -给出地址-> WAIT(地址保持, BRAM 采样) -> CAP(锁存数据)。
module chan_cfg (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        ch1_commit,
    input  wire        ch2_commit,
    output reg  [4:0]  tap_b_addr,
    output reg         tap_b_en,
    input  wire [31:0] tap_b_rdata,
    output reg  [35:0] ch1_gain,
    output reg  [35:0] ch1_delay,
    output reg  [35:0] ch2_gain,
    output reg  [35:0] ch2_delay,
    output reg  [31:0] coef_ab,     // 字 8: B[31:16] | A[15:0], Q2.14 有符号
    output reg  [31:0] coef_cd,     // 字 9: D[31:16] | C[15:0], Q2.14 有符号
    output reg  [31:0] fword        // 字 10: 频率偏移相位增量
);
    localparam S_IDLE = 2'd0, S_WAIT = 2'd1, S_CAP = 2'd2;
    localparam [2:0] LAST = 3'd6;   // step 0..3 抽头; 4/5/6 = 字 8/9/10

    reg [1:0] state;
    reg       cur_ch;          // 0 = CH1, 1 = CH2
    reg [2:0] step;            // 0..6
    reg       commit1_d, commit2_d;
    reg       pend1, pend2;    // 每通道 commit pending

    wire edge1 = (ch1_commit != commit1_d);
    wire edge2 = (ch2_commit != commit2_d);

    wire [4:0] base = cur_ch ? 5'd4 : 5'd0;
    wire [2:0] nxt  = step + 3'd1;
    // 地址表: step 0..3 -> base+step(抽头); step 4/5/6 -> 字 8/9/10(与通道无关)
    wire [4:0] word_next = (nxt < 3'd4) ? (base + {2'd0, nxt}) : (5'd4 + {2'd0, nxt});
    wire       done_ch   = (state == S_CAP) && (step == LAST);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            cur_ch     <= 1'b0;
            step       <= 3'd0;
            commit1_d  <= 1'b0;
            commit2_d  <= 1'b0;
            pend1      <= 1'b0;
            pend2      <= 1'b0;
            tap_b_addr <= 5'd0;
            tap_b_en   <= 1'b0;
            ch1_gain   <= 36'd0;
            ch1_delay  <= 36'd0;
            ch2_gain   <= 36'd0;
            ch2_delay  <= 36'd0;
            coef_ab    <= 32'd0;
            coef_cd    <= 32'd0;
            fword      <= 32'd0;
        end else begin
            commit1_d <= ch1_commit;
            commit2_d <= ch2_commit;

            // pending: 边沿优先置位; 仅当本通道排空完成且同一拍无新边沿才清除,
            // 保证同时/紧邻到达的另一字节不被静默丢弃。
            if (edge1) pend1 <= 1'b1;
            else if (done_ch && cur_ch == 1'b0) pend1 <= 1'b0;
            if (edge2) pend2 <= 1'b1;
            else if (done_ch && cur_ch == 1'b1) pend2 <= 1'b0;

            case (state)
                S_IDLE: begin
                    tap_b_en <= 1'b0;
                    if (pend1 || pend2) begin
                        cur_ch     <= pend1 ? 1'b0 : 1'b1;   // CH1 优先
                        step       <= 3'd0;
                        tap_b_addr <= pend1 ? 5'd0 : 5'd4;
                        tap_b_en   <= 1'b1;
                        state      <= S_WAIT;
                    end
                end
                S_WAIT: begin
                    // 地址/使能保持本拍, BRAM 在本拍末采样, 数据下拍有效
                    state <= S_CAP;
                end
                S_CAP: begin
                    case (step)
                        3'd0, 3'd1, 3'd2, 3'd3: begin
                            // 抽头字: gain[8:0], delay[17:9]
                            if (cur_ch == 1'b0) begin
                                ch1_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch1_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end else begin
                                ch2_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch2_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end
                        end
                        3'd4: coef_ab <= tap_b_rdata;   // 字 8: A | B
                        3'd5: coef_cd <= tap_b_rdata;   // 字 9: C | D
                        default: fword <= tap_b_rdata;  // 字 10: 频偏相位增量
                    endcase
                    if (step == LAST) begin
                        tap_b_en <= 1'b0;
                        state    <= S_IDLE;
                    end else begin
                        step       <= nxt;
                        tap_b_addr <= word_next;
                        tap_b_en   <= 1'b1;
                        state      <= S_WAIT;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
```

- [ ] **Step 4: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
"$SIM/xelab.bat" chan_cfg_tb -s cfg_sim && "$SIM/xsim.bat" cfg_sim -runall
```
Expected: `PASS chan_cfg`（既有 20 项 + 新增 7 项）

- [ ] **Step 5: 提交**

```bash
git add src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
git commit -m "feat(rtl): drain the I/Q matrix words in chan_cfg

Each channel's walk grows from 4 tap words to 7, using an address table
so the non-contiguous layout (0-3 then 8-10) stays legible. Words 8-10
are pair-level rather than per-channel, so either channel's COMMIT
latches them; they are static coefficients with no atomicity
requirement, so no dedicated IQ commit command and no extra epoch bit
is spent. Word 11 stays reserved and is never addressed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 使能位解码 + `awg_top` 最终选择改造 + TB

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/rtl/awg_top.v`
- Test: `src/tb/awg_top_tb.v`

**Interfaces:**
- Consumes: Task 3 的 `iq_impair` 端口、Task 4 的 `chan_cfg` 输出
- Produces:
  - `reg_bridge` 新增输出 `ch1_iq_en`（CH1 控制字 bit11）
  - `awg_top` 内部新增 wire `dm_i`/`dm_q`/`dm_active`/`imp_i`/`imp_q`/`iq_coef_ab`/`iq_coef_cd`/`iq_fword`
  - **`awg_top` 的端口列表不变**（BD 模块引用不动）

- [ ] **Step 1: 写失败 TB 用例**

在 `src/tb/awg_top_tb.v` 里：

(a) 把 `reg [31:0] tap_b_rdata = 32'd0;` 这一行替换成按地址给出的伪 BRAM
（原有断言不依赖 `tap_b_rdata`，本改动不影响它们）:

```verilog
    wire [31:0] tap_b_rdata;
    // 伪 BRAM: 只有复数 I/Q 级的三个字非零(单位矩阵 + 零频偏)
    reg [31:0] tap_mem [0:15];
    reg [31:0] tap_b_rdata_r;
    initial begin
        integer ti;
        for (ti = 0; ti < 16; ti = ti + 1) tap_mem[ti] = 32'd0;
        tap_mem[8]  = 32'h0000_4000;   // A=16384, B=0    (Q2.14)
        tap_mem[9]  = 32'h4000_0000;   // C=0,     D=16384
        tap_mem[10] = 32'd0;           // fword=0
    end
    always @(posedge clk) tap_b_rdata_r <= tap_mem[tap_b_addr[3:0]];
    assign tap_b_rdata = tap_b_rdata_r;
```

(b) 在文件末尾（`if (errs == 0) $display("PASS awg_top_tb");` 之前）追加：

```verilog
        // ---- 复数 I/Q 级(B 组): 旁路逐位 + 使能后的固定延时 ----
        gpio11_in = (32'd2 << 17);          // RECT QPSK
        gpio12_in = 32'h4000_0000;          // 每 4 拍一个 symbol
        ch1_chan_word = 32'd0;
        #1000;
        chk("iq_impair disabled by default", uut.iq_impair_en === 1'b0);
        chk("iq_impair bypass is bit-exact",
            uut.imp_i === uut.dm_i && uut.imp_q === uut.dm_q);
        chk("dac_sel follows dm when iq stage bypassed",
            uut.dac_sel0 === uut.dm_i && uut.dac_sel1 === uut.dm_q);

        // COMMIT 排空字 0..6 -> 锁存单位矩阵; 再置 bit11 使能
        ch1_chan_word = 32'd1 << 8;          // commit epoch 翻转
        #2000;
        chk("chan_cfg latched identity coef_ab", uut.iq_coef_ab === 32'h0000_4000);
        chk("chan_cfg latched identity coef_cd", uut.iq_coef_cd === 32'h4000_0000);
        chk("chan_cfg latched zero fword",       uut.iq_fword === 32'd0);
        ch1_chan_word = (32'd1 << 11) | (32'd1 << 8);   // iq_impair_en = 1
        #200;
        chk("iq_impair enable from CH1 control word bit11",
            uut.iq_impair_en === 1'b1);

        begin : tiqdelay
            integer k, lag, bad, matches, found;
            reg [7:0] dma [0:63];
            reg [7:0] ima [0:63];
            // dma/ima 都在**同一拍之后**采样(索引 k 对应第 k 拍后的值), 故索引差
            // 就等于流水级数 5; 只有唯一一个 lag 能对上全部 48 对样点。
            for (k = 0; k < 64; k = k + 1) begin
                @(posedge clk); #1;
                dma[k] = uut.dm_i;
                ima[k] = uut.imp_i;
            end
            matches = 0; found = 0;
            for (lag = 1; lag <= 12; lag = lag + 1) begin
                bad = 0;
                for (k = 16; k < 64; k = k + 1)
                    if (ima[k] !== dma[k - lag]) bad = bad + 1;
                if (bad == 0) begin matches = matches + 1; found = lag; end
            end
            chk("iq_impair identity matrix: unique fixed-lag copy of dm_i",
                matches == 1);
            chk("iq_impair identity matrix latency is 5 samples", found == 5);
        end
        ch1_chan_word = 32'd0;
        gpio11_in = 32'd0;
        gpio12_in = 32'd858993;
        #1000;
```

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
```
Expected: 编译失败 —— `iq_impair_en` / `dm_i` / `iq_coef_ab` 不存在

- [ ] **Step 3: 改 `reg_bridge.v`**

端口列表末尾（`ch1_tap_commit, ch2_tap_commit` 之后）加一个逗号并追加：

```verilog
    output wire        ch1_tap_commit, ch2_tap_commit,
    // 复数 I/Q 级使能(通道对级, 只在数字调制模式下作用于 DAC0/DAC1 这一对)。
    // 借用 CH1 控制字的空闲位: 固件 write_chan_ctrl() 把控制字截到低 16 位,
    // 故只能用 bits 9-15, bit11 落在可达范围内。
    output wire        ch1_iq_en
```

在 `assign ch2_tap_commit = r18[8];` 之后追加：

```verilog
    assign ch1_iq_en      = r17[11];
```

- [ ] **Step 4: 改 `awg_top.v`**

(a) 在 `wire ch1_tap_commit, ch2_tap_commit;`（约 146 行）之后追加：

```verilog
    wire ch1_iq_en;
    wire [31:0] iq_coef_ab, iq_coef_cd, iq_fword;
```

(b) `reg_bridge` 实例（约 192 行）补一个新端口：

```verilog
        .ch1_tap_commit(ch1_tap_commit), .ch2_tap_commit(ch2_tap_commit),
        .ch1_iq_en(ch1_iq_en)
```

(c) 把 529-534 行那段（`rrc_i8`/`rrc_q8` 与 `dac_sel0/1`）替换为：

```verilog
    wire signed [7:0] rrc_i8 = rrc_to_dac(rrc_i16);
    wire signed [7:0] rrc_q8 = rrc_to_dac(rrc_q16);
    // ---- 复数 I/Q 损伤级(iq_impair): 只作用于数字调制支路 ----
    // 先把数字调制的 I/Q 对单独 mux 出来, 过完复数级再进最终选择。
    // 与旧表达式 `rrc_active ? rrc_i8 : rect_active ? dig_i : a1` 完全等价:
    // rrc_active 与 rect_active 由 digmod_filter 互斥(见上文的 rect_active/rrc_active)。
    // 因此标准 AWG 路径逐位不变; 数字调制支路在使能时多 5 拍 —— 自由运行流上的
    // 恒定相移无害, 与下方 chan_impair 的既有论证同理。
    wire [7:0] dm_i = rrc_active ? rrc_i8 : dig_i;
    wire [7:0] dm_q = rrc_active ? rrc_q8 : dig_q;
    wire       dm_active = rrc_active || rect_active;
    wire [7:0] imp_i, imp_q;
    iq_impair u_iq_impair (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch1_iq_en),
        .i_in(dm_i), .q_in(dm_q),
        .coef_ab(iq_coef_ab), .coef_cd(iq_coef_cd), .fword(iq_fword),
        .i_out(imp_i), .q_out(imp_q)
    );
    // 最终选择后进信道损伤级(chan_impair)。chan_en=0 时为逐位 1 拍寄存透传,
    // 整条通路因此比原设计多一级 50 MHz 流水(自由运行流, 恒定 20 ns 相移无害)。
    wire [7:0] dac_sel0 = dm_active ? imp_i : a1;
    wire [7:0] dac_sel1 = dm_active ? imp_q : a2;
```

(d) `chan_cfg u_chan_cfg (...)` 实例补上三个新端口：

```verilog
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay),
        .coef_ab(iq_coef_ab), .coef_cd(iq_coef_cd), .fword(iq_fword)
```

- [ ] **Step 5: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s awg_sim && "$SIM/xsim.bat" awg_sim -runall
```
Expected: `PASS awg_top_tb`（既有 106 项全过 + 新增 8 项；既有
`dac_sel0 === rrc_i8` 等断言因旁路是组合 mux 而保持不变）

- [ ] **Step 6: 同时跑其余受影响 TB**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
for tb in reg_bridge_tb chan_impair_tb chan_cfg_tb iq_impair_tb sincos_rom_tb; do
  "$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/$tb.v >/dev/null || echo "XVLOG FAIL $tb"
  "$SIM/xelab.bat" $tb -s ${tb}_sim >/dev/null && "$SIM/xsim.bat" ${tb}_sim -runall | tail -1
done
```
Expected: 每个都输出 `PASS <模块名>`

- [ ] **Step 7: 提交**

```bash
git add src/rtl/reg_bridge.v src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat(rtl): route the digital-modulation I/Q pair through iq_impair

The final mux now picks the digital-modulation pair out first, runs it
through one complex stage, and only then selects between that and the
standard AWG output. Algebraically identical to the old nested ternary
because rrc_active and rect_active are mutually exclusive on
digmod_filter, so the standard AWG path is bit-for-bit unchanged and
the existing dac_sel0 == rrc_i8 assertions still hold.

The extra five cycles land only on the digital-modulation branch.
Digital modulation and the standard AWG are mutually exclusive
free-running streams, which is the same argument the channel-impairment
stage already relies on.

Enables ride CH1 control-word bit 11 because firmware write_chan_ctrl
masks the word to 16 bits; bits 16-31 are electrically free but
unreachable from the PS.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task 6: 固件 `IQIMP` 家族、矩阵合成与字 8-10

**Files:**
- Modify: `sdk/src/main.c`
- Consumes: Task 2 的 `sdk/src/sincos_table.h`、Task 4/5 的字映射与 bit11

**Interfaces:**
- Produces（串口协议）:
  - `SET IQIMP ON | OFF`
  - `SET IQIMP PHASE <deg_x100 -36000..36000>`
  - `SET IQIMP FREQOFF <hz -1000000..1000000>`
  - `SET IQIMP GAIN <q14 -32768..32767; 16384 = 1.0>`
  - `SET IQIMP PHASEERR <deg_x100 -18000..18000>`
  - `GET IQIMP` → `IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0`
  - 越界一律 `ERR IQIMP OUT OF RANGE`，参数名不认识则 `ERR BAD VALUE`
- **语义**：`ON/OFF` 写 CH1 控制字 bit11，**立即生效**；`PHASE/FREQOFF/GAIN/PHASEERR`
  写抽头表 BRAM 字 8/9/10，**需随后的 `SET CH1 CHAN COMMIT`（或 CH2）才被 PL 锁存**。

- [ ] **Step 1: 加表头与状态、矩阵合成函数**

(a) 在 `#include <stdio.h>` 之后加一行（`sincos_table.h` 由 `tools/gen_sincos.py` 生成）：

```c
#include "sincos_table.h"   /* 1024 x Q2.14 sin/cos, 与 src/rtl/sincos_rom.v 同值 */
```

(b) 在 `static u32 chan_tap[2][4] = {{0}};`（约 302 行）之后追加：

```c
/* Task B: 复数 I/Q 级(通道对级)的高层参数影子。数值写抽头表 BRAM 字 8-10,
 * 使能写 CH1 控制字 bit11(见 chan_word), 均不改既有位域。
 * 静态三项由主机侧合成为一个 2x2 矩阵 —— 硬件只做矩阵乘, 不区分用户设了哪一项
 * (规格「已确认决策」)。本文件与 host/siggen/channel.py::iq_impair_matrix()
 * 是同一算法的两个实现, 必须逐位一致。 */
static long iq_phase     = 0;       /* 相位偏移, 0.01 度 */
static long iq_phase_err = 0;       /* I/Q 相位失配, 0.01 度 */
static long iq_gain      = 16384;   /* I/Q 增益, Q2.14(16384 = 1.0) */
static long iq_freqoff   = 0;       /* 频率偏移, Hz */
```

(c) 在 `static long sign9(u32 v)` 之前插入（不要用 libm：裸机链接不引入浮点库，
且必须与 Python 模型逐位一致）：

```c
/* 0.01 度 -> Q2.14 sin/cos: 1024 点表 + 线性插值(同 host 的 sincos_q14)。 */
static long iqimp_sincos_q14(long deg_x100, int want_cos)
{
    long pos, num, i0, frac, a, b;
    long long v;
    pos = deg_x100 % 36000L;
    if (pos < 0) pos += 36000L;
    num  = pos * 1024L;
    i0   = num / 36000L;
    frac = (num % 36000L) * 1024L / 36000L;          /* 0..1023 */
    a = (long)(want_cos ? SINCOS_COS[i0 & 1023] : SINCOS_SIN[i0 & 1023]);
    b = (long)(want_cos ? SINCOS_COS[(i0 + 1) & 1023] : SINCOS_SIN[(i0 + 1) & 1023]);
    v = (long long)a * (1024L - frac) + (long long)b * frac;
    return (long)((v >= 0) ? ((v + 512) >> 10) : -(((-v) + 512) >> 10));
}

/* Q2.14 x Q2.14 -> Q2.14, 远离零四舍五入 */
static long iqimp_mul_q14(long a, long b)
{
    long long v = (long long)a * (long long)b;
    return (long)((v >= 0) ? ((v + 8192) >> 14) : -(((-v) + 8192) >> 14));
}

static long iqimp_clamp_q14(long v)
{
    if (v > 32767L) return 32767L;
    if (v < -32768L) return -32768L;
    return v;
}

/* 频率偏移 Hz -> 32 位相位增量 round(f / 50e6 * 2^32); 64 位中间量防溢出 */
static u32 iqimp_hz_to_fword32(long hz)
{
    long long v = (long long)hz * 4294967296LL;
    long long q;
    if (v >= 0) q = (v + 25000000LL) / 50000000LL;
    else        q = -(((-v) + 25000000LL) / 50000000LL);
    return (u32)(q & 0xFFFFFFFFLL);
}

/* M = R(phase) x diag(g,1) x P(phase_err) -> BRAM 字 8(A|B) / 9(C|D) / 10(fword)。
 * P = [[cos(e/2), -sin(e/2)], [-sin(e/2), cos(e/2)]] 是对称非正交的 I/Q 相位失配
 * 矩阵(把 I/Q 轴夹角变成 90-e), 使 E[I*Q] = -sigma^2 * sin(e) —— 正是示波器
 * 用例要量的二阶统计量。 */
static void write_iq_words(void)
{
    long half = (iq_phase_err >= 0) ? (iq_phase_err / 2) : -((-iq_phase_err) / 2);
    long c1, s1, c2, s2, dp00, dp01, dp10, dp11, a, b, c, d;
    u32 ab, cd;
    if (CHAN_TAP_BRAM_BASEADDR == 0) return;
    c1 = iqimp_sincos_q14(iq_phase, 1);
    s1 = iqimp_sincos_q14(iq_phase, 0);
    c2 = iqimp_sincos_q14(half, 1);
    s2 = iqimp_sincos_q14(half, 0);
    dp00 =  iqimp_mul_q14(iq_gain, c2);
    dp01 = -iqimp_mul_q14(iq_gain, s2);
    dp10 = -iqimp_mul_q14(16384L, s2);
    dp11 =  iqimp_mul_q14(16384L, c2);
    a = iqimp_clamp_q14(iqimp_mul_q14(c1, dp00) - iqimp_mul_q14(s1, dp10));
    b = iqimp_clamp_q14(iqimp_mul_q14(c1, dp01) - iqimp_mul_q14(s1, dp11));
    c = iqimp_clamp_q14(iqimp_mul_q14(s1, dp00) + iqimp_mul_q14(c1, dp10));
    d = iqimp_clamp_q14(iqimp_mul_q14(s1, dp01) + iqimp_mul_q14(c1, dp11));
    ab = ((u32)(b & 0xFFFFL) << 16) | (u32)(a & 0xFFFFL);
    cd = ((u32)(d & 0xFFFFL) << 16) | (u32)(c & 0xFFFFL);
    Xil_Out32(CHAN_TAP_BRAM_BASEADDR +  8u * 4u, ab);
    Xil_Out32(CHAN_TAP_BRAM_BASEADDR +  9u * 4u, cd);
    Xil_Out32(CHAN_TAP_BRAM_BASEADDR + 10u * 4u, iqimp_hz_to_fword32(iq_freqoff));
}
```

- [ ] **Step 2: 加 `GET IQIMP`**

在 GET 分支里、`main.c:1919` 的 `if (n == 3 && (...CH1...CH2...) && strcmp(tok[2], "CHAN") == 0)`
**之前**插入（放在 PLAY 块之后）：

```c
        if (n == 2 && strcmp(tok[1], "IQIMP") == 0) {
            char out[128];
            sprintf(out, "IQIMP %s PHASE %ld FREQOFF %ld GAIN %ld PHASEERR %ld\r\n",
                    (chan_word[0] & (1u << 11)) ? "ON" : "OFF",
                    iq_phase, iq_freqoff, iq_gain, iq_phase_err);
            uart_send(out);
            return;
        }
```

- [ ] **Step 3: 加 `SET IQIMP`**

在 SET 分支里、`main.c:2488` 的 `if (n >= 3 && (...CH1...CH2...) && strcmp(tok[2], "CHAN") == 0)`
**之前**插入：

```c
        /* 复数 I/Q 级(B 组)。使能立即生效(GPIO), 数值写 BRAM 需 COMMIT 才锁存 ——
         * 与既有 chan_en(GPIO) / 抽头(BRAM+COMMIT) 的分工一致。 */
        if (n >= 3 && strcmp(tok[1], "IQIMP") == 0) {
            if (n == 3 && strcmp(tok[2], "ON") == 0) {
                chan_word[0] |= (1u << 11);
            } else if (n == 3 && strcmp(tok[2], "OFF") == 0) {
                chan_word[0] &= ~(1u << 11);
            } else if (n == 4 && strcmp(tok[2], "PHASE") == 0) {
                long v;
                if (!parse_long(tok[3], &v) || v < -36000L || v > 36000L) {
                    uart_send("ERR IQIMP OUT OF RANGE\r\n"); return;
                }
                iq_phase = v;
                write_iq_words();
            } else if (n == 4 && strcmp(tok[2], "FREQOFF") == 0) {
                long v;
                if (!parse_long(tok[3], &v) || v < -1000000L || v > 1000000L) {
                    uart_send("ERR IQIMP OUT OF RANGE\r\n"); return;
                }
                iq_freqoff = v;
                write_iq_words();
            } else if (n == 4 && strcmp(tok[2], "GAIN") == 0) {
                long v;
                if (!parse_long(tok[3], &v) || v < -32768L || v > 32767L) {
                    uart_send("ERR IQIMP OUT OF RANGE\r\n"); return;
                }
                iq_gain = v;
                write_iq_words();
            } else if (n == 4 && strcmp(tok[2], "PHASEERR") == 0) {
                long v;
                if (!parse_long(tok[3], &v) || v < -18000L || v > 18000L) {
                    uart_send("ERR IQIMP OUT OF RANGE\r\n"); return;
                }
                iq_phase_err = v;
                write_iq_words();
            } else {
                uart_send("ERR BAD VALUE\r\n"); return;
            }
            write_chan_ctrl();
            uart_send("OK\r\n"); return;
        }
```

- [ ] **Step 4: 构建固件**

```bash
vivado2025.cmd app build awg_bridge --workspace sdk/ws2025
```
Expected: 编译通过；若报 `SINCOS_COS` 未定义，说明 `sdk/src/sincos_table.h` 没生成
（回到 Task 2 Step 2）

- [ ] **Step 5: 提交**

```bash
git add sdk/src/main.c sdk/src/sincos_table.h
git commit -m "feat(fw): IQIMP command family and matrix synthesis

The PS composes the static three impairments into the 2x2 the RTL
multiplies by, so the hardware never learns which impairment the user
chose. Synthesis is integer Q2.14 against the same 1024-point table the
rotation ROM uses, rather than libm: bare-metal links without the maths
library, and the host model has to agree with it bit for bit.

Enables toggle CH1 control-word bit 11 for immediate effect while
coefficients go to tap-BRAM words 8-10 and land on the next COMMIT,
matching how chan_en and the taps already divide the work.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task 7: 上位机协议构造器与解析器

**Files:**
- Modify: `host/siggen/protocol.py`
- Test: `host/tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `IQIMP_PHASE_MIN, IQIMP_PHASE_MAX = -36000, 36000`
  - `IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX = -18000, 18000`
  - `IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX = -1_000_000, 1_000_000`
  - `IQIMP_GAIN_MIN, IQIMP_GAIN_MAX = -32768, 32767`
  - `IQIMP_GAIN_UNITY = 16384`
  - `iqimp_enable_cmd(on: bool) -> str`
  - `iqimp_phase_cmd(deg_x100: int) -> str`
  - `iqimp_freqoff_cmd(hz: int) -> str`
  - `iqimp_gain_cmd(q14: int) -> str`
  - `iqimp_phaseerr_cmd(deg_x100: int) -> str`
  - `get_iqimp_cmd() -> str`
  - `parse_iqimp_status(line: str) -> dict`，键为
    `enabled`(bool)、`phase`、`freqoff`、`gain`、`phaseerr`(均 int)

- [ ] **Step 1: 写失败测试**

追加到 `host/tests/test_protocol.py`（顶部 import 列表补上新名字）：

```python
from siggen.protocol import (
    IQIMP_GAIN_UNITY,
    get_iqimp_cmd,
    iqimp_enable_cmd,
    iqimp_freqoff_cmd,
    iqimp_gain_cmd,
    iqimp_phase_cmd,
    iqimp_phaseerr_cmd,
    parse_iqimp_status,
)


def test_iqimp_enable_cmd():
    assert iqimp_enable_cmd(True) == "SET IQIMP ON"
    assert iqimp_enable_cmd(False) == "SET IQIMP OFF"
    with pytest.raises(ValueError):
        iqimp_enable_cmd(1)


def test_iqimp_phase_cmd_and_range():
    assert iqimp_phase_cmd(3000) == "SET IQIMP PHASE 3000"
    assert iqimp_phase_cmd(-3000) == "SET IQIMP PHASE -3000"
    with pytest.raises(ValueError):
        iqimp_phase_cmd(36001)
    with pytest.raises(ValueError):
        iqimp_phase_cmd(-36001)


def test_iqimp_freqoff_cmd_is_narrowed_to_1mhz():
    assert iqimp_freqoff_cmd(2000) == "SET IQIMP FREQOFF 2000"
    assert iqimp_freqoff_cmd(-1_000_000) == "SET IQIMP FREQOFF -1000000"
    with pytest.raises(ValueError):
        iqimp_freqoff_cmd(1_000_001)


def test_iqimp_gain_cmd_is_raw_q14():
    assert iqimp_gain_cmd(IQIMP_GAIN_UNITY) == "SET IQIMP GAIN 16384"
    assert iqimp_gain_cmd(20480) == "SET IQIMP GAIN 20480"
    with pytest.raises(ValueError):
        iqimp_gain_cmd(32768)
    with pytest.raises(ValueError):
        iqimp_gain_cmd(-32769)


def test_iqimp_phaseerr_cmd_range():
    assert iqimp_phaseerr_cmd(2000) == "SET IQIMP PHASEERR 2000"
    with pytest.raises(ValueError):
        iqimp_phaseerr_cmd(18001)


def test_get_iqimp_cmd():
    assert get_iqimp_cmd() == "GET IQIMP"


def test_parse_iqimp_status_round_trip():
    status = parse_iqimp_status(
        "IQIMP ON PHASE 3000 FREQOFF 2000 GAIN 20480 PHASEERR 2000")
    assert status == {"enabled": True, "phase": 3000, "freqoff": 2000,
                      "gain": 20480, "phaseerr": 2000}
    status = parse_iqimp_status("IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
    assert status["enabled"] is False and status["gain"] == IQIMP_GAIN_UNITY


def test_parse_iqimp_status_rejects_malformed_lines():
    for bad in ("IQIMP ON", "IQIMP PHASE 0 FREQOFF 0 GAIN 1 PHASEERR 0",
                "IQIMP 1 PHASE 0 FREQOFF 0 GAIN 1 PHASEERR 0",
                "IQIMP ON PHASE 0 FREQOFF 0 GAIN 1 BOGUS 0",
                "IQIMP ON PHASE 0 FREQOFF 0 GAIN 1 PHASEERR 99999",
                "IQIMP ON PHASE 99999 FREQOFF 0 GAIN 1 PHASEERR 0",
                "IQIMP ON PHASE 0 FREQOFF 0 GAIN 99999 PHASEERR 0"):
        with pytest.raises(ValueError):
            parse_iqimp_status(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v`
Expected: FAIL — `ImportError: cannot import name 'IQIMP_GAIN_UNITY'`

- [ ] **Step 3: 实现构造器与解析**

追加到 `host/siggen/protocol.py` 末尾：

```python
# ---- 复数 I/Q 级(IQIMP);通道对级, 只在数字调制模式下作用于 DAC0/DAC1 ----
IQIMP_PHASE_MIN, IQIMP_PHASE_MAX = -36000, 36000        # 0.01 度
IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX = -18000, 18000  # 0.01 度
IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX = -1_000_000, 1_000_000   # Hz
IQIMP_GAIN_MIN, IQIMP_GAIN_MAX = -32768, 32767          # Q2.14 原始码
IQIMP_GAIN_UNITY = 16384                                # Q2.14 的 1.0


def _iqimp_int(value, low, high, what):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{what} out of range: {value!r}")
    return value


def iqimp_enable_cmd(on: bool) -> str:
    """构造复数 I/Q 级使能命令;立即生效(GPIO)。"""
    if not isinstance(on, bool):
        raise ValueError(f"enable must be a bool: {on!r}")
    return f"SET IQIMP {'ON' if on else 'OFF'}"


def iqimp_phase_cmd(deg_x100: int) -> str:
    """静态相位偏移;±360° = ±36000(0.01 度)。"""
    return "SET IQIMP PHASE %d" % _iqimp_int(
        deg_x100, IQIMP_PHASE_MIN, IQIMP_PHASE_MAX, "iq phase")


def iqimp_freqoff_cmd(hz: int) -> str:
    """频率偏移;协议层刻意收窄到 ±1 MHz(硬件能力到 ±25 MHz)。"""
    return "SET IQIMP FREQOFF %d" % _iqimp_int(
        hz, IQIMP_FREQOFF_MIN, IQIMP_FREQOFF_MAX, "iq freq offset")


def iqimp_gain_cmd(q14: int) -> str:
    """I/Q 增益;Q2.14 原始码, 16384 = 1.0。"""
    return "SET IQIMP GAIN %d" % _iqimp_int(
        q14, IQIMP_GAIN_MIN, IQIMP_GAIN_MAX, "iq gain")


def iqimp_phaseerr_cmd(deg_x100: int) -> str:
    """I/Q 相位失配;±180° = ±18000(0.01 度)。"""
    return "SET IQIMP PHASEERR %d" % _iqimp_int(
        deg_x100, IQIMP_PHASEERR_MIN, IQIMP_PHASEERR_MAX, "iq phase error")


def get_iqimp_cmd() -> str:
    return "GET IQIMP"


def parse_iqimp_status(line: str) -> dict:
    """解析 `IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0`(共 10 个 token)。

    返回 {"enabled": bool, "phase": int, "freqoff": int, "gain": int, "phaseerr": int}。
    任何 token 数量/关键字错误或数值越界都抛 ValueError。
    """
    parts = line.split()
    if (len(parts) != 10 or parts[0] != "IQIMP" or parts[1] not in ("ON", "OFF")
            or parts[2] != "PHASE" or parts[4] != "FREQOFF"
            or parts[6] != "GAIN" or parts[8] != "PHASEERR"):
        raise ValueError(f"bad iqimp status line: {line!r}")
    try:
        phase = int(parts[3])
        freqoff = int(parts[5])
        gain = int(parts[7])
        phaseerr = int(parts[9])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad iqimp status line: {line!r}") from exc
    if not IQIMP_PHASE_MIN <= phase <= IQIMP_PHASE_MAX:
        raise ValueError(f"iq phase out of range: {line!r}")
    if not IQIMP_FREQOFF_MIN <= freqoff <= IQIMP_FREQOFF_MAX:
        raise ValueError(f"iq freq offset out of range: {line!r}")
    if not IQIMP_GAIN_MIN <= gain <= IQIMP_GAIN_MAX:
        raise ValueError(f"iq gain out of range: {line!r}")
    if not IQIMP_PHASEERR_MIN <= phaseerr <= IQIMP_PHASEERR_MAX:
        raise ValueError(f"iq phase error out of range: {line!r}")
    return {"enabled": parts[1] == "ON", "phase": phase, "freqoff": freqoff,
            "gain": gain, "phaseerr": phaseerr}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量上位机测试**

Run: `host/.venv/Scripts/python -m pytest host/tests/`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat(host): IQIMP command builders and status parser

The four I/Q parameters are pair-level rather than per-channel, so they
get their own top-level family instead of extending the CH1/CH2 one.
FREQOFF is deliberately narrowed to +/-1 MHz at the protocol layer: the
hardware can reach +/-25 MHz but nothing in this project will use it,
and a tighter bound catches typos.

GAIN stays a raw Q2.14 code end to end so GET round-trips losslessly.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: `test_bridge.py` 命令断言

**Files:**
- Modify: `scripts/test_bridge.py`
- Consumes: Task 7 的构造器命名（本脚本既有风格是直接写命令字符串，沿用）

- [ ] **Step 1: 加断言**

在既有「通道损伤(CHAN/NOISE/TAP)」段落之后（`cmd("SET CH2 CHAN OFF", "OK")` 之后）追加：

```python
# ---- 复数 I/Q 级(IQIMP;通道对级, 只在数字调制模式下作用于 DAC0/DAC1) ----
cmd("SET IQIMP OFF", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
cmd("SET IQIMP PHASE 3000", "OK")
cmd("SET IQIMP FREQOFF 2000", "OK")
cmd("SET IQIMP GAIN 20480", "OK")
cmd("SET IQIMP PHASEERR 2000", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 3000 FREQOFF 2000 GAIN 20480 PHASEERR 2000")
cmd("SET IQIMP ON", "OK")
cmd("GET IQIMP", "IQIMP ON PHASE 3000 FREQOFF 2000 GAIN 20480 PHASEERR 2000")
# 越界必须具名报错, 且不改状态
cmd("SET IQIMP PHASE 36001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP FREQOFF 1000001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP GAIN 40000", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP PHASEERR -18001", "ERR IQIMP OUT OF RANGE")
cmd("SET IQIMP BOGUS 1", "ERR BAD VALUE")
cmd("GET IQIMP", "IQIMP ON PHASE 3000 FREQOFF 2000 GAIN 20480 PHASEERR 2000")
cmd("SET IQIMP OFF", "OK")
cmd("SET IQIMP PHASE 0", "OK")
cmd("SET IQIMP FREQOFF 0", "OK")
cmd("SET IQIMP GAIN 16384", "OK")
cmd("SET IQIMP PHASEERR 0", "OK")
cmd("GET IQIMP", "IQIMP OFF PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0")
```

- [ ] **Step 2: 上板跑（需板卡 + 已下载最新 bit/ELF）**

```bash
python scripts/build_bitstream.py        # 正常增量, 不要 --force
python scripts/check_bitstream.py
bash scripts/program_board.sh
vivado2025.cmd app build awg_bridge --workspace sdk/ws2025
vivado2025.cmd deploy jtag --app awg_bridge --workspace sdk/ws2025
python scripts/test_bridge.py COM11
```
Expected: 原 150 条 + 新增 20 条全部 PASS，总数上升且 0 FAIL

- [ ] **Step 3: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test(bridge): IQIMP command and range assertions

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task 9: 三个 I/Q 示波器用例

**Files:**
- Modify: `scripts/scope_verify.py`
- Consumes: Task 7 的协议、既有 `setup_board`/`scope_acq`/`scope_wave`/`scope_capture_to_digital`/
  `align_known_sequence`/`generate_symbols`

**判据范式变更（本任务最重要）**：既有 56 项的核心判据是 `BER == 0`，而这批新特性的目的
**恰恰是打破链路**。因此新用例一律改为「**实测统计量 ≈ 模型**」，并且
**不拿数字域理论值做绝对比对**：凡经过 DAC 零阶保持 + 模拟重建滤波的量都有模拟域衰减
（实测 AWGN σ 只有数字域的 0.65 倍，见既有 `chan_noise` 用例）。增益/相位失配用例因此
以 **同一配置下 IQIMP OFF 的捕获为基准**做相对比较，并把实测/模型比值回报在 detail 里
持续观察。

**测量方法（以及为什么不用另一种）**：数字调制支路 RECT 模式下 DAC 是零阶保持，
**每个捕获样点本身就是星座点**（无 ISI、无匹配滤波），所以用「相对最近理想星座点的
残余角」就能直接读出旋转；不需要符号相位搜索，也不需要小数采样相位搜索。
**明确不用"单音镜像"法测 IRR** —— 数字调制通路送的是符号比特，连续相同比特给出的是直流
而不是单音，造不出干净的复单音（这也是规格 §7 的判决）。

- [ ] **Step 1: 加测量助手**

在 `scripts/scope_verify.py` 的 `waveform_sigma()` 之后（「信道损伤」段落内）追加：

```python
# ---------- 复数 I/Q 损伤(B 组) ----------
IQIMP_GAIN_Q14 = 20480          # Q2.14 的 1.25 -> I 轴比 Q 轴高 25%
IQIMP_GAIN_MODEL = 1.25
IQIMP_PHASEERR_X100 = 2000      # 20 度
IQIMP_PHASEERR_MODEL = -0.342   # rho 的变化 = -sin(20 deg)
IQIMP_PHASE_X100 = 3000         # 30 度旋转
IQIMP_PHASE_MODEL_DEG = 30.0
IQIMP_FREQOFF_HZ = 2000         # 2 kHz 星座旋转速率
IQIMP_SYMRATE = 100000
IQIMP_SYMBOLS = 200


def iqimp_residual_angles(i_samples, q_samples):
    """每个捕获样点相对最近理想 QPSK 点的残余角(度, 折到 ±45°)。

    返回 (残余角数组, 被保留样点的下标)。下标必须回传: 频偏用例要在**均匀时间轴**
    上做线性拟合, 丢掉离群点后仍要用原始下标换算出真实时刻。
    RECT 模式是零阶保持, 每个捕获样点本身就是星座点, 故不需要符号对齐 ——
    既有 align_known_sequence 的符号相位搜索是给匹配滤波输出用的, 这里用不上。
    """
    i = np.asarray(i_samples, dtype=np.float64)
    q = np.asarray(q_samples, dtype=np.float64)
    if i.ndim != 1 or q.ndim != 1 or i.size != q.size or i.size == 0:
        raise ValueError("I/Q captures must be equal-length non-empty 1-D arrays")
    mag = np.hypot(i, q)
    scale = float(np.mean(mag))
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("degenerate capture")
    keep = mag > 0.25 * scale
    if int(np.count_nonzero(keep)) < 32:
        raise ValueError("too few usable constellation samples")
    ang = np.degrees(np.arctan2(q[keep], i[keep]))
    nearest = 45.0 + 90.0 * np.round((ang - 45.0) / 90.0)
    return ang - nearest, np.flatnonzero(keep)


def iqimp_circular_mean_deg(angles_deg):
    """圆均值(度): 直接取算术平均会在 ±180° 边界处出错。"""
    a = np.radians(np.asarray(angles_deg, dtype=np.float64))
    return float(np.degrees(np.arctan2(float(np.mean(np.sin(a))),
                                       float(np.mean(np.cos(a))))))


def iqimp_covariance(i_samples, q_samples):
    """去均值后的 2x2 协方差 (cxx, cxy, cyy)。"""
    i = np.asarray(i_samples, dtype=np.float64)
    q = np.asarray(q_samples, dtype=np.float64)
    i = i - float(np.mean(i))
    q = q - float(np.mean(q))
    return float(np.mean(i * i)), float(np.mean(i * q)), float(np.mean(q * q))


def iqimp_eigen_ratio(cxx, cxy, cyy):
    """对称 2x2 协方差的两个特征值之比(>= 1)。纯增益失配时退化为 cxx/cyy。"""
    mid = 0.5 * (cxx + cyy)
    rad = float(np.hypot(0.5 * (cxx - cyy), cxy))
    hi, lo = mid + rad, mid - rad
    if lo <= 0.0:
        return float("inf")
    return hi / lo


def iqimp_correlation(cxx, cxy, cyy):
    """归一化互相关 rho = E[I*Q]/sqrt(E[I^2]E[Q^2]);相位失配把它推离 0。"""
    if cxx <= 0.0 or cyy <= 0.0:
        raise ValueError("degenerate covariance")
    return cxy / float(np.sqrt(cxx * cyy))


def iqimp_unwrap(values, period):
    """按给定周期展开序列(不依赖 numpy.unwrap 的 period 参数, 兼容老版本)。"""
    out = np.array(values, dtype=np.float64)
    offset = 0.0
    half = period / 2.0
    for k in range(1, out.size):
        delta = out[k] - out[k - 1]
        while delta > half:
            offset -= period
            delta -= period
        while delta < -half:
            offset += period
            delta += period
        out[k] = out[k] + offset
    return out


def iqimp_symbol_samples(i_capture, q_capture, symrate_hz, xinc, symbol_count):
    """按符号率从 RECT 捕获里取符号样点(零阶保持 -> 最近样点即符号值)。

    用时间->样点下标映射而不是固定步长, 避免示波器采样率与 DAC 时钟不成整数比时
    的累积漂移。
    """
    total = int(np.size(i_capture))
    if total == 0 or xinc <= 0.0 or symrate_hz <= 0.0:
        raise ValueError("bad capture or rates")
    times = np.arange(symbol_count, dtype=np.float64) / float(symrate_hz)
    idx = np.rint(times / xinc).astype(np.int64)
    idx = idx[(idx >= 0) & (idx < total)]
    if idx.size < 8:
        raise ValueError("capture too short for the symbol rate")
    return (np.asarray(i_capture, dtype=np.float64)[idx],
            np.asarray(q_capture, dtype=np.float64)[idx])
```

- [ ] **Step 2: 加三个用例**

在既有 `chan_noise`（AWGN 噪底）用例之后、`setup_board(["SET DIGMOD MODE OFF"])` 之前插入：

```python
    # ---- 信道损伤: I/Q 相位偏移(复数级) ----
    # 判据: 星座相对理想 QPSK 点的圆均值旋转角 == 设定值; 并反旋转后用既有
    # align_known_sequence 复核"旋转之外没有别的损伤"(BER == 0)。
    iqimp_phase_name = "信道 I/Q 相位偏移 30deg"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 0",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0",
                     "SET CH1 TAP CLEAR", "SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT",
                     "SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                     "SET DIGMOD SYMRATE %d" % IQIMP_SYMRATE, "SET DIGMOD SEED 127",
                     "SET IQIMP GAIN 16384", "SET IQIMP PHASEERR 0",
                     "SET IQIMP FREQOFF 0",
                     "SET IQIMP PHASE %d" % IQIMP_PHASE_X100,
                     "SET IQIMP ON", "SET CH1 CHAN COMMIT"])
        scope_acq(200e-6, 2.0)
        xinc, y_i = scope_wave("CHAN1")
        _, y_q = scope_wave("CHAN2")
        i_dig = scope_capture_to_digital(y_i)
        q_dig = scope_capture_to_digital(y_q)
        resid, _ = iqimp_residual_angles(i_dig, q_dig)
        rot = iqimp_circular_mean_deg(resid)
        # 反旋转 + 已知 PRBS7 序列对齐: 证明旋转之外没有别的损伤
        exp_i, exp_q = generate_symbols("QPSK", 127, seed=127)
        th = np.radians(rot)
        de_i = i_dig * np.cos(th) + q_dig * np.sin(th)
        de_q = -i_dig * np.sin(th) + q_dig * np.cos(th)
        sy_i, sy_q = iqimp_symbol_samples(de_i, de_q, IQIMP_SYMRATE, xinc,
                                          IQIMP_SYMBOLS)
        ber, offset, inverted = align_known_sequence(
            (sy_i, sy_q), exp_i, exp_q, "QPSK")
        ok = abs(rot - IQIMP_PHASE_MODEL_DEG) <= 3.0 and ber == 0.0
        report(iqimp_phase_name, ok,
               "rotation measured=%.2fdeg model=%.2fdeg derotated_BER=%s offset=%s"
               % (rot, IQIMP_PHASE_MODEL_DEG,
                  "0" if ber == 0.0 else ber, offset))
    except Exception as e:
        report(iqimp_phase_name, False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET IQIMP OFF", "SET IQIMP PHASE 0", "SET DIGMOD MODE OFF"])
        except Exception:
            pass

    # ---- 信道损伤: I/Q 增益与相位失配(复数级) ----
    # 一律以 IQIMP OFF 的同配置捕获为基准做相对判据(模拟链路频响在比值里抵消):
    #   GAIN     = diag(1.25, 1)  ->  sqrt(E[I^2]/E[Q^2]) 变为基准的 1.25 倍
    #   PHASEERR = 对称非正交矩阵 ->  rho = E[I*Q]/sqrt(E[I^2]E[Q^2]) 变化 -sin(20deg)
    iqimp_gain_name = "信道 I/Q 增益/相位失配"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 0",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0",
                     "SET CH1 TAP CLEAR", "SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT",
                     "SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                     "SET DIGMOD SYMRATE %d" % IQIMP_SYMRATE, "SET DIGMOD SEED 127",
                     "SET IQIMP GAIN 16384", "SET IQIMP PHASEERR 0",
                     "SET IQIMP PHASE 0", "SET IQIMP FREQOFF 0",
                     "SET IQIMP OFF", "SET CH1 CHAN COMMIT"])

        def capture_stats():
            scope_acq(200e-6, 2.0)
            _, cap_i = scope_wave("CHAN1")
            _, cap_q = scope_wave("CHAN2")
            cxx, cxy, cyy = iqimp_covariance(
                scope_capture_to_digital(cap_i), scope_capture_to_digital(cap_q))
            return (float(np.sqrt(cxx / cyy)),
                    iqimp_correlation(cxx, cxy, cyy),
                    iqimp_eigen_ratio(cxx, cxy, cyy))

        ratio_off, rho_off, eigen_off = capture_stats()
        setup_board(["SET IQIMP GAIN %d" % IQIMP_GAIN_Q14, "SET IQIMP ON",
                     "SET CH1 CHAN COMMIT"])
        ratio_on, _, eigen_on = capture_stats()
        setup_board(["SET IQIMP GAIN 16384",
                     "SET IQIMP PHASEERR %d" % IQIMP_PHASEERR_X100,
                     "SET CH1 CHAN COMMIT"])
        _, rho_err, eigen_err = capture_stats()
        gain_rel = (ratio_on / ratio_off) if ratio_off else 0.0
        rho_delta = rho_err - rho_off
        gain_ok = abs(gain_rel - IQIMP_GAIN_MODEL) <= 0.15
        phase_ok = abs(rho_delta - IQIMP_PHASEERR_MODEL) <= 0.10
        report(iqimp_gain_name, gain_ok and phase_ok,
               "gain ratio meas=%.3f model=%.2f (eigen %.2f->%.2f); "
               "rho delta meas=%.3f model=%.3f (eigen %.2f->%.2f)"
               % (gain_rel, IQIMP_GAIN_MODEL, eigen_off, eigen_on,
                  rho_delta, IQIMP_PHASEERR_MODEL, eigen_off, eigen_err))
    except Exception as e:
        report(iqimp_gain_name, False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET IQIMP OFF", "SET IQIMP GAIN 16384",
                         "SET IQIMP PHASEERR 0", "SET DIGMOD MODE OFF"])
        except Exception:
            pass

    # ---- 信道损伤: I/Q 频率偏移(复数级) ----
    # 残余角序列按 90° 周期展开后线性拟合, 斜率即星座旋转速率(deg/s);
    # 换算 /360 即频偏(Hz)。每样点旋转必须 < 45 度, 否则展开会产生整周跳变 ——
    # 2 kHz @ ~2us/样点 只有 1.44 度/样点, 余量充足。
    iqimp_foff_name = "信道 I/Q 频率偏移"
    try:
        setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 1000", "SET CH1 AMPL 0",
                     "SET CH1 BASE 0", "SET CH1 NOISE 0",
                     "SET CH1 TAP CLEAR", "SET CH1 CHAN OFF", "SET CH1 CHAN COMMIT",
                     "SET DIGMOD MODE OFF", "SET DIGMOD FILTER RECT",
                     "SET DIGMOD SYMRATE %d" % IQIMP_SYMRATE, "SET DIGMOD SEED 127",
                     "SET IQIMP GAIN 16384", "SET IQIMP PHASEERR 0",
                     "SET IQIMP PHASE 0",
                     "SET IQIMP FREQOFF %d" % IQIMP_FREQOFF_HZ,
                     "SET IQIMP ON", "SET CH1 CHAN COMMIT"])
        scope_acq(200e-6, 2.0)
        xinc, y_i = scope_wave("CHAN1")
        _, y_q = scope_wave("CHAN2")
        resid, idx = iqimp_residual_angles(
            scope_capture_to_digital(y_i), scope_capture_to_digital(y_q))
        unwrapped = iqimp_unwrap(resid, 90.0)
        t = idx.astype(np.float64) * xinc
        slope = float(np.polyfit(t, unwrapped, 1)[0])       # 度/秒
        measured_hz = slope / 360.0 if np.isfinite(slope) else float("nan")
        ok = (np.isfinite(measured_hz)
              and abs(measured_hz - IQIMP_FREQOFF_HZ) <= 0.25 * IQIMP_FREQOFF_HZ)
        report(iqimp_foff_name, ok,
               "measured=%.0fHz model=%dHz rotation=%.0fdeg over %.2fms"
               % (measured_hz, IQIMP_FREQOFF_HZ,
                  float(unwrapped[-1] - unwrapped[0]),
                  float(t[-1] - t[0]) * 1e3))
    except Exception as e:
        report(iqimp_foff_name, False, "failed: %r" % e)
    finally:
        try:
            setup_board(["SET IQIMP OFF", "SET IQIMP FREQOFF 0",
                         "SET DIGMOD MODE OFF"])
        except Exception:
            pass
```

- [ ] **Step 3: 上板跑**

```bash
python scripts/scope_verify.py COM11
```
Expected: `RESULT: N/N PASS`，N 为本轮之前的项数 + 3
（B 组单独实施时 56 → 59；若 A 组也已实施则 58 → 61）

**若某个判据不收敛**，先把实测/模型比值量清楚再调容差 ——
**不要**改成"看波形像不像"这种不可复现的判据。特别注意两组会真实存在的偏差来源：
① 旋转角有 ±0.18° 的 ROM 地址量化（1024 点）。② 增益/相位失配的第二阶统计量会受
模拟 I/Q 支路自身失配与量化噪声影响，故一律用 OFF 基准做差。

- [ ] **Step 4: 提交**

```bash
git add scripts/scope_verify.py
git commit -m "test(scope): complex I/Q impairment cases

The existing suite asserts BER == 0; these impairments exist to break
the link, so the new cases assert 'measured statistic matches model'
instead. None of them compares against a digital-domain absolute value,
because everything the DAC reconstructs is attenuated and shaped by the
analog path -- the same mechanism that put the AWGN sigma at 0.65x its
digital model. Gain and phase imbalance are therefore measured as a
difference against a same-configuration IQIMP OFF capture.

Rotation is read straight off the constellation: RECT mode is a
zero-order hold, so every captured sample is itself a constellation
point and no symbol or fractional-sample alignment search is needed.
The single-tone image method is deliberately not used -- the digital
modulator emits symbol bits, and repeated identical bits give DC, not a
tone.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: 哈希清单、文档、完整构建与上板验收

**Files:**
- Modify: `scripts/release_manifest.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 把 `tools/gen_sincos.py` 计入归一化输入哈希**

`scripts/release_manifest.py` 的 `normalized_input_hash()` 里，在
`root / "tools" / "write_bitstream_pre.tcl",` 之后追加一行：

```python
            root / "tools" / "gen_sincos.py",
```

（规格 §6 要求。注意 `tools/gen_sine.py` **当前并不在**这个列表里 —— 这是既有的一处
不一致；生成的 `.v` 由 `sorted(rtl.glob("*.v"))` 覆盖，所以 ROM 内容变化本来就会改变
哈希，加生成器只是把"生成器改了但产出没变"的语义也纳入。）

- [ ] **Step 2: 更新协议说明**

`README.md` 串口协议节加：

```
SET IQIMP ON | OFF
SET IQIMP PHASE <deg_x100 -36000..36000>
SET IQIMP FREQOFF <hz -1000000..1000000>
SET IQIMP GAIN <q14 -32768..32767; 16384 = 1.0>
SET IQIMP PHASEERR <deg_x100 -18000..18000>
GET IQIMP -> IQIMP ON PHASE 0 FREQOFF 0 GAIN 16384 PHASEERR 0
```

并说明：
- 该级是**通道对级**的复数 I/Q 损伤，只在数字调制模式下作用于 DAC0/DAC1（标准 AWG 路径不经过它）。
- **开关走 GPIO（立即生效），数值走抽头表 BRAM 字 8/9/10，需要
  `SET CH1 CHAN COMMIT`（或 CH2）才被 PL 锁存** —— 与抽头同一约定。
  未 COMMIT 前矩阵系数为 0，会使数字调制输出塌成 0，纯透传请用 `SET IQIMP OFF`。
- 静态三项（相位偏移 / I/Q 增益失配 / I/Q 相位失配）在 PS 侧合成为一个 2×2 矩阵，
  硬件只做矩阵乘；`GAIN` 是 Q2.14 原始码，`PHASE`/`PHASEERR` 单位 0.01°。
- 越界返回 `ERR IQIMP OUT OF RANGE`。

- [ ] **Step 3: 更新计数与测试速查，并加一条经验条目**

`CLAUDE.md` 测试速查节：示波器闭环项数 +3（B 组单独实施 56 → 59；A 组也已实施则
58 → 61）；上位机测试数按实际增量更新；新增 TB `sincos_rom_tb`(9 项) 与
`iq_impair_tb`(5 项)，`chan_cfg_tb` 20 → 28 项、`awg_top_tb` 106 → 114 项（按实际更新）。

`CLAUDE.md` 的「信道损伤经验」节追加：

```
- **复数 I/Q 级 B 组(2026-09-10)**: `iq_impair` 放在数字调制 I/Q 对与最终选择之间,
  单实例, 标准 AWG 路径零影响。静态三项(相位偏移/I-Q 增益失配/I-Q 相位失配)由主机
  **合成一个 2×2 矩阵**再下发 —— 硬件只做矩阵乘, 不知道该矩阵由哪几项组成, 这是本轮
  最大的简化。系数用 **Q2.14 而不是 Q1.15**(增益失配要求 α=1+ε>1, Q1.15 表示不了);
  旋转级用 `θ[31:22]` 查 1024 点 Q2.14 sin/cos ROM(`ram_style="block"`, LUTRAM 会吃掉
  约 16K LUT)。`enable=0` 是**组合**旁路, 故 `dac_sel0/1` 与改造前逐位等价。
- **I/Q 损伤怎么量(2026-09-10)**: RECT 模式下 DAC 是零阶保持, **每个捕获样点本身就是
  星座点**, 因此旋转角可以直接用"相对最近理想星座点的残余角"的圆均值读出, 不需要符号
  对齐/小数采样相位搜索。失配用二阶统计量: 增益 = `sqrt(E[I²]/E[Q²])` 的**相对基准
  变化**, 相位 = `rho = E[I·Q]/sqrt(E[I²]E[Q²])` 的变化 `-sin(ε)`。**不用单音镜像法**:
  数字调制送的是符号比特, 连续相同比特给直流而非单音。判据一律"实测统计量 ≈ 模型"
  且以 OFF 捕获为基准做差 —— 模拟链路会整体衰减/整形(实测 AWGN σ 只有数字域 0.65 倍)。
- **延时只加在数字调制支路(2026-09-10)**: `iq_impair` 使能后给数字调制支路多 5 拍,
  标准 AWG 支路不加。因两条支路互斥且都是自由运行流, 恒定相移无害 —— 与
  `chan_impair` 的既有论证相同。若日后引入需要两条支路严格对齐的功能, 需重新评估。
```

- [ ] **Step 4: 完整构建与下载**

```bash
python scripts/build_bitstream.py        # 正常增量, 不要 --force
python scripts/check_bitstream.py
bash scripts/program_board.sh
vivado2025.cmd app build awg_bridge --workspace sdk/ws2025
vivado2025.cmd deploy jtag --app awg_bridge --workspace sdk/ws2025
```

- [ ] **Step 5: 验收（对应规格验收标准 1/2/3/4/5/6）**

```bash
python scripts/test_bridge.py COM11            # 新增 IQIMP 断言全过
python scripts/scope_verify.py COM11           # 本轮的 3 项全过且既有项不回归
python scripts/scope_verify_user.py COM11      # 4/4 不回归
host/.venv/Scripts/python -m pytest host/tests/
```

逐条核对：
1. `SET IQIMP OFF`（且 `CHAN OFF`）时 DAC 输出与改动前逐位一致 —— 既有 56 项正是这个证据，
   加上 `awg_top_tb` 的 `dac_sel0 === dm_i` 断言。
2. 新增 TB（`sincos_rom_tb`/`iq_impair_tb`/`chan_cfg_tb`/`awg_top_tb`）全过；host/tests 全绿。
3. 相位偏移的实测旋转角、增益/相位失配的二阶统计量、频偏的旋转速率在容差内。
4. `GET IQIMP` 回读与下发一致；数值参数在 COMMIT 后生效（`test_bridge.py` 覆盖回读，
   `chan_cfg_tb` 覆盖 COMMIT 锁存）。
5. 既有项与 `scope_verify_user.py` 4/4 不回归。
6. 时序仍收敛（WNS > 0），且**无 DSP48 级联**引入的长路径：

```bash
grep -a "WNS" awg/awg.runs/impl_1/host_bd_wrapper_timing_summary_routed.rpt | head -5
grep -a "RAMB36" awg/awg.runs/impl_1/host_bd_wrapper_utilization_placed.rpt | head -3
grep -a "DSP48E1" awg/awg.runs/impl_1/host_bd_wrapper_utilization_placed.rpt | head -3
```
Expected: WNS > 0；RAMB36 比改动前多 1（新增 `sincos_rom` 1 个 RAMB36）；
DSP48E1 比改动前多 8（8 个新乘法寄存器对）；
并在 `timing_summary_routed.rpt` 中确认最差路径的 Slack 报错源**不是** `DSP48` 的
`PCOUT/PCIN` 网络（若是，说明加法树被串回了 DSP 链，必须回去把中间量加拍）。

- [ ] **Step 6: 提交**

```bash
git add scripts/release_manifest.py README.md CLAUDE.md build_manifest.json
git commit -m "docs: complex I/Q impairment protocol, counts and measurement notes

Records the two facts this round produced. First, the static three
impairments compose into a single matrix on the reference side, so the
RTL never learns which one the user chose -- that is what makes one
stage cover all three. Second, RECT mode's zero-order hold means every
captured sample is itself a constellation point, so the rotation angle
is readable directly and the single-tone image method is unusable
because the modulator emits symbol bits rather than a clean tone.

Also registers tools/gen_sincos.py in the release input hash.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 自审记录

**规格覆盖**（Round B 部分，逐条对应到任务）：

| 规格条目 | 任务 |
|---|---|
| §1「B 组（复数，I/Q 对级）」定点格式与数值模型 | Task 1（模型）、Task 3（RTL）、Task 6（固件合成） |
| §2.1 `iq_impair.v` | Task 3 |
| §2.2 `sincos_rom.v` + `tools/gen_sincos.py` | Task 2 |
| §2.7 `awg_top.v` 最终选择改造 | Task 5 |
| §3 字映射 8-11 中的 8-10 | Task 4（锁存）、Task 6（写入） |
| §3 使能位 CH1 字 bit11 | Task 5（解码/接线）、Task 6（置位） |
| §4 `IQIMP` 协议家族 | Task 6（固件）、Task 7（上位机）、Task 8（板上断言） |
| §5 上位机矩阵合成纯函数 + 单测 | Task 1（`iq_impair_matrix` + `test_channel.py`） |
| §7 三个 I/Q 验证行（相位偏移/失配/频偏） | Task 9 |
| §7 RTL TB `iq_impair_tb` | Task 3 |
| §6 构建、`release_manifest.py`、部署 | Task 10（+ Task 8/10 的构建下载步骤） |
| 验收标准 1/2/3/4/5/6 | Task 10 Step 5 逐条核对 |
| 「与既有设计的偏离」（推翻"不做复数 I/Q 信道"） | 已在 Global Constraints 与 Task 5 注释中写明 |

规格 §2.3/2.4/2.5/2.6 属 A 组（`tone_gen`/`impulse_gen`/`chan_impair` 扩展），
不在本计划；§1 「A 组」与 §3 字 12-15、§4 的 `TONE`/`IMPULSE` 命令同理。

**占位符扫描**：唯一一处"执行时才能算出"的内容是 Task 3 Step 2 TB 里的
`g_i[0..31]`/`g_q[0..31]`，Step 4 给了**确切的生成命令**（`apply_iq_impair` + `iq_coef_words`），
并说明了要替换哪一行；这不是未定项。Task 2 生成的两个文件（`src/rtl/sincos_rom.v`、
`sdk/src/sincos_table.h`）是**生成物**，由 Step 1 的完整生成器源码与 Step 2 的确切命令产出，
同样不是占位符。

**类型一致性核对**（本计划已逐项对齐）：
- `apply_iq_impair(i_samples, q_samples, a, b, c, d, fword=0)` 在 Task 1 定义；
  Task 3 的 TB golden 生成命令、Task 9 的模型比对都用这个参数顺序。
- `iq_impair` 端口名（`clk, rst_n, enable, i_in, q_in, coef_ab, coef_cd, fword, i_out, q_out`）
  在 Task 3 定义，Task 5 的实例化逐字一致；`sincos_rom` 的
  `clk, addr, sin_out, cos_out` 在 Task 2 定义，Task 3 实例化一致。
- `chan_cfg` 新输出 `coef_ab, coef_cd, fword` 在 Task 4 定义，Task 5 接线一致。
- `reg_bridge` 新输出 `ch1_iq_en` 在 Task 5 定义，同任务内完成 `awg_top` 接线。
- 固件命令文本（`SET IQIMP ON|OFF|PHASE|FREQOFF|GAIN|PHASEERR`、`GET IQIMP`）在
  Task 6 实现、Task 7 生成、Task 8 断言、Task 9 直接下发，四处字符串逐字相同。
- `GET IQIMP` 的 token 布局（10 个）在 Task 6 的 `sprintf`、Task 7 的
  `parse_iqimp_status`、Task 8 的断言、README 里一致。
- 字 8/9/10 的打包方向在 Task 4（原样透传 32 位）、Task 6（`(b<<16)|a`、`(d<<16)|c`）、
  Task 1（`iq_coef_words`）、Task 3（`coef_ab[15:0]=A`）四处一致。

**自审中已就地修掉的问题**：
1. `iq_impair` 的旁路最初按 `chan_impair` 写成"末级寄存 + enable 选择"，
   这会让 `awg_top` 的 `dac_sel0` 在旁路时比 `rrc_i8` 晚一拍，直接打破既有
   `awg_top_tb` 的 `dac_sel0 === rrc_i8` 断言。改成**组合 mux**后旁路逐位等价，
   既有断言不变（Task 3 的 RTL 与 Task 5 的 TB 都已按此写）。
2. `chan_cfg_tb` 原有的 `wait_cycles(20)` 是给 4 个字（12 拍）算的；本计划把每通道
   排空扩到 7 个字（21 拍），两处等待必须放宽到 40/80 拍，否则会偶发失败。
3. `sincos_rom` 的表值定为 **Q2.14（幅度 16384）** 而不是 Q1.15（32767），
   这样静态级与旋转级共用同一个 `>>14` 与同一个 `shift_sat14()`，不需要尺度换算，
   也消除了"ROM 用 Q1.15、系数用 Q2.14"这种容易写错的混用。
4. `iqimp_residual_angles()` 必须**回传保留样点的下标**：频偏用例要在均匀时间轴上
   做线性拟合，若只回传被过滤后的数组，时间轴就与样点错位。
5. 频率偏移用例的每样点旋转必须 < 45°（余量给 90° 周期的展开）。2 kHz @ 约 2 µs/样点
   只有 1.44°/样点，已把这一条写进用例注释，避免后人调大 Δf 后出现整周跳变误判。
6. `sincos_rom` 的读口**不能带复位**（否则综合器不把它放进 BRAM），已在生成器注释与
   验收步骤里显式写出；`ram_style="block"` 同理（LUTRAM 会吃掉约 16K LUT）。
7. Task 1 的 `test_sincos_rom_verilog_matches_python_table` 在 ROM 尚未生成时 SKIP，
   避免 Task 1 与 Task 2 的执行顺序互相阻塞。
8. 两个 TB 的延时断言用了**不同的索引约定**，会看错就白 debug 一轮：
   `iq_impair_tb` 里 `xv[j]` 是"第 j 拍驱动的输入"、`ov[j]` 是"第 j 拍后采到的输出"，
   故索引差是 **4**；`awg_top_tb` 里 `dma[k]`/`ima[k]` 是**同拍采到的两个数组**，
   故索引差直接等于流水级数 **5**。两处都已在代码注释里写明，并都要求唯一匹配
   （`matches == 1`）而不只是"存在某个延时可对上"。

**已知需在执行时按既有代码风格落地的地方**（语义是硬要求，表达形式可调）：
1. Task 6 的插入位置按 `sdk/src/main.c` 当前行号给出（GET 1919 之前、SET 2488 之前）；
   若文件已被 A 组改动，按"插在 `CH1/CH2 CHAN` 分支之前"这个语义定位。
2. Task 9 的常量（`IQIMP_GAIN_Q14=20480` 等）与容差（±3°/±0.15/±0.10/±25%）是按
   "8 位链路 + 模拟重建"的合理余量给的；上板若某项贴着容差边界，**先量清实测/模型
   比值再决定是调容差还是查 RTL**，不要直接放宽到失去判别力。
3. Task 5 的 `tap_b_rdata` 由常量改为按地址的伪 BRAM 后，`awg_top_tb` 里
   `chan_cfg` 会在 commit 后锁存到字 8/9/10 的内容；这**不影响**既有断言，
   但若实现时发现其它地方依赖 `tap_b_rdata` 恒 0，需要一并核对。






