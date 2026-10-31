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
