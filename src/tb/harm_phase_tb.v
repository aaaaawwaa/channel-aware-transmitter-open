`timescale 1ns / 1ps
// 相位对齐专项: base=50 + h1(ord=2,amp=50), 1kHz, dump 100000 拍 sample
// 到 harm_out.txt, 供 Python 分析谐波相对基波相位差。
module harm_phase_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] fword = 0;
    reg [7:0]  base = 0;
    reg [3:0]  h1_ord = 0;
    reg [7:0]  h1_amp = 0;
    reg signed [8:0] pm_off = 0;
    wire [7:0] sample;

    harm_synth uut (
        .clk(clk), .rst_n(rst_n), .fword(fword), .pm_off(pm_off), .base(base),
        .h1_ord(h1_ord), .h2_ord(4'd0), .h3_ord(4'd0), .h4_ord(4'd0),
        .h1_amp(h1_amp), .h2_amp(8'd0), .h3_amp(8'd0), .h4_amp(8'd0),
        .sample(sample)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer fh;
    integer i;

    initial begin
        #100 rst_n = 1;
        fword = 32'd85900;   // ~1kHz
        base = 8'd50;
        h1_ord = 4'd2;
        h1_amp = 8'd50;
        repeat (20) @(posedge clk);
        fh = $fopen("harm_out.txt", "w");
        for (i = 0; i < 100000; i = i + 1) begin
            @(posedge clk); #1;
            $fwrite(fh, "%d\n", $signed(sample));
        end
        $fclose(fh);
        $display("dump done");
        $finish;
    end
endmodule
