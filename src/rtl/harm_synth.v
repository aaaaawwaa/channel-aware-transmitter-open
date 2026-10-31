`timescale 1ns / 1ps
// Harmonic synthesizer (sine only): base + up to 4 harmonics.
// out = (base*sine0 + SUM_k hk_amp*sine(ord_k*phase)) / 100, ~4-cycle pipeline.
// Amplitudes are NORMALIZED upstream (sum <= 100) so the result never
// overflows +-127; the final divide is defensive truncation.
// NOTE: amplitudes must be normalized upstream (sum <= 100). Un-normalized
// inputs (sum > 100) WRAP on the final 8-bit truncation (no saturation).
module harm_synth (
    input  wire clk,
    input  wire rst_n,
    input  wire [31:0] fword,
    input  wire [7:0]  base,       // 0..100 (normalized)
    input  wire [3:0]  h1_ord,     // 0=off, 2..8
    input  wire [3:0]  h2_ord,
    input  wire [3:0]  h3_ord,
    input  wire [3:0]  h4_ord,
    input  wire [7:0]  h1_amp,     // 0..100 (normalized)
    input  wire [7:0]  h2_amp,
    input  wire [7:0]  h3_amp,
    input  wire [7:0]  h4_amp,
    input  wire signed [8:0] pm_off,   // PM 相位偏移(地址 LSB, 0=直通)
    input  wire phase_ext_valid,   // 外部相位注入(相位联动主从)
    input  wire [31:0] phase_ext,  // 主通道相位 + 偏移(谐波与基波一起移相)
    output reg  [7:0]  sample      // signed two's complement
);
    // 谐波相位从基波累加器派生: ph_k = acc0 * ord_k (36-bit 乘积, 高字节取相位),
    // 保证 θk ≡ ord_k*θ0 恒成立(谐波与基波永远锁相, 与 GUI 理论同相一致)。
    // 不能用独立谐波累加器: ord 从 0 变非 0 时其从冻结值启动, 与已积累的
    // acc0 相位错位 -> 谐波相对基波相位随机偏移(实测 -96 deg)。
    reg [31:0] acc0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) acc0 <= 32'd0;
        else if (phase_ext_valid) acc0 <= phase_ext;
        else        acc0 <= acc0 + fword;

    wire [35:0] ph1 = acc0 * h1_ord;
    wire [35:0] ph2 = acc0 * h2_ord;
    wire [35:0] ph3 = acc0 * h3_ord;
    wire [35:0] ph4 = acc0 * h4_ord;

    // sine lookups (phase high byte)
    // 板上实测: 偶次谐波相对基波反相 180°(奇次同相), 功能仿真/网表均同相;
    // 为与 GUI 理论(同相)一致, 仅对偶次(ord[0]=0)做地址 MSB 翻转
    // (^0x80 = 相位+180°; sine_rom 满足 mem[addr+128] = -mem[addr], 等价谐波取反)。
    // 注意: 全量 ^0x80 会把原本正确的奇次也翻转(此前 bug), 必须按 ord 奇偶条件翻转。
    // PM 注入: 查表地址加偏移(mod 256), 谐波偏移 = ord×pm_off(与 θk≡k·θ0 一致)。
    // 偶次翻转在偏移之后进行;pm_off=0 时与旧版逐位一致。
    wire signed [13:0] off1 = $signed(pm_off) * $signed({1'b0, h1_ord});
    wire signed [13:0] off2 = $signed(pm_off) * $signed({1'b0, h2_ord});
    wire signed [13:0] off3 = $signed(pm_off) * $signed({1'b0, h3_ord});
    wire signed [13:0] off4 = $signed(pm_off) * $signed({1'b0, h4_ord});
    wire [7:0] s0 = acc0[31:24] + pm_off[7:0];
    wire [7:0] s1_pre = (ph1[31:24] + off1[7:0]);
    wire [7:0] s2_pre = (ph2[31:24] + off2[7:0]);
    wire [7:0] s3_pre = (ph3[31:24] + off3[7:0]);
    wire [7:0] s4_pre = (ph4[31:24] + off4[7:0]);
    wire [7:0] s1 = h1_ord[0] ? s1_pre : s1_pre ^ 8'h80;
    wire [7:0] s2 = h2_ord[0] ? s2_pre : s2_pre ^ 8'h80;
    wire [7:0] s3 = h3_ord[0] ? s3_pre : s3_pre ^ 8'h80;
    wire [7:0] s4 = h4_ord[0] ? s4_pre : s4_pre ^ 8'h80;
    wire [7:0] sine0, sine1, sine2, sine3, sine4;
    sine_rom u_rom0 (.addr(s0), .dout(sine0));
    sine_rom u_rom1 (.addr(s1), .dout(sine1));
    sine_rom u_rom2 (.addr(s2), .dout(sine2));
    sine_rom u_rom3 (.addr(s3), .dout(sine3));
    sine_rom u_rom4 (.addr(s4), .dout(sine4));

    // weighted products (signed, 15-bit)
    // ord=0 gates the harmonic off (accumulator frozen, product forced to 0)
    reg signed [14:0] prod0, prod1, prod2, prod3, prod4;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            prod0 <= 15'sd0; prod1 <= 15'sd0; prod2 <= 15'sd0;
            prod3 <= 15'sd0; prod4 <= 15'sd0;
        end else begin
            prod0 <= $signed(sine0) * $signed({1'b0, base});
            prod1 <= (h1_ord == 4'd0) ? 15'sd0 : ($signed(sine1) * $signed({1'b0, h1_amp}));
            prod2 <= (h2_ord == 4'd0) ? 15'sd0 : ($signed(sine2) * $signed({1'b0, h2_amp}));
            prod3 <= (h3_ord == 4'd0) ? 15'sd0 : ($signed(sine3) * $signed({1'b0, h3_amp}));
            prod4 <= (h4_ord == 4'd0) ? 15'sd0 : ($signed(sine4) * $signed({1'b0, h4_amp}));
        end

    // sum (17-bit signed: 5 x 127 x 100 = 63500 fits)
    reg signed [16:0] sump;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) sump <= 17'sd0;
        else        sump <= prod0 + prod1 + prod2 + prod3 + prod4;

    // final divide by 100 (truncate toward zero); result <= 127 by normalization
    // Use a wire to force wide-context division, then truncate to 8-bit
    wire signed [16:0] div_val = sump / 17'sd100;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample <= 8'sd0;
        else        sample <= div_val[7:0];
endmodule
