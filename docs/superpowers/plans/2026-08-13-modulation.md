# 内调制功能(AM/FM/PM/扫频)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AC820 信号发生器每通道添加内调制(AM/FM/PM/扫频),上位机可控,与现有谐波合成正交叠加。

**Architecture:** 新模块 mod_synth(48 位调制累加器 + 4 波形)产生 m/fword_inst/pm_off/am_gain;
FM/SWEEP 用 fword_inst 注入 dds_core 与 harm_synth 的 fword(锁相保持),PM 注入查表地址层
(谐波偏移 ×ord),AM 在波形选择 mux 后经新模块 am_stage 应用增益。新增 AXI GPIO 字 C/D
承载调制参数,PS 桥扩展 6 条命令,上位机增加调制面板与预览。

**Tech Stack:** Verilog-2001(纯 RTL,零 IP)、Vivado 2018.3(xvlog/xelab/xsim 仿真,
`SIM="<LOCAL_PATH>"`)、`cli-anything-vivado`(SDK 编译/烧录)、
Python3(PySide6 上位机 + pytest)、numpy(预览)。

**规格:** `docs/superpowers/specs/2026-08-13-modulation-design.md`(已批准)

## 关键公式速查

- `fword48 = mfreq_01 × 562950`(mfreq_01 为 0.1Hz 单位,1..500000)
- `fword_dev = fm_dev×85 + fm_dev×9/10`(fm_dev 0..500000 Hz;fword 与 awg_top 现有公式一致)
- AM:`am_gain = 12700 + m×am_depth`(am_depth 0..100,增益范围 [0, 25400]);`s_am = sat(sample×am_gain/12700)`
- FM/SWEEP:`fword_inst = sat(fword_base + (m×fword_dev)/127, [0, 2^31])`
- PM:`pm_off = (m×pm_dev)/127`(pm_dev 0..511,1/256 周期 LSB = 1.40625°);谐波偏移 = ord×pm_off(mod 256),先加偏移再偶次 ^0x80
- 所有除法均为**截断向零**(与 Verilog $signed 除法一致)

## 文件结构

| 文件 | 责任 |
|------|------|
| `src/rtl/mod_synth.v`(新建) | 调制源:48 位累加器 + 4 波形 + AM/FM/PM 数学 |
| `src/rtl/am_stage.v`(新建) | AM 增益级:sample×gain/12700,饱和 ±127 |
| `src/rtl/dds_core.v`(修改) | 新增 `pm_off` 输入,查表地址层偏移 |
| `src/rtl/harm_synth.v`(修改) | 新增 `pm_off` 输入,基波/谐波地址偏移(先偏移后偶次翻转) |
| `src/rtl/reg_bridge.v`(修改) | 新增 gpio6..9 输入,拆包调制字 C/D |
| `src/rtl/awg_top.v`(修改) | 实例化 mod_synth×2/am_stage×2;fword_inst 注入两合成器 |
| `src/tb/mod_synth_tb.v`(新建) | mod_synth 仿真(约 10 项) |
| `src/tb/am_stage_tb.v`(新建) | am_stage 仿真 |
| `src/tb/dds_core_tb.v`/`harm_synth_tb.v`/`reg_bridge_tb.v`/`awg_top_tb.v`(修改) | PM/拆包/集成测试扩展 |
| `scripts/create_bd.tcl`(修改) | 新增 axi_gpio_3/4(gpio6..9) |
| `scripts/build_bitstream.py`/`check_bitstream.py`(修改) | 哈希输入加入 create_bd.tcl |
| `sdk/src/main.c`(修改) | 调制字段 + 6 条新命令 + GET 扩展 |
| `scripts/test_bridge.py`(修改) | +12 项协议用例(板上) |
| `host/siggen/protocol.py`(修改) | MODES 常量、mod_cmd、parse_status 扩展、parse_modfreq_input |
| `host/siggen/controller.py`(修改) | ChannelState 调制字段 + set_field/_on_line |
| `host/siggen/preview.py`(修改) | make_plot_data 调制路径(50MHz 相位积分) |
| `host/siggen/ui/widgets.py`(修改) | ChannelPanel 调制区 + set_state |
| `host/siggen/ui/main_window.py`(修改) | _on_state 传递调制参数 |
| `scripts/scope_verify.py`(修改) | +4 项调制闭环用例 |
| `README.md`/`CLAUDE.md`(修改) | 功能/命令/测试更新 |

---

## Task 1: mod_synth 模块(TDD)

**Files:**
- Create: `src/rtl/mod_synth.v`
- Create: `src/tb/mod_synth_tb.v`

- [ ] **Step 1: 写失败测试 mod_synth_tb.v**

