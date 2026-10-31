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
    integer window_rises = 0;
    reg [7:0] exp_d0 = 8'h80;  // matches DUT reset (midpoint)
    reg [7:0] exp_d1 = 8'h80;
    reg window_en = 0;

    always @(posedge clk) begin
        if (rst_n) begin
            exp_d0 <= sample0 ^ 8'h80;
            exp_d1 <= sample1 ^ 8'h80;
        end

    end
    always @(posedge daclk0) begin
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
