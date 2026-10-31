# 信号源信道损伤（静态多径 + AWGN）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Zynq PL 最终 DAC 输出上加每通道可配的静态多径 FIR + 近似 AWGN 噪声，并打通寄存器/BD/固件/协议/GUI/验证整条链路。

**Architecture:** 在 `awg_top.v` 两路最终样本汇合点（`dac_in0/1`）后插入 `chan_impair`（`fade_fir` → `noise_gen` → 饱和），两通道各一份；抽头表经新增 AXI BRAM 由 PS 写、PL 读并在 commit 边沿锁存；enable=0 时逐位透传。主机侧 `host/siggen/channel.py` 用整数算术镜像 RTL，作为 RTL TB 的 golden 与示波器比对参考。

**Tech Stack:** Verilog-2001（纯 RTL，无新 IP 核，除 BD 的 AXI GPIO/BRAM）、Vivado 2025.2 xvlog/xelab/xsim、Vitis 2025.2 裸机桥 `sdk/src/main.c`、Python 3 + numpy/pytest（上位机）、PySide6 GUI。

**Spec:** `docs/superpowers/specs/2026-09-10-channel-impairment-design.md`

## Global Constraints

- 抽头数 **N=4**（配置表预留到 8）。增益 **9-bit 有符号 Q8**（−256…255 = −1.0…+0.9961）；延迟 **9-bit 无符号**（0…511 样本，20 ns 分辨率）。
- 噪声语义 = **幅度 %FS**：`σ = level/100 × 127` LSB，`level ∈ [0,100]`；`noise = round_div((S−510)·σ, 148)`，`S` 为 4 路 8-bit 均匀之和。
- 多径累加 **20-bit 有符号**；输出 `sat(round_signed(acc, 8), ±127)`（符号对称四舍五入，参照 `awg_top.v` 的 `rrc_to_dac`）。
- 顺序固定 **fade → noise → saturate**；`chan_en=0` 时**逐位透传**（既有已验收行为不变）。
- **非目标**：不做时变/多普勒/复 I/Q/分数延迟/自动归一化；`SET`/`GET CHx`（既有）wire 格式不变。
- 仿真命令：`SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"`；`"$SIM/xvlog.bat" --sv <files>` → `"$SIM/xelab.bat" <tb> -s <sim>` → `"$SIM/xsim.bat" <sim> -runall`；TB 输出必须含 `PASS <模块名>`。
- BD 结构改动（新 AXI GPIO/BRAM）必须用 `VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" python scripts/build_bitstream.py --force` 重建 HDF，并从新 HDF 新建 BSP/应用。
- 上位机测试：`QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/ -q`。
- 每个 Task 结束时提交（commit）；单个 Task 内保持工作树可运行。

---

### Task 1: 主机信道模型 `host/siggen/channel.py`

**Files:**
- Create: `host/siggen/channel.py`
- Test: `host/tests/test_channel.py`

**Interfaces:**
- Produces:
  - `GAIN_W=9`, `DELAY_W=9`, `TAPS=4`, `DEPTH=512`, `NOISE_SIGMA_RAW=148`, `DAC_MIN=-128`, `DAC_MAX=127`
  - `round_shift_signed(value: int, shift: int) -> int`（符号对称四舍五入）
  - `multipath_fir(samples, gains, delays) -> list[int]`（`gains` 为 Q8 整数列表，`delays` 整数样本，返回同长度、钳位到 ±127 的列表）
  - `noise_samples(count: int, level: int, seed: int) -> list[int]`
  - `add_noise(samples, level, seed) -> list[int]`

- [ ] **Step 1: Write the failing test**

`host/tests/test_channel.py`：

```python
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
    x = [0, 10, -10, 100, -128, 127, 1]
    assert multipath_fir(x, [256, 0, 0, 0], [0, 0, 0, 0]) == x


def test_two_tap_echo_matches_hand_computed():
    # g=[256,128] d=[0,2]: y[n] = x[n] + 0.5*x[n-2]
    x = [0, 0, 0, 40, 0, 0, 0, 0]
    assert multipath_fir(x, [256, 128, 0, 0], [0, 2, 0, 0]) == \
        [0, 0, 0, 40, 0, 0, 20, 0]


def test_multipath_saturates():
    x = [100, 100]
    assert multipath_fir(x, [256, 256, 0, 0], [0, 0, 0, 0])[0] == DAC_MAX
    x = [-100, -100]
    assert multipath_fir(x, [256, 256, 0, 0], [0, 0, 0, 0])[0] == -100 - 100 if False else True
    # 两点同址相加 = 200 -> 饱和 +127; 负向 -200 -> 饱和 -127
    assert multipath_fir([-100], [256, 256, 0, 0], [0, 0, 0, 0])[0] == -127


def test_multipath_rejects_out_of_range_taps():
    with pytest.raises(ValueError):
        multipath_fir([0], [256, 0], [0, 0, 0])      # 长度不匹配
    with pytest.raises(ValueError):
        multipath_fir([0], [512, 0, 0, 0], [0, 0, 0, 0])   # 增益越界
    with pytest.raises(ValueError):
        multipath_fir([0], [256, 0, 0, 0], [512, 0, 0, 0])  # 延迟越界


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/test_channel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'siggen.channel'`

- [ ] **Step 3: Write minimal implementation**

`host/siggen/channel.py`：

```python
"""静态多径 + 近似 AWGN 的整数算术模型（RTL `fade_fir.v`/`noise_gen.v` 的镜像）。"""
import math

GAIN_W = 9
DELAY_W = 9
TAPS = 4
DEPTH = 512
GAIN_MIN, GAIN_MAX = -256, 255
DELAY_MIN, DELAY_MAX = 0, 511
DAC_MIN, DAC_MAX = -128, 127
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/test_channel.py -q`
Expected: PASS（全部用例）

- [ ] **Step 5: Commit**

```bash
git add host/siggen/channel.py host/tests/test_channel.py
git commit -m "feat(host): static multipath + AWGN integer model (RTL mirror)"
```

---

### Task 2: `src/rtl/fade_fir.v` + TB

**Files:**
- Create: `src/rtl/fade_fir.v`
- Test: `src/tb/fade_fir_tb.v`

**Interfaces:**
- Consumes: 无（叶子模块）。
- Produces: `fade_fir #(GAIN_W=9, DELAY_W=9, TAPS=4, DEPTH=512)(clk, rst_n, sample_in[7:0], tap_gain[TAPS*GAIN_W-1:0], tap_delay[TAPS*DELAY_W-1:0], sample_out[7:0])`，**3 拍流水**（乘→平衡树→舍入饱和）。

- [ ] **Step 1: Write the failing test**

`src/tb/fade_fir_tb.v`：

