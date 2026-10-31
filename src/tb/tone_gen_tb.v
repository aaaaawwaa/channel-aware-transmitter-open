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
