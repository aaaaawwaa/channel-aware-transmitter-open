# 数字调制 RRC 成形 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有 RECT 数字调制契约的前提下，为内部 PRBS7 QPSK 增加 4 SPS、α=0.35、8-symbol span 的定点 RRC 发射成形，并完成协议、GUI、RTL、综合和板级验收。

**Architecture:** 保留 `iq_digital_mod.v` 作为 bit-exact RECT 路径，新增独立的 `rrc_digital_mod.v` 和 `rrc_filter.v`。RRC 使用 GPIO11 bit20 选择、GPIO12 现有 phase step 派生 4×采样节拍，在 `awg_top.v` 最后一级将 RECT 或 RRC 结果送入现有 DAC。PS、协议、控制器和 GUI 采用 FILTER 状态的 ACK → GET 回读确认；RRC 只允许 QPSK，默认和非法硬件组合均安全回退。

**Tech Stack:** Verilog-2001/SystemVerilog testbench、Vivado 2018.3 XSIM、Zynq AXI GPIO、裸机 C、Python/NumPy、PySide6/pytest、PyVISA DSOX1102A。

---

## 文件结构

| 路径 | 责任 |
|---|---|
| `docs/superpowers/specs/2026-08-28-digital-modulation-rrc-design.md` | 已冻结 RRC 参数、定点、协议、GUI 和验收契约 |
| `host/siggen/rrc.py` | 浮点系数生成、Q1.15 量化、4 SPS 定点发射、匹配滤波和恢复参考 |
| `host/tests/test_rrc.py` | RRC 系数、定点边界、插值顺序、匹配滤波 BER 测试 |
| `tools/generate_rrc_vectors.py` | 生成 `rrc_coeffs.vh` 和 RTL 逐点参考向量 |
| `src/rtl/rrc_coeffs.vh` | 33 个 signed Q1.15 系数常量，仅由生成器更新 |
| `src/rtl/rrc_filter.v` | 4 相 polyphase 9/8/8/8 tap signed 16-bit FIR |
| `src/rtl/rrc_digital_mod.v` | 4×SYMRATE 相位调度、PRBS7/QPSK、RRC filter 编排 |
| `src/tb/rrc_filter_tb.v` | 单符号冲激、每相 tap、群延迟、舍入和饱和验收 |
| `src/tb/rrc_digital_mod_tb.v` | QPSK PRBS、4 SPS、速率、复位/启停、参考向量验收 |
| `src/tb/rrc_expected.mem` | 生成器输出的 signed 16-bit I/Q 逐样本 GOLD |
| `src/rtl/reg_bridge.v` | GPIO11 bit20 FILTER 两级同步输出 |
| `src/rtl/awg_top.v` | RRC/RECT mux、16→8 定标、DAC 接口保持 |
| `src/tb/reg_bridge_tb.v`、`src/tb/awg_top_tb.v` | FILTER 位图、低 20 位兼容、mux、OFF 和 DAC 定标 |
| `sdk/src/main.c` | FILTER 状态、命令、GPIO11 bit20 打包和 GET 状态 |
| `host/siggen/protocol.py` | FILTER 常量、命令生成、新旧 GET 状态解析 |
| `host/siggen/controller.py` | `DigitalModState.filter` 和 ACK→GET 设置流程 |
| `host/siggen/preview.py` | RECT/RRC 理论时域、星座和速率传递 |
| `host/siggen/ui/widgets.py`、`ui/main_window.py` | FILTER 控件、回读显示和控制器连接 |
| `host/tests/test_protocol.py`、`test_controller.py`、`test_preview.py`、`test_widgets.py` | FILTER 协议、回读、预览和离屏 GUI 回归 |
| `scripts/test_bridge.py` | FILTER 命令和完整状态板级协议回归 |
| `scripts/scope_verify.py`、`host/tests/test_scope_verify_rrc.py` | RRC 中心采样、匹配滤波、频谱和 OFF 恢复 |
| `scripts/build_bitstream.py`、`scripts/check_bitstream.py` | 将 `src/rtl/*.vh` 系数 include 纳入 bitstream 新鲜度哈希和下载前检查 |
| `README.md`、`CLAUDE.md`、`AGENTS.md` | 使用顺序、定点边界和板级验收说明 |

`scripts/create_bd.tcl` 不修改：FILTER 使用现有 GPIO11 bit20，符号率使用已连接的 GPIO12。只有综合证明确有新 IP/时钟依赖时才另开设计变更，不在本计划中隐式改变 BD。

本计划不创建外部数据源、AXI-Stream、BRAM、RRC 接收硬件、EVM、BER 硬件测量或 QAM RRC。除用户明确要求外不创建 git commit。

## 设计常量和不变量

所有实现必须与已创建的 `docs/superpowers/specs/2026-08-28-digital-modulation-rrc-design.md` 一致：

```text
QPSK、PRBS7 seed=0x7f、SPS=4、alpha=0.35、span=8 symbols、taps=33
coeff = signed Q1.15，离散能量归一化，ties-away-from-zero
x16 = sign_extend(mapper_i8) << 8，QPSK ±127 -> ±32512
MAC = signed 40-bit
MAC >> 15：负数先取绝对值再 round-half-away-from-zero
FIR 输出 saturate 到 [-32768,32767]
DAC 定标：round-half-away-from-zero(rrc_i16 / 2^8)，再 saturate 到 [-127,127]
单 RRC 延迟 16 samples；匹配 RRC 总延迟 32 samples
GPIO11 bit20：FILTER 0=RECT、1=RRC；默认 RECT
GET 顺序：MODE SOURCE FILTER SYMRATE STATE
```

### Task 1: 先锁定 Python 定点黄金模型

**Files:**
- Create: `host/siggen/rrc.py`
- Create: `host/tests/test_rrc.py`
- Modify: `host/tests/test_digital_mod.py`

- [ ] **Step 1: 写入会失败的系数和定点测试**

在 `host/tests/test_rrc.py` 先写入以下测试；此时 `siggen.rrc` 不存在，导入必须失败而不是默默测试错误实现：

```python
import numpy as np
import pytest

from siggen.digital_mod import generate_symbols
from siggen.rrc import (
    RRC_ALPHA,
    RRC_SPS,
    RRC_SPAN,
    RRC_TAPS,
    dac_scale,
    matched_filter_symbols,
    rrc_coefficients,
    rrc_filter_symbols,
    round_shift_signed,
)


def test_rrc_constants_and_coefficients_are_symmetric():
    coeff = rrc_coefficients()
    assert (RRC_ALPHA, RRC_SPS, RRC_SPAN, RRC_TAPS) == (0.35, 4, 8, 33)
    assert coeff.dtype == np.int16
    assert coeff.shape == (33,)
    assert np.array_equal(coeff, coeff[::-1])
    assert coeff[16] == 17953
    assert int(np.dot(coeff.astype(np.int64), coeff.astype(np.int64))) == 1_073_666_113
    assert int(np.max(np.abs(coeff))) == 17953
    assert coeff.tolist() == [
        33, 218, 157, -156, -417, -242, 420, 1071, 936,
        -362, -2215, -3091, -1388, 3390, 9959, 15683, 17953,
        15683, 9959, 3390, -1388, -3091, -2215, -362, 936,
        1071, 420, -242, -417, -156, 157, 218, 33,
    ]


def test_round_shift_signed_uses_ties_away_from_zero():
    assert round_shift_signed(128, 8) == 1
    assert round_shift_signed(127, 8) == 0
    assert round_shift_signed(-128, 8) == -1
    assert round_shift_signed(-127, 8) == 0
    assert round_shift_signed(3 * 32768 + 16384, 15) == 4
    assert round_shift_signed(-(3 * 32768 + 16384), 15) == -4


def test_rrc_filter_has_four_samples_per_symbol_and_32_sample_tail():
    i, q = generate_symbols("QPSK", 3)
    fi, fq = rrc_filter_symbols(i, q)
    assert len(fi) == len(fq) == 3 * 4 + 32
    assert fi.dtype == fq.dtype == np.int16
    assert np.any(fi[:12] != 0)
    assert np.any(fi[-32:] != 0)


def test_rrc_input_scale_and_dac_scale_are_symmetric():
    i, q = generate_symbols("QPSK", 1)
    fi, fq = rrc_filter_symbols(i, q)
    assert max(abs(int(fi.min())), abs(int(fi.max()))) <= 32767
    assert dac_scale(np.array([32512, -32512], dtype=np.int16)).tolist() == [127, -127]
    assert dac_scale(np.array([32767, -32768], dtype=np.int16)).tolist() == [127, -127]


def test_qpsk_rrc_match_filter_recovers_prbs_signs_after_total_delay():
    i, q = generate_symbols("QPSK", 127)
    tx_i, tx_q = rrc_filter_symbols(i, q)
    recovered_i, recovered_q = matched_filter_symbols((tx_i, tx_q))
    valid = slice(8, -8)
    assert np.array_equal(np.sign(recovered_i[valid]), np.sign(i[valid]))
    assert np.array_equal(np.sign(recovered_q[valid]), np.sign(q[valid]))


def test_rrc_rejects_mismatched_axes_and_invalid_match_lengths():
    with pytest.raises(ValueError):
        rrc_filter_symbols(np.array([1], dtype=np.int16), np.array([], dtype=np.int16))
    samples = (np.zeros(33, dtype=np.int16), np.zeros(33, dtype=np.int16))
    with pytest.raises(ValueError):
        matched_filter_symbols(samples)
    with pytest.raises(ValueError):
        matched_filter_symbols((np.zeros(32, dtype=np.int16),
                                np.zeros(32, dtype=np.int16)))
```