```verilog
`timescale 1ns / 1ps
module mod_synth_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [47:0] mfreq_fword = 0;
    reg [1:0]  mwave = 0;
    reg [2:0]  mode = 0;
    reg [7:0]  am_depth = 0;
    reg [25:0] fm_dev_fword = 0;
    reg [8:0]  pm_dev = 0;
    reg [31:0] fword_base = 0;
    wire signed [7:0]  m;
    wire [31:0] fword_inst;
    wire signed [8:0]  pm_off;
    wire signed [16:0] am_gain;

    mod_synth uut (
        .clk(clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword), .mwave(mwave), .mode(mode),
        .am_depth(am_depth), .fm_dev_fword(fm_dev_fword), .pm_dev(pm_dev),
        .fword_base(fword_base),
        .m(m), .fword_inst(fword_inst), .pm_off(pm_off), .am_gain(am_gain)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    // 直接置内部累加器(fword=0 时保持),下一拍读组合输出
    task poke;
        input [47:0] v;
        begin
            uut.acc_m = v;
            @(posedge clk); #1;
        end
    endtask

    // t1: 4 种调制波形形状(mfreq_fword=0 冻结累加器,逐一 poke t)
    begin : t1
        integer i, bad;
        reg signed [7:0] v;
        mfreq_fword = 48'd0; mode = 3'd0;
        // sine: 与 sine_rom 参考逐点一致
        bad = 0;
        mwave = 2'd0;
        for (i = 0; i < 256; i = i + 1) begin
            poke({40'd0, i[7:0]});
            v = $signed(uut.sine_v);
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=SINE == sine_rom", bad == 0);
        // square: ±127
        bad = 0;
        mwave = 2'd1;
        for (i = 0; i < 256; i = i + 1) begin
            poke({40'd0, i[7:0]});
            v = (i < 128) ? 8'sd127 : -8'sd127;
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=SQUARE +-127", bad == 0);
        // triangle: -128..-1 上升, 127..0 下降
        bad = 0;
        mwave = 2'd2;
        for (i = 0; i < 256; i = i + 1) begin
            poke({40'd0, i[7:0]});
            v = (i < 128) ? (i - 128) : (383 - i - 128);
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=TRIANGLE linear", bad == 0);
        // saw: t-128
        bad = 0;
        mwave = 2'd3;
        for (i = 0; i < 256; i = i + 1) begin
            poke({40'd0, i[7:0]});
            v = i - 128;
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=SAW t-128", bad == 0);
    end

    // t2: fword48 值正确 + 50kHz 周期
    begin : t2
        mfreq_fword = 48'd562950;
        poke(48'd0);
        chk("fword48 = 562950 (0.1Hz)", uut.acc_m == 48'd562950);
        // 50kHz: fword = 500000*562950; t 每 1000 拍回绕一次
        mfreq_fword = 48'd281475000000;
        uut.acc_m = 48'd0;
        begin : t2b
            integer c, wraps;
            reg [7:0] prev_t;
            wraps = 0;
            prev_t = uut.acc_m[47:40];
            for (c = 0; c < 2500; c = c + 1) begin
                @(posedge clk); #1;
                if (uut.acc_m[47:40] < prev_t) wraps = wraps + 1;  // t 回绕
                prev_t = uut.acc_m[47:40];
            end
            chk("50kHz -> t wraps ~2.5x/2500 clk", wraps >= 2 && wraps <= 3);
        end
    end

    // t3: AM 增益
    begin : t3
        mfreq_fword = 48'd0;
        mode = 3'd1;                       // AM
        mwave = 2'd1;                      // square
        am_depth = 8'd100;
        poke({47'd0, 1'b0});               // m=+127
        chk("AM depth100 m=+127 -> 25400", am_gain == 17'sd25400);
        poke({47'd0, 1'b1});               // m=-127
        chk("AM depth100 m=-127 -> 0", am_gain == 17'sd0);
        am_depth = 8'd50;
        poke({47'd0, 1'b0});
        chk("AM depth50 m=+127 -> 19050", am_gain == 17'sd19050);
        poke({47'd0, 1'b1});
        chk("AM depth50 m=-127 -> 6350", am_gain == 17'sd6350);
        mode = 3'd0;                       // OFF
        poke({47'd0, 1'b0});
        chk("OFF -> am_gain 12700", am_gain == 17'sd12700);
    end

    // t4: FM fword_inst(含饱和)
    begin : t4
        mode = 3'd2;                       // FM
        mwave = 2'd1;
        fword_base = 32'd85899;
        fm_dev_fword = 26'd4295000;        // 50kHz 频偏
        poke({47'd0, 1'b0});               // m=+127
        chk("FM m=+127 -> base+dev", fword_inst == 32'd4380899);
        poke({47'd0, 1'b1});               // m=-127: 85899-4295000 < 0 -> 饱和 0
        chk("FM 负频饱和 -> 0", fword_inst == 32'd0);
        mode = 3'd0;
        poke({47'd0, 1'b0});
        chk("OFF -> fword_inst == base", fword_inst == 32'd85899);
    end

    // t5: PM pm_off
    begin : t5
        mode = 3'd3;                       // PM
        mwave = 2'd1;
        pm_dev = 9'd511;
        poke({47'd0, 1'b0});
        chk("PM m=+127 pm=511 -> 511", pm_off == 9'sd511);
        poke({47'd0, 1'b1});
        chk("PM m=-127 pm=511 -> -511", pm_off == -9'sd511);
        pm_dev = 9'd128;
        poke({47'd0, 1'b0});
        chk("PM m=+127 pm=128 -> 128", pm_off == 9'sd128);
        mode = 3'd0;
        poke({47'd0, 1'b0});
        chk("OFF -> pm_off 0", pm_off == 9'sd0);
    end

    // t6: SWEEP == FM(同一数据通路)
    begin : t6
        mode = 3'd4;                       // SWEEP
        mwave = 2'd1;
        fm_dev_fword = 26'd4295000;
        fword_base = 32'd85899;
        poke({47'd0, 1'b0});
        chk("SWEEP m=+127 -> base+dev", fword_inst == 32'd4380899);
        poke({47'd0, 1'b1});
        chk("SWEEP m=-127 -> 饱和 0", fword_inst == 32'd0);
    end

    // t7: 模式切换瞬态(同拍生效)
    begin : t7
        mode = 3'd2;
        poke({47'd0, 1'b0});
        chk("FM on -> inst != base", fword_inst != 32'd85899);
        mode = 3'd0;
        #1;
        chk("FM off -> inst == base 同拍", fword_inst == 32'd85899);
    end

    if (errs == 0) $display("PASS mod_synth_tb");
    else $display("FAIL mod_synth_tb: %0d errors", errs);
    $finish;
endmodule
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/tb/mod_synth_tb.v
"$SIM/xelab.bat" mod_synth_tb -s mod_sim
```
期望: xelab 报 `module 'mod_synth' not found`。

- [ ] **Step 3: 写最小实现 mod_synth.v**

```verilog
`timescale 1ns / 1ps
// 内调制源: 48 位累加器 + 4 波形,输出 m/fword_inst/pm_off/am_gain。
// 输出全部为 acc_m 的组合函数(同拍同源),下游按各自流水延迟对齐。
// 参数校验由 PS 桥完成(am_depth<=100, fm_dev<=500000, pm_dev<=511, mfreq>=1)。
module mod_synth (
    input  wire clk,
    input  wire rst_n,
    input  wire [47:0] mfreq_fword,   // = mfreq_01 × 562950
    input  wire [1:0]  mwave,         // 0=sine 1=square 2=tri 3=saw
    input  wire [2:0]  mode,          // 0=off 1=am 2=fm 3=pm 4=sweep
    input  wire [7:0]  am_depth,      // 0..100
    input  wire [25:0] fm_dev_fword,  // = fm_dev×85 + fm_dev×9/10
    input  wire [8:0]  pm_dev,        // 0..511, 1/256 周期单位
    input  wire [31:0] fword_base,
    output wire signed [7:0]  m,          // 调制信号(始终运行,切换模式无相位跳变)
    output wire [31:0] fword_inst,        // FM/SWEEP 瞬时 fword,否则 = fword_base
    output wire signed [8:0] pm_off,      // PM 相位偏移,否则 0
    output wire signed [16:0] am_gain     // 12700 + m×am_depth,否则 12700(直通)
);
    reg [47:0] acc_m;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) acc_m <= 48'd0;
        else        acc_m <= acc_m + mfreq_fword;

    // ---- 调制波形(复用 dds_core 的算法与 sine_rom)----
    wire [7:0] t = acc_m[47:40];
    wire [7:0] sine_v;
    sine_rom u_rom (.addr(t), .dout(sine_v));
    wire signed [7:0] m_sine = $signed(sine_v);
    wire signed [7:0] m_sqr  = acc_m[47] ? -8'sd127 : 8'sd127;
    wire signed [7:0] m_tri  = $signed(acc_m[47] ? ~acc_m[46:39] : acc_m[46:39]) - 8'sd128;
    wire signed [7:0] m_saw  = $signed(t) - 8'sd128;
    wire signed [7:0] m_raw  = (mwave == 2'b00) ? m_sine :
                               (mwave == 2'b01) ? m_sqr :
                               (mwave == 2'b10) ? m_tri : m_saw;
    assign m = m_raw;

    // ---- FM/SWEEP: fword_inst = fword_base + (m×fm_dev_fword)/127, 饱和 [0, 2^31] ----
    wire signed [33:0] fm_prod  = $signed(m) * $signed({1'b0, fm_dev_fword});
    wire signed [33:0] fm_delta = fm_prod / 34'sd127;          // 截断向零
    wire signed [34:0] fm_sum   = $signed({1'b0, fword_base}) + fm_delta;
    wire signed [34:0] fm_sat   = (fm_sum > 35'sd2147483647) ? 35'sd2147483647 :
                                  (fm_sum < 35'sd0) ? 35'sd0 : fm_sum;
    assign fword_inst = (mode == 3'b010 || mode == 3'b100) ? fm_sat[31:0] : fword_base;

    // ---- PM: pm_off = (m×pm_dev)/127, ±511 ----
    wire signed [16:0] pm_prod  = $signed(m) * $signed({1'b0, pm_dev});
    wire signed [16:0] pm_off_c = pm_prod / 17'sd127;          // 截断向零
    assign pm_off = (mode == 3'b011) ? pm_off_c[8:0] : 9'sd0;

    // ---- AM: am_gain = 12700 + m×am_depth, 钳位 [0, 32767] ----
    wire signed [15:0] am_prod = $signed(m) * $signed({1'b0, am_depth});
    wire signed [17:0] am_sum  = 18'sd12700 + $signed(am_prod);
    wire signed [17:0] am_sat  = (am_sum > 18'sd32767) ? 18'sd32767 :
                                 (am_sum < 18'sd0)    ? 18'sd0 : am_sum;
    assign am_gain = (mode == 3'b001) ? am_sat[16:0] : 17'sd12700;
endmodule
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/rtl/mod_synth.v src/tb/mod_synth_tb.v
"$SIM/xelab.bat" mod_synth_tb -s mod_sim
"$SIM/xsim.bat" mod_sim -runall
```
期望: 输出含 `PASS mod_synth_tb`(全部子项 PASS)。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/mod_synth.v src/tb/mod_synth_tb.v
git commit -m "feat: mod_synth 内调制源模块(48位累加器+AM/FM/PM/SWEEP, TDD)"
```

---

## Task 2: am_stage 模块(TDD)

**Files:**
- Create: `src/rtl/am_stage.v`
- Create: `src/tb/am_stage_tb.v`

- [ ] **Step 1: 写失败测试 am_stage_tb.v**

```verilog
`timescale 1ns / 1ps
module am_stage_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [7:0] sample_in = 0;
    reg signed [16:0] am_gain = 17'sd12700;
    wire [7:0] sample_out;

    am_stage uut (.clk(clk), .rst_n(rst_n),
                  .sample_in(sample_in), .am_gain(am_gain), .sample_out(sample_out));
    always #10 clk = ~clk;

    integer errs = 0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    task step;
        begin @(posedge clk); #1; end
    endtask

    // 期望值: trunc(sample×gain/12700), 饱和 ±127
    function [7:0] exp_val;
        input signed [31:0] p;
        begin
            if (p / 32'sd12700 > 32'sd127) exp_val = 8'sd127;
            else if (p / 32'sd12700 < -32'sd128) exp_val = 8'sd128;
            else exp_val = (p / 32'sd12700)[7:0];
        end
    endfunction

    initial begin
        #100 rst_n = 1;
        #100;
        begin : t1
            integer i, bad;
            bad = 0;
            for (i = -128; i <= 127; i = i + 1) begin
                sample_in = i[7:0];
                step;
                if (sample_out !== i[7:0]) bad = bad + 1;
            end
            chk("gain=12700 identity", bad == 0);
        end
        begin : t2
            integer i, bad;
            integer p;
            bad = 0;
            am_gain = 17'sd25400;
            for (i = -128; i <= 127; i = i + 1) begin
                sample_in = i[7:0];
                step;
                p = i * 25400;
                if (sample_out !== exp_val(p)) bad = bad + 1;
            end
            chk("gain=25400 saturating x2", bad == 0);
        end
        am_gain = 17'sd0;
        sample_in = 8'sd100;
        step;
        chk("gain=0 -> 0", sample_out == 8'sd0);
        am_gain = 17'sd12701;
        sample_in = 8'sd1;
        step;
        chk("trunc toward zero (12701/12700 -> 1)", sample_out == 8'sd1);
        sample_in = -8'sd1;
        step;
        chk("trunc toward zero (-12701/12700 -> -1)", sample_out == -8'sd1);

        if (errs == 0) $display("PASS am_stage_tb");
        else $display("FAIL am_stage_tb: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/tb/am_stage_tb.v
"$SIM/xelab.bat" am_stage_tb -s am_sim
```
期望: xelab 报 `module 'am_stage' not found`。

- [ ] **Step 3: 写最小实现 am_stage.v**

```verilog
`timescale 1ns / 1ps
// AM 增益级: sample_out = sat(sample_in × am_gain / 12700), 2 拍流水。
// am_gain=12700 时输出与输入逐位一致(直通);除法截断向零。
module am_stage (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] sample_in,          // signed
    input  wire signed [16:0] am_gain,    // 12700 + m×am_depth
    output reg  [7:0] sample_out          // signed, 饱和 ±127
);
    reg signed [24:0] prod;               // 8×17 = 25 位
    wire signed [24:0] scaled = prod / 25'sd12700;   // 截断向零

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            prod       <= 25'sd0;
            sample_out <= 8'sd0;
        end else begin
            prod <= $signed(sample_in) * am_gain;
            sample_out <= (scaled > 25'sd127) ? 8'sd127 :
                          (scaled < -25'sd128) ? 8'sd128 : scaled[7:0];
        end
endmodule
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/am_stage.v src/tb/am_stage_tb.v
"$SIM/xelab.bat" am_stage_tb -s am_sim
"$SIM/xsim.bat" am_sim -runall
```
期望: `PASS am_stage_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/am_stage.v src/tb/am_stage_tb.v
git commit -m "feat: am_stage AM 增益级(饱和/直通, TDD)"
```

---

## Task 3: dds_core PM 注入(TDD)

**Files:**
- Modify: `src/tb/dds_core_tb.v`
- Modify: `src/rtl/dds_core.v`

- [ ] **Step 1: 扩展测试(红)**

在 dds_core_tb.v 中:
1. 声明 `reg signed [8:0] pm_off = 0;` 并接入实例(现有用例 pm_off=0 → 回归)。
2. 在 sawtooth 段之后追加:

```verilog
        // --- PM: pm_off 偏移查表地址(正弦) ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 2'b00;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 2'b00;
        fword = 32'h01000000;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ($signed(sample) !== $signed(sine_expected((i + 64) % 256))) begin
                $display("FAIL pm sine step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- PM: pm_off=128 -> 正弦反相 ---
        pm_off = 9'sd128;
        uut.pm_off_r = 9'sd128;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ($signed(sample) !== -$signed(sine_expected(i[7:0]))) begin
                $display("FAIL pm invert step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- PM: 方波符号随 p_off[7] ---
        pm_off = 9'sd128;
        uut.pm_off_r = 9'sd128;
        wave_sel = 2'b01;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 2'b01;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] exp_v;
                exp_v = (((i + 128) % 256) < 128) ? 8'h7F : 8'h81;
                if (sample !== exp_v) begin
                    $display("FAIL pm square step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- PM: 三角波对偏移相位做相同旋转(phase_acc=0 -> 位23=0) ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 2'b10;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 2'b10;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] poff8, tri_pm, tri_p, exp_v;
                poff8 = (i + 64) % 256;
                tri_pm = {poff8[6:0], 1'b0};
                tri_p  = poff8[7] ? ~tri_pm : tri_pm;
                exp_v  = tri_p - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL pm tri step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- PM: 锯齿地址 = p_off - 0x80 ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 2'b11;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 2'b11;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] exp_v;
                exp_v = ((i + 64) % 256) - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL pm saw step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/tb/dds_core_tb.v
"$SIM/xelab.bat" dds_core_tb -s dds_sim
```
期望: xelab 报端口不匹配(`dds_core` 无 `pm_off` 端口)。

- [ ] **Step 3: 修改 dds_core.v 实现**

新增端口与 PM 逻辑(替换原查表/波形段,pm_off=0 时逐位一致):

```verilog
module dds_core (
    input  wire clk,          // 50 MHz
    input  wire rst_n,
    input  wire [31:0] fword,
    input  wire [1:0]  wave_sel,   // 00 sin / 01 sqr / 10 tri / 11 saw
    input  wire signed [8:0] pm_off,   // PM 相位偏移(地址 LSB, 0=直通)
    output reg  [7:0] sample
);
    reg [31:0] phase_acc;
    reg [1:0]  wave_sel_r;
    reg signed [8:0] pm_off_r;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            phase_acc  <= 32'd0;
            wave_sel_r <= 2'b00;
            pm_off_r   <= 9'sd0;
        end else begin
            phase_acc  <= phase_acc + fword;
            wave_sel_r <= wave_sel;
            pm_off_r   <= pm_off;
        end

    // PM 注入: 累加器之后、查表之前加偏移(mod 256 回绕), 不改频率
    wire [7:0] p_off = phase_acc[31:24] + pm_off_r[7:0];
    wire [7:0] sine_v;
    sine_rom u_rom (.addr(p_off), .dout(sine_v));
    wire [7:0] sqr_v = p_off[7] ? 8'h81 : 8'h7F;   // -127 / +127
    // 三角波原实现取 phase_acc[30:23](相位字节左旋 1 位);PM 版本对偏移后的
    // 相位字节做同样旋转(位 23 不参与偏移,保持原样)
    wire [7:0] tri_pm = {p_off[6:0], phase_acc[23]};
    wire [7:0] tri_p  = p_off[7] ? ~tri_pm : tri_pm;
    wire [7:0] tri_v  = tri_p - 8'h80;
    wire [7:0] saw_v  = p_off - 8'h80;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample <= 8'd0;
        else begin
            case (wave_sel_r)
                2'b00:   sample <= sine_v;
                2'b01:   sample <= sqr_v;
                2'b10:   sample <= tri_v;
                default: sample <= saw_v;
            endcase
        end
endmodule
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/rtl/dds_core.v src/tb/dds_core_tb.v
"$SIM/xelab.bat" dds_core_tb -s dds_sim
"$SIM/xsim.bat" dds_sim -runall
```
期望: `PASS dds_core`(旧 4 段 + 新 5 段全部通过)。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/dds_core.v src/tb/dds_core_tb.v
git commit -m "feat: dds_core PM 地址层注入(pm_off=0 回归兼容)"
```

---

## Task 4: harm_synth PM 注入(TDD)

**Files:**
- Modify: `src/tb/harm_synth_tb.v`
- Modify: `src/rtl/harm_synth.v`

- [ ] **Step 1: 扩展测试(红)**

在 harm_synth_tb.v 中:
1. 声明 `reg signed [8:0] pm_off = 0;`,接入实例(现有 t1..t8 为回归)。
2. 在 t8 之后追加:

```verilog
        // 9) PM: 基波地址偏移 + 谐波偏移 ×ord,先加偏移再偶次翻转
        fword = 32'd85899;
        base = 8'd50;
        h1_ord = 4'd2; h1_amp = 8'd50;
        h2_ord = 4'd0; h2_amp = 8'd0;
        h3_ord = 4'd0; h3_amp = 8'd0;
        h4_ord = 4'd0; h4_amp = 8'd0;
        pm_off = 9'sd64;
        settle(10);
        begin : t9
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 2;                 // ph1
                // s1 = (ph1 + ord×64) ^ 0x80(偶次翻转, 2×64=128 mod 256)
                if (uut.s1 !== ((exp1[31:24] + 8'd128) ^ 8'h80)) mism = mism + 1;
                if (uut.s0 !== (uut.acc0[31:24] + 8'sd64))       mism = mism + 1;
            end
            chk("PM: s0 +64, even s1 = (ph1+128)^0x80", mism == 0);
        end
        // 10) 奇次谐波 PM: 无翻转
        h1_ord = 4'd3;
        begin : t10
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 3;
                // 3×64 = 192 mod 256
                if (uut.s1 !== (exp1[31:24] + 8'd192)) mism = mism + 1;
            end
            chk("PM: odd s1 = ph1 + ord×off (no flip)", mism == 0);
        end
        pm_off = 9'sd0;
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/tb/harm_synth_tb.v
"$SIM/xelab.bat" harm_synth_tb -s harm_sim
```
期望: xelab 报端口不匹配(`harm_synth` 无 `pm_off` 端口)。

- [ ] **Step 3: 修改 harm_synth.v 实现**

新增端口,并修改相位地址段(替换 s0..s4 的 wire 定义;pm_off=0 时逐位一致):

```verilog
    input  wire signed [8:0] pm_off,   // PM 相位偏移(地址 LSB, 0=直通)
```

原 `wire [7:0] s0 = acc0[31:24];` 至 `wire [7:0] s4 = h4_ord[0] ? ph4[31:24] : ph4[31:24] ^ 8'h80;`
整段替换为:

```verilog
    // PM 注入: 查表地址加偏移(mod 256), 谐波偏移 = ord×pm_off(与 θk≡k·θ0 一致)。
    // 偶次翻转在偏移之后进行;pm_off=0 时与旧版逐位一致。
    wire signed [13:0] off1 = $signed(pm_off) * $signed({1'b0, h1_ord});
    wire signed [13:0] off2 = $signed(pm_off) * $signed({1'b0, h2_ord});
    wire signed [13:0] off3 = $signed(pm_off) * $signed({1'b0, h3_ord});
    wire signed [13:0] off4 = $signed(pm_off) * $signed({1'b0, h4_ord});
    wire [7:0] s0 = acc0[31:24] + pm_off[7:0];
    wire [7:0] s1 = (ph1[31:24] + off1[7:0]);
    wire [7:0] s2 = (ph2[31:24] + off2[7:0]);
    wire [7:0] s3 = (ph3[31:24] + off3[7:0]);
    wire [7:0] s4 = (ph4[31:24] + off4[7:0]);
    wire [7:0] s1_f = h1_ord[0] ? s1 : s1 ^ 8'h80;
    wire [7:0] s2_f = h2_ord[0] ? s2 : s2 ^ 8'h80;
    wire [7:0] s3_f = h3_ord[0] ? s3 : s3 ^ 8'h80;
    wire [7:0] s4_f = h4_ord[0] ? s4 : s4 ^ 8'h80;
    wire [7:0] sine0, sine1, sine2, sine3, sine4;
    sine_rom u_rom0 (.addr(s0),   .dout(sine0));
    sine_rom u_rom1 (.addr(s1_f), .dout(sine1));
    sine_rom u_rom2 (.addr(s2_f), .dout(sine2));
    sine_rom u_rom3 (.addr(s3_f), .dout(sine3));
    sine_rom u_rom4 (.addr(s4_f), .dout(sine4));
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/rtl/harm_synth.v src/tb/harm_synth_tb.v
"$SIM/xelab.bat" harm_synth_tb -s harm_sim
"$SIM/xsim.bat" harm_sim -runall
```
期望: `PASS harm_synth_tb`(t1..t8 回归 + t9/t10 新增)。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/harm_synth.v src/tb/harm_synth_tb.v
git commit -m "feat: harm_synth PM 注入(谐波偏移 ord×pm_off, 先偏移后偶次翻转)"
```

---

## Task 5: reg_bridge 调制字 C/D(TDD)

**Files:**
- Modify: `src/tb/reg_bridge_tb.v`
- Modify: `src/rtl/reg_bridge.v`

- [ ] **Step 1: 扩展测试(红)**

在 reg_bridge_tb.v 中:
1. 声明 `reg [31:0] gpio6_in = 0, gpio7_in = 0, gpio8_in = 0, gpio9_in = 0;`
2. 声明新输出 wire:`ch1_mode, ch2_mode`([2:0])、`ch1_mwave, ch2_mwave`([1:0])、
   `ch1_mfreq, ch2_mfreq`([18:0])、`ch1_am_depth, ch2_am_depth`([7:0])、
   `ch1_fm_dev, ch2_fm_dev`([18:0])、`ch1_pm_dev, ch2_pm_dev`([8:0])
3. 接入实例(新端口)。
4. 在"wordA=0"断言后追加:

```verilog
        // ---- 调制字拆包 ----
        // 字C: {am_depth[7:0], mfreq[18:0], mwave[1:0], mode[2:0]}
        // 字D: {reserved[3:0], pm_dev[8:0], fm_dev[18:0]}
        gpio6_in = {8'd100, 19'd500, 2'd2, 3'd4};   // CH1 字C
        gpio7_in = {8'd80,  19'd100, 2'd1, 3'd1};   // CH2 字C
        gpio8_in = {4'd0, 9'd128, 19'd50000};       // CH1 字D
        gpio9_in = {4'd0, 9'd64, 19'd1000};         // CH2 字D
        #40;
        chk("ch1 mode/mwave/mfreq/depth",
            ch1_mode == 3'd4 && ch1_mwave == 2'd2 &&
            ch1_mfreq == 19'd500 && ch1_am_depth == 8'd100);
        chk("ch1 fm/pm dev", ch1_fm_dev == 19'd50000 && ch1_pm_dev == 9'd128);
        chk("ch2 mode/mwave/mfreq/depth",
            ch2_mode == 3'd1 && ch2_mwave == 2'd1 &&
            ch2_mfreq == 19'd100 && ch2_am_depth == 8'd80);
        chk("ch2 fm/pm dev", ch2_fm_dev == 19'd1000 && ch2_pm_dev == 9'd64);
        // 全 0 -> 合法关闭态(无哨兵)
        gpio6_in = 32'd0; gpio8_in = 32'd0;
        #40;
        chk("wordC/D=0 -> mode 0", ch1_mode == 3'd0 && ch1_fm_dev == 19'd0);
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/tb/reg_bridge_tb.v
"$SIM/xelab.bat" reg_bridge_tb -s reg_sim
```
期望: xelab 报端口不匹配。

- [ ] **Step 3: 修改 reg_bridge.v 实现**

1. 端口区新增(在 gpio5_in 后):

```verilog
    input  wire [31:0] gpio6_in,  // -> CH1 调制字 C
    input  wire [31:0] gpio7_in,  // -> CH2 调制字 C
    input  wire [31:0] gpio8_in,  // -> CH1 调制字 D
    input  wire [31:0] gpio9_in,  // -> CH2 调制字 D
```

2. 输出区新增(在 ch2_h4_amp 后):

```verilog
    output wire [2:0]  ch1_mode, ch2_mode,
    output wire [1:0]  ch1_mwave, ch2_mwave,
    output wire [18:0] ch1_mfreq, ch2_mfreq,
    output wire [7:0]  ch1_am_depth, ch2_am_depth,
    output wire [18:0] ch1_fm_dev, ch2_fm_dev,
    output wire [8:0]  ch1_pm_dev, ch2_pm_dev,
```

3. 同步段新增(仿照 s2..s5):

```verilog
    reg [31:0] s6, s7, s8, s9, r6, r7, r8, r9;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s6 <= 32'd0; s7 <= 32'd0; s8 <= 32'd0; s9 <= 32'd0;
            r6 <= 32'd0; r7 <= 32'd0; r8 <= 32'd0; r9 <= 32'd0;
        end else begin
            s6 <= gpio6_in; s7 <= gpio7_in;
            s8 <= gpio8_in; s9 <= gpio9_in;
            r6 <= s6;       r7 <= s7;
            r8 <= s8;       r9 <= s9;
        end
```

4. 拆包(文件末尾):

```verilog
    // 拆包(字C: am_depth[31:24] mfreq[23:5] mwave[4:3] mode[2:0])
    //      (字D: reserved[31:28] pm_dev[27:19] fm_dev[18:0])
    assign ch1_mode    = r6[2:0];
    assign ch1_mwave   = r6[4:3];
    assign ch1_mfreq   = r6[23:5];
    assign ch1_am_depth = r6[31:24];
    assign ch2_mode    = r7[2:0];
    assign ch2_mwave   = r7[4:3];
    assign ch2_mfreq   = r7[23:5];
    assign ch2_am_depth = r7[31:24];
    assign ch1_fm_dev  = r8[18:0];
    assign ch1_pm_dev  = r8[27:19];
    assign ch2_fm_dev  = r9[18:0];
    assign ch2_pm_dev  = r9[27:19];
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"$SIM/xelab.bat" reg_bridge_tb -s reg_sim
"$SIM/xsim.bat" reg_sim -runall
```
期望: `PASS reg_bridge_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
git commit -m "feat: reg_bridge 调制字 C/D 拆包(gpio6..9, TDD)"
```

---

## Task 6: awg_top 集成(TDD)

**Files:**
- Modify: `src/tb/awg_top_tb.v`
- Modify: `src/rtl/awg_top.v`

- [ ] **Step 1: 扩展测试(红)**

在 awg_top_tb.v 中:
1. 声明 `reg [31:0] gpio6_in = 0, gpio7_in = 0, gpio8_in = 0, gpio9_in = 0;` 并接入实例
   (现有用例全部默认 0 → 回归)。
2. 监控 always 块增加 0x80 平段计数(AM 测试用):

```verilog
    integer ch0_flat;
    ...
        if (counting) begin
            ...
            if (dac0_db == 8'h80) ch0_flat = ch0_flat + 1;
        end
```

3. reset_stats 增加 `ch0_flat = 0;`。
4. 在"square unaffected by harms"之后、最终 PASS 之前追加:

```verilog
        // ---- 调制: 字 C/D 拆包(内部探针) ----
        gpio0_in = pack(20'd100000, 8'd50, 2'd0);   // 100kHz SINE 50%
        gpio6_in = {8'd100, 19'd500, 2'd2, 3'd4};   // 字C: depth100 mfreq500(50Hz) tri SWEEP
        gpio8_in = {4'd0, 9'd128, 19'd50000};       // 字D: pm128 fm_dev50000
        #1000;
        chk("wordC unpack mode/mwave/mfreq/depth",
            uut.ch1_mode == 3'd4 && uut.ch1_mwave == 2'd2 &&
            uut.ch1_mfreq == 19'd500 && uut.ch1_am_depth == 8'd100);
        chk("wordD unpack fm/pm",
            uut.ch1_fm_dev == 19'd50000 && uut.ch1_pm_dev == 9'd128);

        // ---- AM: 方波调制(mfreq=500000 -> m 每 ~500 拍翻转), 增益 0/2 倍 ----
        gpio6_in = {8'd100, 19'd500000, 2'd1, 3'd1};   // mode=AM, mwave=SQUARE
        gpio8_in = 32'd0;
        #1000;
        reset_stats;
        #200_000;
        chk("AM gain2 -> full swing (max>=0xFD)",
            ch0_max >= 8'hFD && ch0_min <= 8'h03);
        chk("AM gain0 -> flat 0x80 runs (>1000)",
            ch0_flat > 1000 && ch0_trans > 500);

        // ---- FM: fword_inst 交替 base±dev(内部探针) ----
        // fword1(100kHz)=8590000; dev(50kHz)=4295000 -> {12885000, 4295000}
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd2};   // mode=FM
        gpio8_in = {4'd0, 9'd0, 19'd50000};
        #1000;
        begin : tfm
            integer i, okhi, oklo, bad;
            okhi = 0; oklo = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst1 == 32'd12885000) okhi = okhi + 1;
                else if (uut.fword_inst1 == 32'd4295000) oklo = oklo + 1;
                else bad = bad + 1;
            end
            chk("FM fword_inst alternates base±dev", okhi > 1000 && oklo > 1000 && bad == 0);
        end

        // ---- PM: pm_off 交替 ±128; 谐波地址偏移 ord×pm_off ----
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd3};   // mode=PM
        gpio8_in = {4'd0, 9'd128, 19'd0};
        #1000;
        begin : tpm
            integer i, okp, okn, bad;
            okp = 0; okn = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.pm_off1 == 9'sd128) okp = okp + 1;
                else if (uut.pm_off1 == -9'sd128) okn = okn + 1;
                else bad = bad + 1;
                // 基波地址 = acc0 + pm_off(mod 256)
                if (uut.u_harm1.s0 !== (uut.u_harm1.acc0[31:24] + uut.pm_off1[7:0]))
                    bad = bad + 1;
            end
            chk("PM pm_off alternates +-128, s0 offset", okp > 1000 && okn > 1000 && bad == 0);
        end

        // ---- SWEEP == FM ----
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd4};   // mode=SWEEP
        #1000;
        begin : tsw
            integer i, okhi, oklo, bad;
            okhi = 0; oklo = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst1 == 32'd12885000) okhi = okhi + 1;
                else if (uut.fword_inst1 == 32'd4295000) oklo = oklo + 1;
                else bad = bad + 1;
            end
            chk("SWEEP == FM fword_inst", okhi > 1000 && oklo > 1000 && bad == 0);
        end
```

- [ ] **Step 2: 运行验证失败**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s top_sim
```
期望: xelab 报端口不匹配(`awg_top` 无 gpio6..9,或 reg_bridge 端口不匹配)。

- [ ] **Step 3: 修改 awg_top.v 集成**

按规格 §5.4 修改:
1. 端口新增 gpio6_in..gpio9_in(注释"调制字 C/D")。
2. reg_bridge 实例接线扩展(新端口)。
3. 声明调制相关 wire 并计算 fword_dev / mfreq_fword:

```verilog
    wire [2:0]  ch1_mode, ch2_mode;
    wire [1:0]  ch1_mwave, ch2_mwave;
    wire [18:0] ch1_mfreq, ch2_mfreq;
    wire [7:0]  ch1_am_depth, ch2_am_depth;
    wire [18:0] ch1_fm_dev, ch2_fm_dev;
    wire [8:0]  ch1_pm_dev, ch2_pm_dev;
    ...
    // 频偏 fword 化(同 fword 公式); 调制累加增量 = mfreq_01 × 562950
    wire [25:0] fword_dev1 = ch1_fm_dev * 20'd85 + ((ch1_fm_dev * 20'd9) / 20'd10);
    wire [25:0] fword_dev2 = ch2_fm_dev * 20'd85 + ((ch2_fm_dev * 20'd9) / 20'd10);
    wire [38:0] mfreq_fword1 = ch1_mfreq * 20'd562950;
    wire [38:0] mfreq_fword2 = ch2_mfreq * 20'd562950;
```

4. 实例化 mod_synth×2,输出 wire:

```verilog
    wire signed [7:0] m1, m2;
    wire [31:0] fword_inst1, fword_inst2;
    wire signed [8:0] pm_off1, pm_off2;
    wire signed [16:0] am_gain1, am_gain2;
    mod_synth u_mod1 (
        .clk(sys_clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword1), .mwave(ch1_mwave), .mode(ch1_mode),
        .am_depth(ch1_am_depth), .fm_dev_fword(fword_dev1), .pm_dev(ch1_pm_dev),
        .fword_base(fword1),
        .m(m1), .fword_inst(fword_inst1), .pm_off(pm_off1), .am_gain(am_gain1)
    );
    mod_synth u_mod2 (
        .clk(sys_clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword2), .mwave(ch2_mwave), .mode(ch2_mode),
        .am_depth(ch2_am_depth), .fm_dev_fword(fword_dev2), .pm_dev(ch2_pm_dev),
        .fword_base(fword2),
        .m(m2), .fword_inst(fword_inst2), .pm_off(pm_off2), .am_gain(am_gain2)
    );
```

5. dds_core/harm_synth 的 fword 改用 fword_inst1/2,新增 pm_off 接线。
6. mux 后新增 am_stage×2,ampl_scale 输入改为 s1_am/s2_am:

```verilog
    wire [7:0] s1_am, s2_am;
    am_stage u_am1 (.clk(sys_clk), .rst_n(rst_n),
                    .sample_in(s1), .am_gain(am_gain1), .sample_out(s1_am));
    am_stage u_am2 (.clk(sys_clk), .rst_n(rst_n),
                    .sample_in(s2), .am_gain(am_gain2), .sample_out(s2_am));
    ampl_scale u_amp1 (.clk(sys_clk), .rst_n(rst_n), .sample_in(s1_am), .ampl_pct(ch1_ampl), .sample_out(a1));
    ampl_scale u_amp2 (.clk(sys_clk), .rst_n(rst_n), .sample_in(s2_am), .ampl_pct(ch2_ampl), .sample_out(a2));
```

- [ ] **Step 4: 运行验证通过**

```bash
SIM="<LOCAL_PATH>"
"$SIM/xvlog.bat" --sv src/rtl/sine_rom.v src/rtl/dds_core.v src/rtl/harm_synth.v \
  src/rtl/ampl_scale.v src/rtl/dac_driver.v src/rtl/reg_bridge.v \
  src/rtl/mod_synth.v src/rtl/am_stage.v src/rtl/awg_top.v src/tb/awg_top_tb.v
"$SIM/xelab.bat" awg_top_tb -s top_sim
"$SIM/xsim.bat" top_sim -runall
```
期望: `PASS awg_top_tb`(旧 12 项回归 + 新 5 项)。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat: awg_top 集成 mod_synth/am_stage(AM/FM/PM/SWEEP 全链路)"
```

---

## Task 7: BD 扩展 + 构建哈希

**Files:**
- Modify: `scripts/create_bd.tcl`
- Modify: `scripts/build_bitstream.py`
- Modify: `scripts/check_bitstream.py`

- [ ] **Step 1: create_bd.tcl 新增 2 个 AXI GPIO**

1. 第 32 行 foreach 列表改为 `{axi_gpio_0 axi_gpio_1 axi_gpio_2 axi_gpio_3 axi_gpio_4}`。
2. GPIO 输出连线追加:

```tcl
connect_bd_net [get_bd_pins axi_gpio_3/gpio_io_o]   [get_bd_pins awg_top_0/gpio6_in]
connect_bd_net [get_bd_pins axi_gpio_3/gpio2_io_o]  [get_bd_pins awg_top_0/gpio7_in]
connect_bd_net [get_bd_pins axi_gpio_4/gpio_io_o]   [get_bd_pins awg_top_0/gpio8_in]
connect_bd_net [get_bd_pins axi_gpio_4/gpio2_io_o]  [get_bd_pins awg_top_0/gpio9_in]
```

3. interconnect 从 3MI 改 5MI(第 68-69 行):

```tcl
set_property -dict [list CONFIG.NUM_MI {5} CONFIG.NUM_SI {1} CONFIG.S00_HAS_REGSLICE {4}] $axiint
```

4. interconnect 从表追加 M03/M04;ACLK 列表、ARESETN 列表、GPIO 时钟/复位 foreach 各加
   axi_gpio_3 axi_gpio_4:

```tcl
foreach {mi slave} {M00_AXI axi_gpio_0/S_AXI  M01_AXI axi_gpio_1/S_AXI  M02_AXI axi_gpio_2/S_AXI
                    M03_AXI axi_gpio_3/S_AXI  M04_AXI axi_gpio_4/S_AXI} {
    connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/$mi] [get_bd_intf_pins $slave]
}
```

- [ ] **Step 2: build_bitstream.py / check_bitstream.py 哈希加入 create_bd.tcl**

两个文件的 `hash_rtl()` 函数改为:

```python
def hash_rtl():
    # 归一化换行(CRLF->LF), 避免 git 检出/touch 改变换行导致哈希漂移误报;
    # 含 create_bd.tcl: BD 源变化(如新增 AXI GPIO)也必须触发重建
    h = hashlib.sha256()
    files = sorted(RTL.glob("*.v")) + [ROOT / "scripts" / "create_bd.tcl"]
    for f in files:
        h.update(f.name.encode("utf-8"))
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()
```

(check_bitstream.py 的 `for f in RTL.glob("*.v")` mtime 提示循环保持不变即可。)

- [ ] **Step 3: 提交**

```bash
git add scripts/create_bd.tcl scripts/build_bitstream.py scripts/check_bitstream.py
git commit -m "build: BD 新增 2× AXI GPIO(调制字 C/D), 哈希覆盖 create_bd.tcl"
```

---

## Task 8: 构建 bitstream + PS 桥扩展

**Files:**
- Modify: `sdk/src/main.c`

- [ ] **Step 1: 完整重建 bitstream**

```bash
python scripts/build_bitstream.py
```
期望: 检测到 RTL+BD 变化 → 完整重建(约 10-20 分钟),末尾 `BITSTREAM-OK`,manifest 更新。
(哈希含 create_bd.tcl/ps7_props.tcl,BD 变更会触发;rebuild_bitstream.tcl 会先重跑
create_bd.tcl 重建 BD 结构,再 generate_target+OOC+主综合+实现。)
注意: 重建后应确认 host_bd.bd 内含 axi_gpio_3/4。

- [ ] **Step 2: main.c 扩展**

1. ch_state_t 新增字段:

```c
    u32 mode;      /* 0=OFF 1=AM 2=FM 3=PM 4=SWEEP */
    u32 mwave;     /* 0=SINE 1=SQUARE 2=TRIANGLE 3=SAW */
    u32 mfreq_01;  /* 0.1Hz 单位, 1..500000 */
    u32 am_depth;  /* 0..100 */
    u32 fm_dev;    /* 0..500000 Hz */
    u32 pm_dev;    /* 0..511, 1/256 周期 */
```

2. 文件作用域加 MODE_NAME(仿 WAVE_NAME):

```c
static const char *WAVE_NAME[4] = {"SINE", "SQUARE", "TRIANGLE", "SAW"};
static const char *MODE_NAME[5] = {"OFF", "AM", "FM", "PM", "SWEEP"};
```

3. pack_word_c/d + write_all 追加:

```c
static u32 pack_word_c(const ch_state_t *s)
{
    /* {am_depth[7:0], mfreq[18:0], mwave[1:0], mode[2:0]} */
    return (s->am_depth << 24) | (s->mfreq_01 << 5) | (s->mwave << 3) | s->mode;
}

static u32 pack_word_d(const ch_state_t *s)
{
    /* {reserved[3:0], pm_dev[8:0], fm_dev[18:0]} */
    return (s->pm_dev << 19) | s->fm_dev;
}
```

write_all() 末尾追加:

```c
    XGpio_DiscreteWrite(&Gpio4, 1, pack_word_c(&ch[0]));    /* ch6: CH1 字C */
    XGpio_DiscreteWrite(&Gpio4, 2, pack_word_c(&ch[1]));    /* ch7: CH2 字C */
    XGpio_DiscreteWrite(&Gpio5, 1, pack_word_d(&ch[0]));    /* ch8: CH1 字D */
    XGpio_DiscreteWrite(&Gpio5, 2, pack_word_d(&ch[1]));    /* ch9: CH2 字D */
```

4. main() 初始化 Gpio4/Gpio5(XPAR_AXI_GPIO_3/4_DEVICE_ID,双通道 all-output)与默认值:

```c
    ch[0].mode = 0; ch[0].mwave = 0; ch[0].mfreq_01 = 10;
    ch[0].am_depth = 100; ch[0].fm_dev = 0; ch[0].pm_dev = 0;
```

5. SET 分支新增 6 条命令(放在 AMPL 分支后、BASE 分支前;与 WAVE/FREQ/AMPL 一样
   落入末尾 `write_all(); uart_send("OK\r\n"); return;`):

```c
        } else if (strcmp(tok[2], "MODE") == 0) {
            int md;
            for (md = 0; md < 5; md++)
                if (strcmp(tok[3], MODE_NAME[md]) == 0) break;
            if (md >= 5) { uart_send("ERR MODE OUT OF RANGE\r\n"); return; }
            ch[ci].mode = (u32)md;
        } else if (strcmp(tok[2], "MODWAVE") == 0) {
            int w;
            for (w = 0; w < 4; w++)
                if (strcmp(tok[3], WAVE_NAME[w]) == 0) break;
            if (w >= 4) { uart_send("ERR MODWAVE OUT OF RANGE\r\n"); return; }
            ch[ci].mwave = (u32)w;
        } else if (strcmp(tok[2], "MODFREQ") == 0) {
            u32 v;
            if (!parse_u32(tok[3], &v) || v < 1 || v > 500000) {
                uart_send("ERR MODFREQ OUT OF RANGE\r\n"); return;
            }
            ch[ci].mfreq_01 = v;
        } else if (strcmp(tok[2], "AMDEPTH") == 0) {
            u32 v;
            if (!parse_u32(tok[3], &v) || v > 100) {
                uart_send("ERR AMDEPTH OUT OF RANGE\r\n"); return;
            }
            ch[ci].am_depth = v;
        } else if (strcmp(tok[2], "FMDEV") == 0) {
            u32 v;
            if (!parse_u32(tok[3], &v) || v > 500000) {
                uart_send("ERR FMDEV OUT OF RANGE\r\n"); return;
            }
            ch[ci].fm_dev = v;
        } else if (strcmp(tok[2], "PMDEV") == 0) {
            u32 v;
            if (!parse_u32(tok[3], &v) || v > 511) {
                uart_send("ERR PMDEV OUT OF RANGE\r\n"); return;
            }
            ch[ci].pm_dev = v;
        } else if (strcmp(tok[2], "BASE") == 0) {
```

6. GET 输出扩展(缓冲区 96→160,末尾追加 MODE..PMDEV):

```c
        char out[160];
        sprintf(out, "CH%d WAVE %s FREQ %lu AMPL %lu BASE %lu "
                     "H1 %lu %lu H2 %lu %lu H3 %lu %lu H4 %lu %lu "
                     "MODE %s MODWAVE %s MODFREQ %lu AMDEPTH %lu FMDEV %lu PMDEV %lu\r\n",
                ci + 1, WAVE_NAME[ch[ci].wave],
                (unsigned long)ch[ci].freq_hz, (unsigned long)ch[ci].ampl_pct,
                (unsigned long)ch[ci].base_pct,
                (unsigned long)ch[ci].h_ord[0], (unsigned long)ch[ci].h_ampl[0],
                (unsigned long)ch[ci].h_ord[1], (unsigned long)ch[ci].h_ampl[1],
                (unsigned long)ch[ci].h_ord[2], (unsigned long)ch[ci].h_ampl[2],
                (unsigned long)ch[ci].h_ord[3], (unsigned long)ch[ci].h_ampl[3],
                MODE_NAME[ch[ci].mode], WAVE_NAME[ch[ci].mwave],
                (unsigned long)ch[ci].mfreq_01, (unsigned long)ch[ci].am_depth,
                (unsigned long)ch[ci].fm_dev, (unsigned long)ch[ci].pm_dev);
```

- [ ] **Step 3: 编译 PS 桥并下载(需板卡在线)**

```bash
cli-anything-vivado sdk app build
bash scripts/program_board.sh
cli-anything-vivado sdk run
```
期望: 串口打印 `AC820-AWG bridge ready`。

- [ ] **Step 4: 提交**

```bash
git add sdk/src/main.c
git commit -m "feat: PS 桥调制命令 MODE/MODWAVE/MODFREQ/AMDEPTH/FMDEV/PMDEV + GET 扩展"
```

---

## Task 9: 协议回归扩展(板上)

**Files:**
- Modify: `scripts/test_bridge.py`

- [ ] **Step 1: 复位循环追加调制复位**

在顶部复位循环的 `for c in (...)` 列表中追加:

```python
              "SET %s MODE OFF" % ch, "SET %s MODWAVE SINE" % ch,
              "SET %s MODFREQ 10" % ch, "SET %s AMDEPTH 100" % ch,
              "SET %s FMDEV 0" % ch, "SET %s PMDEV 0" % ch,
```

- [ ] **Step 2: 追加调制用例(放在现有用例之后、最终汇总之前)**

```python
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
    "CH1 WAVE SINE FREQ 1000 AMPL 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE FM MODWAVE SQUARE MODFREQ 100 AMDEPTH 80 FMDEV 10000 PMDEV 64")
cmd("SET CH1 MODE OFF", "OK")
cmd("GET CH1",
    "CH1 WAVE SINE FREQ 1000 AMPL 50 BASE 100 H1 0 0 H2 0 0 H3 0 0 H4 0 0 "
    "MODE OFF MODWAVE SQUARE MODFREQ 100 AMDEPTH 80 FMDEV 10000 PMDEV 64")
# 模式关闭保留其余参数

# 越界
cmd("SET CH1 MODE FOO", "ERR MODE OUT OF RANGE")
cmd("SET CH1 MODE 5", "ERR MODE OUT OF RANGE")
cmd("SET CH1 MODWAVE BOGUS", "ERR MODWAVE OUT OF RANGE")
cmd("SET CH1 MODFREQ 0", "ERR MODFREQ OUT OF RANGE")
cmd("SET CH1 MODFREQ 500001", "ERR MODFREQ OUT OF RANGE")
cmd("SET CH1 AMDEPTH 101", "ERR AMDEPTH OUT OF RANGE")
cmd("SET CH1 FMDEV 500001", "ERR FMDEV OUT OF RANGE")
cmd("SET CH1 PMDEV 512", "ERR PMDEV OUT OF RANGE")
```

(最终汇总代码位于文件末尾;新增用例应插在其之前。)

- [ ] **Step 3: 运行回归(需板卡在线)**

```bash
python scripts/test_bridge.py COM11
```
期望: 原有 39 项 + 新增 12 项全部 PASS。

- [ ] **Step 4: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test: 协议回归新增调制命令 12 项"
```

---

## Task 10: host protocol.py 调制命令(TDD)

**Files:**
- Modify: `host/siggen/protocol.py`
- Modify: `host/tests/test_protocol.py`

- [ ] **Step 1: 写失败测试(追加到 test_protocol.py)**

```python
def test_mod_cmd():
    assert mod_cmd(1, "MODE", "FM") == "SET CH1 MODE FM"
    assert mod_cmd(1, "mode", "sweep") == "SET CH1 MODE SWEEP"
    assert mod_cmd(2, "MODWAVE", "TRIANGLE") == "SET CH2 MODWAVE TRIANGLE"
    assert mod_cmd(1, "MODFREQ", 100) == "SET CH1 MODFREQ 100"
    assert mod_cmd(1, "AMDEPTH", 80) == "SET CH1 AMDEPTH 80"
    assert mod_cmd(1, "FMDEV", 10000) == "SET CH1 FMDEV 10000"
    assert mod_cmd(1, "PMDEV", 64) == "SET CH1 PMDEV 64"
    with pytest.raises(ValueError):
        mod_cmd(1, "MODE", "FOO")
    with pytest.raises(ValueError):
        mod_cmd(1, "MODWAVE", "BOGUS")
    with pytest.raises(ValueError):
        mod_cmd(1, "MODFREQ", 0)
    with pytest.raises(ValueError):
        mod_cmd(1, "MODFREQ", 500001)
    with pytest.raises(ValueError):
        mod_cmd(1, "AMDEPTH", 101)
    with pytest.raises(ValueError):
        mod_cmd(1, "FMDEV", 500001)
    with pytest.raises(ValueError):
        mod_cmd(1, "PMDEV", 512)
    with pytest.raises(ValueError):
        mod_cmd(3, "MODE", "AM")
    with pytest.raises(ValueError):
        mod_cmd(1, "FOO", 1)


def test_parse_modfreq_input():
    assert parse_modfreq_input("1") == 10        # 1.0Hz
    assert parse_modfreq_input("1.5") == 15
    assert parse_modfreq_input("10k") == 100000  # 10kHz
    assert parse_modfreq_input("0.1") == 1       # 0.1Hz 下限
    assert parse_modfreq_input("50k") == 500000  # 50kHz 上限
    with pytest.raises(ValueError):
        parse_modfreq_input("0.05")
    with pytest.raises(ValueError):
        parse_modfreq_input("abc")
    with pytest.raises(ValueError):
        parse_modfreq_input("51k")


def test_parse_status_mod():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 "
                     "H2 0 0 H3 0 0 H4 0 0 MODE FM MODWAVE SQUARE MODFREQ 100 "
                     "AMDEPTH 80 FMDEV 10000 PMDEV 64")
    assert d["mode"] == "FM" and d["mwave"] == "SQUARE"
    assert d["mfreq_01"] == 100 and d["am_depth"] == 80
    assert d["fm_dev"] == 10000 and d["pm_dev"] == 64
    d2 = parse_status("CH1 WAVE SINE FREQ 1000 AMPL 50")
    assert d2["mode"] is None and d2["fm_dev"] is None   # 旧格式兼容
```

更新文件头 import:

```python
from siggen.protocol import (
    set_cmd, get_cmd, parse_response, parse_status,
    parse_freq_input, base_cmd, harm_cmd, mod_cmd, parse_modfreq_input,
)
```

- [ ] **Step 2: 运行验证失败**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v
```
期望: FAIL(ImportError: cannot import name 'mod_cmd')。

- [ ] **Step 3: protocol.py 实现**

1. 常量区追加:

```python
MODES = ("OFF", "AM", "FM", "PM", "SWEEP")
MODWAVES = WAVES
MODFREQ_MIN, MODFREQ_MAX = 1, 500_000      # 0.1Hz 单位
FMDEV_MAX = 500_000
PMDEV_MAX = 511
```

2. 新增 mod_cmd(放在 harm_cmd 之后):

```python
def mod_cmd(ch: int, field: str, value) -> str:
    """构造调制 SET 命令;非法参数抛 ValueError。
    field: MODE|MODWAVE|MODFREQ|AMDEPTH|FMDEV|PMDEV"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    field = field.upper()
    if field == "MODE":
        name = str(value).upper()
        if name not in MODES:
            raise ValueError(f"bad mod mode: {value}")
        return f"SET CH{ch} MODE {name}"
    if field == "MODWAVE":
        name = str(value).upper()
        if name not in WAVES:
            raise ValueError(f"bad mod wave: {value}")
        return f"SET CH{ch} MODWAVE {name}"
    if field == "MODFREQ":
        n = int(value)
        if not MODFREQ_MIN <= n <= MODFREQ_MAX:
            raise ValueError(f"modfreq out of range: {value}")
        return f"SET CH{ch} MODFREQ {n}"
    if field == "AMDEPTH":
        n = int(value)
        if not 0 <= n <= 100:
            raise ValueError(f"am depth out of range: {value}")
        return f"SET CH{ch} AMDEPTH {n}"
    if field == "FMDEV":
        n = int(value)
        if not 0 <= n <= FMDEV_MAX:
            raise ValueError(f"fm dev out of range: {value}")
        return f"SET CH{ch} FMDEV {n}"
    if field == "PMDEV":
        n = int(value)
        if not 0 <= n <= PMDEV_MAX:
            raise ValueError(f"pm dev out of range: {value}")
        return f"SET CH{ch} PMDEV {n}"
    raise ValueError(f"bad field: {field}")


def parse_modfreq_input(s: str) -> int:
    """解析调制频率(Hz, 支持小数与 k/M 后缀) -> 0.1Hz 单位整数(1..500000)。"""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)\s*$", s, re.IGNORECASE)
    if not m:
        raise ValueError(f"bad mod freq input: {s!r}")
    v = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        v *= 1000
    elif unit == "m":
        v *= 1_000_000
    n = int(round(v * 10))
    if not MODFREQ_MIN <= n <= MODFREQ_MAX:
        raise ValueError(f"mod freq out of range: {s!r}")
    return n
```

3. parse_status 扩展(默认字段 + 解析键):

```python
    d = {"ch": int(parts[0][2]), "wave": parts[2],
         "freq_hz": int(parts[4]), "ampl_pct": int(parts[6]),
         "base_pct": None, "harms": [],
         "mode": None, "mwave": None, "mfreq_01": None,
         "am_depth": None, "fm_dev": None, "pm_dev": None}
    i = 7
    while i < len(parts):
        key = parts[i]
        if key == "BASE":
            d["base_pct"] = int(parts[i + 1])
            i += 2
        elif key in ("H1", "H2", "H3", "H4"):
            d["harms"].append((int(parts[i + 1]), int(parts[i + 2])))
            i += 3
        elif key in ("MODE", "MODWAVE"):
            d[key.lower()] = parts[i + 1]
            i += 2
        elif key in ("MODFREQ", "AMDEPTH", "FMDEV", "PMDEV"):
            d[key.lower()] = int(parts[i + 1])
            i += 2
        else:
            i += 1
    return d
```

- [ ] **Step 4: 运行验证通过**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_protocol.py -v
```
期望: 全部 PASS(含旧用例回归)。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat: protocol.py 调制命令 mod_cmd/parse_modfreq_input/parse_status 扩展(TDD)"
```

---

## Task 11: host controller.py 调制字段(TDD)

**Files:**
- Modify: `host/siggen/controller.py`
- Modify: `host/tests/test_controller.py`

- [ ] **Step 1: 写失败测试(追加到 test_controller.py)**

```python
def test_mod_field_sends_command(ctrl):
    ctrl.set_field(0, "mode", "FM")
    assert ctrl.worker.sent[-1] == "SET CH1 MODE FM\r\n"
    ctrl.set_field(1, "fmdev", 10000)
    assert ctrl.worker.sent[-1] == "SET CH2 FMDEV 10000\r\n"
    ctrl.set_field(0, "pmdev", 128)
    assert ctrl.worker.sent[-1] == "SET CH1 PMDEV 128\r\n"


def test_mod_invalid_rejected(ctrl):
    ctrl.set_field(0, "mode", "FOO")
    assert ctrl.worker.sent == []


def test_mod_ack_applies_state(ctrl):
    ctrl.set_field(0, "mode", "FM")
    ctrl._on_line("OK")
    assert ctrl.state[0].mode == "FM"
    ctrl.set_field(0, "fmdev", 10000)
    ctrl._on_line("OK")
    assert ctrl.state[0].fm_dev == 10000


def test_mod_status_updates_state(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 0 0 "
                  "H3 0 0 H4 0 0 MODE PM MODWAVE SAW MODFREQ 50 AMDEPTH 60 "
                  "FMDEV 5000 PMDEV 32")
    st = ctrl.state[0]
    assert st.mode == "PM" and st.mwave == "SAW"
    assert st.mfreq_01 == 50 and st.am_depth == 60
    assert st.fm_dev == 5000 and st.pm_dev == 32
```

- [ ] **Step 2: 运行验证失败**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_controller.py -v
```
期望: FAIL(ChannelState 无 mode 字段 / set_field 不发 MODE 命令)。

- [ ] **Step 3: controller.py 实现**

1. import 追加 `mod_cmd`:

```python
from .protocol import (set_cmd, get_cmd, parse_response, parse_status,
                       base_cmd, harm_cmd, mod_cmd)
```

2. ChannelState 追加字段:

```python
    mode: str = "OFF"
    mwave: str = "SINE"
    mfreq_01: int = 10
    am_depth: int = 100
    fm_dev: int = 0
    pm_dev: int = 0
```

3. set_field 中(harm 分支之后、set_cmd 兜底之前)插入调制路由:

```python
        if field_l in ("mode", "mwave", "modfreq", "amdepth", "fmdev", "pmdev"):
            try:
                cmd = mod_cmd(ch + 1, field_l, value)
            except ValueError as e:
                self.log.emit(str(e), "error")
                return
            self._send(ch, field_l, value, cmd)
            return
```

4. _on_line 的 OK 分支追加(置于 base/harm 分支之前):

```python
                elif field == "mode":
                    st.mode = str(value).upper()
                elif field == "mwave":
                    st.mwave = str(value).upper()
                elif field in ("modfreq", "amdepth", "fmdev", "pmdev"):
                    setattr(st, field, int(value))
```

5. _on_line 的状态行分支追加(harms 同步之后):

```python
            for k in ("mode", "mwave", "mfreq_01", "am_depth", "fm_dev", "pm_dev"):
                if d.get(k) is not None:
                    setattr(st, k, d[k])
```

- [ ] **Step 4: 运行验证通过**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_controller.py -v
```
期望: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/controller.py host/tests/test_controller.py
git commit -m "feat: controller 调制字段与命令路由(TDD)"
```

---

## Task 12: host preview.py 调制预览(TDD)

**Files:**
- Modify: `host/siggen/preview.py`
- Modify: `host/tests/test_preview.py`(新建)

- [ ] **Step 1: 写失败测试(新建 test_preview.py)**

```python
import numpy as np
import pytest

from siggen.preview import make_plot_data


def test_make_plot_data_mod_off_identical():
    t, y = make_plot_data("SINE", 1000, 80, base_pct=100, harms=[(2, 50)],
                          mod={"mode": "OFF"})
    t2, y2 = make_plot_data("SINE", 1000, 80, base_pct=100, harms=[(2, 50)])
    assert np.allclose(y, y2)


def test_am_envelope_depth100():
    # 载波 1kHz 3 周期窗口, 调制 1kHz 100%: 包络 0..2
    t, y = make_plot_data("SINE", 1000, 100,
                          mod={"mode": "AM", "mwave": "SINE",
                               "mfreq": 1000, "am_depth": 100})
    assert abs(y.max() - 2.0) < 0.1
    assert abs(y.min() + 2.0) < 0.1
    assert np.min(np.abs(y)) < 0.05          # 包络谷点


def test_fm_square_dev():
    # 方波调制 ±1: 瞬时频率 800/1200Hz 交替 -> 上升过零间隔 1667/2500 点
    t, y = make_plot_data("SINE", 1000, 100, n=6000,
                          mod={"mode": "FM", "mwave": "SQUARE",
                               "mfreq": 500, "fm_dev": 200})
    up = np.where((y[:-1] < 0) & (y[1:] >= 0))[0]
    gaps = np.diff(up)
    assert gaps.min() < 2000 and gaps.max() > 2000


def test_pm_square_shift():
    # 方波调制 90°: m=+1 区间相位 +90°(=cos), m=-1 区间 -90°(=-cos)
    t, y = make_plot_data("SINE", 1000, 100, n=6000,
                          mod={"mode": "PM", "mwave": "SQUARE",
                               "mfreq": 500, "pm_dev": 64})
    cos = np.cos(2 * np.pi * 1000 * t)
    m = np.where(t < 1e-3, 1.0, -1.0)        # 调制 500Hz 方波: 前 1ms +1
    assert np.allclose(y, cos * m, atol=0.05)


def test_sweep_equals_fm():
    kw = dict(mode="FM", mwave="TRIANGLE", mfreq=100, fm_dev=10000)
    t1, y1 = make_plot_data("SINE", 1000, 100, mod=kw)
    kw2 = dict(kw, mode="SWEEP")
    t2, y2 = make_plot_data("SINE", 1000, 100, mod=kw2)
    assert np.allclose(y1, y2)
```

- [ ] **Step 2: 运行验证失败**

```bash
host/.venv/Scripts/python -m pytest host/tests/test_preview.py -v
```
期望: FAIL(make_plot_data 无 mod 参数,TypeError)。

- [ ] **Step 3: preview.py 实现**

新增私有辅助函数,重写 make_plot_data(旧路径不变,mod 缺省/ OFF 时走原逻辑):

```python
PERIODS = 3   # 显示 3 个周期
MOD_FS = 50e6  # 与硬件一致的调制/相位积分采样率


def _waveform(wave, phase, base_pct, harms):
    """phase 为周期单位数组/标量;SINE 含谐波叠加(理论同相, 与硬件一致)。"""
    if wave == "SINE":
        y = np.zeros(np.asarray(phase).shape)
        y += (base_pct / 100.0) * np.sin(2 * np.pi * phase)
        for order, a in (harms or []):
            if order:
                y += (a / 100.0) * np.sin(2 * np.pi * phase * order)
        return y
    if wave == "SQUARE":
        return np.where(phase < 0.5, 1.0, -1.0)
    if wave == "TRIANGLE":
        return 4 * np.abs(phase - 0.5) - 1
    return 2 * phase - 1


def _mod_wave(mwave, phase):
    if mwave == "SINE":
        return np.sin(2 * np.pi * phase)
    if mwave == "SQUARE":
        return np.where(phase < 0.5, 1.0, -1.0)
    if mwave == "TRIANGLE":
        return 4 * np.abs(phase - 0.5) - 1
    return 2 * phase - 1


def make_plot_data(wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, n: int = 600, mod=None):
    """返回 (t, y)。harms = [(order, ampl_pct), ...] 归一化值。
    mod = dict(mode, mwave, mfreq, am_depth, fm_dev, pm_dev);mode OFF/缺省直通。"""
    if freq_hz <= 0:
        return np.zeros(n), np.zeros(n)
    mod = mod or {}
    if mod.get("mode", "OFF") == "OFF":
        t = np.linspace(0, PERIODS / freq_hz, n, endpoint=False)
        phase = (t * freq_hz) % 1.0
        y = _waveform(wave, phase, base_pct, harms)
        return t, y * ampl_pct / 100.0

    # 调制路径: 50MHz 相位积分 + 抽取到 n 点
    total_s = PERIODS / freq_hz
    ns = max(int(round(total_s * MOD_FS)), 2)
    m_ph = (np.arange(ns) * mod.get("mfreq", 1.0) / MOD_FS) % 1.0
    m = _mod_wave(mod.get("mwave", "SINE"), m_ph)
    mode = mod["mode"]
    if mode == "AM":
        gain = 1.0 + m * mod.get("am_depth", 0) / 100.0
        phase = (np.cumsum(np.full(ns, freq_hz)) / MOD_FS) % 1.0
        y = _waveform(wave, phase, base_pct, harms) * gain
    elif mode in ("FM", "SWEEP"):
        f_inst = freq_hz + m * mod.get("fm_dev", 0)
        phase = (np.cumsum(f_inst) / MOD_FS) % 1.0
        y = _waveform(wave, phase, base_pct, harms)
    elif mode == "PM":
        off = m * mod.get("pm_dev", 0) / 256.0          # 周期单位
        phase = ((np.cumsum(np.full(ns, freq_hz)) / MOD_FS) % 1.0 + off)
        y = _waveform(wave, phase, base_pct, harms)
    else:
        raise ValueError(f"bad mod mode: {mode}")
    idx = np.linspace(0, ns, n, endpoint=False).astype(int)
    t = np.linspace(0, total_s, n, endpoint=False)
    return t, np.clip(y[idx], -1, 1) * ampl_pct / 100.0
```

- [ ] **Step 4: 运行验证通过**

```bash
host/.venv/Scripts/python -m pytest host/tests/ -v
```
期望: 全部 PASS(含旧 protocol/controller 用例回归)。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/preview.py host/tests/test_preview.py
git commit -m "feat: preview 调制预览(50MHz 相位积分, AM/FM/PM/SWEEP)"
```

---

## Task 13: GUI 调制面板

**Files:**
- Modify: `host/siggen/ui/widgets.py`
- Modify: `host/siggen/ui/main_window.py`

- [ ] **Step 1: widgets.py 调制区**

1. import 追加:

```python
from ..protocol import WAVES, MODES, parse_freq_input, parse_modfreq_input
```

2. ChannelPanel.__init__ 在谐波区之后、Vpp 区之前插入调制区(注意 `self._updating` 构建
   期间为 True,控件信号会被 _emit_mod 忽略):

```python
        # ---- 调制区 ----
        mod_box = QVBoxLayout()
        mod_row1 = QHBoxLayout()
        mod_row1.addWidget(QLabel("调制"))
        self.mod_mode = QComboBox()
        self.mod_mode.addItems(["关", "AM", "FM", "PM", "扫频"])
        self.mod_mode.currentIndexChanged.connect(
            lambda idx: self._emit_mod("mode", MODES[idx]))
        mod_row1.addWidget(self.mod_mode)
        self.mod_wave = QComboBox()
        self.mod_wave.addItems(["正弦", "方波", "三角", "锯齿"])
        self.mod_wave.currentIndexChanged.connect(
            lambda idx: self._emit_mod("mwave", WAVES[idx]))
        mod_row1.addWidget(self.mod_wave)
        mod_box.addLayout(mod_row1)

        mod_row2 = QHBoxLayout()
        mod_row2.addWidget(QLabel("频率"))
        self.mod_freq = QLineEdit()
        self.mod_freq.setPlaceholderText("Hz (0.1~50k)")
        self.mod_freq.returnPressed.connect(self._mod_freq_typed)
        self.mod_freq_label = QLabel("1.0 Hz")
        mod_row2.addWidget(self.mod_freq)
        mod_row2.addWidget(self.mod_freq_label)
        mod_row2.addWidget(QLabel("AM深度"))
        self.am_depth = QSlider(Qt.Horizontal)
        self.am_depth.setRange(0, 100)
        self.am_depth.setValue(100)
        self.am_depth.valueChanged.connect(
            lambda v: self._emit_mod("amdepth", v))
        self.am_depth_label = QLabel("100%")
        mod_row2.addWidget(self.am_depth, 1)
        mod_row2.addWidget(self.am_depth_label)
        mod_box.addLayout(mod_row2)

        mod_row3 = QHBoxLayout()
        mod_row3.addWidget(QLabel("FM频偏"))
        self.fm_dev = QSlider(Qt.Horizontal)
        self.fm_dev.setRange(0, 500000)
        self.fm_dev.valueChanged.connect(
            lambda v: self._emit_mod("fmdev", v))
        self.fm_dev_label = QLabel("0 Hz")
        mod_row3.addWidget(self.fm_dev, 1)
        mod_row3.addWidget(self.fm_dev_label)
        mod_row3.addWidget(QLabel("PM相位"))
        self.pm_dev = QSlider(Qt.Horizontal)
        self.pm_dev.setRange(0, 128)
        self.pm_dev.valueChanged.connect(
            lambda v: self._emit_mod("pmdev", v))
        self.pm_dev_label = QLabel("0°")
        mod_row3.addWidget(self.pm_dev, 1)
        mod_row3.addWidget(self.pm_dev_label)
        mod_box.addLayout(mod_row3)
        lay.addLayout(mod_box)
```

3. 状态字段与发射方法(在 _emit_harm_if_user 附近):

```python
        self._mod_freq_01 = 10

    def _emit_mod(self, field, value):
        if self._updating:
            return
        self.param_changed.emit(field, value)
        self._update_mod_labels()

    def _mod_freq_typed(self):
        try:
            v = parse_modfreq_input(self.mod_freq.text())
        except ValueError:
            return
        self._mod_freq_01 = v
        self._emit_mod("modfreq", v)
        self.mod_freq.clear()

    def _update_mod_labels(self):
        self.mod_freq_label.setText(f"{self._mod_freq_01 / 10.0:g} Hz")
        self.am_depth_label.setText(f"{self.am_depth.value()}%")
        self.fm_dev_label.setText(f"{self.fm_dev.value()} Hz")
        self.pm_dev_label.setText(f"{int(round(self.pm_dev.value() * 1.40625))}°")
```

4. set_state 追加调制参数与同步(末尾 _update_vpp 前):

```python
    def set_state(self, wave: str, freq_hz: int, ampl_pct: int,
                  base_pct: int = 100, harms=None,
                  mode: str = "OFF", mwave: str = "SINE", mfreq_01: int = 10,
                  am_depth: int = 100, fm_dev: int = 0, pm_dev: int = 0):
        ...
        self._mod_freq_01 = mfreq_01
        for combo, name, names in ((self.mod_mode, mode, MODES),
                                   (self.mod_wave, mwave, WAVES)):
            combo.blockSignals(True)
            combo.setCurrentIndex(names.index(name) if name in names else 0)
            combo.blockSignals(False)
        for slider, val in ((self.am_depth, am_depth),
                            (self.fm_dev, fm_dev),
                            (self.pm_dev, pm_dev)):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
        self._update_mod_labels()
        self._update_norm_hint()
        self._update_vpp()
```

(将 set_state 中原末尾的两行 `self._update_norm_hint(); self._update_vpp();` 保留在追加代码之后。)

- [ ] **Step 2: main_window.py 传递调制参数**

```python
    def _on_state(self, ch: int):
        st = self.ctrl.state[ch]
        panel = self.ch1 if ch == 0 else self.ch2
        panel.set_state(st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms,
                        st.mode, st.mwave, st.mfreq_01, st.am_depth,
                        st.fm_dev, st.pm_dev)
        self.status_panel.set_channel(ch, st)
        self.status_panel.preview.set_params(
            st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms,
            mod={"mode": st.mode, "mwave": st.mwave,
                 "mfreq": st.mfreq_01 / 10.0, "am_depth": st.am_depth,
                 "fm_dev": st.fm_dev, "pm_dev": st.pm_dev})
```

preview.py 的 WavePreview.set_params 同步追加 mod 参数:

```python
    def set_params(self, wave: str, freq_hz: int, ampl_pct: int,
                   base_pct: int = 100, harms=None, mod=None) -> None:
        t, y = make_plot_data(wave, freq_hz, ampl_pct, base_pct, harms, mod=mod)
        self._curve.setData(t, y)
```

- [ ] **Step 3: 运行上位机单元测试回归 + GUI 冒烟**

```bash
host/.venv/Scripts/python -m pytest host/tests/ -v
host/.venv/Scripts/python -m siggen.ui.main_window   # 若入口存在则启动 GUI 目检
```
(GUI 目检: 调制区控件可操作,模式切换时预览随之变化,无异常日志。)

- [ ] **Step 4: 提交**

```bash
git add host/siggen/ui/widgets.py host/siggen/ui/main_window.py host/siggen/preview.py
git commit -m "feat: GUI 调制面板(模式/波形/频率/AM深度/FM频偏/PM相位)"
```

---

## Task 14: scope_verify.py 调制闭环(板上)

**Files:**
- Modify: `scripts/scope_verify.py`

- [ ] **Step 1: 抽取 FFT 辅助函数并扩展用例**

1. 新增辅助函数(measure 之后):

```python
def wav_fft():
    xinc = float(inst.query(":WAV:XINC?"))
    data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
    inst.write(":RUN")
    y = np.array(data, dtype=np.uint8).astype(float)
    y = y - np.mean(y)
    n = len(y)
    spec = np.abs(np.fft.rfft(y * np.hanning(n)))
    return np.fft.rfftfreq(n, xinc), spec
```

(现有 HARM FFT 用例可改用此函数,行为不变。)

2. 在 HARM FFT 用例之后、`inst.close()` 之前追加 4 个调制用例:

```python
    # ---- 内调制 ----
    # AM 包络: 载波 10kHz 50% + 正弦调制 1kHz 100% -> 包络 0~2×, Vpp ≈ cal
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 50",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE AM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 1000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 0"])
    scope_acq(200e-6, 2.0)
    f, v = measure()
    am_ok = v is not None and abs(v - cal) <= cal * 0.06
    report("AM envelope 10k/50% + 1kHz/100%", am_ok,
           "Vpp=%.3fV (exp ~%.3f +-6%%)" % (v or -1, cal))

    # FM 边带: 载波 10kHz + 调制 1kHz 频偏 2kHz -> FFT 边带 10k±1k
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE FM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 1000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 2000", "SET CH1 PMDEV 0"])
    scope_acq(400e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m_c = (freqs > 9500) & (freqs < 10500)
        m_s = ((freqs > 8500) & (freqs < 9500)) | ((freqs > 10500) & (freqs < 11500))
        ac = float(np.max(spec[m_c]))
        asb = float(np.max(spec[m_s]))
        ok = ac > 0 and asb > 0.1 * ac
        report("FM sidebands f0±fm (dev 2kHz)", ok,
               "carrier=%.1f side=%.1f ratio=%.2f" % (ac, asb, asb / ac if ac else 0))
    except Exception as e:
        report("FM sidebands f0±fm (dev 2kHz)", False, "waveform read failed: %r" % e)

    # PM 边带: 载波 10kHz + 相位调制 1kHz 90°(pm_dev=64)
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE PM", "SET CH1 MODWAVE SINE", "SET CH1 MODFREQ 1000",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 0", "SET CH1 PMDEV 64"])
    scope_acq(400e-6, 2.0)
    try:
        freqs, spec = wav_fft()
        m_c = (freqs > 9500) & (freqs < 10500)
        m_s = ((freqs > 8500) & (freqs < 9500)) | ((freqs > 10500) & (freqs < 11500))
        ac = float(np.max(spec[m_c]))
        asb = float(np.max(spec[m_s]))
        ok = ac > 0 and asb > 0.05 * ac
        report("PM sidebands f0±fm (dev 90°)", ok,
               "carrier=%.1f side=%.1f ratio=%.2f" % (ac, asb, asb / ac if ac else 0))
    except Exception as e:
        report("PM sidebands f0±fm (dev 90°)", False, "waveform read failed: %r" % e)

    # SWEEP: 载波 10kHz 三角扫频 ±5kHz @ 100Hz -> 第一象限平均频率 > 第三象限
    setup_board(["SET CH1 WAVE SINE", "SET CH1 FREQ 10000", "SET CH1 AMPL 100",
                 "SET CH1 BASE 0",
                 "SET CH1 MODE SWEEP", "SET CH1 MODWAVE TRIANGLE", "SET CH1 MODFREQ 100",
                 "SET CH1 AMDEPTH 100", "SET CH1 FMDEV 5000", "SET CH1 PMDEV 0"])
    scope_acq(2e-3, 2.0)
    try:
        xinc = float(inst.query(":WAV:XINC?"))
        data = inst.query_binary_values(":WAV:DATA?", datatype="B", container=list)
        inst.write(":RUN")
        y = np.array(data, dtype=np.uint8).astype(float)
        mid = np.mean(y)
        n4 = len(y) // 4

        def freq_chunk(a):
            zc = np.sum((a[:-1] < mid) & (a[1:] >= mid))
            return zc / (xinc * (len(a) - 1))

        f1 = freq_chunk(y[:n4])                 # 三角 m:1→0, 频率 15k→10k, 均值 12.5k
        f3 = freq_chunk(y[2 * n4:3 * n4])       # 三角 m:-1→0, 频率 5k→10k, 均值 7.5k
        ok = f1 - f3 > 2000
        report("SWEEP tri +-5kHz (f_q1 > f_q3)", ok,
               "f_q1=%.0fHz f_q3=%.0fHz" % (f1, f3))
    except Exception as e:
        report("SWEEP tri +-5kHz (f_q1 > f_q3)", False, "waveform read failed: %r" % e)
```

(注: 三角波 m(t)=4|ph−0.5|−1,ph=t/10ms;第一象限 ph∈[0,0.25] → m 由 1→0 下降,
频率 15k→10k 均值 12.5k;第三象限 ph∈[0.5,0.75] → m 由 −1→0,均值 7.5k。)
(现有"原有 9 项回归"即本文件原有全部用例,扩展后运行 13 项。)

- [ ] **Step 2: 运行闭环验证(需板卡+示波器)**

```bash
python scripts/scope_verify.py COM11
```
期望: 13 项全 PASS(原有 9 项 + 新增 4 项)。

- [ ] **Step 3: 提交**

```bash
git add scripts/scope_verify.py
git commit -m "test: scope_verify 调制闭环(AM 包络/FM/PM 边带/SWEEP 象限频率)"
```

---

## Task 15: 文档更新

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README.md**

- 功能列表追加:"内调制:每通道 AM/FM/PM/扫频(调制源正弦/方波/三角/锯齿 0.1Hz~50kHz;AM 深度 0~100%、FM 频偏 0~500kHz、PM 0~180°、扫频跨度=FMDEV)"
- 串口协议小节追加:`SET CH1 MODE FM | SET CH1 MODWAVE SQUARE | SET CH1 MODFREQ 100 | SET CH1 AMDEPTH 80 | SET CH1 FMDEV 10000 | SET CH1 PMDEV 64`
- 测试小节追加: mod_synth_tb/am_stage_tb;test_bridge 51 项;scope_verify 13 项
- 调试记录追加: 调制实现要点(FM/SWEEP 同一数据通路、PM 地址层注入、AM 增益 0~2×)

- [ ] **Step 2: CLAUDE.md**

- 目录结构:`src/rtl/` 追加 mod_synth/am_stage;命令集更新
- 新增小节"调制经验": 锁相 FM(fword_inst 同源注入)、PM 谐波偏移 ×ord、偶次翻转在偏移之后
- 测试速查更新(仿真模块数、协议/示波器用例数)

- [ ] **Step 3: 提交**

```bash
git add README.md CLAUDE.md
git commit -m "docs: 内调制功能文档(README/CLAUDE.md)"
```

---

## 自审清单(写完计划后对照执行)

1. **规格覆盖**: 规格 §2 架构(→Task 1/2/6)、§3 调制源(→Task 1)、§4 数学(→Task 1/3/4)、
   §5 寄存器/BD(→Task 5/7)、§6 协议(→Task 8/9)、§7 上位机(→Task 10-13)、
   §8 测试(→Task 1-4/9/12/14)、§9 构建哈希(→Task 7)、§11 顺序一致。
2. **占位符扫描**: 无 TBD/TODO;所有代码步骤给出完整代码。
3. **类型一致**: `mod_cmd`(Task 10 定义)→ controller(Task 11)引用;
   `make_plot_data(..., mod=)`(Task 12)→ WavePreview.set_params(Task 13)引用;
   `set_state(..., mode, mwave, mfreq_01, am_depth, fm_dev, pm_dev)`(Task 13)与
   `_on_state` 调用一致;wire 名 ch1_mode/ch1_mfreq/ch1_am_depth/ch1_fm_dev/ch1_pm_dev
   在 reg_bridge/awg_top/awg_top_tb 中一致;`pm_off`/`fword_inst1`/`am_gain1` 在
   mod_synth/awg_top/awg_top_tb 中一致;`MODE_NAME[5]` 在 main.c 中文件级可见。
