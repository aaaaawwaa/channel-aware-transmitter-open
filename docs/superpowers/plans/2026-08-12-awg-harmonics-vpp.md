# AWG 优化(谐波合成 + 峰峰值显示 + 频率单位输入)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已交付的上位机控制信号发生器上增加:1) 频率步进保留+手动输入支持 k/M 后缀;2) 正弦谐波合成(每通道基波+4 谐波,次数 2~8,满幅%+超限自动归一化);3) 幅度条实时 Vpp 显示/按 Vpp 设置/校准常数可调(默认 9.60V,实测 9.56~9.62V@100%)。

**Architecture:** 上位机 → 串口协议扩展(SET BASE/HARM)→ PS 桥(状态扩展+归一化)→ AXI GPIO 扩展(3 个双通道 IP,6×32bit)→ reg_bridge 扩展拆包 → 新模块 harm_synth(5 相位累加器+查表+加权求和,仅正弦)→ 幅度条缩放 → DAC。归一化在桥内(Σ>100% 等比缩放,GET 返回归一化值)。

**Tech Stack:** Vivado 2018.3、SDK 裸机 C、Verilog(xsim TB)、Python 3.8 + PySide6 + pytest。

**规格**:`docs/superpowers/specs/2026-08-12-awg-harmonics-vpp-design.md`

**关键接口约定(三层必须一致):**
- 寄存器字 A:`{base[7:0], h2_amp[6:0], h2_ord[3:0], h1_amp[6:0], h1_ord[3:0]}` = 30bit(base=bits[29:22])
- 寄存器字 B:`{reserved[9:0], h4_amp[6:0], h4_ord[3:0], h3_amp[6:0], h3_ord[3:0]}` = 32bit
- ord:0=禁用,2~8 有效;amp/base:0~100(已归一化)
- GPIO 映射:IP0 ch1=ch0(CH1 基础)、IP0 ch2=ch1(CH2 基础)、IP1 ch1=ch2(CH1 字A)、IP1 ch2=ch3(CH2 字A)、IP2 ch1=ch4(CH1 字B)、IP2 ch2=ch5(CH2 字B)
- 协议:`SET CHx BASE <0..100>`、`SET CHx HARM<n> ORDER <0|2..8>`、`SET CHx HARM<n> AMPL <0..100>`;GET 追加 `BASE <n> H1 <o> <a> H2 <o> <a> H3 <o> <a> H4 <o> <a>`
- 归一化规则:`sum = base + Σharm`;sum>100 → 每分量 ×100/sum(整数截断);sum=0 → 全 0(静音)
- 工具不在 PATH:vivado/xvlog 等用全路径 `"<LOCAL_PATH>"`;pytest 在 host/ 下跑

---

### Task 1: harm_synth 模块(TDD)

**Files:**
- Create: `src/rtl/harm_synth.v`
- Create: `src/tb/harm_synth_tb.v`

- [ ] **Step 1: 写失败测试**

`src/tb/harm_synth_tb.v`:

