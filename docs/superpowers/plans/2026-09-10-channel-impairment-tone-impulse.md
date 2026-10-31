# 信道损伤扩展 A 组（窄带单音 + 周期冲激干扰）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给两个 DAC 通道各加一路可数值配置的窄带单音干扰与周期冲激干扰，接在既有 `fade_fir` 之后、与 AWGN 同一加法节点。

**Architecture:** 新增 `tone_gen.v`（24 位相位累加器 + 复用既有 `sine_rom`）与 `impulse_gen.v`（周期/宽度计数器）两个模块，各实例化两份（CH1/CH2），输出与 `fade_fir`/`noise_gen` 在 3 拍处对齐后相加。参数放抽头表 BRAM 的预留字 12-15，由 `chan_cfg` 的状态机扩到 16 字一并排空；使能位用 `axi_gpio_12` 的空闲位。**不改 BD。**

**Tech Stack:** Verilog-2001（纯 RTL，无新 IP）、Vivado 2025.2 XSIM、Zynq-7020 裸机 C（`sdk/src/main.c`）、Python 3（上位机 + 测试）、DSOX1102A 示波器闭环。

**Spec:** `docs/superpowers/specs/2026-09-10-channel-impairment-extension-design.md`

## Global Constraints

- **仿真工具**：`SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"`；TB 流程为
  `"$SIM/xvlog.bat" --sv <files>` → `"$SIM/xelab.bat" <tb> -s <sim>` →
  `"$SIM/xsim.bat" <sim> -runall`；输出必须含 `PASS <模块名>`。
- **上位机测试**：`host/.venv/Scripts/python -m pytest host/tests/`。
- **时序（最高优先级约束）**：当前 **WNS 仅 0.071 ns**。所有新算术必须
  **逐级注册、用显式平衡加法树**，**严禁 DSP48 `PCOUT→PCIN` 级联** ——
  `fade_fir.v:54` 与 `rrc_filter.v` 都因串行级联崩过时序，必须照抄它们的模式。
- **`chan_impair` 端到端延时必须保持 3 拍**：既有 `chan_impair_tb` 断言
  `ov[n+3]` 对应输入 `x[n]`。新增干扰源必须对齐到 3 拍，不得改变既有延时。
- **`enable=0` 必须逐位透传**：这是既有 56 项示波器用例不回归的前提。
- **不改 BD**：`scripts/create_bd.tcl`、`scripts/ps7_props.tcl` 不动 →
  正常 `python scripts/build_bitstream.py`（**不要** `--force`），不需要重新生成 BSP。
- **饱和边界是 ±127**（镜像 `rrc_to_dac`），不是 8-bit 的 −128。
- **提交信息**：Conventional Commits 风格英文主题行，正文说明"为什么"，
  结尾 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。
- **参数约定**：`ampl` 为"百分比满幅"（0..100，与既有 `NOISE` 等级同约定）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/rtl/tone_gen.v` | **新建**。单音干扰：24 位相位累加器 + `sine_rom` + 幅度缩放，3 拍流水 |
| `src/rtl/impulse_gen.v` | **新建**。周期冲激：单个周期计数器 + 宽度比较 + 幅度缩放，3 拍流水 |
| `src/tb/tone_gen_tb.v` | **新建**。对 host golden 逐值比对 |
| `src/tb/impulse_gen_tb.v` | **新建**。计数/宽度/幅度边界 |
| `host/siggen/channel.py` | **修改**。新增 `tone_samples()` / `impulse_samples()`（RTL 的整数镜像） |
| `host/tests/test_channel.py` | **修改**。新增模型单测（先写失败测试） |
| `src/rtl/chan_impair.v` | **修改**。新增 tone/impulse 端口，加法节点扩为四项 |
| `src/tb/chan_impair_tb.v` | **修改**。新增干扰项用例，并钉死 3 拍延时不变 |
| `src/rtl/chan_cfg.v` | **修改**。字表排空从 4 字扩到覆盖 8..15，新增参数字输出 |
| `src/tb/chan_cfg_tb.v` | **修改**。新增字 12-15 的锁存用例 |
| `src/rtl/reg_bridge.v` | **修改**。控制字新增使能位解码 |
| `src/rtl/awg_top.v` | **修改**。接线新参数与使能位 |
| `sdk/src/main.c` | **修改**。新增命令处理、影子数组、BRAM 写入、`GET CHx CHAN` 扩展 |
| `host/siggen/protocol.py` | **修改**。新增命令构造器 + 状态行解析（含 17-token 兼容） |
| `host/tests/test_protocol.py` | **修改**。构造器与解析单测 |
| `scripts/test_bridge.py` | **修改**。新增命令断言 |
| `scripts/scope_verify.py` | **修改**。新增两个示波器用例 |
| `README.md` / `CLAUDE.md` | **修改**。协议说明与计数 |

---

### Task 1: 主机信道模型（单音 + 冲激）

先把整数模型写出来，后面 RTL 的 TB 用它做 golden。**这是 TDD 的第一步。**

**Files:**
- Modify: `host/siggen/channel.py`
- Test: `host/tests/test_channel.py`

**Interfaces:**
- Consumes: `host/siggen/channel.py` 既有 `round_shift_signed`、`_round_div_signed`、`_clamp`、`DAC_MIN/DAC_MAX`
- Produces:
  - `TONE_ACC_BITS = 24`、`TONE_ROM = <256 项 8-bit 有符号正弦表，与 src/rtl/sine_rom.v 同值>`
  - `tone_samples(count, freq_hz, ampl, sample_rate_hz)` → `list[int]`（8-bit 有符号域）
  - `IMPULSE_MAX_PERIOD = 65535`、`IMPULSE_MAX_WIDTH = 255`
  - `impulse_samples(count, period, width, ampl)` → `list[int]`

- [ ] **Step 1: 写失败测试**

追加到 `host/tests/test_channel.py`：

```python
from siggen.channel import (
    IMPULSE_MAX_PERIOD,
    IMPULSE_MAX_WIDTH,
    TONE_ROM,
    impulse_samples,
    tone_samples,
)


def test_tone_rom_matches_rtl_sine_rom_table():
    # sine_rom.v 由 tools/gen_sine.py 生成: round(127*sin(2*pi*i/256)), 取低 8 位
    import math
    expected = [round(127 * math.sin(2.0 * math.pi * i / 256)) & 0xFF for i in range(256)]
    assert TONE_ROM == expected


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_channel.py -v`
Expected: FAIL —`ImportError: cannot import name 'TONE_ROM'`

- [ ] **Step 3: 实现最简模型**

追加到 `host/siggen/channel.py`：

```python
import math

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

    RTL 结构: acc[23:0] 每拍 += fword; ROM 同步读 dout <= mem[acc[23:16]];
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
    fword = int(round(int(freq_hz) * (1 << TONE_ACC_BITS) / float(sample_rate_hz)))
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_channel.py -v`
Expected: PASS（既有用例也全部仍通过）

- [ ] **Step 5: 提交**

```bash
git add host/siggen/channel.py host/tests/test_channel.py
git commit -m "feat(host): integer models for tone and impulse interference

Mirrors the RTL these will be checked against, same as multipath_fir and
noise_samples. The tone model encodes the RTL's synchronous ROM read
explicitly: addr[n] comes from acc[n-1], so a serial-latency mismatch
between model and RTL shows up as a test failure rather than a silent
constant phase offset.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: `tone_gen.v` + TB

**Files:**
- Create: `src/rtl/tone_gen.v`
- Test: `src/tb/tone_gen_tb.v`

**Interfaces:**
- Consumes: `src/rtl/sine_rom.v`（既有，`addr[7:0] → dout[7:0]`，**组合**读，见 Step 3 注释）
- Produces: 模块 `tone_gen`，端口见 Step 3，输出 `output reg signed [7:0] tone_out`。
  **时序约定（已由控制器仿真复核，是本任务最容易搞错的一处）**：本模块没有数据输入，
  所以"延时"只体现在它自身的索引约定上。把复位释放后的第一个 posedge 记为 `k=0`，则

  ```
  k=0,1,2      : tone_out = 0            (被 enable 的 3 级流水门控)
  k>=3         : tone_out[k] = round(ampl × ROM[((k-2)·fword)>>16] / 100)
  host 模型    : tone_samples(..., )[n]  ==  k = n+1 处的 tone_out
  ```

  索引偏移是 **1**（不是 2）：模型的 `n=0` 项是它对 `acc=0` 的重复，与 `n=1` 同值。
  TB 因此"复位释放后先空等 1 个 posedge（k=0），再从 k=1 起逐拍比对"。

- [ ] **Step 1: 写失败 TB**

创建 `src/tb/tone_gen_tb.v`。goldens 由 Task 1 的模型算出（**已算好，直接照抄**，
Step 4 只做复核）：

