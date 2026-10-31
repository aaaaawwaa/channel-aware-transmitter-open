`timescale 1ns / 1ps
module reg_bridge_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] gpio0_in = 0, gpio1_in = 0;
    reg [31:0] gpio2_in = 0, gpio3_in = 0, gpio4_in = 0, gpio5_in = 0;
    reg [31:0] gpio6_in = 0, gpio7_in = 0, gpio8_in = 0, gpio9_in = 0;
    reg [31:0] gpio10_in = 0, gpio11_in = 0, gpio12_in = 0, gpio13_in = 0;
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
    wire ch_link_en;
    wire [31:0] ch_phase_off;
    wire [2:0] digmod_sel;
    wire digmod_filter;
    wire [1:0] digmod_rolloff;
    wire [6:0] digmod_seed;
    wire digmod_source_user;
    wire user_loop;
    wire user_commit_epoch;
    wire [8:0] user_length_bytes;
    wire [12:0] user_length;
    wire user_active_bank;
    wire [31:0] digmod_sym_phase_step;
    // 信道损伤控制字(每信道独立 32 位 AXI GPIO 输出)
    reg [31:0] ch1_chan_word_in = 0, ch2_chan_word_in = 0;
    wire ch1_chan_en, ch2_chan_en;
    wire [6:0] ch1_noise, ch2_noise;
    wire ch1_tap_commit, ch2_tap_commit;
    wire ch1_tone_en, ch1_imp_en, ch2_tone_en, ch2_imp_en;
    wire iq_impair_en;

    reg_bridge uut (
        .clk(clk), .rst_n(rst_n),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .gpio2_in(gpio2_in), .gpio3_in(gpio3_in),
        .gpio4_in(gpio4_in), .gpio5_in(gpio5_in),
        .gpio10_in(gpio10_in), .gpio11_in(gpio11_in), .gpio12_in(gpio12_in),
        .gpio13_in(gpio13_in),
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
        .gpio6_in(gpio6_in), .gpio7_in(gpio7_in),
        .gpio8_in(gpio8_in), .gpio9_in(gpio9_in),
        .ch1_mode(ch1_mode), .ch2_mode(ch2_mode),
        .ch1_mwave(ch1_mwave), .ch2_mwave(ch2_mwave),
        .ch1_mfreq(ch1_mfreq), .ch2_mfreq(ch2_mfreq),
        .ch1_am_depth(ch1_am_depth), .ch2_am_depth(ch2_am_depth),
        .ch1_fm_dev(ch1_fm_dev), .ch2_fm_dev(ch2_fm_dev),
        .ch1_pm_dev(ch1_pm_dev), .ch2_pm_dev(ch2_pm_dev),
        .ch_link_en(ch_link_en), .ch_phase_off(ch_phase_off),
        .digmod_sel(digmod_sel),
        .digmod_filter(digmod_filter),
        .digmod_rolloff(digmod_rolloff),
        .digmod_seed(digmod_seed),
        .digmod_source_user(digmod_source_user),
        .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch),
        .user_length_bytes(user_length_bytes),
        .user_length(user_length),
        .user_active_bank(user_active_bank),
        .digmod_sym_phase_step(digmod_sym_phase_step),
        .ch1_chan_word_in(ch1_chan_word_in), .ch2_chan_word_in(ch2_chan_word_in),
        .ch1_chan_en(ch1_chan_en), .ch2_chan_en(ch2_chan_en),
        .ch1_noise(ch1_noise), .ch2_noise(ch2_noise),
        .ch1_tap_commit(ch1_tap_commit), .ch2_tap_commit(ch2_tap_commit),
        .ch1_tone_en(ch1_tone_en), .ch1_imp_en(ch1_imp_en),
        .ch2_tone_en(ch2_tone_en), .ch2_imp_en(ch2_imp_en),
        .iq_impair_en(iq_impair_en)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    function [31:0] pack;
        input [19:0] f;
        input [7:0] a;
        input [2:0] w;
        begin
            pack = {1'b0, w, a, f};
        end
    endfunction

    initial begin
        // 上电默认(复位释放后,PS 尚未写入):1kHz/50%/SINE
        #100 rst_n = 1;
        #60;   // 2-FF 同步 + 组合 -> 2 clk 内稳定
        chk("default ch1 freq=1000", ch1_freq == 20'd1000);
        chk("default ch1 ampl=50",   ch1_ampl == 8'd50);
        chk("default ch1 wave=0",    ch1_wave == 3'd0);
        chk("default ch2 freq=1000", ch2_freq == 20'd1000);
        chk("default ch2 ampl=50",   ch2_ampl == 8'd50);
        chk("default ch2 wave=0",    ch2_wave == 3'd0);

        // CH1: 100kHz/80%/SQUARE ; CH2: 2500Hz/20%/SAW
        gpio0_in = pack(20'd100000, 8'd80, 3'd1);
        gpio1_in = pack(20'd2500,   8'd20, 3'd3);
        #40;
        chk("ch1 freq=100000", ch1_freq == 20'd100000);
        chk("ch1 ampl=80",     ch1_ampl == 8'd80);
        chk("ch1 wave=1",      ch1_wave == 3'd1);
        chk("ch2 freq=2500",   ch2_freq == 20'd2500);
        chk("ch2 ampl=20",     ch2_ampl == 8'd20);
        chk("ch2 wave=3",      ch2_wave == 3'd3);

        // 3-bit PULSE code and GPIO11 duty fields; bit0 must survive.
        gpio0_in = pack(20'd1000, 8'd50, 3'd4);
        gpio11_in = {15'd0, 8'd75, 8'd25, 1'b1};
        #40;
        chk("ch1 wave=4 pulse", ch1_wave == 3'd4);
        chk("duty fields unpack", ch1_pulse_duty == 8'd25 && ch2_pulse_duty == 8'd75);
        chk("duty preserves link", ch_link_en == 1'b1);

        // duty values above 100 are clamped independently.
        gpio11_in = {15'd0, 8'd200, 8'd101, 1'b0};
        #40;
        chk("duty clamp", ch1_pulse_duty == 8'd100 && ch2_pulse_duty == 8'd100);

        // Restore a normal CH1 code for the remaining unpack tests.
        gpio0_in = pack(20'd100000, 8'd80, 3'd1);
        gpio11_in = 32'd0;
        #40;
        gpio0_in = pack(20'd5, 8'd50, 3'd0);
        #40;
        chk("clamp freq 5->10", ch1_freq == 20'd10);
        chk("clamp keeps ampl", ch1_ampl == 8'd50);

        // 双通道独立:改 CH1 不影响 CH2
        gpio0_in = pack(20'd999, 8'd33, 3'd2);
        #40;
        chk("ch1 updated", ch1_freq == 20'd999 && ch1_ampl == 8'd33 && ch1_wave == 3'd2);
        chk("ch2 untouched", ch2_freq == 20'd2500 && ch2_ampl == 8'd20 && ch2_wave == 3'd3);

        // ---- 谐波字拆包 ----
        // 字A: {base[7:0], h2_amp[6:0], h2_ord[3:0], h1_amp[6:0], h1_ord[3:0]}
        // 字B: {reserved[9:0], h4_amp[6:0], h4_ord[3:0], h3_amp[6:0], h3_ord[3:0]}
        gpio2_in = {8'd100, 7'd40, 4'd3, 7'd30, 4'd2};   // CH1 字A
        gpio3_in = {8'd100, 7'd40, 4'd3, 7'd30, 4'd2};   // CH2 字A
        gpio4_in = {10'd0,  7'd20, 4'd5, 7'd10, 4'd4};   // CH1 字B
        gpio5_in = {10'd0,  7'd20, 4'd5, 7'd10, 4'd4};   // CH2 字B
        #40;
        chk("ch1 base=100",      ch1_base == 8'd100);
        chk("ch1 h1 ord=2 amp=30", ch1_h1_ord == 4'd2 && ch1_h1_amp == 8'd30);
        chk("ch1 h2 ord=3 amp=40", ch1_h2_ord == 4'd3 && ch1_h2_amp == 8'd40);
        chk("ch1 h3 ord=4 amp=10", ch1_h3_ord == 4'd4 && ch1_h3_amp == 8'd10);
        chk("ch1 h4 ord=5 amp=20", ch1_h4_ord == 4'd5 && ch1_h4_amp == 8'd20);
        chk("ch2 mirrors ch1",    ch2_base == 8'd100 && ch2_h1_ord == 4'd2 && ch2_h4_amp == 8'd20);

        // 全 0(未写入)→ 无哨兵:base=0、ord=0(静音配置可达)
        gpio2_in = 32'd0;
        #40;
        chk("wordA=0 -> base 0 (no sentinel)", ch1_base == 8'd0 && ch1_h1_ord == 4'd0);

        // ---- 调制字拆包 ----
        // 字C: {am_depth[7:0], mfreq[18:0], mwave[1:0], mode[2:0]}
        // 字D: {reserved[3:0], pm_dev[8:0], fm_dev[18:0]}
        gpio6_in = {8'd100, 19'd500, 2'd2, 3'd4};   // CH1 字C
        gpio7_in = {8'd80,  19'd100, 2'd1, 3'd1};   // CH2 字C
        gpio8_in = {4'd0, 9'd128, 19'd50000};       // CH1 字D
        gpio9_in = {4'd0, 9'd64, 19'd1000};         // CH2 字D
        #40;
        chk("ch1 mode/mwave/mfreq/depth",
            ch1_mode == 3'd4 && ch1_mwave == 2'd2 &&
            ch1_mfreq == 19'd500 && ch1_am_depth == 8'd100);
        chk("ch1 fm/pm dev", ch1_fm_dev == 19'd50000 && ch1_pm_dev == 9'd128);
        chk("ch2 mode/mwave/mfreq/depth",
            ch2_mode == 3'd1 && ch2_mwave == 2'd1 &&
            ch2_mfreq == 19'd100 && ch2_am_depth == 8'd80);
        chk("ch2 fm/pm dev", ch2_fm_dev == 19'd1000 && ch2_pm_dev == 9'd64);
        // 全 0 -> 合法关闭态(无哨兵)
        gpio6_in = 32'd0; gpio8_in = 32'd0;
        #40;
        chk("wordC/D=0 -> mode 0",
            ch1_mode == 3'd0 && ch1_fm_dev == 19'd0 && ch1_am_depth == 8'd0);

        // 字D 保留位 [31:28] 非 0:不得泄漏进 pm_dev[27:19]/fm_dev[18:0]
        gpio8_in = {4'hF, 9'd64, 19'd1000};
        #40;
        chk("reserved bits ignored", ch1_pm_dev == 9'd64 && ch1_fm_dev == 19'd1000);

        // ---- 通道关系字: gpio10 = 相位差, gpio11 bit0 = 联动使能 ----
        gpio10_in = 32'h4000_0000;   // 90° (2^32/4)
        gpio11_in = 32'd1;
        #100;
        chk("phase_off unpack 0x40000000", ch_phase_off == 32'h4000_0000);
        chk("link_en unpack bit0", ch_link_en == 1'b1);
        gpio11_in = 32'd0;
        #100;
        chk("link_en clear -> 0", ch_link_en == 1'b0);

        // ---- 数字调制全局模式: gpio11[19:17] = digmod_sel ----
        // 与相位联动/两路 DUTY 同字, 低 17 位必须保持
        gpio11_in = {12'd0, 3'd4, 8'd75, 8'd25, 1'b1};   // QAM16, duty 25/75, link on
        #40;
        chk("digmod_sel unpack", digmod_sel == 3'd4);
        chk("digmod preserves duty", ch1_pulse_duty == 8'd25 && ch2_pulse_duty == 8'd75);
        chk("digmod preserves link", ch_link_en == 1'b1);
        gpio11_in = {12'd0, 3'd7, 8'd50, 8'd50, 1'b0};   // 保留编码 7 直通
        #40;
        chk("digmod_sel reserved passes", digmod_sel == 3'd7);
        gpio11_in = 32'd0;
        #40;
        chk("digmod_sel off", digmod_sel == 3'd0);

        gpio11_in = 32'h0010_0000 | (32'd2 << 17) | (32'd25 << 1) |
                    (32'd75 << 9) | 32'h1;
        #40;
        chk("digmod_filter bit20", digmod_filter == 1'b1);
        chk("filter preserves mode", digmod_sel == 3'd2);
        chk("filter preserves duty/link",
            ch1_pulse_duty == 8'd25 && ch2_pulse_duty == 8'd75 && ch_link_en == 1'b1);
        gpio11_in = 32'd0;
        #40;
        chk("digmod_filter clear", digmod_filter == 1'b0);

        gpio12_in = 32'h0123_4567;
        #40;
        chk("digmod phase step after two FF stages",
            digmod_sym_phase_step == 32'h0123_4567);

        // ---- PRBS7 seed: gpio11[29:23] ----
        gpio11_in = (32'd127 << 23);
        #40;
        chk("digmod_seed unpack 127", digmod_seed == 7'd127);
        gpio11_in = (32'd5 << 23) | (32'd1 << 17) | 32'd1;
        #40;
        chk("digmod_seed unpack 5 with mode/link", digmod_seed == 7'd5 &&
            digmod_sel == 3'd1 && ch_link_en == 1'b1);
        gpio11_in = 32'd0;
        #40;
        chk("digmod_seed zero", digmod_seed == 7'd0);

        // GPIO13 user buffer frozen control word:
        //   bit0=source_user, bit1=loop, bit2=commit_epoch, bits[11:3]=length_bytes, bit12=active_bank.
        gpio13_in = (32'd1 << 12) | (32'd160 << 3) | (32'd1 << 2) | (32'd1 << 1) | 32'd1;
        #40;
        chk("user source control", digmod_source_user == 1'b1 && user_loop == 1'b1);
        chk("user commit epoch", user_commit_epoch == 1'b1);
        chk("user length bytes 160 -> 1280 bit", user_length_bytes == 9'd160 &&
            user_length == 13'd1280);
        chk("user active bank=1", user_active_bank == 1'b1);
        gpio13_in = 32'd0;
        #40;
        chk("user controls clear", !digmod_source_user && !user_loop &&
            !user_commit_epoch && user_length_bytes == 9'd0 && !user_active_bank);

        // ---- 信道损伤控制字: 每信道独立 32 位 GPIO ----
        // 布局: bit0=chan_en, bits[7:1]=noise(0..100, 超 100 钳位), bit8=tap_commit。
        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        #40;
        chk("chan default en off", ch1_chan_en == 1'b0 && ch2_chan_en == 1'b0);
        chk("chan default noise 0", ch1_noise == 7'd0 && ch2_noise == 7'd0);
        chk("chan default commit 0", ch1_tap_commit == 1'b0 && ch2_tap_commit == 1'b0);

        // CH1: en=1, noise=40, commit=0
        ch1_chan_word_in = {23'd0, 1'b0, 7'd40, 1'b1};
        #40;
        chk("ch1 chan en=1",      ch1_chan_en == 1'b1);
        chk("ch1 noise=40",       ch1_noise == 7'd40);
        chk("ch1 commit=0",       ch1_tap_commit == 1'b0);
        chk("ch2 chan untouched", ch2_chan_en == 1'b0 && ch2_noise == 7'd0);

        // noise > 100 钳位到 100 (noise=120)
        ch1_chan_word_in = {23'd0, 1'b0, 7'd120, 1'b1};
        #40;
        chk("ch1 noise clamp 120->100", ch1_noise == 7'd100);
        chk("ch1 en survives clamp",    ch1_chan_en == 1'b1);

        // bit8 = tap_commit epoch 位(独立于 en/noise)
        ch1_chan_word_in = {23'd0, 1'b1, 7'd0, 1'b0};
        #40;
        chk("ch1 tap_commit bit8", ch1_tap_commit == 1'b1);
        chk("commit does not force en", ch1_chan_en == 1'b0 && ch1_noise == 7'd0);

        // CH2 独立字: en=1, noise=77, commit=1; CH1 保持上一状态
        ch2_chan_word_in = {23'd0, 1'b1, 7'd77, 1'b1};
        #40;
        chk("ch2 chan word unpack", ch2_chan_en == 1'b1 && ch2_noise == 7'd77 &&
            ch2_tap_commit == 1'b1);
        chk("ch2 leaves ch1 word alone", ch1_chan_en == 1'b0 && ch1_noise == 7'd0 &&
            ch1_tap_commit == 1'b1);
        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        #40;
        chk("chan words clear", ch1_chan_en == 1'b0 && ch1_noise == 7'd0 &&
            !ch1_tap_commit && ch2_chan_en == 1'b0 && ch2_noise == 7'd0 &&
            !ch2_tap_commit);

        // ---- 新增: bit9/bit10 = TONE/IMPULSE 使能 ----
        // 拼接顺序: {bit31..bit11, bit10=imp_en, bit9=tone_en, bit8=commit, bits7:1=noise, bit0=chan_en}
        // 既有 TB 用 #40 (=2 拍); 这里用 3 拍更稳(2 级同步器 + 解码)。
        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        repeat (3) @(posedge clk); #1;
        chk("en bits off by default", ch1_tone_en == 1'b0 && ch1_imp_en == 1'b0
                                   && ch2_tone_en == 1'b0 && ch2_imp_en == 1'b0);
        // bit9 = tone_en (CH1), bit10 = 0
        ch1_chan_word_in = {22'd0, 1'b1, 1'b0, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 tone_en set",   ch1_tone_en == 1'b1);
        chk("ch1 imp_en clear",  ch1_imp_en  == 1'b0);
        chk("ch2 en unaffected", ch2_tone_en == 1'b0 && ch2_imp_en == 1'b0);
        // 既有解码不受 bit9/bit10 影响
        chk("bit9 does not disturb", ch1_chan_en == 1'b1 && ch1_noise == 7'd40
                                  && ch1_tap_commit == 1'b0);
        // bit10 = imp_en (CH1), bit9 = 0 -> tone_en 必须回落
        ch1_chan_word_in = {21'd0, 1'b1, 1'b0, 1'b1, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 imp_en set",    ch1_imp_en  == 1'b1);
        chk("ch1 tone_en clear", ch1_tone_en == 1'b0);
        // CH2 的 bit9/bit10 走它自己的字
        ch1_chan_word_in = 32'd0;
        ch2_chan_word_in = {21'd0, 1'b1, 1'b1, 1'b1, 7'd55, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch2 both en set", ch2_tone_en == 1'b1 && ch2_imp_en == 1'b1);
        chk("ch2 noise",       ch2_noise == 7'd55);
        chk("ch1 en cleared",  ch1_tone_en == 1'b0 && ch1_imp_en == 1'b0);

        // ---- 新增: bit11 = iq_impair_en(复数 I/Q 级使能) ----
        // 借 CH1 控制字的位: iq_impair 是通道对级单实例, 不属于任何单通道。
        // 拼接位序自高到低: [31:12] 保留(20) | bit11=iq_en | bit10=imp_en | bit9=tone_en
        //                   | bit8=commit | [7:1] noise | bit0=chan_en  = 32 位
        ch1_chan_word_in = {20'd0, 1'b1, 1'b0, 1'b0, 1'b1, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 iq_impair_en set", iq_impair_en == 1'b1);
        chk("bit11 disturbs nothing else",
            ch1_tone_en == 1'b0 && ch1_imp_en == 1'b0 &&
            ch1_chan_en == 1'b1 && ch1_noise == 7'd40);
        // 清掉 bit11 -> 必须回落
        ch1_chan_word_in = {21'd0, 1'b0, 1'b0, 1'b1, 7'd40, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch1 iq_impair_en clear", iq_impair_en == 1'b0);
        // 该位只在 CH1 字上解码: CH2 字把 bit11 置 1 不应驱动它
        ch1_chan_word_in = 32'd0;
        ch2_chan_word_in = {20'd0, 1'b1, 1'b0, 1'b0, 1'b0, 7'd55, 1'b1};
        repeat (3) @(posedge clk); #1;
        chk("ch2 bit11 does not drive it", iq_impair_en == 1'b0);

        ch1_chan_word_in = 32'd0; ch2_chan_word_in = 32'd0;
        repeat (3) @(posedge clk); #1;

        if (errs == 0) $display("PASS reg_bridge_tb");
        else $display("FAIL reg_bridge_tb: %0d errors", errs);
        $finish;
    end
endmodule
