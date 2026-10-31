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
            dac0_db <= 8'h80;   // offset-binary midpoint (~0V) during reset
            dac1_db <= 8'h80;
        end else begin
            dac0_db <= sample0 ^ 8'h80;
            dac1_db <= sample1 ^ 8'h80;
        end

    // NOTE: cannot register ~clk on posedge clk (would latch constant 0);
    // combinational inversion gives the rising edge at the clk falling edge.
    assign daclk0 = ~clk;
    assign daclk1 = ~clk;
endmodule