```verilog
`timescale 1ns / 1ps
// tone_gen TB: 与 host/siggen/channel.py::tone_samples 逐值一致。
// 时序约定: 复位释放后第一个 posedge 记为 k=0; 模型的 out[n] 对应 k=n+1。
// 故复位释放后先空等 1 个 posedge, 再从 k=1 起比对 12 个样点。
// 复位用 negedge do_reset 释放(与下一个 posedge 隔半个周期), 避免采样竞争 ——
// 在 posedge 上直接给 rst_n 会让不同次运行相差一拍(noise_gen_tb.v 的 do_reset 同法)。
module tone_gen_tb;
    reg clk = 0, rst_n = 0, enable = 0;
    reg [23:0] fword = 24'd0;
    reg [6:0]  ampl  = 7'd0;
    wire signed [7:0] tone_out;

    tone_gen uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                  .fword(fword), .ampl(ampl), .tone_out(tone_out));

    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer i = 0;

    // golden 表: 三段各 12 项
    //   [0:11]  100 kHz, ampl 40  -> tone_samples(12, 100000, 40, 50e6)
    //   [12:23] 100 kHz, ampl 100 -> tone_samples(12, 100000, 100, 50e6)
    //   [24:35] 10 MHz,  ampl 40  -> tone_samples(12, 10000000, 40, 50e6)
    reg signed [7:0] gol [0:35];
    initial begin
        gol[ 0]=8'sd0;   gol[ 1]=8'sd0;   gol[ 2]=8'sd0;   gol[ 3]=8'sd1;
        gol[ 4]=8'sd1;   gol[ 5]=8'sd2;   gol[ 6]=8'sd2;   gol[ 7]=8'sd4;
        gol[ 8]=8'sd4;   gol[ 9]=8'sd5;   gol[10]=8'sd5;   gol[11]=8'sd6;
        gol[12]=8'sd0;   gol[13]=8'sd0;   gol[14]=8'sd0;   gol[15]=8'sd3;
        gol[16]=8'sd3;   gol[17]=8'sd6;   gol[18]=8'sd6;   gol[19]=8'sd9;
        gol[20]=8'sd9;   gol[21]=8'sd12;  gol[22]=8'sd12;  gol[23]=8'sd16;
        gol[24]=8'sd0;   gol[25]=8'sd0;   gol[26]=8'sd48;  gol[27]=8'sd30;
        gol[28]=-8'sd29; gol[29]=-8'sd49; gol[30]=-8'sd1;  gol[31]=8'sd48;
        gol[32]=8'sd30;  gol[33]=-8'sd29; gol[34]=-8'sd49; gol[35]=-8'sd1;
    end

    task chk;
        input [127:0] name;
        input signed [7:0] got;
        input signed [7:0] want;
        begin
            if (got !== want) begin
                $display("FAIL %0s: got %0d want %0d", name, got, want);
                errs = errs + 1;
            end
        end
    endtask

    task do_reset;
        begin
            @(negedge clk); rst_n = 0;
            repeat (3) @(negedge clk);
            @(negedge clk); rst_n = 1;
        end
    endtask

    task run_case;
        input [127:0] tag;
        input [23:0]  f;
        input [6:0]   a;
        input [31:0]  b;          // 该项在 gol 里的基址
        begin
            do_reset;
            fword = f; ampl = a; enable = 1;
            @(posedge clk);       // k = 0
            for (i = 0; i < 12; i = i + 1) begin
                @(posedge clk); #1;
                if (tone_out !== gol[b + i]) begin
                    $display("FAIL %0s[%0d]: got %0d want %0d",
                             tag, i, tone_out, gol[b + i]);
                    errs = errs + 1;
                end
            end
        end
    endtask

    initial begin
        $dumpfile("tone_gen_tb.vcd"); $dumpvars(0, tone_gen_tb);

        run_case("a100k_40",  24'd33554,   7'd40,  32'd0);
        run_case("a100k_100", 24'd33554,   7'd100, 32'd12);
        run_case("a10M_40",   24'd3355443, 7'd40,  32'd24);

        // enable=0: 输出恒 0
        do_reset;
        fword = 24'd33554; ampl = 7'd40; enable = 0;
        repeat (6) begin @(posedge clk); #1; chk("disabled", tone_out, 8'sd0); end

        // ampl=0: 输出恒 0
        do_reset;
        fword = 24'd33554; ampl = 7'd0; enable = 1;
        @(posedge clk);
        repeat (6) begin @(posedge clk); #1; chk("silent", tone_out, 8'sd0); end

        if (errs == 0) $display("PASS tone_gen");
        else           $display("FAIL tone_gen (%0d)", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/tone_gen.v src/rtl/sine_rom.v src/tb/tone_gen_tb.v
```
Expected: 报 `module 'tone_gen' not found`（或文件不存在）

- [ ] **Step 3: 实现 `tone_gen.v`**

```verilog
`timescale 1ns / 1ps
// 窄带单音干扰: tone[n] = round(ampl/100 × sine(2π f n / fs))。
// 24 位相位累加器, 地址取 acc[23:16] -> 复用既有 sine_rom(256 点, 与 dds_core 同质量)。
// 3 个寄存器级, 与 fade_fir / noise_gen 的输出同拍有效:
//   (1) acc 累加 + 寄存 dout   (2) ampl × sine_r   (3) 除以 100
//
// 时序(把复位释放后的第一个 posedge 记为 k=0):
//   k=0,1,2  被 enable 的 3 级流水门控为 0;
//   k>=3     tone_out[k] = round(ampl × ROM[((k-2)·fword)>>16] / 100)
// 本模块**没有数据输入**, 所以这里没有"输入→输出"的延时可言; 上式的 k-2 就是
// 3 个寄存器级的真实深度。host 模型 channel.py::tone_samples(...)[n] 对应 k=n+1
// (模型的 n=0 项是它对 acc=0 的重复, 故偏移是 1 不是 2), TB 按此逐拍比对。
//
// ⚠ sine_rom.v 是**组合**读(`always @(*) dout = mem[addr]`, 见该文件末行), 一拍延迟
//   来自本模块的 sine_r 寄存器, **不是** ROM 本身。不要以"ROM 已经是同步读"为由删掉
//   sine_r —— 删掉会少一拍, 与上面的 k-2 对不上, TB 会失败。
// 除以 100 独占一拍, 与 noise_gen 的 round_div(148) 同法, 避免乘除堆在同一周期。
module tone_gen (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [23:0] fword,
    input  wire [6:0]  ampl,
    output reg  signed [7:0] tone_out
);

    reg [23:0] acc;
    reg signed [7:0] sine_r;

    // ROM 输出是组合的; 声明必须在被引用之前(Verilog-2001, 否则 xvlog 报 VRFC 10-3380)
    wire [7:0] dout;

    // 第 1 拍: 累加 + 寄存 ROM 输出
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin acc <= 24'd0; sine_r <= 8'sd0; end
        else begin
            acc    <= acc + fword;
            sine_r <= dout;
        end

    sine_rom u_rom (.addr(acc[23:16]), .dout(dout));

    // 第 2 拍: 幅度乘 (ampl<=100, sine<=127 -> 最大 12700, 15-bit 有符号足够)
    reg signed [14:0] prod_r;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) prod_r <= 15'sd0;
        else        prod_r <= $signed({8'd0, ampl}) * $signed(sine_r);

    // 第 3 拍: 除以 100（有符号四舍五入）
    reg en_d1, en_d2, en_d3;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin en_d1 <= 1'b0; en_d2 <= 1'b0; en_d3 <= 1'b0; end
        else        begin en_d1 <= enable; en_d2 <= en_d1; en_d3 <= en_d2; end

    function signed [7:0] div100;
        input signed [14:0] v;
        reg signed [15:0] m;
        reg signed [15:0] r;
        begin
            if (v >= 0) r = (v + 50) / 100;
            else begin m = -v; r = -((m + 50) / 100); end
            div100 = r[7:0];
        end
    endfunction

    always @(posedge clk or negedge rst_n)
        if (!rst_n) tone_out <= 8'sd0;
        else        tone_out <= en_d3 ? div100(prod_r) : 8'sd0;
endmodule
```

- [ ] **Step 4: 用模型复核 TB 里的 golden**

TB 里的 `gol[0:35]` 已在计划中算好，此步只需**验证**它们与模型一致（不一致说明
模型或 TB 有错，必须先查清再继续）：

```bash
cd "<LOCAL_PATH>"
host/.venv/Scripts/python -c "
import sys; sys.path.insert(0,'host')
from siggen.channel import tone_samples
print(tone_samples(12, 100000, 40, 50_000_000))
print(tone_samples(12, 100000, 100, 50_000_000))
print(tone_samples(12, 10000000, 40, 50_000_000))"
```

Expected（与 TB 的 `gol[0:11]` / `gol[12:23]` / `gol[24:35]` 逐项一致）:

```
[0, 0, 0, 1, 1, 2, 2, 4, 4, 5, 5, 6]
[0, 0, 0, 3, 3, 6, 6, 9, 9, 12, 12, 16]
[0, 0, 48, 30, -29, -49, -1, 48, 30, -29, -49, -1]
```

- [ ] **Step 5: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/tone_gen.v src/rtl/sine_rom.v src/tb/tone_gen_tb.v
"$SIM/xelab.bat" tone_gen_tb -s tone_sim
"$SIM/xsim.bat" tone_sim -runall
```
Expected: `PASS tone_gen`

- [ ] **Step 6: 提交**

```bash
git add src/rtl/tone_gen.v src/tb/tone_gen_tb.v
git commit -m "feat(rtl): tone_gen narrowband interferer

24-bit phase accumulator addressing the existing sine_rom, with the
amplitude multiply and the divide by 100 in separate pipeline stages.
Keeping the divide out of the multiply stage follows the same reasoning
as noise_gen: the design has only 0.071 ns of WNS, so no stage gets two
wide operations.

Single-tone only -- band-limited noise would need a very narrow filter at
50 MHz, which is the same cost problem as a fading engine.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: `impulse_gen.v` + TB

**Files:**
- Create: `src/rtl/impulse_gen.v`
- Test: `src/tb/impulse_gen_tb.v`

**Interfaces:**
- Produces: 模块 `impulse_gen`，端口
  `input clk, rst_n, enable; input [15:0] period; input [7:0] width; input signed [7:0] ampl;
   output reg signed [7:0] impulse_out`。
  **时序**：`cnt` 是**自由运行**计数器（不受 `enable` 门控），所以复位释放后的脉冲相位
  是任意的；`enable` 标志经 3 级流水（`in_r1`/`in_r2`/输出寄存器）后与 `fade_fir` /
  `noise_gen` 同拍。`ampl` 的缩放是静态量，**不在**流水路径上。
  因此 TB **不假设**采样起点落在 `cnt=0`，只断言周期性模式（见 Step 1）。

- [ ] **Step 1: 写失败 TB**

创建 `src/tb/impulse_gen_tb.v`：

```verilog
`timescale 1ns / 1ps
// impulse_gen TB: 与 host/siggen/channel.py::impulse_samples 一致。
// cnt 是自由运行计数器, 复位释放后的相位任意, 所以**不假设**采样起点落在 cnt=0,
// 只断言模式: period=10/width=2 时每 10 拍恰好 2 拍非零(取 20 拍 = 整数个周期 -> 4 拍)。
// 复位用 negedge do_reset 释放(与下一个 posedge 隔半个周期), 避免 posedge 采样竞争
// —— 在 posedge 上直接给 rst_n 会让不同次运行相差一拍(noise_gen_tb.v 的 do_reset 同法)。
// ampl=50 -> level = round(127*50/100) = 64; ampl=-50 -> -64
module impulse_gen_tb;
    reg clk = 0, rst_n = 0, enable = 0;
    reg [15:0] period = 16'd10;
    reg [7:0]  width  = 8'd2;
    reg signed [7:0] ampl = 8'sd50;
    wire signed [7:0] impulse_out;

    impulse_gen uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                     .period(period), .width(width), .ampl(ampl),
                     .impulse_out(impulse_out));
    always #10 clk = ~clk;

    integer errs = 0;
    integer i = 0;
    integer nz = 0, ze = 0;

    task chk;
        input [127:0] name;
        input signed [31:0] got;
        input signed [31:0] want;
        begin
            if (got !== want) begin
                $display("FAIL %0s: got %0d want %0d", name, got, want);
                errs = errs + 1;
            end
        end
    endtask

    task do_reset;
        begin
            @(negedge clk); rst_n = 0;
            repeat (3) @(negedge clk);
            @(negedge clk); rst_n = 1;
        end
    endtask

    initial begin
        $dumpfile("impulse_gen_tb.vcd"); $dumpvars(0, impulse_gen_tb);

        do_reset;
        enable = 1; period = 16'd10; width = 8'd2; ampl = 8'sd50;

        // 模式: 20 拍里恰好 4 拍 64、16 拍 0(20 是 period 的整数倍, 与相位无关)
        nz = 0; ze = 0;
        for (i = 0; i < 20; i = i + 1) begin
            @(posedge clk); #1;
            if (impulse_out === 8'sd64) nz = nz + 1;
            else if (impulse_out === 8'sd0) ze = ze + 1;
            else begin
                $display("FAIL unexpected level: %0d", impulse_out);
                errs = errs + 1;
            end
        end
        chk("2-of-10 duty", nz, 4);
        chk("rest zero",    ze, 16);

        // 负幅度: 符号必须翻转, 幅值相同(同样取整数个周期 -> 2 拍)
        ampl = -8'sd50;
        repeat (2) @(posedge clk);
        nz = 0;
        for (i = 0; i < 10; i = i + 1) begin
            @(posedge clk); #1;
            if (impulse_out === -8'sd64) nz = nz + 1;
        end
        chk("neg level", nz, 2);

        // width == period -> 连续输出, 不再有 0
        ampl = 8'sd50; width = 8'd10;
        repeat (2) @(posedge clk);
        nz = 0;
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk); #1;
            if (impulse_out === 8'sd64) nz = nz + 1;
        end
        chk("continuous", nz, 8);

        // enable=0 -> 恒 0
        enable = 0;
        repeat (4) @(posedge clk);
        repeat (4) begin @(posedge clk); #1; chk("disabled", impulse_out, 0); end

        if (errs == 0) $display("PASS impulse_gen");
        else           $display("FAIL impulse_gen (%0d)", errs);
        $finish;
    end
endmodule
```

（`chk` 的 `got`/`want` 是 32 位有符号：`impulse_out` 会符号扩展进来，计数器
`nz`/`ze` 也直接传入，无需再窄化。）

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/impulse_gen.v src/tb/impulse_gen_tb.v
```
Expected: 报 `module 'impulse_gen' not found`

- [ ] **Step 3: 实现 `impulse_gen.v`**

```verilog
`timescale 1ns / 1ps
// 周期冲激干扰: 单计数器 cnt 在 0..period-1 循环, cnt < width 时输出幅度。
// 幅值 ampl 是静态 BRAM 参数, 其缩放(ampl×127/100)放在每样点路径之外 ——
// 只有 enable 标志需要走 3 拍流水, 与 fade_fir / noise_gen 对齐。
module impulse_gen (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [15:0] period,
    input  wire [7:0]  width,
    input  wire signed [7:0] ampl,
    output reg  signed [7:0] impulse_out
);

    reg [15:0] cnt;
    wire       period_end = (period == 16'd0) || (cnt >= period - 16'd1);
    wire       in_window  = enable && (cnt < {8'd0, width});

    always @(posedge clk or negedge rst_n)
        if (!rst_n) cnt <= 16'd0;
        else        cnt <= period_end ? 16'd0 : cnt + 16'd1;

    // 幅值缩放: 静态参数, 不在每样点路径上（有符号四舍五入, 除以 100）
    function signed [7:0] scale_amp;
        input signed [7:0] a;
        reg signed [15:0] m;
        reg signed [15:0] r;
        begin
            if (a >= 0) r = ((a * 127) + 50) / 100;
            else begin m = -(a * 127); r = -((m + 50) / 100); end
            scale_amp = r[7:0];
        end
    endfunction

    wire signed [7:0] level = scale_amp(ampl);

    reg in_r1, in_r2;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin in_r1 <= 1'b0; in_r2 <= 1'b0; end
        else        begin in_r1 <= in_window; in_r2 <= in_r1; end

    always @(posedge clk or negedge rst_n)
        if (!rst_n) impulse_out <= 8'sd0;
        else        impulse_out <= in_r2 ? level : 8'sd0;
endmodule
```

- [ ] **Step 4: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/impulse_gen.v src/tb/impulse_gen_tb.v
"$SIM/xelab.bat" impulse_gen_tb -s imp_sim
"$SIM/xsim.bat" imp_sim -runall
```
Expected: `PASS impulse_gen`

- [ ] **Step 5: 提交**

```bash
git add src/rtl/impulse_gen.v src/tb/impulse_gen_tb.v
git commit -m "feat(rtl): impulse_gen periodic interferer

One counter looping over period with a width comparison, so the pulse
train needs no second counter. The amplitude scaling is a static
function of a BRAM parameter, so it sits off the per-sample path; only
the enable flag is pipelined to match fade_fir's 3-cycle latency.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: `chan_impair.v` 扩展 + TB

**Files:**
- Modify: `src/rtl/chan_impair.v`
- Test: `src/tb/chan_impair_tb.v`

**Interfaces:**
- Consumes: Task 2 的 `tone_gen`、Task 3 的 `impulse_gen`
- Produces: `chan_impair` 新增端口
  `input [23:0] tone_fword; input [6:0] tone_ampl; input tone_en;
   input [15:0] impulse_period; input [7:0] impulse_width;
   input signed [7:0] impulse_ampl; input impulse_en;`
  **端到端延时仍为 3 拍**

- [ ] **Step 1: 写失败 TB 用例**

改 `src/tb/chan_impair_tb.v`，共三处：
**(a)** 模块级新增干扰控制寄存器与计数器；**(b)** 替换实例化以接上新端口；
**(c)** 在既有 golden 段之后、最终 `PASS/FAIL` 之前追加三个子用例。

⚠️ **Verilog-2001 不允许在 `initial` 块中"语句之后再声明变量"**，所以下面所有
`integer`/`reg` 声明必须放在**模块级**（(a) 块），不能跟着子用例一起塞进
既有 `initial` 块。既有 TB 已经用 `integer j, n, gold_errs;` 这种模块级写法。
新端口默认全 0（干扰关闭），既有用例因此逐位不变。

**关键：既有 `ov[n+3]` 的 3 拍断言必须保持通过。**

### (a)(b) 模块级声明与实例化

```verilog
    // 新增干扰控制（默认关闭 -> 既有用例逐位不变）
    reg              tone_en = 0;
    reg [23:0]       tone_fword = 0;
    reg [6:0]        tone_ampl = 0;
    reg              impulse_en = 0;
    reg [15:0]       impulse_period = 0;
    reg [7:0]        impulse_width = 0;
    reg signed [7:0] impulse_ampl = 0;

    // 新增子用例的计数器与 golden（模块级, 见上面 ⚠️）
    integer nz, ze, n100, n127, nm27, nbad, gold2_errs;
    reg signed [7:0] tone_gold [0:15];
    initial begin
        tone_gold[0]=0;   tone_gold[1]=48;  tone_gold[2]=30;  tone_gold[3]=71;
        tone_gold[4]=-99; tone_gold[5]=-1;  tone_gold[6]=48;  tone_gold[7]=50;
        tone_gold[8]=-29; tone_gold[9]=-49; tone_gold[10]=-1; tone_gold[11]=-22;
        tone_gold[12]=30; tone_gold[13]=-29; tone_gold[14]=-49; tone_gold[15]=-1;
    end

    chan_impair uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                     .sample_in(sample_in), .tap_gain(tap_gain),
                     .tap_delay(tap_delay), .noise_level(noise_level),
                     .tone_en(tone_en), .tone_fword(tone_fword), .tone_ampl(tone_ampl),
                     .impulse_en(impulse_en), .impulse_period(impulse_period),
                     .impulse_width(impulse_width), .impulse_ampl(impulse_ampl),
                     .sample_out(sample_out));
```

### 单音对齐 golden（**索引偏移是 [n+1]，不是 [n] 也不是 [n+2]**）

推导：加法节点是**组合**的，而 `sample_out` 是寄存器，所以在边沿 j 采样进
`sample_out` 的是"边沿 j **之前**"的各路寄存器值：

```
S[j] = sat( F[j−1] + noise[j−1] + tone[j−1] + impulse[j−1] )
F[j−1] = f(x[j−3])           // fade_fir 的输入到输出只隔 2 拍(x 未寄存, 乘直接吃组合输入)
T[j−1] = tone_samples(...)[j−2]   // 因为 tone_raw[k] = tone_samples(...)[k−1] (Task 2)
```

令 `j = n+3`：`S[n+3] = sat(f(x[n]) + tone_samples(...)[n+1])`。
第一个等号与既有 golden 段（不带干扰时 `S[n+3] = f(x[n])`）完全一致，所以
**金值用 `[n+1]`**。这一条已用仿真实测确认（见下）。

**用 10 MHz 单音**（`fword = 3355443`，即 `round(10e6·2²⁴/50e6)`）：10 MHz 时地址每拍
前进约 51，**每个样点都不同**，所以任何索引错位都会被抓住。用 100 kHz 则地址约每 2 拍
才变一次，偶数下标会掩盖错位——不要用低频。

golden 由 Task 1 模型生成（**已算好，上面 `tone_gold` 就是它**）：

```bash
host/.venv/Scripts/python -c "
import sys; sys.path.insert(0,'host')
from siggen.channel import multipath_fir, tone_samples, DAC_MIN, DAC_MAX
xv = [0,0,0,100,-50,0,0,20,0,0,0,-70,0,0,0,0,0,0,0]
fade = multipath_fir(xv, [255,0,0,0], [0,0,0,0])
tone = tone_samples(20, 10000000, 40, 50_000_000)
print([max(DAC_MIN, min(DAC_MAX, fade[n] + tone[n+1])) for n in range(16)])"
```

Expected: `[0, 48, 30, 71, -99, -1, 48, 50, -29, -49, -1, -22, 30, -29, -49, -1]`

### (c) 追加到既有 `initial` 块末尾（最终 `PASS/FAIL` 之前）

```verilog
        // ==================================================================
        // 新增 1: 单音干扰加入加法节点
        //   抽头纯透传(tap0 gain=255, 全部 delay=0), 噪声 0, 冲激关 -> fade_out = x[n-3]。
        //   S[n+3] = sat(x[n] + tone_samples(...,40,50e6)[n+1])  (见上面的推导)
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; impulse_en = 0; noise_level = 7'd0;
        sample_in = 8'd0;
        repeat (3) @(posedge clk);
        enable      = 1'b1;
        tap_gain    = {9'd0, 9'd0, 9'd0, 9'd255};    // 仅 tap0 = 255
        tap_delay   = 36'd0;                          // 全部 delay=0 -> 纯透传
        tone_en     = 1'b1;
        tone_fword  = 24'd3355443;                    // round(10e6*2^24/50e6): 地址每拍约 +51
        tone_ampl   = 7'd40;
        sample_in   = xv[0];
        @(negedge clk);
        rst_n = 1'b1;                                 // 下一个 posedge 即 x 下标 0 的采样拍
        @(posedge clk); #1;
        for (j = 1; j < 19; j = j + 1) begin
            sample_in = xv[j];
            @(posedge clk); #1;
            ov[j] = sample_out;
        end
        gold2_errs = 0;
        for (n = 0; n < 16; n = n + 1)
            if ($signed(ov[n + 3]) != tone_gold[n]) begin
                $display("FAIL tone golden[%0d] got=%0d exp=%0d",
                         n, $signed(ov[n + 3]), tone_gold[n]);
                gold2_errs = gold2_errs + 1;
            end
        chk("tone summed at adder node 16/16", gold2_errs == 0);

        // ==================================================================
        // 新增 2: 冲激项确实接进了加法节点
        //   cnt 自由运行, 相位不可预测 -> 只断言相位无关的模式:
        //   period=4/width=2 -> 稳态每 4 拍里 2 拍非零; 16 拍 = 4 个整周期 -> 恰好 8 拍非零。
        //   抽头全 0 -> fade_out = 0; 噪声 0; 单音关 -> 输出只可能是 0 或 127。
        //   ⚠ 必须先丢弃前 8 拍: 流水填充 + 首个不完整周期会让 16 拍窗口读到 7/9 而非
        //     8/8(实测如此)。丢弃后窗口完全落在稳态, 任意相位都是恰好 8/8。
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; noise_level = 7'd0;
        tap_gain = 36'd0; tap_delay = 36'd0; sample_in = 8'd0;
        impulse_en = 1'b1; impulse_period = 16'd4; impulse_width = 8'd2;
        impulse_ampl = 8'sd100;                       // level = round(100*127/100) = 127
        repeat (3) @(posedge clk);
        enable = 1'b1;
        @(negedge clk); rst_n = 1'b1;
        repeat (8) @(posedge clk); #1;                // 丢弃前 8 拍, 等流水与首个完整周期
        nz = 0; ze = 0;
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk); #1;
            if      ($signed(sample_out) == 127) nz = nz + 1;
            else if ($signed(sample_out) == 0)   ze = ze + 1;
            else begin
                $display("FAIL impulse case unexpected level: %0d", $signed(sample_out));
                errs = errs + 1;
            end
        end
        chk("impulse 2-of-4 duty in sum", nz == 8 && ze == 8);
        impulse_en = 1'b0;

        // ==================================================================
        // 新增 3: 加法节点饱和到 ±127
        //   用 12.5 MHz 单音(fword=2^22): 地址每拍 +64 -> 走遍 0/90/180/270 度。
        //   常数 x=100 + 单位抽头 -> fade_out = 100; tone_ampl=100 -> tone 峰值 ±127。
        //   稳态输出在 sat(100 + {0,127,0,-127}) = {100,127,100,-27} 间循环, 周期 4
        //   -> 每 4 拍 1 个 127、2 个 100、1 个 -27; 16 拍 = 4 周期 -> 4/8/4。
        //   ⚠ 同样是 4/8/4 而不是 4/4/4: 100 在一个周期里出现两次。
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; impulse_en = 0; noise_level = 7'd0;
        repeat (3) @(posedge clk);
        enable      = 1'b1;
        tap_gain    = {9'd0, 9'd0, 9'd0, 9'd255};
        tap_delay   = 36'd0;
        tone_en     = 1'b1;
        tone_fword  = 24'd4194304;                    // 地址每拍 +64
        tone_ampl   = 7'd100;
        sample_in   = 8'd100;
        @(negedge clk); rst_n = 1'b1;
        repeat (8) @(posedge clk); #1;                // 丢弃前 8 拍, 等流水稳定
        n100 = 0; n127 = 0; nm27 = 0; nbad = 0;
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk); #1;
            if      ($signed(sample_out) ==  100) n100 = n100 + 1;
            else if ($signed(sample_out) ==  127) n127 = n127 + 1;
            else if ($signed(sample_out) ==  -27) nm27 = nm27 + 1;
            else                                  nbad = nbad + 1;
        end
        chk("sum saturates +/-127 (4/8/4 pattern)",
            n100 == 8 && n127 == 4 && nm27 == 4 && nbad == 0);
        tone_en = 1'b0;
```

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_impair.v src/rtl/fade_fir.v src/rtl/noise_gen.v \
    src/rtl/tone_gen.v src/rtl/sine_rom.v src/rtl/impulse_gen.v src/tb/chan_impair_tb.v
```
Expected: 编译失败（`chan_impair` 无 `tone_en` 端口）

- [ ] **Step 3: 扩展 `chan_impair.v`**

**保留文件既有的第 1 行 `` `timescale 1ns / 1ps `` 与模块头注释**（下面是模块体替换，
不含 timescale；漏掉会让 xelab 报 `Module chan_impair doesn't have a timescale`），
只改端口列表、加法节点与头注释。

改端口列表并替换加法节点：

```verilog
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
    // 新增: 单音干扰（tone_gen, 3 拍）
    input  wire        tone_en,
    input  wire [23:0] tone_fword,
    input  wire [6:0]  tone_ampl,
    // 新增: 周期冲激干扰（impulse_gen, 3 拍）
    input  wire        impulse_en,
    input  wire [15:0] impulse_period,
    input  wire [7:0]  impulse_width,
    input  wire signed [7:0] impulse_ampl,
    output reg  [7:0] sample_out
);
    wire [7:0] fade_out;
    wire signed [10:0] noise_raw;
    wire signed [7:0]  tone_raw;
    wire signed [7:0]  impulse_raw;

    fade_fir u_fade (
        .clk(clk), .rst_n(rst_n), .sample_in(sample_in),
        .tap_gain(tap_gain), .tap_delay(tap_delay), .sample_out(fade_out));
    noise_gen #(.SEED(SEED)) u_noise (
        .clk(clk), .rst_n(rst_n), .enable(enable), .level(noise_level),
        .noise_out(noise_raw));
    // 干扰与噪声同位置(都在 fade_fir 之后): 干扰是接收端叠加的外部信号,
    // 不应再过信道 FIR。四个来源均为 3 拍, 第 n 拍输出同拍有效。
    tone_gen u_tone (
        .clk(clk), .rst_n(rst_n), .enable(enable && tone_en),
        .fword(tone_fword), .ampl(tone_ampl), .tone_out(tone_raw));
    impulse_gen u_impulse (
        .clk(clk), .rst_n(rst_n), .enable(enable && impulse_en),
        .period(impulse_period), .width(impulse_width), .ampl(impulse_ampl),
        .impulse_out(impulse_raw));

    // 13-bit 有符号: fade(±127) + noise(约 ±438) + tone(±127) + impulse(±127) -> ±819,
    //   13-bit 范围 ±4096 充裕。
    wire signed [12:0] sum = $signed({fade_out[7], fade_out})
                           + $signed({{2{noise_raw[10]}}, noise_raw})
                           + $signed({{5{tone_raw[7]}}, tone_raw})
                           + $signed({{5{impulse_raw[7]}}, impulse_raw});
    wire signed [7:0] clamped = (sum > 13'sd127)  ?  8'sd127 :
                                (sum < -13'sd127) ? -8'sd127 : sum[7:0];

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample_out <= 8'sd0;
        else        sample_out <= enable ? clamped : sample_in;
endmodule
```

- [ ] **Step 4: 跑 TB 确认通过（含既有 3 拍断言）**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_impair.v src/rtl/fade_fir.v src/rtl/noise_gen.v \
    src/rtl/tone_gen.v src/rtl/sine_rom.v src/rtl/impulse_gen.v src/tb/chan_impair_tb.v
"$SIM/xelab.bat" chan_impair_tb -s chan_imp_sim
"$SIM/xsim.bat" chan_imp_sim -runall
```
Expected: `PASS chan_impair` 且原有 10 项全过

- [ ] **Step 5: 提交**

```bash
git add src/rtl/chan_impair.v src/tb/chan_impair_tb.v
git commit -m "feat(rtl): sum tone and impulse interference into chan_impair

Interference joins at the same node as AWGN -- after fade_fir -- because
it models an external signal present at the receiver, so it must not pass
through the channel FIR. All four sources are 3 cycles deep, so the
existing ov[n+3] latency assertion in the testbench still holds; growing
the accumulator to 13 bits leaves ample headroom for the extra terms.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: `chan_cfg.v` 字表扩展 + TB

**Files:**
- Modify: `src/rtl/chan_cfg.v`
- Test: `src/tb/chan_cfg_tb.v`

**Interfaces:**
- Produces: `chan_cfg` 新增端口
  - `output reg [23:0] ch1_tone_fword, ch2_tone_fword`
  - `output reg [6:0]  ch1_tone_ampl,  ch2_tone_ampl`
  - `output reg [15:0] ch1_imp_period, ch2_imp_period`
  - `output reg [7:0]  ch1_imp_width,  ch2_imp_width`
  - `output reg signed [7:0] ch1_imp_ampl, ch2_imp_ampl`
  - `input wire ch_commit` 语义不变（CH1/CH2 各自 commit 排空自己的字组）

**字组划分**：CH1 commit 排空字 `0-3` 与 `12-13`；CH2 commit 排空字 `4-7` 与 `14-15`；
字 `8-11`（B 组，本轮不实现）与字 `11` 预留**本轮不排空**。

- [ ] **Step 1: 写失败 TB 用例**

改 `src/tb/chan_cfg_tb.v`：`mem[]` 初始化里补上字 12-15，并在既有 Phase C **之后**、
最终 `PASS/FAIL` **之前**追加 Phase D/E。既有设施的真实名字是 `mem[0:15]`（不是
`bram`）、`wait_cycles(n)`（没有 `wait_drain`），commit 由 `@(negedge clk); chX_commit = 1;`
制造上升沿触发。

```verilog
        // Phase D: CH1 commit 一并排空字 12(tone) / 13(impulse)
        // 字 12: fword[23:0] | ampl[30:24]
        // 字 13: period[15:0] | width[23:16] | ampl[31:24] (ampl 有符号)
        // 用拼接而不是 32'd(...) —— Verilog 的 sized 十进制字面量里不能写表达式。
        mem[12] = {8'd40,  24'd33554};                 // ampl=+40
        mem[13] = {8'd50,  8'd2, 16'd10};              // ampl=+50, width=2, period=10
        @(negedge clk); ch1_commit = 1;      // 上升沿 -> 触发 CH1 排空
        wait_cycles(30);
        chk("ch1 tone fword", uut.ch1_tone_fword == 24'd33554);
        chk("ch1 tone ampl",  uut.ch1_tone_ampl  == 7'd40);
        chk("ch1 imp period", uut.ch1_imp_period == 16'd10);
        chk("ch1 imp width",  uut.ch1_imp_width  == 8'd2);
        chk("ch1 imp ampl",   uut.ch1_imp_ampl   == 8'sd50);
        // 负幅度要保住符号位 (206 = -50 的 8 位补码)
        mem[13] = {8'd206, 8'd2, 16'd10};
        @(negedge clk); ch1_commit = 0;
        @(negedge clk); ch1_commit = 1;      // 一个新的上升沿
        wait_cycles(30);
        chk("ch1 imp ampl signed", uut.ch1_imp_ampl == -8'sd50);

        // Phase E: CH2 也要排空字 14/15, 且不碰 CH1
        mem[14] = {8'd100, 24'd3355443};               // ampl=+100
        mem[15] = {8'd226, 8'd8, 16'd1000};            // ampl=-30 (226 = -30 补码)
        @(negedge clk); ch2_commit = 1;      // 上升沿 -> 触发 CH2 排空
        wait_cycles(30);
        chk("ch2 tone fword", uut.ch2_tone_fword == 24'd3355443);
        chk("ch2 tone ampl",  uut.ch2_tone_ampl  == 7'd100);
        chk("ch2 imp period", uut.ch2_imp_period == 16'd1000);
        chk("ch2 imp width",  uut.ch2_imp_width  == 8'd8);
        chk("ch2 imp ampl",   uut.ch2_imp_ampl   == -8'sd30);
        chk("ch1 tone untouched E", uut.ch1_tone_fword == 24'd33554);
```

注意相位 D 的第二次触发：既有 Phase C 已把两个 commit 都置 0，所以
`ch1_commit = 1` 就是上升沿；要再触发一次必须**先回 0、隔一个 negedge 再置 1**
（FSM 检测的是边沿，不是电平）。

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
"$SIM/xelab.bat" chan_cfg_tb -s cfg_sim && "$SIM/xsim.bat" cfg_sim -runall
```
Expected: FAIL（`ch1_tone_fword` 不存在 / 值为 0）

- [ ] **Step 3: 扩展 `chan_cfg.v`**

把每通道的排空从"4 个字"改成"6 个字"（4 抽头 + tone + impulse），用一张地址表描述：

```verilog
    // 每通道排空 6 个字: {0,1,2,3,12,13} for CH1; {4,5,6,7,14,15} for CH2
    localparam STEPS = 6;
    reg [2:0] step;            // 0..5

    // 字地址表: 用函数表达, 避免 base+step 在跨段时出现断点。
    function [4:0] word_sel;
        input       ch;        // 0 = CH1, 1 = CH2
        input [2:0] s;
        begin
            if (ch == 1'b0) word_sel = (s < 3'd4) ? {2'd0, s}            // 0..3
                                                  : (5'd8 + {1'd0, s});  // 12,13
            else            word_sel = (s < 3'd4) ? (5'd4 + {2'd0, s})   // 4..7
                                                  : (5'd10 + {1'd0, s}); // 14,15
        end
    endfunction

    wire [4:0] cur_word = word_sel(cur_ch, step);
    wire [4:0] next_word = word_sel(cur_ch, step + 3'd1);
```

`cur_word` 用于 `S_IDLE` 的起始地址与 `S_WAIT` 的地址保持；`next_word` 用于
`S_CAP` 推进到下一个字（最后一步不推进）。

在 `S_CAP` 中按 `step` 分派锁存：

```verilog
                S_CAP: begin
                    case (step)
                        3'd0, 3'd1, 3'd2, 3'd3: begin
                            if (cur_ch == 1'b0) begin
                                ch1_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch1_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end else begin
                                ch2_gain [step*9 +: 9] <= tap_b_rdata[8:0];
                                ch2_delay[step*9 +: 9] <= tap_b_rdata[17:9];
                            end
                        end
                        3'd4: begin   // tone 字
                            if (cur_ch == 1'b0) begin
                                ch1_tone_fword <= tap_b_rdata[23:0];
                                ch1_tone_ampl  <= tap_b_rdata[30:24];
                            end else begin
                                ch2_tone_fword <= tap_b_rdata[23:0];
                                ch2_tone_ampl  <= tap_b_rdata[30:24];
                            end
                        end
                        3'd5: begin   // impulse 字
                            if (cur_ch == 1'b0) begin
                                ch1_imp_period <= tap_b_rdata[15:0];
                                ch1_imp_width  <= tap_b_rdata[23:16];
                                ch1_imp_ampl   <= tap_b_rdata[31:24];
                            end else begin
                                ch2_imp_period <= tap_b_rdata[15:0];
                                ch2_imp_width  <= tap_b_rdata[23:16];
                                ch2_imp_ampl   <= tap_b_rdata[31:24];
                            end
                        end
                        default: ;    // step 6/7 不可达(step 到 5 即结束), 显式留空
                    endcase
                    if (step == STEPS - 1) begin
                        tap_b_en <= 1'b0;
                        state    <= S_IDLE;
                    end else begin
                        step       <= step + 3'd1;
                        tap_b_addr <= next_word;
                        tap_b_en   <= 1'b1;
                        state      <= S_WAIT;
                    end
                end
```

同时：
- `done_ch` 改成 `(state == S_CAP) && (step == STEPS - 1)`；
- 复位块中把 10 个新输出（CH1/CH2 × tone_fword/tone_ampl/imp_period/imp_width/imp_ampl）清零；
- 头注释里"每通道 4 个 32-bit 字…字 8..15 预留"要更新为 6 个字与字 12-15 的用途
  （字 8-11 留给第二轮的复数 I/Q 级）。

⚠️ **`S_IDLE` 的起始地址不要改成 `cur_word`。** 那里同时在做
`cur_ch <= pend1 ? 1'b0 : 1'b1;`，而 `cur_word = word_sel(cur_ch, step)` 读的是
**非阻塞赋值之前**的旧 `cur_ch` —— 从 CH1 切到 CH2 时会算出 0 而不是 4，CH2 会去排空
CH1 的字。**保持既有的 `tap_b_addr <= pend1 ? 5'd0 : 5'd4;` 不动**：它正好等于
`word_sel(ch, 0)` 的两个取值，已经是正确写法。（`S_CAP` 里的 `next_word` 用 `cur_ch`
是安全的，那里 `cur_ch` 已经稳定。）

**每通道排空 6 个字 = 1 拍 IDLE + 6×2 拍 = 13 拍**（既有 4 字是 9 拍）。既有 TB 各段用
`wait_cycles(20)` / `wait_cycles(40)`，对 6 字与"两通道各 6 字"（26 拍）仍然足够，
不需要改等待长度。

- [ ] **Step 4: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
"$SIM/xelab.bat" chan_cfg_tb -s cfg_sim && "$SIM/xsim.bat" cfg_sim -runall
```
Expected: `PASS chan_cfg`（既有 20 项 + 新增 5 项）

- [ ] **Step 5: 提交**

```bash
git add src/rtl/chan_cfg.v src/tb/chan_cfg_tb.v
git commit -m "feat(rtl): drain tone/impulse words in chan_cfg

Widens each channel's walk from 4 tap words to 6, using an address table
instead of base+step so the non-contiguous layout (0-3 then 12-13 for
CH1) stays legible. Words 8-11 stay untouched; they belong to the complex
I/Q stage in the next round.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 使能位解码 + `awg_top` 接线

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/rtl/awg_top.v`
- Test: `src/tb/reg_bridge_tb.v`
- Test: `src/tb/awg_top_tb.v`

**Interfaces:**
- Consumes: Task 4 的 `chan_impair` 端口、Task 5 的 `chan_cfg` 输出
- Produces: `reg_bridge` 新增输出
  `ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en`
  （CH1 字 bit9/bit10；CH2 字 bit9/bit10）

- [ ] **Step 1: 写失败 TB 用例**

两个 TB 都要改。`reg_bridge` 也被 `reg_bridge_tb.v` 直接实例化，所以新增的 4 个输出那里
也**必须**接上，否则会出 `VRFC 10-3645 port remains unconnected`（本计划把 xelab 告警
视为缺陷，不是可忽略噪声）。

**(a) `src/tb/reg_bridge_tb.v`** —— 在既有 `wire ch1_tap_commit, ch2_tap_commit;` 一行
（约 41 行）后面加 wire，实例化里（约 83 行 `ch1_chan_word_in`/`ch2_chan_word_in` 附近）
接上，然后在既有信道字段（约 275-308 行）之后追加断言。既有 TB 用
`ch1_chan_word_in = {23'd0, 1'b0, 7'd40, 1'b1};` 这种拼接写法：bit[8]=commit、
bits[7:1]=noise、bit0=chan_en。

```verilog
    wire ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en;
```
```verilog
        .ch1_tone_en(ch1_tone_en), .ch1_imp_en(ch1_imp_en),
        .ch2_tone_en(ch2_tone_en), .ch2_imp_en(ch2_imp_en),
```
```verilog
        // ---- 新增: bit9/bit10 = TONE/IMPULSE 使能 ----
        // ⚠ 拼接是 MSB 在前, 且总线宽必须正好 32 位。字段自高到低:
        //   [31:11] 21 位保留 | bit10=imp_en | bit9=tone_en | bit8=commit
        //   | [7:1] noise | bit0 = chan_en
        //   写成 22'd0 开头会把两个 1 位字段挤到 bit9/bit8 —— 下面 22'd0 那行是
        //   唯一合法的例外(它只要 bit9=1 且 bit8=0, 正好少一位保留)。
        // 既有 TB 用 #40 (=2 拍); 这里用 3 拍更稳(2 级同步器 + 解码)。
        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        repeat (3) @(posedge clk); #1;
        chk("en bits off by default", ch1_tone_en == 1'b0 && ch1_imp_en == 1'b0
                                   && ch2_tone_en == 1'b0 && ch2_imp_en == 1'b0);
        // bit9 = tone_en (CH1), bit10 = 0
        ch1_chan_word_in = {22'd0, 1'b1, 1'b0, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 tone_en set",   ch1_tone_en == 1'b1);
        chk("ch1 imp_en clear",  ch1_imp_en  == 1'b0);
        chk("ch2 en unaffected", ch2_tone_en == 1'b0 && ch2_imp_en == 1'b0);
        // 既有解码不受 bit9/bit10 影响
        chk("bit9 does not disturb", ch1_chan_en == 1'b1 && ch1_noise == 7'd40
                                  && ch1_tap_commit == 1'b0);
        // bit10 = imp_en (CH1), bit9 = 0 -> tone_en 必须回落
        ch1_chan_word_in = {21'd0, 1'b1, 1'b0, 1'b1, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 imp_en set",    ch1_imp_en  == 1'b1);
        chk("ch1 tone_en clear", ch1_tone_en == 1'b0);
        // CH2 的 bit9/bit10 走它自己的字
        ch1_chan_word_in = 32'd0;
        ch2_chan_word_in = {21'd0, 1'b1, 1'b1, 1'b1, 7'd55, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch2 both en set", ch2_tone_en == 1'b1 && ch2_imp_en == 1'b1);
        chk("ch2 noise",       ch2_noise == 7'd55);
        chk("ch1 en cleared",  ch1_tone_en == 1'b0 && ch1_imp_en == 1'b0);
        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        repeat (3) @(posedge clk); #1;
```

**(b) `src/tb/awg_top_tb.v`** —— 端到端穿透检查。该 TB 已经声明并接好了
`ch1_chan_word`/`ch2_chan_word` 与 `tap_b_rdata`/`tap_b_addr`/`tap_b_en`，但
**`tap_b_rdata` 从没被任何东西驱动**（一直是 0），所以要先补一个最小 BRAM 模型。
`tap_b_addr` 是**字节**地址，`awg_top` 给出的是 `{tap_word_addr, 2'b00}`，故字下标取
`tap_b_addr[5:2]`。

模块级补（放在既有 `tap_b_rdata` 声明附近）：

```verilog
    // 抽头表 BRAM 的最小模型: 只有字 12/13 给非零值(其余 0, 与既有行为一致)
    reg [31:0] tmem [0:15];
    initial begin
        tmem[12] = {8'd40,  24'd33554};        // tone: ampl=+40, fword=100kHz
        tmem[13] = {8'd206, 8'd2, 16'd10};     // impulse: ampl=-50, width=2, period=10
    end
    always @(posedge clk) if (tap_b_en) tap_b_rdata <= tmem[tap_b_addr[5:2]];
```

然后在最终 `PASS/FAIL` **之前**追加（放在最后，避免影响既有 106 项：

```verilog
        // ---- 新增: 使能位 + BRAM 参数端到端穿透 ----
        // 该 TB 里没有 wait_cycles helper(chk 与 reset_stats 是这个文件仅有的 task),
        // 用显式 repeat 循环。30 拍 = 2 级 GPIO 同步 + 边沿检测 + 6 字排空(13 拍)。
        ch1_chan_word = {21'd0, 1'b1, 1'b1, 1'b1, 7'd0, 1'b1}; // bit10=imp_en, bit9=tone_en, bit8=commit, bit0=chan_en
        repeat (30) @(posedge clk);
        chk("awg_top ch1 tone_en",  uut.u_chan0.tone_en    == 1'b1);
        chk("awg_top ch1 imp_en",   uut.u_chan0.impulse_en == 1'b1);
        chk("awg_top ch2 en off",   uut.u_chan1.tone_en    == 1'b0
                                 && uut.u_chan1.impulse_en == 1'b0);
        chk("awg_top ch1 tone_fword",  uut.ch1_tone_fword  == 24'd33554);
        chk("awg_top ch1 tone_ampl",   uut.ch1_tone_ampl   == 7'd40);
        chk("awg_top ch1 imp_period",  uut.ch1_imp_period  == 16'd10);
        chk("awg_top ch1 imp_width",   uut.ch1_imp_width   == 8'd2);
        chk("awg_top ch1 imp_ampl",    uut.ch1_imp_ampl    == -8'sd50);
        ch1_chan_word = 32'd0;
```

注意：`uut.ch1_tone_fword` 等是 `awg_top` 内部的 wire（Step 4 里新增的那批），
不是 `awg_top` 的端口，靠层次引用访问；`uut.u_chan0`/`uut.u_chan1` 是既有两个
`chan_impair` 实例的名字（已核对 `awg_top.v:547/552`）。这段放在 TB 最后，
因为 `ch1_chan_word` 在此之前一直是 0（既有 106 项从不驱动信道控制字），
改动它不会影响既有用例。

- [ ] **Step 2: 跑 TB 确认失败**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s awg_sim && "$SIM/xsim.bat" awg_sim -runall
```
Expected: 失败在 **xelab**（xvlog 只做语法检查、退出码 0），报
`cannot find 'ch1_tone_fword'` 之类的层次引用错误 —— 该 wire 还没在 `awg_top` 里声明。
（注意**不是** "`tone_en` 不存在"：`chan_impair` 的 `tone_en` 端口在 Task 4 就有了。）

- [ ] **Step 3: 改 `reg_bridge.v`**

```verilog
    assign ch1_chan_en    = r17[0];
    assign ch1_noise      = (r17[7:1] > 7'd100) ? 7'd100 : r17[7:1];
    assign ch1_tap_commit = r17[8];
    // 新增: 干扰源使能（开关走 GPIO 立即生效, 数值走 BRAM + COMMIT）
    // 注意 main.c write_chan_ctrl() 把控制字截到低 16 位, 故只用 bits 9-15。
    assign ch1_tone_en    = r17[9];
    assign ch1_imp_en     = r17[10];
    assign ch2_chan_en    = r18[0];
    assign ch2_noise      = (r18[7:1] > 7'd100) ? 7'd100 : r18[7:1];
    assign ch2_tap_commit = r18[8];
    assign ch2_tone_en    = r18[9];
    assign ch2_imp_en     = r18[10];
```

并在端口列表与其上方的信号声明中补上这四个 wire。

- [ ] **Step 4: 改 `awg_top.v` 接线**

```verilog
    wire ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en;
    wire [23:0] ch1_tone_fword, ch2_tone_fword;
    wire [6:0]  ch1_tone_ampl,  ch2_tone_ampl;
    wire [15:0] ch1_imp_period, ch2_imp_period;
    wire [7:0]  ch1_imp_width,  ch2_imp_width;
    wire signed [7:0] ch1_imp_ampl, ch2_imp_ampl;
```

`chan_cfg` 实例补上新端口（端口名来自 Task 5 的实现，已核对）：

```verilog
    chan_cfg u_chan_cfg (
        .clk(sys_clk), .rst_n(rst_n),
        .ch1_commit(ch1_tap_commit), .ch2_commit(ch2_tap_commit),
        .tap_b_addr(tap_word_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay),
        .ch1_tone_fword(ch1_tone_fword), .ch1_tone_ampl(ch1_tone_ampl),
        .ch1_imp_period(ch1_imp_period), .ch1_imp_width(ch1_imp_width),
        .ch1_imp_ampl(ch1_imp_ampl),
        .ch2_tone_fword(ch2_tone_fword), .ch2_tone_ampl(ch2_tone_ampl),
        .ch2_imp_period(ch2_imp_period), .ch2_imp_width(ch2_imp_width),
        .ch2_imp_ampl(ch2_imp_ampl)
    );
```

两个 `chan_impair` 实例补上（CH2 用 `ch2_*` 与 `u_chan1`/`chan_i1`）：

```verilog
    chan_impair #(.SEED(32'h00C0FFEE)) u_chan0 (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch1_chan_en),
        .sample_in(dac_sel0), .tap_gain(ch1_gain), .tap_delay(ch1_delay),
        .noise_level(ch1_noise),
        .tone_en(ch1_tone_en), .tone_fword(ch1_tone_fword), .tone_ampl(ch1_tone_ampl),
        .impulse_en(ch1_imp_en), .impulse_period(ch1_imp_period),
        .impulse_width(ch1_imp_width), .impulse_ampl(ch1_imp_ampl),
        .sample_out(chan_i0)
    );
```

（CH2 同理，用 `ch2_*`。）

- [ ] **Step 5: 跑 TB 确认通过**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
"$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s awg_sim && "$SIM/xsim.bat" awg_sim -runall
```
Expected: `PASS awg_top`（既有 106 项全过 + 新增）

- [ ] **Step 6: 同时跑其余受影响 TB**

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
for tb in reg_bridge_tb awg_top_stream_tb chan_impair_tb chan_cfg_tb; do
  "$SIM/xvlog.bat" --sv src/rtl/*.v src/tb/$tb.v >/dev/null || echo "XVLOG FAIL $tb"
  "$SIM/xelab.bat" $tb -s ${tb}_sim >/dev/null && "$SIM/xsim.bat" ${tb}_sim -runall | tail -1
done
```
Expected: 每个都输出 `PASS <模块名>`

- [ ] **Step 7: 提交**

```bash
git add src/rtl/reg_bridge.v src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat(rtl): wire tone/impulse enables and params into awg_top

Enables ride the control word (immediate effect, like chan_en and noise)
while numeric parameters stay in BRAM and land on COMMIT (like taps), so
the existing staging semantics extend without a new command family.

Bits 9 and 10 are used because firmware write_chan_ctrl masks the word to
16 bits; bits 16-31 are electrically free but not reachable from the PS.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: 固件命令、影子数组与 `GET` 扩展

**Files:**
- Modify: `sdk/src/main.c`

**Interfaces:**
- Consumes: Task 6 的位映射
- Produces: 命令
  - `SET CH1|CH2 CHAN TONE <freq_hz 0..25000000> <ampl 0..100>`
  - `SET CH1|CH2 CHAN IMPULSE <period 1..65535> <width 1..255> <ampl -100..100>`
  - `GET CHx CHAN` 扩展为 24 token：在既有 `NOISE <n>` 之后插入
    `TONE <freq> <ampl> IMPULSE <period> <width> <ampl>`

- [ ] **Step 1: 加影子数组与写函数**

在 `static u32 chan_tap[2][4]` 附近：

```c
/* 干扰源参数影子(供 GET 回读); BRAM 字 12-15 由 write_chan_ext() 写。 */
static u32 chan_tone[2]  = {0u, 0u};   /* fword[23:0] | ampl[30:24] */
static u32 chan_imp[2]   = {0u, 0u};   /* period[15:0] | width[23:16] | ampl[31:24] */
/* ⚠ 频率必须单独留一份 Hz 影子, 不能靠 fword 反算回读:
   fword = round(f*2^24/50e6) 的分辨率是 50e6/2^24 = 2.98 Hz, 远大于 1 Hz,
   反算必然丢位 —— 实测 100000 -> 99998、1000000 -> 999999、10000000 -> 9999999,
   而 spec §4 的例子明确回读 1000000、验收标准 4 要求"回读与下发一致"。
   故 GET 回报用户下发的 Hz; 硬件实际发出的频率由示波器用例独立测量(容差 1 bin)。 */
static u32 chan_tone_freq[2] = {0u, 0u};   /* 用户下发的 Hz(0..25000000) */
#define CHAN_TONE_WORD_BASE(ch)  (12u + (u32)(ch) * 2u)
```

```c
static void write_chan_ext(int ci)
{
    u32 base;
    if (CHAN_TAP_BRAM_BASEADDR == 0 || ci < 0 || ci > 1) return;
    /* CH1 tone/impulse = 字 12/13, CH2 = 字 14/15 */
    base = CHAN_TAP_BRAM_BASEADDR + CHAN_TONE_WORD_BASE(ci) * 4u;
    Xil_Out32(base + 0u, chan_tone[ci]);
    Xil_Out32(base + 4u, chan_imp[ci]);
}
```

- [ ] **Step 2: 加命令处理**

在既有 `CHAN` 分支（`strcmp(tok[2], "CHAN") == 0`）内，`COMMIT` 之后、`else` 之前插入：

```c
            } else if (n == 6 && strcmp(tok[3], "TONE") == 0) {
                u32 a, f;
                /* ⚠ 频率不能用 parse_long: 它的上限写死 1000000(main.c:752),
                   会把 (1MHz, 25MHz] 全部误判为超范围 —— 本命令要求 0..25000000。
                   parse_u32_limit 需要显式传 limit。 */
                if (!parse_u32_limit(tok[4], &f, 25000000u)) {
                    uart_send("ERR TONE OUT OF RANGE\r\n"); return;
                }
                if (!parse_u32(tok[5], &a) || a > 100u) {
                    uart_send("ERR TONE OUT OF RANGE\r\n"); return;
                }
                /* fword = round(f * 2^24 / 50e6)，24 位累加器 */
                chan_tone_freq[cc] = f;   /* GET 回读用(见 Step 1 的说明) */
                chan_tone[cc] = (u32)((((unsigned long long)f << 24) + 25000000ull)
                                      / 50000000ull) & 0xFFFFFFu;
                chan_tone[cc] |= ((a & 0x7Fu) << 24);
                /* 开关在 GPIO 的 bit9(reg_bridge.v): ampl==0 视同关闭。
                   ampl==0 的启用与关闭在行为上完全一致(输出恒 0), 故不必另开命令。 */
                if (a == 0u) chan_word[cc] &= ~(1u << 9);
                else         chan_word[cc] |=  (1u << 9);
                write_chan_ctrl();          /* 内含 write_chan_ext() */
                uart_send("OK\r\n"); return;
            } else if (n == 7 && strcmp(tok[3], "IMPULSE") == 0) {
                u32 p, w; long a;
                if (!parse_u32(tok[4], &p) || p < 1u || p > 65535u) {
                    uart_send("ERR IMPULSE OUT OF RANGE\r\n"); return;
                }
                if (!parse_u32(tok[5], &w) || w < 1u || w > 255u) {
                    uart_send("ERR IMPULSE OUT OF RANGE\r\n"); return;
                }
                if (!parse_long(tok[6], &a) || a < -100 || a > 100) {
                    uart_send("ERR IMPULSE OUT OF RANGE\r\n"); return;
                }
                chan_imp[cc] = (p & 0xFFFFu) | ((w & 0xFFu) << 16)
                             | (((u32)(a & 0xFF)) << 24);
                /* 开关在 GPIO 的 bit10(reg_bridge.v): ampl==0 视同关闭 */
                if (a == 0L) chan_word[cc] &= ~(1u << 10);
                else         chan_word[cc] |=  (1u << 10);
                write_chan_ctrl();
                uart_send("OK\r\n"); return;
```

并在 `write_chan_ctrl()` 中随 `chan_word` 一起刷新扩展字（保证 COMMIT 后生效）：

```c
static void write_chan_ctrl(void)
{
    if (CHAN_CTRL_BASEADDR == 0) return;
    XGpio_DiscreteWrite(&Gpio12, 1, chan_word[0] & 0xffffu);
    XGpio_DiscreteWrite(&Gpio12, 2, chan_word[1] & 0xffffu);
    write_chan_ext(0);
    write_chan_ext(1);
}
```

- [ ] **Step 3: 扩展 `GET CHx CHAN`**

把 `main.c:1923` 的 `sprintf` 改为 24 token（`NOISE` 之后插入新字段）。
`chan_tone[cc]` 的 ampl 在 `[30:24]`，`chan_imp[cc]` 的 ampl 在 `[31:24]`（有符号 8 位）。
**频率回读取 `chan_tone_freq[cc]`**（Step 1 已说明为什么不能从 fword 反算）。

缓冲区不用动：现有 `char out[192]`（`main.c:1922`）对 24 token 的最坏长度约 120 字符
（`TONE 25000000 100` + `IMPULSE 65535 255 -100`）仍然够用，改大反而无谓。

```c
            sprintf(out, "CH%d CHAN %s NOISE %lu TONE %lu %lu IMPULSE %lu %lu %ld "
                         "TAP0 %ld %lu TAP1 %ld %lu TAP2 %ld %lu TAP3 %ld %lu\r\n",
                    cc + 1, chan_word[cc] & 1u ? "ON" : "OFF",
                    (unsigned long)((chan_word[cc] >> 1) & 0x7Fu),
                    (unsigned long)chan_tone_freq[cc],
                    (unsigned long)((chan_tone[cc] >> 24) & 0x7Fu),
                    (unsigned long)(chan_imp[cc] & 0xFFFFu),
                    (unsigned long)((chan_imp[cc] >> 16) & 0xFFu),
                    (long)(signed char)((chan_imp[cc] >> 24) & 0xFFu),
                    /* 既有 TAP0..TAP3 五个参数不变 */
                    sign9(chan_tap[cc][0] & 0x1FFu), (unsigned long)((chan_tap[cc][0] >> 9) & 0x1FFu),
                    sign9(chan_tap[cc][1] & 0x1FFu), (unsigned long)((chan_tap[cc][1] >> 9) & 0x1FFu),
                    sign9(chan_tap[cc][2] & 0x1FFu), (unsigned long)((chan_tap[cc][2] >> 9) & 0x1FFu),
                    sign9(chan_tap[cc][3] & 0x1FFu), (unsigned long)((chan_tap[cc][3] >> 9) & 0x1FFu));
```

- [ ] **Step 4: 构建固件**

固件没有单元测试框架（本仓库 RTL/host 才有），所以这一任务的"测试"就是**编译干净**，
行为验证留给 Task 9 的板上命令断言与 Task 11 的部署。

工程根的 `vivado2025.cmd` 只是两行 .cmd 包装（`python "%~dp0vivado2025.py" %*`）。
在本仓库的 bash 里**不要用 `cmd //c vivado2025.cmd …`**：cmd.exe 的当前目录与 bash
不一致，会报 `vivado2025.cmd is not recognized`。直接调 Python 入口：

```bash
python vivado2025.py app build awg_bridge --workspace sdk/ws2025_gem_fresh
```

**⚠ 两个必须遵守的点（都在 Task 7 实跑时暴露）：**

1. **工作区要用 `sdk/ws2025_gem_fresh`，不是 README/CLAUDE.md 里写的 `sdk/ws2025`。**
   `sdk/ws2025` 的 BSP **没有 lwip**（实测：`ws2025` 下找不到 `liblwip220.a`，
   `ws2025_gem` / `ws2025_gem_fresh` 下有），而现在的 `sdk/src/main.c` 依赖
   `udp_ddr_pp.c` 的 lwip，所以它根本编不过当前固件。
2. **`app.build: ok` 不等于真的编译了。** `build_app()`（`core/app.py:142-169`）在
   Vitis 侧跑完 `component.build()` 后只做 `find_elf()`—— **只查 ELF 存在、不看时间戳**，
   所以只要工作区里留着一个旧 ELF，编译失败也会返回 ok。实测：在 `sdk/ws2025` 里跑
   该命令打印 `ok` 且退出码 0，但 ELF 仍是 09-07 的旧文件（obj 变了、没重新链接）。

   因此**必须自己断言产物新鲜**（`obj` 的 mtime 晚于源文件，且 ELF 与 obj 同时刻）：

```bash
ls -la --time-style=full-iso \
  sdk/ws2025_gem_fresh/awg_bridge/src/main.c \
  sdk/ws2025_gem_fresh/awg_bridge/build/CMakeFiles/awg_bridge.elf.dir/main.c.obj \
  sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf
```

Expected: `main.c.obj` 的 mtime **晚于** `main.c`，`awg_bridge.elf` 与 obj 同一时刻；
编译日志 `Build Finished successfully`，0 error / 0 warning。
（`app build` 会从 `<ws>/<app>/src/` 编译，**不是** `sdk/src/`；`create_app` 的
`empty` 模板不搬运源码。若那个副本落后，先把它同步成 `sdk/src/main.c` 再构建——
工作区目录是生成物，同步是安全的。若出现 `implicit declaration of function:
write_chan_ext`，说明 `write_chan_ext` 定义在 `write_chan_ctrl` 之后了。）

- [ ] **Step 5: 提交**

```bash
git add sdk/src/main.c
git commit -m "feat(fw): tone and impulse interference commands

Adds SET CHx CHAN TONE/IMPULSE plus the extended 24-token GET CHx CHAN
line. Parameters go to tap-BRAM words 12-15 and are refreshed alongside
the control word, so a COMMIT latches them exactly like taps do.

Frequency converts to a 24-bit phase step against the fixed 50 MHz
sample clock; the RTL accumulator is 24 bits, so the resolution is
50e6/2^24 = 2.98 Hz.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: 上位机协议构造器与解析器

**Files:**
- Modify: `host/siggen/protocol.py`
- Test: `host/tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `CHAN_TONE_FREQ_MIN, CHAN_TONE_FREQ_MAX = 0, 25_000_000`
  - `CHAN_IMP_PERIOD_MIN, CHAN_IMP_PERIOD_MAX = 1, 65535`
  - `CHAN_IMP_WIDTH_MIN, CHAN_IMP_WIDTH_MAX = 1, 255`
  - `CHAN_IMP_AMPL_MIN, CHAN_IMP_AMPL_MAX = -100, 100`
  - `chan_tone_cmd(ch, freq_hz, ampl)` → `str`
  - `chan_impulse_cmd(ch, period, width, ampl)` → `str`
  - `parse_chan_status(line)` 返回值新增
    `"tone": (freq_hz, ampl)` 与 `"impulse": (period, width, ampl)`

- [ ] **Step 1: 写失败测试**

追加到 `host/tests/test_protocol.py`：

```python
from siggen.protocol import (
    CHAN_IMP_AMPL_MAX,
    CHAN_IMP_AMPL_MIN,
    chan_impulse_cmd,
    chan_tone_cmd,
    parse_chan_status,
)


def test_chan_tone_cmd_builds_expected_line():
    assert chan_tone_cmd(1, 1000000, 20) == "SET CH1 CHAN TONE 1000000 20"
    assert chan_tone_cmd(2, 0, 0) == "SET CH2 CHAN TONE 0 0"


def test_chan_tone_cmd_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_tone_cmd(1, -1, 10)
    with pytest.raises(ValueError):
        chan_tone_cmd(1, 25_000_001, 10)
    with pytest.raises(ValueError):
        chan_tone_cmd(1, 1000, 101)


def test_chan_impulse_cmd_builds_expected_line():
    assert chan_impulse_cmd(1, 1000, 8, 30) == "SET CH1 CHAN IMPULSE 1000 8 30"
    assert chan_impulse_cmd(2, 1, 1, CHAN_IMP_AMPL_MIN) == "SET CH2 CHAN IMPULSE 1 1 -100"


def test_chan_impulse_cmd_rejects_out_of_range():
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 0, 1, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 65536, 1, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 10, 256, 10)
    with pytest.raises(ValueError):
        chan_impulse_cmd(1, 10, 1, CHAN_IMP_AMPL_MAX + 1)


def test_parse_chan_status_reads_new_24_token_line():
    line = ("CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 "
            "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    status = parse_chan_status(line)
    assert status["enabled"] is True
    assert status["noise"] == 30
    assert status["tone"] == (1000000, 20)
    assert status["impulse"] == (1000, 8, 30)
    assert status["taps"] == [(255, 0), (0, 0), (0, 0), (0, 0)]


def test_parse_chan_status_accepts_legacy_17_token_line():
    line = "CH1 CHAN OFF NOISE 0 TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0"
    status = parse_chan_status(line)
    assert status["tone"] == (0, 0)
    assert status["impulse"] is None
    assert status["noise"] == 0


def test_parse_chan_status_accepts_fresh_board_idle_line():
    """上电/复位后 chan_imp 全 0, GET 发 IMPULSE 0 0 0 —— 必须能解析。

    这一条是板上联调的前提: 解析失败会让 controller 记 protocol parse error
    且不发 chan_status_changed, GUI 状态栏对所有通道恒显示 "--"。
    """
    line = ("CH1 CHAN OFF NOISE 0 TONE 0 0 IMPULSE 0 0 0 "
            "TAP0 0 0 TAP1 0 0 TAP2 0 0 TAP3 0 0")
    status = parse_chan_status(line)
    assert status["enabled"] is False
    assert status["impulse"] is None      # 全 0 == 从未配置, 与 17-token 形式一致
    assert status["tone"] == (0, 0)
    assert status["taps"] == [(0, 0)] * 4


@pytest.mark.parametrize("bad", [
    # TONE keyword misplaced (off-by-one habit: "TONE" written where the freq belongs)
    "CH1 CHAN ON NOISE 30 1000000 TONE 20 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # tone amplitude out of range
    "CH1 CHAN ON NOISE 30 TONE 1000000 101 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # impulse period 0 with non-zero width/ampl -> corrupt, not the all-zero sentinel
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 0 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # impulse amplitude out of range
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 101 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0 0",
    # only 23 tokens
    "CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 "
    "TAP0 255 0 TAP1 0 0 TAP2 0 0 TAP3 0",
])
def test_parse_chan_status_rejects_malformed_24_token(bad):
    with pytest.raises(ValueError):
        parse_chan_status(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v`
Expected: FAIL —`ImportError: cannot import name 'chan_tone_cmd'`

- [ ] **Step 3: 实现构造器与解析**

追加到 `host/siggen/protocol.py`（放在既有 `chan_commit_cmd` 之后）：

```python
CHAN_TONE_FREQ_MIN, CHAN_TONE_FREQ_MAX = 0, 25_000_000
CHAN_DAC_SAMPLE_RATE_HZ = 50_000_000


def chan_tone_cmd(ch, freq_hz: int, ampl: int) -> str:
    """构造窄带单音干扰命令;freq 0..25MHz, ampl 0..100,否则抛 ValueError。"""
    if (isinstance(freq_hz, bool) or not isinstance(freq_hz, int)
            or not CHAN_TONE_FREQ_MIN <= freq_hz <= CHAN_TONE_FREQ_MAX):
        raise ValueError(f"tone frequency out of range: {freq_hz!r}")
    if (isinstance(ampl, bool) or not isinstance(ampl, int)
            or not CHAN_NOISE_MIN <= ampl <= CHAN_NOISE_MAX):
        raise ValueError(f"tone amplitude out of range: {ampl!r}")
    return f"SET {_channel_token(ch)} CHAN TONE {freq_hz} {ampl}"


CHAN_IMP_PERIOD_MIN, CHAN_IMP_PERIOD_MAX = 1, 65535
CHAN_IMP_WIDTH_MIN, CHAN_IMP_WIDTH_MAX = 1, 255
CHAN_IMP_AMPL_MIN, CHAN_IMP_AMPL_MAX = -100, 100


def chan_impulse_cmd(ch, period: int, width: int, ampl: int) -> str:
    """构造周期冲激干扰命令;period 1..65535, width 1..255, ampl −100..100。"""
    if (isinstance(period, bool) or not isinstance(period, int)
            or not CHAN_IMP_PERIOD_MIN <= period <= CHAN_IMP_PERIOD_MAX):
        raise ValueError(f"impulse period out of range: {period!r}")
    if (isinstance(width, bool) or not isinstance(width, int)
            or not CHAN_IMP_WIDTH_MIN <= width <= CHAN_IMP_WIDTH_MAX):
        raise ValueError(f"impulse width out of range: {width!r}")
    if (isinstance(ampl, bool) or not isinstance(ampl, int)
            or not CHAN_IMP_AMPL_MIN <= ampl <= CHAN_IMP_AMPL_MAX):
        raise ValueError(f"impulse amplitude out of range: {ampl!r}")
    return f"SET {_channel_token(ch)} CHAN IMPULSE {period} {width} {ampl}"
```

`parse_chan_status` 改为**先按 token 数分流**：`len(parts) == 24` 走新格式，
`len(parts) == 17` 走旧格式（`tone=(0,0)`、`impulse=None`），其余抛 ValueError：

```python
def parse_chan_status(line: str) -> dict:
    """解析通道状态行（24 token 新格式 / 17 token 旧格式兼容）。"""
    parts = line.split()
    if len(parts) == 24:
        #  0    1     2      3      4      5    6     7      8        9      10     11
        # CHx CHAN ON|OFF NOISE <n> TONE <f> <a> IMPULSE <p> <w> <a> TAP0..TAP3(12 个)
        # ⚠ TONE 关键字在 parts[5]、参量在 [6]/[7]；IMPULSE 关键字在 parts[8]、参量在
        #   [9]/[10]/[11]；抽头从 parts[12] 起。整段从 5 起必须 +1，别照抄旧行的下标习惯。
        if (parts[0] not in ("CH1", "CH2") or parts[1] != "CHAN"
                or parts[2] not in ("ON", "OFF") or parts[3] != "NOISE"
                or parts[5] != "TONE" or parts[8] != "IMPULSE"):
            raise ValueError(f"bad chan status line: {line!r}")
        noise = _parse_int(parts[4])
        tone_freq = _parse_int(parts[6])
        tone_ampl = _parse_int(parts[7])
        imp_period = _parse_int(parts[9])
        imp_width = _parse_int(parts[10])
        imp_ampl = _parse_int(parts[11])
        taps = _parse_taps(parts[12:])
        tone = (tone_freq, tone_ampl)
        # ⚠ 固件初值是 chan_imp[2] = {0, 0}(main.c:307), 从未配置过冲激时 GET 发
        #   "IMPULSE 0 0 0"。period=0 不可能来自合法配置(固件要求 period>=1), 所以
        #   全 0 是"未配置"哨兵, 按旧 17-token 形式回报 impulse=None。
        #   **只认全 0**: 半 0 半非 0(如 "0 8 30")是损坏行, 仍按范围校验报错。
        #   不这样处理的话, 板子一上电/一复位, GET 的行就会被判非法 —— 实测
        #   host 侧 controller 会记 "protocol parse error" 且不发 chan_status_changed。
        impulse = None if (imp_period, imp_width, imp_ampl) == (0, 0, 0) \
            else (imp_period, imp_width, imp_ampl)
    elif len(parts) == 17:
        if (parts[0] not in ("CH1", "CH2") or parts[1] != "CHAN"
                or parts[2] not in ("ON", "OFF") or parts[3] != "NOISE"):
            raise ValueError(f"bad chan status line: {line!r}")
        noise = _parse_int(parts[4])
        taps = _parse_taps(parts[5:])
        tone = (0, 0)
        impulse = None
    else:
        raise ValueError(f"bad chan status line: {line!r}")
    if not CHAN_NOISE_MIN <= noise <= CHAN_NOISE_MAX:
        raise ValueError(f"noise out of range: {line!r}")
    if not CHAN_TONE_FREQ_MIN <= tone[0] <= CHAN_TONE_FREQ_MAX \
            or not CHAN_NOISE_MIN <= tone[1] <= CHAN_NOISE_MAX:
        raise ValueError(f"tone out of range: {line!r}")
    if impulse is not None and (
            not CHAN_IMP_PERIOD_MIN <= impulse[0] <= CHAN_IMP_PERIOD_MAX
            or not CHAN_IMP_WIDTH_MIN <= impulse[1] <= CHAN_IMP_WIDTH_MAX
            or not CHAN_IMP_AMPL_MIN <= impulse[2] <= CHAN_IMP_AMPL_MAX):
        raise ValueError(f"impulse out of range: {line!r}")
    return {"enabled": parts[2] == "ON", "noise": noise,
            "tone": tone, "impulse": impulse, "taps": taps}
```

字段范围校验与既有 `noise` 同风格（抽头那两行照抄既有逻辑，这里多出的
gain/delay 范围检查是本轮有意收紧：固件 `GET` 只发出 `sign9()` 的 −256..255
与 0..511，合法输出必然通过）。

其中 `_parse_int` 与 `_parse_taps` 是本地小助手（`_parse_taps` 校验 4 组
`TAPk <g> <d>` 的既有逻辑原样搬入）：

```python
def _parse_int(text):
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad integer token: {text!r}") from exc


def _parse_taps(parts):
    if len(parts) != 12:
        raise ValueError(f"expected 4 taps, got: {parts!r}")
    taps = []
    for k in range(CHAN_TAP_COUNT):
        base = k * 3
        if parts[base] != f"TAP{k}":
            raise ValueError(f"expected TAP{k}: {parts!r}")
        gain = _parse_int(parts[base + 1])
        delay = _parse_int(parts[base + 2])
        if not CHAN_TAP_GAIN_MIN <= gain <= CHAN_TAP_GAIN_MAX:
            raise ValueError(f"tap gain out of range: {gain!r}")
        if not CHAN_TAP_DELAY_MIN <= delay <= CHAN_TAP_DELAY_MAX:
            raise ValueError(f"tap delay out of range: {delay!r}")
        taps.append((gain, delay))
    return taps
```

- [ ] **Step 4: 跑测试确认通过**

Run: `host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量上位机测试**

Run: `host/.venv/Scripts/python -m pytest host/tests/`
Expected: 全绿（既有 685 项 + 新增）

- [ ] **Step 6: 提交**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat(host): tone/impulse command builders and 24-token parser

parse_chan_status branches on token count so the archived 17-token form
still parses, reporting tone as (0,0) and impulse as None. Without that
the existing firmware tests would break the moment the new firmware
starts emitting 24 tokens.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: `test_bridge.py` 命令断言

**Files:**
- Modify: `scripts/test_bridge.py`

**Interfaces:**
- Consumes: Task 7 的命令与 24-token `GET`（`ACTIVE_CH`、`CMD_TIMEOUT` 等既有辅助）
- Produces: 板上协议断言（本任务不加 RTL、不加固件）

**⚠ 本任务必须同时修一条**既有**断言（Task 7 把它改红了）：**
`scripts/test_bridge.py:424` 是

```python
cmd("GET CH1 CHAN", "CH1 CHAN ON NOISE 0 TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
```

`cmd()` 用**精确相等**（`line == expect`，见 `:119-126`），所以固件改成 24 token 后这条会 FAIL。
**但只是"插入 `TONE … IMPULSE …`"是不够的**：`cmd()` 会把 24 个字段全部钉死，而
tone/impulse 的**影子值**是跨运行持续存在的（本脚本的复位前导段 `:81-105` 只复位
波形/调制/DIGMOD，**不复位 CHAN/NOISE/TAP**，既有信道块是在块内自建状态的）。
所以若把 `:424` 写成 `TONE 0 0 IMPULSE 0 0 0`，它就只在**刚上电/刚复位**时成立：
上一次运行结束时 CH1 的影子是 `TONE 1000000 0 IMPULSE 1000 8 -100`，第二次运行
不重启桥就会在这里挂掉。

**因此 `:424` 必须先由块内命令把要断言的字段建出来**，插在 `SET CH1 TAP 1 -128 2`
之后、`SET CH1 CHAN ON` 之前（这样既有的那条 `CHAN COMMIT` 会一并锁存）：

```python
cmd("SET CH1 CHAN TONE 0 0", "OK")        # 块内自建: GET 断言的字段必须由本块决定, 不能靠上电默认值
cmd("SET CH1 CHAN IMPULSE 1 1 0", "OK")   # period/width 不能写 0: 固件要求 >=1;
                                          # ampl=0 即为"关闭", 是可达且可重复的关闭态
```

然后 `:424` 改成：

```python
cmd("GET CH1 CHAN", "CH1 CHAN ON NOISE 0 TONE 0 0 IMPULSE 1 1 0 "
                    "TAP0 255 0 TAP1 -128 2 TAP2 0 0 TAP3 0 0")
```

**注意 `IMPULSE 0 0 0` 是不可达的**：固件拒绝 `period < 1`，所以"未配置"这个影子态只能
由上电/复位产生，没有任何命令能把它设回去。因此**断言里不要出现 `IMPULSE 0 0 0`** ——
它要么只在首轮成立、要么根本不可达。`IMPULSE 1 1 0`（ampl=0 即关闭）才是随手可设的规范关闭态，
且落在解析器 `(1,1,0)` 的合法区间内。

**这是本任务存在的前提，不是可选项。**

- [ ] **Step 1: 加断言**

插在 `cmd("SET CH2 CHAN OFF", "OK")`（约 `:429`）之后、`# ---- 阶段 2` 之前。
沿用既有 `cmd(s, expect)` 辅助；期望串必须是**固件真实输出**，注意：

- `GET CHx CHAN` 回读的是**影子数组**（用户下发的值），**与是否 COMMIT 无关**；
- `TONE … <ampl=0>` 会把 GPIO bit9 清零（`ampl==0` 视同关闭），但回读仍报用户下发的 `0`；
- **`IMPULSE 0 0 0` 不可达**（见上），故本块 CH1 的冲激起始态是上一步刚设的 `1 1 0`，
  下面三条 CH1 的 `GET` 期望里冲激段都写 `IMPULSE 1 1 0`；
- 该区块里 CH1 此前已 `CHAN OFF`、噪声 0、抽头 `TAP0 255 0`/`TAP1 -128 2`；
  CH2 全 0。**所以期望串里的 `ON|OFF`、`NOISE`、`TAP*` 部分不是笔误。**
- 本块 CH2 先设冲激再 `GET`，故 CH2 的期望里可以出现非零冲激值 —— 与 CH1 的起始态不同。

```python
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
```

最后两条是**回归护栏**：固件曾用 `parse_long`（上限写死 1000000）解析频率，
把 (1MHz, 25MHz] 全部误判为超范围。`2500001` 正好落在那个区间里，这组断言若
再次变红就说明回退到了旧解析器。

区块结束时 CH1/CH2 的**使能位 9/10 仍是置位的**（最后一条 IMPULSE 的 ampl≠0），
而 `chan_en`(bit0) 为 0 —— 即干扰源被配置了但整条 `chan_impair` 仍在旁路。
这是**有意的**：后段（阶段 2）只走 DIGMOD/USER，不读通道损伤状态，且
`CHAN OFF` 时旁路逐位透传，故不需要额外清理。**别为此再加 4 条 CLEAR**，
那只会让计数与 150 基线对不上。

- [ ] **Step 2: 上板跑（需板卡 + 已下载最新 bit/ELF）**

```bash
bash scripts/program_board.sh
python scripts/test_bridge.py COM11
```

Expected: 末行 `RESULT: ALL PASS`，**0 FAIL**。上面新增 **22** 条 `cmd()`（本块 18 条 +
`:424` 前自建状态的 2 条 + CH2 起手自建状态的 2 条）；另有 1 条既有 `GET CH1 CHAN` 被改写
（不计入新增）。故默认（不带 `--user-buffer`）运行的断言总数应由 150 升到 **172** ——
但这是**推算值**：脚本不打印总数，只逐条打印 `PASS/FAIL` 与末行 `RESULT:`。实现者请把
实际 `PASS` 行数一并报出来（`python scripts/test_bridge.py COM11 | grep -c '^PASS '`），
好让 Task 11 的文档用真实数字；判据只认 `fails == 0`。

**并且必须连跑两次**（不重启桥、不重新下载），两次都要 `ALL PASS` —— 这正是
上面自建状态那段要解决的问题；只跑一次是证明不了可重复性的。

- [ ] **Step 3: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test(bridge): tone and impulse command assertions

Covers SET CHx CHAN TONE/IMPULSE acceptance and each out-of-range bound,
plus CH1/CH2 readback of the new fields. Also updates the pre-existing
GET CH1 CHAN assertion, which pinned the old 17-token line and has been
red since the firmware started emitting 24 tokens.

The CH2 case uses 2500001 Hz on purpose: the frequency used to go through
parse_long, whose 1000000 cap silently rejected most of the advertised
0..25000000 range.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: 示波器用例

**Files:**
- Modify: `scripts/scope_verify.py`

**Interfaces:**
- Consumes: Task 8 的构造器、既有 `scope_wave`/`scope_acq`/`wav_fft`/`waveform_sigma`

- [ ] **Step 1: 加两个用例**

插在 AWGN 噪底用例的 `finally` 之后、`setup_board(["SET DIGMOD MODE OFF"])`
（`scripts/scope_verify.py:1312`，即「DIGMOD OFF 恢复标准正弦」用例）**之前** ——
就是既有信道损伤两用例（多径在 `:1229`、AWGN 在 `:1274`）的末尾。
锚点用 `chan_noise` 那个 `finally` 块，别插到 `reset_board` 或校准段附近。

两个用例都在 `finally` 里复位自己用的干扰源**并关掉 `CH1 CHAN`**，所以插在这里
不会污染其后的 DIGMOD 用例。（⚠ 最初的版本只复位了干扰源、**没有关 CHAN**，
那是不够的 —— 见下面每个用例 `finally` 里的说明：`chan_impair` 在 `enable=1`
时会把 AWG 输入换掉，抽头又是全 0，于是输出恒 0，后一个用例必然读成直线。）

**判据只做相对行为**（沿用噪声用例的教训：
不拿数字域理论值做绝对比对），并把实测/模型比值回报在 detail 里。

```python
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
                     "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT",
                     "SET CH1 CHAN TONE %d 0" % CHAN_TONE_FREQ,
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
```

冲激用例：**必须用宽冲激**（几十个采样点），因为 1 个采样点的冲激会被
DAC 零阶保持 + 模拟重建滤波严重削平。

```python
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
                     "SET CH1 CHAN ON", "SET CH1 CHAN COMMIT",
                     "SET CH1 CHAN IMPULSE %d %d 0" % (CHAN_IMP_PERIOD, CHAN_IMP_WIDTH),
                     "SET CH1 CHAN COMMIT"])
        scope_acq(200e-6, 1.0)
        _, base = scope_wave("CHAN1")
        base_span = float(np.max(base) - np.min(base))
        setup_board(["SET CH1 CHAN IMPULSE %d %d %d"
                     % (CHAN_IMP_PERIOD, CHAN_IMP_WIDTH, CHAN_IMP_AMPL),
                     "SET CH1 CHAN COMMIT"])
        scope_acq(200e-6, 1.0)
        xinc, y = scope_wave("CHAN1")
        y = y - np.mean(y)
        thresh = 8.0
        crossings = int(np.sum((y[:-1] <= thresh) & (y[1:] > thresh)))
        span = float(np.max(y) - np.min(y))
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
        report(chan_imp, count_ok and amp_ok and samples_ok,
               "rise-crossings=%d (expect ~%d) span=%.0f base=%.0f "
               "xinc=%.1ens samples/impulse=%.1f"
               % (crossings, round(expected_count), span, base_span,
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
```

常量放进既有 `CHAN_*` 块（`:676-681` 一带），与本文件既有风格一致，
不要把这些数字内联在两处：

```python
CHAN_TONE_FREQ = 200_000          # 单音探针频率(Hz; 窗 200us -> bin 5kHz, 落在 bin 40)
CHAN_TONE_AMPLS = (20, 40, 60)    # 幅度单调性扫描(%满幅)
CHAN_IMP_PERIOD = 2000            # 冲激周期(DAC 采样点 @50MHz -> 40us)
CHAN_IMP_WIDTH = 64               # 冲激宽度(DAC 采样点 -> 1.28us; 必须够宽, 见上)
CHAN_IMP_AMPL = 60                # 冲激幅度(%满幅)
```

- [ ] **Step 2: 上板跑**

```bash
python scripts/scope_verify.py COM11
```
Expected: `RESULT: 58/58 PASS`（既有 56 + 新增 2）

**若冲激计数判据不收敛**，先量清实际时基/记录长度再调容差 ——
**不要**改成"看波形像不像"这种不可复现的判据。

- [ ] **Step 3: 提交**

```bash
git add scripts/scope_verify.py
git commit -m "test(scope): narrowband tone and periodic impulse cases

Both assert relative behaviour only. The analog path attenuates anything
the DAC zero-order hold reconstructs, which is how the AWGN sigma came
out at 0.65x its digital model; the same applies to a narrow impulse, so
the impulse case deliberately uses a 64-sample-wide pulse and the tone
case checks spectral position plus monotonic growth rather than absolute
amplitude.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 11: 文档与上板验收

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新协议说明**

`README.md` 串口协议节加：

```
SET CH1 CHAN TONE <freq_hz 0..25000000> <ampl 0..100>
SET CH1 CHAN IMPULSE <period 1..65535> <width 1..255> <ampl -100..100>
GET CH1 CHAN -> CH1 CHAN ON NOISE 30 TONE 1000000 20 IMPULSE 1000 8 30 TAP0 255 0 ...
```
并说明：干扰源与 AWGN 同位置（`fade_fir` 之后）；开关走 GPIO 立即生效、
数值走 BRAM **需 `CHAN COMMIT`**；`GET CHx CHAN` 由 17 token 增至 24 token，
**旧 17-token 状态行仍可解析**（TONE 视为关闭）。

- [ ] **Step 2: 更新计数与测试速查**

`CLAUDE.md` 测试速查节更新（**除下列数字外，TB 项数一律实测得到，不要照抄本计划**）：

- 示波器闭环 56 → **58** 项（新增的单音 + 冲激两组）。
- 上位机 `host/tests/`：**703 项**（本计划新增单音/冲激模型、构造器、解析器用例后的实测值；
  原文档写的 685 已过时）。
- 新增 TB：`tone_gen_tb`、`impulse_gen_tb`；并更新 `chan_impair_tb` / `chan_cfg_tb` /
  `reg_bridge_tb` / `awg_top_tb` 的项数。**这些数字请分别跑一遍 TB 得到，不要照抄本计划。**

**⚠ 两套 TB 的打印约定不同**，直接用 `grep -c '^PASS '` 数会把新 TB 数成 1：

- **逐项打印**（`chan_impair_tb` / `chan_cfg_tb` / `reg_bridge_tb` / `fade_fir_tb` /
  `noise_gen_tb` / `awg_top_tb`）：每条检查打一行 `PASS <名字>`，直接数即可。
  控制器实测基线：`chan_impair_tb` 13、`chan_cfg_tb` 32、`reg_bridge_tb` 79、
  `fade_fir_tb` 13、`noise_gen_tb` 17。
- **只打结论行**（`tone_gen_tb` / `impulse_gen_tb`）：失败才打 `FAIL <name>`，
  结尾只打一行 `PASS tone_gen` 或 `FAIL tone_gen (<errs>)`。所以它们的"项数"要从
  TB 源码数比较点：`tone_gen_tb` = 3 个 `run_case` × 12 拍 + 6 拍 disabled + 6 拍 silent
  = **48**；`impulse_gen_tb` = **8** 个 `chk()`（其中 "disabled" 那条在 `repeat (4)` 里）。
  文档里请把这两条的计数方式写清楚（"比较点"而非"PASS 行"），否则下一个人会以为它们只有 1 项。

```bash
SIM="<LOCAL_PATH>2025.2/2025.2/Vivado/bin"
cd <LOCAL_PATH>   # 别在仓库根跑, xsim 会写 xsim.dir/
for tb in tone_gen_tb impulse_gen_tb chan_impair_tb chan_cfg_tb reg_bridge_tb fade_fir_tb noise_gen_tb; do
  "$SIM/xvlog.bat" "<LOCAL_PATH>"/src/rtl/*.v \
                   "<LOCAL_PATH>"/src/tb/$tb.v >/dev/null 2>&1
  "$SIM/xelab.bat" $tb -s ${tb}_s >/dev/null 2>&1
  printf "%s: " "$tb"
  "$SIM/xsim.bat" ${tb}_s -runall 2>/dev/null | grep -E "^(PASS|FAIL) " | tail -1
done
# awg_top_tb 需要 -sv: xvlog --sv ... src/rtl/*.v src/tb/awg_top_tb.v
```


- [ ] **Step 3: 加经验条目**

`CLAUDE.md` 的「信道损伤经验」节追加（**日期写 2026-09-11**）：

```
- **干扰源的量法(2026-09-11)**: 单音用 FFT 谱线位置 + 幅度单调性判据; 冲激必须用
  **宽脉冲(几十个采样点)** —— 1 个采样点的冲激会被 DAC 零阶保持 + 模拟重建滤波
  严重削平, 与噪声 σ 实测只有数字域 0.65 倍是同一机制。冲激用例的**时基不能用长时基**:
  示波器记录长度有限, 2ms/div 会把采样率压到 ~3MSa/s, 1.28us 的冲激只剩几个采样点,
  计数判据退化成噪声; 改用 200us/div(2ms 窗, ~50 个冲激)并把"每冲激示波器采样点数 ≥4"
  并入判据。两者都只判相对行为, 实测/模型比值回报在 detail 里持续观察。
  干扰源与 AWGN **同位置**(fade_fir 之后), 不过信道 FIR —— 语义是"接收端叠加的外部信号"。
- **TONE/IMPULSE 的数值参数同样要 `CHAN COMMIT` 才锁存**(与抽头一致): 开关走 GPIO
  立即生效、数值走 BRAM 等 COMMIT。故 `SET CHx CHAN TONE <f>` 单独发不会改变当前输出,
  必须再发 `SET CHx CHAN COMMIT`; `GET CHx CHAN` 回读的是**影子数组**(用户下发的值),
  与是否已 COMMIT 无关。
```

- [ ] **Step 4: 完整构建与下载**

**⚠ 三条硬性修正（都是本计划执行时实测出来的，不要改回去）：**

1. **`build_bitstream.py` 必须显式注入 Vivado 2025.2 路径。** 脚本里
   `VIVADO = os.environ.get("VIVADO_BIN", r"<LOCAL_PATH>")`
   —— 不注入就会去用 **2018.3**，而本工程是 2025.2 工程（2018.3 打开会只读失败）。
2. **固件工作区是 `sdk/ws2025_gem_fresh`，不是 README/CLAUDE.md 里写的 `sdk/ws2025`。**
   `sdk/ws2025` 的 BSP 没有 lwip，编不过当前固件。
3. **`app build` 打印 `ok` 不代表真的编译了**（它只查 ELF 存在、不看时间戳），
   所以构建后必须**自己断言产物新鲜**。

```bash
VIVADO_BIN="<LOCAL_PATH>2025.2/2025.2/Vivado/bin/vivado.bat" python scripts/build_bitstream.py
python scripts/check_bitstream.py
bash scripts/program_board.sh
python vivado2025.py app build awg_bridge --workspace sdk/ws2025_gem_fresh
ls -la --time-style=full-iso \
  sdk/ws2025_gem_fresh/awg_bridge/src/main.c \
  sdk/ws2025_gem_fresh/awg_bridge/build/CMakeFiles/awg_bridge.elf.dir/main.c.obj \
  sdk/ws2025_gem_fresh/awg_bridge/build/awg_bridge.elf   # obj 必须晚于 src, elf 与 obj 同时刻
python vivado2025.py deploy jtag --app awg_bridge --workspace sdk/ws2025_gem_fresh
```

**并把 README.md / CLAUDE.md 里所有 `sdk/ws2025` 的构建/部署命令改成 `sdk/ws2025_gem_fresh`**
（`README.md:151,157,158`、`CLAUDE.md` 板级验证流程节）—— 否则下一个人照文档做会拿到
一个"ok 但其实没编译"的结果。

- [ ] **Step 5: 验收（对应规格验收标准 1/3/5/6）**

```bash
python scripts/test_bridge.py COM11            # 新增断言全过, 末行 RESULT: ALL PASS
python scripts/scope_verify.py COM11           # 58/58
python scripts/scope_verify_user.py COM11      # 4/4 不回归
host/.venv/Scripts/python -m pytest host/tests/   # 703 项全绿
```
逐条核对：
1. `CHAN OFF` 时 DAC 输出与改动前逐位一致（既有 56 项正是这个证据）。
3. 单音谱线位置与幅度、冲激计数与幅度符合判据。
5. 既有 56 项与 USER 4/4 不回归。
6. 实现后时序收敛（WNS > 0）、无 DSP48 级联长路径 ——
   查 `awg/awg.runs/impl_1/host_bd_wrapper_timing_summary_routed.rpt`。
   本计划新增 `tone_gen` / `impulse_gen` 的乘法与加法，**WNS 是必须回报的实测值**，
   不是"应该没问题"（既有基线只有 0.071 ns，余量极小）。

- [ ] **Step 6: 提交**

```bash
git add README.md CLAUDE.md build_manifest.json
git commit -m "docs: tone/impulse interference protocol, counts and measurement notes

Records the two measurement facts this round produced: interference joins
after the channel FIR because it models an external signal at the
receiver, and impulses must be tens of samples wide or the reconstruction
filter flattens them. The impulse case also needs a fast timebase, since
a slow one drops the scope's sample rate below the impulse width.

Also corrects the documented build/deploy workspace to ws2025_gem_fresh.
The documented ws2025 has no lwip in its BSP and cannot compile the
firmware, and app.build reports ok on a stale ELF, so following the old
instructions silently produced a build that never happened.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 自审记录

**规格覆盖**：规格 §1（A 组数值模型）→ Task 1/2/3；§2.3/2.4/2.5 → Task 2/3/4；
§2.6 → Task 5；§2.8 → Task 6；§3（字映射 + 使能位）→ Task 5/6/7；§4 → Task 7/8/9；
§5（上位机协议层）→ Task 8；§7（验证）→ Task 10/11；§6（构建）→ Task 11。
规格 §2.1/2.2/2.7 属 B 组（`iq_impair` / `sincos_rom` / 最终选择改造），不在本计划。
冲激 period 上限 65535、宽度上限 255、单音 24 位累加器（上限约 50 MHz、协议收窄到
25 MHz）均与规格一致。

**占位符扫描**：无 TBD / TODO / "later" 占位。Task 2 的 `A0..A5` 已在计划中算出
具体值（`[0,0,0,1,1,2]`），Step 4 只做复核而非填空。

**类型一致性**：`tone_samples(count, freq_hz, ampl, sample_rate_hz)`、
`impulse_samples(count, period, width, ampl)` 在 Task 1 定义，Task 2/4 的 golden 与
Task 8 的构造器参数顺序一致；`chan_tone_cmd(ch, freq_hz, ampl)`、
`chan_impulse_cmd(ch, period, width, ampl)` 的参数顺序与固件命令
`SET CHx CHAN TONE <freq> <ampl>` / `IMPULSE <period> <width> <ampl>` 一致。
`chan_impair` 的新端口名在 Task 4 定义、Task 6 实例化时一致。
`chan_cfg` 的新输出名在 Task 5 定义、Task 6 接线时一致。

**已知需在执行时按既有代码风格落地的地方**（语义是硬要求，表达形式可调）：
1. Task 5 的 `S_IDLE` 起始地址须改用 `cur_word`（CH1 起始仍为 0、CH2 为 4，与既有
   行为一致），`done_ch` 改为 `(state == S_CAP) && (step == STEPS - 1)`。
2. Task 7 的频率换算必须用 `unsigned long long` 避免 32 位溢出
   （`f << 24` 在 f=25e6 时是 4.19e14）。
3. Task 4 的 TB 用例需确认 `chan_impair_tb` 既有的端口连接与 `chk` 任务风格后补齐。
4. Task 5 的 TB 需按 `chan_cfg_tb` 既有的 `bram` / commit 触发辅助命名对齐。

**执行前扫描已修正的计划缺陷**（控制器在派发前就地修掉，不再留给实现者）：
1. **Critical** — Task 7 原稿只写 `chan_tone`/`chan_imp` 与 `write_chan_ext()`，
   **从未置位 GPIO 的使能位**，干扰源永远不会生效。已改为：`ampl == 0` 清
   bit9/bit10，否则置位，并调用 `write_chan_ctrl()`。
2. **Important** — Task 1 的 `test_tone_frequency_sets_accumulator_step` 与同任务的
   实现差一位（测试用 `acc[n]`，实现用 `acc[n-1]`）。已改为镜像实现的索引约定。
3. **Important** — Task 3 的 TB 假设复位后采样起点落在 `cnt == 0`，而计数器自由运行，
   相位任意 → 会偶发失败。已改为相位无关判据（每 20 拍恰好 4 拍非零）。
4. **Minor** — Task 8 的 `parse_chan_status` 里有一段无副作用的 `if ... pass` 残留，
   已删除。
5. **Minor** — Task 5 的地址表达式在 `word_sel` / `word_sel_next` 两个名字间不一致，
   已统一为 `word_sel` 函数 + `cur_word` / `next_word`。
6. **Important（Task 1 实现者发现，控制器裁定后修正）** — Task 1 的两条断言自身算错：
   `test_tone_is_bounded_by_amplitude` 把 `ampl` 当成 DAC LSB 数（峰值应为
   `round(40×127/100) = 51`，不是 40），已改名并改为精确断言；
   `test_tone_frequency_sets_accumulator_step` 末尾沿用了 Task 2 场景（fword=33554）
   的"前两拍为 0"，但本用例 fword=1e6 → 首个非零在 n=2 且值为 `TONE_ROM[15]`。
   **实现本身正确（符合规格 §1），错的是测试**。
7. **Important（控制器算术复核发现）** — Task 10 冲激用例的期望冲激数写成了
   `y.size / period`，把**示波器采样点**与 **DAC 采样点**混在一起，算出 31 而非
   正确的 500（`y.size=62500` 是示波器点数，`period=2000` 是 50 MHz 下的 DAC 点数）。
   已改为 `(y.size × xinc) / (period / CHAN_SAMPLE_RATE)`，即"窗口时长 ÷ 周期时长"。
   同处把 `base_peak` 的除法改成 `max(base_peak, 1.0)` 兜底，避免静默时除以 0。
   *（本条同时记录一次自我纠正：控制器曾误判 20 µs/div 下 200 kHz 单音会混叠，
   实际 `xinc = 200µs/62500 = 3.2 ns` → 312.5 MSa/s，单音用例无需改动。）*
