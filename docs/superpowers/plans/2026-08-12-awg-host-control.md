# AC820 信号发生器上位机控制(PS 串口桥 + PySide6 上位机)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PC 上位机(Python + PySide6)通过 USB 串口(CH9102F → PS UART MIO48/49)控制 AC820 + ACM2108 信号发生器的双通道波形/频率/幅度,并支持状态回读与波形预览。

**Architecture:** PC 上位机 → 115200 串口 ASCII 命令 → PS 裸机桥(SDK 小程序,收行→解析→写 AXI GPIO)→ AXI GPIO(2×32bit 全输出)→ PL reg_bridge(2-FF 同步+拆包)→ 双 dds_core(各自 fword)→ 幅度缩放 → dac_driver(偏移二进制)→ ACM2108 DAC0/DAC1。PS 无 DDR,程序跑 OCM(资料包附录1 无DDR 方案,板级验证)。上位机三栏界面 + pyqtgraph 预览。

**Tech Stack:** Vivado 2018.3(BD: PS7 + AXI GPIO v2.0)、Xilinx SDK 2018.3(standalone, XUartPs + XGpio)、Verilog(纯 RTL)、Python 3 + PySide6 + pyqtgraph + pyserial + pytest、xsim(Verilog TB)、cli-anything-vivado。

**已就绪参考(计划前已完成):**
- `scripts/ps7_props.tcl` — 从资料包 ex01(无DDR 例程)提取的**板级验证** PS7 属性集(793 行):PCW_EN_DDR=0、UART1 MIO48/49 @115200、FCLK0 50MHz、M_AXI_GP0=1
- `scripts/lscript_ocm.ld` — 无DDR 链接脚本(.text→ps7_ram_1 @0xFFFF0000,数据→ps7_ram_0 @0x0,192KB)
- 设计规格: `docs/superpowers/specs/2026-08-12-awg-host-control-design.md`
- 资料包 ex01 位置: `<LOCAL_PATH>文档教材/03_CPU裸机编程教材文档/ZYNQ/附录 第1章 无DDR的程序烧录与QSPI固化/ex01_Key_Ctrl_Led_no_ddr.rar`
- Vivado 根目录: `<LOCAL_PATH>`(bat 模式可直接 `vivado.bat -mode batch -source ...`)

**验证过的工具链事实:**
- `cli-anything-vivado` 支持: `export hw`(.hwh)、`sdk app new --hw <hdf> --src <dir> --workspace <ws>`、`sdk build <name> --workspace <ws> --type all`、`sdk run --elf <elf> --hw-platform <dir> --bit <bit>`
- 工程顶层从 `awg_top` 变为 BD wrapper(BD 内部例化 awg_top 模块,GPIO 输出在 BD 内部直连,顶层只有 sys_clk/led/dac 端口——避免未约束顶层端口 UCIO-1 错误)

---

### Task 1: reg_bridge 模块(TDD)

**Files:**
- Create: `src/rtl/reg_bridge.v`
- Create: `src/tb/reg_bridge_tb.v`

寄存器桥:AXI GPIO 输出(FCLK0 域)→ 2-FF 同步到 50MHz 域 → 拆包。打包格式(每 32bit):`freq[19:0] | ampl[7:0]<<20 | wave[1:0]<<28`。

- [ ] **Step 1: 写失败测试**

`src/tb/reg_bridge_tb.v`:

