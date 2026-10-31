`timescale 1ns / 1ps
// AM 增益级: sample_out = sat(sample_in × am_gain / 12700), 2 拍流水。
// am_gain=12700 时输出与输入逐位一致(直通);除法截断向零。
module am_stage (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] sample_in,          // signed
    input  wire signed [16:0] am_gain,    // 12700 + m×am_depth
    output reg  [7:0] sample_out          // signed, 饱和 ±127
);
    reg signed [24:0] prod;               // 8×17 = 25 位
    wire signed [24:0] scaled = prod / 25'sd12700;   // 截断向零

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            prod       <= 25'sd0;
            sample_out <= 8'sd0;
        end else begin
            prod <= $signed(sample_in) * am_gain;
            sample_out <= (scaled > 25'sd127) ? 8'sd127 :
                          (scaled < -25'sd128) ? 8'sd128 : scaled[7:0];
        end
endmodule
