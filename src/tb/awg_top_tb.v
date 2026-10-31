`timescale 1ns / 1ps
module awg_top_tb;
    reg clk = 0;
    reg status_clk = 0;
    reg [31:0] gpio0_in = 0, gpio1_in = 0;
    reg [31:0] gpio2_in = 0, gpio3_in = 0, gpio4_in = 0, gpio5_in = 0;
    reg [31:0] gpio6_in = 0, gpio7_in = 0, gpio8_in = 0, gpio9_in = 0;
    reg [31:0] gpio10_in = 0, gpio11_in = 0, gpio12_in = 32'd858993;
    reg [31:0] gpio13_in = 0;
    reg [31:0] play_ctrl_in = 0, play_cfg_in = 0;
    wire [31:0] play_status_out;
    wire [31:0] gpio14_out;
    wire bram_b_clk, bram_b_en;
    wire [31:0] bram_b_addr;
    reg [31:0] bram_b_rdata = 32'd0;
    reg [31:0] ch1_chan_word = 32'd0, ch2_chan_word = 32'd0;
    reg [31:0] tap_b_rdata = 32'd0;
    wire [5:0] tap_b_addr;
    wire tap_b_en;
    // 抽头表 BRAM 的最小模型: 字 12/13(tone/impulse)与字 8/9/10(I/Q 级)给非零值
    reg [31:0] tmem [0:15];
    initial begin
        tmem[12] = {8'd40,  24'd33554};        // tone: ampl=+40, fword=100kHz
        tmem[13] = {8'd206, 8'd2, 16'd10};     // impulse: ampl=-50, width=2, period=10
        tmem[8]  = 32'h00004000;               // I/Q 级: A=+16384, B=0
        tmem[9]  = 32'hC0000000;               //         C=0, D=-16384(负系数验保号)
        tmem[10] = 32'h40000000;               //         fword = 2^30
    end
    always @(posedge clk) if (tap_b_en) tap_b_rdata <= tmem[tap_b_addr[5:2]];
    wire [7:0] led;
    wire [7:0] dac0_db, dac1_db;
    wire daclk0, daclk1;

    awg_top uut (
        .sys_clk(clk), .status_clk(status_clk),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .gpio2_in(gpio2_in), .gpio3_in(gpio3_in),
        .gpio4_in(gpio4_in), .gpio5_in(gpio5_in),
        .gpio6_in(gpio6_in), .gpio7_in(gpio7_in),
        .gpio8_in(gpio8_in), .gpio9_in(gpio9_in),
        .gpio10_in(gpio10_in), .gpio11_in(gpio11_in),
        .gpio12_in(gpio12_in),
        .gpio13_in(gpio13_in),
        .play_ctrl_in(play_ctrl_in), .play_cfg_in(play_cfg_in),
        .play_status_out(play_status_out), .gpio14_out(gpio14_out),
        .bram_b_clk(bram_b_clk), .bram_b_en(bram_b_en),
        .bram_b_addr(bram_b_addr), .bram_b_rdata(bram_b_rdata),
        .ch1_chan_word(ch1_chan_word), .ch2_chan_word(ch2_chan_word),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en),
        .tap_b_rdata(tap_b_rdata),
        .led(led), .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );
    always #10 clk = ~clk;   // 50 MHz
    always #10 status_clk = ~status_clk;

    integer errs = 0;
    integer ch0_trans, ch1_trans;
    integer ch1_flat;
    integer ch0_max, ch0_min;
    integer ch0_zcross;
    integer ch1_zcross;
    integer ch0_flat, ch0_high, ch0_low;
    integer ch1_max, ch1_min;
    integer qam_i_02, qam_i_AA, qam_i_56, qam_i_FE;
    integer qam_q_02, qam_q_AA, qam_q_56, qam_q_FE;
    reg [7:0] prev0, prev1;
    reg counting;

    always @(posedge clk) begin
        if (counting) begin
            if (dac0_db !== prev0) begin
                // 零交叉检测: 偏移二进制 0x80 = 零点,上升沿穿过
                if (prev0 < 8'h80 && dac0_db >= 8'h80)
                    ch0_zcross = ch0_zcross + 1;
                ch0_trans = ch0_trans + 1; prev0 = dac0_db;
            end
            if (dac1_db !== prev1) begin
                if (prev1 < 8'h80 && dac1_db >= 8'h80)
                    ch1_zcross = ch1_zcross + 1;
                ch1_trans = ch1_trans + 1; prev1 = dac1_db;
            end
            if (uut.digmod_enable && uut.digmod_mode == 3'd3) begin
                case (dac0_db)
                    8'h02: qam_i_02 = qam_i_02 + 1;
                    8'hAA: qam_i_AA = qam_i_AA + 1;
                    8'h56: qam_i_56 = qam_i_56 + 1;
                    8'hFE: qam_i_FE = qam_i_FE + 1;
                endcase
                case (dac1_db)
                    8'h02: qam_q_02 = qam_q_02 + 1;
                    8'hAA: qam_q_AA = qam_q_AA + 1;
                    8'h56: qam_q_56 = qam_q_56 + 1;
                    8'hFE: qam_q_FE = qam_q_FE + 1;
                endcase
            end
            if (dac0_db > ch0_max) ch0_max = dac0_db;
            if (dac0_db < ch0_min) ch0_min = dac0_db;
            if (dac1_db > ch1_max) ch1_max = dac1_db;
            if (dac1_db < ch1_min) ch1_min = dac1_db;
            if (dac0_db == 8'h80) ch0_flat = ch0_flat + 1;
            if (dac0_db == 8'hFF) ch0_high = ch0_high + 1;
            if (dac0_db == 8'h01) ch0_low = ch0_low + 1;
            if (dac1_db == 8'h80) ch1_flat = ch1_flat + 1;
        end
    end

    task chk;
        input string name;
        input ok;
        begin
            if (ok) $display("PASS %s", name);
            else begin $display("FAIL %s", name); errs = errs + 1; end
        end
    endtask

    task reset_stats;
        begin
            counting   = 0;
            ch0_trans  = 0;  ch1_trans  = 0;
            ch0_max    = 0;  ch0_min    = 255;
            ch0_zcross = 0;
            ch1_zcross = 0;
            ch0_flat   = 0;
            ch0_high   = 0;
            ch0_low    = 0;
            ch1_flat   = 0;
            ch1_max    = 0;  ch1_min    = 255;
            qam_i_02   = 0;  qam_i_AA = 0;  qam_i_56 = 0;  qam_i_FE = 0;
            qam_q_02   = 0;  qam_q_AA = 0;  qam_q_56 = 0;  qam_q_FE = 0;
            @(posedge clk);
            prev0      = dac0_db; prev1 = dac1_db;
            counting  <= 1;
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
        counting = 0;
        wait (uut.rst_n == 1'b1);
        #1000;     // let pipeline settle

        // ---- 信道损伤: 默认未配置, chan_en=0, tap 表未锁存 ----
        chk("chan_en defaults low",
            uut.ch1_chan_en === 1'b0 && uut.ch2_chan_en === 1'b0);
        chk("chan noise defaults zero",
            uut.ch1_noise === 7'd0 && uut.ch2_noise === 7'd0);
        begin : tbyp
            reg [7:0] sel0_q, sel1_q;
            @(negedge clk);
            sel0_q = uut.dac_sel0; sel1_q = uut.dac_sel1;
            @(posedge clk); #1;
            chk("chan bypass ch0 is 1-cycle registered passthrough",
                uut.chan_i0 === sel0_q);
            chk("chan bypass ch1 is 1-cycle registered passthrough",
                uut.chan_i1 === sel1_q);
        end

        // 默认(未写入):1kHz/50%/SINE 双通道
        // 1 kHz 周期 = 1 ms = 50,000 clk; 1 ms 窗口观察 ~1 个完整正弦周期
        reset_stats;
        #1_000_000;   // 1 ms, ~256 LUT entries, ~200-250 unique DAC transitions
        chk("default ch0 active (1 kHz, 1 ms)", ch0_trans > 100);
        chk("default ch1 active (1 kHz, 1 ms)", ch1_trans > 100);
        chk("default ch0 ampl 50% range [0x41,0xBF]", ch0_min >= 8'h41 && ch0_max <= 8'hBF);
        chk("default ch1 ampl 50% range [0x41,0xBF]", ch1_min >= 8'h41 && ch1_max <= 8'hBF);
        chk("default LED=0", led == 8'h00);

        // CH1 NOISE: deterministic pseudo-random carrier uses the normal FREQ/AMPL path.
        gpio0_in = pack(20'd100000, 8'd100, 3'd5);
        #1000;
        reset_stats;
        #200_000;
        chk("noise active transitions", ch0_trans > 1000);
        chk("noise bipolar full range", ch0_min <= 8'h10 && ch0_max >= 8'hF0);

        // CH1: 100kHz/100%/SINE ; CH2: 1kHz/50%/SQUARE
        gpio0_in = pack(20'd100000, 8'd100, 3'd0);
        gpio1_in = pack(20'd1000,   8'd50,  3'd1);
        #1000;
        reset_stats;
        #200_000;   // 200 us: 100 kHz gets 20 periods, 1 kHz gets 0.2 periods
        chk("ch0 100kHz more trans than ch1", ch0_trans > ch1_trans * 20);
        chk("ch0 100% full swing (min<=0x06)", ch0_min <= 8'h06);
        chk("ch0 100% full swing (max>=0xFA)", ch0_max >= 8'hFA);
        chk("ch1 square 50% range [0x40,0xBF]", ch1_min >= 8'h40 && ch1_max <= 8'hBF);
        chk("LED freq 100k -> 0x18", led == 8'h18);

        // CH2 独立:改 CH2 频率为 10kHz,CH1 不动
        gpio1_in = pack(20'd10000, 8'd50, 3'd1);
        #1000;
        reset_stats;
        #100_000;
        chk("ch1 unchanged after ch2 edit (still fast)", ch0_trans > 0);
        chk("LED still shows ch1 100kHz", led == 8'h18);

        // 幅度 0% -> 输出恒为偏移二进制中点 0x80
        gpio0_in = pack(20'd100000, 8'd0, 3'd0);
        #1000;
        reset_stats;
        #200_000;
        chk("ch0 ampl 0 -> const 0x80", ch0_trans == 0 && prev0 == 8'h80);

        // 波形切换:CH1 -> SAW,LED[1:0]=3
        gpio0_in = pack(20'd100000, 8'd100, 3'd3);
        #20_000;
        chk("LED wave=3", (led & 2'b11) == 2'b11);

        // ---- PULSE: GPIO11 duty=25%,载波波形编码=4;板上物理高电平对应 DAC 低码 ----
        gpio0_in = pack(20'd100000, 8'd100, 3'd4);
        gpio11_in = {15'd0, 8'd50, 8'd25, 1'b0};
        #1000;
        reset_stats;
        #200_000;
        chk("pulse output reaches both levels", ch0_min <= 8'h02 && ch0_max >= 8'hFE);
        chk("pulse duty 25 percent", ch0_low > 0 && ch0_high > ch0_low * 2 &&
            ch0_high < ch0_low * 6);

        // PULSE=100% must remain board-high, including phase byte 255.
        gpio11_in = {15'd0, 8'd50, 8'd100, 1'b0};
        #1000;
        reset_stats;
        #100_000;
        chk("pulse duty 100 percent board-high", ch0_min <= 8'h02 && ch0_max <= 8'h02 &&
            ch0_high == 0);

        // Restore SINE before the harmonic chain.
        gpio0_in = pack(20'd100000, 8'd100, 3'd0);
        gpio11_in = 32'd0;
        #1000;

        // ---- 谐波链路:CH1 正弦 + 2 次谐波(归一化值:base=67 + h1=33,sum=100)----
        gpio0_in = pack(20'd100000, 8'd100, 3'd0);   // 100kHz SINE 100%
        gpio2_in = {8'd67, 7'd0, 4'd0, 7'd33, 4'd2}; // 字A: base=67, h1(2nd)=33 (归一化)
        gpio3_in = {8'd67, 7'd0, 4'd0, 7'd33, 4'd2};
        ch0_trans = 0; ch0_zcross = 0; ch0_max = 0; ch0_min = 255; prev0 = 8'hFF;
        #100_000;
        chk("harm path active (transitions)", ch0_trans > 500);
        // base=67/h1=33: 基波主导,过零数与纯基波相同(≈11),
        // 但幅值被 base=67 限制 → 峰值低于满幅 240
        chk("harm path alters amplitude (base=67 limits peak)",
            ch0_max < 8'hF0 && ch0_min > 3);   // max=222 < 240, min=7 > 3

        // 纯 2 次谐波(base=0, h1=100):过零数 ≈ 2 倍,峰值满幅
        gpio2_in = {8'd0, 7'd0, 4'd0, 7'd100, 4'd2};
        ch0_trans = 0; ch0_zcross = 0; ch0_max = 0; ch0_min = 255; prev0 = 8'hFF;
        #100_000;
        chk("pure 2nd harmonic zcross ~ 2x fundamental", ch0_zcross > 17 && ch0_zcross < 23);
        chk("pure 2nd harmonic full scale (max>=0xFA)", ch0_max >= 8'hFA);

        // 谐波全 0 → 退化纯基波:过零 ≈ 10(10 周期上升过零)
        gpio2_in = 32'd0;
        ch0_trans = 0; ch0_zcross = 0; ch0_max = 0; ch0_min = 255; prev0 = 8'hFF;
        #100_000;
        chk("all-zero harms -> pure fundamental (zcross ~10)", ch0_zcross > 8 && ch0_zcross < 14);
        // 纯基波 100% 振幅应满幅
        chk("all-zero harms full scale (max>=0xFA)", ch0_max >= 8'hFA);

        // 方波不受谐波影响:wave=1 时仍走 dds_core(谐波配置保留但不用)
        gpio0_in = pack(20'd100000, 8'd100, 3'd1);   // SQUARE
        ch0_trans = 0; ch0_zcross = 0; ch0_max = 0; ch0_min = 255; prev0 = 8'hFF;
        #100_000;
        chk("square unaffected by harms (range full)", ch0_min <= 8'h02 && ch0_max >= 8'hFE);

        // ---- 调制: 字 C/D 拆包(内部探针) ----
        gpio0_in = pack(20'd100000, 8'd50, 3'd0);   // 100kHz SINE 50%
        gpio6_in = {8'd100, 19'd500, 2'd2, 3'd4};   // 字C: depth100 mfreq500(50Hz) tri SWEEP
        gpio8_in = {4'd0, 9'd128, 19'd50000};       // 字D: pm128 fm_dev50000
        #1000;
        chk("wordC unpack m/mw/mf/depth",
            uut.ch1_mode == 3'd4 && uut.ch1_mwave == 2'd2 &&
            uut.ch1_mfreq == 19'd500 && uut.ch1_am_depth == 8'd100);
        chk("wordD unpack fm/pm",
            uut.ch1_fm_dev == 19'd50000 && uut.ch1_pm_dev == 9'd128);

        // ---- AM: 方波调制(mfreq=500000 -> m 每 ~500 拍翻转), 增益 0/2 倍 ----
        gpio0_in = pack(20'd100000, 8'd100, 3'd0);     // 满幅: gain2 -> 0xFD+
        gpio6_in = {8'd100, 19'd500000, 2'd1, 3'd1};   // mode=AM, mwave=SQUARE
        gpio8_in = 32'd0;
        #1000;
        reset_stats;
        #200_000;
        chk("AM gain2 full swing",
            ch0_max >= 8'hFD && ch0_min == 8'h00);
        chk("AM gain0 flat 0x80 runs",
            ch0_flat > 1000 && ch0_trans > 500);

        // ---- FM: fword_inst 交替 base±dev(内部探针) ----
        // fword1(100kHz)=8590000; dev(50kHz)=4295000 -> {12885000, 4295000}
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd2};   // mode=FM
        gpio8_in = {4'd0, 9'd0, 19'd50000};
        #1000;
        begin : tfm
            integer i, okhi, oklo, bad;
            okhi = 0; oklo = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst1 == 32'd12885000) okhi = okhi + 1;
                else if (uut.fword_inst1 == 32'd4295000) oklo = oklo + 1;
                else bad = bad + 1;
            end
            chk("FM fword_inst alt base+dev", okhi > 1000 && oklo > 1000 && bad == 0);
        end

        // ---- PM: pm_off 交替 ±128; 谐波地址偏移 ord×pm_off ----
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd3};   // mode=PM
        gpio8_in = {4'd0, 9'd128, 19'd0};
        #1000;
        begin : tpm
            integer i, okp, okn, bad;
            okp = 0; okn = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.pm_off1 == 9'sd128) okp = okp + 1;
                else if (uut.pm_off1 == -9'sd128) okn = okn + 1;
                else bad = bad + 1;
                // 基波地址 = acc0 + pm_off_r(mod 256), pm_off 已在 awg_top 寄存一拍
                if (uut.u_harm1.s0 !== (uut.u_harm1.acc0[31:24] + uut.pm_off1_r[7:0]))
                    bad = bad + 1;
            end
            chk("PM pm_off alt +-128 s0 off",
                okp > 1000 && okn > 1000 && bad == 0);
        end

        // ---- SWEEP == FM ----
        gpio6_in = {8'd0, 19'd500000, 2'd1, 3'd4};   // mode=SWEEP
        gpio8_in = {4'd0, 9'd0, 19'd50000};          // 恢复 fm_dev(PM 测试曾清零)
        #1000;
        begin : tsw
            integer i, okhi, oklo, bad;
            okhi = 0; oklo = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst1 == 32'd12885000) okhi = okhi + 1;
                else if (uut.fword_inst1 == 32'd4295000) oklo = oklo + 1;
                else bad = bad + 1;
            end
            chk("SWEEP == FM fword_inst", okhi > 1000 && oklo > 1000 && bad == 0);
        end

        // ---- CH2 调制: AM 方波(通道 2 接线验证) ----
        gpio1_in = pack(20'd100000, 8'd100, 3'd0);   // CH2: 100kHz SINE 100%
        gpio7_in = {8'd100, 19'd500000, 2'd1, 3'd1}; // CH2 字C: AM square
        gpio9_in = 32'd0;
        #1000;
        reset_stats;
        #200_000;
        chk("CH2 AM gain2 swing", ch1_max >= 8'hFD && ch1_min == 8'h00);
        chk("CH2 AM gain0 flat", ch1_flat > 1000 && ch1_trans > 500);

        // ---- 数字调制 I/Q 旁路(DAC0=I, DAC1=Q) ----
        // BPSK: DAC0(I) ∈ {0x01, 0xFF}, DAC1(Q) 恒 0x80
        gpio0_in = pack(20'd1000, 8'd100, 3'd0);
        gpio1_in = pack(20'd1000, 8'd50, 3'd0);
        gpio6_in = 32'd0;
        gpio7_in = 32'd0;
        gpio8_in = 32'd0;
        gpio9_in = 32'd0;
        gpio10_in = 32'd0;
        gpio11_in = {12'd0, 3'd1, 8'd50, 8'd50, 1'b0};   // sel=1 -> BPSK
        #1000;
        reset_stats;
        #8_000_000;   // 8 ms -> ~80 symbols @ 100 us
        chk("digmod BPSK enable", uut.digmod_enable === 1'b1);
        chk("BPSK I offset-binary extreme", ch0_min == 8'h01 && ch0_max == 8'hFF);
        chk("BPSK I holds symbols", ch0_trans < 500);
        chk("BPSK Q constant 0x80", ch1_min == 8'h80 && ch1_max == 8'h80);
        chk("BPSK Q no transitions", ch1_trans == 0);

        // QAM16: I/Q values stay within the four legal levels and hold symbols.
        gpio11_in = {12'd0, 3'd4, 8'd50, 8'd50, 1'b0};   // sel=4 -> QAM16
        #1000;
        #100_000;   // wait for the previous symbol to expire at the mode boundary
        reset_stats;
        #8_000_000;   // 8 ms = 80 symbols at 100 us/symbol under 1 ns timescale
        chk("digmod QAM16 enable", uut.digmod_enable === 1'b1);
        chk("QAM16 I offset-binary range", ch0_min >= 8'h02 && ch0_max <= 8'hFE);
        chk("QAM16 Q offset-binary range", ch1_min >= 8'h02 && ch1_max <= 8'hFE);
        chk("QAM16 I observes all legal levels",
            qam_i_02 > 0 && qam_i_AA > 0 && qam_i_56 > 0 && qam_i_FE > 0);
        chk("QAM16 Q observes all legal levels",
            qam_q_02 > 0 && qam_q_AA > 0 && qam_q_56 > 0 && qam_q_FE > 0);
        chk("QAM16 I holds symbols", ch0_trans < 500);
        chk("QAM16 Q holds symbols", ch1_trans < 500);

        // selector 5 = QAM64: top-level I/Q bypass coverage.
        gpio11_in = {12'd0, 3'd5, 8'd50, 8'd50, 1'b0};   // sel=5 -> QAM64
        #101_000;                                        // 超过 100 us 符号周期，含同步裕量
        reset_stats;
        #8_000_000;                                      // 8 ms = 80 symbols under 1 ns timescale
        chk("digmod QAM64 enable", uut.digmod_enable === 1'b1);
        chk("digmod QAM64 mode", uut.digmod_mode === 3'd4);
        chk("QAM64 I offset-binary range", ch0_min >= 8'h02 && ch0_max <= 8'hFE);
        chk("QAM64 Q offset-binary range", ch1_min >= 8'h02 && ch1_max <= 8'hFE);
        chk("QAM64 I holds symbols", ch0_trans < 500);
        chk("QAM64 Q holds symbols", ch1_trans < 500);

        // FILTER=0 + QPSK must select the original RECT I/Q samples exactly.
        gpio11_in = (32'd2 << 17) | (32'd50 << 1) | (32'd50 << 9);
        #1000;
        chk("QPSK RECT mux I", uut.digmod_enable && !uut.digmod_filter &&
            uut.dac_sel0 === uut.dig_i);
        chk("QPSK RECT mux Q", uut.digmod_enable && !uut.digmod_filter &&
            uut.dac_sel1 === uut.dig_q);
        chk("QPSK RECT path enables IQ reader", uut.rect_active === 1'b1 &&
            uut.u_dig.enable === 1'b1);

        // RRC rolloff=0.2 (GPIO11[22:21]=01) must reach the top-level register bridge.
        gpio11_in = (32'd1 << 21) | (32'd2 << 17) | (32'd50 << 1) | (32'd50 << 9);
        gpio11_in[20] = 1'b1;  // FILTER=RRC remains GPIO11[20].
        #1000;
        chk("QPSK RRC rolloff 0.2 wiring", uut.digmod_rolloff === 2'b01 &&
            uut.digmod_filter === 1'b1);

        // FILTER=1 + QPSK enables the RRC chain and selects its scaled output.
        gpio11_in = 32'h0010_0000 | (32'd2 << 17) | (32'd50 << 1) |
                    (32'd50 << 9);
        #1000;
        chk("QPSK RRC active", uut.rrc_active === 1'b1);
        chk("QPSK RRC disables IQ reader", uut.rect_active === 1'b0 &&
            uut.u_dig.enable === 1'b0 && uut.iq_bram_b_en === 1'b0);
        chk("QPSK RRC mux scaled I", uut.dac_sel0 === uut.iq_i);
        chk("QPSK RRC mux scaled Q", uut.dac_sel1 === uut.iq_q);
        // 数字调制支路的选择目标现在是 iq_impair 的输出; 默认(未使能)它逐位透传。
        chk("iq_impair disabled by default", uut.iq_impair_en === 1'b0);
        force uut.rrc_i16 = 16'sh7fff;
        force uut.rrc_q16 = 16'sh8000;
        // ⚠ iq_impair 插在数字调制支路上, 比原设计多 4 拍流水:
        //   原路径 rrc_i8 -> dac_sel -> chan_impair(1 拍) -> dac_driver 只需 2 拍,
        //   现在必须把 iq_impair 的 4 拍一并等掉。
        repeat (8) begin @(posedge clk); #1; end
        chk("RRC positive saturation scales to 127", uut.rrc_i8 === 8'h7f);
        chk("RRC negative saturation scales to -127", uut.rrc_q8 === 8'h81);
        chk("RRC DAC offset-binary extremes", dac0_db === 8'hff && dac1_db === 8'h01);
        release uut.rrc_i16;
        release uut.rrc_q16;

        // FILTER=1 now routes every digital mode through the RRC chain.
        gpio11_in = 32'h0010_0000 | (32'd1 << 17) | (32'd50 << 1) |
                    (32'd50 << 9);
        #1000;
        chk("FILTER BPSK enables RRC", uut.rrc_active === 1'b1 &&
            uut.dac_sel0 === uut.rrc_i8 && uut.dac_sel1 === uut.rrc_q8 &&
            (^uut.dac_sel0 !== 1'bx) && (^uut.dac_sel1 !== 1'bx));
        gpio11_in = 32'h0010_0000 | (32'd4 << 17) | (32'd50 << 1) |
                    (32'd50 << 9);
        #1000;
        chk("FILTER QAM16 enables RRC", uut.rrc_active === 1'b1 &&
            uut.dac_sel0 === uut.rrc_i8 && uut.dac_sel1 === uut.rrc_q8 &&
            (^uut.dac_sel0 !== 1'bx) && (^uut.dac_sel1 !== 1'bx));
        gpio11_in = 32'h0010_0000 | (32'd5 << 17) | (32'd50 << 1) |
                    (32'd50 << 9);
        #1000;
        chk("FILTER QAM64 enables RRC", uut.rrc_active === 1'b1 &&
            uut.dac_sel0 === uut.rrc_i8 && uut.dac_sel1 === uut.rrc_q8 &&
            (^uut.dac_sel0 !== 1'bx) && (^uut.dac_sel1 !== 1'bx));

        // Reserved/off selectors must return to the standard AWG path.
        // At 1 ns timescale, #4_000_000 = 4 ms = four 1 kHz sine periods.
        gpio0_in = pack(20'd1000, 8'd100, 3'd0);   // CH1: standard 1 kHz SINE
        gpio1_in = pack(20'd1000, 8'd50,  3'd0);   // CH2: standard 1 kHz SINE
        gpio2_in = 32'd0;  gpio3_in = 32'd0;  gpio4_in = 32'd0;  gpio5_in = 32'd0;
        gpio6_in = 32'd0;  gpio7_in = 32'd0;  gpio8_in = 32'd0;  gpio9_in = 32'd0;
        gpio10_in = 32'd0;

        gpio11_in = {12'd0, 3'd6, 8'd50, 8'd50, 1'b0};   // reserved selector 6
        #1000;
        reset_stats;
        #4_000_000;
        chk("digmod selector 6 enable low", uut.digmod_enable === 1'b0);
        chk("selector 6 standard sine active", ch0_trans > 500);
        chk("selector 6 standard sine frequency", ch0_zcross >= 3 && ch0_zcross <= 5);
        chk("selector 6 standard sine bipolar", ch0_min < 8'h80 && ch0_max > 8'h80);
        chk("selector 6 CH2 standard sine active", ch1_trans > 500);
        chk("selector 6 CH2 standard sine frequency", ch1_zcross >= 3 && ch1_zcross <= 5);
        chk("selector 6 CH2 standard sine bipolar", ch1_min < 8'h80 && ch1_max > 8'h80);

        gpio11_in = {12'd0, 3'd4, 8'd50, 8'd50, 1'b0};   // direct switch through QAM16
        #101_000;                                        // 超过 100 us 符号周期，含同步裕量
        gpio11_in = {12'd0, 3'd7, 8'd50, 8'd50, 1'b0};   // reserved selector 7
        #1000;
        reset_stats;
        #4_000_000;
        chk("digmod selector 7 enable low", uut.digmod_enable === 1'b0);
        chk("selector 7 standard sine active", ch0_trans > 500);
        chk("selector 7 standard sine frequency", ch0_zcross >= 3 && ch0_zcross <= 5);
        chk("selector 7 standard sine bipolar", ch0_min < 8'h80 && ch0_max > 8'h80);
        chk("selector 7 CH2 standard sine active", ch1_trans > 500);
        chk("selector 7 CH2 standard sine frequency", ch1_zcross >= 3 && ch1_zcross <= 5);
        chk("selector 7 CH2 standard sine bipolar", ch1_min < 8'h80 && ch1_max > 8'h80);

        gpio11_in = {12'd0, 3'd4, 8'd50, 8'd50, 1'b0};   // direct switch through QAM16
        #101_000;                                        // 超过 100 us 符号周期，含同步裕量
        gpio11_in = 32'd0;                              // selector 0/OFF
        #1000;
        reset_stats;
        #4_000_000;
        chk("digmod off enable low", uut.digmod_enable === 1'b0);
        chk("digmod off standard sine active", ch0_trans > 500);
        chk("digmod off standard sine frequency", ch0_zcross >= 3 && ch0_zcross <= 5);
        chk("digmod off standard sine bipolar", ch0_min < 8'h80 && ch0_max > 8'h80);
        chk("digmod off CH2 standard sine active", ch1_trans > 500);
        chk("digmod off CH2 standard sine frequency", ch1_zcross >= 3 && ch1_zcross <= 5);
        chk("digmod off CH2 standard sine bipolar", ch1_min < 8'h80 && ch1_max > 8'h80);

        // ---- 相位联动: CH1/CH2 同频 10kHz, link 开启, 相位差 90° (2^32/4) ----
        gpio0_in = pack(20'd10000, 8'd100, 3'd0);   // CH1: 10kHz SINE 100%
        gpio1_in = pack(20'd10000, 8'd100, 3'd0);   // CH2: 同频(联动后强制跟随)
        gpio6_in = 32'd0;  gpio7_in = 32'd0;  gpio8_in = 32'd0;  gpio9_in = 32'd0;
        gpio2_in = 32'd0;  gpio3_in = 32'd0;  gpio4_in = 32'd0;  gpio5_in = 32'd0;
        gpio10_in = 32'h4000_0000;   // 相位差 90°
        gpio11_in = 32'd1;           // link 开启
        #1000;
        begin : tlink
            integer i, ok, bad;
            reg [31:0] ch1_ph;
            ok = 0; bad = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                // 边沿前采样 CH1 相位: 注入在边沿打拍, CH2 恒滞后 CH1 恰 1 拍
                // (直接比 phase_acc1+off 会差一个 fword, 故必须先采样)
                ch1_ph = uut.phase_acc1;
                @(posedge clk); #1;
                // CH2 相位恒 = CH1 相位 + 偏移(全周期 mod 2^32)
                if (uut.u_dds2.phase_acc === (ch1_ph + 32'h4000_0000)) ok = ok + 1;
                else bad = bad + 1;
            end
            chk("link ch2 phase = ch1 + 90deg", ok > 4900 && bad == 0);
        end
        // 相位差实时改 180°
        gpio10_in = 32'h8000_0000;
        #100;
        begin : tlink2
            integer i, ok;
            reg [31:0] ch1_ph;
            ok = 0;
            for (i = 0; i < 2000; i = i + 1) begin
                ch1_ph = uut.phase_acc1;
                @(posedge clk); #1;
                if (uut.u_dds2.phase_acc === (ch1_ph + 32'h8000_0000)) ok = ok + 1;
            end
            chk("link phase_off live update 180deg", ok > 1900);
        end
        // 关联动: CH2 恢复独立(频率不同, 相位自由累加)
        gpio11_in = 32'd0;
        gpio1_in = pack(20'd20000, 8'd100, 3'd0);   // CH2: 20kHz(不再跟随)
        #1000;
        begin : tunlink
            integer i, ok;
            ok = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if (uut.fword_inst2_used !== uut.fword_inst1_r) ok = ok + 1;
            end
            chk("unlink ch2 fword independent of ch1", ok > 4900);
        end

        // ---- 用户 BRAM 源: GPIO13 冻结控制字(source/loop/epoch/length_bytes/bank) ----
        // bank1, length_bytes=160(1280 bit), epoch=1, loop=1, source=1
        gpio11_in = (32'd2 << 17);
        gpio13_in = (32'd1 << 12) | (32'd160 << 3) | (32'd1 << 2) | (32'd1 << 1) | 32'd1;
        #20;
        chk("gpio14 READY waits for user prefetch", gpio14_out[3] === 1'b0);
        #200;
        chk("gpio14 VALID after commit", gpio14_out[0] === 1'b1);
        chk("gpio14 ACTIVE_BANK=1", gpio14_out[4] === 1'b1);
        chk("gpio14 READY", gpio14_out[3] === 1'b1);
        chk("reg_bridge user length bytes=160", uut.user_length_bytes == 9'd160);

        // 提交 epoch 重启用户源时，VALID 保持但 READY 必须反映重新预取窗口。
        gpio13_in = (32'd1 << 12) | (32'd160 << 3) | (32'd0 << 2) | (32'd1 << 1) | 32'd1;
        repeat (3) @(posedge clk);
        #1;
        chk("gpio14 READY drops during user restart", uut.u_dig.u_user.ready === 1'b0 &&
            gpio14_out[3] === 1'b0);
        #100;
        chk("gpio14 READY returns after user restart", uut.u_dig.u_user.ready === 1'b1 &&
            gpio14_out[3] === 1'b1);

        // SOURCE USER + QPSK RECT: BRAM B 读口由 iq_digital_mod 驱动(无滤波)
        gpio11_in = (32'd2 << 17);                 // sel=2 -> QPSK(SOURCE 由 GPIO13 bit0 提供)
        gpio12_in = 32'h4000_0000;                 // 每 4 拍一个 symbol
        #1000;
        chk("RECT bram read mux driven by iq", uut.rrc_active === 1'b0 &&
            bram_b_en === uut.iq_bram_b_en && bram_b_addr[9:2] === uut.iq_bram_b_addr);
        chk("RECT user source selects iq path", uut.digmod_source_user === 1'b1 &&
            uut.u_dig.source_user === 1'b1);
        gpio11_in = 32'h0010_0000 | (32'd2 << 17); // FILTER=RRC
        #1000;
        chk("RRC bram read mux driven by rrc", uut.rrc_active === 1'b1 &&
            bram_b_en === uut.rrc_bram_b_en && bram_b_addr[9:2] === uut.rrc_bram_b_addr);
        // 清回默认, 后续不依赖用户状态
        gpio11_in = 32'd0;
        gpio12_in = 32'd858993;
        gpio13_in = 32'd0;
        #100;

        // ---- 新增: 使能位 + BRAM 参数端到端穿透 ----
        // 该 TB 里没有 wait_cycles helper(chk 与 reset_stats 是这个文件仅有的 task),
        // 用显式 repeat 循环。50 拍 = 2 级 GPIO 同步 + 边沿检测 + 9 字排空(19 拍)留足余量。
        ch1_chan_word = {20'd0, 1'b1, 1'b1, 1'b1, 1'b1, 7'd0, 1'b1};
        //                [31:12] 保留(20) | bit11=iq_en | bit10=imp_en | bit9=tone_en
        //                | bit8=commit | bits7:1=noise | bit0=chan_en
        repeat (50) @(posedge clk);
        chk("awg_top ch1 tone_en",  uut.u_chan0.tone_en    == 1'b1);
        chk("awg_top ch1 imp_en",   uut.u_chan0.impulse_en == 1'b1);
        chk("awg_top ch2 en off",   uut.u_chan1.tone_en    == 1'b0
                                 && uut.u_chan1.impulse_en == 1'b0);
        chk("awg_top ch1 tone_fword",  uut.ch1_tone_fword  == 24'd33554);
        chk("awg_top ch1 tone_ampl",   uut.ch1_tone_ampl   == 7'd40);
        chk("awg_top ch1 imp_period",  uut.ch1_imp_period  == 16'd10);
        chk("awg_top ch1 imp_width",   uut.ch1_imp_width   == 8'd2);
        chk("awg_top ch1 imp_ampl",    uut.ch1_imp_ampl    == -8'sd50);
        // I/Q 复数级: 使能位(借 CH1 bit11)与字 8/9/10 一路穿到 iq_impair 实例
        chk("awg_top iq_impair_en",       uut.iq_impair_en == 1'b1);
        chk("awg_top iq_impair enabled",  uut.u_iq_impair.enable == 1'b1);
        chk("awg_top iq_coef_ab",         uut.iq_coef_ab == 32'h00004000);
        chk("awg_top iq_coef_cd",         uut.iq_coef_cd == 32'hC0000000);
        chk("awg_top iq_fword",           uut.iq_fword   == 32'h40000000);
        chk("awg_top iq D signed", $signed(uut.iq_coef_cd[31:16]) == -16'sd16384);
        ch1_chan_word = 32'd0;

        if (errs == 0) $display("PASS awg_top_tb");
        else $display("FAIL awg_top_tb: %0d errors", errs);
        $finish;
    end
endmodule
