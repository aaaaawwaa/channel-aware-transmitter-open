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

    // drive inputs, wait 3 cycles (2-stage pipe + margin), then check
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
        drive_check(-8'sd127, 8'd100, -8'sd127, "-127*100% = -127");
        drive_check(-8'sd127, 8'd50,  -8'sd63,  "-127*50% = -63 (trunc)");
        drive_check( 8'd127, 8'd0,    8'd0,   "127*0% = 0");
        drive_check( 8'd0,   8'd100,  8'd0,   "0*100% = 0");
        // -128 is the extreme negative: sample_in=0x80 maps to $signed(-128),
        // DUT computes -128*100/100=-128, output is 0x80.
        // The drive_check $signed comparison has a Vivado 2018.3 quirk with
        // out-of-range 8'sd128 literals, so we check the raw bit pattern.
        begin
            @(posedge clk); #1;
            sample_in = 8'd128;
            ampl_pct  = 8'd100;
            repeat (3) @(posedge clk); #1;
            chk("-128*100% = -128", sample_out == 8'd128);
        end

        // 背靠背吞吐:每拍驱动新样本,输出应每拍更新(无气泡)
        begin : throughput
            integer i;
            reg [7:0] last_out;
            integer gaps;
            @(posedge clk); #1;
            sample_in = 8'sd0;
            ampl_pct  = 8'd100;
            repeat (2) @(posedge clk);   // 流水填充
            // 再走2拍刷新流水线,使输出对应新的样本流
            repeat (2) begin
                @(posedge clk); #1;
                sample_in = sample_in + 8'sd1;
            end
            // 背靠背驱动10拍,输出应该每拍都变
            last_out = sample_out;
            gaps = 0;
            for (i = 0; i < 10; i = i + 1) begin
                @(posedge clk); #1;
                sample_in = sample_in + 8'sd1;
                if (sample_out === last_out) gaps = gaps + 1;
                last_out = sample_out;
            end
            chk("back-to-back no pipeline bubbles", gaps == 0);
        end

        if (errs == 0) $display("PASS ampl_scale_tb");
        else $display("FAIL ampl_scale_tb: %0d errors", errs);
        $finish;
    end
endmodule
