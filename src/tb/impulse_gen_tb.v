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
