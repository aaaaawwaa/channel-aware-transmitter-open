`timescale 1ns / 1ps
// 复数 I/Q 损伤: 静态 2x2 (Q2.14) -> 旋转 2x2 (Q1.15 的 sincos_rom)。
// 只作用于数字调制支路; `enable=0` 时逐位透传(与 chan_impair 的保证同级)。
//
// 5 个寄存器级, 每级只做一件事; **乘积先落寄存器再求和**, I/Q 两支路逐级对称:
//   S1: 寄存输入 + 相位累加              (ROM 同步读的输出同沿与样本配对)
//   S2: 静态 4 个乘积 A*i, B*q, C*i, D*q  (cos/sin 也在此打拍)
//   S3: 静态求和 + 舍入 + 饱和 -> i1/q1   (cos/sin 再打一拍, 与 i1/q1 对齐)
//   S4: 旋转 4 个乘积 i1*cos, q1*sin, i1*sin, q1*cos
//   S5: 旋转求和 + 舍入 + 饱和 -> 输出
// 实测: 输出相对输入滞后 **4** 个采样(5 级寄存器相邻级的关系); TB 用单点脉冲
// 实测并断言该值, 不写死。
//
// ⚠ 时序: 每级只有一个 DSP48 + 少量 fabric 逻辑, **不构成 PCOUT->PCIN 级联**
//   (这正是 fade_fir.v / rrc_filter.v 的既有教训 —— 乘法必须落在寄存器上,
//   不能让综合器把多级乘法串成一条长路径)。
//
// ⚠ 相位索引: sincos_rom 是**同步**读, 样本与 ROM 输出在同一个时钟沿配对,
//   故第 n 个样本用 `n*fword` 的地址。**不要照抄 tone_gen 的 n-1** ——
//   `sine_rom` 是组合读, 两者结构不同; 抄错只会让整条旋转慢一拍(恒定相位偏置,
//   仿真不报错, 只有与 host 模型比对时才会暴露)。
module iq_impair (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               enable,
    input  wire signed [7:0]  i_in,
    input  wire signed [7:0]  q_in,
    input  wire [31:0]        coef_ab,   // A = [15:0], B = [31:16]  (Q2.14 有符号)
    input  wire [31:0]        coef_cd,   // C = [15:0], D = [31:16]
    input  wire [31:0]        fword,     // 频偏相位增量(32 位全宽)
    output reg  signed [7:0]  i_out,
    output reg  signed [7:0]  q_out
);
    localparam LAT = 4;      // 5 级寄存器 -> 输出滞后输入 4 个采样

    wire signed [15:0] ca = coef_ab[15:0];
    wire signed [15:0] cb = coef_ab[31:16];
    wire signed [15:0] cc = coef_cd[15:0];
    wire signed [15:0] cd = coef_cd[31:16];

    // 对称饱和到 ±127(镜像 awg_top.v 的 rrc_to_dac, 不是 8-bit 的 -128)
    function signed [7:0] sat127;
        input signed [31:0] v;
        begin
            if (v > 32'sd127)       sat127 = 8'sd127;
            else if (v < -32'sd127) sat127 = -8'sd127;
            else                    sat127 = v[7:0];
        end
    endfunction

    // 符号对称四舍五入右移(与 host channel.py::round_shift_signed 同法)
    function signed [31:0] rsh;
        input signed [31:0] v;
        input integer       s;
        reg   signed [31:0] m;
        begin
            if (v >= 0) rsh = (v + (32'sd1 <<< (s - 1))) >>> s;
            else begin
                m   = -v;
                rsh = -((m + (32'sd1 <<< (s - 1))) >>> s);
            end
        end
    endfunction

    // ---- S1: 寄存输入 + 相位累加 ----
    // ⚠ fword=0 必须把相位**清零**, 不能只"停止累加": 累加器自由运行, 停下来的话
    //   会把上一次频偏留下的相位**冻住** -> 一个恒定但任意的旋转, 且随历史变化。
    //   上板实测: 跑过一次 FREQOFF 20000 之后再设 FREQOFF 0, QPSK 星座整体转
    //   ~45 度(正好落在判决边界上, 重对齐 BER 0.37、星座统计量看着仍"对"), 而
    //   host 模型 apply_iq_impair(fword=0) 是 arch=0 的恒等旋转 —— 两边必须一致。
    //   TB 的 "freqoff->0 phase reset" 用例钉住这条(用非 360 度整数倍的相位)。
    reg signed [7:0] s1_i, s1_q;
    reg [31:0] phase;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s1_i <= 8'sd0; s1_q <= 8'sd0; phase <= 32'd0;
        end else begin
            s1_i <= i_in; s1_q <= q_in;
            phase <= (fword == 32'd0) ? 32'd0 : phase + fword;
        end

    wire signed [15:0] rom_cos, rom_sin;
    sincos_rom u_rom (
        .clk(clk), .addr(phase[31:22]),
        .cos_out(rom_cos), .sin_out(rom_sin));

    // ---- S2: 静态 4 个乘积 + cos/sin 打拍 ----
    reg signed [24:0] p_ai, p_bq, p_ci, p_dq;
    reg signed [15:0] s2_cos, s2_sin;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            p_ai <= 25'sd0; p_bq <= 25'sd0; p_ci <= 25'sd0; p_dq <= 25'sd0;
            s2_cos <= 16'sd0; s2_sin <= 16'sd0;
        end else begin
            p_ai <= ca * s1_i;
            p_bq <= cb * s1_q;
            p_ci <= cc * s1_i;
            p_dq <= cd * s1_q;
            s2_cos <= rom_cos;
            s2_sin <= rom_sin;
        end

    // ---- S3: 静态求和 + 舍入 + 饱和; cos/sin 再打一拍与 i1/q1 对齐 ----
    // 只有两项相加, 平衡树在此是平凡的; 要求的是"乘积已在上一个寄存器里",
    // 故这里不会把 DSP48 级联起来。
    reg signed [7:0] i1, q1;
    reg signed [15:0] s3_cos, s3_sin;
    wire signed [25:0] sum_ab = p_ai + p_bq;
    wire signed [25:0] sum_cd = p_ci + p_dq;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            i1 <= 8'sd0; q1 <= 8'sd0; s3_cos <= 16'sd0; s3_sin <= 16'sd0;
        end else begin
            i1 <= sat127(rsh(sum_ab, 14));
            q1 <= sat127(rsh(sum_cd, 14));
            s3_cos <= s2_cos;
            s3_sin <= s2_sin;
        end

    // ---- S4: 旋转 4 个乘积 ----
    reg signed [24:0] p_ic, p_qs, p_is, p_qc;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            p_ic <= 25'sd0; p_qs <= 25'sd0; p_is <= 25'sd0; p_qc <= 25'sd0;
        end else begin
            p_ic <= s3_cos * i1;
            p_qs <= s3_sin * q1;
            p_is <= s3_sin * i1;
            p_qc <= s3_cos * q1;
        end

    // ---- S5: 旋转求和 + 舍入 + 饱和 ----
    wire signed [25:0] rot_i = p_ic - p_qs;
    wire signed [25:0] rot_q = p_is + p_qc;

    // ---- 旁路: 与 S1..S4 **等深(4 级)**, 不要做成 5 级 ----
    // ⚠ 输出寄存器(S5)在"S4 的旋转结果"和"旁路值"之间选择, 而它取的是**沿前**的值。
    //   所以旁路只需与 S4 同拍(4 级); 做成 5 级会让选择信号比数据晚一拍, 结果是
    //   enable 置 1 的那一拍仍选中旁路(值恰好是上一拍的空数据), 输出恒 0 —— 这个
    //   bug 在仿真里不报错, 只是"永远没有输出"。TB 的 enable=0 用例同时钉住两支路。
    reg signed [7:0] byp_i [0:LAT-1];
    reg signed [7:0] byp_q [0:LAT-1];
    reg              byp_en [0:LAT-1];
    integer k;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            for (k = 0; k < LAT; k = k + 1) begin
                byp_i[k] <= 8'sd0; byp_q[k] <= 8'sd0; byp_en[k] <= 1'b0;
            end
        end else begin
            byp_i[0] <= i_in; byp_q[0] <= q_in; byp_en[0] <= enable;
            for (k = 1; k < LAT; k = k + 1) begin
                byp_i[k] <= byp_i[k - 1];
                byp_q[k] <= byp_q[k - 1];
                byp_en[k] <= byp_en[k - 1];
            end
        end

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            i_out <= 8'sd0; q_out <= 8'sd0;
        end else begin
            i_out <= byp_en[LAT-1] ? sat127(rsh(rot_i, 15)) : byp_i[LAT-1];
            q_out <= byp_en[LAT-1] ? sat127(rsh(rot_q, 15)) : byp_q[LAT-1];
        end
endmodule
