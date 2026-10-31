# 数字调制符号流与可变符号率 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将固定 10 kSym/s PRBS7 I/Q 发射器重构为固定映射、可替换符号源和 10 Hz 至 1 MSym/s 可配置的全局数字调制链路。

**Architecture:** 新增 `prbs7_source` 和 `iq_constellation_mapper`，由重构后的 `iq_digital_mod` 以 32 位相位累加器产生符号边界。PS 将原始 `SYMRATE`（Hz）转换为 phase step，经新增独立 AXI GPIO、`reg_bridge` 两级同步输入 PL；模式仍通过 GPIO11 控制。标准 AWG 路径仅在数字模式启用时被 I/Q mux 旁路。

**Tech Stack:** Verilog-2001、Vivado 2018.3 XSIM、Xilinx AXI GPIO、Zynq 裸机 C、Python/PySide6、pytest、PyVISA DSOX1102A 闭环。

---

## 文件结构

| 路径 | 责任 |
|---|---|
| `src/rtl/prbs7_source.v` | PRBS7 状态、6-bit MSB-first look-ahead 和按符号消费的推进 |
| `src/rtl/iq_constellation_mapper.v` | 五种模式的固定 bits→I/Q 真值表和 consume bit 数 |
| `src/rtl/iq_digital_mod.v` | 首符号启动、32 位 phase accumulator、source/mapper 编排 |
| `src/rtl/reg_bridge.v` | 新配置字的两级同步和 `digmod_sym_phase_step` 输出 |
| `src/rtl/awg_top.v` | 连接新 GPIO 字到 `reg_bridge` 和数字发射器 |
| `scripts/create_bd.tcl` | 创建并接线专用 32-bit `axi_gpio_6` |
| `src/tb/prbs7_source_tb.v` | PRBS look-ahead、消费和重启 RTL 验收 |
| `src/tb/iq_constellation_mapper_tb.v` | 固定星座/bit 顺序真值表 RTL 验收 |
| `src/tb/iq_digital_mod_tb.v` | 5×127 GOLD 与六档 phase-step 符号边界验收 |
| `src/tb/reg_bridge_tb.v`、`src/tb/awg_top_tb.v` | 新 GPIO 同步、I/Q mux 和 OFF 回退集成验收 |
| `sdk/src/main.c` | `SYMRATE` 命令、phase-step 计算、新 GPIO 写入、完整 DIGMOD 回读 |
| `host/siggen/protocol.py`、`controller.py`、`ui/widgets.py`、`ui/main_window.py` | 完整 DIGMOD 状态解析、回读和显示 |
| `host/tests/test_*.py` | Python 协议、控制器、数字模型、示波器 helper 与离屏控件回归 |
| `scripts/test_bridge.py`、`scripts/scope_verify.py` | 协议和三档符号率板级闭环 |
| `README.md` | 新配置接口、时序与验收说明 |

本计划不创建 SOURCE 命令、BRAM/AXI-Stream、成帧、RRC 或通信质量分析功能。除文档性改动外，不创建提交；只有用户明确要求时才提交。

### Task 1: 编写 PRBS7 符号源的失败测试

**Files:**
- Create: `src/tb/prbs7_source_tb.v`
- Create: `src/rtl/prbs7_source.v`

- [ ] **Step 1: 写入尚不存在模块的 testbench**

创建 `src/tb/prbs7_source_tb.v`，以 `7'h7f` 为种子、`advance_bits=1/2/4/6` 验证 look-ahead 的 bit 顺序与状态推进。测试台必须把当前 PRBS 的第一个输出放在 `symbol_bits[5]`：