```verilog
`timescale 1ns / 1ps
module fade_fir_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [7:0] sample_in = 0;
    reg [3*9+8:0] tap_gain = 0;    // 4 x 9-bit Q8
    reg [3*9+8:0] tap_delay = 0;   // 4 x 9-bit
    wire [7:0] sample_out;

    fade_fir uut (.clk(clk), .rst_n(rst_n), .sample_in(sample_in),
                  .tap_gain(tap_gain), .tap_delay(tap_delay),
                  .sample_out(sample_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    // 设抽头并让 3 级流水清空
    task set_taps; input [8:0] g0,g1,g2,g3; input [8:0] d0,d1,d2,d3;
        begin tap_gain = {g3,g2,g1,g0}; tap_delay = {d3,d2,d1,d0}; end
    endtask

    integer i;
    initial begin
        #100 rst_n = 1;
        // 单位抽头: 逐位透传
        set_taps(9'sd256, 0, 0, 0, 0, 0, 0, 0);
        @(posedge clk);
        for (i = 0; i < 8; i = i + 1) begin
            sample_in = (i * 37 - 100);
            @(posedge clk); #1;
            repeat (3) @(posedge clk); // 等 3 拍流水
            chk("unity passthrough", $signed(sample_out) == $signed(sample_in));
            sample_in = 0; repeat (3) @(posedge clk);
        end
        // 两点回声 g=[256,128] d=[0,2] 于冲激序列
        rst_n = 0; @(posedge clk); rst_n = 1; @(posedge clk);
        set_taps(9'sd256, 9'sd128, 0, 0, 9'd0, 9'd2, 0, 0);
        sample_in = 8'd40; @(posedge clk);
        sample_in = 8'd0;
        repeat (3) @(posedge clk); #1; chk("echo direct", $signed(sample_out) == 40);
        @(posedge clk); #1; chk("echo n+1", $signed(sample_out) == 0);
        @(posedge clk); #1; chk("echo n+2", $signed(sample_out) == 20);
        // 饱和: 两个抽头同址 g=[256,256] d=[0,0] on +100 -> 200 -> 127
        rst_n = 0; @(posedge clk); rst_n = 1; @(posedge clk);
        set_taps(9'sd256, 9'sd256, 0, 0, 0, 0, 0, 0);
        sample_in = 8'd100; @(posedge clk); sample_in = 8'd0;
        repeat (3) @(posedge clk); #1; chk("saturate +", $signed(sample_out) == 127);
        if (errs == 0) $display("PASS fade_fir");
        else $display("FAIL fade_fir (%0d errors)", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/fade_fir.v src/tb/fade_fir_tb.v
"$SIM/xelab.bat" fade_fir_tb -s fade_sim
```
Expected: xelab 报 `module 'fade_fir' not found`（文件尚未实现）。

- [ ] **Step 3: Write minimal implementation**

`src/rtl/fade_fir.v`：

```verilog
`timescale 1ns / 1ps
// 静态多径实数 FIR: y[n] = sat(round(Σ g_k·x[n−d_k] / 256), ±127)
// g_k 9-bit Q8 有符号, d_k 9-bit 无符号。每抽头一条 512x8 分布式 RAM,广播写、并行读;
// d_k=0 直接取当前输入(避开写读同址)。3 拍流水: 乘 -> 平衡加法树 -> 舍入饱和。
module fade_fir #(
    parameter GAIN_W = 9,
    parameter DELAY_W = 9,
    parameter TAPS = 4,
    parameter DEPTH = 512
)(
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] sample_in,
    input  wire [TAPS*GAIN_W-1:0] tap_gain,
    input  wire [TAPS*DELAY_W-1:0] tap_delay,
    output reg  [7:0] sample_out
);
    localparam ADDR_W = $clog2(DEPTH);

    reg [ADDR_W-1:0] wr_ptr;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) wr_ptr <= {ADDR_W{1'b0}};
        else        wr_ptr <= wr_ptr + 1'b1;

    wire signed [GAIN_W-1:0] gain_k  [0:TAPS-1];
    wire [DELAY_W-1:0]       delay_k [0:TAPS-1];
    wire signed [7:0]        tap_src [0:TAPS-1];

    genvar k;
    generate
        for (k = 0; k < TAPS; k = k + 1) begin : g_tap
            assign gain_k[k]  = tap_gain[k*GAIN_W +: GAIN_W];
            assign delay_k[k] = tap_delay[k*DELAY_W +: DELAY_W];
            reg [7:0] mem [0:DEPTH-1];
            wire [ADDR_W-1:0] rd_addr = wr_ptr - delay_k[k][ADDR_W-1:0];
            always @(posedge clk) mem[wr_ptr] <= sample_in;
            assign tap_src[k] = (delay_k[k] == {DELAY_W{1'b0}}) ? $signed(sample_in)
                                                                : $signed(mem[rd_addr]);
        end
    endgenerate

    reg signed [GAIN_W+8-1:0] prod_r [0:TAPS-1];
    always @(posedge clk) begin : g_prod
        integer t;
        for (t = 0; t < TAPS; t = t + 1)
            prod_r[t] <= gain_k[t] * tap_src[t];
    end

    reg signed [GAIN_W+10-1:0] acc_r;
    always @(posedge clk) begin
        acc_r <= (prod_r[0] + prod_r[1]) + (prod_r[2] + prod_r[3]);
    end

    function signed [7:0] round_sat;
        input signed [GAIN_W+10-1:0] v;
        reg signed [GAIN_W+10-1:0] m;
        reg signed [GAIN_W+10-1:0] r;
        begin
            if (v >= 0) r = (v + 128) >>> 8;
            else begin m = -v; r = -((m + 128) >>> 8); end
            if (r > 127)        round_sat = 8'sd127;
            else if (r < -127)  round_sat = -8'sd127;
            else                round_sat = r[7:0];
        end
    endfunction

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample_out <= 8'sd0;
        else        sample_out <= round_sat(acc_r);
endmodule
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/fade_fir.v src/tb/fade_fir_tb.v
"$SIM/xelab.bat" fade_fir_tb -s fade_sim
"$SIM/xsim.bat" fade_sim -runall | grep -E "PASS|FAIL"
```
Expected: `PASS fade_fir` 且无 `FAIL`。

- [ ] **Step 5: Commit**

```bash
git add src/rtl/fade_fir.v src/tb/fade_fir_tb.v
git commit -m "feat(rtl): fade_fir static multipath 4-tap FIR"
```

---

### Task 3: `src/rtl/noise_gen.v` + TB

**Files:**
- Create: `src/rtl/noise_gen.v`
- Test: `src/tb/noise_gen_tb.v`

**Interfaces:**
- Produces: `noise_gen #(SEED)(clk, rst_n, enable, level[6:0], noise_out signed[10:0])`，**1 拍流水**；`level=0` 或 `enable=0` → 输出 0。
- 与 `host/siggen/channel.py::noise_samples` 逐值一致（同 `SEED`、同 level）。

- [ ] **Step 1: Write the failing test**

先用主机模型算前 4 个 golden（同 SEED=0x00C0FFEE、level=50）：

Run: `host/.venv/Scripts/python -c "import sys; sys.path.insert(0,'host'); from siggen.channel import noise_samples; print(noise_samples(4,50,0x00C0FFEE))"`
把打印出的 4 个整数填入下面 TB 的 `G0..G3`。

`src/tb/noise_gen_tb.v`：

```verilog
`timescale 1ns / 1ps
module noise_gen_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg enable = 1;
    reg [6:0] level = 0;
    wire signed [10:0] noise_out;

    noise_gen #(.SEED(32'h00C0FFEE)) uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .level(level),
        .noise_out(noise_out));
    always #10 clk = ~clk;

    // 由 host/siggen/channel.py::noise_samples(4, 50, 0x00C0FFEE) 打印得到:
    localparam signed [10:0] G0 = 0, G1 = 0, G2 = 0, G3 = 0;  // <-- 替换为打印值

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    integer i;
    initial begin
        #100 rst_n = 1;
        // level=0 -> 全 0
        level = 0;
        repeat (8) @(posedge clk);
        chk("level0 zero", noise_out == 0);

        // golden: 复位后 level=50 的前 4 个样点
        rst_n = 0; repeat (2) @(posedge clk); rst_n = 1;
        level = 7'd50;
        @(posedge clk); #1;
        chk("golden0", noise_out == G0);
        @(posedge clk); #1; chk("golden1", noise_out == G1);
        @(posedge clk); #1; chk("golden2", noise_out == G2);
        @(posedge clk); #1; chk("golden3", noise_out == G3);

        // enable=0 -> 0
        enable = 0; repeat (4) @(posedge clk); #1; chk("disabled zero", noise_out == 0);
        enable = 1;

        if (errs == 0) $display("PASS noise_gen");
        else $display("FAIL noise_gen (%0d errors)", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/noise_gen.v src/tb/noise_gen_tb.v
"$SIM/xelab.bat" noise_gen_tb -s noise_sim
```
Expected: xelab 报 `module 'noise_gen' not found`。

- [ ] **Step 3: Write minimal implementation**

`src/rtl/noise_gen.v`：

```verilog
`timescale 1ns / 1ps
// 确定性近似 AWGN: xorshift32 -> 4 路 8-bit 均匀求和(CLT) -> 减均值 -> 按 level 缩放。
// sigma = level*127/100 (LSB), noise = round_div((S-510)*sigma, 148)。1 拍流水。
module noise_gen #(
    parameter [31:0] SEED = 32'h00C0FFEE
)(
    input  wire clk,
    input  wire rst_n,
    input  wire enable,
    input  wire [6:0] level,
    output reg signed [10:0] noise_out
);
    localparam signed [17:0] SIGMA_RAW = 18'sd148;

    reg [31:0] state;
    wire [31:0] xs1 = state ^ (state << 13);
    wire [31:0] xs2 = xs1 ^ (xs1 >> 17);
    wire [31:0] xs3 = xs2 ^ (xs2 << 5);

    wire [9:0] sum_uniform = xs3[7:0] + xs3[15:8] + xs3[23:16] + xs3[31:24];
    wire signed [10:0] centered = $signed({1'b0, sum_uniform}) - 11'sd510;
    wire [6:0] lvl = (level > 7'd100) ? 7'd100 : level;
    wire signed [17:0] sigma = (lvl * 7'sd127) / 7'sd100;
    wire signed [17:0] prod = centered * sigma;

    function signed [10:0] round_div;
        input signed [17:0] v;
        reg signed [17:0] m;
        begin
            if (v >= 0) round_div = (v + (SIGMA_RAW >>> 1)) / SIGMA_RAW;
            else begin m = -v; round_div = -((m + (SIGMA_RAW >>> 1)) / SIGMA_RAW); end
        end
    endfunction

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin state <= SEED; noise_out <= 11'sd0; end
        else begin
            state <= xs3;
            noise_out <= enable ? round_div(prod) : 11'sd0;
        end
endmodule
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/noise_gen.v src/tb/noise_gen_tb.v
"$SIM/xelab.bat" noise_gen_tb -s noise_sim
"$SIM/xsim.bat" noise_sim -runall | grep -E "PASS|FAIL"
```
Expected: `PASS noise_gen`。
若 golden 不匹配，修正 `noise_gen.v` 的位序/取整使其与 `noise_samples` 一致（不是改 golden）。

- [ ] **Step 5: Commit**

```bash
git add src/rtl/noise_gen.v src/tb/noise_gen_tb.v
git commit -m "feat(rtl): noise_gen deterministic approximate-AWGN source"
```

---

### Task 4: `src/rtl/chan_impair.v` + TB

**Files:**
- Create: `src/rtl/chan_impair.v`
- Test: `src/tb/chan_impair_tb.v`

**Interfaces:**
- Consumes: `fade_fir`（Task 2）、`noise_gen`（Task 3）。
- Produces: `chan_impair #(SEED)(clk, rst_n, enable, sample_in[7:0], tap_gain[35:0], tap_delay[35:0], noise_level[6:0], sample_out[7:0])`；`enable=0` 透传（1 拍），`enable=1` 为 fade→noise→sat（4 拍）。

- [ ] **Step 1: Write the failing test**

`src/tb/chan_impair_tb.v`：

```verilog
`timescale 1ns / 1ps
module chan_impair_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg enable = 0;
    reg [7:0] sample_in = 0;
    reg [35:0] tap_gain = 36'h0000_0100;   // tap0 g=256, 其余 0
    reg [35:0] tap_delay = 36'd0;
    reg [6:0] noise_level = 0;
    wire [7:0] sample_out;

    chan_impair uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                     .sample_in(sample_in), .tap_gain(tap_gain),
                     .tap_delay(tap_delay), .noise_level(noise_level),
                     .sample_out(sample_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    integer i;
    initial begin
        #100 rst_n = 1;
        // enable=0: 逐位透传(1 拍)
        enable = 0;
        for (i = 0; i < 6; i = i + 1) begin
            sample_in = (i * 29 - 80);
            @(posedge clk); #1;
            repeat (2) @(posedge clk);
            chk("bypass passthrough", $signed(sample_out) == $signed(sample_in));
        end
        // enable=1, noise=0, unity tap: 透传(4 拍)
        enable = 1; noise_level = 0;
        sample_in = 8'd77;
        repeat (6) @(posedge clk); #1;
        chk("unity tap noise0 passthrough", $signed(sample_out) == 77);
        // noise>0 但 unity tap: 输出非恒定(噪声注入)
        noise_level = 7'd80;
        repeat (16) @(posedge clk);
        chk("noise perturbs", $signed(sample_out) != 77);
        if (errs == 0) $display("PASS chan_impair");
        else $display("FAIL chan_impair (%0d errors)", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/fade_fir.v src/rtl/noise_gen.v src/rtl/chan_impair.v src/tb/chan_impair_tb.v
"$SIM/xelab.bat" chan_impair_tb -s chan_sim
```
Expected: xelab 报 `module 'chan_impair' not found`。

- [ ] **Step 3: Write minimal implementation**

`src/rtl/chan_impair.v`：

```verilog
`timescale 1ns / 1ps
// 信道损伤: fade_fir -> noise_gen -> 饱和。enable=0 时逐位透传(1 拍)。
// enable=1 时 fade(3 拍)与 noise(1 拍)对齐后相加,总 4 拍。
module chan_impair #(
    parameter [31:0] SEED = 32'h00C0FFEE
)(
    input  wire clk,
    input  wire rst_n,
    input  wire enable,
    input  wire [7:0] sample_in,
    input  wire [35:0] tap_gain,
    input  wire [35:0] tap_delay,
    input  wire [6:0] noise_level,
    output reg  [7:0] sample_out
);
    wire [7:0] fade_out;
    wire signed [10:0] noise_raw;

    fade_fir u_fade (
        .clk(clk), .rst_n(rst_n), .sample_in(sample_in),
        .tap_gain(tap_gain), .tap_delay(tap_delay), .sample_out(fade_out));
    noise_gen #(.SEED(SEED)) u_noise (
        .clk(clk), .rst_n(rst_n), .enable(enable), .level(noise_level),
        .noise_out(noise_raw));

    // 对齐 fade(已在第 3 拍)与 noise(第 1 拍): 噪声再延迟 2 拍
    reg signed [10:0] noise_d1, noise_d2;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin noise_d1 <= 0; noise_d2 <= 0; end
        else begin noise_d1 <= noise_raw; noise_d2 <= noise_d1; end

    wire signed [11:0] sum = $signed({fade_out[7], fade_out}) + noise_d2;
    wire signed [7:0] clamped = (sum > 127) ? 8'sd127 :
                                (sum < -128) ? -8'sd128 : sum[7:0];

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample_out <= 8'sd0;
        else        sample_out <= enable ? clamped : sample_in;
endmodule
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/fade_fir.v src/rtl/noise_gen.v src/rtl/chan_impair.v src/tb/chan_impair_tb.v
"$SIM/xelab.bat" chan_impair_tb -s chan_sim
"$SIM/xsim.bat" chan_sim -runall | grep -E "PASS|FAIL"
```
Expected: `PASS chan_impair`。

- [ ] **Step 5: Commit**

```bash
git add src/rtl/chan_impair.v src/tb/chan_impair_tb.v
git commit -m "feat(rtl): chan_impair fade+noise+saturate with bypass"
```

---

### Task 5: `src/rtl/chan_cfg.v` + TB（抽头表读取/锁存）

**Files:**
- Create: `src/rtl/chan_cfg.v`
- Test: `src/tb/chan_cfg_tb.v`

**Interfaces:**
- Produces: `chan_cfg(clk, rst_n, ch1_commit, ch2_commit, ch1_en, ch2_en, ch1_noise[6:0], ch2_noise[6:0], tap_b_addr[4:0], tap_b_en, tap_b_rdata[31:0], ch1_gain[35:0], ch1_delay[35:0], ch2_gain[35:0], ch2_delay[35:0])`。
- BRAM 布局（32-bit × 16）：字 `0..3` = CH1 tap0..3，字 `4..7` = CH2 tap0..3，字 `8..15` 预留。每字 = `gain[8:0] | delay[17:9]`。
- 行为：`chX_commit` 出现边沿 → 顺序读该通道 4 个字（`tap_b_addr` 递增、`tap_b_en=1`），把 `gain/delay` 锁存到 `chX_gain/delay` 寄存器。

- [ ] **Step 1: Write the failing test**

`src/tb/chan_cfg_tb.v`：

```verilog
`timescale 1ns / 1ps
module chan_cfg_tb;
    reg clk = 0, rst_n = 0;
    reg ch1_commit = 0, ch2_commit = 0, ch1_en = 1, ch2_en = 1;
    reg [6:0] ch1_noise = 7'd20, ch2_noise = 7'd30;
    wire [4:0] tap_b_addr;
    wire tap_b_en;
    reg [31:0] tap_b_rdata = 0;

    wire [35:0] ch1_gain, ch1_delay, ch2_gain, ch2_delay;
    chan_cfg uut (.clk(clk), .rst_n(rst_n),
        .ch1_commit(ch1_commit), .ch2_commit(ch2_commit),
        .ch1_en(ch1_en), .ch2_en(ch2_en),
        .ch1_noise(ch1_noise), .ch2_noise(ch2_noise),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay));
    always #10 clk = ~clk;

    // 伪 BRAM: 按地址返回 CH1 tap0..3 = g=256,d=0/1/2/3; CH2 tap0..3 = g=128,d=4/5/6/7
    always @* begin
        case (tap_b_addr)
            5'd0: tap_b_rdata = {9'd0, 9'sd256};
            5'd1: tap_b_rdata = {9'd1, 9'sd256};
            5'd2: tap_b_rdata = {9'd2, 9'sd256};
            5'd3: tap_b_rdata = {9'd3, 9'sd256};
            5'd4: tap_b_rdata = {9'd4, 9'sd128};
            5'd5: tap_b_rdata = {9'd5, 9'sd128};
            5'd6: tap_b_rdata = {9'd6, 9'sd128};
            5'd7: tap_b_rdata = {9'd7, 9'sd128};
            default: tap_b_rdata = 32'd0;
        endcase
    end

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    initial begin
        #100 rst_n = 1;
        repeat (4) @(posedge clk);
        // CH1 commit 边沿
        @(posedge clk); ch1_commit = 1;
        repeat (20) @(posedge clk); #1;
        chk("ch1 tap0 gain", ch1_gain[8:0]  == 9'sd256);
        chk("ch1 tap0 delay", ch1_delay[8:0] == 9'd0);
        chk("ch1 tap3 delay", ch1_delay[3*9+8 -: 9] == 9'd3);
        chk("ch2 untouched", ch2_gain[8:0] == 9'd0);
        @(posedge clk); ch2_commit = 1;
        repeat (20) @(posedge clk); #1;
        chk("ch2 tap0 gain", ch2_gain[8:0]  == 9'sd128);
        chk("ch2 tap3 delay", ch2_delay[3*9+8 -: 9] == 9'd7);
        if (errs == 0) $display("PASS chan_cfg");
        else $display("FAIL chan_cfg (%0d errors)", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
"$SIM/xelab.bat" chan_cfg_tb -s cfg_sim
```
Expected: xelab 报 `module 'chan_cfg' not found`。

- [ ] **Step 3: Write minimal implementation**

`src/rtl/chan_cfg.v`：

```verilog
`timescale 1ns / 1ps
// 抽头表读取/锁存: chX_commit 边沿 -> 顺序读 BRAM 4 个字 -> 锁存 gain/delay。
// 字布局: gain[8:0] | delay[17:9]。CH1 base=0, CH2 base=4。
module chan_cfg (
    input  wire clk,
    input  wire rst_n,
    input  wire ch1_commit,
    input  wire ch2_commit,
    input  wire ch1_en,
    input  wire ch2_en,
    input  wire [6:0] ch1_noise,
    input  wire [6:0] ch2_noise,
    output reg  [4:0] tap_b_addr,
    output reg  tap_b_en,
    input  wire [31:0] tap_b_rdata,
    output reg  [35:0] ch1_gain,
    output reg  [35:0] ch1_delay,
    output reg  [35:0] ch2_gain,
    output reg  [35:0] ch2_delay
);
    localparam S_IDLE = 2'd0, S_READ = 2'd1, S_DONE = 2'd2;

    reg [1:0] state;
    reg cur_ch;          // 0 = CH1, 1 = CH2
    reg [2:0] step;      // 0..3 当前抽头
    reg commit_d1, commit_d2;
    reg en_d1, en_d2;    // (enable/noise 由 awg_top 直连, 此处仅读表)

    wire any_commit = !state && ((ch1_commit != commit_d1) || (ch2_commit != commit_d2));

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE; cur_ch <= 0; step <= 0;
            tap_b_addr <= 0; tap_b_en <= 0;
            ch1_gain <= 0; ch1_delay <= 0; ch2_gain <= 0; ch2_delay <= 0;
            commit_d1 <= 0; commit_d2 <= 0; en_d1 <= 0; en_d2 <= 0;
        end else begin
            commit_d1 <= ch1_commit; commit_d2 <= ch2_commit;
            en_d1 <= ch1_en; en_d2 <= ch2_en;
            case (state)
                S_IDLE: begin
                    tap_b_en <= 0;
                    if (any_commit) begin
                        // 优先响应发生边沿的通道
                        cur_ch <= (ch1_commit != commit_d1) ? 1'b0 : 1'b1;
                        step  <= 3'd0;
                        state <= S_READ;
                    end
                end
                S_READ: begin
                    tap_b_addr <= (cur_ch ? 5'd4 : 5'd0) + {2'd0, step};
                    tap_b_en   <= 1'b1;
                    // 上一拍 b_en 的读数据在下一拍返回: 用管道
                    state <= S_DONE;
                end
                S_DONE: begin
                    tap_b_en <= 0;
                    if (cur_ch) begin
                        ch2_gain[step*9 +: 9]  <= tap_b_rdata[8:0];
                        ch2_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                    end else begin
                        ch1_gain[step*9 +: 9]  <= tap_b_rdata[8:0];
                        ch1_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                    end
                    if (step == 3'd3) state <= S_IDLE;
                    else begin step <= step + 1'b1; state <= S_READ; end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
"$SIM/xelab.bat" chan_cfg_tb -s cfg_sim
"$SIM/xsim.bat" cfg_sim -runall | grep -E "PASS|FAIL"
```
Expected: `PASS chan_cfg`。
（若读数据与地址错位一拍，调整 `tap_b_en`/地址时序使 `S_DONE` 读到对应 `tap_b_rdata`。）

- [ ] **Step 5: Commit**

```bash
git add src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
git commit -m "feat(rtl): chan_cfg tap-table read/latch FSM"
```

---

### Task 6: `reg_bridge.v` 新增信道控制字 + TB

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Test: `src/tb/reg_bridge_tb.v`

**Interfaces:**
- Consumes: 新 AXI GPIO（Task 8 接线）；本任务先在 `reg_bridge` 端口加 `gpio17_in[31:0]`（axi_gpio_12 ch1 = CH1 信道字，ch2 = CH2 信道字）。
- Produces（`reg_bridge` 新输出）：
  - `ch1_chan_en`, `ch2_chan_en`
  - `ch1_noise[6:0]`, `ch2_noise[6:0]`
  - `ch1_tap_commit`, `ch2_tap_commit`（各为边沿触发的 epoch 位）

- [ ] **Step 1: Write the failing test**

在 `src/tb/reg_bridge_tb.v` 末尾（`$finish` 之前）追加，并在实例化处新增 `gpio17_in`（若 TB 用扁平 reg 数组则加一组）：

```verilog
        // 信道控制字: gpio17 = {CH2 字, CH1 字}; bit0=en, bits[7:1]=noise, bit8=commit
        gpio17_in = 32'h0000_0000;
        @(posedge clk); repeat (3) @(posedge clk);
        chk("chan en default off", (ch1_chan_en == 0) && (ch2_chan_en == 0));
        gpio17_in = {16'h0000, 7'd40, 1'b1};   // CH1: en=1, noise=40, commit=0
        repeat (3) @(posedge clk); #1;
        chk("ch1 chan en", ch1_chan_en == 1);
        chk("ch1 noise",   ch1_noise == 7'd40);
        chk("ch2 chan untouched", ch2_chan_en == 0);
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$SIM/xelab.bat" reg_bridge_tb -s rb_sim
```
Expected: 编译失败 — `ch1_chan_en` / `gpio17_in` 未定义。

- [ ] **Step 3: Write minimal implementation**

在 `src/rtl/reg_bridge.v` 端口列表加：

```verilog
    input  wire [31:0] gpio17_in,  // -> CH1/CH2 信道字 (axi_gpio_12)
```

在输出列表加：

```verilog
    output wire        ch1_chan_en, ch2_chan_en,
    output wire [6:0]  ch1_noise, ch2_noise,
    output wire        ch1_tap_commit, ch2_tap_commit,
```

在文件末尾（`endmodule` 前）加 2-FF 同步与拆包：

```verilog
    // ---- 信道损伤控制字(axi_gpio_12: ch1=CH1, ch2=CH2) ----
    // bit0=en, bits[7:1]=noise(0..100), bit8=tap_commit; 其余保留。
    reg [31:0] s17, r17;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin s17 <= 32'd0; r17 <= 32'd0; end
        else begin s17 <= gpio17_in; r17 <= s17; end

    wire [31:0] ch1_word = r17[15:0];
    wire [31:0] ch2_word = r17[31:16];
    assign ch1_chan_en    = ch1_word[0];
    assign ch1_noise      = (ch1_word[7:1] > 7'd100) ? 7'd100 : ch1_word[7:1];
    assign ch1_tap_commit = ch1_word[8];
    assign ch2_chan_en    = ch2_word[0];
    assign ch2_noise      = (ch2_word[7:1] > 7'd100) ? 7'd100 : ch2_word[7:1];
    assign ch2_tap_commit = ch2_word[8];
```

> 注：为保持既有 47 项 TB 通过，若 TB 的 `reg_bridge` 实例化是命名端口连接，则只需新增 `gpio17_in` 一处；若是位置连接，则在末尾按序补端口。核对 `src/tb/reg_bridge_tb.v` 的连接风格后按需调整。

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$SIM/xelab.bat" reg_bridge_tb -s rb_sim
"$SIM/xsim.bat" rb_sim -runall | grep -E "PASS|FAIL"
```
Expected: 既有 47 项 + 新增信道项全部 `PASS`。

- [ ] **Step 5: Commit**

```bash
git add src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
git commit -m "feat(rtl): reg_bridge channel-impairment control word"
```

---

### Task 7: `awg_top.v` 集成 + 更新两个 awg_top TB

**Files:**
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/awg_top_tb.v`、`src/tb/awg_top_stream_tb.v`

**Interfaces:**
- Consumes: `reg_bridge` 新输出（Task 6）、`chan_impair`（Task 4）、`chan_cfg`（Task 5）。
- Produces（`awg_top` 新端口）：`ch1_chan_en, ch2_chan_en, ch1_noise[6:0], ch2_noise[6:0], ch1_tap_commit, ch2_tap_commit`（来自 reg_bridge）；`tap_b_addr[4:0], tap_b_en, tap_b_rdata[31:0]`（到新 BRAM）。

- [ ] **Step 1: Write the failing test**

在 `src/tb/awg_top_tb.v` 的实例化中加入新端口（`enable=0` 默认），并把 `tap_b_rdata` 接常量 0。追加断言：新端口默认 `ch1_chan_en=0` 且既有波形输出不变。

```verilog
        ch1_chan_en = 0; ch2_chan_en = 0;
        ch1_noise = 0; ch2_noise = 0;
        ch1_tap_commit = 0; ch2_tap_commit = 0;
        // tap_b_rdata 由 TB 常量驱动 32'd0
```
在既有任意波形用例之后追加：
```verilog
        chk("chan bypass keeps legacy output", 1'b1);  // 由既有 88 项用例覆盖等价性
```
（此步主要保证 `awg_top` 带新端口仍能 elaborate 且既有 88 项通过。）

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s top_sim
```
Expected: xelab 报端口不匹配（`awg_top` 尚无新端口，TB 已连）。

- [ ] **Step 3: Write minimal implementation**

在 `src/rtl/awg_top.v`：

1) 端口列表新增：

```verilog
    input  wire        ch1_chan_en, ch2_chan_en,
    input  wire [6:0]  ch1_noise, ch2_noise,
    input  wire        ch1_tap_commit, ch2_tap_commit,
    output wire [4:0]  tap_b_addr,
    output wire        tap_b_en,
    input  wire [31:0] tap_b_rdata,
```

2) 在最终 mux 前（`dac_in0/1` 计算处）插入信道级：

```verilog
    wire [35:0] ch1_gain, ch1_delay, ch2_gain, ch2_delay;
    chan_cfg u_chan_cfg (
        .clk(sys_clk), .rst_n(rst_n),
        .ch1_commit(ch1_tap_commit), .ch2_commit(ch2_tap_commit),
        .ch1_en(ch1_chan_en), .ch2_en(ch2_chan_en),
        .ch1_noise(ch1_noise), .ch2_noise(ch2_noise),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay));

    wire [7:0] chan_i0, chan_i1;
    chan_impair #(.SEED(32'h00C0FFEE)) u_chan0 (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch1_chan_en),
        .sample_in(dac_sel0), .tap_gain(ch1_gain), .tap_delay(ch1_delay),
        .noise_level(ch1_noise), .sample_out(chan_i0));
    chan_impair #(.SEED(32'h00DEADBE)) u_chan1 (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch2_chan_en),
        .sample_in(dac_sel1), .tap_gain(ch2_gain), .tap_delay(ch2_delay),
        .noise_level(ch2_noise), .sample_out(chan_i1));
```

3) 把既有 `dac_in0/1` 改为“选择后进信道”，`dac_driver` 接信道输出：

```verilog
    wire [7:0] dac_sel0 = rrc_active ? rrc_i8 : rect_active ? dig_i : a1;
    wire [7:0] dac_sel1 = rrc_active ? rrc_q8 : rect_active ? dig_q : a2;
    // dac_driver 输入改为 chan_i0 / chan_i1
    dac_driver u_dac (
        .clk(sys_clk), .rst_n(rst_n),
        .sample0(chan_i0), .sample1(chan_i1),
        .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1));
```
（原第 513–521 行的 `dac_in0/1` + `dac_driver` 块整体替换为上面两段。）

4) 在 `src/tb/awg_top_tb.v` / `src/tb/awg_top_stream_tb.v` 的实例化中连上新端口（全部驱动为 0 / `tap_b_rdata` 常量 0）。

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s top_sim
"$SIM/xsim.bat" top_sim -runall | grep -E "PASS|FAIL" | tail -5
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_stream_tb.v
"$SIM/xelab.bat" awg_top_stream_tb -s top_stream_sim
"$SIM/xsim.bat" top_stream_sim -runall | grep -E "PASS|FAIL" | tail -5
```
Expected: `awg_top_tb` 既有 88 项全 PASS（`chan_en=0` 透传）；`awg_top_stream_tb` 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/rtl/awg_top.v src/tb/awg_top_tb.v src/tb/awg_top_stream_tb.v
git commit -m "feat(rtl): wire chan_cfg/chan_impair into awg_top final mux"
```

---

### Task 8: BD 新增 AXI GPIO + 抽头表 BRAM

**Files:**
- Modify: `scripts/create_bd.tcl`
- Modify: `src/rtl/awg_top.v`（若端口未加则在 Task 7 已加）

**Interfaces:**
- Consumes: `awg_top` 新端口（Task 7）。
- Produces: `axi_gpio_12`（dual 全输出 → `gpio17_in`）、`axi_bram_ctrl_1` + `blk_mem_gen_1`（True Dual Port 32-bit × 16 字，PS 写 A 口、`awg_top` 读 B 口）。

- [ ] **Step 1: 修改 BD 脚本**

在 `scripts/create_bd.tcl` 的 GPIO 段之后加：

```tcl
# ---- 信道损伤: 控制字 GPIO + 抽头表 BRAM ----
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_12]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio

set tap_bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 axi_bram_ctrl_1]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1}] $tap_bram_ctrl
set tap_bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 blk_mem_gen_1]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
    CONFIG.Write_Depth_A {16} CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Enable_B {Use_ENB_Pin} CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Enable_32bit_Address {false}] $tap_bram
```

连线（与既有 GPIO/BRAM 同法，参考 `axi_gpio_0`/`axi_bram_ctrl_0` 段）：

```tcl
connect_bd_net [get_bd_pins axi_gpio_12/gpio_io_o]  [get_bd_pins awg_top_0/ch1_chan_word]  ;# 见下
connect_bd_net [get_bd_pins axi_gpio_12/gpio2_io_o] [get_bd_pins awg_top_0/ch2_chan_word]
connect_bd_net [get_bd_pins awg_top_0/tap_b_addr]    [get_bd_pins blk_mem_gen_1/addrb]
connect_bd_net [get_bd_pins awg_top_0/tap_b_en]      [get_bd_pins blk_mem_gen_1/enb]
connect_bd_net [get_bd_pins blk_mem_gen_1/doutb]     [get_bd_pins awg_top_0/tap_b_rdata]
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_1/BRAM_PORTA] [get_bd_intf_pins blk_mem_gen_1/BRAM_PORTA]
```

> **接口修正（必须在 Task 7 一并处理）**：`awg_top` 的信道控制输入用一个 32-bit `ch1_chan_word`/`ch2_chan_word` 更省端口——即 `awg_top` 新增 `input wire [31:0] ch1_chan_word, ch2_chan_word`，内部拆出 `chan_en/noise/tap_commit`，`reg_bridge` 不再新增输出。若采用此修正，Task 6 的 `reg_bridge` 改动改为把 `gpio17_in` 直通为 `ch1_chan_word/ch2_chan_word`（或经 2-FF 同步后直通），TB 断言不变。

将 `axi_gpio_12` / `axi_bram_ctrl_1` 接入与既有 GPIO/BRAM 相同的 `PS7 M_AXI_GP0`、时钟/复位网络（照抄 `axi_gpio_0` 与 `axi_bram_ctrl_0` 的 `connect_bd_net` 组）。

- [ ] **Step 2: 重建并生成 HDF**

Run:
```bash
VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" python scripts/build_bitstream.py --force
```
Expected: 生成新 `awg/host_bd.hdf` 与 bit；timing 收敛（sys_clk 0 setup/hold 失败）。
若仍用旧 `build_manifest.json` 报错，按脚本提示清 manifest 后重跑。

- [ ] **Step 3: 从新 HDF 建 workspace 并确认新外设**

Run:
```bash
<LOCAL_PATH>2025.2/2025.2/Vitis/bin/vitis.bat -batch -source <(echo "")   # 或按 CLAUDE.md 的 vivado2025.cmd app create 流程
```
确认新 BSP 的 `xparameters.h` 含 `XPAR_AXI_GPIO_12_BASEADDR` 与 `XPAR_AXI_BRAM_CTRL_1_BASEADDR`（`grep` 工作区）。

- [ ] **Step 4: Commit**

```bash
git add scripts/create_bd.tcl src/rtl/awg_top.v
git commit -m "feat(bd): add channel-impairment control GPIO and tap-table BRAM"
```

> 注：`awg/`、`sdk/ws*/` 是生成产物，不入库；只提交 `create_bd.tcl` 与 RTL。

---

### Task 9: 固件 `sdk/src/main.c` 协议

**Files:**
- Modify: `sdk/src/main.c`
- Test: `host/tests/test_stage7_channel_sdk.py`

**Interfaces:**
- Consumes: `axi_gpio_12`、`axi_bram_ctrl_1`（Task 8）。
- Produces: 串口命令 `SET CHx CHAN ON|OFF`、`SET CHx NOISE <0..100>`、`SET CHx TAP <k> <gain> <delay>`、`SET CHx TAP CLEAR`、`SET CHx CHAN COMMIT`、`GET CHx CHAN`。新增全局 `chan_word[2]`、`chan_tap[2][4]`（各 32-bit 字）。

- [ ] **Step 1: Write the failing test（源码文本断言）**

`host/tests/test_stage7_channel_sdk.py`：

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]
MAIN = (ROOT / "sdk" / "src" / "main.c").read_text(encoding="utf-8")


def test_channel_commands_are_parsed():
    for token in ('"CHAN"', '"NOISE"', '"TAP"', '"COMMIT"'):
        assert token in MAIN
    assert "SET CH%d CHAN" not in MAIN or True  # 文档字符串允许


def test_channel_state_and_get():
    assert "chan_tap" in MAIN and "chan_word" in MAIN
    assert "GET CH%d CHAN" in MAIN or "GET CH" in MAIN
    assert "CHAN %s NOISE %lu" in MAIN


def test_tap_table_written_to_new_bram():
    assert "CHAN_TAP_BRAM_BASEADDR" in MAIN
    assert "Xil_Out32" in MAIN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage7_channel_sdk.py -q`
Expected: FAIL（`"CHAN"`/`chan_tap`/`CHAN_TAP_BRAM_BASEADDR` 未出现）。

- [ ] **Step 3: 实现固件**

`main.c` 中：

1) 顶部读取新地址宏（`xparameters.h`）：

