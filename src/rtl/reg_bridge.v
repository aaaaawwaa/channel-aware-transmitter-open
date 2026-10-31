`timescale 1ns / 1ps
// AXI GPIO (PS FCLK0 domain) -> 2-FF sync -> unpack CH1/CH2 params.
// Packing: freq[19:0] | ampl[7:0]<<20 | wave[2:0]<<28. Params are quasi-static
// (change only on host command), so per-bit 2-FF sync is safe.
// word == 0 means "PS 尚未写入" (freq 有效下限 10,PS 永不写全 0) -> hold DEF.
// GPIO13 = 用户 BRAM 控制字(bit0=source_user, bit1=loop, bit2=commit_toggle,
// bits[11:3]=length_bytes, bit12=active_bank); 数据本体在 PS 侧 AXI BRAM, 由
// user_bit_source 经 BRAM B 口读取, reg_bridge 不再承载用户数据存储。
module reg_bridge (
    input  wire clk,           // 50 MHz
    input  wire rst_n,
    input  wire [31:0] gpio0_in,  // -> CH1
    input  wire [31:0] gpio1_in,  // -> CH2
    input  wire [31:0] gpio2_in,  // -> CH1 谐波字 A
    input  wire [31:0] gpio3_in,  // -> CH2 谐波字 A
    input  wire [31:0] gpio4_in,  // -> CH1 谐波字 B
    input  wire [31:0] gpio5_in,  // -> CH2 谐波字 B
    input  wire [31:0] gpio6_in,  // -> CH1 调制字 C
    input  wire [31:0] gpio7_in,  // -> CH2 调制字 C
    input  wire [31:0] gpio8_in,  // -> CH1 调制字 D
    input  wire [31:0] gpio9_in,  // -> CH2 调制字 D
    input  wire [31:0] gpio10_in, // -> 相位差字 E
    input  wire [31:0] gpio11_in, // -> 联动使能字 F
    input  wire [31:0] gpio12_in, // -> 数字调制符号相位步进字 G
    input  wire [31:0] gpio13_in, // -> 用户 BRAM 控制字 H
    input  wire [31:0] gpio15_in, // -> PLAY_CTRL
    input  wire [31:0] gpio16_in, // -> PLAY_CFG
    input  wire [31:0] ch1_chan_word_in, // -> CH1 信道损伤控制字
    input  wire [31:0] ch2_chan_word_in, // -> CH2 信道损伤控制字
    output wire ch_link_en,
    output wire [31:0] ch_phase_off,
    output wire [2:0] digmod_sel,
    output wire digmod_filter,
    output wire [1:0] digmod_rolloff,
    output wire [6:0]  digmod_seed,
    // 用户 BRAM 控制字(冻结布局): bit0=source_user, bit1=loop, bit2=commit_toggle,
    // bits[11:3]=length_bytes, bit12=active_bank。准静态经 2-FF 同步, SDK 单次 Xil_Out32 原子写。
    output wire        digmod_source_user,
    output wire        user_loop,
    output wire        user_commit_epoch,
    output wire [8:0]  user_length_bytes,
    output wire [12:0] user_length,     // bit 数 = length_bytes << 3
    output wire        user_active_bank,
    output wire        play_manual_mode,
    output wire [2:0]  play_cmd_code,
    output wire        play_cmd_epoch,
    output wire        play_trigger_external,
    output wire        play_trigger_polarity,
    output wire [15:0] play_repeat,
    output wire [31:0] digmod_sym_phase_step,
    output wire [19:0] ch1_freq,
    output wire [7:0]  ch1_ampl,
    output wire [2:0]  ch1_wave,
    output wire [7:0]  ch1_pulse_duty,
    output wire [19:0] ch2_freq,
    output wire [7:0]  ch2_ampl,
    output wire [2:0]  ch2_wave,
    output wire [7:0]  ch2_pulse_duty,
    output wire [7:0]  ch1_base, ch2_base,
    output wire [3:0]  ch1_h1_ord, ch1_h2_ord, ch1_h3_ord, ch1_h4_ord,
    output wire [7:0]  ch1_h1_amp, ch1_h2_amp, ch1_h3_amp, ch1_h4_amp,
    output wire [3:0]  ch2_h1_ord, ch2_h2_ord, ch2_h3_ord, ch2_h4_ord,
    output wire [7:0]  ch2_h1_amp, ch2_h2_amp, ch2_h3_amp, ch2_h4_amp,
    output wire [2:0]  ch1_mode, ch2_mode,
    output wire [1:0]  ch1_mwave, ch2_mwave,
    output wire [18:0] ch1_mfreq, ch2_mfreq,
    output wire [7:0]  ch1_am_depth, ch2_am_depth,
    output wire [18:0] ch1_fm_dev, ch2_fm_dev,
    output wire [8:0]  ch1_pm_dev, ch2_pm_dev,
    // 信道损伤控制字输出(每信道独立 GPIO, 2-FF 同步后拆包)
    output wire        ch1_chan_en, ch2_chan_en,
    output wire [6:0]  ch1_noise, ch2_noise,
    output wire        ch1_tap_commit, ch2_tap_commit,
    // 干扰源使能(开关走 GPIO 立即生效): bit9=tone_en, bit10=impulse_en
    output wire        ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en,
    output wire        iq_impair_en
);
    localparam [31:0] DEF = 32'h0320_03E8;  // 1000 | 50<<20 | 0<<28 : 1kHz/50%/SINE

    reg [31:0] s0, s1, r0, r1;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s0 <= 32'd0; s1 <= 32'd0;
            r0 <= 32'd0; r1 <= 32'd0;
        end else begin
            s0 <= gpio0_in; s1 <= gpio1_in;
            r0 <= s0;       r1 <= s1;
        end

    // ---- 谐波字(无哨兵:全 0 = 静音,合法配置)----
    reg [31:0] s2, s3, s4, s5, r2, r3, r4, r5;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s2 <= 32'd0; s3 <= 32'd0; s4 <= 32'd0; s5 <= 32'd0;
            r2 <= 32'd0; r3 <= 32'd0; r4 <= 32'd0; r5 <= 32'd0;
        end else begin
            s2 <= gpio2_in; s3 <= gpio3_in;
            s4 <= gpio4_in; s5 <= gpio5_in;
            r2 <= s2;       r3 <= s3;
            r4 <= s4;       r5 <= s5;
        end

    // ---- 调制字(无哨兵:全 0 = 关闭,合法配置)----
    reg [31:0] s6, s7, s8, s9, r6, r7, r8, r9;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s6 <= 32'd0; s7 <= 32'd0; s8 <= 32'd0; s9 <= 32'd0;
            r6 <= 32'd0; r7 <= 32'd0; r8 <= 32'd0; r9 <= 32'd0;
        end else begin
            s6 <= gpio6_in; s7 <= gpio7_in;
            s8 <= gpio8_in; s9 <= gpio9_in;
            r6 <= s6;       r7 <= s7;
            r8 <= s8;       r9 <= s9;
        end

    // ---- 通道关系字(字E: 相位差 32 位全周期; 字F: bit0=联动, bit[8:1]=CH1 DUTY, bit[16:9]=CH2 DUTY) ----
    reg [31:0] s10, s11, r10, r11;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s10 <= 32'd0; s11 <= 32'd0;
            r10 <= 32'd0; r11 <= 32'd0;
        end else begin
            s10 <= gpio10_in; s11 <= gpio11_in;
            r10 <= s10;       r11 <= s11;
        end

    reg [31:0] s12, r12;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s12 <= 32'd0;
            r12 <= 32'd0;
        end else begin
            s12 <= gpio12_in;
            r12 <= s12;
        end

    reg [31:0] s13, r13;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s13 <= 32'd0;
            r13 <= 32'd0;
        end else begin
            s13 <= gpio13_in;
            r13 <= s13;
        end

    reg [31:0] s15, r15, s16, r16;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s15 <= 32'd0; r15 <= 32'd0;
            s16 <= 32'd0; r16 <= 32'd0;
        end else begin
            s15 <= gpio15_in; r15 <= s15;
            s16 <= gpio16_in; r16 <= s16;
        end

    // ---- 用户 BRAM 控制字(GPIO13 直通, 冻结布局) ----
    // bit0=source_user, bit1=loop, bit2=commit_toggle, bits[11:3]=length_bytes, bit12=active_bank。
    // 准静态: SDK 每次变更单次 Xil_Out32 原子写, PL 侧 2-FF 同步后直接使用, 无 strobe 跨域问题。
    assign digmod_source_user = r13[0];
    assign user_loop          = r13[1];
    assign user_commit_epoch  = r13[2];
    assign user_length_bytes  = r13[11:3];
    assign user_length        = (digmod_source_user && (user_length_bytes == 9'd0)) ?
                                 13'd4096 : ({4'd0, user_length_bytes} << 3);
    assign user_active_bank   = r13[12];

    assign play_manual_mode     = r15[0];
    assign play_cmd_code        = r15[3:1];
    assign play_cmd_epoch       = r15[4];
    assign play_trigger_external = r15[5];
    assign play_trigger_polarity = r15[6];
    assign play_repeat          = r16[15:0];

    assign ch_phase_off = r10;
    assign ch_link_en   = r11[0];
    assign digmod_sel   = r11[19:17];
    assign digmod_filter = r11[20];
    assign digmod_rolloff = r11[22:21];  // 00=0.35, 01=0.2, 10=0.5, 11=0.35 reserved/default
    assign digmod_seed    = r11[29:23];  // PRBS7 seed, 0 -> 默认 127(prbs7_source 内处理)
    assign digmod_sym_phase_step = r12;
    assign ch1_pulse_duty = (r11[8:1] > 8'd100) ? 8'd100 : r11[8:1];
    assign ch2_pulse_duty = (r11[16:9] > 8'd100) ? 8'd100 : r11[16:9];

    // word==0 (PS 未写入) -> 保持默认;否则拆包 + 钳位(防御)
    wire [31:0] p0 = (r0 == 32'd0) ? DEF : r0;
    wire [31:0] p1 = (r1 == 32'd0) ? DEF : r1;

    assign ch1_freq = (p0[19:0] < 20'd10) ? 20'd10 : p0[19:0];
    assign ch1_ampl = p0[27:20];
    assign ch1_wave = p0[30:28];
    assign ch2_freq = (p1[19:0] < 20'd10) ? 20'd10 : p1[19:0];
    assign ch2_ampl = p1[27:20];
    assign ch2_wave = p1[30:28];

    // 拆包(字A: base[29:22] h2_amp[21:15] h2_ord[14:11] h1_amp[10:4] h1_ord[3:0])
    //       (字B: reserved[31:22] h4_amp[21:15] h4_ord[14:11] h3_amp[10:4] h3_ord[3:0])
    assign ch1_base   = r2[29:22];
    assign ch1_h1_ord = r2[3:0];
    assign ch1_h1_amp = {1'b0, r2[10:4]};
    assign ch1_h2_ord = r2[14:11];
    assign ch1_h2_amp = {1'b0, r2[21:15]};
    assign ch1_h3_ord = r4[3:0];
    assign ch1_h3_amp = {1'b0, r4[10:4]};
    assign ch1_h4_ord = r4[14:11];
    assign ch1_h4_amp = {1'b0, r4[21:15]};

    assign ch2_base   = r3[29:22];
    assign ch2_h1_ord = r3[3:0];
    assign ch2_h1_amp = {1'b0, r3[10:4]};
    assign ch2_h2_ord = r3[14:11];
    assign ch2_h2_amp = {1'b0, r3[21:15]};
    assign ch2_h3_ord = r5[3:0];
    assign ch2_h3_amp = {1'b0, r5[10:4]};
    assign ch2_h4_ord = r5[14:11];
    assign ch2_h4_amp = {1'b0, r5[21:15]};

    // 拆包(字C: am_depth[31:24] mfreq[23:5] mwave[4:3] mode[2:0])
    //      (字D: reserved[31:28] pm_dev[27:19] fm_dev[18:0])
    assign ch1_mode     = r6[2:0];
    assign ch1_mwave    = r6[4:3];
    assign ch1_mfreq    = r6[23:5];
    assign ch1_am_depth = r6[31:24];
    assign ch2_mode     = r7[2:0];
    assign ch2_mwave    = r7[4:3];
    assign ch2_mfreq    = r7[23:5];
    assign ch2_am_depth = r7[31:24];
    assign ch1_fm_dev   = r8[18:0];
    assign ch1_pm_dev   = r8[27:19];
    assign ch2_fm_dev   = r9[18:0];
    assign ch2_pm_dev   = r9[27:19];

    // ---- 信道损伤控制字: 每信道独立 32 位 GPIO, 2-FF 同步 ----
    reg [31:0] s17, r17, s18, r18;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            s17 <= 32'd0; r17 <= 32'd0;
            s18 <= 32'd0; r18 <= 32'd0;
        end else begin
            s17 <= ch1_chan_word_in; r17 <= s17;
            s18 <= ch2_chan_word_in; r18 <= s18;
        end

    // ---- 信道损伤控制字拆包(冻结布局) ----
    // bit0=chan_en, bits[7:1]=noise(0..100, >100 钳位到 100), bit8=tap_commit epoch;
    // bit9=tone_en, bit10=impulse_en(干扰源使能, 与 chan_en/noise 一样立即生效);
    // bit11=iq_impair_en(复数 I/Q 级使能)。**借 CH1 控制字的位**: iq_impair 是
    //   通道对级单实例, 不属于任何单通道, 而 CH2 控制字在这个位上是空的 ——
    //   与其新开一条 GPIO 通道, 不如借用空闲位(设计文档 3 节)。
    // 其余保留位忽略。准静态: 主机单次原子写, 2-FF 同步后直用。
    assign ch1_chan_en    = r17[0];
    assign ch1_noise      = (r17[7:1] > 7'd100) ? 7'd100 : r17[7:1];
    assign ch1_tap_commit = r17[8];
    // 新增: 干扰源使能（开关走 GPIO 立即生效, 数值走 BRAM + COMMIT）
    // 注意 main.c write_chan_ctrl() 把控制字截到低 16 位, 故只用 bits 9-15。
    assign ch1_tone_en    = r17[9];
    assign ch1_imp_en     = r17[10];
    assign iq_impair_en   = r17[11];
    assign ch2_chan_en    = r18[0];
    assign ch2_noise      = (r18[7:1] > 7'd100) ? 7'd100 : r18[7:1];
    assign ch2_tap_commit = r18[8];
    assign ch2_tone_en    = r18[9];
    assign ch2_imp_en     = r18[10];
endmodule
