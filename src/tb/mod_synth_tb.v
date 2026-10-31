`timescale 1ns / 1ps
module mod_synth_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [47:0] mfreq_fword = 0;
    reg [1:0]  mwave = 0;
    reg [2:0]  mode = 0;
    reg [7:0]  am_depth = 0;
    reg [25:0] fm_dev_fword = 0;
    reg [8:0]  pm_dev = 0;
    reg [31:0] fword_base = 0;
    wire signed [7:0]  m;
    wire [31:0] fword_inst;
    wire signed [8:0]  pm_off;
    wire signed [16:0] am_gain;

    mod_synth uut (
        .clk(clk), .rst_n(rst_n),
        .mfreq_fword(mfreq_fword), .mwave(mwave), .mode(mode),
        .am_depth(am_depth), .fm_dev_fword(fm_dev_fword), .pm_dev(pm_dev),
        .fword_base(fword_base),
        .m(m), .fword_inst(fword_inst), .pm_off(pm_off), .am_gain(am_gain)
    );
    always #10 clk = ~clk;   // 50 MHz

    // 独立参考 sine_rom(与 DUT 同一张表): 逐点比对 m, 避免直接读 uut.sine_v 的自参照
    reg  [7:0] ref_addr = 0;
    wire [7:0] ref_sine;
    sine_rom ref_rom (.addr(ref_addr), .dout(ref_sine));

    integer errs = 0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    // 直接置内部累加器(fword=0 时保持),下一拍读组合输出
    task poke;
        input [47:0] v;
        begin
            uut.acc_m = v;
            @(posedge clk); #1;
        end
    endtask

    initial begin
        #100 rst_n = 1;
        #100;

    // t1: 4 种调制波形形状(mfreq_fword=0 冻结累加器,逐一 poke t)
    begin : t1
        integer i, bad;
        reg signed [7:0] v;
        mfreq_fword = 48'd0; mode = 3'd0;
        // sine: 与独立参考 sine_rom 逐点比对(ref_addr 与 acc_m 同拍置位,
        // 两者输出均为组合逻辑, posedge 后同一 #1 时刻采样)
        bad = 0;
        mwave = 2'd0;
        for (i = 0; i < 256; i = i + 1) begin
            uut.acc_m = {i[7:0], 40'd0};
            ref_addr = i[7:0];
            @(posedge clk); #1;
            if (m !== $signed(ref_sine)) bad = bad + 1;
        end
        chk("mwave=SINE == ref sine_rom", bad == 0);
        chk("sine 参考遍历 256 点", i == 256);
        // square: ±127
        bad = 0;
        mwave = 2'd1;
        for (i = 0; i < 256; i = i + 1) begin
            poke({i[7:0], 40'd0});
            v = (i < 128) ? 8'sd127 : -8'sd127;
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=SQUARE +-127", bad == 0);
        // triangle: dds_core 斜率 2, -128..126 上升, 127..-127 下降
        bad = 0;
        mwave = 2'd2;
        for (i = 0; i < 256; i = i + 1) begin
            poke({i[7:0], 40'd0});
            // dds_core 算法: acc_m[46:39] = {t[6:0], 0}, 第二半取反
            v = (i < 128) ? (2*i - 128) : (383 - 2*i);
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=TRIANGLE linear", bad == 0);
        // saw: t-128
        bad = 0;
        mwave = 2'd3;
        for (i = 0; i < 256; i = i + 1) begin
            poke({i[7:0], 40'd0});
            v = i - 128;
            if (m !== v) bad = bad + 1;
        end
        chk("mwave=SAW t-128", bad == 0);
    end

    // t2: fword48 值正确 + 50kHz 周期
    begin : t2
        mfreq_fword = 48'd562950;
        poke(48'd0);
        chk("fword48 = 562950 (0.1Hz)", uut.acc_m == 48'd562950);
        // 50kHz: fword = 500000*562950; t 每 1000 拍回绕一次
        mfreq_fword = 48'd281475000000;
        uut.acc_m = 48'd0;
        begin : t2b
            integer c, wraps;
            reg [7:0] prev_t;
            wraps = 0;
            prev_t = uut.acc_m[47:40];
            for (c = 0; c < 2500; c = c + 1) begin
                @(posedge clk); #1;
                if (uut.acc_m[47:40] < prev_t) wraps = wraps + 1;  // t 回绕
                prev_t = uut.acc_m[47:40];
            end
            chk("50kHz -> t wraps ~2.5x/2500 clk", wraps >= 2 && wraps <= 3);
        end
    end

    // t3: AM 增益
    begin : t3
        mfreq_fword = 48'd0;
        mode = 3'd1;                       // AM
        mwave = 2'd1;                      // square
        am_depth = 8'd100;
        poke({47'd0, 1'b0});               // m=+127
        chk("AM depth100 m=+127 -> 25400", am_gain == 17'sd25400);
        poke({1'b1, 47'd0});               // m=-127
        chk("AM depth100 m=-127 -> 0", am_gain == 17'sd0);
        am_depth = 8'd50;
        poke({47'd0, 1'b0});
        chk("AM depth50 m=+127 -> 19050", am_gain == 17'sd19050);
        poke({1'b1, 47'd0});
        chk("AM depth50 m=-127 -> 6350", am_gain == 17'sd6350);
        // 负向钳位: saw t=0 -> m=-128, depth=100 -> 12700-12800 = -100 -> 钳位 0
        am_depth = 8'd100;
        mwave = 2'd3;                      // saw
        poke({8'd0, 40'd0});
        chk("AM 未钳位和 = -100", uut.am_sum == -18'sd100);
        chk("AM m=-128 depth100 -> 钳位 0", am_gain == 17'sd0);
        mode = 3'd0;                       // OFF
        poke({47'd0, 1'b0});
        chk("OFF -> am_gain 12700", am_gain == 17'sd12700);
    end

    // t4: FM fword_inst(含饱和)
    begin : t4
        mode = 3'd2;                       // FM
        mwave = 2'd1;
        fword_base = 32'd85899;
        fm_dev_fword = 26'd4295000;        // 50kHz 频偏
        poke({47'd0, 1'b0});               // m=+127
        chk("FM m=+127 -> base+dev", fword_inst == 32'd4380899);
        poke({1'b1, 47'd0});               // m=-127: 85899-4295000 < 0 -> 饱和 0
        chk("FM 负频饱和 -> 0", fword_inst == 32'd0);
        // 上饱和: base=2^31, m=+127 -> 未钳位和 2147483648+4295000=2151778648 -> 钳位 2^31-1
        fword_base = 32'h8000_0000;
        poke({47'd0, 1'b0});
        chk("FM 未钳位和 = 2151778648", uut.fm_sum == 35'd2151778648);
        chk("FM 上饱和 -> 2^31-1", fword_inst == 32'd2147483647);
        fword_base = 32'd85899;
        mode = 3'd0;
        poke({47'd0, 1'b0});
        chk("OFF -> fword_inst == base", fword_inst == 32'd85899);
    end

    // t5: PM pm_off
    begin : t5
        mode = 3'd3;                       // PM
        mwave = 2'd1;
        pm_dev = 9'd511;
        // 9 位有符号字面量: 9'sd511 实为 -1(pm_off 的 9 位输出同样回绕到 -1),
        // -9'sd511 实为 +1; 断言依赖 mod-512 回绕等价(±511 ≡ ∓1 mod 512)
        poke({47'd0, 1'b0});
        chk("PM m=+127 pm=511 -> 511", pm_off == 9'sd511);
        poke({1'b1, 47'd0});
        chk("PM m=-127 pm=511 -> -511", pm_off == -9'sd511);
        pm_dev = 9'd128;
        poke({47'd0, 1'b0});
        chk("PM m=+127 pm=128 -> 128", pm_off == 9'sd128);
        // 除法截断向零: saw t=228 -> m=+100, 100*511/127 = 402.36 -> 402(非 403)
        mwave = 2'd3;                      // saw
        pm_dev = 9'd511;
        poke({8'd228, 40'd0});
        chk("PM m=+100 pm=511 -> 402 截断", pm_off == 9'sd402);
        poke({8'd28, 40'd0});              // t=28 -> m=-100
        chk("PM m=-100 pm=511 -> -402 截断", pm_off == -9'sd402);
        mode = 3'd0;
        poke({47'd0, 1'b0});
        chk("OFF -> pm_off 0", pm_off == 9'sd0);
    end

    // t6: SWEEP == FM(同一数据通路)
    begin : t6
        mode = 3'd4;                       // SWEEP
        mwave = 2'd1;
        fm_dev_fword = 26'd4295000;
        fword_base = 32'd85899;
        poke({47'd0, 1'b0});
        chk("SWEEP m=+127 -> base+dev", fword_inst == 32'd4380899);
        poke({1'b1, 47'd0});
        chk("SWEEP m=-127 -> 饱和 0", fword_inst == 32'd0);
    end

    // t7: 模式切换在时钟边界共同生效，避免 fword/PM/AM 不同拍更新
    begin : t7
        mode = 3'd2;
        mwave = 2'd1;
        am_depth = 8'd100;
        pm_dev = 9'd128;
        poke({47'd0, 1'b0});
        chk("FM on -> inst != base", fword_inst != 32'd85899);
        mode = 3'd1;
        #1;
        chk("mode change holds prior outputs before clock",
            fword_inst != 32'd85899 && pm_off == 9'sd0 && am_gain == 17'sd12700);
        @(posedge clk); #1;
        chk("mode change updates fword/PM/AM together",
            fword_inst == 32'd85899 && pm_off == 9'sd0 && am_gain == 17'sd25400);
    end

    if (errs == 0) $display("PASS mod_synth_tb");
    else $display("FAIL mod_synth_tb: %0d errors", errs);
    $finish;
    end
endmodule