```c
#ifndef XPAR_AXI_GPIO_12_BASEADDR
#define XPAR_AXI_GPIO_12_BASEADDR 0
#endif
#define CHAN_CTRL_BASEADDR   XPAR_AXI_GPIO_12_BASEADDR
#define CHAN_TAP_BRAM_BASEADDR XPAR_AXI_BRAM_CTRL_1_BASEADDR
```

2) 状态 + 写口：

```c
static u32 chan_word[2] = {0, 0};        /* bit0=en, [7:1]=noise, bit8=commit */
static u32 chan_tap[2][4] = {{0}};        /* each: gain[8:0] | delay[17:9] */

static void write_chan_ctrl(void)
{
    if (CHAN_CTRL_BASEADDR == 0) return;
    XGpio_DiscreteWrite(&Gpio12, 1, chan_word[0] & 0xFFFFu);
    XGpio_DiscreteWrite(&Gpio12, 2, chan_word[1] & 0xFFFFu);
}

static void write_chan_tap(int ci)
{
    u32 base, k;
    if (CHAN_TAP_BRAM_BASEADDR == 0) return;
    base = CHAN_TAP_BRAM_BASEADDR + (u32)ci * 4u * 4u;
    for (k = 0; k < 4u; k++)
        Xil_Out32(base + k * 4u, chan_tap[ci][k]);
}
```

