`timescale 1ns / 1ps
// 窄带单音干扰: tone[n] = round(ampl/100 × sine(2π f n / fs))。
// 24 位相位累加器, 地址取 acc[23:16] -> 复用既有 sine_rom(256 点, 与 dds_core 同质量)。
// 3 个寄存器级, 与 fade_fir / noise_gen 的输出同拍有效:
//   (1) acc 累加 + 寄存 dout   (2) ampl × sine_r   (3) 除以 100
//
// 时序(把复位释放后的第一个 posedge 记为 k=0):
//   k=0,1,2  被 enable 的 3 级流水门控为 0;
//   k>=3     tone_out[k] = round(ampl × ROM[((k-2)·fword)>>16] / 100)
// 本模块**没有数据输入**, 所以这里没有"输入→输出"的延时可言; 上式的 k-2 就是
// 3 个寄存器级的真实深度。host 模型 channel.py::tone_samples(...)[n] 对应 k=n+1
// (模型的 n=0 项是它对 acc=0 的重复, 故偏移是 1 不是 2), TB 按此逐拍比对。
//
// ⚠ sine_rom.v 是**组合**读(`always @(*) dout = mem[addr]`, 见该文件末行), 一拍延迟
//   来自本模块的 sine_r 寄存器, **不是** ROM 本身。不要以"ROM 已经是同步读"为由删掉
//   sine_r —— 删掉会少一拍, 与上面的 k-2 对不上, TB 会失败。
// 除以 100 独占一拍, 与 noise_gen 的 round_div(148) 同法, 避免乘除堆在同一周期。
module tone_gen (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [23:0] fword,
    input  wire [6:0]  ampl,
    output reg  signed [7:0] tone_out
);

    reg [23:0] acc;
    reg signed [7:0] sine_r;

    // ROM 输出是组合的; 声明必须在被引用之前(Verilog-2001, 否则 xvlog 报 VRFC 10-3380)
    wire [7:0] dout;

    // 第 1 拍: 累加 + 寄存 ROM 输出
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin acc <= 24'd0; sine_r <= 8'sd0; end
        else begin
            acc    <= acc + fword;
            sine_r <= dout;
        end

    sine_rom u_rom (.addr(acc[23:16]), .dout(dout));

    // 第 2 拍: 幅度乘 (ampl<=100, sine<=127 -> 最大 12700, 15-bit 有符号足够)
    reg signed [14:0] prod_r;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) prod_r <= 15'sd0;
        else        prod_r <= $signed({8'd0, ampl}) * $signed(sine_r);

    // 第 3 拍: 除以 100（有符号四舍五入）
    reg en_d1, en_d2, en_d3;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin en_d1 <= 1'b0; en_d2 <= 1'b0; en_d3 <= 1'b0; end
        else        begin en_d1 <= enable; en_d2 <= en_d1; en_d3 <= en_d2; end

    function signed [7:0] div100;
        input signed [14:0] v;
        reg signed [15:0] m;
        reg signed [15:0] r;
        begin
            if (v >= 0) r = (v + 50) / 100;
            else begin m = -v; r = -((m + 50) / 100); end
            div100 = r[7:0];
        end
    endfunction

    always @(posedge clk or negedge rst_n)
        if (!rst_n) tone_out <= 8'sd0;
        else        tone_out <= en_d3 ? div100(prod_r) : 8'sd0;
endmodule
