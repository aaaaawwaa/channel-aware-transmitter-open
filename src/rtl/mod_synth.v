`timescale 1ns / 1ps
// 内调制源: 48 位累加器 + 4 波形,输出 m/fword_inst/pm_off/am_gain。
// 输出运算操作数在同一拍采样，FM/PM/AM 从同一流水级组合产生并保持对齐。
// 参数校验由 PS 桥完成(am_depth<=100, fm_dev<=500000, pm_dev<=511, mfreq>=1)。
module mod_synth (
    input  wire clk,
    input  wire rst_n,
    input  wire [47:0] mfreq_fword,   // = mfreq_01 × 562950
    input  wire [1:0]  mwave,         // 0=sine 1=square 2=tri 3=saw
    input  wire [2:0]  mode,          // 0=off 1=am 2=fm 3=pm 4=sweep
    input  wire [7:0]  am_depth,      // 0..100
    input  wire [25:0] fm_dev_fword,  // = fm_dev×85 + fm_dev×9/10
    input  wire [8:0]  pm_dev,        // 0..511, 1/256 周期单位
    input  wire [31:0] fword_base,
    output wire signed [7:0] m,          // 调制信号(始终运行,切换模式无相位跳变)
    output wire [31:0] fword_inst,       // FM/SWEEP 瞬时 fword,否则 = fword_base
    output wire signed [8:0] pm_off,     // PM 相位偏移,否则 0
    output wire signed [16:0] am_gain    // 12700 + m×am_depth,否则 12700(直通)
);
    reg [47:0] acc_m;
    reg signed [33:0] fm_prod_r;
    reg signed [16:0] pm_prod_r;
    reg signed [15:0] am_prod_r;
    reg [31:0] fword_base_r;
    reg [2:0] mode_r;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            acc_m        <= 48'd0;
            fm_prod_r    <= 34'sd0;
            pm_prod_r    <= 17'sd0;
            am_prod_r    <= 16'sd0;
            fword_base_r <= 32'd0;
            mode_r       <= 3'd0;
        end else begin
            acc_m        <= acc_m + mfreq_fword;
            fm_prod_r    <= $signed(m) * $signed({1'b0, fm_dev_fword});
            pm_prod_r    <= $signed(m) * $signed({1'b0, pm_dev});
            am_prod_r    <= $signed(m) * $signed({1'b0, am_depth});
            fword_base_r <= fword_base;
            mode_r       <= mode;
        end

    // ---- 调制波形(复用 dds_core 的算法与 sine_rom)----
    wire [7:0] t = acc_m[47:40];
    wire [7:0] sine_v;
    sine_rom u_rom (.addr(t), .dout(sine_v));
    wire signed [7:0] m_sine = $signed(sine_v);
    wire signed [7:0] m_sqr  = acc_m[47] ? -8'sd127 : 8'sd127;
    wire signed [7:0] m_tri  = $signed(acc_m[47] ? ~acc_m[46:39] : acc_m[46:39]) - 8'sd128;
    wire signed [7:0] m_saw  = $signed(t) - 8'sd128;
    wire signed [7:0] m_raw  = (mwave == 2'b00) ? m_sine :
                               (mwave == 2'b01) ? m_sqr :
                               (mwave == 2'b10) ? m_tri : m_saw;
    assign m = m_raw;

    // ---- FM/SWEEP: fword_inst = fword_base + (m×fm_dev_fword)/127, 饱和 [0, 2^31] ----
    wire signed [33:0] fm_prod  = fm_prod_r;
    wire signed [33:0] fm_delta = fm_prod / 34'sd127;          // 截断向零
    wire signed [34:0] fm_sum   = $signed({1'b0, fword_base_r}) + fm_delta;
    wire signed [34:0] fm_sat   = (fm_sum > 35'sd2147483647) ? 35'sd2147483647 :
                                  (fm_sum < 35'sd0) ? 35'sd0 : fm_sum;
    assign fword_inst = (mode_r == 3'b010 || mode_r == 3'b100) ? fm_sat[31:0] : fword_base_r;

    // ---- PM: pm_off = (m×pm_dev)/127, ±511 ----
    wire signed [16:0] pm_prod  = pm_prod_r;
    wire signed [16:0] pm_off_c = pm_prod / 17'sd127;          // 截断向零
    assign pm_off = (mode_r == 3'b011) ? pm_off_c[8:0] : 9'sd0;

    // ---- AM: am_gain = 12700 + m×am_depth, 钳位 [0, 32767] ----
    wire signed [15:0] am_prod = am_prod_r;
    wire signed [17:0] am_sum  = 18'sd12700 + $signed(am_prod);
    wire signed [17:0] am_sat  = (am_sum > 18'sd32767) ? 18'sd32767 :
                                 (am_sum < 18'sd0)    ? 18'sd0 : am_sum;
    assign am_gain = (mode_r == 3'b001) ? am_sat[16:0] : 17'sd12700;
endmodule