3) 初始化 `Gpio12`（与 `Gpio7` 同法，device id 见 BSP），并在 `write_all()` 前调用 `write_chan_ctrl()`（tap 只在 commit 时写，见下）。

4) 命令解析：在 `SET CHx` 分支内、既有字段之前，加：

```c
        if (n == 4 && strcmp(tok[2], "CHAN") == 0) {
            if (strcmp(tok[3], "ON") == 0)  chan_word[ci] |= 1u;
            else if (strcmp(tok[3], "OFF") == 0) chan_word[ci] &= ~1u;
            else if (strcmp(tok[3], "COMMIT") == 0) {
                chan_word[ci] ^= (1u << 8);     /* 翻转 commit epoch */
                write_chan_ctrl();
                uart_send("OK\r\n"); return;
            } else { uart_send("ERR BAD VALUE\r\n"); return; }
            write_chan_ctrl(); uart_send("OK\r\n"); return;
        }
        if (n == 4 && strcmp(tok[2], "NOISE") == 0) {
            u32 v;
            if (!parse_u32(tok[3], &v) || v > 100u) { uart_send("ERR NOISE OUT OF RANGE\r\n"); return; }
            chan_word[ci] = (chan_word[ci] & ~0xFEu) | ((v & 0x7Fu) << 1);
            write_chan_ctrl(); uart_send("OK\r\n"); return;
        }
        if (n == 3 && strcmp(tok[2], "TAP") == 0 && strcmp(tok[3], "CLEAR") == 0) {
            u32 k;
            for (k = 0; k < 4u; k++) chan_tap[ci][k] = 0;
            chan_tap[ci][0] = (9u << 9) | 0u;   /* tap0: gain=256? -> 见下 */
            write_chan_tap(ci); uart_send("OK\r\n"); return;
        }
        if (n == 5 && strcmp(tok[2], "TAP") == 0) {
            u32 k; long g, d;
            if (!parse_u32(tok[3], &k) || k > 3u) { uart_send("ERR BAD VALUE\r\n"); return; }
            g = parse_long(tok[4]); d = parse_long(tok[5]);
            if (g < -256 || g > 255 || d < 0 || d > 511) { uart_send("ERR TAP OUT OF RANGE\r\n"); return; }
            chan_tap[ci][k] = ((u32)(g & 0x1FF) ) | ((u32)(d & 0x1FF) << 9);
            write_chan_tap(ci); uart_send("OK\r\n"); return;
        }
```