```verilog
`timescale 1ns / 1ps
module prbs7_source_tb;
    reg clk = 1'b0, rst_n = 1'b0, reset_seed = 1'b1;
    reg advance = 1'b0;
    reg [2:0] advance_bits = 3'd1;
    wire [5:0] symbol_bits;
    integer failures = 0;

    always #10 clk = ~clk;
    prbs7_source uut (.clk(clk), .rst_n(rst_n), .reset_seed(reset_seed),
        .advance(advance), .advance_bits(advance_bits), .symbol_bits(symbol_bits));

    task check_bits;
        input [5:0] expected;
        input [255:0] label;
        begin
            if (symbol_bits !== expected) begin
                $display("FAIL %0s got=%b exp=%b", label, symbol_bits, expected);
                failures = failures + 1;
            end
        end
    endtask

    function [6:0] advance_once;
        input [6:0] state;
        begin
            advance_once = {state[5:0], state[6] ^ state[5]};
        end
    endfunction

    function [6:0] advance_n;
        input [6:0] state;
        input [2:0] count;
        integer n;
        reg [6:0] next_state;
        begin
            next_state = state;
            for (n = 0; n < count; n = n + 1)
                next_state = advance_once(next_state);
            advance_n = next_state;
        end
    endfunction

    function [5:0] expected_bits;
        input [6:0] state;
        reg [6:0] s1, s2, s3, s4, s5;
        begin
            s1 = advance_once(state); s2 = advance_once(s1);
            s3 = advance_once(s2); s4 = advance_once(s3); s5 = advance_once(s4);
            expected_bits = {state[6], s1[6], s2[6], s3[6], s4[6], s5[6]};
        end
    endfunction

    reg [6:0] model_state = 7'h7f;
    task consume;
        input [2:0] count;
        begin
            @(negedge clk); advance_bits = count; advance = 1'b1;
            @(negedge clk); advance = 1'b0;
            if (count == 3'd1 || count == 3'd2 || count == 3'd4 || count == 3'd6)
                model_state = advance_n(model_state, count);
        end
    endtask

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk); reset_seed = 1'b0;
        check_bits(expected_bits(model_state), "seed b0..b5");
        consume(3'd1); check_bits(expected_bits(model_state), "one-bit consume");
        consume(3'd2); check_bits(expected_bits(model_state), "two-bit consume");
        consume(3'd4); check_bits(expected_bits(model_state), "four-bit consume");
        consume(3'd6); check_bits(expected_bits(model_state), "six-bit consume");
        @(negedge clk); reset_seed = 1'b1;
        @(negedge clk); reset_seed = 1'b0;
        model_state = 7'h7f;
        check_bits(expected_bits(model_state), "seed restored");
        if (failures == 0) $display("PASS prbs7_source");
        else $display("FAILURES=%0d", failures);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行测试，确认缺少模块导致失败**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" src/tb/prbs7_source_tb.v
"$VIVADO/xelab.bat" prbs7_source_tb -s prbs7_source_sim
```

Expected: `xelab` reports that `prbs7_source` is undefined.

- [ ] **Step 3: 实现最小 PRBS7 source**

创建 `src/rtl/prbs7_source.v`。必须使用以下 LFSR 状态转换和 look-ahead 方向；不能把 `symbol_bits[5]` 改成最后一个 bit：

```verilog
`timescale 1ns / 1ps
module prbs7_source (
    input wire clk,
    input wire rst_n,
    input wire reset_seed,
    input wire advance,
    input wire [2:0] advance_bits,
    output wire [5:0] symbol_bits
);
    reg [6:0] state;
    wire [6:0] s1 = {state[5:0], state[6] ^ state[5]};
    wire [6:0] s2 = {s1[5:0], s1[6] ^ s1[5]};
    wire [6:0] s3 = {s2[5:0], s2[6] ^ s2[5]};
    wire [6:0] s4 = {s3[5:0], s3[6] ^ s3[5]};
    wire [6:0] s5 = {s4[5:0], s4[6] ^ s4[5]};
    wire [6:0] s6 = {s5[5:0], s5[6] ^ s5[5]};

    assign symbol_bits = {state[6], s1[6], s2[6], s3[6], s4[6], s5[6]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || reset_seed)
            state <= 7'h7f;
        else if (advance) begin
            case (advance_bits)
                3'd1: state <= s1;
                3'd2: state <= s2;
                3'd4: state <= s4;
                3'd6: state <= s6;
                default: state <= state;
            endcase
        end
    end
endmodule
```

- [ ] **Step 4: 扩展 testbench 到完整周期**

在 `prbs7_source_tb.v` 中添加 127 次 `consume(3'd1)` 循环，断言每个状态非零、前 127 个状态无重复、第 128 次恢复 `7'h7f`。对 `advance_bits=3'd3/3'd5/3'd7` 断言状态不变。

- [ ] **Step 5: 用 Vivado XSIM 验收模块**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" src/rtl/prbs7_source.v src/tb/prbs7_source_tb.v
"$VIVADO/xelab.bat" prbs7_source_tb -s prbs7_source_sim
"$VIVADO/xsim.bat" prbs7_source_sim -runall
```

Expected: `PASS prbs7_source`，退出码为 0。

### Task 2: 编写固定星座 mapper 的失败测试并实现它

**Files:**
- Create: `src/tb/iq_constellation_mapper_tb.v`
- Create: `src/rtl/iq_constellation_mapper.v`
- Modify: `host/tests/test_digital_mod.py`

- [ ] **Step 1: 在 Python 黄金模型中固定 PRBS seed 的首符号和 QAM4 契约**

在 `host/tests/test_digital_mod.py` 添加以下断言。它们锁定 `PRBS7_SEED=0x7f` 的首个 bit 为 1，因此 BPSK 的首符号必须是负电平；完整 QAM16/QAM64 真值表在本任务的 RTL mapper testbench 中逐码覆盖。

```python
def test_prbs7_seed_preserves_initial_bpsk_and_qpsk_symbols():
    i_bpsk, q_bpsk = generate_symbols("BPSK", 4)
    i_qpsk, q_qpsk = generate_symbols("QPSK", 4)
    assert i_bpsk.tolist() == [-127, -127, -127, -127]
    assert q_bpsk.tolist() == [0, 0, 0, 0]
    assert i_qpsk.tolist() == [-127, -127, -127, -127]
    assert q_qpsk.tolist() == [-127, -127, -127, 127]


def test_qam4_preserves_qpsk_mapping_for_full_prbs_period():
    qam4_i, qam4_q = generate_symbols("QAM4", 127)
    qpsk_i, qpsk_q = generate_symbols("QPSK", 127)
    assert qam4_i.tolist() == qpsk_i.tolist()
    assert qam4_q.tolist() == qpsk_q.tolist()
```

These tests preserve the Python seed contract; mapper truth-table tests below are the exhaustive fixed mapping contract.

- [ ] **Step 2: 运行 Python 测试确认当前黄金模型通过**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_digital_mod.py -q
```

Expected: PASS。此步骤在修改 RTL 前锁定已有软件契约。

- [ ] **Step 3: 创建 mapper testbench 并确认它因模块不存在而失败**

创建 `src/tb/iq_constellation_mapper_tb.v`，对每一种模式枚举完整输入码。最小断言接口为：

```verilog
task check_map;
    input [2:0] mode_in;
    input [5:0] bits_in;
    input signed [7:0] i_exp, q_exp;
    input [2:0] consume_exp;
    begin
        mode = mode_in; symbol_bits = bits_in; #1;
        if (i_symbol !== i_exp || q_symbol !== q_exp || consume_bits !== consume_exp) begin
            $display("FAIL mode=%0d bits=%b i=%0d/%0d q=%0d/%0d consume=%0d/%0d",
                mode_in, bits_in, i_symbol, i_exp, q_symbol, q_exp,
                consume_bits, consume_exp);
            failures = failures + 1;
        end
    end
endtask

initial begin
    check_map(3'd0, 6'b000000, 8'sd127, 8'sd0, 3'd1);
    check_map(3'd0, 6'b100000, -8'sd127, 8'sd0, 3'd1);
    check_map(3'd1, 6'b010000, 8'sd127, -8'sd127, 3'd2);
    check_map(3'd2, 6'b100000, -8'sd127, 8'sd127, 3'd2);
    check_map(3'd3, 6'b011000, 8'sd42, 8'sd126, 3'd4);
    check_map(3'd4, 6'b011101, 8'sd54, -8'sd90, 3'd6);
    check_map(3'd5, 6'b000000, 8'sd0, 8'sd0, 3'd0);
end
```

为 QAM16 的 4 个 I 码和 4 个 Q 码、QAM64 的 8 个 I 码和 8 个 Q 码各写一组循环或显式断言，确保全表覆盖。

- [ ] **Step 4: 实现纯组合 mapper**

创建 `src/rtl/iq_constellation_mapper.v`，端口必须为：

```verilog
module iq_constellation_mapper (
    input wire [2:0] mode,
    input wire [5:0] symbol_bits,
    output reg signed [7:0] i_symbol,
    output reg signed [7:0] q_symbol,
    output reg [2:0] consume_bits
);
```

使用 `b0=symbol_bits[5]` 到 `b5=symbol_bits[0]`。沿用当前模式编码 BPSK=0、QPSK=1、QAM4=2、QAM16=3、QAM64=4；将现有 `iq_digital_mod.v` 的 case 真值表原样移到该模块，默认值为零 I/Q、零消费。

- [ ] **Step 5: 用 XSIM 验收 mapper 真值表**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" src/rtl/iq_constellation_mapper.v src/tb/iq_constellation_mapper_tb.v
"$VIVADO/xelab.bat" iq_constellation_mapper_tb -s iq_constellation_mapper_sim
"$VIVADO/xsim.bat" iq_constellation_mapper_sim -runall
```

Expected: `PASS iq_constellation_mapper`，退出码为 0。

### Task 3: 以 phase accumulator 重构发射调度器

**Files:**
- Modify: `src/rtl/iq_digital_mod.v`
- Modify: `src/tb/iq_digital_mod_tb.v`
- Test: `host/siggen/digital_mod.py`

- [ ] **Step 1: 将 testbench 的 DUT 端口改为 phase step 并写入六档失败测试**

删除测试台中的 `localparam integer SYM_TICKS` 和 DUT 参数覆盖；加入：

```verilog
localparam [31:0] STEP_10K  = 32'd858993;
localparam [31:0] STEP_25K  = 32'd2147483;
localparam [31:0] STEP_100K = 32'd8589934;
localparam [31:0] STEP_250K = 32'd21474836;
localparam [31:0] STEP_500K = 32'd42949672;
localparam [31:0] STEP_1M   = 32'd85899345;
reg [31:0] sym_phase_step = STEP_10K;
```

将实例化改为：

```verilog
iq_digital_mod uut (
    .clk(clk), .rst_n(rst_n), .enable(enable), .mode(mode),
    .sym_phase_step(sym_phase_step),
    .i_sample(i_sample), .q_sample(q_sample), .symbol_valid(symbol_valid)
);
```

新增 `check_symbol_rate(step, low_ticks, high_ticks)`：捕获连续 128 个 `symbol_valid` 周期，断言相邻边界间隔只允许为 `low_ticks` 或 `high_ticks`，且首符号仍在 enable 后一个时钟产生。六组期望为 `(5000,5001)`、`(2000,2001)`、`(500,501)`、`(200,201)`、`(100,101)`、`(50,51)`。

- [ ] **Step 2: 运行 XSIM，确认新增端口和速率测试失败**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
"$VIVADO/xelab.bat" iq_digital_mod_tb -s iq_digital_mod_sim
```

Expected: 失败原因是现有 DUT 没有 `sym_phase_step` 端口或速率断言失败。

- [ ] **Step 3: 用 source、mapper 和 phase accumulator 替换旧的固定计数器**

将 `iq_digital_mod` 的端口改为：

```verilog
module iq_digital_mod (
    input wire clk,
    input wire rst_n,
    input wire enable,
    input wire [2:0] mode,
    input wire [31:0] sym_phase_step,
    output reg signed [7:0] i_sample,
    output reg signed [7:0] q_sample,
    output reg symbol_valid
);
```

实现核心时序如下；`first_symbol` 只在 enable 上升后的首个时钟为真，mapper 用边界前的 look-ahead，PRBS 在同一 posedge 后推进：

```verilog
reg [31:0] phase_acc;
reg active;
wire [5:0] symbol_bits;
wire signed [7:0] mapped_i, mapped_q;
wire [2:0] consume_bits;
wire [32:0] phase_sum = {1'b0, phase_acc} + {1'b0, sym_phase_step};
wire symbol_tick = active && phase_sum[32];
wire emit_symbol = enable && (!active || symbol_tick);

prbs7_source u_prbs (
    .clk(clk), .rst_n(rst_n), .reset_seed(!enable),
    .advance(emit_symbol && active), .advance_bits(consume_bits),
    .symbol_bits(symbol_bits)
);
iq_constellation_mapper u_mapper (
    .mode(mode), .symbol_bits(symbol_bits), .i_symbol(mapped_i),
    .q_symbol(mapped_q), .consume_bits(consume_bits)
);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        phase_acc <= 32'd0; active <= 1'b0;
        i_sample <= 8'sd0; q_sample <= 8'sd0; symbol_valid <= 1'b0;
    end else if (!enable) begin
        phase_acc <= 32'd0; active <= 1'b0;
        i_sample <= 8'sd0; q_sample <= 8'sd0; symbol_valid <= 1'b0;
    end else begin
        symbol_valid <= 1'b0;
        if (!active) begin
            active <= 1'b1; phase_acc <= 32'd0;
            i_sample <= mapped_i; q_sample <= mapped_q; symbol_valid <= 1'b1;
        end else begin
            phase_acc <= phase_sum[31:0];
            if (phase_sum[32]) begin
                i_sample <= mapped_i; q_sample <= mapped_q; symbol_valid <= 1'b1;
            end
        end
    end
end
```

Do not advance PRBS on the first symbol: the first mapper value must read seed look-ahead; the following symbol tick advances into the next source state. Before finalizing this step, simulate the first five BPSK/QPSK/QAM16/QAM64 vectors and compare them to existing expectations.

- [ ] **Step 4: 恢复并运行完整 5×127 GOLD 与六档速率测试**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
"$VIVADO/xelab.bat" iq_digital_mod_tb -s iq_digital_mod_sim
"$VIVADO/xsim.bat" iq_digital_mod_sim -runall > iq_digital_mod.sim.log
PYTHONPATH=host host/.venv/Scripts/python -c "import re; from pathlib import Path; from siggen.digital_mod import generate_symbols; t=Path('iq_digital_mod.sim.log').read_text(encoding='utf-8', errors='replace'); r=[tuple(map(int,x)) for x in re.findall(r'GOLD mode=(\d+) index=(\d+) i=(-?\d+) q=(-?\d+)',t)]; names=('BPSK','QPSK','QAM4','QAM16','QAM64'); a={m:[(i,q) for _,i,q in sorted((n,i,q) for k,n,i,q in r if k==m)] for m in range(5)}; e={m:[(int(i),int(q)) for i,q in zip(*generate_symbols(n,127))] for m,n in enumerate(names)}; assert all(len(a[m])==127 for m in range(5)); assert a==e"
```

Expected: `PASS iq_digital_mod` and no Python assertion. The 635 ordered records must be exactly unchanged.

### Task 4: 为新 phase-step 字扩展 PL bridge 和 top-level

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/reg_bridge_tb.v`
- Modify: `src/tb/awg_top_tb.v`

- [ ] **Step 1: 写入 `reg_bridge_tb` 的失败断言**

在现有测试台中新增 `gpio12_in` 驱动，连接 DUT 的 `digmod_sym_phase_step` 输出。写入以下两类断言：

```verilog
gpio12_in = 32'h0123_4567;
repeat (2) @(posedge clk);
check(digmod_sym_phase_step === 32'h0123_4567, "sym phase step after two FF stages");

// GPIO11 的旧字段必须不受新字影响。
gpio11_in = (3'd5 << 17) | (8'd25 << 1) | (8'd75 << 9) | 1'b1;
gpio12_in = 32'h0555_5555;
repeat (2) @(posedge clk);
check(digmod_sel === 3'd5, "DIGMOD selector preserved");
check(ch1_pulse_duty === 8'd25 && ch2_pulse_duty === 8'd75 && ch_link_en,
      "GPIO11 low fields preserved");
```

- [ ] **Step 2: 运行 bridge test，确认新端口/输出尚不存在**

Run:

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$VIVADO/xelab.bat" reg_bridge_tb -s reg_bridge_symrate_sim
```

Expected: 端口连接或符号缺失失败。

- [ ] **Step 3: 实现两级同步和 top-level 连接**

在 `reg_bridge` 增加：

```verilog
input wire [31:0] gpio12_in,
output wire [31:0] digmod_sym_phase_step,
reg [31:0] s12, r12;
```

在单独的 2-FF always block 中加入：

```verilog
always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
        s12 <= 32'd0;
        r12 <= 32'd0;
    end else begin
        s12 <= gpio12_in;
        r12 <= s12;
    end
assign digmod_sym_phase_step = r12;
```

在 `awg_top` 增加 `gpio12_in` 输入、`digmod_sym_phase_step` wire、`reg_bridge` 端口连接，并把 `u_dig` 的新端口连接为：

```verilog
.sym_phase_step(digmod_sym_phase_step)
```

仍由 `digmod_sel` 的合法 1..5 范围启用 I/Q mux；OFF/6/7 不得改变 `a1/a2` 路径。

- [ ] **Step 4: 扩展 awg_top selector test 并运行两个集成 testbench**

在 `awg_top_tb` 写入 10 k phase step `32'd858993`，启用 QPSK 并断言 `dac_in0/dac_in1` 仅在 `symbol_valid` 边界改变；随后写 `digmod_sel=0`，断言标准 DDS 输出重新进入 `dac_driver`。运行：

```bash
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/iq_digital_mod.v src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$VIVADO/xelab.bat" reg_bridge_tb -s reg_bridge_symrate_sim
"$VIVADO/xsim.bat" reg_bridge_symrate_sim -runall
```

接着使用现有 `awg_top_tb` 所列依赖，加上两个新 RTL 文件编译、elaborate 和 runall。Expected: 两个 testbench 都打印各自 `PASS`，并保留全部既有断言。

### Task 5: 在 BD 中创建专用 32-bit AXI GPIO

**Files:**
- Modify: `scripts/create_bd.tcl`
- Modify: `scripts/ps7_props.tcl` only if existing Tcl helper assigns every AXI master explicitly
- Test: `python scripts/build_bitstream.py`

- [ ] **Step 1: 在 Tcl 中加入新的 AXI GPIO IP 和端口连接**

在已有 `axi_gpio_0` 至 `axi_gpio_5` 创建区域之后创建 `axi_gpio_6`。使用与现有控制 GPIO 相同的 AXI clock/reset/interconnect 连接模式，但必须配置为单通道输出：

```tcl
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_6
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0}] [get_bd_cells axi_gpio_6]
```

为 `s_axi_aclk`、`s_axi_aresetn`、AXI master slave pin、`gpio_io_o` 添加与已有 GPIO 相同的 `connect_bd_net`/`connect_bd_intf_net` 调用；将 `gpio_io_o[31:0]` 连接到 `awg_top` 的 `gpio12_in[31:0]`。若现有 AXI interconnect 没有空闲 master，使用与当前第六个 GPIO 一致的 SmartConnect/AXI interconnect 扩容方式，不得覆盖既有地址段。

- [ ] **Step 2: 生成 BD 并检查地址和新端口**

Run:

```bash
python scripts/build_bitstream.py
```

Expected: `create_bd.tcl` 完成，`host_bd` 中出现 `axi_gpio_6`，生成的硬件平台包含 `XPAR_AXI_GPIO_6_DEVICE_ID`，实现继续到 bitstream。若 Tcl 报 interconnect master 不足，先按现有连接模式扩容，再从头运行该命令；禁止手工在 GUI 中补线。

- [ ] **Step 3: 校验 bitstream manifest**

Run:

```bash
python scripts/check_bitstream.py
```

Expected: 输出 `OK: bitstream 当前 RTL 源一致`。

### Task 6: 添加 PS `SYMRATE` 命令、phase-step 写入和完整回读

**Files:**
- Modify: `sdk/src/main.c`
- Modify: `scripts/test_bridge.py`

- [ ] **Step 1: 在板级协议脚本中写入新命令的预期失败用例**

在 `scripts/test_bridge.py` 的 DIGMOD 用例后加入：

```python
expect("SET DIGMOD SYMRATE 100000", "OK")
expect("GET DIGMOD", "DIGMOD MODE OFF SOURCE PRBS7 SYMRATE 100000 STATE STOPPED")
expect("SET DIGMOD SYMRATE 9", "ERR SYMRATE OUT OF RANGE")
expect("SET DIGMOD SYMRATE 1000001", "ERR SYMRATE OUT OF RANGE")
expect("SET DIGMOD SYMRATE abc", "ERR BAD VALUE")
expect("SET DIGMOD MODE QPSK", "OK")
expect("SET DIGMOD SYMRATE 250000", "ERR DIGMOD RUNNING")
expect("GET DIGMOD", "DIGMOD MODE QPSK SOURCE PRBS7 SYMRATE 100000 STATE RUNNING")
expect("SET DIGMOD MODE OFF", "OK")
```

- [ ] **Step 2: 在当前固件上运行脚本并确认新命令失败**

Run after loading the current pre-change ELF:

```bash
python scripts/test_bridge.py COM11
```

Expected: 新 `SYMRATE` 和完整 GET 断言失败；已有命令保持通过。

- [ ] **Step 3: 实现 PS state 和 GPIO 写入**

在 `sdk/src/main.c` 中：

```c
#define GPIO7_DEVICE_ID XPAR_AXI_GPIO_6_DEVICE_ID
#define SYMRATE_MIN 10u
#define SYMRATE_MAX 1000000u
#define PL_CLK_HZ 50000000u

static XGpio Gpio7;
static u32 digmod_symrate_hz = 10000u;

static u32 symrate_to_phase_step(u32 rate_hz)
{
    return (u32)(((u64)rate_hz << 32) / PL_CLK_HZ);
}
```

初始化 `Gpio7` 为单通道输出。新增 `write_digmod_phase_step()`，以 `XGpio_DiscreteWrite(&Gpio7, 1, symrate_to_phase_step(digmod_symrate_hz))` 写入。`write_all()` 必须先写 phase step 再写 GPIO11 word F。

把 `SET DIGMOD` 分支改为严格区分 `MODE` 与 `SYMRATE`：`MODE` 维持现有 4-token 验证；`SYMRATE` 仅接收 4-token、严格 `parse_u32()`、范围 `10..1000000`，且 `digmod_mode != 0` 时发送 `ERR DIGMOD RUNNING\r\n` 并返回。成功时更新状态、写新 phase step、发送 `OK\r\n`。

将 GET DIGMOD 的本地缓冲从 `char out[32]` 扩容为 `char out[96]`，再替换输出为：

```c
sprintf(out, "DIGMOD MODE %s SOURCE PRBS7 SYMRATE %lu STATE %s\r\n",
        DIGMOD_NAME[digmod_mode], (unsigned long)digmod_symrate_hz,
        digmod_mode ? "RUNNING" : "STOPPED");
```

- [ ] **Step 4: 同步、重建并运行新 ELF**

Run:

```bash
cp sdk/src/main.c sdk/ws/awg_bridge/src/main.c
cli-anything-vivado sdk app build --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf awg/awg.xpr
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw awg/awg.xpr
```

Expected: SDK 编译成功且 PS 桥重新开始运行。若当前 CLI 仅支持工程级构建，使用 `cli-anything-vivado sdk app build --help` 确认等价调用；不得加载旧 ELF。

- [ ] **Step 5: 执行完整协议回归**

Run:

```bash
python scripts/test_bridge.py COM11
```

Expected: 所有旧协议断言以及新增的 SYMRATE/完整 GET/运行中拒绝断言都通过。

### Task 7: 扩展上位机 DIGMOD 状态回读和显示

**Files:**
- Modify: `host/siggen/protocol.py`
- Modify: `host/siggen/controller.py`
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`
- Modify: `host/tests/test_protocol.py`
- Modify: `host/tests/test_controller.py`
- Modify: `host/tests/test_widgets.py`

- [ ] **Step 1: 先写协议、控制器和控件失败测试**

在 `test_protocol.py` 断言：

```python
assert digmod_symrate_cmd(100000) == "SET DIGMOD SYMRATE 100000"
assert parse_digmod_status(
    "DIGMOD MODE QAM16 SOURCE PRBS7 SYMRATE 100000 STATE RUNNING"
) == ("QAM16", "PRBS7", 100000, True)
```

增加 `9`、`1_000_001`、`"100k"`、`True` 的 `ValueError` 用例。`test_controller.py` 通过 `FakeWorker` 验证 `set_digmod_symrate(100000)` 发送带 CRLF 的命令、ACK 后只更新已确认速率、ERR/timeout 保留旧状态、完整状态行更新四个字段。`test_widgets.py` 断言 `set_confirmed("QPSK", "PRBS7", 100000, True)` 显示 `当前：QPSK（PRBS7，100 kSym/s，运行中）`。

- [ ] **Step 2: 运行目标测试并确认 API 尚不存在**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_protocol.py host/tests/test_controller.py host/tests/test_widgets.py -q
```

Expected: 导入或属性错误，原因是新函数、字段与控件签名尚不存在。

- [ ] **Step 3: 实现最小协议和状态机改动**

在 `protocol.py` 添加：

```python
def digmod_symrate_cmd(symrate_hz: int) -> str:
    if isinstance(symrate_hz, bool) or not isinstance(symrate_hz, int):
        raise ValueError("SYMRATE must be an integer")
    if not 10 <= symrate_hz <= 1_000_000:
        raise ValueError("SYMRATE out of range")
    return f"SET DIGMOD SYMRATE {symrate_hz}"
```

将 `parse_digmod_status()` 改为严格接受恰好九个 token：

```python
DIGMOD MODE <mode> SOURCE PRBS7 SYMRATE <integer> STATE RUNNING|STOPPED
```

并返回 `(mode, source, symrate_hz, running)`。拒绝未知 source、非法范围、`MODE OFF` 与 `RUNNING` 不一致、非 OFF 与 `STOPPED` 不一致。

在 `controller.py` 定义：

```python
@dataclass
class DigitalModState:
    mode: str = "OFF"
    source: str = "PRBS7"
    symrate_hz: int = 10_000
    running: bool = False
```

增加 `set_digmod_symrate()`，复用已有 `_pending`、ACK、ERR 和 timeout 分流机制。DIGMOD 状态行到达时一次性写入所有四个字段，再 emit `digmod_changed(mode)`。

- [ ] **Step 4: 显示已确认状态而不新增数据源 UI**

将 `DigitalModPanel.set_confirmed()` 扩展为 `(mode, source, symrate_hz, running)`；以 `symrate_hz / 1000` 无小数时显示 `100 kSym/s`，否则显示 `N Sym/s`。在 `host/siggen/ui/main_window.py` 的 `_on_digmod_state()` 中，从 `self.ctrl.digmod` 取完整已确认状态并调用新签名；保留既有 `digmod_changed = Signal(str)`，避免扩大全局 Qt 信号契约。面板只展示回读值；不要添加 SOURCE 下拉框、数据上传控件或未实现的运行按钮。

- [ ] **Step 5: 运行上位机回归**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/ -q
```

Expected: 全部测试通过，且离屏 Qt 测试没有 traceback。

### Task 8: 将示波器数字调制验收改为速率感知

**Files:**
- Modify: `scripts/scope_verify.py`
- Create: `host/tests/test_scope_verify.py`
- Test: `host/tests/test_scope_verify.py`

- [ ] **Step 1: 为联合 I/Q 跳变提取函数写入纯 Python 测试**

在 `scripts/scope_verify.py` 顶层增加无仪器依赖函数：

```python
def symbol_period_from_iq_labels(i_labels, q_labels, xinc):
    changed = np.flatnonzero(
        (i_labels[1:] != i_labels[:-1]) | (q_labels[1:] != q_labels[:-1])
    ) + 1
    runs = np.diff(np.r_[0, changed, len(i_labels)])
    runs = runs[runs > 1]
    return np.percentile(runs, 20) * xinc
```

在新的 `host/tests/test_scope_verify.py` 或现有脚本可导入测试位置，构造 10、5、2 sample/符号的联合 label 序列，断言结果分别接近 `10*xinc`、`5*xinc`、`2*xinc`，并确保单通道不变但另一通道变化时仍检测到边界。

- [ ] **Step 2: 运行该测试确认 helper 不存在而失败**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_scope_verify.py -q
```

Expected: import error 或函数不存在。

- [ ] **Step 3: 实现速率相关捕获和判据**

将当前数字调制循环抽为 `verify_digmod_mode(name, symrate_hz, ...)`。每次测试前按顺序发送：

```python
setup_board([
    "SET DIGMOD MODE OFF",
    f"SET DIGMOD SYMRATE {symrate_hz}",
    f"SET DIGMOD MODE {name}",
])
```

令 `expected_period = 1.0 / symrate_hz`，将电平聚类保留不变，但用联合 I/Q labels 的 period 估计替代固定 `i_hold >= 60e-6` / `q_hold >= 60e-6`。通过条件为：

```python
period_ok = abs(measured_period - expected_period) <= max(3 * xinc, expected_period * 0.35)
```

对 10 kSym/s 继续运行 BPSK/QPSK/QAM16/QAM64 星座电平测试；另对 QPSK 运行 100 k 和 1 MSym/s。高速用例必须减小 `scope_acq()` 时基并保持每个预期符号至少 10 个采样点。

- [ ] **Step 4: 先运行离线 helper 测试，再运行板级闭环**

Run:

```bash
host/.venv/Scripts/python -m pytest host/tests/test_scope_verify.py -q
python scripts/scope_verify.py COM11
```

Expected: 离线测试通过；示波器输出保留全部标准 AWG 项，并新增 `QPSK I/Q 100 kSym/s`、`QPSK I/Q 1 MSym/s` 的 PASS。最后仍打印 `DIGMOD OFF 恢复标准正弦` PASS。

### Task 9: 更新 README 与最终验收

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-27-digital-modulation-symbol-rate-design.md` only if implementation changes an approved name or semantic

- [ ] **Step 1: 更新接口和时序说明**

将 README 中“固定 10 kSym/s”替换为：

```text
内部 PRBS7 源，SYMRATE 10..1,000,000 Sym/s；PS 计算 32 位 phase step，
符号边界由相位累加器溢出产生。非整除频率的相邻边界最多相差一个 50 MHz 时钟，长期平均速率符合 SYMRATE。
```

加入完整命令和状态例：

```text
SET DIGMOD SYMRATE 100000
GET DIGMOD
DIGMOD MODE QPSK SOURCE PRBS7 SYMRATE 100000 STATE RUNNING
```

明确运行中修改速率返回 `ERR DIGMOD RUNNING`，以及当前仍是基带 I/Q、未实现外部数据源和 RRC。

- [ ] **Step 2: 检查文档差异**

Run:

```bash
git diff --check
git diff -- README.md docs/superpowers/specs/2026-08-27-digital-modulation-symbol-rate-design.md
```

Expected: 无空白错误；文档与实现中的 `SYMRATE`、`STATE`、`phase step` 名称一致。

- [ ] **Step 3: 执行最终软件、RTL、构建和板级验收**

Run in this order:

```bash
host/.venv/Scripts/python -m pytest host/tests/ -q
VIVADO=<LOCAL_PATH>
"$VIVADO/xvlog.bat" -sv src/rtl/prbs7_source.v src/rtl/iq_constellation_mapper.v src/rtl/iq_digital_mod.v src/tb/iq_digital_mod_tb.v
"$VIVADO/xelab.bat" iq_digital_mod_tb -s iq_digital_mod_sim
"$VIVADO/xsim.bat" iq_digital_mod_sim -runall > iq_digital_mod.sim.log
python scripts/build_bitstream.py
python scripts/check_bitstream.py
bash scripts/program_board.sh
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw awg/awg.xpr
python scripts/test_bridge.py COM11
python scripts/scope_verify.py COM11
```

Expected: pytest 全绿；IQ 5×127 GOLD 精确匹配；完整 bitstream 校验通过；协议回归通过；示波器在 10 k、100 k、1 MSym/s 均通过且 OFF 恢复标准 AWG。