在 `host/tests/test_digital_mod.py` 添加 RECT 不变性测试：

```python

def test_rect_reference_remains_bit_exact_after_rrc_model_addition():
    i, q = generate_samples("QPSK", 7, sym_ticks=11)
    assert i.dtype == q.dtype == np.int16
    assert np.array_equal(i, np.repeat(generate_symbols("QPSK", 7)[0], 11))
    assert np.array_equal(q, np.repeat(generate_symbols("QPSK", 7)[1], 11))
```

- [ ] **Step 2: 运行红灯测试并确认原因正确**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_rrc.py host/tests/test_digital_mod.py -q
```

Expected: `test_rrc.py` 因 `siggen.rrc` 不存在而收集失败；不是 NumPy、Qt 或路径错误。确认后再写生产模块。

- [ ] **Step 3: 实现最小但完整的 `rrc.py` 黄金模型**

创建 `host/siggen/rrc.py`，固定以下公开接口和整数规则：

```python
import math
import numpy as np

RRC_ALPHA = 0.35
RRC_SPS = 4
RRC_SPAN = 8
RRC_TAPS = RRC_SPAN * RRC_SPS + 1
RRC_COEFF_SCALE = 32767
RRC_INPUT_SHIFT = 8
RRC_OUTPUT_SHIFT = 8
RRC_GROUP_DELAY = (RRC_TAPS - 1) // 2
RRC_MATCH_DELAY = 2 * RRC_GROUP_DELAY


def round_half_away_from_zero(value):
    value = float(value)
    return int(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))


def round_shift_signed(value, shift):
    value = int(value)
    if shift < 0:
        raise ValueError("shift must be non-negative")
    if value >= 0:
        return (value + (1 << (shift - 1))) >> shift if shift else value
    magnitude = -value
    rounded = (magnitude + (1 << (shift - 1))) >> shift if shift else magnitude
    return -rounded


def _saturate(value, low, high):
    return min(high, max(low, int(value)))


def _rrc_value(x, alpha):
    if abs(x) < 1e-15:
        return 1.0 + alpha * (4.0 / math.pi - 1.0)
    singular = 1.0 / (4.0 * alpha)
    if abs(abs(x) - singular) < 1e-12:
        return alpha / math.sqrt(2.0) * (
            (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * alpha))
            + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * alpha))
        )
    numerator = math.sin(math.pi * x * (1.0 - alpha))
    numerator += 4.0 * alpha * x * math.cos(math.pi * x * (1.0 + alpha))
    denominator = math.pi * x * (1.0 - (4.0 * alpha * x) ** 2)
    return numerator / denominator