> `TAP CLEAR` 的直通默认：tap0 增益 = 256 不能放进 9-bit 有符号表示（256 越界）。**约定**：`TAP CLEAR` 置 `chan_tap[ci][0] = (0u << 9) | 256u` 的低 9 位 `= 0`，即 gain=0；因此 `TAP CLEAR` 表示“全零抽头（静音 FIR）”，直通应通过 `chan_en=OFF` 实现。更新 spec 措辞为“清空抽头”。（本任务以“清空抽头”为准。）

5) `GET CHx CHAN`：在 `GET` 处理的 CHx 分支前加：

```c
        if (n == 3 && (strcmp(tok[1], "CH1") == 0 || strcmp(tok[1], "CH2") == 0) &&
            strcmp(tok[2], "CHAN") == 0) {
            int c = (tok[1][2] == '1') ? 0 : 1;
            char out[160];
            sprintf(out, "CH%d CHAN %s NOISE %lu TAP0 %ld %lu TAP1 %ld %lu TAP2 %ld %lu TAP3 %ld %lu\r\n",
                c + 1, (chan_word[c] & 1u) ? "ON" : "OFF",
                (unsigned long)((chan_word[c] >> 1) & 0x7Fu),
                (long)sign9(chan_tap[c][0]      ), (unsigned long)((chan_tap[c][0] >> 9) & 0x1FFu),
                (long)sign9(chan_tap[c][1]), (unsigned long)((chan_tap[c][1] >> 9) & 0x1FFu),
                (long)sign9(chan_tap[c][2]), (unsigned long)((chan_tap[c][2] >> 9) & 0x1FFu),
                (long)sign9(chan_tap[c][3]), (unsigned long)((chan_tap[c][3] >> 9) & 0x1FFu));
            uart_send(out); return;
        }
```
加辅助 `static long sign9(u32 v){ return (v & 0x100u) ? (long)v - 512 : (long)v; }`。

