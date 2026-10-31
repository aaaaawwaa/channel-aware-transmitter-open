`timescale 1ns / 1ps
// chan_cfg TB:
// - 伪 BRAM 为 **同步** 读模型(R12): doutb 经一拍寄存, 绝不用组合 case,
//   否则会掩盖真硬件上一拍的地址/数据错位。
// - 单位增益为 255(R2, 9-bit 有符号满幅), 不是 256。
// 覆盖: 未 commit 全 0 / CH1 单独 commit / CH2 单独 commit / 两通道同时 commit 都不丢 /
//       CH1/CH2 各自排空 tone(字 12/14)与 impulse(字 13/15)含负数幅度 /
//       I/Q 级参数(字 8/9/10, 通道无关)含负系数保号。
//
// ⚠ 每通道排空 9 个 word(6 个通道字 + 3 个通道无关的 I/Q 字) = 1 + 9x2 = 19 拍,
//   比原来的 6 字(13 拍)长。wait_cycles 的取值必须覆盖这个长度, 否则用例会在
//   排空途中取样而变成"偶发失败"。
module chan_cfg_tb;
    reg clk = 0, rst_n = 0;
    reg ch1_commit = 0, ch2_commit = 0;
    wire [4:0]  tap_b_addr;
    wire        tap_b_en;
    reg  [31:0] tap_b_rdata = 0;

    wire [35:0] ch1_gain, ch1_delay, ch2_gain, ch2_delay;

    wire [23:0] ch1_tone_fword, ch2_tone_fword;
    wire [6:0]  ch1_tone_ampl,  ch2_tone_ampl;
    wire [15:0] ch1_imp_period, ch2_imp_period;
    wire [7:0]  ch1_imp_width,  ch2_imp_width;
    wire signed [7:0] ch1_imp_ampl, ch2_imp_ampl;
    wire [31:0] iq_coef_ab, iq_coef_cd, iq_fword;

    chan_cfg uut (
        .clk(clk), .rst_n(rst_n),
        .ch1_commit(ch1_commit), .ch2_commit(ch2_commit),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay),
        .ch1_tone_fword(ch1_tone_fword), .ch2_tone_fword(ch2_tone_fword),
        .ch1_tone_ampl(ch1_tone_ampl), .ch2_tone_ampl(ch2_tone_ampl),
        .ch1_imp_period(ch1_imp_period), .ch2_imp_period(ch2_imp_period),
        .ch1_imp_width(ch1_imp_width), .ch2_imp_width(ch2_imp_width),
        .ch1_imp_ampl(ch1_imp_ampl), .ch2_imp_ampl(ch2_imp_ampl),
        .iq_coef_ab(iq_coef_ab), .iq_coef_cd(iq_coef_cd), .iq_fword(iq_fword));

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
    end
    always @(posedge clk) tap_b_rdata <= mem[tap_b_addr];

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

        // Phase A: CH1 单独 commit
        @(negedge clk); ch1_commit = 1;
        wait_cycles(40);
        chk("ch1 t0 gain",  `G(ch1_gain,0) == 9'sd255);
        chk("ch1 t0 delay", `D(ch1_delay,0) == 9'd0);
        chk("ch1 t1 delay", `D(ch1_delay,1) == 9'd1);
        chk("ch1 t2 gain",  `G(ch1_gain,2) == 9'sd255);
        chk("ch1 t3 delay", `D(ch1_delay,3) == 9'd3);
        chk("ch2 untouched A", ch2_gain == 36'd0 && ch2_delay == 36'd0);

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
        @(negedge clk); ch1_commit = 0; ch2_commit = 0;
        wait_cycles(80);
        chk("both ch1 t0 delay", `D(ch1_delay,0) == 9'd10);
        chk("both ch1 t3 delay", `D(ch1_delay,3) == 9'd13);
        chk("both ch1 gain",     `G(ch1_gain,0) == 9'sd255);
        chk("both ch2 t0 delay", `D(ch2_delay,0) == 9'd20);
        chk("both ch2 t3 delay", `D(ch2_delay,3) == 9'd23);
        chk("both ch2 gain",     `G(ch2_gain,0) == 9'sd129);

        // Phase D: CH1 commit 一并排空字 12(tone) / 13(impulse)
        // 字 12: fword[23:0] | ampl[30:24]
        // 字 13: period[15:0] | width[23:16] | ampl[31:24] (ampl 有符号)
        // 用拼接而不是 32'd(...) —— Verilog 的 sized 十进制字面量里不能写表达式。
        mem[12] = {8'd40,  24'd33554};                 // ampl=+40
        mem[13] = {8'd50,  8'd2, 16'd10};              // ampl=+50, width=2, period=10
        @(negedge clk); ch1_commit = 1;      // 上升沿 -> 触发 CH1 排空
        wait_cycles(50);
        chk("ch1 tone fword", ch1_tone_fword == 24'd33554);
        chk("ch1 tone ampl",  ch1_tone_ampl  == 7'd40);
        chk("ch1 imp period", ch1_imp_period == 16'd10);
        chk("ch1 imp width",  ch1_imp_width  == 8'd2);
        chk("ch1 imp ampl",   ch1_imp_ampl   == 8'sd50);
        // 负幅度要保住符号位 (206 = -50 的 8 位补码)
        mem[13] = {8'd206, 8'd2, 16'd10};
        @(negedge clk); ch1_commit = 0;
        @(negedge clk); ch1_commit = 1;      // 一个新的上升沿
        wait_cycles(50);
        chk("ch1 imp ampl signed", ch1_imp_ampl == -8'sd50);

        // Phase E: CH2 也要排空字 14/15, 且不碰 CH1
        mem[14] = {8'd100, 24'd3355443};               // ampl=+100
        mem[15] = {8'd226, 8'd8, 16'd1000};            // ampl=-30 (226 = -30 补码)
        @(negedge clk); ch2_commit = 1;      // 上升沿 -> 触发 CH2 排空
        wait_cycles(50);
        chk("ch2 tone fword", ch2_tone_fword == 24'd3355443);
        chk("ch2 tone ampl",  ch2_tone_ampl  == 7'd100);
        chk("ch2 imp period", ch2_imp_period == 16'd1000);
        chk("ch2 imp width",  ch2_imp_width  == 8'd8);
        chk("ch2 imp ampl",   ch2_imp_ampl   == -8'sd30);
        chk("ch1 tone untouched E", ch1_tone_fword == 24'd33554);

        // Phase F: I/Q 级参数(字 8/9/10)。iq_impair 是**通道对级单实例**, 这三个字
        // 与通道无关, 故任一通道的 commit 都应刷新它们(幂等)。
        // 字 8 = A[15:0] | B[31:16] (各 Q2.14 有符号); 字 9 = C[15:0] | D[31:16]。
        // 取 D 为负, 专门验负系数保号 —— unsigned 截断会在这里露馅。
        mem[8]  = 32'h00004000;   // A = +16384 (Q2.14 的 1.0), B = 0
        mem[9]  = 32'hC0000000;   // C = 0, D = -16384
        mem[10] = 32'h40000000;   // fword = 2^30
        @(negedge clk); ch1_commit = 0;
        @(negedge clk); ch1_commit = 1;          // CH1 新边沿
        wait_cycles(50);
        chk("iq coef_ab", iq_coef_ab == 32'h00004000);
        chk("iq coef_cd", iq_coef_cd == 32'hC0000000);
        chk("iq fword",   iq_fword   == 32'h40000000);
        chk("iq A signed positive", $signed(iq_coef_ab[15:0]) == 16'sd16384);
        chk("iq D signed negative", $signed(iq_coef_cd[31:16]) == -16'sd16384);

        // 通道无关性: 换一组完全不同的值, 由 **CH2** 的 commit 刷新
        mem[8]  = 32'h00002000;   // A = +8192
        mem[9]  = 32'h40000000;   // D = +16384
        mem[10] = 32'h80000000;   // fword 为负
        @(negedge clk); ch2_commit = 0;
        @(negedge clk); ch2_commit = 1;          // CH2 新边沿
        wait_cycles(50);
        chk("iq via ch2 coef_ab", iq_coef_ab == 32'h00002000);
        chk("iq via ch2 coef_cd", iq_coef_cd == 32'h40000000);
        chk("iq via ch2 fword",   iq_fword   == 32'h80000000);
        chk("ch1 tone untouched F", ch1_tone_fword == 24'd33554);
        chk("ch2 tone untouched F", ch2_tone_fword == 24'd3355443);

        if (errs == 0) $display("PASS chan_cfg");
        else $display("FAIL chan_cfg (%0d errors)", errs);
        $finish;
    end

    initial begin
        #100000;
        $display("FAIL chan_cfg (timeout)");
        $finish;
    end
endmodule
