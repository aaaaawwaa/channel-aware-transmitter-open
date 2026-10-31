`timescale 1ns / 1ps
// AWG top (host-controlled): reg_bridge (AXI GPIO from PS) -> dual mod_synth/am_stage (内调制) ->
// dual dds_core + harm_synth -> dual ampl_scale -> dac_driver -> ACM2108 DAC0/DAC1.
module awg_top (
    input  wire sys_clk,          // 50 MHz (Y9)
    input  wire status_clk,       // PS FCLK0 domain for coherent PLAY_STATUS mailbox
    input  wire [31:0] gpio0_in,  // AXI GPIO ch0 (PS FCLK0 domain) -> CH1
    input  wire [31:0] gpio1_in,  // AXI GPIO ch1 (PS FCLK0 domain) -> CH2
    input  wire [31:0] gpio2_in,  // AXI GPIO ch2 (PS FCLK0 domain) -> CH1 谐波字 A
    input  wire [31:0] gpio3_in,  // AXI GPIO ch3 (PS FCLK0 domain) -> CH2 谐波字 A
    input  wire [31:0] gpio4_in,  // AXI GPIO ch4 (PS FCLK0 domain) -> CH1 谐波字 B
    input  wire [31:0] gpio5_in,  // AXI GPIO ch5 (PS FCLK0 domain) -> CH2 谐波字 B
    input  wire [31:0] gpio6_in,  // AXI GPIO ch6 (PS FCLK0 domain) -> CH1 调制字 C
    input  wire [31:0] gpio7_in,  // AXI GPIO ch7 (PS FCLK0 domain) -> CH2 调制字 C
    input  wire [31:0] gpio8_in,  // AXI GPIO ch8 (PS FCLK0 domain) -> CH1 调制字 D
    input  wire [31:0] gpio9_in,  // AXI GPIO ch9 (PS FCLK0 domain) -> CH2 调制字 D
    input  wire [31:0] gpio10_in, // AXI GPIO ch10 (PS FCLK0 domain) -> CH2 相位差(32 位全周期)
    input  wire [31:0] gpio11_in, // AXI GPIO ch11 (PS FCLK0 domain) -> 相位联动使能(bit0)
    input  wire [31:0] gpio12_in, // AXI GPIO ch12 (PS FCLK0 domain) -> 数字调制符号相位步进
    input  wire [31:0] gpio13_in, // AXI GPIO ch13 (PS FCLK0 domain) -> 用户 BRAM 控制字
    input  wire [31:0] ch1_chan_word, // AXI GPIO -> CH1 信道损伤控制字
    input  wire [31:0] ch2_chan_word, // AXI GPIO -> CH2 信道损伤控制字
    input  wire [31:0] play_ctrl_in, // PLAY_CTRL (AXI GPIO ch9 channel 1)
    input  wire [31:0] play_cfg_in,  // PLAY_CFG (AXI GPIO ch9 channel 2)
    output wire [31:0] play_status_out, // PLAY_STATUS (AXI GPIO ch10)
    output wire [31:0] gpio14_out,// AXI GPIO ch14 -> 用户 BRAM 状态
    output wire bram_b_clk,       // 用户 BRAM 同步读 B 口(RECT/RRC 互斥复用)
    output wire bram_b_en,
    output wire [31:0] bram_b_addr,
    input  wire [31:0] bram_b_rdata,
    // 信道损伤抽头表 BRAM B 口(chan_cfg 同步读)
    output wire [5:0]  tap_b_addr,
    output wire        tap_b_en,
    input  wire [31:0] tap_b_rdata,
    input  wire user_stream_enable,
    input  wire [31:0] user_stream_length,
    input  wire [31:0] user_stream_tdata,
    input  wire [3:0] user_stream_tkeep,
    input  wire user_stream_tvalid,
    output wire user_stream_tready,
    input  wire user_stream_tlast,
    output wire [7:0] led,        // P20..T17
    output wire [7:0] dac0_db,    // GPIO0 -> ACM2108 DAC0 data
    output wire [7:0] dac1_db,    // GPIO0 -> ACM2108 DAC1 data
    output wire daclk0,           // GPIO0 -> DAC0 clock
    output wire daclk1,           // GPIO0 -> DAC1 clock
    output wire user_stream_underflow // DDR stream source underflow, sys_clk domain
);
    // power-on reset: ~10.5 ms (2^19 = 524288 clk @ 50 MHz); FF power-on state = 0
    reg [19:0] rst_cnt = 0;
    wire rst_n = rst_cnt[19];
    always @(posedge sys_clk)
        if (!rst_n) rst_cnt <= rst_cnt + 1'b1;
    reg user_stream_enable_meta, user_stream_enable_sync;
    reg [31:0] user_stream_length_meta, user_stream_length_sync;
    always @(posedge sys_clk) begin
        if (!rst_n) begin
            user_stream_enable_meta <= 1'b0;
            user_stream_enable_sync <= 1'b0;
            user_stream_length_meta <= 32'd0;
            user_stream_length_sync <= 32'd0;
        end else begin
            user_stream_enable_meta <= user_stream_enable;
            user_stream_enable_sync <= user_stream_enable_meta;
            user_stream_length_meta <= user_stream_length;
            user_stream_length_sync <= user_stream_length_meta;
        end
    end

    wire [19:0] ch1_freq, ch2_freq;
    wire [7:0]  ch1_ampl, ch2_ampl;
    wire [2:0]  ch1_wave, ch2_wave;
    wire [7:0]  ch1_pulse_duty, ch2_pulse_duty;
    wire [7:0]  ch1_base, ch2_base;
    wire [3:0]  ch1_h1_ord, ch1_h2_ord, ch1_h3_ord, ch1_h4_ord;
    wire [7:0]  ch1_h1_amp, ch1_h2_amp, ch1_h3_amp, ch1_h4_amp;
    wire [3:0]  ch2_h1_ord, ch2_h2_ord, ch2_h3_ord, ch2_h4_ord;
    wire [7:0]  ch2_h1_amp, ch2_h2_amp, ch2_h3_amp, ch2_h4_amp;
    wire [2:0]  ch1_mode, ch2_mode;
    wire [1:0]  ch1_mwave, ch2_mwave;
    wire [18:0] ch1_mfreq, ch2_mfreq;
    wire [7:0]  ch1_am_depth, ch2_am_depth;
    wire [18:0] ch1_fm_dev, ch2_fm_dev;
    wire [8:0]  ch1_pm_dev, ch2_pm_dev;
    wire ch_link_en;              // 相位联动使能(reg_bridge 字F bit0, Task 2 消费)
    wire [31:0] ch_phase_off;     // CH2 相位差 32 位全周期(reg_bridge 字E, Task 2 消费)
    wire [2:0] digmod_sel;        // 全局数字调制选择(reg_bridge 字F bit[19:17])
    wire digmod_filter;           // RRC 滤波选择(reg_bridge 字F bit20)
    wire [1:0] digmod_rolloff;    // RRC 滚降选择(reg_bridge 字F bit[22:21])
    wire [6:0] digmod_seed;       // PRBS7 seed(reg_bridge 字F bit[29:23])
    wire [31:0] digmod_sym_phase_step;
    // 用户 bit 源控制(reg_bridge GPIO13 冻结控制字)
    wire digmod_source_user;      // 数据源选择(0=PRBS7, 1=USER)
    wire user_loop;
    wire user_commit_epoch;
    wire [8:0] user_length_bytes;
    wire [12:0] user_length;
    wire user_active_bank;
    wire play_manual_mode;
    wire [2:0] play_cmd_code;
    wire play_cmd_epoch;
    wire play_trigger_external;
    wire play_trigger_polarity;
    wire [15:0] play_repeat;
    wire [31:0] play_ctrl_safe = {
        25'd0, (play_ctrl_in[6] === 1'b1), (play_ctrl_in[5] === 1'b1),
        (play_ctrl_in[4] === 1'b1), (play_ctrl_in[3] === 1'b1),
        (play_ctrl_in[2] === 1'b1), (play_ctrl_in[1] === 1'b1),
        (play_ctrl_in[0] === 1'b1)};
    wire [31:0] play_cfg_safe =
        (play_cfg_in[31:0] === 32'hzzzz_zzzz) ? 32'd0 : play_cfg_in;
    wire user_stream_enable_safe = (user_stream_enable_sync === 1'b1);
    wire [31:0] user_stream_length_safe = user_stream_length_sync;
    // GPIO7 channel 2 bit 0 enables the DDR stream; payload length uses bits 31:1.
    wire [31:0] user_stream_payload_length = user_stream_length_safe & 32'hffff_fff8;
    wire [31:0] user_stream_tdata_safe =
        (user_stream_tdata === 32'hzzzz_zzzz) ? 32'd0 : user_stream_tdata;
    wire [3:0] user_stream_tkeep_safe =
        (user_stream_tkeep === 4'hz) ? 4'd0 : user_stream_tkeep;
    wire user_stream_tvalid_safe = (user_stream_tvalid === 1'b1);
    wire user_stream_tlast_safe = (user_stream_tlast === 1'b1);
    wire play_run_enable;
    wire play_restart;
    wire [2:0] play_state;
    wire play_frame_done;
    wire play_underflow;
    wire play_trigger_seen;
    wire play_stop_pending;
    wire play_error_valid;
    wire [15:0] play_repeat_remaining;
    wire [7:0] play_error_code;
    wire play_frame_done_pulse;
    wire play_drain_done_pulse;
    wire play_drain_active;
    wire user_valid;              // 有已提交用户数据(由 user_bit_source 状态合成, 过渡置 0)
    wire user_busy;
    reg user_commit_epoch_d;
    reg user_committed_sys;
    wire [7:0] iq_bram_b_addr, rrc_bram_b_addr;   // 字地址(0..255, 每 bank 512B)
    wire iq_bram_b_en, rrc_bram_b_en;
    wire [4:0] tap_word_addr;                    // 抽头表字地址(chan_cfg 输出)
    wire iq_stream_tready, rrc_stream_tready;
    // 信道损伤控制(reg_bridge Task 6 输出); 抽头表配置由 chan_cfg 排空
    wire ch1_chan_en, ch2_chan_en;
    wire [6:0] ch1_noise, ch2_noise;
    wire ch1_tap_commit, ch2_tap_commit;
    // 干扰源使能(reg_bridge bit9/bit10, 开关走 GPIO 立即生效)
    wire ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en;
    wire iq_impair_en;

    reg_bridge u_reg (
        .clk(sys_clk), .rst_n(rst_n),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .gpio2_in(gpio2_in), .gpio3_in(gpio3_in),
        .gpio4_in(gpio4_in), .gpio5_in(gpio5_in),
        .gpio6_in(gpio6_in), .gpio7_in(gpio7_in),
        .gpio8_in(gpio8_in), .gpio9_in(gpio9_in),
        .gpio10_in(gpio10_in), .gpio11_in(gpio11_in), .gpio12_in(gpio12_in),
        .gpio13_in(gpio13_in),
        .ch1_chan_word_in(ch1_chan_word), .ch2_chan_word_in(ch2_chan_word),
        .gpio15_in(play_ctrl_safe), .gpio16_in(play_cfg_safe),
        .ch_link_en(ch_link_en), .ch_phase_off(ch_phase_off),
        .digmod_sel(digmod_sel), .digmod_filter(digmod_filter),
        .digmod_rolloff(digmod_rolloff), .digmod_seed(digmod_seed),
        .digmod_source_user(digmod_source_user),
        .user_loop(user_loop), .user_commit_epoch(user_commit_epoch),
        .user_length_bytes(user_length_bytes),
        .user_length(user_length), .user_active_bank(user_active_bank),
        .play_manual_mode(play_manual_mode), .play_cmd_code(play_cmd_code),
        .play_cmd_epoch(play_cmd_epoch),
        .play_trigger_external(play_trigger_external),
        .play_trigger_polarity(play_trigger_polarity), .play_repeat(play_repeat),
        .digmod_sym_phase_step(digmod_sym_phase_step),
        .ch1_freq(ch1_freq), .ch1_ampl(ch1_ampl), .ch1_wave(ch1_wave),
        .ch1_pulse_duty(ch1_pulse_duty),
        .ch2_freq(ch2_freq), .ch2_ampl(ch2_ampl), .ch2_wave(ch2_wave),
        .ch2_pulse_duty(ch2_pulse_duty),
        .ch1_base(ch1_base), .ch2_base(ch2_base),
        .ch1_h1_ord(ch1_h1_ord), .ch1_h2_ord(ch1_h2_ord),
        .ch1_h3_ord(ch1_h3_ord), .ch1_h4_ord(ch1_h4_ord),
        .ch1_h1_amp(ch1_h1_amp), .ch1_h2_amp(ch1_h2_amp),
        .ch1_h3_amp(ch1_h3_amp), .ch1_h4_amp(ch1_h4_amp),
        .ch2_h1_ord(ch2_h1_ord), .ch2_h2_ord(ch2_h2_ord),
        .ch2_h3_ord(ch2_h3_ord), .ch2_h4_ord(ch2_h4_ord),
        .ch2_h1_amp(ch2_h1_amp), .ch2_h2_amp(ch2_h2_amp),
        .ch2_h3_amp(ch2_h3_amp), .ch2_h4_amp(ch2_h4_amp),
        .ch1_mode(ch1_mode), .ch2_mode(ch2_mode),
        .ch1_mwave(ch1_mwave), .ch2_mwave(ch2_mwave),
        .ch1_mfreq(ch1_mfreq), .ch2_mfreq(ch2_mfreq),
        .ch1_am_depth(ch1_am_depth), .ch2_am_depth(ch2_am_depth),
        .ch1_fm_dev(ch1_fm_dev), .ch2_fm_dev(ch2_fm_dev),
        .ch1_pm_dev(ch1_pm_dev), .ch2_pm_dev(ch2_pm_dev),
        .ch1_chan_en(ch1_chan_en), .ch2_chan_en(ch2_chan_en),
        .ch1_noise(ch1_noise), .ch2_noise(ch2_noise),
        .ch1_tap_commit(ch1_tap_commit), .ch2_tap_commit(ch2_tap_commit),
        .ch1_tone_en(ch1_tone_en), .ch1_imp_en(ch1_imp_en),
        .ch2_tone_en(ch2_tone_en), .ch2_imp_en(ch2_imp_en),
        .iq_impair_en(iq_impair_en)
    );

    // fword = freq*85 + (freq*9)/10  ~  freq * 2^32 / 5e7 (err < 0.001%)
    wire [31:0] fword1 = ch1_freq * 20'd85 + ((ch1_freq * 20'd9) / 20'd10);
    wire [31:0] fword2 = ch2_freq * 20'd85 + ((ch2_freq * 20'd9) / 20'd10);

    // 频偏 fword 化(同 fword 公式); 先寄存结果再送入调制流水，切断寄存器桥到 FM 乘除法的长路径
    // 调制累加增量 = mfreq_01 × 562950
    // (mfreq_fword 声明为 48 位以匹配 mod_synth 端口: 位宽不匹配时 xelab 高位补 Z)
    wire [25:0] fword_dev1_comb = ch1_fm_dev * 20'd85 + ((ch1_fm_dev * 20'd9) / 20'd10);
    wire [25:0] fword_dev2_comb = ch2_fm_dev * 20'd85 + ((ch2_fm_dev * 20'd9) / 20'd10);
    reg [25:0] fword_dev1, fword_dev2;
    always @(posedge sys_clk or negedge rst_n)
        if (!rst_n) begin
            fword_dev1 <= 26'd0;
            fword_dev2 <= 26'd0;
        end else begin
            fword_dev1 <= fword_dev1_comb;
            fword_dev2 <= fword_dev2_comb;
        end
    wire [47:0] mfreq_fword1 = ch1_mfreq * 20'd562950;
    wire [47:0] mfreq_fword2 = ch2_mfreq * 20'd562950;

    // m1/m2: 未用(调试探针), 综合时裁剪
    wire signed [7:0] m1, m2;
    wire [31:0] fword_inst1, fword_inst2;
    wire signed [8:0] pm_off1, pm_off2;
    wire signed [16:0] am_gain1, am_gain2;
    wire [31:0] phase_acc1;      // CH1 相位累加器(主相位源, 供 CH2 联动跟随)
    mod_synth u_mod1 (
        .clk(sys_clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword1), .mwave(ch1_mwave), .mode(ch1_mode),
        .am_depth(ch1_am_depth), .fm_dev_fword(fword_dev1), .pm_dev(ch1_pm_dev),
        .fword_base(fword1),
        .m(m1), .fword_inst(fword_inst1), .pm_off(pm_off1), .am_gain(am_gain1)
    );
    mod_synth u_mod2 (
        .clk(sys_clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword2), .mwave(ch2_mwave), .mode(ch2_mode),
        .am_depth(ch2_am_depth), .fm_dev_fword(fword_dev2), .pm_dev(ch2_pm_dev),
        .fword_base(fword2),
        .m(m2), .fword_inst(fword_inst2), .pm_off(pm_off2), .am_gain(am_gain2)
    );

    // PM 偏移在数据通路统一寄存一拍, 切断 mod_synth 除法器 → 谐波查表/乘积的长组合链;
    // DDS 侧内部 pm_off_r 再寄存一拍, 谐波与 DDS 的 PM 相对延迟保持 1 拍不变
    reg signed [8:0] pm_off1_r, pm_off2_r;
    always @(posedge sys_clk or negedge rst_n)
        if (!rst_n) begin
            pm_off1_r <= 9'sd0;
            pm_off2_r <= 9'sd0;
        end else begin
            pm_off1_r <= pm_off1;
            pm_off2_r <= pm_off2;
        end

    // FM/SWEEP 瞬时频率字统一寄存一拍, 切断 mod_synth FM 组合(除法/饱和) →
    // 谐波/DDS 相位累加器的进位级联长路径; CH1/CH2 同源同拍, 锁相与联动相位关系不变
    reg [31:0] fword_inst1_r, fword_inst2_r;
    always @(posedge sys_clk or negedge rst_n)
        if (!rst_n) begin
            fword_inst1_r <= 32'd0;
            fword_inst2_r <= 32'd0;
        end else begin
            fword_inst1_r <= fword_inst1;
            fword_inst2_r <= fword_inst2;
        end

    // ---- 相位联动: link_en 时 CH2 频率跟随 CH1、相位 = CH1 相位 + 偏移、调制抑制 ----
    wire link_en = ch_link_en;
    wire [31:0] fword_inst2_used = link_en ? fword_inst1_r : fword_inst2_r;
    wire signed [8:0] pm_off2_used = link_en ? 9'sd0 : pm_off2_r;
    wire signed [16:0] am_gain2_used = link_en ? 17'sd12700 : am_gain2;
    // CH2 外部相位 = CH1 累加器 + 静态偏移(组合加法, 下游打拍采样; 全周期 mod 2^32 回绕)
    wire [31:0] phase_ext2 = phase_acc1 + ch_phase_off;

    wire [7:0] harm1, harm2;
    wire [7:0] a1, a2;
    harm_synth u_harm1 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword_inst1_r),
        .phase_ext_valid(1'b0), .phase_ext(32'd0), .pm_off(pm_off1_r),
        .base(ch1_base), .h1_ord(ch1_h1_ord), .h2_ord(ch1_h2_ord),
        .h3_ord(ch1_h3_ord), .h4_ord(ch1_h4_ord),
        .h1_amp(ch1_h1_amp), .h2_amp(ch1_h2_amp),
        .h3_amp(ch1_h3_amp), .h4_amp(ch1_h4_amp),
        .sample(harm1)
    );
    harm_synth u_harm2 (
        .clk(sys_clk), .rst_n(rst_n), .fword(fword_inst2_used), .pm_off(pm_off2_used),
        .phase_ext_valid(link_en), .phase_ext(phase_ext2),
        .base(ch2_base), .h1_ord(ch2_h1_ord), .h2_ord(ch2_h2_ord),
        .h3_ord(ch2_h3_ord), .h4_ord(ch2_h4_ord),
        .h1_amp(ch2_h1_amp), .h2_amp(ch2_h2_amp),
        .h3_amp(ch2_h3_amp), .h4_amp(ch2_h4_amp),
        .sample(harm2)
    );

    wire [7:0] s1_raw, s2_raw;
    dds_core u_dds1 (.clk(sys_clk), .rst_n(rst_n), .fword(fword_inst1_r), .wave_sel(ch1_wave), .pulse_duty(ch1_pulse_duty), .pm_off(pm_off1_r), .phase_ext_valid(1'b0), .phase_ext(32'd0), .phase_acc_out(phase_acc1), .sample(s1_raw));
    dds_core u_dds2 (.clk(sys_clk), .rst_n(rst_n), .fword(fword_inst2_used), .wave_sel(ch2_wave), .pulse_duty(ch2_pulse_duty), .pm_off(pm_off2_used), .phase_ext_valid(link_en), .phase_ext(phase_ext2), .phase_acc_out(), .sample(s2_raw));
    // 仅在谐波参数已配置(非全零)时启用谐波路径;否则走原始 DDS
    wire harm1_en = (ch1_base != 0) || (ch1_h1_amp != 0) || (ch1_h2_amp != 0) || (ch1_h3_amp != 0) || (ch1_h4_amp != 0);
    wire harm2_en = (ch2_base != 0) || (ch2_h1_amp != 0) || (ch2_h2_amp != 0) || (ch2_h3_amp != 0) || (ch2_h4_amp != 0);
    wire [7:0] s1 = (ch1_wave == 3'b000 && harm1_en) ? harm1 : s1_raw;
    wire [7:0] s2 = (ch2_wave == 3'b000 && harm2_en) ? harm2 : s2_raw;

    wire [7:0] s1_am, s2_am;
    am_stage u_am1 (.clk(sys_clk), .rst_n(rst_n),
                    .sample_in(s1), .am_gain(am_gain1), .sample_out(s1_am));
    am_stage u_am2 (.clk(sys_clk), .rst_n(rst_n),
                    .sample_in(s2), .am_gain(am_gain2_used), .sample_out(s2_am));

    ampl_scale u_amp1 (
        .clk(sys_clk), .rst_n(rst_n), .sample_in(s1_am), .ampl_pct(ch1_ampl), .sample_out(a1)
    );
    ampl_scale u_amp2 (
        .clk(sys_clk), .rst_n(rst_n), .sample_in(s2_am), .ampl_pct(ch2_ampl), .sample_out(a2)
    );

    // ---- 数字调制: 全局 digmod_sel 控制共享 I/Q 发射器旁路 ----
    wire digmod_enable = (digmod_sel >= 3'd1) && (digmod_sel <= 3'd5);
    wire [2:0] digmod_mode = digmod_sel - 3'd1;
    wire play_gate = !play_manual_mode || (play_run_enable && !play_restart);
    wire ddr_stream_gate = digmod_source_user && user_stream_enable_safe;
    wire digmod_run_gate = play_gate || ddr_stream_gate;
    wire rect_active = digmod_enable && !digmod_filter && digmod_run_gate;
    wire signed [7:0] dig_i, dig_q;
    wire iq_user_done, iq_user_underflow, iq_user_ready;
    iq_digital_mod u_dig (
        .clk(sys_clk), .rst_n(rst_n), .enable(rect_active), .mode(digmod_mode),
        .prbs_seed(digmod_seed),
        .sym_phase_step(digmod_sym_phase_step),
        .source_user(digmod_source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank),
        .bram_b_addr(iq_bram_b_addr), .bram_b_en(iq_bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_stream_enable(user_stream_enable_safe),
        .user_stream_length(user_stream_payload_length),
        .user_stream_tdata(user_stream_tdata_safe),
        .user_stream_tkeep(user_stream_tkeep_safe),
        .user_stream_tvalid(user_stream_tvalid_safe),
        .user_stream_tready(iq_stream_tready),
        .user_stream_tlast(user_stream_tlast_safe),
        .user_done(iq_user_done), .user_underflow(iq_user_underflow),
        .user_ready(iq_user_ready),
        .i_sample(dig_i), .q_sample(dig_q), .symbol_valid()
    );

    wire rrc_active = digmod_enable && digmod_filter && digmod_run_gate;
    wire signed [15:0] rrc_i16, rrc_q16;
    wire rrc_sample_valid;
    wire rrc_user_done, rrc_user_underflow, rrc_user_ready;
    wire rrc_frame_done_pulse, rrc_drain_active;
    reg selected_user_done_d;
    reg user_stream_done_r;
    wire selected_user_done = rrc_active ? rrc_user_done : iq_user_done;
    wire selected_user_done_pulse = selected_user_done && !selected_user_done_d;
    wire user_stream_done_pulse = digmod_source_user && user_stream_enable_safe &&
                                  (digmod_filter ? rrc_frame_done_pulse :
                                                   selected_user_done_pulse);
    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            selected_user_done_d <= 1'b0;
            user_stream_done_r <= 1'b0;
        end else begin
            selected_user_done_d <= selected_user_done;
            if (!user_stream_enable_safe || !digmod_source_user)
                user_stream_done_r <= 1'b0;
            else if (user_stream_done_pulse)
                user_stream_done_r <= 1'b1;
        end
    end
    rrc_digital_mod u_rrc (
        .clk(sys_clk), .rst_n(rst_n), .enable(rrc_active), .mode(digmod_mode),
        .prbs_seed(digmod_seed),
        .rolloff(digmod_rolloff),
        .sym_phase_step(digmod_sym_phase_step),
        .source_user(digmod_source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank),
        .bram_b_addr(rrc_bram_b_addr), .bram_b_en(rrc_bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_stream_enable(user_stream_enable_safe),
        .user_stream_length(user_stream_payload_length),
        .user_stream_tdata(user_stream_tdata_safe),
        .user_stream_tkeep(user_stream_tkeep_safe),
        .user_stream_tvalid(user_stream_tvalid_safe),
        .user_stream_tready(rrc_stream_tready),
        .user_stream_tlast(user_stream_tlast_safe),
        .user_done(rrc_user_done), .user_underflow(rrc_user_underflow),
        .user_ready(rrc_user_ready),
        .frame_done_pulse(rrc_frame_done_pulse), .drain_active(rrc_drain_active),
        .i_sample(rrc_i16), .q_sample(rrc_q16),
        .sample_valid(rrc_sample_valid), .symbol_valid()
    );

    assign play_frame_done_pulse = selected_user_done_pulse;
    assign play_drain_done_pulse = rrc_active && rrc_frame_done_pulse;
    assign play_drain_active = rrc_active && rrc_drain_active;
    playback_ctrl u_play (
        .clk(sys_clk), .rst_n(rst_n),
        .manual_mode(play_manual_mode),
        .trigger_external(play_trigger_external),
        .trigger_polarity(play_trigger_polarity),
        .trigger_supported(1'b0), .trigger_in(1'b0),
        .source_user(digmod_source_user),
        .user_committed(user_committed_sys), .user_loop(user_loop),
        .mode_valid(digmod_enable),
        .source_ready(rrc_active ? rrc_user_ready : iq_user_ready),
        .frame_done_pulse(play_frame_done_pulse),
        .drain_active(play_drain_active), .drain_done_pulse(play_drain_done_pulse),
        .frame_underflow(rrc_active ? rrc_user_underflow : iq_user_underflow),
        .cmd_code(play_cmd_code), .cmd_epoch(play_cmd_epoch),
        .repeat_cfg(play_repeat),
        .state(play_state), .frame_done(play_frame_done),
        .underflow(play_underflow), .trigger_seen(play_trigger_seen),
        .stop_pending(play_stop_pending), .error_valid(play_error_valid),
        .repeat_remaining(play_repeat_remaining), .error_code(play_error_code),
        .run_enable(play_run_enable), .restart(play_restart)
    );

    wire [31:0] play_status_raw = {play_error_code, play_repeat_remaining,
                                   play_error_valid, play_stop_pending,
                                   play_trigger_seen, play_underflow,
                                   play_frame_done, play_state};
    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            user_commit_epoch_d <= 1'b0;
            user_committed_sys <= 1'b0;
        end else begin
            user_commit_epoch_d <= user_commit_epoch;
            if (user_commit_epoch != user_commit_epoch_d)
                user_committed_sys <= 1'b1;
        end
    end
    reg [31:0] play_status_snapshot;
    reg play_status_req;
    reg play_status_ack_sync1, play_status_ack_sync2;
    reg [31:0] play_status_data_sync1, play_status_data_sync2;
    reg play_status_req_sync1, play_status_req_sync2, play_status_req_seen;
    reg play_status_capture_pending;
    reg play_status_ack;
    reg [31:0] play_status_out_reg = 32'd0;
    reg user_stream_done_status_meta;
    reg user_stream_done_status;
    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            play_status_snapshot <= 32'd0;
            play_status_req <= 1'b0;
            play_status_ack_sync1 <= 1'b0;
            play_status_ack_sync2 <= 1'b0;
        end else begin
            play_status_ack_sync1 <= play_status_ack;
            play_status_ack_sync2 <= play_status_ack_sync1;
            if ((play_status_raw != play_status_snapshot) &&
                (play_status_req == play_status_ack_sync2)) begin
                play_status_snapshot <= play_status_raw;
                play_status_req <= ~play_status_req;
            end
        end
    end
    always @(posedge status_clk or negedge rst_n) begin
        if (!rst_n) begin
            play_status_data_sync1 <= 32'd0;
            play_status_data_sync2 <= 32'd0;
            play_status_req_sync1 <= 1'b0;
            play_status_req_sync2 <= 1'b0;
            play_status_req_seen <= 1'b0;
            play_status_capture_pending <= 1'b0;
            play_status_ack <= 1'b0;
            play_status_out_reg <= 32'd0;
            user_stream_done_status_meta <= 1'b0;
            user_stream_done_status <= 1'b0;
        end else begin
            play_status_data_sync1 <= play_status_snapshot;
            play_status_data_sync2 <= play_status_data_sync1;
            play_status_req_sync1 <= play_status_req;
            play_status_req_sync2 <= play_status_req_sync1;
            user_stream_done_status_meta <= user_stream_done_r;
            user_stream_done_status <= user_stream_done_status_meta;
            if (play_status_capture_pending) begin
                play_status_out_reg <= play_status_data_sync2;
                play_status_ack <= play_status_req_seen;
                play_status_capture_pending <= 1'b0;
            end else if (play_status_req_sync2 != play_status_req_seen) begin
                play_status_req_seen <= play_status_req_sync2;
                play_status_capture_pending <= 1'b1;
            end
        end
    end
    assign play_status_out = play_status_out_reg;

    assign bram_b_clk = sys_clk;
    // BRAM 配为 32 位字节寻址(Enable_32bit_Address=1), A 口由 AXI BRAM ctrl 驱动的
    // 也是字节地址, 故 B 口必须同样给字节地址(word << 2)才能与 A 口编址一致。
    assign bram_b_addr = {22'd0, (rrc_active ? rrc_bram_b_addr : iq_bram_b_addr), 2'b00};
    assign bram_b_en = rrc_active ? rrc_bram_b_en : iq_bram_b_en;
    assign tap_b_addr = {tap_word_addr, 2'b00};
    assign user_stream_tready = digmod_filter ? rrc_stream_tready : iq_stream_tready;

    // GPIO14 用户状态；bit6 为 DDR stream 在数字链路(含 RRC drain)完成后的 sticky 标志。
    assign user_valid = user_committed_sys && (user_length != 13'd0);
    assign user_busy = 1'b0;
    wire user_ready_status = user_valid &&
                             (!digmod_source_user ||
                              (rrc_active ? rrc_user_ready :
                               rect_active ? iq_user_ready : 1'b0));
    wire user_done = rrc_active ? rrc_user_done : iq_user_done;
    wire user_underflow = rrc_active ? rrc_user_underflow : iq_user_underflow;
    assign user_stream_underflow = user_underflow;
    assign gpio14_out = {25'd0, user_stream_done_status, user_busy, user_active_bank, user_ready_status,
                         user_underflow, user_done, user_valid};

    function signed [7:0] rrc_to_dac;
        input signed [15:0] value;
        reg signed [17:0] value_ext;
        reg signed [17:0] magnitude;
        reg signed [17:0] rounded;
        begin
            value_ext = {{2{value[15]}}, value};
            if (value_ext >= 0) begin
                rounded = (value_ext + 18'sd128) >>> 8;
            end else begin
                magnitude = -value_ext;
                rounded = -((magnitude + 18'sd128) >>> 8);
            end
            if (rounded > 18'sd127)
                rrc_to_dac = 8'sd127;
            else if (rounded < -18'sd127)
                rrc_to_dac = -8'sd127;
            else
                rrc_to_dac = rounded[7:0];
        end
    endfunction

    wire signed [7:0] rrc_i8 = rrc_to_dac(rrc_i16);
    wire signed [7:0] rrc_q8 = rrc_to_dac(rrc_q16);

    // ---- 复数 I/Q 损伤级(全链路唯一的复数节点) ----
    // 只作用于**数字调制支路**(rect/RRC 二选一之后的 I/Q 对); 标准 AWG 的 a1/a2
    // 完全不经此级 —— 这是"关闭时与改动前逐位一致"验收标准的前提。
    // 参数取自抽头表 BRAM 字 8/9/10(通道无关), 使能借 CH1 控制字 bit11。
    wire signed [7:0] dm_i = rrc_active ? rrc_i8 : dig_i;
    wire signed [7:0] dm_q = rrc_active ? rrc_q8 : dig_q;
    wire signed [7:0] iq_i, iq_q;
    // 参数来自抽头表 BRAM 字 8/9/10(通道无关), 由下述 chan_cfg 排空;
    // **声明必须在 iq_impair 实例之前** —— 否则会被当成隐式线网, 与后面的
    // 显式声明冲突(VRFC 10-2938)。
    wire [31:0] iq_coef_ab, iq_coef_cd, iq_fword;
    iq_impair u_iq_impair (
        .clk(sys_clk), .rst_n(rst_n), .enable(iq_impair_en),
        .i_in(dm_i), .q_in(dm_q),
        .coef_ab(iq_coef_ab), .coef_cd(iq_coef_cd), .fword(iq_fword),
        .i_out(iq_i), .q_out(iq_q)
    );

    // 最终选择后进信道损伤级(chan_impair)。chan_en=0 时为逐位 1 拍寄存透传,
    // 整条通路因此比原设计多一级 50 MHz 流水(自由运行流, 恒定 20 ns 相移无害)。
    // 数字调制支路取 iq_impair 的输出(iq_impair_en=0 时逐位透传, 与改动前一致)。
    wire [7:0] dac_sel0 = (rrc_active || rect_active) ? iq_i : a1;
    wire [7:0] dac_sel1 = (rrc_active || rect_active) ? iq_q : a2;

    // ---- 信道损伤: 抽头表配置(chan_cfg) + 双通道损伤级(chan_impair) ----
    wire [35:0] ch1_gain, ch1_delay, ch2_gain, ch2_delay;
    // 干扰源参数(数值走 BRAM, 仅在 COMMIT 时经 chan_cfg 锁存)
    wire [23:0] ch1_tone_fword, ch2_tone_fword;
    wire [6:0]  ch1_tone_ampl,  ch2_tone_ampl;
    wire [15:0] ch1_imp_period, ch2_imp_period;
    wire [7:0]  ch1_imp_width,  ch2_imp_width;
    wire signed [7:0] ch1_imp_ampl, ch2_imp_ampl;
    chan_cfg u_chan_cfg (
        .clk(sys_clk), .rst_n(rst_n),
        .ch1_commit(ch1_tap_commit), .ch2_commit(ch2_tap_commit),
        .tap_b_addr(tap_word_addr), .tap_b_en(tap_b_en), .tap_b_rdata(tap_b_rdata),
        .ch1_gain(ch1_gain), .ch1_delay(ch1_delay),
        .ch2_gain(ch2_gain), .ch2_delay(ch2_delay),
        .ch1_tone_fword(ch1_tone_fword), .ch1_tone_ampl(ch1_tone_ampl),
        .ch1_imp_period(ch1_imp_period), .ch1_imp_width(ch1_imp_width),
        .ch1_imp_ampl(ch1_imp_ampl),
        .ch2_tone_fword(ch2_tone_fword), .ch2_tone_ampl(ch2_tone_ampl),
        .ch2_imp_period(ch2_imp_period), .ch2_imp_width(ch2_imp_width),
        .ch2_imp_ampl(ch2_imp_ampl),
        .iq_coef_ab(iq_coef_ab), .iq_coef_cd(iq_coef_cd), .iq_fword(iq_fword)
    );

    wire [7:0] chan_i0, chan_i1;
    chan_impair #(.SEED(32'h00C0FFEE)) u_chan0 (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch1_chan_en),
        .sample_in(dac_sel0), .tap_gain(ch1_gain), .tap_delay(ch1_delay),
        .noise_level(ch1_noise),
        .tone_en(ch1_tone_en), .tone_fword(ch1_tone_fword), .tone_ampl(ch1_tone_ampl),
        .impulse_en(ch1_imp_en), .impulse_period(ch1_imp_period),
        .impulse_width(ch1_imp_width), .impulse_ampl(ch1_imp_ampl),
        .sample_out(chan_i0)
    );
    chan_impair #(.SEED(32'h00DEADBEEF)) u_chan1 (
        .clk(sys_clk), .rst_n(rst_n), .enable(ch2_chan_en),
        .sample_in(dac_sel1), .tap_gain(ch2_gain), .tap_delay(ch2_delay),
        .noise_level(ch2_noise),
        .tone_en(ch2_tone_en), .tone_fword(ch2_tone_fword), .tone_ampl(ch2_tone_ampl),
        .impulse_en(ch2_imp_en), .impulse_period(ch2_imp_period),
        .impulse_width(ch2_imp_width), .impulse_ampl(ch2_imp_ampl),
        .sample_out(chan_i1)
    );

    dac_driver u_dac (
        .clk(sys_clk), .rst_n(rst_n),
        .sample0(chan_i0), .sample1(chan_i1),
        .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );

    // LED: CH1 波形编号 (LED0~1) + CH1 频率粗刻度 freq_khz[9:4] (LED2~7)
    wire [9:0] freq_khz = ch1_freq / 20'd1000;
    assign led[1:0] = ch1_wave;
    assign led[7:2] = freq_khz[9:4];
endmodule