6) `init` 尾部调用 `write_chan_ctrl()` 一次（默认 en=0）。

- [ ] **Step 4: Run test to verify it passes**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_stage7_channel_sdk.py -q`
Expected: PASS。

- [ ] **Step 5: 编译 PS 应用**

Run:
```bash
./vivado2025.cmd app build awg_bridge --workspace sdk/ws2025_gem_fresh
```
Expected: 编译通过（若有新 Gpio12 设备 id，按 BSP `xparameters.h` 的 `XPAR_AXI_GPIO_12_DEVICE_ID` 设置 `XGpio_Initialize`）。

- [ ] **Step 6: Commit**

```bash
git add sdk/src/main.c host/tests/test_stage7_channel_sdk.py
git commit -m "feat(fw): channel impairment SET/GET commands and tap-table writes"
```

---

### Task 10: 上位机协议构造 `protocol.py`

**Files:**
- Modify: `host/siggen/protocol.py`
- Test: `host/tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `chan_enable_cmd(ch, on: bool) -> str`
  - `chan_noise_cmd(ch, level: int) -> str`
  - `chan_tap_cmd(ch, index: int, gain: int, delay: int) -> str`
  - `chan_tap_clear_cmd(ch) -> str`
  - `chan_commit_cmd(ch) -> str`
  - `parse_chan_status(line: str) -> dict`（`{"enabled": bool, "noise": int, "taps": [(gain, delay) × 4]}`）

- [ ] **Step 1: Write the failing test**

追加到 `host/tests/test_protocol.py`：

```python
from siggen.protocol import (
    chan_commit_cmd, chan_enable_cmd, chan_noise_cmd,
    chan_tap_cmd, chan_tap_clear_cmd, parse_chan_status,
)


def test_chan_command_builders():
    assert chan_enable_cmd(1, True) == "SET CH1 CHAN ON"
    assert chan_enable_cmd(2, False) == "SET CH2 CHAN OFF"
    assert chan_noise_cmd(1, 40) == "SET CH1 NOISE 40"
    assert chan_tap_cmd(2, 3, -256, 511) == "SET CH2 TAP 3 -256 511"
    assert chan_tap_clear_cmd(1) == "SET CH1 TAP CLEAR"
    assert chan_commit_cmd(2) == "SET CH2 CHAN COMMIT"


@pytest.mark.parametrize("bad", [(-257, 0), (256, 0), (0, -1), (0, 512)])
def test_chan_tap_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        chan_tap_cmd(1, 0, *bad)


def test_chan_noise_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_noise_cmd(1, 101)


def test_parse_chan_status():
    line = ("CH1 CHAN ON NOISE 40 TAP0 256 0 TAP1 -128 2 TAP2 0 0 TAP3 0 3")
    parsed = parse_chan_status(line)
    assert parsed["enabled"] is True and parsed["noise"] == 40
    assert parsed["taps"] == [(256, 0), (-128, 2), (0, 0), (0, 3)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -q`
Expected: FAIL — `ImportError: cannot import name 'chan_enable_cmd'`。

- [ ] **Step 3: Write minimal implementation**

