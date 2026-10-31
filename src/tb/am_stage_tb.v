`timescale 1ns / 1ps
// am_stage TB — 流水线为 2 拍:
//   第 1 拍 posedge: prod       <= sample_in × am_gain
//   第 2 拍 posedge: sample_out <= sat(prod / 12700)
// 因此每个激励 push() 需推进 2 拍后, sample_out 才反映该激励值。
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

    // 置激励并推进 2 拍(穿过 prod -> sample_out 两级流水)
    task push;
        input [7:0] val;
        begin
            sample_in = val;
            step; step;
        end
    endtask

    // 期望值: trunc(sample×gain/12700), 饱和 ±127
    function [7:0] exp_val;
        input signed [31:0] p;
        reg signed [31:0] q;
        begin
            q = p / 32'sd12700;             // 截断向零
            if (q > 32'sd127) exp_val = 8'sd127;
            else if (q < -32'sd128) exp_val = 8'sd128;
            else exp_val = q[7:0];
        end
    endfunction

    initial begin
        #100 rst_n = 1;
        #100;
        begin : t1
            integer i, bad;
            bad = 0;
            for (i = -128; i <= 127; i = i + 1) begin
                push(i[7:0]);
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
                push(i[7:0]);
                p = i * 25400;
                if (sample_out !== exp_val(p)) bad = bad + 1;
            end
            chk("gain=25400 saturating x2", bad == 0);
        end
        am_gain = 17'sd0;
        push(8'sd100);
        chk("gain=0 -> 0", sample_out == 8'sd0);
        am_gain = 17'sd12701;
        push(8'sd1);
        chk("trunc 12701/12700 -> 1", sample_out == 8'sd1);
        push(-8'sd1);
        chk("trunc -12701/12700 -> -1", sample_out == -8'sd1);
        am_gain = 17'sd19050;
        push(8'sd1);
        chk("trunc 19050/12700=1.5 -> 1", sample_out == 8'sd1);

        if (errs == 0) $display("PASS am_stage_tb");
        else $display("FAIL am_stage_tb: %0d errors", errs);
        $finish;
    end
endmodule
