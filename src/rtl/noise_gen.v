`timescale 1ns / 1ps
// 确定性近似 AWGN 源（host/siggen/channel.py::noise_samples 的 RTL 镜像）。
//
// 算法（逐值对齐主机模型）:
//   state = xorshift32(state)                      ; state ^= state<<13; ^= state>>17; ^= state<<5 (32-bit 截断)
//   S     = sum of bytes [7:0],[15:8],[23:16],[31:24]  (0..1020)
//   c     = S - 510                                ; 去均值, 11-bit signed
//   sigma = min(level,100)*127/100                 ; 整数除, 0..127
//   noise = round_div(c*sigma, 148)                ; 148 = round(sqrt(4*(256^2-1)/12)), 四舍五入(远离零)
//
// 位宽说明（勿缩窄，否则静默截断且与 golden 不符）:
//   sigma 乘积 level*127 最大 12700 -> 需 14 bit，此处用 18-bit 有符号中间量
//   c*sigma 最大 510*127 = 64770     -> 需 17 bit，此处用 29-bit 有符号乘积
//
// 3 级流水：level->sigma 的除 100、c*sigma 的乘法、/148 的 round_div 各占一拍，
//   避免全部堆在单周期形成 ~34ns 组合链（曾在 50MHz 下 WNS=-14.6ns）。
//   端到端 3 拍；level=0 或 enable=0 输出 0。enable 经 en_d1/en_d2 对齐到 round_div 拍。
module noise_gen #(
    parameter [31:0] SEED = 32'h00C0FFEE
)(
    input  wire clk,
    input  wire rst_n,
    input  wire enable,
    input  wire [6:0] level,
    output reg signed [10:0] noise_out
);
    localparam [31:0] SEED_NZ = (SEED == 32'd0) ? 32'd1 : SEED;  // 主机用 `seed or 1`

    reg [31:0] state;
    wire [31:0] xs1 = state ^ (state << 13);
    wire [31:0] xs2 = xs1 ^ (xs1 >> 17);
    wire [31:0] xs3 = xs2 ^ (xs2 << 5);

    wire [9:0] sum_uniform = xs3[7:0] + xs3[15:8] + xs3[23:16] + xs3[31:24];
    wire signed [10:0] centered = $signed({1'b0, sum_uniform}) - 11'sd510;

    wire [6:0] lvl = (level > 7'd100) ? 7'd100 : level;
    // 18-bit context 保证 level*127 不被截断。
    // ⚠ 这一步也**必须无符号**: lvl<=100 时结果恒非负, 但写成 signed 综合器会当成"要处理
    //   负数除法"而退回进位链; 无符号常量除才能化成"乘倒数 + 移位"。
    wire [17:0] sigma_u = ({11'b0, lvl} * 18'd127) / 18'd100;
    wire signed [17:0] sigma = $signed({1'b0, sigma_u});
    // 29-bit context: 11-bit signed x 18-bit signed，结果符号扩展至 29 bit 再相乘
    wire signed [28:0] prod = centered * sigma;

    function signed [10:0] round_div;
        input signed [28:0] v;
        reg [28:0] mag;
        reg [28:0] q;
        begin
            // 不要写成 if/else 分支: 分支会让综合器放弃"乘倒数"而退回 ~20 个 CARRY4 的
            // restoring 除法链(实测把 50MHz 的 WNS 拖成 −0.102ns)。改成"取绝对值 ->
            // 无符号常量除 -> 补符号", 数值逐位不变 —— 判据是本模块 TB 与 host golden
            // 仍逐值对齐(不改 TB)。与 impulse_gen 当年那次是同一个坑。
            mag = v[28] ? (~v + 29'd1) : v;      // |v|: 二补数取负, 无分支
            q = (mag + 29'd74) / 29'd148;        // 无符号常量除 -> 乘倒数
            round_div = v[28] ? -$signed(q) : $signed(q);
        end
    endfunction

    // ---- 3 级流水寄存器 ----
    reg signed [10:0] centered_r;   // 第 1 级: xorshift 去均值
    reg signed [17:0] sigma_r;      // 第 1 级: level -> sigma (除 100 独占一拍)
    reg signed [28:0] prod_r;       // 第 2 级: centered_r * sigma_r
    reg en_d1, en_d2;               // enable 对齐到第 3 级 round_div

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            state <= SEED_NZ;
            centered_r <= 11'sd0;
            sigma_r <= 18'sd0;
            prod_r <= 29'sd0;
            en_d1 <= 1'b0;
            en_d2 <= 1'b0;
            noise_out <= 11'sd0;
        end else begin
            state      <= xs3;
            centered_r <= centered;
            sigma_r    <= sigma;
            prod_r     <= centered_r * sigma_r;
            en_d1      <= enable;
            en_d2      <= en_d1;
            noise_out  <= en_d2 ? round_div(prod_r) : 11'sd0;
        end
endmodule