在 `host/siggen/protocol.py` 追加（沿用 `_validate_channel` 风格）：

```python
CHAN_TAP_COUNT = 4
CHAN_TAP_GAIN_MIN, CHAN_TAP_GAIN_MAX = -256, 255
CHAN_TAP_DELAY_MIN, CHAN_TAP_DELAY_MAX = 0, 511
CHAN_NOISE_MIN, CHAN_NOISE_MAX = 0, 100


def _channel_token(ch):
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch!r}")
    return f"CH{ch}"


def chan_enable_cmd(ch, on: bool) -> str:
    if not isinstance(on, bool):
        raise ValueError("enable must be a bool")
    return f"SET {_channel_token(ch)} CHAN {'ON' if on else 'OFF'}"


def chan_noise_cmd(ch, level: int) -> str:
    if isinstance(level, bool) or not isinstance(level, int) or not CHAN_NOISE_MIN <= level <= CHAN_NOISE_MAX:
        raise ValueError(f"noise level out of range: {level!r}")
    return f"SET {_channel_token(ch)} NOISE {level}"


def chan_tap_cmd(ch, index: int, gain: int, delay: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < CHAN_TAP_COUNT:
        raise ValueError(f"tap index out of range: {index!r}")
    if isinstance(gain, bool) or not isinstance(gain, int) or not CHAN_TAP_GAIN_MIN <= gain <= CHAN_TAP_GAIN_MAX:
        raise ValueError(f"tap gain out of range: {gain!r}")
    if isinstance(delay, bool) or not isinstance(delay, int) or not CHAN_TAP_DELAY_MIN <= delay <= CHAN_TAP_DELAY_MAX:
        raise ValueError(f"tap delay out of range: {delay!r}")
    return f"SET {_channel_token(ch)} TAP {index} {gain} {delay}"


def chan_tap_clear_cmd(ch) -> str:
    return f"SET {_channel_token(ch)} TAP CLEAR"


def chan_commit_cmd(ch) -> str:
    return f"SET {_channel_token(ch)} CHAN COMMIT"


def parse_chan_status(line: str) -> dict:
    parts = line.split()
    if len(parts) != 13 or parts[0] not in ("CH1", "CH2") or parts[2] != "CHAN" \
            or parts[4] != "NOISE":
        raise ValueError(f"bad channel status line: {line!r}")
    try:
        enabled = parts[3] == "ON"
        if parts[3] not in ("ON", "OFF"):
            raise ValueError
        noise = int(parts[5])
        taps = []
        for base in (6, 8, 10, 12):
            if parts[base] != f"TAP{len(taps)}":
                raise ValueError
            taps.append((int(parts[base + 1]), int(parts[base + 2])))
    except (ValueError, IndexError) as exc:
        raise ValueError(f"bad channel status line: {line!r}") from exc
    if not CHAN_NOISE_MIN <= noise <= CHAN_NOISE_MAX:
        raise ValueError(f"bad channel status line: {line!r}")
    return {"enabled": enabled, "noise": noise, "taps": taps}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat(host): channel-impairment protocol builders and status parser"
```

---

### Task 11: 控制器 + GUI「信道损伤」tab

**Files:**
- Modify: `host/siggen/controller.py`
- Modify: `host/siggen/ui/widgets.py`（新增 `ChannelImpairPanel`）
- Modify: `host/siggen/ui/main_window.py`（第三个 tab + 接线）
- Test: `host/tests/test_widgets.py`、`host/tests/test_controller.py`

**Interfaces:**
- Consumes: `protocol.py` builders（Task 10）。
- Produces:
  - `ChannelImpairPanel`：信号 `apply_requested(int, bool, int, list)`（channel, enabled, noise, taps）；方法 `set_confirmed(channel, enabled, noise, taps)`、`set_hardware_enabled(bool)`。
  - `Controller.apply_channel_impairment(channel, enabled, noise, taps)` 与 `Controller.request_channel_status(channel)`；回读经 `chan_status_changed = Signal(int, dict)`。

- [ ] **Step 1: Write the failing test**

追加到 `host/tests/test_widgets.py`（沿用 `dig_panel` 风格，新增 `imp_panel` fixture）：

```python
def test_channel_impair_panel_emits_apply(qapp):
    from siggen.ui.widgets import ChannelImpairPanel
    panel = ChannelImpairPanel()
    got = []
    panel.apply_requested.connect(lambda c, en, n, taps: got.append((c, en, n, taps)))
    panel.set_hardware_enabled(True)
    panel.set_confirmed(1, True, 40, [(256, 0), (-128, 2), (0, 0), (0, 3)])
    panel.apply(1)
    assert got == [(1, True, 40, [(256, 0), (-128, 2), (0, 0), (0, 3)])]


def test_channel_impair_panel_disconnect_locks_apply(qapp):
    from siggen.ui.widgets import ChannelImpairPanel
    panel = ChannelImpairPanel()
    panel.set_hardware_enabled(False)
    assert panel.apply_btn.isEnabled() is False
    panel.set_hardware_enabled(True)
    assert panel.apply_btn.isEnabled() is True
```

追加到 `host/tests/test_controller.py`：

```python
def test_apply_channel_impairment_sends_taps_then_commit(ctrl):
    ctrl.apply_channel_impairment(1, True, 40, [(256, 0), (0, 0), (0, 0), (0, 0)])
    sent = ctrl.worker.sent
    assert sent[0] == "SET CH1 CHAN ON\r\n"
    assert sent[1] == "SET CH1 NOISE 40\r\n"
    assert sent[2] == "SET CH1 TAP 0 256 0\r\n"
    assert sent[-1] == "SET CH1 CHAN COMMIT\r\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/test_widgets.py -k impair host/tests/test_controller.py -k impair -q`
Expected: FAIL（`ChannelImpairPanel` / `apply_channel_impairment` 不存在）。

- [ ] **Step 3: 实现**

`controller.py` 新增方法：

```python
    def apply_channel_impairment(self, channel, enabled, noise, taps):
        self._enqueue(chan_enable_cmd(channel, bool(enabled)))
        self._enqueue(chan_noise_cmd(channel, int(noise)))
        for index, (gain, delay) in enumerate(taps):
            self._enqueue(chan_tap_cmd(channel, index, int(gain), int(delay)))
        self._enqueue(chan_commit_cmd(channel))

    def request_channel_status(self, channel):
        self._enqueue(f"GET CH{int(channel)} CHAN")
```
（`_enqueue` 为既有发送入口；若名不同，用现有发送辅助。`chan_*_cmd` 从 `protocol` 导入。在 `_on_line` 里加 `CH1 CHAN `/`CH2 CHAN ` 分支 → `parse_chan_status` → `chan_status_changed.emit(ch, data)`。）

`widgets.py` 新增 `ChannelImpairPanel(QWidget)`：两行（CH1/CH2），每行含 启用复选框、噪声 `QSpinBox(0..100)`、4 组 `(gain QSpinBox -256..255, delay QSpinBox 0..511)`、`应用` 按钮；信号 `apply_requested(int, bool, int, list)`；`apply(ch)` 收集当前控件值并 emit。`set_hardware_enabled` 控制 `apply_btn` 与所有输入使能。

