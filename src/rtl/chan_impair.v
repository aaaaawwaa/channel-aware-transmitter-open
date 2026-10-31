`timescale 1ns / 1ps
// 信道损伤: fade_fir -> (+ noise_gen + tone_gen + impulse_gen) -> ±127 饱和。
// enable=0: sample_out 直接寄存 sample_in, 逐位透传(1 拍)。
// enable=1: fade_fir(乘 -> 平衡加法树 -> 舍入饱和)与噪声/单音/冲激三个干扰源
//   各自延迟 3 拍后在加法节点汇合; 干扰在 fade_fir **之后**加入(接收端外部信号,
//   不应再过信道 FIR)。相加后经 ±127 对称饱和写入 sample_out;
//   实测端到端 3 拍(TB 中 ov[n+3] 对应输入 x[n])。
module chan_impair #(
    parameter [31:0] SEED = 32'h00C0FFEE
)(
    input  wire clk,
    input  wire rst_n,
    input  wire enable,
    input  wire [7:0] sample_in,
    input  wire [35:0] tap_gain,
    input  wire [35:0] tap_delay,
    input  wire [6:0] noise_level,
    // 新增: 单音干扰（tone_gen, 3 拍）
    input  wire        tone_en,
    input  wire [23:0] tone_fword,
    input  wire [6:0]  tone_ampl,
    // 新增: 周期冲激干扰（impulse_gen, 3 拍）
    input  wire        impulse_en,
    input  wire [15:0] impulse_period,
    input  wire [7:0]  impulse_width,
    input  wire signed [7:0] impulse_ampl,
    output reg  [7:0] sample_out
);
    wire [7:0] fade_out;
    wire signed [10:0] noise_raw;
    wire signed [7:0]  tone_raw;
    wire signed [7:0]  impulse_raw;

    fade_fir u_fade (
        .clk(clk), .rst_n(rst_n), .sample_in(sample_in),
        .tap_gain(tap_gain), .tap_delay(tap_delay), .sample_out(fade_out));
    noise_gen #(.SEED(SEED)) u_noise (
        .clk(clk), .rst_n(rst_n), .enable(enable), .level(noise_level),
        .noise_out(noise_raw));
    // 干扰与噪声同位置(都在 fade_fir 之后): 干扰是接收端叠加的外部信号,
    // 不应再过信道 FIR。四个来源均为 3 拍, 第 n 拍输出同拍有效。
    tone_gen u_tone (
        .clk(clk), .rst_n(rst_n), .enable(enable && tone_en),
        .fword(tone_fword), .ampl(tone_ampl), .tone_out(tone_raw));
    impulse_gen u_impulse (
        .clk(clk), .rst_n(rst_n), .enable(enable && impulse_en),
        .period(impulse_period), .width(impulse_width), .ampl(impulse_ampl),
        .impulse_out(impulse_raw));

    // 13-bit 有符号: fade(±127) + noise(约 ±438) + tone(±127) + impulse(±127) -> ±819,
    //   13-bit 范围 ±4096 充裕。
    wire signed [12:0] sum = $signed({fade_out[7], fade_out})
                           + $signed({{2{noise_raw[10]}}, noise_raw})
                           + $signed({{5{tone_raw[7]}}, tone_raw})
                           + $signed({{5{impulse_raw[7]}}, impulse_raw});
    wire signed [7:0] clamped = (sum > 13'sd127)  ?  8'sd127 :
                                (sum < -13'sd127) ? -8'sd127 : sum[7:0];

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample_out <= 8'sd0;
        else        sample_out <= enable ? clamped : sample_in;
endmodule
