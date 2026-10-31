`timescale 1ns / 1ps
module dds_core (
    input  wire clk,          // 50 MHz
    input  wire rst_n,
    input  wire [31:0] fword,
    input  wire [2:0]  wave_sel,   // 000 sin / 001 sqr / 010 tri / 011 saw / 100 pulse / 101 noise
    input  wire [7:0]  pulse_duty, // PULSE high-time percentage, 0..100
    input  wire signed [8:0] pm_off,   // PM 相位偏移(地址 LSB, 0=直通)
    input  wire phase_ext_valid,       // 外部相位注入(相位联动主从): 不累加, 直接采样 phase_ext
    input  wire [31:0] phase_ext,      // 主通道相位 + 偏移(联动时由 awg_top 组合提供)
    output wire [31:0] phase_acc_out,  // 当前相位累加器(主相位源, 供 CH2 联动跟随)
    output reg  [7:0] sample
);
    reg [31:0] phase_acc;
    reg [2:0]  wave_sel_r;
    reg [7:0]  pulse_duty_r;
    reg signed [8:0] pm_off_r;
    reg [7:0]  noise_lfsr;
    reg [7:0]  noise_phase;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            phase_acc  <= 32'd0;
            wave_sel_r <= 3'b000;
            pulse_duty_r <= 8'd50;
            pm_off_r   <= 9'sd0;
            noise_lfsr <= 8'h01;
            noise_phase <= 8'd0;
        end else begin
            if (phase_ext_valid)
                phase_acc <= phase_ext;
            else
                phase_acc  <= phase_acc + fword;
            wave_sel_r <= wave_sel;
            pulse_duty_r <= pulse_duty > 8'd100 ? 8'd100 : pulse_duty;
            pm_off_r   <= pm_off;
            if (phase_acc[31:24] != noise_phase) begin
                noise_lfsr <= {noise_lfsr[6:0],
                               noise_lfsr[7] ^ noise_lfsr[5] ^
                               noise_lfsr[4] ^ noise_lfsr[3]};
                noise_phase <= phase_acc[31:24];
            end
        end

    assign phase_acc_out = phase_acc;

    // PM 注入: 累加器之后、查表之前加偏移(mod 256 回绕), 不改频率
    wire [7:0] p_off = phase_acc[31:24] + pm_off_r[7:0];
    wire [7:0] sine_v;
    sine_rom u_rom (.addr(p_off), .dout(sine_v));
    wire [7:0] sqr_v = p_off[7] ? 8'h81 : 8'h7F;   // -127 / +127
    // 三角波原实现取 phase_acc[30:23](相位字节左旋 1 位);PM 版本对偏移后的
    // 相位字节做同样旋转(位 23 不参与偏移,保持原样)
    wire [7:0] tri_pm = {p_off[6:0], phase_acc[23]};
    wire [7:0] tri_p  = p_off[7] ? ~tri_pm : tri_pm;
    wire [7:0] tri_v  = tri_p - 8'h80;
    wire [7:0] saw_v  = p_off - 8'h80;
    // LFSR state 1..255 is mapped to symmetric signed samples -127..127.
    wire [7:0] noise_v = {~noise_lfsr[7], noise_lfsr[6:0]};
    wire [15:0] pulse_scaled = {8'd0, pulse_duty_r} * 16'd256;
    wire [15:0] pulse_threshold = pulse_scaled / 16'd100;
    // ACM2108 模拟输出链路对该载波极性反相;让 GUI 的高电平区间对应板上物理高电平。
    wire [7:0] pulse_v = (pulse_duty_r == 8'd100 ||
                          {1'b0, p_off} < pulse_threshold[8:0]) ? 8'h81 : 8'h7F;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) sample <= 8'd0;
        else begin
            case (wave_sel_r)
                3'b000:  sample <= sine_v;
                3'b001:  sample <= sqr_v;
                3'b010:  sample <= tri_v;
                3'b100:  sample <= pulse_v;
                3'b101:  sample <= noise_v;
                default: sample <= saw_v;
            endcase
        end
endmodule