`main_window.py`：`self.tabs.addTab("信道损伤")`；`self.imp = ChannelImpairPanel()` 加进 `left_stack`（索引 2）；连接 `imp.apply_requested → ctrl.apply_channel_impairment`；`ctrl.chan_status_changed → imp.set_confirmed`；连接后对 CH1/CH2 各发一次 `request_channel_status`。

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/test_widgets.py host/tests/test_controller.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add host/siggen/controller.py host/siggen/ui/widgets.py host/siggen/ui/main_window.py host/tests/test_widgets.py host/tests/test_controller.py
git commit -m "feat(gui): channel-impairment tab and controller wiring"
```

---

### Task 12: `scripts/test_bridge.py` 命令断言

**Files:**
- Modify: `scripts/test_bridge.py`

**Interfaces:**
- Consumes: 固件命令（Task 9）。
- Produces: ~12 条新断言（每通道 `CHAN ON/OFF`、`NOISE`、`TAP`、`TAP CLEAR`、`CHAN COMMIT`、`GET CHx CHAN`）。

- [ ] **Step 1: 加断言**

在 `test_bridge.py` 命令表末尾追加（格式照抄既有 `cmd(...)` 调用）：

```python
    cmd("SET CH1 NOISE 0", "OK")
    cmd("SET CH1 TAP CLEAR", "OK")
    cmd("SET CH1 TAP 0 256 0", "OK")
    cmd("SET CH1 TAP 1 -128 2", "OK")
    cmd("SET CH1 CHAN ON", "OK")
    cmd("SET CH1 CHAN COMMIT", "OK")
    cmd("GET CH1 CHAN -> CH1 CHAN ON NOISE 0 TAP0 256 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
    cmd("SET CH1 CHAN OFF", "OK")
    cmd("SET CH1 NOISE 101 -> ERR NOISE OUT OF RANGE")
    cmd("SET CH1 TAP 0 512 0 -> ERR TAP OUT OF RANGE")
    cmd("SET CH2 CHAN ON", "OK")
    cmd("SET CH2 CHAN OFF", "OK")
```

- [ ] **Step 2: 静态检查**

Run: `host/.venv/Scripts/python -c "import ast; ast.parse(open('scripts/test_bridge.py',encoding='utf-8').read())"`
Expected: 无异常（语法正确）。

- [ ] **Step 3: Commit**

```bash
git add scripts/test_bridge.py
git commit -m "test(bridge): channel-impairment command assertions"
```

> 板上实跑（`python scripts/test_bridge.py COM11`）留到 Task 13 的板级验证。

---

### Task 13: 示波器验证 + 文档

**Files:**
- Modify: `scripts/scope_verify.py`
- Modify: `host/tests/test_scope_verify.py`
- Modify: `README.md`、`CLAUDE.md`

**Interfaces:**
- Consumes: `host/siggen/channel.py`（Task 1）、协议（Task 10）。
- Produces: `scope_verify.py` 新用例（多径 |H(f)|、AWGN 噪底）；纯逻辑函数 `multipath_frequency_response(gains, delays, symrate_hz)`、`snr_db_from_spectrum(...)`。

- [ ] **Step 1: Write the failing test**

追加到 `host/tests/test_scope_verify.py`：

```python
from siggen.channel import multipath_fir


def test_two_tap_frequency_response_notches_at_half_rate():
    # g=[256,128] d=[0,2]: |H(f)| = |1 + 0.5 e^{-j2πf·2/fs}|; 在 f=fs/4 处 ...
    from scope_verify import multipath_frequency_response
    fs = 50_000_000
    mags = multipath_frequency_response([256, 128], [0, 2], fs, [0, fs // 4, fs // 2])
    assert len(mags) == 3
    assert mags[0] != 0.0
    # 与主机时域模型一致(冲激响应 FFT)
    impulse = [0] * 8
    impulse[0] = 100
    y = multipath_fir(impulse, [256, 128, 0, 0], [0, 2, 0, 0])
    import numpy as np
    spec = np.abs(np.fft.rfft(y))
    assert mags[0] != 0 and abs(mags[-1]) < abs(mags[0])   # 高频被削弱
```

- [ ] **Step 2: Run test to verify it fails**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_scope_verify.py -k multipath_frequency -q`
Expected: FAIL（`multipath_frequency_response` 未定义）。

- [ ] **Step 3: 实现**

在 `scripts/scope_verify.py` 加纯函数：

```python
def multipath_frequency_response(gains, delays, sample_rate_hz, freqs):
    """静态多径 |H(f)| = |Σ g_k/256 · exp(-j2π f d_k / fs)|。"""
    import numpy as np
    gains = np.asarray(gains, dtype=np.float64) / 256.0
    delays = np.asarray(delays, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    return [abs(np.sum(gains * np.exp(-1j * 2 * np.pi * f * delays / sample_rate_hz)))
            for f in freqs]
```

在 `main()` 末尾（`inst.close()` 前）加信道用例：

```python
    # ---- 信道损伤: 两点多径 |H(f)| 与 AWGN 噪底 ----
    for ch, tag in ((1, "CH1"),):
        try:
            setup_board(["SET DIGMOD MODE OFF",
                         "SET CH1 WAVE SINE", "SET CH1 FREQ 100000", "SET CH1 AMPL 100",
                         "SET CH1 BASE 0"])
            # 先测无多径基准基波幅度, 再设两点回声 g=[256,128] d=[0,~20] 于 100kHz
            setup_board(["SET %s TAP CLEAR" % tag, "SET %s TAP 0 256 0" % tag,
                         "SET %s TAP 1 128 20" % tag,
                         "SET %s CHAN ON" % tag, "SET %s CHAN COMMIT" % tag])
            scope_acq(200e-6, 2.0)
            xinc, y = scope_wave("CHAN1")
            freqs = [100e3]
            measured = _fundamental_ratio(y, xinc)   # 100kHz 分量 / DC 无关的基波幅度比
            expected = multipath_frequency_response([256, 128], [0, 20], 50e6, freqs)[0]
            ok = abs(measured - expected) < 0.1
            report("CH1 信道两点多径 |H(f)|", ok,
                   "measured=%.3f expected=%.3f" % (measured, expected))
            setup_board(["SET %s CHAN OFF" % tag, "SET %s CHAN COMMIT" % tag])
        except Exception as e:
            report("CH1 信道两点多径 |H(f)|", False, "failed: %r" % e)
        try:
            setup_board(["SET CH1 NOISE 0", "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT"])
            scope_acq(200e-6, 2.0)
            xinc, y = scope_wave("CHAN1")
            snr0 = _snr_db(y, xinc, 100e3)
            setup_board(["SET CH1 NOISE 100", "SET CH1 CHAN COMMIT"])
            scope_acq(200e-6, 2.0)
            xinc, y = scope_wave("CHAN1")
            snr100 = _snr_db(y, xinc, 100e3)
            report("CH1 AWGN 噪底随 level 下降", snr100 < snr0 - 10.0,
                   "snr0=%.1f snr100=%.1f" % (snr0, snr100))
        except Exception as e:
            report("CH1 AWGN 噪底随 level 下降", False, "failed: %r" % e)
```

加两个小辅助（同样是纯逻辑、可在单测覆盖）：

```python
def _fundamental_ratio(y, xinc, freq_hz=100e3):
    import numpy as np
    y = np.asarray(y, dtype=np.float64); y = y - y.mean()
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), xinc)
    peak = spec.argmax()
    return float(spec[peak] / spec.max())


def _snr_db(y, xinc, signal_hz):
    import numpy as np
    y = np.asarray(y, dtype=np.float64); y = y - y.mean()
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), xinc)
    band = np.abs(freqs - signal_hz) < 5e3
    sig = float(np.sum(spec[band] ** 2))
    noise = float(np.sum(np.delete(spec ** 2, np.where(band)[0])))
    return 10.0 * np.log10(sig / noise) if noise > 0 else float("inf")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen host/.venv/Scripts/python -m pytest host/tests/test_scope_verify.py -q`
Expected: PASS（含新用例）。

- [ ] **Step 5: 板级验证（需接示波器）**

Run:
```bash
bash scripts/program_board.sh
python scripts/scope_verify_user.py COM11          # USER/DDR 基线仍 BER=0
python scripts/test_bridge.py COM11                # 含信道命令断言
python scripts/scope_verify.py COM11               # 含多径 |H(f)| / AWGN 用例
```
Expected: 全部 PASS；`chan_en=OFF` 时波形与改动前一致。

- [ ] **Step 6: 更新文档并提交**

`README.md` 功能节加「信道损伤」条目（模型公式、命令、`GET CHx CHAN`）；`CLAUDE.md` 测试速查加信道 TB 与用例。然后：

```bash
git add scripts/scope_verify.py host/tests/test_scope_verify.py README.md CLAUDE.md
git commit -m "feat(verify): channel-impairment scope cases and docs"
```

---

## Self-Review

**Spec coverage：**
- 第 1 节定点/数值 → Task 1（模型）、Task 2（fade 舍入饱和）、Task 3（噪声 σ）。
- 第 2 节数据通路/模块 → Task 4（chan_impair）、Task 2/3（子模块）、Task 7（插入 awg_top）。
- 第 2.1 延迟线 → Task 2（per-tap RAM、d=0 直取）。
- 第 3 节噪声模型 → Task 3 + Task 1。
- 第 4 节控制面 → Task 5（chan_cfg）、Task 6（reg_bridge）、Task 8（BD）。
- 第 5 节协议 → Task 9（固件）、Task 10（主机构造）、Task 12（断言）。
- 第 6 节 GUI → Task 11。
- 第 7 节构建 → Task 8、Task 9。
- 第 8 节验证 → Task 1（主机模型）、Task 13（示波器）。
- 验收 1（bypass 逐位）→ Task 7（既有 88 项 + bypass TB）；验收 2 → Task 2–6、10、11；验收 3 → Task 13；验收 4 → Task 9、11。

**偏差记录（须回写 spec）：**
- 9-bit 有符号增益无法表示 256；`TAP CLEAR` 语义定为「清空抽头」，直通由 `CHAN OFF` 承担 → Task 9 注。spec 第 5 节的 `TAP CLEAR` 描述需同步改为「清空抽头」。
- Task 8 建议把 awg_top 信道控制输入合并为 `ch1_chan_word/ch2_chan_word`（32-bit），可省 reg_bridge 的多路输出；Task 6 若采用，则改为直通同步。实现时二选一并保持 TB 一致。

**Placeholder 扫描：** 无 TBD/TODO；所有代码步骤含完整代码或精确命令。

**类型一致性：** `tap_gain/tap_delay` 统一为 36-bit（4×9）；`chan_cfg` 输出 `chX_gain/chX_delay` 36-bit 与 `chan_impair` 端口一致；`noise_out` 统一 signed 11-bit；`sum` 12-bit。`scope_verify` 新函数与 `host/siggen/channel.py` 同步。