def rrc_coefficients(alpha=RRC_ALPHA, sps=RRC_SPS, span=RRC_SPAN):
    if alpha != RRC_ALPHA or sps != RRC_SPS or span != RRC_SPAN:
        raise ValueError("only the frozen RRC configuration is supported")
    values = np.array(
        [_rrc_value((index - (RRC_TAPS - 1) / 2) / sps, alpha)
         for index in range(RRC_TAPS)],
        dtype=np.float64,
    )
    values /= math.sqrt(float(np.dot(values, values)))
    quantized = np.array(
        [round_half_away_from_zero(item * RRC_COEFF_SCALE) for item in values],
        dtype=np.int64,
    )
    quantized = np.clip(quantized, -32768, 32767).astype(np.int16)
    quantized = ((quantized.astype(np.int32) + quantized[::-1].astype(np.int32)) // 2).astype(np.int16)
    return quantized


def _fir_sample(samples, coeffs, index):
    acc = 0
    for tap, coeff in enumerate(coeffs):
        source_index = index - tap
        if 0 <= source_index < len(samples):
            acc += int(samples[source_index]) * int(coeff)
    return _saturate(round_shift_signed(acc, 15), -32768, 32767)


def _filter_sample_rate(samples, coeffs):
    return np.asarray(
        [_fir_sample(samples, coeffs, index) for index in range(len(samples) + len(coeffs) - 1)],
        dtype=np.int16,
    )


def rrc_filter_symbols(i_symbols, q_symbols, coeffs=None):
    i_symbols = np.asarray(i_symbols, dtype=np.int16)
    q_symbols = np.asarray(q_symbols, dtype=np.int16)
    if i_symbols.ndim != 1 or q_symbols.ndim != 1 or len(i_symbols) != len(q_symbols):
        raise ValueError("I/Q symbol arrays must be one-dimensional and equal length")
    coeffs = rrc_coefficients() if coeffs is None else np.asarray(coeffs, dtype=np.int16)
    if coeffs.shape != (RRC_TAPS,):
        raise ValueError("coeffs must contain 33 taps")
    i_up = np.zeros(len(i_symbols) * RRC_SPS, dtype=np.int16)
    q_up = np.zeros(len(q_symbols) * RRC_SPS, dtype=np.int16)
    i_up[::RRC_SPS] = i_symbols.astype(np.int32) * (1 << RRC_INPUT_SHIFT)
    q_up[::RRC_SPS] = q_symbols.astype(np.int32) * (1 << RRC_INPUT_SHIFT)
    return _filter_sample_rate(i_up, coeffs), _filter_sample_rate(q_up, coeffs)


def matched_filter_symbols(samples, coeffs=None, delay_samples=RRC_MATCH_DELAY):
    i_samples, q_samples = samples
    i_samples = np.asarray(i_samples, dtype=np.int16)
    q_samples = np.asarray(q_samples, dtype=np.int16)
    if i_samples.ndim != 1 or q_samples.ndim != 1 or len(i_samples) != len(q_samples):
        raise ValueError("I/Q sample arrays must be equal-length one-dimensional arrays")
    payload = len(i_samples) - (RRC_TAPS - 1)
    if payload < 0 or payload % RRC_SPS:
        raise ValueError("I/Q samples must have length N*4+32")
    if delay_samples < 0:
        raise ValueError("delay_samples must be non-negative")
    coeffs = rrc_coefficients() if coeffs is None else np.asarray(coeffs, dtype=np.int16)
    if coeffs.shape != (RRC_TAPS,):
        raise ValueError("coeffs must contain 33 taps")
    i_matched = _filter_sample_rate(i_samples, coeffs)
    q_matched = _filter_sample_rate(q_samples, coeffs)
    symbol_count = payload // RRC_SPS
    indices = delay_samples + RRC_SPS * np.arange(symbol_count)
    if len(indices) and indices[-1] >= len(i_matched):
        raise ValueError("delay_samples exceeds matched-filter output")
    return i_matched[indices], q_matched[indices]


def dac_scale(samples):
    values = np.asarray(samples, dtype=np.int16)
    result = [
        _saturate(round_shift_signed(int(value), RRC_OUTPUT_SHIFT), -127, 127)
        for value in values.flat
    ]
    return np.asarray(result, dtype=np.int8).reshape(values.shape)
```

`rrc_coefficients()` 的对称化必须只消除浮点末位差异；对称性测试、中心系数、系数平方和和生成文件 hash 都要由测试锁定。`rrc_filter_symbols()` 使用稀疏 4 SPS 输入并保留 32 个尾部采样；`matched_filter_symbols()` 在总延迟 32 samples 处抽样，调用方再去掉首尾 8 个符号瞬态。

- [ ] **Step 4: 运行 Python 测试，确认绿灯并检查数值边界**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_rrc.py host/tests/test_digital_mod.py -q
```

Expected: 新测试和全部既有数字模型测试通过；失败时优先检查负数舍入、输入左移和匹配滤波抽样索引，不修改测试来适应实现。

### Task 2: 生成系数 include 和 RTL GOLD 向量

**Files:**
- Create: `tools/generate_rrc_vectors.py`
- Create: `src/rtl/rrc_coeffs.vh`
- Create: `src/tb/rrc_expected.mem`
- Test: `host/tests/test_rrc.py`

- [ ] **Step 1: 写入生成文件格式测试**

在 `host/tests/test_rrc.py` 添加：

```python
from pathlib import Path


def test_generated_rrc_files_have_33_coefficients_and_paired_samples():
    coeff_file = Path("src/rtl/rrc_coeffs.vh")
    vector_file = Path("src/tb/rrc_expected.mem")
    assert coeff_file.exists()
    assert vector_file.exists()
    assert coeff_file.read_text(encoding="ascii").count("RRC_COEFF_") == 33
    lines = [line for line in vector_file.read_text(encoding="ascii").splitlines() if line]
    assert len(lines) == 16 * 4 + 32
    assert all(len(line) == 8 for line in lines)
```

- [ ] **Step 2: 运行测试确认生成文件不存在**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_rrc.py::test_generated_rrc_files_have_33_coefficients_and_paired_samples -q
```

Expected: 因生成文件不存在失败。

- [ ] **Step 3: 实现确定性生成器**

`tools/generate_rrc_vectors.py` 必须从工程根目录运行，使用 `siggen.rrc` 和 `siggen.digital_mod.generate_symbols()`，不重复实现 RRC 公式。输出格式固定为：

`rrc_coeffs.vh` 每行使用以下确定格式，其中 `<nn>` 是 `00` 到 `32` 的两位十进制编号，`<hhhh>` 是对应 signed 16-bit 值的四位小写十六进制补码：

```text
localparam signed [15:0] RRC_COEFF_<nn> = 16'sh<hhhh>;
```

共写入 33 行，从 `RRC_COEFF_00` 连续到 `RRC_COEFF_32`。`rrc_expected.mem` 每行 8 个十六进制字符，前 4 位为 I[15:0]，后 4 位为 Q[15:0]，按 sample tick 顺序排列。

脚本核心必须等价于：

```python
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from siggen.digital_mod import generate_symbols
from siggen.rrc import rrc_coefficients, rrc_filter_symbols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=16)
    parser.add_argument("--coeff", type=Path, default=ROOT / "src/rtl/rrc_coeffs.vh")
    parser.add_argument("--vectors", type=Path, default=ROOT / "src/tb/rrc_expected.mem")
    args = parser.parse_args()
    coeff = rrc_coefficients()
    i_sym, q_sym = generate_symbols("QPSK", args.symbols)
    i_out, q_out = rrc_filter_symbols(i_sym, q_sym, coeff)
    args.coeff.write_text(
        "`ifndef RRC_COEFFS_VH\n`define RRC_COEFFS_VH\n" +
        "".join(f"localparam signed [15:0] RRC_COEFF_{n:02d} = 16'sh{int(v) & 0xffff:04x};\n"
                for n, v in enumerate(coeff)) +
        "`endif\n",
        encoding="ascii",
    )
    args.vectors.write_text(
        "\n".join(f"{int(i) & 0xffff:04x}{int(q) & 0xffff:04x}" for i, q in zip(i_out, q_out)) + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 生成并验收文件**

Run:

```bash
PYTHONPATH=host python tools/generate_rrc_vectors.py
PYTHONPATH=host python -m pytest host/tests/test_rrc.py::test_generated_rrc_files_have_33_coefficients_and_paired_samples -q
git diff --check
```

Expected: 两个文件生成、格式测试通过、没有空白错误。系数文件只能由生成器修改，禁止手工调整某个 tap 使仿真“通过”。

### Task 3: 实现独立 4 相 RRC filter

**Files:**
- Create: `src/tb/rrc_filter_tb.v`
- Create: `src/rtl/rrc_filter.v`
- Test: `src/rtl/rrc_coeffs.vh`

- [ ] **Step 1: 写入 filter testbench 使其先失败**

`src/tb/rrc_filter_tb.v` 使用 signed 16-bit 单符号冲激，phase 0、1、2、3 各驱动足够 sample tick，并检查输出等于 Python 生成的 33-tap 单符号序列。DUT 接口固定为：

```verilog
module rrc_filter (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire sample_tick,
    input wire [1:0] phase,
    input wire load_symbol,
    input wire signed [15:0] new_i,
    input wire signed [15:0] new_q,
    output reg signed [15:0] i_sample,
    output reg signed [15:0] q_sample,
    output reg sample_valid
);
```

testbench 必须覆盖：

```text
- 第一采样为 coeff[0] × input，中心第 16 采样为 coeff[16] × input。
- phase tap 数为 9、8、8、8。
- load_symbol 只在 phase 0 发生，历史向后移动。
- 正负 MAC 舍入边界：±16384、±32768、±49152 在右移 15 时对称。
- 超出 16-bit 的累加结果饱和而不是回绕。
- disable 和 reset 清空历史、输出和 sample_valid。
```

testbench 先实例化尚不存在的 `rrc_filter`，让 `xelab` 因未定义模块失败。

- [ ] **Step 2: 用 XSIM 验证红灯原因**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/tb/rrc_filter_tb.v
"$VIVADO/xelab.bat" rrc_filter_tb -s rrc_filter_sim
```

Expected: `rrc_filter` 未定义。若出现语法或 simulator 环境错误，先修正 testbench，不能跳过红灯阶段。

- [ ] **Step 3: 实现 4 相 polyphase FIR**

`src/rtl/rrc_filter.v` 必须 include `rrc_coeffs.vh`，保存 `i_hist[0:8]` 和 `q_hist[0:8]`。组合 MAC 只能使用以下 tap 对应关系：

```text
phase 0: coeff[0,4,8,12,16,20,24,28,32] × hist[0..8]
phase 1: coeff[1,5,9,13,17,21,25,29] × hist[0..7]
phase 2: coeff[2,6,10,14,18,22,26,30] × hist[0..7]
phase 3: coeff[3,7,11,15,19,23,27,31] × hist[0..7]
```

在 phase 0 且 `load_symbol=1` 的 MAC 中，`new_i/new_q` 替代 `hist[0]`，但 `hist[1..8]` 仍使用更新前历史；同一时钟再把旧 `hist[0]` 移入 `hist[1]`。MAC 使用 signed 40-bit 临时量。输出函数必须等价于：

```verilog
if (acc >= 0)
    rounded = (acc + 40'sd16384) >>> 15;
else
    rounded = -(((-acc) + 40'sd16384) >>> 15);
if (rounded > 32767) result = 16'sh7fff;
else if (rounded < -32768) result = 16'sh8000;
else result = rounded[15:0];
```

`sample_tick=0` 时保持输出，`sample_tick=1` 时拉高单周期 `sample_valid`。`!enable` 或 `!rst_n` 清空历史、输出和 valid。

- [ ] **Step 4: 用 XSIM 验收 filter**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/rrc_filter.v src/tb/rrc_filter_tb.v
"$VIVADO/xelab.bat" rrc_filter_tb -s rrc_filter_sim
"$VIVADO/xsim.bat" rrc_filter_sim -runall
```

Expected: `PASS rrc_filter`，所有冲激、相位、群延迟、舍入、饱和和 reset 断言通过。

### Task 4: 实现 RRC 采样调度和 QPSK 符号源

**Files:**
- Create: `src/tb/rrc_digital_mod_tb.v`
- Create: `src/rtl/rrc_digital_mod.v`
- Modify: `src/tb/iq_digital_mod_tb.v` only to keep RECT regression explicit

- [ ] **Step 1: 写入 RRC scheduler 的失败测试**

DUT 接口固定为：

```verilog
module rrc_digital_mod (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire [2:0] mode,
    input wire [31:0] sym_phase_step,
    output reg signed [15:0] i_sample,
    output reg signed [15:0] q_sample,
    output reg sample_valid,
    output reg symbol_valid
);
```

`rrc_digital_mod_tb.v` 先连接尚不存在模块，并定义：

```verilog
localparam [31:0] STEP_10K  = 32'd858993;
localparam [31:0] STEP_100K = 32'd8589934;
localparam [31:0] STEP_1M   = 32'd85899345;
```

测试必须逐项检查：

```text
- enable 后首个时钟产生 phase 0 sample 和首个 symbol_valid。
- 每四个 sample_valid 消费一个 QPSK PRBS 符号。
- phase 顺序严格为 0,1,2,3。
- 10 k、100 k、1 M 的 sample_valid 间隔为理想 4×SYMRATE 周期的 floor/ceil 时钟。
- 采样率长期平均符合 rrc_sample_phase_step=sym_phase_step<<2。
- 首 16 symbols 与 rrc_expected.mem 完全一致。
- disable、reset、重新 enable 从同一 seed 和同一 phase 重新开始。
- mode 非 QPSK 时模块不产生 RRC valid 数据。
```

- [ ] **Step 2: 运行 XSIM 红灯检查**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/tb/rrc_digital_mod_tb.v
"$VIVADO/xelab.bat" rrc_digital_mod_tb -s rrc_digital_mod_sim
```

Expected: DUT 未定义或接口不存在导致失败。

- [ ] **Step 3: 实现调度器和 PRBS/mapper 编排**

`rrc_digital_mod.v` 直接实例化已验证的 `prbs7_source` 和 `iq_constellation_mapper`，但 mapper 固定连接 `mode=3'd1`（QPSK）；外部 `mode` 只用于决定 `rrc_enable = enable && (mode == 3'd1)`。使用以下事件定义：

```verilog
wire [31:0] sample_phase_step = sym_phase_step << 2;
wire [32:0] sample_sum = {1'b0, sample_phase_acc}
                       + {1'b0, sample_phase_step};
wire rrc_enable = enable && (mode == 3'd1);
wire first_sample = rrc_enable && !active;
wire sample_tick = first_sample || (active && sample_sum[32]);
wire load_symbol = first_sample
                 || (active && sample_tick && (poly_phase == 2'd0));
wire advance_prbs = active && sample_tick && (poly_phase == 2'd3);
```

时序状态必须满足以下顺序：

```text
!rrc_enable：active、sample_phase_acc、poly_phase、输出和 valid 全部清零；PRBS 通过 reset_seed 恢复 7'h7f。
first_sample：使用当前 seed mapper 输出装入 phase 0，phase_acc 保持 0，poly_phase 变为 1；此边沿不 advance PRBS。
active 且无 sample_tick：phase_acc <= sample_sum[31:0]，输出和 phase 保持。
active 且 sample_tick：phase_acc <= sample_sum[31:0]；phase 0/1/2/3 顺序推进，load_symbol 时把 mapper 当前输出送入 filter。
phase 3 sample 的边沿 advance PRBS；下一次 phase 0 才装入已推进的下一个符号，避免跳过首个后续符号。
```

PRBS `advance_bits` 固定为 `3'd2`，因为每个 QPSK 符号消耗两个 bit。每次 `sample_tick` 的相位顺序为 `0 -> 1 -> 2 -> 3 -> 0`；`sample_valid` 透传 filter valid，`symbol_valid` 只在 `load_symbol` 时拉高一个时钟。非 QPSK mode 不产生 RRC valid 数据；系统层会在协议阶段阻止该组合，硬件层仍保持安全清零。

- [ ] **Step 4: 用 XSIM 和 Python GOLD 验收 scheduler**

Run:

```bash
PYTHONPATH=host python tools/generate_rrc_vectors.py
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/rrc_filter.v src/rtl/rrc_digital_mod.v src/tb/rrc_digital_mod_tb.v
"$VIVADO/xelab.bat" rrc_digital_mod_tb -s rrc_digital_mod_sim
"$VIVADO/xsim.bat" rrc_digital_mod_sim -runall > rrc_digital_mod.sim.log
```

Expected: `PASS rrc_digital_mod`；testbench 读取 `rrc_expected.mem` 后逐样本无差异，并打印 10 k/100 k/1 M 的 floor/ceil sample interval 统计。若 GOLD 不一致，按“输入符号、phase、tap 顺序、舍入、历史更新”顺序追踪，禁止修改参考向量迎合 RTL。

### Task 5: 保持现有 RECT 并接入 GPIO11 FILTER 和 AWG mux

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/reg_bridge_tb.v`
- Modify: `src/tb/awg_top_tb.v`

- [ ] **Step 1: 写入 FILTER 位图和 mux 失败断言**

在 `reg_bridge_tb.v` 的 `digmod_sel` 与 `digmod_sym_phase_step` 声明之间加入：

```verilog
wire digmod_filter;
```

在 DUT 实例现有 `.digmod_sel(digmod_sel),` 后加入：

```verilog
.digmod_filter(digmod_filter),
```

然后增加：

```verilog
gpio11_in = 32'h0010_0000 | (32'd2 << 17) | (32'd25 << 1) | (32'd75 << 9) | 32'h0000_0001;
repeat (2) @(posedge clk);
check(digmod_filter === 1'b1, "FILTER bit20");
check(digmod_sel === 3'd2, "MODE remains in bits 19:17");
check(ch1_pulse_duty === 8'd25 && ch2_pulse_duty === 8'd75 && ch_link_en,
      "GPIO11 low fields remain unchanged");
```

在 `awg_top_tb.v` 增加：

```text
FILTER=0 + QPSK：DAC 输入必须与原 iq_digital_mod RECT 输出 bit-exact。
FILTER=1 + QPSK：DAC 输入必须来自 rrc_i16/rrc_q16 的显式 16→8 定标，且 -32768/32767 边界分别饱和为 -127/127。
FILTER=1 + BPSK/QAM：必须不启用 RRC，不能出现 X 或未初始化数据。
MODE OFF 或 selector 6/7：必须恢复 a1/a2 标准 AWG 路径。
```

- [ ] **Step 2: 运行 bridge/top 红灯验证**

在新增端口尚未实现时运行：

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$VIVADO/xelab.bat" reg_bridge_tb -s reg_bridge_filter_sim
```

Expected: `digmod_filter` 端口/信号不存在导致失败。

- [ ] **Step 3: 修改 `reg_bridge.v`，只新增 bit20 输出**

增加：

```verilog
output wire digmod_filter;
assign digmod_filter = r11[20];
```

保留现有 r11 两级同步和 GPIO11 所有低字段；不得改变 GPIO12 两级同步或 `digmod_sel=r11[19:17]`。

- [ ] **Step 4: 在 `awg_top.v` 增加 RRC 实例和安全 mux**

增加 RRC 实例，并实现 signed 16→8 函数：

```verilog
wire rrc_active = digmod_enable && digmod_filter && (digmod_mode == 3'd1);
wire signed [15:0] rrc_i16, rrc_q16;
wire rrc_sample_valid, rrc_symbol_valid;

function signed [7:0] rrc_to_dac;
    input signed [15:0] value;
    reg signed [17:0] wide_value;
    reg signed [17:0] rounded;
    begin
        wide_value = {{2{value[15]}}, value};
        if (wide_value >= 0)
            rounded = (wide_value + 18'sd128) >>> 8;
        else
            rounded = -(((-wide_value) + 18'sd128) >>> 8);
        if (rounded > 127) rrc_to_dac = 8'sd127;
        else if (rounded < -127) rrc_to_dac = -8'sd127;
        else rrc_to_dac = rounded[7:0];
    end
endfunction

wire signed [7:0] rrc_i8 = rrc_to_dac(rrc_i16);
wire signed [7:0] rrc_q8 = rrc_to_dac(rrc_q16);
wire [7:0] dac_in0 = rrc_active ? rrc_i8
                    : digmod_enable ? dig_i : a1;
wire [7:0] dac_in1 = rrc_active ? rrc_q8
                    : digmod_enable ? dig_q : a2;
```

RRC 模块的 `enable` 连接 `rrc_active`；RECT 模块的 enable 仍连接原 `digmod_enable`。这样 FILTER=RRC 且非法 mode 时安全使用 RECT，OFF/保留 selector 继续使用标准 AWG。

- [ ] **Step 5: 运行集成 RTL 回归**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/rrc_filter.v src/rtl/rrc_digital_mod.v src/rtl/iq_digital_mod.v src/rtl/reg_bridge.v src/rtl/awg_top.v src/tb/reg_bridge_tb.v
"$VIVADO/xelab.bat" reg_bridge_tb -s reg_bridge_filter_sim
"$VIVADO/xsim.bat" reg_bridge_filter_sim -runall
"$VIVADO/xvlog.bat" -sv src/rtl/*.v src/tb/awg_top_tb.v
"$VIVADO/xelab.bat" awg_top_tb -s awg_top_filter_sim
"$VIVADO/xsim.bat" awg_top_filter_sim -runall
```

Expected: FILTER 位图、低字段、RECT GOLD、RRC mux、非法组合、OFF 恢复和 DAC offset-binary 全部通过。

### Task 6: 扩展 PS FILTER 协议和 GPIO 打包

**Files:**
- Modify: `sdk/src/main.c`
- Modify: `scripts/test_bridge.py`

- [ ] **Step 1: 先扩展板级协议测试**

在 `scripts/test_bridge.py` 增加严格顺序用例：

```python
expect("SET DIGMOD MODE OFF", "OK")
expect("SET DIGMOD FILTER RRC", "OK")
expect("GET DIGMOD", "DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC SYMRATE 10000 STATE STOPPED")
expect("SET DIGMOD MODE QPSK", "OK")
expect("GET DIGMOD", "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC SYMRATE 10000 STATE RUNNING")
expect("SET DIGMOD FILTER RECT", "ERR DIGMOD RUNNING")
expect("SET DIGMOD MODE OFF", "OK")
expect("SET DIGMOD FILTER RECT", "OK")
expect("SET DIGMOD FILTER BAD", "ERR BAD VALUE")
expect("SET DIGMOD FILTER", "ERR BAD VALUE")
expect("SET DIGMOD FILTER RECT EXTRA", "ERR BAD VALUE")
expect("SET DIGMOD FILTER RRC", "OK")
expect("SET DIGMOD MODE QAM16", "ERR FILTER REQUIRES QPSK")
expect("GET DIGMOD", "DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC SYMRATE 10000 STATE STOPPED")
expect("SET DIGMOD FILTER RECT", "OK")
```

- [ ] **Step 2: 在旧 ELF 上运行新增测试确认红灯**

Run:

```bash
python scripts/test_bridge.py COM11
```

Expected: 旧固件对 FILTER 命令返回未知/错误，原有 MODE/SYMRATE 用例仍保持通过。没有板子时保留该测试并在报告中注明未执行，不伪造 PASS。

- [ ] **Step 3: 实现 PS 状态、解析、打包和 GET**

在 `sdk/src/main.c` 增加：

```c
static u32 digmod_filter = 0; /* 0=RECT, 1=RRC */
static const char *DIGMOD_FILTER_NAME[] = {"RECT", "RRC"};
```

完整 GPIO11 打包必须保持低字段：

```c
return s2->phaselink
     | (s1->pulse_duty << 1)
     | (s2->pulse_duty << 9)
     | (digmod_mode << 17)
     | (digmod_filter << 20);
```

`SET DIGMOD FILTER` 只接受恰好 4 tokens 和 `RECT/RRC`；数字调制运行中返回 `ERR DIGMOD RUNNING`。`SET DIGMOD MODE` 在写状态前检查：若目标 mode 非 OFF、`digmod_filter==1` 且目标不是 QPSK，返回 `ERR FILTER REQUIRES QPSK`。所有失败分支都不得调用 `write_all()` 或改变本地状态。

`GET DIGMOD` 使用足够大的缓冲并严格返回：

```c
sprintf(out,
    "DIGMOD MODE %s SOURCE PRBS7 FILTER %s SYMRATE %lu STATE %s\r\n",
    DIGMOD_NAME[digmod_mode], DIGMOD_FILTER_NAME[digmod_filter],
    (unsigned long)digmod_symrate_hz,
    digmod_mode ? "RUNNING" : "STOPPED");
```

启动默认 `digmod_filter=0`，首次 `write_all()` 将 bit20 写零。

- [ ] **Step 4: 编译新 ELF 并运行协议回归**

先同步源码到当前 SDK 工作区，再使用最新 HDF/BSP 构建，不加载旧 ELF：

```bash
cp sdk/src/main.c sdk/ws/awg_bridge/src/main.c
cli-anything-vivado sdk app build --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf awg/awg.xpr
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw awg/awg.xpr
python scripts/test_bridge.py COM11
```

Expected: FILTER 合法/非法、RRC QPSK 限制、运行中拒绝、完整 GET 和既有 MODE/SYMRATE 回归全部通过。若 CLI 参数不同，先查看 `--help`，不得直接加载旧 ELF。

### Task 7: 扩展协议解析和控制器回读状态

**Files:**
- Modify: `host/siggen/protocol.py`
- Modify: `host/siggen/controller.py`
- Modify: `host/tests/test_protocol.py`
- Modify: `host/tests/test_controller.py`

- [ ] **Step 1: 写协议和控制器失败测试**

在 `test_protocol.py` 增加：

```python
from siggen.protocol import DIGMOD_FILTERS, digmod_filter_cmd, parse_digmod_status


def test_filter_command_and_new_get_status():
    assert DIGMOD_FILTERS == ("RECT", "RRC")
    assert digmod_filter_cmd("rrc") == "SET DIGMOD FILTER RRC"
    assert parse_digmod_status(
        "DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC SYMRATE 100000 STATE RUNNING"
    ) == ("QPSK", "PRBS7", "RRC", 100000, True)


def test_old_get_status_defaults_filter_to_rect():
    assert parse_digmod_status(
        "DIGMOD MODE OFF SOURCE PRBS7 SYMRATE 10000 STATE STOPPED"
    ) == ("OFF", "PRBS7", "RECT", 10000, False)


@pytest.mark.parametrize("value", ["TRIANGLE", "", None, True])
def test_filter_command_rejects_unknown_values(value):
    with pytest.raises(ValueError):
        digmod_filter_cmd(value)
```

在 `test_controller.py` 继续使用现有 `ctrl` fixture 和 `FakeWorker.sent`，通过 `Controller._on_line()` 注入桥响应；不要引用不存在的 `controller/worker` fixture 或 `respond()/timeout()` 方法：

```python
def test_set_digmod_filter_waits_for_get_confirmation(ctrl):
    ctrl.set_digmod_filter("RRC")
    assert ctrl.worker.sent[-1] == "SET DIGMOD FILTER RRC\r\n"
    assert ctrl.digmod.filter == "RECT"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET DIGMOD\r\n"
    ctrl._on_line("DIGMOD MODE OFF SOURCE PRBS7 FILTER RRC SYMRATE 10000 STATE STOPPED")
    assert ctrl.digmod.filter == "RRC"


def test_filter_error_and_timeout_keep_confirmed_state(ctrl):
    ctrl.set_digmod_filter("RRC")
    ctrl._on_line("ERR DIGMOD RUNNING")
    assert ctrl.digmod.filter == "RECT"
    ctrl.set_digmod_filter("RRC")
    ctrl._on_timeout()
    assert ctrl.digmod.filter == "RECT"
```

同时更新 `test_protocol.py` 和 `test_controller.py` 中所有旧 DIGMOD 状态断言：新解析结果和状态字段均增加第三项 `filter`，旧 9-token 输入仍显式断言默认 `RECT`。
- [ ] **Step 2: 运行红灯测试**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_protocol.py host/tests/test_controller.py -q
```

Expected: `DIGMOD_FILTERS`、`digmod_filter_cmd`、五元组解析或 controller 方法不存在而失败；不要先改生产代码。

- [ ] **Step 3: 实现 FILTER 协议和状态机**

在 `protocol.py` 增加：

```python
DIGMOD_FILTERS = ("RECT", "RRC")


def digmod_filter_cmd(filter_name):
    name = str(filter_name).upper()
    if name not in DIGMOD_FILTERS:
        raise ValueError(f"unsupported DIGMOD filter: {filter_name}")
    return f"SET DIGMOD FILTER {name}"
```

`parse_digmod_status()` 接受两种且仅两种 token 数：

```text
旧格式 9 tokens：DIGMOD MODE M SOURCE PRBS7 SYMRATE HZ STATE S
新格式 11 tokens：DIGMOD MODE M SOURCE PRBS7 FILTER F SYMRATE HZ STATE S
```

旧格式将 filter 设置为 RECT；新格式校验 mode、source、filter、rate 和 STATE 的一致性，并返回 `(mode, source, filter_name, symrate_hz, running)`。任何额外 token、未知 FILTER、非法状态或 MODE/STATE 矛盾都抛出 `ValueError`。

在 `controller.py`：

```python
@dataclass
class DigitalModState:
    mode: str = "OFF"
    source: str = "PRBS7"
    filter: str = "RECT"
    symrate_hz: int = 10_000
    running: bool = False
```

增加 `set_digmod_filter()`，复用已有单槽 pending、ACK、ERR、timeout 和 ACK 后 GET 流程。收到状态行时一次性更新五个字段，再发出已有 `digmod_changed` 信号；不扩大全局 Qt 信号参数。

- [ ] **Step 4: 运行协议/控制器和全部上位机测试**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_protocol.py host/tests/test_controller.py -q
PYTHONPATH=host python -m pytest host/tests/ -q
```

Expected: FILTER 专项和既有全部测试通过；旧 9-token 状态测试仍通过，状态默认 RECT。

### Task 8: 扩展理论预览、数字面板和主窗口

**Files:**
- Modify: `host/siggen/preview.py`
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`
- Modify: `host/tests/test_preview.py`
- Modify: `host/tests/test_widgets.py`

- [ ] **Step 1: 写 GUI/预览失败测试**

在 `test_widgets.py` 增加：

```python

def test_digital_panel_filter_selection_emits_request(dig_panel):
    got = []
    dig_panel.filter_requested.connect(got.append)
    dig_panel.filter_combo.setCurrentText("RRC")
    assert got == ["RRC"]


def test_digital_panel_confirmed_filter_locks_only_when_running(dig_panel):
    dig_panel.set_confirmed("QPSK", "PRBS7", 100000, True, filter_name="RRC")
    assert dig_panel.filter_combo.currentText() == "RRC"
    assert dig_panel.filter_combo.isEnabled() is False
    assert dig_panel.mode_combo.isEnabled() is True
    dig_panel.set_confirmed("OFF", "PRBS7", 100000, False, filter_name="RRC")
    assert dig_panel.filter_combo.isEnabled() is True
```

在 `test_preview.py` 增加：

```python

def test_preview_digital_rrc_uses_confirmed_rate_and_non_rect_samples(monkeypatch):
    import numpy as np
    import siggen.preview as preview
    from siggen.preview import WavePreview
    w = WavePreview()
    calls = []

    def fake_digital_data(mode, symbols=64, sym_ticks=5000, *,
                          symrate_hz=10000, filter_name="RECT"):
        calls.append((mode, symbols, symrate_hz, filter_name))
        return (np.array([0.0]), np.array([0.0]), np.array([0.0]))

    monkeypatch.setattr(preview, "make_digital_mod_data", fake_digital_data)
    w.set_digital("QPSK", "compare", 100000, "RRC")
    assert calls == [("QPSK", 64, 100000, "RRC"),
                     ("QPSK", 64, 100000, "RRC")]
    assert "RRC 群延迟 16 samples" in w.titleLabel.text
    assert "仅理论仿真，未接入采集数据" in w.titleLabel.text
```

同步更新 `host/tests/test_widgets.py` 中既有 `test_digital_panel_set_confirmed` 的运行状态断言，将旧文本 `当前：QAM64（PRBS7，100 kSym/s，运行中）` 改为 `当前：QAM64（PRBS7，RECT，100 kSym/s，运行中）`，并保留 OFF 状态的标准 AWG 断言。
- [ ] **Step 2: 运行红灯测试**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_widgets.py host/tests/test_preview.py -q
```

Expected: FILTER signal/combo、扩展 `set_confirmed()` 或预览参数不存在而失败。

- [ ] **Step 3: 实现最小 GUI 变更并保持旧调用兼容**

`DigitalModPanel` 增加：

```python
filter_requested = Signal(str)
self.filter_combo = QComboBox()
self.filter_combo.addItems(["RECT", "RRC"])
self.filter_combo.currentTextChanged.connect(self.filter_requested.emit)
```

`set_confirmed()` 保留现有前三个业务参数的顺序，新增 FILTER 为关键字参数，避免破坏当前调用方：

```python
def set_confirmed(self, mode, source="PRBS7", symrate_hz=10_000,
                  running=False, *, filter_name="RECT"):
    self.set_mode(mode)
    self.source_label.setText(f"数据源：内部固定 {source}（当前仅支持）")
    self.filter_combo.blockSignals(True)
    self.filter_combo.setCurrentText(filter_name)
    self.filter_combo.blockSignals(False)
    self.symrate_spin.blockSignals(True)
    self.symrate_spin.setValue(symrate_hz)
    self.symrate_spin.blockSignals(False)
    self.filter_combo.setEnabled(not running)
    self.symrate_spin.setEnabled(not running)
    self.symrate_btn.setEnabled(not running)
    self.note.setText(self._status_text(mode, source, filter_name,
                                        symrate_hz, running))

def _status_text(self, mode, source, filter_name, symrate_hz, running):
    if mode == "OFF":
        return "当前：标准 AWG 输出"
    if symrate_hz % 1000 == 0:
        symrate_text = f"{symrate_hz // 1000} kSym/s"
    else:
        symrate_text = f"{symrate_hz} Sym/s"
    state = "运行中" if running else "已停止"
    return f"当前：{mode}（{source}，{filter_name}，{symrate_text}，{state}）"
```

`_status_text()` 必须在 OFF 状态仍显示标准 AWG，否则显示 mode、source、filter、rate 和运行状态。

模式下拉框继续始终可用，以便运行中选择 OFF；FILTER 和符号率仅在 OFF 状态可编辑。面板说明明确“RRC 仅支持 QPSK；配置顺序为 OFF → FILTER RRC → MODE QPSK”。选择 RRC 后不自动改 MODE，不产生乐观状态。

`WavePreview.set_digital()` 增加 `symrate_hz` 和 `filter_name` 两个参数，并使用默认值 `10_000` 和 `"RECT"` 保持所有旧调用行为。同步扩展 `make_digital_mod_data()` 为：

```python
def make_digital_mod_data(mode, symbols=64, sym_ticks=5000, *,
                          symrate_hz=10_000, filter_name="RECT"):
```

RECT 分支继续使用原 `np.repeat()` 逻辑，但时间轴改为 `symrate_hz * sym_ticks`；RRC 分支只接受 QPSK，调用 `generate_symbols()` 和 `rrc_filter_symbols()`，时间轴为 `np.arange(len(i)) / (4 * symrate_hz)`，I/Q 除以 32767 归一化。`set_digital()` 以关键字传递 `symrate_hz` 和 `filter_name`，避免把历史 `sym_ticks` 位置参数误解释为符号率；旧的 `set_digital(mode, view)` 调用必须仍得到 RECT、10 kSym/s 的结果。星座视图继续使用同一 QPSK 理论点集；对比视图固定显示“仅理论仿真，未接入采集数据”。

`main_window.py`：

```python
self.dig.filter_requested.connect(self.ctrl.set_digmod_filter)

self.dig.set_confirmed(
    state.mode, state.source, state.symrate_hz, state.running,
    filter_name=state.filter,
)
self.status_panel.preview.set_digital(
    state.mode, view, state.symrate_hz, state.filter,
)
```

主窗口只使用 controller 已确认的 filter/rate；ACK 前和 ERR/timeout 时不更新预览。

- [ ] **Step 4: 运行离屏 GUI 回归**

Run:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=host python -m pytest host/tests/test_preview.py host/tests/test_widgets.py -q
QT_QPA_PLATFORM=offscreen PYTHONPATH=host python -m pytest host/tests/ -q
```

Expected: FILTER 控件只在 OFF 可编辑，MODE 运行中仍可选 OFF，RRC 预览使用确认速率，全部上位机测试通过且无 Qt traceback。

### Task 9: 扩展协议脚本和 RRC 板级离线判据

**Files:**
- Modify: `scripts/test_bridge.py`
- Modify: `scripts/scope_verify.py`
- Create: `host/tests/test_scope_verify_rrc.py`

- [ ] **Step 1: 先写无仪器的 RRC 判据测试**

在 `host/tests/test_scope_verify_rrc.py` 写入纯 NumPy 测试，不能实例化 VISA：

```python
import numpy as np
from scripts.scope_verify import (
    match_filter_capture,
    rrc_expected_waveform,
    rrc_symbol_ber,
)


def test_rrc_expected_waveform_has_four_samples_per_symbol():
    waveform = rrc_expected_waveform("QPSK", 100000, 32)
    assert waveform.shape == (2, 32 * 4 + 32)


def test_match_filter_capture_recovers_known_qpsk_sequence():
    expected = rrc_expected_waveform("QPSK", 100000, 127)
    recovered = match_filter_capture(expected[0], expected[1], 100000, xinc=1 / 4_000_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_match_filter_capture_removes_scope_dc_offset():
    expected = rrc_expected_waveform("QPSK", 100000, 127)
    i_capture = expected[0].astype(np.float64) / 32767.0 * 80.0 + 128.0
    q_capture = expected[1].astype(np.float64) / 32767.0 * 80.0 + 130.0
    recovered = match_filter_capture(i_capture, q_capture, 100000,
                                     xinc=1 / 4_000_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0


def test_rrc_ber_accepts_common_scope_polarity_inversion():
    expected = rrc_expected_waveform("QPSK", 100000, 32)
    recovered = match_filter_capture(-expected[0], -expected[1], 100000, xinc=1 / 4_000_000)
    assert rrc_symbol_ber(recovered, expected, trim_symbols=8) == 0.0
```

- [ ] **Step 2: 运行红灯测试确认 helper 尚不存在**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_scope_verify_rrc.py -q
```

Expected: helper 导入失败；无 VISA 设备也不应成为该阶段的失败原因。

- [ ] **Step 3: 实现速率感知的 RRC helper**

`scope_verify.py` 运行时必须能从工程根目录导入 `host/siggen`；在现有 `import sys`、`import time`、`import numpy as np` 后加入：

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
from siggen.digital_mod import generate_symbols
from siggen.rrc import matched_filter_symbols, rrc_filter_symbols
```

然后增加以下无 VISA 依赖 helper。它把示波器浮点电压重采样到 `4*symrate_hz` 网格，归一化到 signed int16 后调用黄金模型的匹配滤波；实际设备流程另负责从采集缓冲区传入两通道数组。

```python
def rrc_expected_waveform(mode, symrate_hz, symbol_count):
    if mode != "QPSK" or symrate_hz <= 0 or symbol_count < 1:
        raise ValueError("RRC scope reference requires positive QPSK parameters")
    i_sym, q_sym = generate_symbols("QPSK", symbol_count)
    return np.vstack(rrc_filter_symbols(i_sym, q_sym))


def match_filter_capture(i_capture, q_capture, symrate_hz, xinc):
    i_capture = np.asarray(i_capture, dtype=np.float64)
    q_capture = np.asarray(q_capture, dtype=np.float64)
    if i_capture.ndim != 1 or q_capture.ndim != 1 or len(i_capture) != len(q_capture):
        raise ValueError("I/Q captures must be equal-length one-dimensional arrays")
    if len(i_capture) == 0 or symrate_hz <= 0 or xinc <= 0:
        raise ValueError("capture and timing parameters must be positive")
    target_xinc = 1.0 / (4.0 * symrate_hz)
    source_time = np.arange(len(i_capture), dtype=np.float64) * xinc
    target_count = int(np.floor(source_time[-1] / target_xinc)) + 1
    target_time = np.arange(target_count, dtype=np.float64) * target_xinc
    i_grid = np.interp(target_time, source_time, i_capture)
    q_grid = np.interp(target_time, source_time, q_capture)
    i_grid -= float(np.mean(i_grid))
    q_grid -= float(np.mean(q_grid))
    peak = max(float(np.max(np.abs(i_grid))), float(np.max(np.abs(q_grid))))
    if peak == 0.0:
        return np.zeros(0, dtype=np.int16), np.zeros(0, dtype=np.int16)
    i_grid = np.clip(i_grid / peak * 32767.0, -32768.0, 32767.0).astype(np.int16)
    q_grid = np.clip(q_grid / peak * 32767.0, -32768.0, 32767.0).astype(np.int16)
    return matched_filter_symbols((i_grid, q_grid), delay_samples=32)


def rrc_symbol_ber(recovered, expected, trim_symbols=8):
    recovered_i, recovered_q = recovered
    expected = np.asarray(expected)
    if (expected.ndim != 2 or expected.shape[0] != 2 or expected.shape[1] < 32
            or (expected.shape[1] - 32) % 4
            or len(recovered_i) != len(recovered_q)):
        raise ValueError("invalid RRC reference or recovered arrays")
    if trim_symbols < 0:
        raise ValueError("trim_symbols must be non-negative")
    symbol_count = (expected.shape[1] - 32) // 4
    if len(recovered_i) < symbol_count:
        raise ValueError("capture contains fewer symbols than the reference")
    expected_i, expected_q = generate_symbols("QPSK", symbol_count)
    start = trim_symbols
    stop = symbol_count - trim_symbols
    if stop <= start:
        raise ValueError("capture is shorter than the transient guard")
    scores = []
    for observed, reference in ((recovered_i, expected_i), (recovered_q, expected_q)):
        observed = np.sign(np.asarray(observed)[start:stop])
        reference = np.sign(reference[start:stop])
        scores.append(max(float(np.mean(observed == reference)),
                          float(np.mean(-observed == reference))))
    return 1.0 - min(scores)
```

`rrc_symbol_ber()` 允许每个物理通道整体极性翻转，但不允许任意符号重排、通道交换或不一致的随机修正。

原有 RECT `symbol_period_from_iq_labels()` 和电平聚类只用于 RECT 用例，不能用于 RRC 主判据。

- [ ] **Step 4: 增加板级 RRC 测试顺序**

`scripts/scope_verify.py` 增加 10 k、100 k、1 M 三组 QPSK RRC 用例，每组严格执行：

```text
SET DIGMOD MODE OFF
SET DIGMOD FILTER RRC
SET DIGMOD SYMRATE <rate>
SET DIGMOD MODE QPSK
GET DIGMOD，确认 FILTER RRC、MODE QPSK、SYMRATE、STATE RUNNING
采集双通道 I/Q
离线匹配滤波、中心抽样、BER、眼图和频谱
SET DIGMOD MODE OFF
SET DIGMOD FILTER RECT
恢复标准 1 kHz 正弦并执行既有 OFF 检查
```

高速用例动态设置时基和采集长度，至少覆盖 32 symbols；采样点不足时报告 `SKIP/FAIL`，不得降低判据伪造通过。RRC 失败时保留原始采集和计算参数，便于诊断。

- [ ] **Step 5: 运行离线和板级协议/示波器回归**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/test_scope_verify_rrc.py -q
python scripts/test_bridge.py COM11
python scripts/scope_verify.py COM11
```

Expected: 无仪器 helper 测试通过；有板卡时新增 FILTER 协议、10k/100k/1M RRC、频谱、匹配滤波 BER 和 OFF 恢复通过。原有 RECT 28 项判据保持通过。

### Task 10: 综合、时序和资源验收

**Files:**
- Modify: `scripts/build_bitstream.py`, `scripts/check_bitstream.py`
- Modify only if required by actual reports: `src/rtl/rrc_filter.v`, `src/rtl/rrc_digital_mod.v`, `README.md`
- Test: Vivado build outputs and reports

- [ ] **Step 1: 在综合前执行全量离线/RTL 回归**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/ -q
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/rrc_filter.v src/rtl/rrc_digital_mod.v src/tb/rrc_filter_tb.v
"$VIVADO/xelab.bat" rrc_filter_tb -s rrc_filter_sim
"$VIVADO/xsim.bat" rrc_filter_sim -runall
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/rrc_filter.v src/rtl/rrc_digital_mod.v src/tb/rrc_digital_mod_tb.v
"$VIVADO/xelab.bat" rrc_digital_mod_tb -s rrc_digital_mod_sim
"$VIVADO/xsim.bat" rrc_digital_mod_sim -runall
"$VIVADO/xvlog.bat" -sv src/rtl/*.v src/tb/awg_top_tb.v
"$VIVADO/xelab.bat" awg_top_tb -s awg_top_filter_sim
"$VIVADO/xsim.bat" awg_top_filter_sim -runall
```

Expected: Python、filter、scheduler、top 和已有 RECT GOLD 全部通过后才允许综合。

- [ ] **Step 2: 将系数 include 纳入 bitstream 新鲜度判断**

`build_bitstream.py` 和 `check_bitstream.py` 的 `hash_rtl()` 必须使用同一文件集合：

```python
files = (sorted(RTL.glob("*.v")) + sorted(RTL.glob("*.vh"))
         + sorted((ROOT / "src" / "constraints").glob("*.xdc")) + [
             ROOT / "scripts" / "create_bd.tcl",
             ROOT / "scripts" / "ps7_props.tcl",
             ROOT / "tools" / "rebuild_bitstream.tcl",
             ROOT / "tools" / "synth1_read_awgtop.tcl",
         ])
```

实际实现中保留两个脚本当前的完整配方文件列表。`check_bitstream.py` 的 mtime 提示也遍历 `*.v` 和 `*.vh`，避免只修改 `rrc_coeffs.vh` 时误判 manifest 仍然新鲜。先运行：

```bash
python -m py_compile scripts/build_bitstream.py scripts/check_bitstream.py
python scripts/check_bitstream.py
```

预期：脚本可编译；在系数未进入当前 manifest 时明确报告 `STALE`，不得声称 bitstream 可下载。

- [ ] **Step 3: 使用唯一构建脚本重建 bitstream**

Run:

```bash
python scripts/build_bitstream.py
python scripts/check_bitstream.py
```

Expected: `create_bd.tcl` 不变但 `awg_top.v`、RRC RTL 和系数进入哈希；综合、实现和 bitstream 成功。禁止直接运行 Vivado synth 或下载旧 bitstream。

- [ ] **Step 4: 检查资源和 50 MHz 时序**

从 `awg/awg.runs/synth_1` 和 `impl_1` 报告记录：

```text
- LUT、FF、DSP48、BRAM 使用量及百分比。
- sys_clk 50 MHz 的 WNS/TNS 和最高频率。
- rrc_filter 的乘法器是否被推断为预期 DSP/LUT。
- 4 MS/s sample tick 下 sample_valid 到下一 tick 的计算余量。
- 是否出现 signed truncation、latch、multiple driver 或 timing exception 警告。
```

门槛为：实现通过、50 MHz WNS≥0、无未解释的 signed 截断/锁存警告；若资源不足，优先把每相 MAC 改为经过验证的时分结构，但必须重新生成 GOLD、重新跑 RTL 逐点比较，并记录延迟不变。

### Task 11: 完整板级 RRC 验收和文档收尾

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-08-27-digital-modulation-symbol-rate-design.md` only if it must link to the new phase

- [ ] **Step 1: 下载最新 bitstream 和 ELF**

Run:

```bash
bash scripts/program_board.sh
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw awg/awg.xpr
```

Expected: 下载前 manifest 校验通过，PS 桥使用与当前源码匹配的新 ELF。

- [ ] **Step 2: 执行三档 RRC 采样率和频谱验收**

Run:

```bash
python scripts/test_bridge.py COM11
python scripts/scope_verify.py COM11
```

逐项记录：

```text
10 kSym/s：40 kS/s，4 sample/symbol，RRC 中心抽样和 BER=0
100 kSym/s：400 kS/s，4 sample/symbol，RRC 中心抽样和 BER=0
1 MSym/s：4 MS/s，4 sample/symbol，4 MS/s 调度和 BER=0
RECT/RRC：相对频谱滚降趋势和理论预览一致
OFF：恢复既有 1 kHz 标准正弦
```

- [ ] **Step 3: 更新 README 的实际使用顺序**

加入准确命令和状态示例：

```text
SET DIGMOD MODE OFF
SET DIGMOD FILTER RRC
SET DIGMOD SYMRATE 100000
SET DIGMOD MODE QPSK
GET DIGMOD
DIGMOD MODE QPSK SOURCE PRBS7 FILTER RRC SYMRATE 100000 STATE RUNNING
```

明确：FILTER 只在 OFF 修改；RRC 只支持 QPSK；RRC 内部为 16-bit、DAC 输出经过右移/饱和；匹配滤波总延迟 32 samples；RECT 默认和既有协议兼容；理论预览不等于实测。

- [ ] **Step 4: 执行最终全量回归**

Run:

```bash
PYTHONPATH=host python -m pytest host/tests/ -q
python scripts/test_bridge.py COM11
python scripts/scope_verify.py COM11
python scripts/build_bitstream.py
python scripts/check_bitstream.py
git diff --check
git status --short
```

Expected：

```text
Python 上位机全量测试通过。
RTL filter/scheduler/top 测试通过。
协议回归通过。
10 k/100 k/1 M RRC 板级采样、匹配滤波 BER 和频谱验收通过。
OFF 后 RECT/标准 AWG 恢复通过。
bitstream manifest 与当前 RTL 一致。
git diff --check 无输出。
```

## 阶段闸门

- 阶段 1 闸门：RRC 公式、奇异点、Q1.15、归一化、舍入、MAC、饱和、DAC 定标和延迟均已由规格冻结。
- 阶段 2 闸门：Python 33 tap、4 SPS、定点卷积和匹配滤波 BER=0；RECT 模型 bit-exact。
- 阶段 3 闸门：filter 冲激响应、4 相 tap 顺序、复位/启停和舍入饱和 XSIM 通过。
- 阶段 4 闸门：scheduler 三档速率、4 samples/symbol、Python/RTL 逐点 GOLD 和 PRBS 重启通过。
- 阶段 5 闸门：GPIO11 bit20、RECT mux、非法组合、OFF 恢复和既有 RECT GOLD 通过。
- 阶段 6 闸门：FILTER 协议、新旧 GET 解析、运行中拒绝、QPSK 限制和回读确认通过。
- 阶段 7 闸门：GUI 只显示已确认状态，RRC 预览使用确认速率，运行中 MODE 仍可选 OFF。
- 阶段 8 闸门：综合资源和 50 MHz 时序通过，未出现未解释截断或 latch 警告。
- 阶段 9 闸门：10 k/100 k/1 M 板级匹配滤波 BER=0、频谱趋势通过，OFF 恢复和全部旧回归通过。

## 自检清单

- [ ] 规格没有把 span=8 错写成 8 taps 或单边 8 taps。
- [ ] 33 taps 的中心抽头是 index 16，匹配总延迟是 32 samples。
- [ ] Python 和 RTL 均使用负数 ties-away-from-zero，而不是语言默认负数右移。
- [ ] RRC 输出和 DAC 转换均显式饱和，没有 two's-complement 回绕。
- [ ] RRC 采样节拍使用 `sym_phase_step << 2`，没有用固定 50 MHz/符号计数器。
- [ ] RECT 路径没有经过 RRC 定标，5×127 GOLD 完全不变。
- [ ] GPIO11 bit20 与 MODE、旧 link/duty 同字更新，GPIO12 仍只承载 phase step。
- [ ] FILTER 只在 OFF 配置，RRC 非 QPSK 被协议拒绝、硬件安全回退。
- [ ] 旧 9-token GET 状态按 RECT 兼容解析，新固件输出 11-token 完整状态。
- [ ] GUI ACK 前不乐观更新，运行中 MODE 下拉框仍可选择 OFF。
- [ ] 示波器 RRC 使用中心抽样/匹配滤波，不使用 RECT 离散电平聚类作为主判据。
- [ ] 未经用户明确要求不创建 git commit。