```verilog
`timescale 1ns / 1ps
module reg_bridge_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] gpio0_in = 0, gpio1_in = 0;
    wire [19:0] ch1_freq, ch2_freq;
    wire [7:0]  ch1_ampl, ch2_ampl;
    wire [1:0]  ch1_wave, ch2_wave;

    reg_bridge uut (
        .clk(clk), .rst_n(rst_n),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .ch1_freq(ch1_freq), .ch1_ampl(ch1_ampl), .ch1_wave(ch1_wave),
        .ch2_freq(ch2_freq), .ch2_ampl(ch2_ampl), .ch2_wave(ch2_wave)
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

    // pack: freq | ampl<<20 | wave<<28
    function [31:0] pack;
        input [19:0] f;
        input [7:0] a;
        input [1:0] w;
        begin
            pack = {2'b00, w, a, f};
        end
    endfunction

    initial begin
        // 上电默认(复位释放后,PS 尚未写入):1kHz/50%/SINE
        #100 rst_n = 1;
        #60;   // 2-FF 同步 + 组合 -> 2 clk 内稳定
        chk("default ch1 freq=1000", ch1_freq == 20'd1000);
        chk("default ch1 ampl=50",   ch1_ampl == 8'd50);
        chk("default ch1 wave=0",    ch1_wave == 2'd0);
        chk("default ch2 freq=1000", ch2_freq == 20'd1000);
        chk("default ch2 ampl=50",   ch2_ampl == 8'd50);
        chk("default ch2 wave=0",    ch2_wave == 2'd0);

        // CH1: 100kHz/80%/SQUARE ; CH2: 2500Hz/20%/SAW
        gpio0_in = pack(20'd100000, 8'd80, 2'd1);
        gpio1_in = pack(20'd2500,   8'd20, 2'd3);
        #40;
        chk("ch1 freq=100000", ch1_freq == 20'd100000);
        chk("ch1 ampl=80",     ch1_ampl == 8'd80);
        chk("ch1 wave=1",      ch1_wave == 2'd1);
        chk("ch2 freq=2500",   ch2_freq == 20'd2500);
        chk("ch2 ampl=20",     ch2_ampl == 8'd20);
        chk("ch2 wave=3",      ch2_wave == 2'd3);

        // 钳位:freq<10 -> 10
        gpio0_in = pack(20'd5, 8'd50, 2'd0);
        #40;
        chk("clamp freq 5->10", ch1_freq == 20'd10);
        chk("clamp keeps ampl", ch1_ampl == 8'd50);

        // 双通道独立:改 CH1 不影响 CH2
        gpio0_in = pack(20'd999, 8'd33, 2'd2);
        #40;
        chk("ch1 updated", ch1_freq == 20'd999 && ch1_ampl == 8'd33 && ch1_wave == 2'd2);
        chk("ch2 untouched", ch2_freq == 20'd2500 && ch2_ampl == 8'd20 && ch2_wave == 2'd3);

        if (errs == 0) $display("PASS reg_bridge_tb");
        else $display("FAIL reg_bridge_tb: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行确认失败**

```bash
xvlog --sv src/tb/reg_bridge_tb.v && xelab reg_bridge_tb -s sim && xsim sim -runall
```

Expected: FAIL(模块未定义)。

- [ ] **Step 3: 写最小实现**

`src/rtl/reg_bridge.v`:

```verilog
`timescale 1ns / 1ps
// AXI GPIO (PS FCLK0 domain) -> 2-FF sync -> unpack CH1/CH2 params.
// Packing: freq[19:0] | ampl[7:0]<<20 | wave[1:0]<<28. Params are quasi-static
// (change only on host command), so per-bit 2-FF sync is safe.
// word == 0 means "PS 尚未写入" (freq 有效下限 10,PS 永不写全 0) -> hold DEF.
module reg_bridge (
    input  wire clk,           // 50 MHz
    input  wire rst_n,
    input  wire [31:0] gpio0_in,  // -> CH1
    input  wire [31:0] gpio1_in,  // -> CH2
    output wire [19:0] ch1_freq,
    output wire [7:0]  ch1_ampl,
    output wire [1:0]  ch1_wave,
    output wire [19:0] ch2_freq,
    output wire [7:0]  ch2_ampl,
    output wire [1:0]  ch2_wave
);
    localparam [31:0] DEF = 32'h0320_03E8;  // 1000 | 50<<20 | 0<<28 : 1kHz/50%/SINE

    reg [31:0] s0, s1, r0, r1;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s0 <= 32'd0; s1 <= 32'd0;
            r0 <= 32'd0; r1 <= 32'd0;
        end else begin
            s0 <= gpio0_in; s1 <= gpio1_in;
            r0 <= s0;       r1 <= s1;
        end

    // word==0 (PS 未写入) -> 保持默认;否则拆包 + 钳位(防御)
    wire [31:0] p0 = (r0 == 32'd0) ? DEF : r0;
    wire [31:0] p1 = (r1 == 32'd0) ? DEF : r1;

    assign ch1_freq = (p0[19:0] < 20'd10) ? 20'd10 : p0[19:0];
    assign ch1_ampl = p0[27:20];
    assign ch1_wave = p0[29:28];
    assign ch2_freq = (p1[19:0] < 20'd10) ? 20'd10 : p1[19:0];
    assign ch2_ampl = p1[27:20];
    assign ch2_wave = p1[29:28];
endmodule
```

- [ ] **Step 4: 运行确认通过**

```bash
xvlog --sv src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v && xelab reg_bridge_tb -s sim && xsim sim -runall
```

Expected: `PASS reg_bridge_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/reg_bridge.v src/tb/reg_bridge_tb.v
git commit -m "feat: register bridge (2-FF sync, param unpack, clamp) with TB"
```

> 注(实现时确认):Vivado 2018.3 的 xvlog/xelab/xsim 在 `<LOCAL_PATH>` 且**不在 PATH**,须用全路径调用,如 `"<LOCAL_PATH>" --sv ...`(已验证可用)。其余任务同理。

---

### Task 2: ampl_scale 模块(TDD)

**Files:**
- Create: `src/rtl/ampl_scale.v`
- Create: `src/tb/ampl_scale_tb.v`

幅度缩放:`out = sample_in * ampl_pct / 100`。sample 为有符号 ±127,ampl 0~100。综合对常数 100 做乘倒数优化,误差 <1 LSB(8bit DAC 无感知)。流水 2 拍(乘→除)。

- [ ] **Step 1: 写失败测试**

`src/tb/ampl_scale_tb.v`:

```verilog
`timescale 1ns / 1ps
module ampl_scale_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [7:0] sample_in = 0;
    reg [7:0] ampl_pct = 0;
    wire [7:0] sample_out;

    ampl_scale uut (.clk(clk), .rst_n(rst_n), .sample_in(sample_in), .ampl_pct(ampl_pct), .sample_out(sample_out));
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

    // 驱动一组输入,等 3 拍(2 拍流水 + 裕量)后检查
    task drive_check;
        input [7:0] s;
        input [7:0] a;
        input [15:0] exp;
        input [255:0] name;
        begin
            @(posedge clk); #1;
            sample_in = s;
            ampl_pct = a;
            repeat (3) @(posedge clk); #1;
            chk(name, $signed(sample_out) == $signed(exp));
        end
    endtask

    initial begin
        #100 rst_n = 1;

        drive_check( 8'd127, 8'd100,  8'd127, "127*100% = 127");
        drive_check( 8'd127, 8'd50,   8'd63,  "127*50% = 63");
        drive_check( 8'd100, 8'd80,   8'd80,  "100*80% = 80");
        drive_check( 8'd64,  8'd33,   8'd21,  "64*33% = 21 (trunc)");
        drive_check( 8'd1,   8'd50,   8'd0,   "1*50% = 0 (trunc)");
        drive_check(-8'd127, 8'd100, -8'd127, "-127*100% = -127");
        drive_check(-8'd127, 8'd50,  -8'd63,  "-127*50% = -63 (trunc)");
        drive_check( 8'd127, 8'd0,    8'd0,   "127*0% = 0");
        drive_check( 8'd0,   8'd100,  8'd0,   "0*100% = 0");

        if (errs == 0) $display("PASS ampl_scale_tb");
        else $display("FAIL ampl_scale_tb: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行确认失败**

```bash
xvlog --sv src/tb/ampl_scale_tb.v && xelab ampl_scale_tb -s sim && xsim sim -runall
```

Expected: FAIL(模块未定义)。

- [ ] **Step 3: 写最小实现**

`src/rtl/ampl_scale.v`:

```verilog
`timescale 1ns / 1ps
// out = sample_in * ampl_pct / 100, signed 8-bit, 2-cycle pipeline.
// Division by constant 100 -> synthesis uses multiply-by-reciprocal (err < 1 LSB).
module ampl_scale (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] sample_in,  // two's complement -128..+127
    input  wire [7:0] ampl_pct,   // 0..100 (%)
    output reg  [7:0] sample_out  // two's complement
);
    reg signed [14:0] prod;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            prod       <= 15'sd0;
            sample_out <= 8'sd0;
        end else begin
            prod       <= $signed(sample_in) * $signed({1'b0, ampl_pct});
            sample_out <= prod / 15'sd100;   // truncated toward zero
        end
endmodule
```

- [ ] **Step 4: 运行确认通过**

```bash
xvlog --sv src/rtl/ampl_scale.v src/tb/ampl_scale_tb.v && xelab ampl_scale_tb -s sim && xsim sim -runall
```

Expected: `PASS ampl_scale_tb`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/ampl_scale.v src/tb/ampl_scale_tb.v
git commit -m "feat: amplitude scaler (signed mult + const div) with TB"
```

---

### Task 3: dac_driver 双样本改造(TDD)

**Files:**
- Modify: `src/rtl/dac_driver.v`(单 sample → sample0/sample1)
- Modify: `src/tb/dac_driver_tb.v`

- [ ] **Step 1: 改 TB(先红)**

`src/tb/dac_driver_tb.v` 整体替换:

```verilog
`timescale 1ns / 1ps
module dac_driver_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [7:0] sample0 = 8'h00;
    reg [7:0] sample1 = 8'h00;
    wire [7:0] dac0_db, dac1_db;
    wire daclk0, daclk1;

    dac_driver uut (
        .clk(clk), .rst_n(rst_n),
        .sample0(sample0), .sample1(sample1),
        .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer clk_rises = 0;
    integer daclk_rises = 0;
    integer window_rises = 0;
    reg [7:0] exp_d0 = 8'h00;
    reg [7:0] exp_d1 = 8'h00;
    reg window_en = 0;

    always @(posedge clk) begin
        if (rst_n) begin
            exp_d0 <= sample0 ^ 8'h80;
            exp_d1 <= sample1 ^ 8'h80;
        end
        clk_rises = clk_rises + 1;
    end
    always @(posedge daclk0) begin
        daclk_rises = daclk_rises + 1;
        if (window_en) window_rises = window_rises + 1;
        if (rst_n) begin
            if (dac0_db !== exp_d0) begin
                $display("FAIL dac_driver: ch0 at daclk rise %h exp %h", dac0_db, exp_d0);
                errs = errs + 1;
            end
            if (dac1_db !== exp_d1) begin
                $display("FAIL dac_driver: ch1 at daclk rise %h exp %h", dac1_db, exp_d1);
                errs = errs + 1;
            end
            if (daclk0 !== daclk1) begin
                $display("FAIL dac_driver: clk0 != clk1"); errs = errs + 1;
            end
        end
    end

    initial begin
        #100 rst_n = 1;
        #100;
        repeat (100) begin
            @(posedge clk); #1;
            sample0 = sample0 + 8'd3;
            sample1 = sample1 - 8'd5;   // 双通道数据不同
        end
        #1000;
        window_en = 1;
        repeat (1000) @(posedge clk);
        window_en = 0;
        if (window_rises < 950 || window_rises > 1050) begin
            $display("FAIL dac_driver: %0d daclk rises in 1000 clk", window_rises);
            errs = errs + 1;
        end
        if (errs == 0) $display("PASS dac_driver");
        else $display("FAIL dac_driver: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行确认失败**

```bash
xvlog --sv src/rtl/dac_driver.v src/tb/dac_driver_tb.v && xelab dac_driver_tb -s sim && xsim sim -runall
```

Expected: FAIL(端口不匹配编译错误)。

- [ ] **Step 3: 改 DUT(变绿)**

`src/rtl/dac_driver.v` 整体替换:

```verilog
`timescale 1ns / 1ps
// Parallel DAC driver for MS9708 (latch on rising edge).
// Data registered on posedge clk; DACLK = ~clk (combinational) -> its rising
// edge occurs at the clk falling edge, 10 ns after the data update (half a
// 50 MHz period), ample setup time. DAC0 <- sample0, DAC1 <- sample1.
// Data format: the ACM2108 DAC expects OFFSET-BINARY (0..255, 128 = mid),
// while the DDS produces TWO'S COMPLEMENT (-128..+127); convert via ^0x80.
module dac_driver (
    input  wire clk,          // 50 MHz
    input  wire rst_n,
    input  wire [7:0] sample0, // CH1 -> DAC0
    input  wire [7:0] sample1, // CH2 -> DAC1
    output reg  [7:0] dac0_db,
    output reg  [7:0] dac1_db,
    output wire daclk0,
    output wire daclk1
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            dac0_db <= 8'd0;
            dac1_db <= 8'd0;
        end else begin
            dac0_db <= sample0 ^ 8'h80;
            dac1_db <= sample1 ^ 8'h80;
        end

    // NOTE: cannot register ~clk on posedge clk (would latch constant 0);
    // combinational inversion gives the rising edge at the clk falling edge.
    assign daclk0 = ~clk;
    assign daclk1 = ~clk;
endmodule
```

- [ ] **Step 4: 运行确认通过**

```bash
xvlog --sv src/rtl/dac_driver.v src/tb/dac_driver_tb.v && xelab dac_driver_tb -s sim && xsim sim -runall
```

Expected: `PASS dac_driver`。

- [ ] **Step 5: 提交**

```bash
git add src/rtl/dac_driver.v src/tb/dac_driver_tb.v
git commit -m "feat: dual-sample DAC driver (DAC0/1 independent data)"
```

---

### Task 4: awg_top 改造(双通道寄存器驱动,TDD)

**Files:**
- Modify: `src/rtl/awg_top.v`(整体替换:移除 key_ctrl/S1/S2/sw,加入 reg_bridge + 双 dds_core + 双 ampl_scale)
- Modify: `src/tb/awg_top_tb.v`(整体替换:寄存器驱动,双通道独立验证)
- Delete: `src/rtl/key_ctrl.v`、`src/tb/key_ctrl_tb.v`

- [ ] **Step 1: 写失败测试**

`src/tb/awg_top_tb.v` 整体替换:

```verilog
`timescale 1ns / 1ps
module awg_top_tb;
    reg clk = 0;
    reg [31:0] gpio0_in = 0, gpio1_in = 0;
    wire [7:0] led;
    wire [7:0] dac0_db, dac1_db;
    wire daclk0, daclk1;

    awg_top uut (
        .sys_clk(clk), .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .led(led), .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer ch0_trans = 0, ch1_trans = 0;
    integer ch0_max = 0, ch0_min = 255;
    integer ch1_max = 0, ch1_min = 255;
    reg [7:0] prev0 = 8'hFF, prev1 = 8'hFF;

    // 统计 DAC 数据变化次数与幅度范围
    always @(posedge clk) begin
        if (dac0_db !== prev0) begin ch0_trans = ch0_trans + 1; prev0 = dac0_db; end
        if (dac1_db !== prev1) begin ch1_trans = ch1_trans + 1; prev1 = dac1_db; end
        if (dac0_db > ch0_max) ch0_max = dac0_db;
        if (dac0_db < ch0_min) ch0_min = dac0_db;
        if (dac1_db > ch1_max) ch1_max = dac1_db;
        if (dac1_db < ch1_min) ch1_min = dac1_db;
    end

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    // pack: freq | ampl<<20 | wave<<28
    function [31:0] pack;
        input [19:0] f;
        input [7:0] a;
        input [1:0] w;
        begin
            pack = {2'b00, w, a, f};
        end
    endfunction

    initial begin
        // 快速释放上电复位(同旧 TB:直接 poke rst_cnt)
        uut.rst_cnt = 20'd0;
        #100;
        uut.rst_cnt = 20'h7FFFF;
        #20;
        uut.rst_cnt = 20'h80000;
        #100;

        // 默认(未写入):1kHz/50%/SINE 双通道
        // 等待 ~1 个正弦周期(1kHz = 20000 clk)收集统计
        #200_000;
        chk("default ch0 active", ch0_trans > 100);
        chk("default ch1 active", ch1_trans > 100);
        chk("default ch0 ampl 50% range [0x41,0xBF]", ch0_min >= 8'h41 && ch0_max <= 8'hBF);
        chk("default ch1 ampl 50% range [0x41,0xBF]", ch1_min >= 8'h41 && ch1_max <= 8'hBF);
        chk("default LED=0", led == 8'h00);   // wave 0, freq_khz[9:4]=0

        // CH1: 100kHz/100%/SINE ; CH2: 1kHz/50%/SQUARE
        gpio0_in = pack(20'd100000, 8'd100, 2'd0);
        gpio1_in = pack(20'd1000,   8'd50,  2'd1);
        ch0_trans = 0; ch1_trans = 0; ch0_max = 0; ch0_min = 255; ch1_max = 0; ch1_min = 255;
        prev0 = 8'hFF; prev1 = 8'hFF;
        #200_000;   // 100kHz = 20 周期;1kHz 方波 = 1 周期
        chk("ch0 100kHz more trans than ch1", ch0_trans > ch1_trans * 20);
        chk("ch0 100% full swing (min<=0x06)", ch0_min <= 8'h06);
        chk("ch0 100% full swing (max>=0xFA)", ch0_max >= 8'hFA);
        chk("ch1 square 50% range [0x40,0xBF]", ch1_min >= 8'h40 && ch1_max <= 8'hBF);
        chk("LED freq 100k -> 0x18", led == 8'h18);   // wave0 | (100/1000=0? -> [9:4]=(100>>4)=6) = 0x18
        // 注:LED[7:2] = freq_khz[9:4];100kHz -> freq_khz=100 -> [9:4]=100>>4=6 -> 0b000110<<2 = 0x18

        // CH2 独立:改 CH2 频率为 10kHz,CH1 不动
        gpio1_in = pack(20'd10000, 8'd50, 2'd1);
        ch0_trans = 0; ch1_trans = 0;
        prev0 = 8'hFF; prev1 = 8'hFF;
        #100_000;
        chk("ch1 unchanged after ch2 edit (still fast)", ch0_trans > 0);
        chk("LED still shows ch1 100kHz", led == 8'h18);

        // 幅度 0% -> 输出恒为偏移二进制中点 0x80
        gpio0_in = pack(20'd100000, 8'd0, 2'd0);
        ch0_trans = 0;
        prev0 = 8'hFF;
        #200_000;
        chk("ch0 ampl 0 -> const 0x80", ch0_trans == 0 && prev0 == 8'h80);

        // 波形切换:CH1 -> SAW,LED[1:0]=3
        gpio0_in = pack(20'd100000, 8'd100, 2'd3);
        #20_000;
        chk("LED wave=3", (led & 2'b11) == 2'b11);

        if (errs == 0) $display("PASS awg_top_tb");
        else $display("FAIL awg_top_tb: %0d errors", errs);
        $finish;
    end
endmodule
```

- [ ] **Step 2: 运行确认失败**

```bash
xvlog --sv src/rtl/awg_top.v src/tb/awg_top_tb.v && xelab awg_top_tb -s sim && xsim sim -runall
```

Expected: FAIL(端口不匹配编译错误)。

- [ ] **Step 3: 改 DUT**

`src/rtl/awg_top.v` 整体替换:

```verilog
`timescale 1ns / 1ps
// AWG top (host-controlled): reg_bridge (AXI GPIO from PS) -> dual dds_core ->
// dual ampl_scale -> dac_driver -> ACM2108 DAC0/DAC1.
module awg_top (
    input  wire sys_clk,          // 50 MHz (Y9)
    input  wire [31:0] gpio0_in,  // AXI GPIO ch0 (PS FCLK0 domain) -> CH1
    input  wire [31:0] gpio1_in,  // AXI GPIO ch1 (PS FCLK0 domain) -> CH2
    output wire [7:0] led,        // P20..T17
    output wire [7:0] dac0_db,    // GPIO0 -> ACM2108 DAC0 data
    output wire [7:0] dac1_db,    // GPIO0 -> ACM2108 DAC1 data
    output wire daclk0,           // GPIO0 -> DAC0 clock
    output wire daclk1            // GPIO0 -> DAC1 clock
);
    // power-on reset: ~10.5 ms (2^19 = 524288 clk @ 50 MHz); FF power-on state = 0
    reg [19:0] rst_cnt;
    wire rst_n = rst_cnt[19];
    always @(posedge sys_clk)
        if (!rst_n) rst_cnt <= rst_cnt + 1'b1;

    wire [19:0] ch1_freq, ch2_freq;
    wire [7:0]  ch1_ampl, ch2_ampl;
    wire [1:0]  ch1_wave, ch2_wave;

    reg_bridge u_reg (
        .clk(sys_clk), .rst_n(rst_n),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .ch1_freq(ch1_freq), .ch1_ampl(ch1_ampl), .ch1_wave(ch1_wave),
        .ch2_freq(ch2_freq), .ch2_ampl(ch2_ampl), .ch2_wave(ch2_wave)
    );

    // fword = freq*85 + (freq*9)/10  ~  freq * 2^32 / 5e7 (err < 0.001%)
    wire [31:0] fword1 = ch1_freq * 20'd85 + ((ch1_freq * 20'd9) / 20'd10);
    wire [31:0] fword2 = ch2_freq * 20'd85 + ((ch2_freq * 20'd9) / 20'd10);

    wire [7:0] s1, s2, a1, a2;

    dds_core u_dds1 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword1), .wave_sel(ch1_wave), .sample(s1)
    );
    dds_core u_dds2 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword2), .wave_sel(ch2_wave), .sample(s2)
    );

    ampl_scale u_amp1 (
        .clk(sys_clk), .rst_n(rst_n), .sample_in(s1), .ampl_pct(ch1_ampl), .sample_out(a1)
    );
    ampl_scale u_amp2 (
        .clk(sys_clk), .rst_n(rst_n), .sample_in(s2), .ampl_pct(ch2_ampl), .sample_out(a2)
    );

    dac_driver u_dac (
        .clk(sys_clk), .rst_n(rst_n),
        .sample0(a1), .sample1(a2),
        .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );

    // LED: CH1 波形编号 (LED0~1) + CH1 频率粗刻度 freq_khz[9:4] (LED2~7)
    assign led[1:0] = ch1_wave;
    assign led[7:2] = (ch1_freq / 20'd1000)[9:4];
endmodule
```

- [ ] **Step 4: 删除 key_ctrl**

```bash
git rm src/rtl/key_ctrl.v src/tb/key_ctrl_tb.v
```

- [ ] **Step 5: 运行确认通过**

```bash
xvlog --sv src/rtl/reg_bridge.v src/rtl/ampl_scale.v src/rtl/dds_core.v src/rtl/sine_rom.v src/rtl/dac_driver.v src/rtl/awg_top.v src/tb/awg_top_tb.v && xelab awg_top_tb -s sim && xsim sim -runall
```

Expected: `PASS awg_top_tb`。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat: host-controlled AWG top (reg_bridge + dual DDS + ampl scale); drop key_ctrl"
```

---

### Task 5: XDC 更新(移除按键/拨码)

**Files:**
- Modify: `src/constraints/awg.xdc`

顶层已无 key_s1/key_s2/sw 端口,对应约束必须删除(否则 UCIO-1)。

- [ ] **Step 1: 编辑 XDC**

删除以下行(约 10-15 行):

```tcl
# keys (pressed = low)
set_property PACKAGE_PIN H18 [get_ports key_s1]
set_property PACKAGE_PIN H19 [get_ports key_s2]

# switches (up = 1)
set_property PACKAGE_PIN G21 [get_ports {sw[0]}]
set_property PACKAGE_PIN G20 [get_ports {sw[1]}]
```

并把 IOSTANDARD 批量行改为(移除 key/sw 引用):

```tcl
set_property IOSTANDARD LVCMOS33 [get_ports {led[*] dac0_db[*] dac1_db[*] daclk0 daclk1}]
```

- [ ] **Step 2: 验证约束与顶层端口一致**

```bash
grep -c "get_ports" src/constraints/awg.xdc
grep -oE "get_ports [^]]*" src/constraints/awg.xdc | grep -E "key|sw" || echo "OK: no key/sw refs"
```

Expected: 第二个命令输出 `OK: no key/sw refs`。

- [ ] **Step 3: 提交**

```bash
git add src/constraints/awg.xdc
git commit -m "chore: drop key/sw pin constraints (host-controlled top)"
```

---

### Task 6: Block Design(PS7 + AXI GPIO + awg_top 模块)

**Files:**
- Create: `scripts/create_bd.tcl`
- Run: vivado batch(约 2-3 分钟)

BD 结构:`host_bd` = PS7(UART1 MIO48/49 + FCLK0 50MHz + M_AXI_GP0,无 DDR)+ AXI GPIO(2×32bit 全输出)+ awg_top(作为 BD 模块例化)+ axi_interconnect + proc_sys_reset。顶层 = `host_bd_wrapper`。awg_top 的 gpio0_in/gpio1_in 在 BD 内部连 AXI GPIO 输出,**不出现在顶层**,避免 UCIO-1。

- [ ] **Step 1: 写 create_bd.tcl**

`scripts/create_bd.tcl`:

```tcl
# Create host_bd: PS7 (UART1 MIO48/49 @115200, FCLK0 50MHz, M_AXI_GP0, no DDR)
# + AXI GPIO (dual 32-bit all-output) + awg_top (BD module).
# Usage: vivado -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
set proj_file [lindex $argv 0]
set script_dir [file dirname [file normalize [info script]]]

open_project $proj_file
source $script_dir/ps7_props.tcl

create_bd_design host_bd

# ---- PS7 (board-verified props from 资料包 ex01 无DDR 例程) ----
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
set_property -dict $ps7_props $ps7

# FIXED_IO 外部接口(MIO 引脚,含 UART1)
create_bd_intf_port -mode Master -vlnv xilinx.com:interface:fix_io_rtl:1.0 FIXED_IO_0
connect_bd_intf_net [get_bd_intf_pins ps7_0/FIXED_IO] [get_bd_intf_ports FIXED_IO_0]

# ---- AXI GPIO: dual channel, 32-bit, all outputs ----
set agpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_0]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS_2 {1}] $agpio

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

# AXI GPIO 输出 -> awg_top 参数输入(内部连接)
connect_bd_net [get_bd_pins axi_gpio_0/gpio_io_o]  [get_bd_pins awg_top_0/gpio0_in]
connect_bd_net [get_bd_pins axi_gpio_0/gpio2_io_o] [get_bd_pins awg_top_0/gpio1_in]

# ---- AXI interconnect (1SI/1MI, 手写, 确定性) ----
set axiint [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {1}] $axiint
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_interconnect_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M00_AXI] [get_bd_intf_pins axi_gpio_0/S_AXI]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/S00_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_interconnect_0/M00_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_gpio_0/s_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]

# ---- reset ----
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]     [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7/interconnect_aresetn] [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/S00_ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_interconnect_0/M00_ARESETN]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn]   [get_bd_pins axi_gpio_0/s_axi_aresetn]

# ---- wrapper ----
validate_bd_design
save_bd_design
make_wrapper -files [get_files host_bd.bd] -top
generate_target all [get_files host_bd.bd]
close_project
```

- [ ] **Step 2: 运行创建 BD**

```bash
cd <LOCAL_PATH>
"<LOCAL_PATH>" -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
```

Expected: 无 ERROR;日志含 `validate_bd_design` 通过。若报错,常见修复:
- `create_bd_cell -type module` 找不到 awg_top → 确认 src/rtl/*.v 已 `source add` 进工程(旧工程应有)
- 属性名错误 → 对照 ps7_props.tcl 中实际属性名
- 若 `-reference` 方式失败,改为 `create_bd_cell -type module -reference awg_top` 不带名称(自动命名),或检查模块名大小写

- [ ] **Step 3: 提交**

```bash
git add scripts/create_bd.tcl
git commit -m "feat: block design script (PS7 UART1 + AXI GPIO dual + awg_top module)"
```

---

### Task 7: 综合/实现/比特流

**Files:** 无新增(运行 CLI)

- [ ] **Step 1: 综合**

```bash
cli-anything-vivado synth run awg/awg.xpr
```

Expected: 综合完成,顶层为 host_bd_wrapper;无 UCIO-1(顶层只有已约束端口)。若报 UCIO-1:检查 XDC 是否还有 key/sw 残留(Task 5)。

- [ ] **Step 2: 实现 + 比特流**

```bash
cli-anything-vivado impl run awg/awg.xpr
cli-anything-vivado bitstream generate awg/awg.xpr
```

Expected: `awg/awg.runs/impl_1/host_bd_wrapper.bit` 生成;时序报告无违例(50MHz + DAC 约束裕量充足)。

---

### Task 8: SDK 裸机桥(export hdf → app → main.c → OCM lscript → build)

**Files:**
- Create: `scripts/export_hdf.tcl`
- Create: `sdk/src/main.c`
- Run: cli-anything-vivado sdk app new / sdk build

- [ ] **Step 1: 导出硬件平台(.hdf)**

`scripts/export_hdf.tcl`:

```tcl
# Export hardware platform for SDK (2018.3 .hdf format).
# Usage: vivado -mode batch -source scripts/export_hdf.tcl -tclargs <LOCAL_PATH>
set proj_file [lindex $argv 0]
open_project $proj_file
write_hw_platform -include_bit -file [file join [file dirname $proj_file] host_bd.hdf]
close_project
```

运行:

```bash
cd <LOCAL_PATH>
"<LOCAL_PATH>" -mode batch -source scripts/export_hdf.tcl -tclargs <LOCAL_PATH>
ls awg/host_bd.hdf
```

Expected: `awg/host_bd.hdf` 存在(内含 .hwh + ps7_init.tcl + bit)。

- [ ] **Step 2: 写桥程序**

`mkdir -p sdk/src` 并创建 `sdk/src/main.c`:

```c
/*****************************************************************************
 * AC820 AWG bridge (PS bare-metal, runs from OCM, no DDR).
 * UART1 (MIO48/49) @115200, 8N1. ASCII line protocol, CR/LF terminated.
 *   SET CH1|CH2 WAVE SINE|SQUARE|TRIANGLE|SAW   -> OK
 *   SET CH1|CH2 FREQ <10..1000000>              -> OK
 *   SET CH1|CH2 AMPL <0..100>                   -> OK
 *   GET CH1|CH2                                 -> CHx WAVE .. FREQ .. AMPL ..
 * Errors: ERR UNKNOWN CMD / ERR BAD CHANNEL / ERR BAD FIELD / ERR BAD VALUE
 *         ERR FREQ OUT OF RANGE / ERR AMPL OUT OF RANGE / ERR TIMEOUT
 * Writes per-channel params to AXI GPIO (dual 32-bit all-output):
 *   word = freq | ampl<<20 | wave<<28  (matches PL reg_bridge packing)
 *****************************************************************************/
#include "xparameters.h"
#include "xuartps.h"
#include "xgpio.h"
#include "xil_printf.h"
#include "xtime_l.h"
#include <string.h>

#define UART_DEVICE_ID XPAR_XUARTPS_0_DEVICE_ID
#define GPIO_DEVICE_ID XPAR_AXI_GPIO_0_DEVICE_ID

#define RX_BUF_MAX    64
#define FREQ_MIN      10
#define FREQ_MAX      1000000
#define AMPL_MAX      100
#define TIMEOUT_TICKS (XTime)(666666666ULL / 2)   /* 500ms @ 666.666MHz CPU_3x2x */

typedef struct {
    u32 wave;      /* 0=SINE 1=SQUARE 2=TRIANGLE 3=SAW */
    u32 freq_hz;   /* FREQ_MIN..FREQ_MAX */
    u32 ampl_pct;  /* 0..AMPL_MAX */
} ch_state_t;

static XUartPs  Uart;
static XGpio    Gpio;
static ch_state_t ch[2];
static char rx_buf[RX_BUF_MAX];
static int  rx_len;
static XTime last_rx;

static const char *WAVE_NAME[4] = {"SINE", "SQUARE", "TRIANGLE", "SAW"};

static u32 pack_word(const ch_state_t *s)
{
    return s->freq_hz | (s->ampl_pct << 20) | (s->wave << 28);
}

static void write_all(void)
{
    XGpio_DiscreteWrite(&Gpio, 1, pack_word(&ch[0]));  /* CH1 -> GPIO ch0 */
    XGpio_DiscreteWrite(&Gpio, 2, pack_word(&ch[1]));  /* CH2 -> GPIO ch1 */
}

static void uart_send(const char *s)
{
    int len = (int)strlen(s);
    while (len > 0) {
        int sent = XUartPs_Send(&Uart, (u8 *)s, len);
        if (sent <= 0) break;   /* TX FIFO full; 下轮再发 */
        s  += sent;
        len -= sent;
    }
}

static int parse_u32(const char *s, u32 *out)
{
    u32 v = 0;
    if (*s == '\0') return 0;
    while (*s) {
        if (*s < '0' || *s > '9') return 0;
        v = v * 10 + (u32)(*s - '0');
        if (v > 2000000u) return 0;   /* 溢出保护 */
        s++;
    }
    *out = v;
    return 1;
}

static void do_line(char *line)
{
    char *tok[8];
    int n = 0;
    char *p = line;

    while (*p) {                        /* 按空格/制表切词 */
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0') break;
        if (n >= 8) { uart_send("ERR BAD VALUE\r\n"); return; }
        tok[n++] = p;
        while (*p && *p != ' ' && *p != '\t') p++;
        if (*p) *p++ = '\0';
    }
    if (n < 1) return;

    /* 全部转大写(收到时已转,这里兜底) */
    int i;
    for (i = 0; tok[0][i]; i++)
        if (tok[0][i] >= 'a' && tok[0][i] <= 'z') tok[0][i] -= 32;

    if (strcmp(tok[0], "GET") == 0) {
        int ci;
        if (n < 2 || strcmp(tok[1], "CH1") != 0 && strcmp(tok[1], "CH2") != 0) {
            uart_send("ERR BAD CHANNEL\r\n"); return;
        }
        ci = (tok[1][2] == '1') ? 0 : 1;
        char out[64];
        xil_sprintf(out, "CH%d WAVE %s FREQ %lu AMPL %lu\r\n", ci + 1,
                    WAVE_NAME[ch[ci].wave], (unsigned long)ch[ci].freq_hz,
                    (unsigned long)ch[ci].ampl_pct);
        uart_send(out);
        return;
    }

    if (strcmp(tok[0], "SET") == 0) {
        int ci;
        if (n < 4 || strcmp(tok[1], "CH1") != 0 && strcmp(tok[1], "CH2") != 0) {
            uart_send("ERR BAD CHANNEL\r\n"); return;
        }
        ci = (tok[1][2] == '1') ? 0 : 1;

        if (strcmp(tok[2], "WAVE") == 0) {
            int w;
            for (w = 0; w < 4; w++)
                if (strcmp(tok[3], WAVE_NAME[w]) == 0) break;
            if (w >= 4) { uart_send("ERR BAD VALUE\r\n"); return; }
            ch[ci].wave = (u32)w;
        } else if (strcmp(tok[2], "FREQ") == 0) {
            u32 f;
            if (!parse_u32(tok[3], &f)) { uart_send("ERR BAD VALUE\r\n"); return; }
            if (f < FREQ_MIN || f > FREQ_MAX) { uart_send("ERR FREQ OUT OF RANGE\r\n"); return; }
            ch[ci].freq_hz = f;
        } else if (strcmp(tok[2], "AMPL") == 0) {
            u32 a;
            if (!parse_u32(tok[3], &a)) { uart_send("ERR BAD VALUE\r\n"); return; }
            if (a > AMPL_MAX) { uart_send("ERR AMPL OUT OF RANGE\r\n"); return; }
            ch[ci].ampl_pct = a;
        } else {
            uart_send("ERR BAD FIELD\r\n");
            return;
        }
        write_all();
        uart_send("OK\r\n");
        return;
    }

    uart_send("ERR UNKNOWN CMD\r\n");
}

int main(void)
{
    XUartPs_Config *ucfg = XUartPs_LookupConfig(UART_DEVICE_ID);
    XUartPs_CfgInitialize(&Uart, ucfg, ucfg->BaseAddress);
    XUartPs_SetBaudRate(&Uart, 115200);

    XGpio_Initialize(&Gpio, GPIO_DEVICE_ID);
    XGpio_SetDataDirection(&Gpio, 1, 0x0);   /* 全输出 */
    XGpio_SetDataDirection(&Gpio, 2, 0x0);

    /* 上电默认:双通道 1kHz / 50% / SINE */
    ch[0].wave = 0; ch[0].freq_hz = 1000; ch[0].ampl_pct = 50;
    ch[1] = ch[0];
    write_all();

    xil_printf("AC820-AWG bridge ready\r\n");

    rx_len = 0;
    XTime_GetTime(&last_rx);

    while (1) {
        while (XUartPs_IsReceiveData(Uart.Config.BaseAddress)) {
            u8 byte = XUartPs_ReadReg(Uart.Config.BaseAddress, XUARTPS_FIFO_OFFSET);
            XTime_GetTime(&last_rx);
            if (byte == '\r' || byte == '\n') {
                if (rx_len > 0) {
                    rx_buf[rx_len] = '\0';
                    int i;
                    for (i = 0; rx_buf[i]; i++)       /* 统一大写,大小写不敏感 */
                        if (rx_buf[i] >= 'a' && rx_buf[i] <= 'z') rx_buf[i] -= 32;
                    do_line(rx_buf);
                    rx_len = 0;
                }
            } else if (rx_len < RX_BUF_MAX - 1) {
                rx_buf[rx_len++] = (char)byte;
            } else {
                rx_len = 0;   /* 行溢出丢弃 */
                uart_send("ERR BAD VALUE\r\n");
            }
        }
        if (rx_len > 0) {   /* 半行超时 */
            XTime now;
            XTime_GetTime(&now);
            if (now - last_rx > TIMEOUT_TICKS) {
                rx_len = 0;
                uart_send("ERR TIMEOUT\r\n");
            }
        }
    }
    return 0;
}
```

- [ ] **Step 3: 创建 SDK 工程并替换 lscript**

```bash
cd <LOCAL_PATH>
cli-anything-vivado sdk app new awg_bridge --hw awg/host_bd.hdf --src sdk/src --workspace sdk/ws
```

Expected: `sdk/ws/awg_bridge/`(app)与 `sdk/ws/awg_bridge_bsp/`(BSP)生成;`sdk/ws/awg_bridge/src/` 含 main.c 与 lscript.ld。

若 CLI 报 hdf 无法解析,改用解包目录:

```bash
mkdir -p sdk/hw && unzip -o awg/host_bd.hdf -d sdk/hw
cli-anything-vivado sdk app new awg_bridge --hw sdk/hw --src sdk/src --workspace sdk/ws
```

替换链接脚本为 OCM 版(关键!否则默认链接到 DDR 无法运行):

```bash
cp scripts/lscript_ocm.ld sdk/ws/awg_bridge/src/lscript.ld
grep -c "ps7_ddr_0" sdk/ws/awg_bridge/src/lscript.ld || echo "OK: no DDR refs"
```

Expected: `OK: no DDR refs`。

- [ ] **Step 4: 编译**

```bash
cli-anything-vivado sdk build awg_bridge --workspace sdk/ws --type all
```

Expected: `sdk/ws/awg_bridge/Debug/awg_bridge.elf` 生成;无链接错误。若链接超 OCM(192KB),检查 lscript 是否替换成功。

- [ ] **Step 5: 提交**

```bash
git add scripts/export_hdf.tcl sdk/src/main.c
git commit -m "feat: PS UART bridge app (AXI GPIO writer, ASCII protocol, OCM link)"
```

---

### Task 9: 板上集成测试(test_bridge.py,需硬件)

**Files:**
- Create: `scripts/test_bridge.py`
- Run: `cli-anything-vivado sdk run` + 脚本

- [ ] **Step 1: 写测试脚本**

`scripts/test_bridge.py`:

```python
#!/usr/bin/env python3
"""AC820 bridge protocol test: drive the full command set over serial and
assert exact responses. Usage: python scripts/test_bridge.py [COMx]
Requires: bitstream + bridge elf running (cli-anything-vivado sdk run)."""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(0.3)
ser.reset_input_buffer()
ser.reset_output_buffer()

fails = 0


def cmd(s, expect):
    global fails
    ser.write((s + "\r\n").encode())
    line = ser.readline().decode(errors="replace").strip()
    ok = line == expect
    if not ok:
        fails += 1
    print(("PASS" if ok else "FAIL"), repr(s), "->", repr(line), "(expect", repr(expect) + ")")


# 正常下发(小写验证大小写不敏感)
cmd("set ch1 wave sine", "OK")
cmd("set ch1 freq 100000", "OK")
cmd("set ch1 ampl 80", "OK")
cmd("SET CH2 WAVE SAW", "OK")
cmd("SET CH2 FREQ 2500", "OK")
cmd("SET CH2 AMPL 20", "OK")

# 状态回读
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20")

# 非法输入
cmd("SET CH1 FREQ 5", "ERR FREQ OUT OF RANGE")
cmd("SET CH1 FREQ 1000001", "ERR FREQ OUT OF RANGE")
cmd("SET CH1 FREQ abc", "ERR BAD VALUE")
cmd("SET CH1 AMPL 101", "ERR AMPL OUT OF RANGE")
cmd("SET CH1 AMPL abc", "ERR BAD VALUE")
cmd("SET CH1 WAVE BOGUS", "ERR BAD VALUE")
cmd("SET CH3 WAVE SINE", "ERR BAD CHANNEL")
cmd("SET CH1 FOO 5", "ERR BAD FIELD")
cmd("FROB CH1", "ERR UNKNOWN CMD")

# 错误后状态不变
cmd("GET CH1", "CH1 WAVE SINE FREQ 100000 AMPL 80")
cmd("GET CH2", "CH2 WAVE SAW FREQ 2500 AMPL 20")

print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
sys.exit(0 if fails == 0 else 1)
```

- [ ] **Step 2: 解包 hdf 并运行桥(需板卡 JTAG 在线)**

```bash
mkdir -p sdk/hw && unzip -o awg/host_bd.hdf -d sdk/hw
cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw --bit awg/awg.runs/impl_1/host_bd_wrapper.bit
```

Expected: 日志显示编程 PL + 下载 elf;串口(COM 口)输出 `AC820-AWG bridge ready`。

- [ ] **Step 3: 跑自动化测试**

```bash
python scripts/test_bridge.py COM5
```

(COM 号以设备管理器为准。)

Expected: 全部 PASS,`RESULT: ALL PASS`。

- [ ] **Step 4: 提交**

```bash
git add scripts/test_bridge.py
git commit -m "test: board-level bridge protocol test"
```

---

### Task 10: 上位机 protocol.py(TDD)

**Files:**
- Create: `host/siggen/__init__.py`、`host/siggen/protocol.py`
- Create: `host/tests/test_protocol.py`
- Create: `host/requirements.txt`

- [ ] **Step 1: 写失败测试**

`host/tests/test_protocol.py`:

```python
import pytest

from siggen.protocol import set_cmd, get_cmd, parse_response, parse_status


def test_set_wave():
    assert set_cmd(1, "wave", "SINE") == "SET CH1 WAVE SINE"
    assert set_cmd(2, "WAVE", "saw") == "SET CH2 WAVE SAW"   # 输入大小写不敏感


def test_set_freq():
    assert set_cmd(1, "freq", 100000) == "SET CH1 FREQ 100000"
    assert set_cmd(1, "freq", 10) == "SET CH1 FREQ 10"
    assert set_cmd(1, "freq", 1000000) == "SET CH1 FREQ 1000000"


def test_set_ampl():
    assert set_cmd(1, "ampl", 80) == "SET CH1 AMPL 80"
    assert set_cmd(1, "ampl", 0) == "SET CH1 AMPL 0"
    assert set_cmd(1, "ampl", 100) == "SET CH1 AMPL 100"


def test_set_validation():
    with pytest.raises(ValueError):
        set_cmd(3, "wave", "SINE")              # 坏通道
    with pytest.raises(ValueError):
        set_cmd(1, "wave", "BOGUS")             # 坏波形
    with pytest.raises(ValueError):
        set_cmd(1, "freq", 9)                   # 低于下限
    with pytest.raises(ValueError):
        set_cmd(1, "freq", 1000001)             # 高于上限
    with pytest.raises(ValueError):
        set_cmd(1, "ampl", -1)
    with pytest.raises(ValueError):
        set_cmd(1, "ampl", 101)
    with pytest.raises(ValueError):
        set_cmd(1, "foo", 1)                    # 坏字段


def test_get_cmd():
    assert get_cmd(1) == "GET CH1"
    assert get_cmd(2) == "GET CH2"
    with pytest.raises(ValueError):
        get_cmd(0)


def test_parse_response():
    assert parse_response("OK") == (True, "OK")
    assert parse_response("OK\r\n") == (True, "OK")
    ok, msg = parse_response("ERR FREQ OUT OF RANGE")
    assert ok is False and msg == "FREQ OUT OF RANGE"
    ok, msg = parse_response("ERR BAD VALUE")
    assert ok is False and msg == "BAD VALUE"
    ok, line = parse_response("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert ok is True
    ok, msg = parse_response("garbage")
    assert ok is False and "garbage" in msg


def test_parse_status():
    d = parse_status("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert d == {"ch": 1, "wave": "SINE", "freq_hz": 100000, "ampl_pct": 80}
    d = parse_status("CH2 WAVE SAW FREQ 2500 AMPL 20")
    assert d == {"ch": 2, "wave": "SAW", "freq_hz": 2500, "ampl_pct": 20}
```

`host/requirements.txt`:

```
PySide6
pyqtgraph
pyserial
pytest
```

- [ ] **Step 2: 运行确认失败**

```bash
cd <LOCAL_PATH>
python -m pytest tests/ -v
```

Expected: FAIL(ModuleNotFoundError: siggen)。

- [ ] **Step 3: 实现**

`host/siggen/protocol.py`:

```python
"""AC820 AWG 串口协议(上位机侧)。

ASCII 行协议,CR/LF 结尾,大小写不敏感,响应全大写:
  SET CH1|CH2 WAVE SINE|SQUARE|TRIANGLE|SAW   -> OK
  SET CH1|CH2 FREQ <10..1000000>              -> OK
  SET CH1|CH2 AMPL <0..100>                   -> OK
  GET CH1|CH2                                 -> CHx WAVE .. FREQ .. AMPL ..
错误响应: ERR <msg> (msg 见桥程序 main.c)。
"""

WAVES = ("SINE", "SQUARE", "TRIANGLE", "SAW")
FREQ_MIN, FREQ_MAX = 10, 1_000_000
AMPL_MIN, AMPL_MAX = 0, 100


def set_cmd(ch: int, field: str, value) -> str:
    """构造 SET 命令;非法参数抛 ValueError。"""
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    field = field.upper()
    if field == "WAVE":
        wave = str(value).upper()
        if wave not in WAVES:
            raise ValueError(f"bad wave: {value}")
        return f"SET CH{ch} WAVE {wave}"
    if field == "FREQ":
        freq = int(value)
        if not FREQ_MIN <= freq <= FREQ_MAX:
            raise ValueError(f"freq out of range: {value}")
        return f"SET CH{ch} FREQ {freq}"
    if field == "AMPL":
        ampl = int(value)
        if not AMPL_MIN <= ampl <= AMPL_MAX:
            raise ValueError(f"ampl out of range: {value}")
        return f"SET CH{ch} AMPL {ampl}"
    raise ValueError(f"bad field: {field}")


def get_cmd(ch: int) -> str:
    if ch not in (1, 2):
        raise ValueError(f"bad channel: {ch}")
    return f"GET CH{ch}"


def parse_response(line: str) -> tuple:
    """解析一行响应 -> (ok, text)。ok=True 表示 OK 或状态行。"""
    line = line.strip()
    if line == "OK":
        return True, "OK"
    if line.startswith("ERR "):
        return False, line[4:]
    if line.startswith("CH1 ") or line.startswith("CH2 "):
        return True, line
    return False, f"unparseable: {line!r}"


def parse_status(line: str) -> dict:
    """解析 'CHx WAVE .. FREQ .. AMPL ..' -> dict(ch, wave, freq_hz, ampl_pct)。"""
    parts = line.split()
    if len(parts) != 7 or parts[0] not in ("CH1", "CH2"):
        raise ValueError(f"bad status line: {line!r}")
    return {
        "ch": int(parts[0][2]),
        "wave": parts[2],
        "freq_hz": int(parts[4]),
        "ampl_pct": int(parts[6]),
    }
```

- [ ] **Step 4: 运行确认通过**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add host/
git commit -m "feat: host protocol module with pytest"
```

---

### Task 11: 上位机 transport + controller(TDD)

**Files:**
- Create: `host/siggen/transport.py`、`host/siggen/controller.py`
- Create: `host/tests/test_controller.py`

- [ ] **Step 1: 写失败测试**

`host/tests/test_controller.py`(用假 worker 注入,无需硬件):

```python
import pytest
from PySide6.QtCore import QCoreApplication

from siggen.controller import Controller

app = QCoreApplication([])


class FakeWorker:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def stop(self):
        pass


@pytest.fixture
def ctrl():
    c = Controller()
    c.worker = FakeWorker()
    return c


def test_set_field_sends_command(ctrl):
    ctrl.set_field(0, "freq", 100000)
    assert ctrl.worker.sent[-1] == "SET CH1 FREQ 100000\r\n"
    assert ctrl._pending == (0, "freq", 100000)


def test_set_field_invalid_no_send(ctrl):
    ctrl.set_field(0, "freq", 5)
    assert ctrl.worker.sent == []
    assert ctrl._pending is None


def test_not_connected_logs(ctrl):
    ctrl.worker = None
    logs = []
    ctrl.log.connect(lambda text, level: logs.append((text, level)))
    ctrl.set_field(0, "freq", 1000)
    assert logs and logs[0][1] == "error"


def test_ok_ack_applies_state(ctrl):
    ctrl.set_field(1, "wave", "SAW")
    ctrl._on_line("OK")
    assert ctrl.state[1].wave == "SAW"
    assert ctrl._pending is None


def test_err_ack_keeps_state(ctrl):
    ctrl.set_field(0, "freq", 100000)
    ctrl._on_line("ERR FREQ OUT OF RANGE")
    assert ctrl.state[0].freq_hz == 1000   # 默认未变
    assert ctrl._pending is None


def test_status_line_updates_state(ctrl):
    ctrl._on_line("CH1 WAVE SINE FREQ 100000 AMPL 80")
    assert ctrl.state[0].freq_hz == 100000
    assert ctrl.state[0].ampl_pct == 80


def test_timeout_clears_pending(ctrl):
    ctrl.set_field(0, "freq", 1000)
    assert ctrl._pending is not None
    ctrl._on_timeout()
    assert ctrl._pending is None
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest tests/test_controller.py -v
```

Expected: FAIL(ModuleNotFoundError: siggen.controller)。

- [ ] **Step 3: 实现**

`host/siggen/transport.py`:

```python
"""串口传输层:后台读线程 + 主线程写。断线/错误通过信号上报。"""
import serial
from PySide6.QtCore import QThread, Signal


class SerialWorker(QThread):
    line_received = Signal(str)
    connection_lost = Signal(str)

    def __init__(self, port: str, baud: int = 115200, parent=None):
        super().__init__(parent)
        self._port = port
        self._baud = baud
        self._ser = None
        self._quit = False

    def open(self) -> str | None:
        """打开串口;失败返回错误消息,成功返回 None。"""
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.2)
        except serial.SerialException as e:
            return str(e)
        return None

    def send(self, text: str) -> None:
        if self._ser is None:
            return
        try:
            self._ser.write(text.encode())
        except serial.SerialException as e:
            self.connection_lost.emit(str(e))
            self._close()

    def stop(self) -> None:
        self._quit = True
        self._close()
        self.wait(1000)

    def _close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except serial.SerialException:
                pass
            self._ser = None

    def run(self) -> None:
        while not self._quit:
            if self._ser is None:
                self.msleep(100)
                continue
            try:
                line = self._ser.readline()
            except serial.SerialException as e:
                self.connection_lost.emit(str(e))
                self._close()
                break
            if line:
                self.line_received.emit(line.decode(errors="replace").strip())
```

`host/siggen/controller.py`:

```python
"""连接与通道状态模型 + 命令流。UI 无关,可注入假 worker 单测。"""
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from .protocol import set_cmd, get_cmd, parse_response, parse_status

TIMEOUT_MS = 1000


@dataclass
class ChannelState:
    wave: str = "SINE"
    freq_hz: int = 1000
    ampl_pct: int = 50


class Controller(QObject):
    connected = Signal()
    disconnected = Signal(str)
    state_changed = Signal(int)        # ch 0/1
    log = Signal(str, str)             # (text, level: info/ok/error)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.state = {0: ChannelState(), 1: ChannelState()}
        self._pending = None           # (ch, field, value)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    # ---- 连接 ----
    def connect_serial(self, port: str, baud: int = 115200) -> str | None:
        from .transport import SerialWorker
        self.worker = SerialWorker(port, baud, self)
        err = self.worker.open()
        if err:
            self.worker = None
            return err
        self.worker.line_received.connect(self._on_line)
        self.worker.connection_lost.connect(self._on_lost)
        self.worker.start()
        self.connected.emit()
        self.log.emit(f"connected {port} @ {baud}", "ok")
        return None

    def disconnect(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self._pending = None
        self._timer.stop()
        self.disconnected.emit("disconnected")

    # ---- 命令流 ----
    def set_field(self, ch: int, field: str, value) -> None:
        """校验并发 SET。ch 为 0/1(界面索引)。"""
        if self.worker is None:
            self.log.emit("not connected", "error")
            return
        try:
            cmd = set_cmd(ch + 1, field, value)
        except ValueError as e:
            self.log.emit(str(e), "error")
            return
        self._pending = (ch, field, value)
        self.worker.send(cmd + "\r\n")
        self._timer.start(TIMEOUT_MS)
        self.log.emit(f">> {cmd}", "info")

    def request_status(self) -> None:
        if self.worker is None:
            return
        self.worker.send(get_cmd(1) + "\r\n")
        self.worker.send(get_cmd(2) + "\r\n")

    # ---- 响应处理 ----
    def _on_line(self, line: str) -> None:
        ok, text = parse_response(line)
        if line.startswith("CH1 ") or line.startswith("CH2 "):
            d = parse_status(line)
            st = self.state[d["ch"] - 1]
            st.wave, st.freq_hz, st.ampl_pct = d["wave"], d["freq_hz"], d["ampl_pct"]
            self.state_changed.emit(d["ch"] - 1)
            self.log.emit(f"<< {line}", "ok")
            return
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
                self.state_changed.emit(ch)
                self.log.emit(f"<< {line}", "ok")
            else:
                self.log.emit(f"<< {line}", "error")

    def _on_timeout(self) -> None:
        if self._pending is not None:
            self._pending = None
            self.log.emit("timeout: no response", "error")

    def _on_lost(self, msg: str) -> None:
        self._pending = None
        self._timer.stop()
        self.log.emit(f"connection lost: {msg}", "error")
        self.disconnected.emit(msg)
```

- [ ] **Step 4: 运行确认通过**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add host/siggen/transport.py host/siggen/controller.py host/tests/test_controller.py
git commit -m "feat: serial transport + controller state machine with tests"
```

---

### Task 12: 上位机预览 + 三栏 UI + 入口

**Files:**
- Create: `host/siggen/preview.py`
- Create: `host/siggen/ui/__init__.py`、`host/siggen/ui/widgets.py`、`host/siggen/ui/main_window.py`
- Create: `host/main.py`

- [ ] **Step 1: 波形预览(纯函数 + pyqtgraph)**

`host/siggen/preview.py`:

```python
"""理论波形预览:纯函数生成数据 + pyqtgraph 控件封装。"""
import numpy as np
import pyqtgraph as pg

PERIODS = 3   # 显示 3 个周期


def make_plot_data(wave: str, freq_hz: int, ampl_pct: int, n: int = 600):
    """返回 (t, y):t 为秒,y 为归一化幅度(ampl_pct/100 缩放)。"""
    if freq_hz <= 0:
        return np.zeros(n), np.zeros(n)
    t = np.linspace(0, PERIODS / freq_hz, n, endpoint=False)
    phase = (t * freq_hz) % 1.0
    if wave == "SINE":
        y = np.sin(2 * np.pi * phase)
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLabel("bottom", "time", units="s")
        self.setLabel("left", "amplitude")
        self.setYRange(-1.1, 1.1)
        self.showGrid(x=True, y=True)
        self._curve = self.plot([], [], pen=pg.mkPen("c", width=2))

    def set_params(self, wave: str, freq_hz: int, ampl_pct: int) -> None:
        t, y = make_plot_data(wave, freq_hz, ampl_pct)
        self._curve.setData(t, y)
```

- [ ] **Step 2: UI 组件**

`host/siggen/ui/widgets.py`:

```python
"""界面组件:连接栏 / 通道面板 / 状态面板。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from ..protocol import WAVES

PORTS = [f"COM{i}" for i in range(1, 17)]
BAUDS = ["9600", "115200", "460800"]


class ConnectionBar(QWidget):
    connect_requested = Signal(str, int)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = QComboBox()
        self.port.addItems(PORTS)
        self.baud = QComboBox()
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText("115200")
        self.btn = QPushButton("连接")
        self.status = QLabel("未连接")
        lay = QHBoxLayout(self)
        lay.addWidget(QLabel("串口"))
        lay.addWidget(self.port)
        lay.addWidget(QLabel("波特率"))
        lay.addWidget(self.baud)
        lay.addWidget(self.btn)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.btn.clicked.connect(self._on_click)

    def _on_click(self):
        if self.btn.text() == "连接":
            self.connect_requested.emit(self.port.currentText(), int(self.baud.currentText()))
        else:
            self.disconnect_requested.emit()

    def set_connected(self, on: bool):
        self.btn.setText("断开" if on else "连接")
        self.status.setText("已连接" if on else "未连接")
        self.status.setStyleSheet("color: green;" if on else "color: gray;")


class ChannelPanel(QWidget):
    """单通道控制:波形按钮 + 频率步进/输入 + 幅度滑块。"""
    param_changed = Signal(str, object)   # (field, value)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(title))

        # 波形按钮
        wave_lay = QHBoxLayout()
        self.wave_btns = {}
        for w in WAVES:
            b = QPushButton(w.capitalize())
            b.setCheckable(True)
            b.clicked.connect(lambda _, ww=w: self.param_changed.emit("wave", ww))
            self.wave_btns[w] = b
            wave_lay.addWidget(b)
        lay.addLayout(wave_lay)

        # 频率
        self.freq_label = QLabel("1000 Hz")
        self.freq_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        lay.addWidget(self.freq_label)
        freq_lay = QHBoxLayout()
        for text, delta in (("-10", -10), ("+10", 10), ("÷10", None), ("×10", None)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, d=delta: self._step(d))
            freq_lay.addWidget(b)
        self.freq_edit = QLineEdit()
        self.freq_edit.setPlaceholderText("Hz")
        self.freq_edit.returnPressed.connect(self._typed)
        freq_lay.addWidget(self.freq_edit)
        lay.addLayout(freq_lay)

        # 幅度
        self.ampl = QSlider(Qt.Horizontal)
        self.ampl.setRange(0, 100)
        self.ampl.setValue(50)
        self.ampl.valueChanged.connect(lambda v: self.param_changed.emit("ampl", v))
        self.ampl_label = QLabel("50%")
        ampl_lay = QHBoxLayout()
        ampl_lay.addWidget(QLabel("幅度"))
        ampl_lay.addWidget(self.ampl)
        ampl_lay.addWidget(self.ampl_label)
        lay.addLayout(ampl_lay)

    def _step(self, delta):
        try:
            cur = int(self.freq_label.text().split()[0])
        except (ValueError, IndexError):
            cur = 1000
        if delta is None:
            return
        self.param_changed.emit("freq", cur + delta)

    def _typed(self):
        try:
            v = int(self.freq_edit.text())
        except ValueError:
            return
        self.param_changed.emit("freq", v)
        self.freq_edit.clear()

    def set_state(self, wave: str, freq_hz: int, ampl_pct: int):
        for w, b in self.wave_btns.items():
            b.setChecked(w == wave)
        self.freq_label.setText(f"{freq_hz} Hz")
        self.ampl.setValue(ampl_pct)
        self.ampl_label.setText(f"{ampl_pct}%")


class StatusPanel(QWidget):
    """右栏:回读状态 + 波形预览 + 日志。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        from ..preview import WavePreview
        lay = QVBoxLayout(self)
        self.ch1_label = QLabel("CH1: --")
        self.ch2_label = QLabel("CH2: --")
        self.preview = WavePreview()
        self.log = QListWidget()
        lay.addWidget(QLabel("状态回读"))
        lay.addWidget(self.ch1_label)
        lay.addWidget(self.ch2_label)
        lay.addWidget(QLabel("波形预览"))
        lay.addWidget(self.preview, 1)
        lay.addWidget(QLabel("日志"))
        lay.addWidget(self.log, 1)

    def set_channel(self, ch: int, st):
        (self.ch1_label if ch == 0 else self.ch2_label).setText(
            f"CH{ch + 1}: {st.wave} {st.freq_hz} Hz {st.ampl_pct}%")

    def add_log(self, text: str, level: str = "info"):
        color = {"error": "red", "ok": "green"}.get(level, "black")
        item = self.log.currentItem()
        self.log.addItem(text)
        item = self.log.item(self.log.count() - 1)
        item.setForeground(color)
        self.log.scrollToBottom()
```

`host/siggen/ui/main_window.py`:

```python
"""三栏主窗口:连接栏 | 通道控制 | 状态+预览+日志。"""
from PySide6.QtWidgets import QMainWindow, QSplitter, QVBoxLayout, QWidget

from ..controller import Controller
from .widgets import ChannelPanel, ConnectionBar, StatusPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AC820 任意波形发生器 — 上位机")
        self.resize(1080, 720)

        self.ctrl = Controller(self)
        self.conn = ConnectionBar()
        self.ch1 = ChannelPanel("CH1 — DAC0")
        self.ch2 = ChannelPanel("CH2 — DAC1")
        self.status_panel = StatusPanel()

        splitter = QSplitter()
        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        mid_lay.addWidget(self.ch1)
        mid_lay.addWidget(self.ch2)
        splitter.addWidget(mid)
        splitter.addWidget(self.status_panel)
        splitter.setSizes([420, 600])

        root = QWidget()
        root_lay = QVBoxLayout(root)
        root_lay.addWidget(self.conn)
        root_lay.addWidget(splitter, 1)
        self.setCentralWidget(root)

        # UI -> controller
        self.conn.connect_requested.connect(self.ctrl.connect_serial)
        self.conn.disconnect_requested.connect(self.ctrl.disconnect)
        for panel, ch in ((self.ch1, 0), (self.ch2, 1)):
            panel.param_changed.connect(
                lambda field, value, c=ch: self.ctrl.set_field(c, field, value))

        # controller -> UI
        self.ctrl.connected.connect(lambda: self.conn.set_connected(True))
        self.ctrl.disconnected.connect(lambda _msg: self.conn.set_connected(False))
        self.ctrl.state_changed.connect(self._on_state)
        self.ctrl.log.connect(self.status_panel.add_log)

    def _on_state(self, ch: int):
        st = self.ctrl.state[ch]
        panel = self.ch1 if ch == 0 else self.ch2
        panel.set_state(st.wave, st.freq_hz, st.ampl_pct)
        self.status_panel.set_channel(ch, st)
        self.status_panel.preview.set_params(st.wave, st.freq_hz, st.ampl_pct)

    def closeEvent(self, event):
        self.ctrl.disconnect()
        super().closeEvent(event)
```

`host/siggen/ui/__init__.py`(空)、`host/siggen/__init__.py` 已存在。

`host/main.py`:

```python
#!/usr/bin/env python3
"""AC820 任意波形发生器上位机入口。"""
import sys

from PySide6.QtWidgets import QApplication

from siggen.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 安装依赖 + 启动验证**

```bash
cd <LOCAL_PATH>
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python main.py
```

Expected: 窗口打开,三栏可见;未连接时操作控件 → 日志红色 "not connected";CH1/CH2 面板可切换波形(按钮高亮)。

- [ ] **Step 4: 提交**

```bash
git add host/
git commit -m "feat: PySide6 three-column GUI + pyqtgraph waveform preview"
```

---

### Task 13: E2E 全链路 + README 更新

**Files:**
- Modify: `README.md`
- Run: 全链路验证

- [ ] **Step 1: 全链路验证(需板卡)**

1. `cli-anything-vivado sdk run --elf sdk/ws/awg_bridge/Debug/awg_bridge.elf --hw-platform sdk/hw --bit awg/awg.runs/impl_1/host_bd_wrapper.bit`
2. `python scripts/test_bridge.py COM5` → ALL PASS
3. 启动上位机,连接串口 → 状态回读显示板卡当前参数
4. 界面操作:CH1 正弦 100kHz 80% → 示波器 DAC0 观察;CH2 方波 10kHz 50% → DAC1
5. 断线(拔 USB)→ 界面提示,重插重连正常

- [ ] **Step 2: 更新 README**

`README.md` 整体替换:

```markdown
# AC820 任意波形发生器(上位机控制版)

PC 上位机(Python + PySide6)通过 USB 串口控制 AC820 + ACM2108 双通道信号发生器:
波形(正弦/方波/三角/锯齿)、频率(10Hz~1MHz)、幅度(0~100%)独立可调,支持状态回读与波形预览。

## 架构
上位机 → CH9102F 串口(PS MIO48/49,115200)→ PS 裸机桥(ASCII 命令 → AXI GPIO 2×32bit)
→ PL reg_bridge → 双通道 DDS → 幅度缩放 → ACM2108 DAC0/DAC1。PS 无 DDR,程序跑 OCM。

## 使用
1. 下载:JTAG 下比特流 + 运行桥程序(见 scripts/export_hdf.tcl 与 sdk/src/main.c,或 cli-anything-vivado sdk run)
2. 上位机:host/.venv/Scripts/python host/main.py,选串口连接
3. 界面:CH1/CH2 独立设波形/频率/幅度;右侧状态回读 + 波形预览 + 日志

## 串口协议(115200 8N1,CR/LF 结尾,大小写不敏感)
SET CH1 WAVE SINE | SET CH2 FREQ 100000 | SET CH1 AMPL 80 | GET CH1
错误响应:ERR <原因>。完整命令集与用例:scripts/test_bridge.py

## 构建
- PL:cli-anything-vivado 全流程;BD 由 scripts/create_bd.tcl 生成(顶层 host_bd_wrapper)
- PS:cli-anything-vivado sdk app new/build(sdk/src/main.c;lscript 用 scripts/lscript_ocm.ld OCM 版)

## 调试记录(重要)
- ACM2108 DAC 期望偏移二进制(0..255,128=中点);补码直出导致正弦/三角 V 形错位、
  方波归零。修复:dac_driver 内 sample ^ 8'h80。
- PS 无 DDR:程序跑 OCM(资料包附录1 无DDR 例程方案);lscript 默认链 DDR 会失败。
- BD 顶层用 wrapper(awg_top 作为 BD 内模块),避免未约束顶层端口 UCIO-1。
- 板级调试方法论:网表仿真 → 引脚核对 → CSV 分析 → 参考设计(见 ac820-board 技能)。
```

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README for host-controlled AWG"
```

---

## Self-Review 记录

- **Spec 覆盖**:§4 PS 侧(BD/打包/桥/上电默认)→ Task 6/8;§5 协议 → Task 8(main.c 实现)+ Task 9(板测)+ Task 10(上位机解析);§6 PL 改造(reg_bridge/双 DDS/幅度/偏移二进制)→ Task 1~5;§7 上位机框架(协议/传输/控制器/预览/三栏)→ Task 10~12;§8 错误处理(ERR 集合/超时/断线)→ Task 8/9/11;§9 测试(TB + 板测 + pytest + E2E)→ 各任务;§10 交付流程 → Task 6~8;§11 风险项全部落地(ps7_props 已提取验证、OCM lscript 已固化、AXI GPIO 双通道属性在 create_bd.tcl、2-FF 同步在 reg_bridge、双保险默认值在 DEF 常量 + main 默认写)。
- **占位符扫描**:无 TBD/TODO;每个代码步骤含完整代码与期望输出。
- **类型一致性**:打包格式 freq|ampl<<20|wave<<28 在 reg_bridge.v、main.c(pack_word)、TB(pack 函数)三处一致;协议命令串在 main.c / test_bridge.py / protocol.py 一致(`SET CH1 WAVE SINE`、`GET CH1`、`CH1 WAVE SINE FREQ 100000 AMPL 80`、`ERR FREQ OUT OF RANGE` 等);controller 信号(state_changed/log 等)在 Task 11/12 一致。