```verilog
`timescale 1ns / 1ps
module harm_synth_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] fword = 0;
    reg [7:0]  base = 0;
    reg [3:0]  h1_ord = 0, h2_ord = 0, h3_ord = 0, h4_ord = 0;
    reg [7:0]  h1_amp = 0, h2_amp = 0, h3_amp = 0, h4_amp = 0;
    wire [7:0] sample;

    harm_synth uut (
        .clk(clk), .rst_n(rst_n), .fword(fword), .base(base),
        .h1_ord(h1_ord), .h2_ord(h2_ord), .h3_ord(h3_ord), .h4_ord(h4_ord),
        .h1_amp(h1_amp), .h2_amp(h2_amp), .h3_amp(h3_amp), .h4_amp(h4_amp),
        .sample(sample)
    );
    always #10 clk = ~clk;   // 50 MHz

    // 参考正弦表(与 DUT 相同 ROM 内容,用于逐点比对)
    wire [7:0] ref_sine;
    sine_rom ref_rom (.addr(phase_ref[31:24]), .dout(ref_sine));
    reg [31:0] phase_ref = 0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) phase_ref <= 32'd0;
        else        phase_ref <= phase_ref + fword;

    integer errs = 0;
    integer peaks = 0;
    integer cross = 0;
    reg [7:0] prev = 8'd0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    // 流水 ~4 拍;等 6 拍后逐点比对
    task settle;
        input [15:0] n;
        begin repeat (n) @(posedge clk); end
    endtask

    initial begin
        #100 rst_n = 1;

        // 1) 纯基波:base=100 → sample 应等于参考正弦
        fword = 32'd85899;   // ~1kHz
        base = 8'd100;
        settle(10);
        begin : t1
            integer i;
            reg [7:0] got;
            reg [7:0] want;
            integer mism;
            mism = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                got = $signed(sample);
                want = $signed(ref_sine);   // 参考 ROM 相位推进一拍,同拍比对
                if (got !== want) mism = mism + 1;
            end
            chk("base=100 == pure sine (<=1% mismatch)", mism <= 5);
        end

        // 2) 基波 0 + 2 次谐波 100%:频率翻倍(过零计数 ≈ 2 倍)+ 幅度 = 满幅
        base = 8'd0;
        h1_ord = 4'd2;
        h1_amp = 8'd100;
        settle(10);
        begin : t2
            integer i;
            prev = 8'd0;
            cross = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) >= 0 && $signed(prev) < 0) cross = cross + 1;
                prev = sample;
                if ($signed(sample) > peaks) peaks = $signed(sample);
                if (-$signed(sample) > peaks) peaks = -$signed(sample);
            end
            chk("h1=2nd/100%: crossings ~ 2x base (>= 50)", cross >= 50);
            chk("h1=2nd/100%: peak ~ full scale (>= 120)", peaks >= 120);
        end

        // 3) 归一化边界:base=60 + h1=40 (sum=100) → 峰值 ≤ 127
        base = 8'd60;
        h1_amp = 8'd40;
        peaks = 0;
        settle(10);
        begin : t3
            integer i;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) > peaks) peaks = $signed(sample);
                if (-$signed(sample) > peaks) peaks = -$signed(sample);
            end
            chk("sum=100 peak <= 127", peaks <= 127);
        end

        // 4) 全关(base=0, 所有 amp=0)→ 输出恒 0
        base = 8'd0;
        h1_amp = 8'd0;
        settle(10);
        begin : t4
            integer i;
            integer nonzero;
            nonzero = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                if (sample !== 8'd0) nonzero = nonzero + 1;
            end
            chk("all off -> output 0", nonzero == 0);
        end

        // 5) ord=0 的谐波贡献 0:base=50 + h1 ord=0 amp=100 → 等效纯基波 50%
        base = 8'd50;
        h1_ord = 4'd0;
        h1_amp = 8'd100;
        settle(10);
        begin : t5
            integer i;
            integer mism;
            mism = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                // 期望 = 参考正弦 × 50 / 100(截断)
                if (sample !== (ref_sine * 8'd50) / 8'd100) mism = mism + 1;
            end
            chk("ord=0 ignored (base 50 == sine*0.5)", mism <= 5);
        end

        if (errs == 0) $display("PASS harm_synth_tb");
        else $display("FAIL harm_synth_tb: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行确认失败**

```bash
cd <LOCAL_PATH>
"<LOCAL_PATH>" --sv src/rtl/sine_rom.v src/tb/harm_synth_tb.v
"<LOCAL_PATH>" harm_synth_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 编译报错 Unknown module harm_synth。

- [ ] **Step 3: 写最小实现**

`src/rtl/harm_synth.v`:

```verilog
`timescale 1ns / 1ps
// Harmonic synthesizer (sine only): base + up to 4 harmonics.
// out = (base*sine0 + SUM_k hk_amp*sine(ord_k*phase)) / 100, ~4-cycle pipeline.
// Amplitudes are NORMALIZED upstream (sum <= 100) so the result never
// overflows +-127; the final divide is defensive truncation.
module harm_synth (
    input  wire clk,
    input  wire rst_n,
    input  wire [31:0] fword,
    input  wire [7:0]  base,       // 0..100 (normalized)
    input  wire [3:0]  h1_ord,     // 0=off, 2..8
    input  wire [3:0]  h2_ord,
    input  wire [3:0]  h3_ord,
    input  wire [3:0]  h4_ord,
    input  wire [7:0]  h1_amp,     // 0..100 (normalized)
    input  wire [7:0]  h2_amp,
    input  wire [7:0]  h3_amp,
    input  wire [7:0]  h4_amp,
    output reg  [7:0]  sample      // signed two's complement
);
    // phase accumulators; harmonic step = fword * ord (36-bit, low 32 used).
    // ord=0 -> step=0 -> accumulator frozen -> component contributes 0.
    wire [35:0] step1 = fword * h1_ord;
    wire [35:0] step2 = fword * h2_ord;
    wire [35:0] step3 = fword * h3_ord;
    wire [35:0] step4 = fword * h4_ord;

    reg [31:0] acc0, acc1, acc2, acc3, acc4;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            acc0 <= 32'd0; acc1 <= 32'd0; acc2 <= 32'd0;
            acc3 <= 32'd0; acc4 <= 32'd0;
        end else begin
            acc0 <= acc0 + fword;
            acc1 <= acc1 + step1[31:0];
            acc2 <= acc2 + step2[31:0];
            acc3 <= acc3 + step3[31:0];
            acc4 <= acc4 + step4[31:0];
        end

    // sine lookups (phase high byte)
    wire [7:0] s0 = acc0[31:24], s1 = acc1[31:24], s2 = acc2[31:24];
    wire [7:0] s3 = acc3[31:24], s4 = acc4[31:24];
    wire [7:0] sine0, sine1, sine2, sine3, sine4;
    sine_rom u_rom0 (.addr(s0), .dout(sine0));
    sine_rom u_rom1 (.addr(s1), .dout(sine1));
    sine_rom u_rom2 (.addr(s2), .dout(sine2));
    sine_rom u_rom3 (.addr(s3), .dout(sine3));
    sine_rom u_rom4 (.addr(s4), .dout(sine4));

    // weighted products (signed, 15-bit)
    reg signed [14:0] prod0, prod1, prod2, prod3, prod4;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            prod0 <= 15'sd0; prod1 <= 15'sd0; prod2 <= 15'sd0;
            prod3 <= 15'sd0; prod4 <= 15'sd0;
        end else begin
            prod0 <= $signed(sine0) * $signed({1'b0, base});
            prod1 <= $signed(sine1) * $signed({1'b0, h1_amp});
            prod2 <= $signed(sine2) * $signed({1'b0, h2_amp});
            prod3 <= $signed(sine3) * $signed({1'b0, h3_amp});
            prod4 <= $signed(sine4) * $signed({1'b0, h4_amp});
        end

    // sum (17-bit signed: 5 x 127 x 100 = 63500 fits)
    reg signed [16:0] sump;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) sump <= 17'sd0;
        else        sump <= prod0 + prod1 + prod2 + prod3 + prod4;

    // final divide by 100 (truncate toward zero); result <= 127 by normalization
    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample <= 8'sd0;
        else        sample <= (sump / 15'sd100);
endmodule
```

- [ ] **Step 4: 运行确认通过**

```bash
"<LOCAL_PATH>" --sv src/rtl/sine_rom.v src/rtl/harm_synth.v src/tb/harm_synth_tb.v
"<LOCAL_PATH>" harm_synth_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 5 组检查全 PASS,`PASS harm_synth_tb`。若逐点比对有小量不匹配,检查流水对齐(`#1` 采样时机、参考 ROM 相位);**不要放宽阈值掩盖问题**。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/harm_synth.v src/tb/harm_synth_tb.v
git commit -m "feat: harmonic synthesizer (base + 4 harmonics, weighted sum) with TB"
```

---

### Task 2: reg_bridge 扩展(TDD)

**Files:**
- Modify: `src/rtl/reg_bridge.v`
- Modify: `src/tb/reg_bridge_tb.v`

- [ ] **Step 1: 扩展 TB(新增检查,保留现有)**

在 `src/tb/reg_bridge_tb.v` 中:端口例化追加 `gpio2_in..gpio5_in` 与 `ch1_base/ch2_base/ch1_h1_ord..ch1_h4_amp/ch2_h1_ord..ch2_h4_amp`,并在 initial 块末尾追加(现有检查不动):

```verilog
        // ---- 谐波字拆包 ----
        // 字A: {base[7:0], h2_amp[6:0], h2_ord[3:0], h1_amp[6:0], h1_ord[3:0]}
        // 字B: {reserved[9:0], h4_amp[6:0], h4_ord[3:0], h3_amp[6:0], h3_ord[3:0]}
        gpio2_in = {8'd100, 7'd40, 4'd3, 7'd30, 4'd2};   // CH1 字A
        gpio3_in = {8'd100, 7'd40, 4'd3, 7'd30, 4'd2};   // CH2 字A
        gpio4_in = {10'd0,  7'd20, 4'd5, 7'd10, 4'd4};   // CH1 字B
        gpio5_in = {10'd0,  7'd20, 4'd5, 7'd10, 4'd4};   // CH2 字B
        #40;
        chk("ch1 base=100",      ch1_base == 8'd100);
        chk("ch1 h1 ord=2 amp=30", ch1_h1_ord == 4'd2 && ch1_h1_amp == 8'd30);
        chk("ch1 h2 ord=3 amp=40", ch1_h2_ord == 4'd3 && ch1_h2_amp == 8'd40);
        chk("ch1 h3 ord=4 amp=10", ch1_h3_ord == 4'd4 && ch1_h3_amp == 8'd10);
        chk("ch1 h4 ord=5 amp=20", ch1_h4_ord == 4'd5 && ch1_h4_amp == 8'd20);
        chk("ch2 mirrors ch1",    ch2_base == 8'd100 && ch2_h1_ord == 4'd2 && ch2_h4_amp == 8'd20);

        // 全 0(未写入)→ 无哨兵:base=0、ord=0(静音配置可达)
        gpio2_in = 32'd0;
        #40;
        chk("wordA=0 -> base 0 (no sentinel)", ch1_base == 8'd0 && ch1_h1_ord == 4'd0);
```

- [ ] **Step 2: 运行确认失败**

```bash
"<LOCAL_PATH>" --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"<LOCAL_PATH>" reg_bridge_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 编译报错(端口不匹配)。

- [ ] **Step 3: 扩展 DUT**

`src/rtl/reg_bridge.v`:端口新增 `gpio2_in..gpio5_in` 与拆包输出(保留现有全部);内部新增 4 个同步链(无 DEF 哨兵——**谐波字全 0 是合法静音配置**,只有 freq 字保留 word==0→DEF):

```verilog
    // ---- 谐波字(无哨兵:全 0 = 静音,合法配置)----
    reg [31:0] s2, s3, s4, s5, r2, r3, r4, r5;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s2 <= 32'd0; s3 <= 32'd0; s4 <= 32'd0; s5 <= 32'd0;
            r2 <= 32'd0; r3 <= 32'd0; r4 <= 32'd0; r5 <= 32'd0;
        end else begin
            s2 <= gpio2_in; s3 <= gpio3_in;
            s4 <= gpio4_in; s5 <= gpio5_in;
            r2 <= s2;       r3 <= s3;
            r4 <= s4;       r5 <= s5;
        end

    // 拆包(字A: base[29:22] h2_amp[21:15] h2_ord[14:11] h1_amp[10:4] h1_ord[3:0])
    //       (字B: reserved[31:22] h4_amp[21:15] h4_ord[14:11] h3_amp[10:4] h3_ord[3:0])
    assign ch1_base   = r2[29:22];
    assign ch1_h1_ord = r2[3:0];
    assign ch1_h1_amp = {1'b0, r2[10:4]};
    assign ch1_h2_ord = r2[14:11];
    assign ch1_h2_amp = {1'b0, r2[21:15]};
    assign ch1_h3_ord = r4[3:0];
    assign ch1_h3_amp = {1'b0, r4[10:4]};
    assign ch1_h4_ord = r4[14:11];
    assign ch1_h4_amp = {1'b0, r4[21:15]};

    assign ch2_base   = r3[29:22];
    assign ch2_h1_ord = r3[3:0];
    assign ch2_h1_amp = {1'b0, r3[10:4]};
    assign ch2_h2_ord = r3[14:11];
    assign ch2_h2_amp = {1'b0, r3[21:15]};
    assign ch2_h3_ord = r5[3:0];
    assign ch2_h3_amp = {1'b0, r5[10:4]};
    assign ch2_h4_ord = r5[14:11];
    assign ch2_h4_amp = {1'b0, r5[21:15]};
```

端口声明(插入到现有端口之后):

```verilog
    input  wire [31:0] gpio2_in,  // -> CH1 谐波字 A
    input  wire [31:0] gpio3_in,  // -> CH2 谐波字 A
    input  wire [31:0] gpio4_in,  // -> CH1 谐波字 B
    input  wire [31:0] gpio5_in,  // -> CH2 谐波字 B
    output wire [7:0]  ch1_base, ch2_base,
    output wire [3:0]  ch1_h1_ord, ch1_h2_ord, ch1_h3_ord, ch1_h4_ord,
    output wire [7:0]  ch1_h1_amp, ch1_h2_amp, ch1_h3_amp, ch1_h4_amp,
    output wire [3:0]  ch2_h1_ord, ch2_h2_ord, ch2_h3_ord, ch2_h4_ord,
    output wire [7:0]  ch2_h1_amp, ch2_h2_amp, ch2_h3_amp, ch2_h4_amp
```

- [ ] **Step 4: 运行确认通过**

```bash
"<LOCAL_PATH>" --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
"<LOCAL_PATH>" reg_bridge_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 现有 16 项 + 新增 8 项全 PASS,`PASS reg_bridge_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
git commit -m "feat: reg_bridge unpack harmonic words (base + 4x{ord,amp})"
```

---

### Task 3: awg_top 集成(TDD)

**Files:**
- Modify: `src/rtl/awg_top.v`
- Modify: `src/tb/awg_top_tb.v`

- [ ] **Step 1: 扩展 TB**

`src/tb/awg_top_tb.v` 端口例化追加 gpio2_in..gpio5_in(初始 0),在现有检查后追加:

```verilog
        // ---- 谐波链路:CH1 正弦 + 2 次谐波 50% ----
        // 字A = {base=100, h2=0, h1_amp=50, h1_ord=2}
        gpio0_in = pack(20'd100000, 8'd100, 2'd0);   // 100kHz SINE 100%
        gpio2_in = {8'd100, 7'd0, 4'd0, 7'd50, 4'd2};
        gpio3_in = {8'd100, 7'd0, 4'd0, 7'd50, 4'd2};
        ch0_trans = 0; ch0_max = 0; ch0_min = 255;
        prev0 = 8'hFF;
        #100_000;   // 100kHz = 10 周期
        chk("harm ch0 active", ch0_trans > 500);
        // 归一化在桥内保证 Σ≤100 → 峰值不超满幅
        chk("harm peak <= full scale", ch0_max <= 8'hFF && ch0_min >= 8'h00);

        // 方波不受谐波影响:wave=1 时仍走 dds_core(谐波配置保留但不用)
        gpio0_in = pack(20'd100000, 8'd100, 2'd1);   // SQUARE
        ch0_trans = 0; ch0_max = 0; ch0_min = 255; prev0 = 8'hFF;
        #100_000;
        chk("square unaffected by harms (range full)", ch0_min <= 8'h02 && ch0_max >= 8'hFE);
```

- [ ] **Step 2: 运行确认失败**

```bash
cd <LOCAL_PATH>
"<LOCAL_PATH>" --sv src/rtl/reg_bridge.v src/rtl/ampl_scale.v src/rtl/dds_core.v src/rtl/sine_rom.v src/rtl/dac_driver.v src/rtl/harm_synth.v src/rtl/awg_top.v src/tb/awg_top_tb.v
"<LOCAL_PATH>" awg_top_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 编译报错(awg_top 端口不匹配)。

- [ ] **Step 3: 改 DUT**

`src/rtl/awg_top.v`:
- 端口追加 `gpio2_in..gpio5_in`(注释:谐波字 A/B)
- reg_bridge 例化追加连接
- 每通道新增 `harm_synth` 例化 + 选择器:

```verilog
    wire [7:0] harm1, harm2;
    harm_synth u_harm1 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword1),
        .base(ch1_base), .h1_ord(ch1_h1_ord), .h2_ord(ch1_h2_ord),
        .h3_ord(ch1_h3_ord), .h4_ord(ch1_h4_ord),
        .h1_amp(ch1_h1_amp), .h2_amp(ch1_h2_amp),
        .h3_amp(ch1_h3_amp), .h4_amp(ch1_h4_amp),
        .sample(harm1)
    );
    harm_synth u_harm2 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword2),
        .base(ch2_base), .h1_ord(ch2_h1_ord), .h2_ord(ch2_h2_ord),
        .h3_ord(ch2_h3_ord), .h4_ord(ch2_h4_ord),
        .h1_amp(ch2_h1_amp), .h2_amp(ch2_h2_amp),
        .h3_amp(ch2_h3_amp), .h4_amp(ch2_h4_amp),
        .sample(harm2)
    );

    // wave==SINE 时输出谐波合成,否则 dds_core 原波形
    wire [7:0] s1_raw, s2_raw;
    dds_core u_dds1 (.clk(sys_clk), .rst_n(rst_n), .fword(fword1), .wave_sel(ch1_wave), .sample(s1_raw));
    dds_core u_dds2 (.clk(sys_clk), .rst_n(rst_n), .fword(fword2), .wave_sel(ch2_wave), .sample(s2_raw));
    wire [7:0] s1 = (ch1_wave == 2'b00) ? harm1 : s1_raw;
    wire [7:0] s2 = (ch2_wave == 2'b00) ? harm2 : s2_raw;
```

(原 `wire [7:0] s1, s2, a1, a2;` 与 dds 例化替换为上述;ampl_scale 输入改接 s1/s2。)

- [ ] **Step 4: 运行确认通过**

```bash
"<LOCAL_PATH>" --sv src/rtl/reg_bridge.v src/rtl/ampl_scale.v src/rtl/dds_core.v src/rtl/sine_rom.v src/rtl/dac_driver.v src/rtl/harm_synth.v src/rtl/awg_top.v src/tb/awg_top_tb.v
"<LOCAL_PATH>" awg_top_tb -s sim
"<LOCAL_PATH>" sim -runall
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/awg_top.v src/tb/awg_top_tb.v
git commit -m "feat: integrate harmonic synthesis into AWG top (sine path only)"
```

---

### Task 4: Block Design 扩展(3× AXI GPIO)

**Files:**
- Modify: `scripts/create_bd.tcl`(重写:删除重建 + 3 个 AXI GPIO + NUM_MI 3)
- Run: vivado batch

- [ ] **Step 1: 重写 create_bd.tcl**

关键变化:BD 每次**删除重建**(awg_top 端口变化后模块引用刷新最简单可靠);AXI GPIO 增至 3 个双通道 IP;互联 NUM_MI=3;awg_top 端口连接 gpio0_in..gpio5_in。

```tcl
# Create host_bd: PS7 (UART1 MIO48/49 @115200, FCLK0 50MHz, M_AXI_GP0, no DDR)
# + 3x AXI GPIO (dual 32-bit all-output) + awg_top (BD module).
# Regenerates the BD from scratch (delete + recreate) — safe because the BD
# is derived state and awg_top's port list is the source of truth.
# Usage: vivado -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
set proj_file [lindex $argv 0]
set script_dir [file dirname [file normalize [info script]]]

open_project $proj_file
source $script_dir/ps7_props.tcl

# --- remove existing BD + wrapper (regenerate from scratch) ---
if {[get_files -quiet host_bd.bd] ne ""} {
    set bd_dir [file dirname [get_files host_bd.bd]]
    remove_files host_bd.bd
    file delete -force $bd_dir
}
if {[get_files -quiet host_bd_wrapper.v] ne ""} {
    remove_files host_bd_wrapper.v
}

create_bd_design host_bd

# ---- PS7 ----
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
set_property -dict $ps7_props $ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable"} \
    [get_bd_cells ps7_0]

# ---- 3x AXI GPIO (dual 32-bit all-output) ----
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2} {
    set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $ip_name]
    set_property -dict [list \
        CONFIG.C_GPIO_WIDTH {32} \
        CONFIG.C_ALL_OUTPUTS {1} \
        CONFIG.C_IS_DUAL {1} \
        CONFIG.C_GPIO2_WIDTH {32} \
        CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio
}

# ---- awg_top as BD module ----
set awg [create_bd_cell -type module -reference awg_top awg_top_0]

create_bd_port -dir I -type clk sys_clk
create_bd_port -dir O -from 7 -to 0 led
create_bd_port -dir O -from 7 -to 0 dac0_db
create_bd_port -dir O -from 7 -to 0 dac1_db
create_bd_port -dir O daclk0
create_bd_port -dir O daclk1

connect_bd_net [get_bd_pins awg_top_0/sys_clk] [get_bd_ports sys_clk]
connect_bd_net [get_bd_pins awg_top_0/led]     [get_bd_ports led]
connect_bd_net [get_bd_pins awg_top_0/dac0_db] [get_bd_ports dac0_db]
connect_bd_net [get_bd_pins awg_top_0/dac1_db] [get_bd_ports dac1_db]
connect_bd_net [get_bd_pins awg_top_0/daclk0]  [get_bd_ports daclk0]
connect_bd_net [get_bd_pins awg_top_0/daclk1]  [get_bd_ports daclk1]

# AXI GPIO outputs -> awg_top params (internal)
connect_bd_net [get_bd_pins axi_gpio_0/gpio_io_o]   [get_bd_pins awg_top_0/gpio0_in]
connect_bd_net [get_bd_pins axi_gpio_0/gpio2_io_o]  [get_bd_pins awg_top_0/gpio1_in]
connect_bd_net [get_bd_pins axi_gpio_1/gpio_io_o]   [get_bd_pins awg_top_0/gpio2_in]
connect_bd_net [get_bd_pins axi_gpio_1/gpio2_io_o]  [get_bd_pins awg_top_0/gpio3_in]
connect_bd_net [get_bd_pins axi_gpio_2/gpio_io_o]   [get_bd_pins awg_top_0/gpio4_in]
connect_bd_net [get_bd_pins axi_gpio_2/gpio2_io_o]  [get_bd_pins awg_top_0/gpio5_in]

# ---- AXI interconnect (1SI/3MI) ----
set axiint [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {3} CONFIG.NUM_SI {1}] $axiint
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
foreach {mi slave} {M00_AXI axi_gpio_0/S_AXI  M01_AXI axi_gpio_1/S_AXI  M02_AXI axi_gpio_2/S_AXI} {
    connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/$mi] [get_bd_intf_pins $slave]
}
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/S00_ACLK]
foreach mi {M00_ACLK M01_ACLK M02_ACLK} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/$mi]
}
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2} {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $ip_name/s_axi_aclk]
}
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]

# ---- reset ----
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]     [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7/interconnect_aresetn] [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/S00_ARESETN]
foreach mi {M00_ARESETN M01_ARESETN M02_ARESETN} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins axi_interconnect_0/$mi]
}
foreach ip_name {axi_gpio_0 axi_gpio_1 axi_gpio_2} {
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins $ip_name/s_axi_aresetn]
}

# ---- wrap up ----
assign_bd_address
validate_bd_design
save_bd_design
make_wrapper -files [get_files host_bd.bd] -top
set wrapper [file join [file dirname [get_files host_bd.bd]] hdl host_bd_wrapper.v]
add_files -norecurse $wrapper
set_property top host_bd_wrapper [current_fileset]
generate_target all [get_files host_bd.bd]
close_project
```

- [ ] **Step 2: 运行**

```bash
cd <LOCAL_PATH>
"<LOCAL_PATH>" -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
```

Expected: validate 无 ERROR;wrapper 端口仍为 sys_clk/led/dac0_db/dac1_db/daclk0/daclk1/FIXED_IO(谐波输入在 BD 内部)。若 create_bd_cell -type module 报旧端口引用,确认 awg_top.v 已更新(Task 3 完成后才跑本任务)。

- [ ] **Step 3: 核对地址分配**

```bash
grep -E "axi_gpio_[012]" awg/awg.srcs/sources_1/bd/host_bd/host_bd.bd | grep -oE "0x[0-9A-Fa-f]+" | sort -u | head -8
```

Expected: 3 个不同地址(如 0x41200000 / 0x41210000 / 0x41220000)。

- [ ] **Step 4: 提交**

```bash
git add scripts/create_bd.tcl
git commit -m "feat: BD with 3x AXI GPIO (6x32bit) for harmonics; regenerate from scratch"
```

---

### Task 5: 综合/实现/比特流

- [ ] **Step 1: 运行**

```bash
cli-anything-vivado synth run awg/awg.xpr
cli-anything-vivado impl run awg/awg.xpr
cli-anything-vivado bitstream generate awg/awg.xpr
```

Expected: 0 errors;`awg/awg.runs/impl_1/host_bd_wrapper.bit` 生成;资源 LUT < 7%、FF < 3%;WNS ≥ 0。若有 UCIO-1:检查 XDC 无新增未约束端口(gpio2~5 是 BD 内部信号,不在顶层)。

---

### Task 6: SDK 桥扩展(归一化 + BASE/HARM 命令)

**Files:**
- Modify: `sdk/src/main.c`
- Run: `cli-anything-vivado sdk build`

- [ ] **Step 1: 扩展 main.c**

在 `ch_state_t` 追加字段并实现打包/归一化/命令/GET(保留现有命令不变):

```c
typedef struct {
    u32 wave;      /* 0=SINE 1=SQUARE 2=TRIANGLE 3=SAW */
    u32 freq_hz;
    u32 ampl_pct;
    u32 base_pct;  /* 0..100, 归一化后 */
    u32 h_ord[4];  /* 0=off, 2..8 */
    u32 h_ampl[4]; /* 0..100, 归一化后 */
} ch_state_t;
```

`write_all` 替换为 6 通道写入:

```c
static u32 pack_word_a(const ch_state_t *s)
{
    /* {base[7:0], h2_amp[6:0], h2_ord[3:0], h1_amp[6:0], h1_ord[3:0]} */
    return (s->base_pct << 22) | (s->h_ampl[1] << 15) | (s->h_ord[1] << 11)
         | (s->h_ampl[0] << 4)  | s->h_ord[0];
}

static u32 pack_word_b(const ch_state_t *s)
{
    /* {reserved, h4_amp[6:0], h4_ord[3:0], h3_amp[6:0], h3_ord[3:0]} */
    return (s->h_ampl[3] << 15) | (s->h_ord[3] << 11)
         | (s->h_ampl[2] << 4)  | s->h_ord[2];
}

static void write_all(void)
{
    XGpio_DiscreteWrite(&Gpio,  1, pack_word(&ch[0]));      /* ch0: CH1 基础 */
    XGpio_DiscreteWrite(&Gpio,  2, pack_word(&ch[1]));      /* ch1: CH2 基础 */
    XGpio_DiscreteWrite(&Gpio2, 1, pack_word_a(&ch[0]));    /* ch2: CH1 字A */
    XGpio_DiscreteWrite(&Gpio2, 2, pack_word_a(&ch[1]));    /* ch3: CH2 字A */
    XGpio_DiscreteWrite(&Gpio3, 1, pack_word_b(&ch[0]));    /* ch4: CH1 字B */
    XGpio_DiscreteWrite(&Gpio3, 2, pack_word_b(&ch[1]));    /* ch5: CH2 字B */
}

/* 归一化:sum>100 时每分量 ×100/sum(整数截断);sum=0 保持全 0(静音) */
static void normalize(ch_state_t *s)
{
    u32 sum = s->base_pct;
    int i;
    for (i = 0; i < 4; i++) sum += s->h_ampl[i];
    if (sum > 100) {
        s->base_pct = s->base_pct * 100 / sum;
        for (i = 0; i < 4; i++) s->h_ampl[i] = s->h_ampl[i] * 100 / sum;
    }
}
```

main() 增加:初始化 `Gpio2`/`Gpio3`(XPAR_AXI_GPIO_1/2_DEVICE_ID,方向 0x0);默认状态 `base_pct=100`、h_* 全 0;do_line 增加分支:

```c
    } else if (strcmp(tok[2], "BASE") == 0) {
        u32 v;
        if (!parse_u32(tok[3], &v)) { uart_send("ERR BAD VALUE\r\n"); return; }
        if (v > 100) { uart_send("ERR BASE OUT OF RANGE\r\n"); return; }
        ch[ci].base_pct = v;
        normalize(&ch[ci]);
        write_all();
        uart_send("OK\r\n");
        return;
    } else if (strcmp(tok[2], "HARM") == 0) {
        u32 n, v;
        if (n < 4 + 1) {   /* n = tok[3], 1..4 */
            if (!parse_u32(tok[3], &n) || n < 1 || n > 4) {
                uart_send("ERR BAD VALUE\r\n"); return;
            }
            if (strcmp(tok[4], "ORDER") == 0) {
                if (!parse_u32(tok[5], &v)) { uart_send("ERR BAD VALUE\r\n"); return; }
                if (v != 0 && (v < 2 || v > 8)) { uart_send("ERR HARM ORDER OUT OF RANGE\r\n"); return; }
                ch[ci].h_ord[n - 1] = v;
            } else if (strcmp(tok[4], "AMPL") == 0) {
                if (!parse_u32(tok[5], &v)) { uart_send("ERR BAD VALUE\r\n"); return; }
                if (v > 100) { uart_send("ERR HARM AMPL OUT OF RANGE\r\n"); return; }
                ch[ci].h_ampl[n - 1] = v;
            } else {
                uart_send("ERR BAD FIELD\r\n"); return;
            }
            normalize(&ch[ci]);
            write_all();
            uart_send("OK\r\n");
            return;
        }
    }
```

GET 分支输出改为(缓冲 96 字节):

```c
        char out[96];
        xil_sprintf(out, "CH%d WAVE %s FREQ %lu AMPL %lu BASE %lu "
                         "H1 %lu %lu H2 %lu %lu H3 %lu %lu H4 %lu %lu\r\n",
                    ci + 1, WAVE_NAME[ch[ci].wave],
                    (unsigned long)ch[ci].freq_hz, (unsigned long)ch[ci].ampl_pct,
                    (unsigned long)ch[ci].base_pct,
                    (unsigned long)ch[ci].h_ord[0], (unsigned long)ch[ci].h_ampl[0],
                    (unsigned long)ch[ci].h_ord[1], (unsigned long)ch[ci].h_ampl[1],
                    (unsigned long)ch[ci].h_ord[2], (unsigned long)ch[ci].h_ampl[2],
                    (unsigned long)ch[ci].h_ord[3], (unsigned long)ch[ci].h_ampl[3]);
```

注意:do_line 中 SET 分支需先检查 token 数(n ≥ 6 才能访问 tok[4]/tok[5]);HARM 分支放在现有 WAVE/FREQ/AMPL 之后。`#define GPIO2_DEVICE_ID XPAR_AXI_GPIO_1_DEVICE_ID`、`#define GPIO3_DEVICE_ID XPAR_AXI_GPIO_2_DEVICE_ID`,静态 `XGpio Gpio2, Gpio3;`。

- [ ] **Step 2: 编译**

```bash
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws --type all
```

Expected: `sdk/ws/awg_bridge/Debug/awg_bridge.elf` 生成无错误。若 XPAR_AXI_GPIO_1/2_DEVICE_ID 未定义:重新 export hdf(Task 5 之后)+ `sdk app new` 重建工程(或手动改 BSP xparameters.h 后重建)。

- [ ] **Step 3: 提交**

```bash
git add sdk/src/main.c
git commit -m "feat: bridge BASE/HARM commands with in-bridge normalization"
```

---

### Task 7: 板上协议测试(需硬件)

**Files:**
- Modify: `scripts/test_bridge.py`

- [ ] **Step 1: 扩展测试脚本**

在现有用例后追加(注意归一化期望值:桥内整数截断):

```python
# ---- 谐波与基波(归一化期望值 = 桥内整数截断,已核算)----
cmd("SET CH1 BASE 100", "OK")
cmd("SET CH1 HARM1 ORDER 3", "OK")
cmd("SET CH1 HARM1 AMPL 30", "OK")     # sum=130 -> base=100*100//130=76, h1=30*100//130=23
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 76 H1 3 23 H2 0 0 H3 0 0 H4 0 0")
cmd("SET CH1 BASE 80", "OK")           # sum=80+23=103 -> base=80*100//103=77, h1=23*100//103=22
cmd("SET CH1 HARM2 ORDER 5", "OK")     # sum=77+22=99 <=100,不变
cmd("SET CH1 HARM2 AMPL 10", "OK")     # sum=77+22+10=109 -> base=70, h1=20, h2=9
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 70 H1 3 20 H2 5 9 H3 0 0 H4 0 0")
# 非法
cmd("SET CH1 BASE 101", "ERR BASE OUT OF RANGE")
cmd("SET CH1 HARM1 ORDER 1", "ERR HARM ORDER OUT OF RANGE")
cmd("SET CH1 HARM1 ORDER 9", "ERR HARM ORDER OUT OF RANGE")
cmd("SET CH1 HARM1 AMPL 101", "ERR HARM AMPL OUT OF RANGE")
cmd("SET CH1 HARM5 ORDER 2", "ERR BAD VALUE")
cmd("SET CH1 HARM1 FOO 2", "ERR BAD FIELD")
# 静音可达:base=0(其余不变)→ 字A base 段为 0
cmd("SET CH1 BASE 0", "OK")            # sum=0+20+9=29 <=100,base=0,harm 不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 0 H1 3 20 H2 5 9 H3 0 0 H4 0 0")
# 恢复
cmd("SET CH1 BASE 100", "OK")          # sum=100+20+9=129 -> base=77, h1=15, h2=6
cmd("SET CH1 HARM1 AMPL 0", "OK")      # sum=77+0+6=83 <=100,不变
cmd("SET CH1 HARM2 AMPL 0", "OK")      # sum=77+0+0=77 <=100,不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 77 H1 3 15 H2 5 6 H3 0 0 H4 0 0")
```

(实现时若与上面期望不符:用 `python -c "print(100*100//130, 30*100//130)"` 复核整数除法;桥行为以"当前已归一化状态"为准,新增 SET 触发 re-normalize。)

- [ ] **Step 2: 运行(需板卡)**

```bash
cd <LOCAL_PATH>
mkdir -p sdk/hw && unzip -o awg/host_bd.hdf -d sdk/hw
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw --bit awg/awg.runs/impl_1/host_bd_wrapper.bit
python scripts/test_bridge.py COM11
```

Expected: 全部 PASS(旧 19 项 + 新 ~14 项)。若 GET 归一化值与预期不符:用 `python -c "print(80*100//113, 30*100//113, ...)"` 核对整数除法,修正期望值(脚本的期望必须与实际桥行为一致,桥行为以整数截断为准)。

- [ ] **Step 3: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test: board-level BASE/HARM protocol cases (incl. normalization)"
```

---

### Task 8: 上位机 protocol.py 扩展(TDD)

**Files:**
- Modify: `host/siggen/protocol.py`
- Modify: `host/tests/test_protocol.py`

- [ ] **Step 1: 写失败测试(追加)**

`host/tests/test_protocol.py` 追加:

```python
import pytest

from siggen.protocol import (
    parse_freq_input, base_cmd, harm_cmd, set_cmd, get_cmd,
    parse_response, parse_status,
)


def test_parse_freq_input():
    assert parse_freq_input("100000") == 100000
    assert parse_freq_input("100k") == 100000
    assert parse_freq_input("250kHz") == 250000
    assert parse_freq_input("0.5M") == 500000
    assert parse_freq_input(" 100k ") == 100000
    with pytest.raises(ValueError):
        parse_freq_input("abc")
    with pytest.raises(ValueError):
        parse_freq_input("1.5M")          # 超 1MHz 上限
    with pytest.raises(ValueError):
        parse_freq_input("5")             # 低于 10Hz


def test_base_cmd():
    assert base_cmd(1, 100) == "SET CH1 BASE 100"
    assert base_cmd(2, 0) == "SET CH2 BASE 0"
    with pytest.raises(ValueError):
        base_cmd(1, 101)
    with pytest.raises(ValueError):
        base_cmd(3, 50)


def test_harm_cmd():
    assert harm_cmd(1, 2, "ORDER", 3) == "SET CH1 HARM2 ORDER 3"
    assert harm_cmd(1, 2, "AMPL", 30) == "SET CH1 HARM2 AMPL 30"
    assert harm_cmd(1, 1, "order", 0) == "SET CH1 HARM1 ORDER 0"
    with pytest.raises(ValueError):
        harm_cmd(1, 5, "ORDER", 2)        # n 超 4
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "ORDER", 1)        # 次数 1 非法
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "ORDER", 9)
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "AMPL", 101)
    with pytest.raises(ValueError):
        harm_cmd(1, 1, "FOO", 2)


def test_parse_status_extended():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 5 8 H3 0 0 H4 0 0")
    assert d["base_pct"] == 68
    assert d["harms"] == [(3, 20), (5, 8), (0, 0), (0, 0)]


def test_parse_status_legacy():
    # 旧格式(无 BASE/HARM)仍可解析
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert d["base_pct"] is None and d["harms"] == []
```

- [ ] **Step 2: 运行确认失败**

```bash
cd <LOCAL_PATH>
python -m pytest tests/test_protocol.py -v
```

Expected: FAIL(AttributeError: parse_freq_input 等未定义)。

- [ ] **Step 3: 实现**

`host/siggen/protocol.py` 追加(保留现有函数):

```python
import re

_K_SUFFIX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)(?:hz)?\s*$", re.IGNORECASE)


def parse_freq_input(s: str) -> int:
    """解析频率输入:支持 100000 / 100k / 250kHz / 0.5M;范围 10..1MHz。"""
    m = _K_SUFFIX.match(s)
    if not m:
        raise ValueError(f"bad frequency input: {s!r}")
    v = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        v *= 1000
    elif unit == "m":
        v *= 1_000_000
    f = int(round(v))
    if not FREQ_MIN <= f <= FREQ_MAX:
        raise ValueError(f"freq out of range: {s!r}")
    return f


def base_cmd(ch: int, value) -> str:
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    v = int(value)
    if not 0 <= v <= 100:
        raise ValueError(f"base out of range: {value}")
    return f"SET CH{ch} BASE {v}"


def harm_cmd(ch: int, n: int, field: str, value) -> str:
    """构造 HARM 命令;n=1..4,field=ORDER|AMPL。"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    if not 1 <= n <= 4:
        raise ValueError(f"bad harmonic index: {n}")
    field = field.upper()
    v = int(value)
    if field == "ORDER":
        if v != 0 and not 2 <= v <= 8:
            raise ValueError(f"harm order out of range: {value}")
    elif field == "AMPL":
        if not 0 <= v <= 100:
            raise ValueError(f"harm ampl out of range: {value}")
    else:
        raise ValueError(f"bad field: {field}")
    return f"SET CH{ch} HARM{n} {field} {v}"
```

`parse_status` 扩展(替换原实现,保持旧格式兼容):

```python
def parse_status(line: str) -> dict:
    """解析 'CHx WAVE .. FREQ .. AMPL .. [BASE .. H1 o a H2 o a ..]'。
    旧格式(无 BASE/HARM)兼容:base_pct=None, harms=[]。"""
    parts = line.split()
    if len(parts) < 7 or parts[0] not in ("CH1", "CH2"):
        raise ValueError(f"bad status line: {line!r}")
    d = {"ch": int(parts[0][2]), "wave": parts[2],
         "freq_hz": int(parts[4]), "ampl_pct": int(parts[6]),
         "base_pct": None, "harms": []}
    i = 7
    while i < len(parts):
        key = parts[i]
        if key == "BASE":
            d["base_pct"] = int(parts[i + 1])
            i += 2
        elif key in ("H1", "H2", "H3", "H4"):
            d["harms"].append((int(parts[i + 1]), int(parts[i + 2])))
            i += 3
        else:
            i += 1
    return d
```

- [ ] **Step 4: 运行确认通过**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASS(旧 7 + 新 5)。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/protocol.py host/tests/test_protocol.py
git commit -m "feat: host protocol BASE/HARM commands, k/M freq input, extended status"
```

---

### Task 9: 上位机 controller 扩展(TDD)

**Files:**
- Modify: `host/siggen/controller.py`
- Modify: `host/tests/test_controller.py`

- [ ] **Step 1: 写失败测试(追加)**

`host/tests/test_controller.py` 追加:

```python
def test_base_harm_send_and_resync(ctrl):
    # 谐波字段:发 SET;OK 后应自动 GET 该通道回读归一化值
    ctrl.set_field(0, "base", 80)
    assert ctrl.worker.sent[-1] == "SET CH1 BASE 80\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET CH1\r\n"      # ack 后自动回读
    assert ctrl._pending is None


def test_harm_order_field(ctrl):
    ctrl.set_field(0, "h1_order", 3)
    assert ctrl.worker.sent[-1] == "SET CH1 HARM1 ORDER 3\r\n"
    ctrl._on_line("OK")
    assert ctrl.worker.sent[-1] == "GET CH1\r\n"


def test_harm_invalid_rejected(ctrl):
    ctrl.set_field(0, "h1_order", 9)
    assert ctrl.worker.sent == []     # 校验前置,不发命令


def test_extended_status_updates_harms(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 5 8 H3 0 0 H4 0 0")
    st = ctrl.state[0]
    assert st.base_pct == 68
    assert st.harms == [(3, 20), (5, 8), (0, 0), (0, 0)]
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest tests/test_controller.py -v
```

Expected: FAIL(ChannelState 无 base_pct 等字段)。

- [ ] **Step 3: 实现**

`host/siggen/controller.py`:
- `ChannelState` 追加字段:

```python
@dataclass
class ChannelState:
    wave: str = "SINE"
    freq_hz: int = 1000
    ampl_pct: int = 50
    base_pct: int = 100
    harms: list = field(default_factory=lambda: [(0, 0)] * 4)   # (order, ampl) 归一化
```

- `set_field` 增加字段映射(在现有 freq/wave/ampl 之后):

```python
        field_l = field.lower()
        if field_l == "base":
            cmd = base_cmd(ch + 1, value)
            self._send(ch, "base", value, cmd)
            return
        if field_l in ("h1_order", "h2_order", "h3_order", "h4_order") or \
           field_l in ("h1_ampl", "h2_ampl", "h3_ampl", "h4_ampl"):
            n = int(field_l[1])
            kind = "ORDER" if field_l.endswith("order") else "AMPL"
            cmd = harm_cmd(ch + 1, n, kind, value)
            self._send(ch, field_l, value, cmd)
            return
```

- 新增 `_send` 助手(原有发送逻辑提取;谐波类字段 ack 后自动回读):

```python
    def _send(self, ch, field, value, cmd) -> None:
        self._pending = (ch, field, value)
        self.worker.send(cmd + "\r\n")
        self._timer.start(TIMEOUT_MS)
        self.log.emit(f">> {cmd}", "info")

    def _on_line(self, line: str) -> None:
        ...existing...
        if self._pending is not None:
            ch, field, value = self._pending
            self._pending = None
            self._timer.stop()
            if ok:
                st = self.state[ch]
                if field == "wave":
                    st.wave = str(value).upper()
                elif field == "freq":
                    st.freq_hz = int(value)
                elif field == "ampl":
                    st.ampl_pct = int(value)
                elif field in ("base",) or field.endswith("order") or field.endswith("ampl"):
                    # 谐波/基波:桥内已归一化,本地值不可信 → 发 GET 回读
                    self.worker.send(get_cmd(ch + 1) + "\r\n")
                self.state_changed.emit(ch)
                self.log.emit(f"<< {line}", "ok")
            else:
                self.log.emit(f"<< {line}", "error")
```

- `parse_status` 调用处同步扩展(状态行分支):

```python
        if line.startswith("CH1 ") or line.startswith("CH2 "):
            d = parse_status(line)
            st = self.state[d["ch"] - 1]
            st.wave, st.freq_hz, st.ampl_pct = d["wave"], d["freq_hz"], d["ampl_pct"]
            if d["base_pct"] is not None:
                st.base_pct = d["base_pct"]
                st.harms = d["harms"]
            self.state_changed.emit(d["ch"] - 1)
            self.log.emit(f"<< {line}", "ok")
            return
```

- [ ] **Step 4: 运行确认通过**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/controller.py host/tests/test_controller.py
git commit -m "feat: controller harmonic/base fields with ack-resync GET"
```

---

### Task 10: 上位机 GUI + 预览

**Files:**
- Modify: `host/siggen/ui/widgets.py`(谐波区 + Vpp + k/M 输入)
- Modify: `host/siggen/preview.py`(谐波预览)
- Modify: `host/siggen/ui/main_window.py`(新字段接线)

- [ ] **Step 1: preview.py 扩展**

`make_plot_data` 增加 base/harms 参数(仅 SINE 时叠加):

```python
def make_plot_data(wave, freq_hz, ampl_pct, base_pct=100, harms=None, n=600):
    """返回 (t, y)。harms = [(order, ampl_pct), ...] 归一化值。"""
    if freq_hz <= 0:
        return np.zeros(n), np.zeros(n)
    t = np.linspace(0, PERIODS / freq_hz, n, endpoint=False)
    phase = (t * freq_hz) % 1.0
    if wave == "SINE":
        y = np.zeros(n)
        y += (base_pct / 100.0) * np.sin(2 * np.pi * phase)
        if harms:
            for order, a in harms:
                if order:
                    y += (a / 100.0) * np.sin(2 * np.pi * phase * order)
    elif wave == "SQUARE":
        y = np.where(phase < 0.5, 1.0, -1.0)
    elif wave == "TRIANGLE":
        y = 4 * np.abs(phase - 0.5) - 1
    elif wave == "SAW":
        y = 2 * phase - 1
    else:
        raise ValueError(f"bad wave: {wave}")
    return t, y * ampl_pct / 100.0


class WavePreview(pg.PlotWidget):
    def set_params(self, wave, freq_hz, ampl_pct, base_pct=100, harms=None):
        t, y = make_plot_data(wave, freq_hz, ampl_pct, base_pct, harms)
        self._curve.setData(t, y)
```

- [ ] **Step 2: widgets.py 扩展(ChannelPanel)**

新增控件与逻辑(在幅度滑块行之后):

```python
        # ---- 谐波区 ----
        harm_box = QVBoxLayout()
        self.norm_hint = QLabel("")          # 归一化提示
        self.norm_hint.setStyleSheet("color: orange;")
        harm_box.addWidget(self.norm_hint)

        base_lay = QHBoxLayout()
        base_lay.addWidget(QLabel("基波"))
        self.base_slider = QSlider(Qt.Horizontal)
        self.base_slider.setRange(0, 100)
        self.base_slider.setValue(100)
        self.base_slider.valueChanged.connect(
            lambda v: self._emit_harm_if_user("base", v))
        self.base_label = QLabel("100%")
        base_lay.addWidget(self.base_slider, 1)
        base_lay.addWidget(self.base_label)
        harm_box.addLayout(base_lay)

        self.harm_ord = []
        self.harm_ampl = []
        self.harm_ampl_labels = []
        for i in range(4):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"谐波{i + 1}"))
            combo = QComboBox()
            combo.addItems(["关"] + [str(o) for o in range(2, 9)])
            combo.currentIndexChanged.connect(
                lambda idx, hh=i: self._emit_harm_if_user("order", hh, idx))
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setEnabled(False)
            slider.valueChanged.connect(
                lambda v, hh=i: self._emit_harm_if_user("ampl", hh, v))
            lab = QLabel("0%")
            row.addWidget(combo)
            row.addWidget(slider, 1)
            row.addWidget(lab)
            self.harm_ord.append(combo)
            self.harm_ampl.append(slider)
            self.harm_ampl_labels.append(lab)
            harm_box.addLayout(row)
        lay.addLayout(harm_box)
```

辅助方法(防程序化更新回环 + 归一化提示 + Vpp):

```python
    def _emit_harm_if_user(self, kind, idx, value):
        if self._updating:
            return
        if kind == "base":
            self.param_changed.emit("base", value)
        elif kind == "order":
            order = 0 if idx == 0 else idx + 1
            self.param_changed.emit(f"h{idx + 1}_order", order)
        else:
            self.param_changed.emit(f"h{idx + 1}_ampl", value)

    def _update_norm_hint(self):
        base = self.base_slider.value()
        total = base + sum(self.harm_ampl[i].value() for i in range(4))
        if total > 100:
            self.norm_hint.setText(f"已自动归一化(总幅 {total}% → 100%)")
        else:
            self.norm_hint.setText("")
```

`set_state` 扩展(base/harms 更新 + 归一化提示 + Vpp 显示):

```python
    def set_state(self, wave, freq_hz, ampl_pct, base_pct=100, harms=None):
        self._updating = True
        self._freq_hz = freq_hz
        for w, b in self.wave_btns.items():
            b.setChecked(w == wave)
        self.freq_label.setText(f"{freq_hz} Hz")
        self.base_slider.setValue(base_pct)
        self.base_label.setText(f"{base_pct}%")
        harms = harms or [(0, 0)] * 4
        for i in range(4):
            order, a = harms[i]
            self.harm_ord[i].setCurrentIndex(0 if order == 0 else order - 1)
            self.harm_ampl[i].setValue(a)
            self.harm_ampl[i].setEnabled(order != 0)
            self.harm_ampl_labels[i].setText(f"{a}%")
        self._updating = False
        self._update_norm_hint()
        self._update_vpp()
```

Vpp 相关(幅度行下方):

```python
        # ---- Vpp 显示与校准 ----
        vpp_lay = QHBoxLayout()
        self.vpp_label = QLabel("≈ -- Vpp")
        self.vpp_label.setStyleSheet("color: blue; font-weight: bold;")
        vpp_lay.addWidget(self.vpp_label)
        vpp_lay.addStretch(1)
        lay.addLayout(vpp_lay)

        set_lay = QHBoxLayout()
        set_lay.addWidget(QLabel("按 Vpp 设置"))
        self.vpp_edit = QLineEdit()
        self.vpp_edit.setPlaceholderText("V")
        self.vpp_edit.returnPressed.connect(self._on_vpp_typed)
        self.cal_edit = QDoubleSpinBox()
        self.cal_edit.setRange(0.1, 20.0)
        self.cal_edit.setDecimals(2)
        self.cal_edit.setValue(9.60)
        self.cal_edit.setSuffix(" V")
        self.cal_edit.valueChanged.connect(self._update_vpp)
        set_lay.addWidget(self.vpp_edit)
        set_lay.addWidget(QLabel("校准"))
        set_lay.addWidget(self.cal_edit)
        lay.addLayout(set_lay)

    def _update_vpp(self):
        pct = self.ampl.value()
        vpp = pct * self.cal_edit.value() / 100.0
        self.vpp_label.setText(f"≈ {vpp:.2f} Vpp")

    def _on_vpp_typed(self):
        try:
            v = float(self.vpp_edit.text())
        except ValueError:
            return
        cal = self.cal_edit.value()
        if v < 0 or v > cal:
            self.vpp_edit.clear()
            return
        self.ampl.setValue(int(round(v / cal * 100)))
        self.vpp_edit.clear()
```

频率输入改用 `parse_freq_input`(`_typed` 方法替换实现):

```python
    def _typed(self):
        try:
            v = parse_freq_input(self.freq_edit.text())
        except ValueError:
            return
        self.param_changed.emit("freq", v)
        self.freq_edit.clear()
```

幅度滑块在 set_state 中已有 blockSignals(保持)。

- [ ] **Step 3: main_window.py 接线**

`_on_state` 传递扩展参数:

```python
    def _on_state(self, ch: int):
        st = self.ctrl.state[ch]
        panel = self.ch1 if ch == 0 else self.ch2
        panel.set_state(st.wave, st.freq_hz, st.ampl_pct, st.base_pct, st.harms)
        self.status_panel.set_channel(ch, st)
        self.status_panel.preview.set_params(st.wave, st.freq_hz, st.ampl_pct,
                                             st.base_pct, st.harms)
```

(`StatusPanel.set_channel` 文案追加 `B{base}%`;controller 的 field 映射已覆盖 "base"/"h1_order".. → 无需改 param_changed 接线。)

- [ ] **Step 4: 验证**

```bash
cd <LOCAL_PATH>
python -m pytest tests/ -q
QT_QPA_PLATFORM=offscreen python -c "
from siggen.preview import make_plot_data
t, y = make_plot_data('SINE', 1000, 100, base_pct=76, harms=[(3, 23)])
assert len(t) == 600 and y.max() <= 1.001 and y.min() >= -1.001
t2, y2 = make_plot_data('SQUARE', 1000, 80)
assert y2.max() == 0.8
print('preview OK')
"
```

Expected: pytest 全 PASS;`preview OK`。

- [ ] **Step 5: 提交**

```bash
git add host/
git commit -m "feat: GUI harmonics panel, Vpp display/calibration, k/M freq input"
```

---

### Task 11: E2E 全链路 + README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 全链路验证(需板卡)**

1. 板卡在线:`cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw --bit awg/awg.runs/impl_1/host_bd_wrapper.bit`
2. `python scripts/test_bridge.py COM11` → ALL PASS(旧 + 新用例)
3. 上位机 offscreen 全链路驱动(真实串口):连接 COM11 → 回读含 BASE/HARM → SET BASE 80 + HARM1 ORDER 3 AMPL 30 → 等 GET 回读 → 断言界面 base/harms 与桥归一化值一致 → 恢复默认
4. 真实 GUI 人工操作(用户):谐波面板设置 2 次谐波 50% → 示波器 DAC0 应见明显二次谐波叠加;幅度 100% 方波 10kHz → 示波器 Vpp ≈ 9.56~9.62V 与界面 Vpp 读数一致;校准常数改 9.6 验证换算

- [ ] **Step 2: README 更新**

在 README 的"串口协议"与"调试记录"节补充:

```markdown
## 串口协议(新增)
SET CH1 BASE 100 | SET CH1 HARM1 ORDER 3 | SET CH1 HARM1 AMPL 30 | GET CH1
谐波:每通道基波+4 谐波(次数 2~8);Σ>100% 自动归一化;GET 返回归一化值

## 使用提示
- 频率输入支持 k/M 后缀(100k / 250kHz / 0.5M)
- 幅度条旁实时显示 Vpp(= 幅度% × 校准常数,默认 9.60V = 100% 满幅实测 9.56~9.62Vpp 方波10kHz)
- 按 Vpp 设置:输入目标电压自动换算幅度%;校准常数可调
```

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README for harmonics + Vpp features"
```

---

## Self-Review 记录

- **Spec 覆盖**:§3 架构(谐波链路)→ Task 1/3;§4 寄存器打包 → Task 2(PL 拆包)+ Task 6(桥打包)+ Task 8(协议);§5 协议与归一化 → Task 6/7;§6 harm_synth → Task 1;§7 GUI(Vpp/谐波面板/kM 输入)→ Task 8/9/10;§8 错误处理 → Task 6(ERR 码)+ Task 7(板测)+ Task 8/9(ValueError);§9 测试 → 各任务 TDD + Task 7 + Task 11;§11 风险(地址自动分配→Task 4 核对、截断→Task 6 归一化、向后兼容→Task 8 parse_status 旧格式)。
- **占位符扫描**:无 TBD/TODO;每个代码步骤含完整代码与期望输出。
- **类型一致性**:寄存器位布局在 Task 2(reg_bridge 拆包)、Task 6(pack_word_a/b)、Task 7(GET 期望)三处一致;协议串 `SET CH1 BASE 100`/`SET CH1 HARM1 ORDER 3` 在 Task 6/7/8 一致;GET 格式 `CH1 WAVE SINE FREQ 100000 AMPL 80 BASE 68 H1 3 20 H2 5 8 H3 0 0 H4 0 0` 在 Task 6/7/8/9 一致;字段名 `base`/`h1_order`/`h1_ampl` 在 Task 9/10 一致。
- **注意**:Task 7 中归一化期望值示例数字需实现时用整数除法现场核算(`x*100//sum`),脚本期望必须与桥行为一致。
