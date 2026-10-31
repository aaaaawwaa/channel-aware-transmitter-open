`timescale 1ns / 1ps
// 静态多径实数 FIR: y[n] = sat(round(Σ g_k·x[n−d_k] / 256), ±127)
// g_k 9-bit Q8 有符号 (−256..255, 单位增益 = 255), d_k 9-bit 无符号。
// 每抽头一条 512x8 分布式 RAM,广播写、并行读; d_k=0 直接取当前输入(避开写读同址)。
// 3 拍流水: 乘 -> 平衡加法树 -> 舍入饱和。
// 内部位宽: 每抽头积 17-bit (gain −256 × sample −128 = +32768, 16-bit 会溢出);
//           累加 20-bit 有符号 (±131072)。
module fade_fir #(
    parameter GAIN_W = 9,
    parameter DELAY_W = 9,
    parameter TAPS = 4,
    parameter DEPTH = 512
)(
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] sample_in,
    input  wire [TAPS*GAIN_W-1:0] tap_gain,
    input  wire [TAPS*DELAY_W-1:0] tap_delay,
    output reg  [7:0] sample_out
);
    localparam ADDR_W = $clog2(DEPTH);

    reg [ADDR_W-1:0] wr_ptr;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) wr_ptr <= {ADDR_W{1'b0}};
        else        wr_ptr <= wr_ptr + 1'b1;

    wire signed [GAIN_W-1:0] gain_k  [0:TAPS-1];
    wire [DELAY_W-1:0]       delay_k [0:TAPS-1];
    wire signed [7:0]        tap_src [0:TAPS-1];

    genvar k;
    generate
        for (k = 0; k < TAPS; k = k + 1) begin : g_tap
            assign gain_k[k]  = tap_gain[k*GAIN_W +: GAIN_W];
            assign delay_k[k] = tap_delay[k*DELAY_W +: DELAY_W];
            reg [7:0] mem [0:DEPTH-1];
            wire [ADDR_W-1:0] rd_addr = wr_ptr - delay_k[k][ADDR_W-1:0];
            always @(posedge clk) mem[wr_ptr] <= sample_in;
            assign tap_src[k] = (delay_k[k] == {DELAY_W{1'b0}}) ? $signed(sample_in)
                                                                : $signed(mem[rd_addr]);
        end
    endgenerate

    reg signed [16:0] prod_r [0:TAPS-1];   // R11: 17-bit, −256×−128 = +32768 fits
    always @(posedge clk) begin : g_prod
        integer t;
        for (t = 0; t < TAPS; t = t + 1)
            prod_r[t] <= gain_k[t] * tap_src[t];
    end

    reg signed [19:0] acc_r;               // R11: 20-bit signed, ±131072
    always @(posedge clk) begin
        acc_r <= (prod_r[0] + prod_r[1]) + (prod_r[2] + prod_r[3]);  // 平衡树, 不串 DSP48
    end

    function signed [7:0] round_sat;
        input signed [19:0] v;
        reg signed [19:0] m;
        reg signed [19:0] r;
        begin
            if (v >= 0) r = (v + 128) >>> 8;
            else begin m = -v; r = -((m + 128) >>> 8); end
            if (r > 127)        round_sat = 8'sd127;
            else if (r < -127)  round_sat = -8'sd127;   // R10: 对称 ±127
            else                round_sat = r[7:0];
        end
    endfunction

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample_out <= 8'sd0;
        else        sample_out <= round_sat(acc_r);
endmodule
