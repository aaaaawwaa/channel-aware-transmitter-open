`timescale 1ns / 1ps
module harm_synth_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] fword = 0;
    reg [7:0]  base = 0;
    reg [3:0]  h1_ord = 0, h2_ord = 0, h3_ord = 0, h4_ord = 0;
    reg [7:0]  h1_amp = 0, h2_amp = 0, h3_amp = 0, h4_amp = 0;
    reg signed [8:0] pm_off = 0;
    reg phase_ext_valid = 0;
    reg [31:0] phase_ext = 0;
    wire [7:0] sample;

    harm_synth uut (
        .clk(clk), .rst_n(rst_n), .fword(fword), .base(base),
        .h1_ord(h1_ord), .h2_ord(h2_ord), .h3_ord(h3_ord), .h4_ord(h4_ord),
        .h1_amp(h1_amp), .h2_amp(h2_amp), .h3_amp(h3_amp), .h4_amp(h4_amp),
        .pm_off(pm_off),
        .phase_ext_valid(phase_ext_valid), .phase_ext(phase_ext),
        .sample(sample)
    );
    always #10 clk = ~clk;   // 50 MHz

    // Reference sine table (same ROM as DUT, for pointwise comparison)
    wire [7:0] ref_sine;
    sine_rom ref_rom (.addr(phase_ref[31:24]), .dout(ref_sine));
    reg [31:0] phase_ref = 0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) phase_ref <= 32'd0;
        else        phase_ref <= phase_ref + fword;

    // Pipeline-aligned reference: DUT has 4 stages (acc->prod->sump->sample),
    // ref ROM has 1 stage (phase_ref->ref_sine). Delay ref by 3 cycles to align.
    reg [7:0] ref_d1, ref_d2, ref_aligned;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            ref_d1 <= 8'd0; ref_d2 <= 8'd0; ref_aligned <= 8'd0;
        end else begin
            ref_d1 <= ref_sine;
            ref_d2 <= ref_d1;
            ref_aligned <= ref_d2;
        end

    integer errs = 0;
    integer peaks = 0;
    integer zcross = 0;
    reg [7:0] prev = 8'd0;

    task chk;
        input [255:0] name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    task settle;
        input [15:0] n;
        begin repeat (n) @(posedge clk); end
    endtask

    initial begin
        #100 rst_n = 1;

        // 1) Pure fundamental: base=100 -> sample should match reference sine
        fword = 32'd85899;   // ~1kHz
        base = 8'd100;
        settle(10);
        begin : t1
            integer i;
            reg [7:0] got;
            reg [7:0] want;
            integer mism;
            mism = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                got = $signed(sample);
                want = $signed(ref_aligned);
                if (got !== want) mism = mism + 1;
            end
            chk("base=100 == pure sine (<=1% mismatch)", mism <= 5);
        end

        // 2) Base 0 + 2nd harmonic 100%: frequency doubled (zero-cross count) + full amplitude
        fword = 32'd42949672; // ~500 kHz fundamental -> 1 MHz 2nd harmonic
        base = 8'd0;
        h1_ord = 4'd2;
        h1_amp = 8'd100;
        settle(10);
        begin : t2
            integer i;
            peaks = 0;
            prev = 8'd0;
            zcross = 0;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) >= 0 && $signed(prev) < 0) zcross = zcross + 1;
                prev = sample;
                if ($signed(sample) > peaks) peaks = $signed(sample);
                if (-$signed(sample) > peaks) peaks = -$signed(sample);
            end
            chk("h1=2nd/100%: crossings ~ 2x base (>= 50)", zcross >= 50);
            chk("h1=2nd/100%: peak ~ full scale (>= 120)", peaks >= 120);
        end

        // 3) Normalization boundary: base=60 + h1=40 (sum=100) -> peak <= 127
        fword = 32'd85899;   // restore ~1kHz
        base = 8'd60;
        h1_amp = 8'd40;
        peaks = 0;
        settle(10);
        begin : t3
            integer i;
            for (i = 0; i < 5000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) > peaks) peaks = $signed(sample);
                if (-$signed(sample) > peaks) peaks = -$signed(sample);
            end
            chk("sum=100 peak <= 127", peaks <= 127);
        end

        // 3b) h2 通道独立验证:base=50 + h2(ord=3, amp=50);纯基波(只有 base=50)峰值应为 50 左右,
        //     叠加 3 次谐波后峰值明显超过纯基波(证明 h2 真实参与)
        base = 8'd50;
        h1_amp = 8'd0;
        h2_ord = 4'd3;
        h2_amp = 8'd50;
        h3_amp = 8'd0;
        h4_amp = 8'd0;
        settle(10);
        begin : t3b
            integer i;
            integer peak_only_base;
            integer peak_with_h2;
            integer peak3;
            peaks = 0;
            // 先测纯基波峰值(参考:正弦 50% ≈ 63)
            // 注意: ~1kHz 时 1 周期 = 50000 拍, 采样窗口必须覆盖至少 1 周期
            base = 8'd50; h2_amp = 8'd0;
            settle(10);
            peak_only_base = 0;
            for (i = 0; i < 60000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) > peak_only_base) peak_only_base = $signed(sample);
            end
            // 再测叠加 3 次谐波
            h2_amp = 8'd50;
            settle(10);
            peak_with_h2 = 0;
            peak3 = 0;
            for (i = 0; i < 60000; i = i + 1) begin
                @(posedge clk); #1;
                if ($signed(sample) > peak_with_h2) peak_with_h2 = $signed(sample);
                if ($signed(sample) > peak3) peak3 = $signed(sample);
            end
            chk("h2 contributes (peak grows with h2)", peak_with_h2 > peak_only_base + 10);
            chk("h2 sum=100 peak <= 127", peak3 <= 127);
        end
        h2_ord = 4'd0;
        h2_amp = 8'd0;

        // 4) All off (base=0, all amps=0) -> output constant 0
        base = 8'd0;
        h1_amp = 8'd0;
        settle(10);
        begin : t4
            integer i;
            integer nonzero;
            nonzero = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                if (sample !== 8'd0) nonzero = nonzero + 1;
            end
            chk("all off -> output 0", nonzero == 0);
        end

        // 5) ord=0 harmonic contributes 0: base=50 + h1 ord=0 amp=100 -> equivalent pure base 50%
        base = 8'd50;
        h1_ord = 4'd0;
        h1_amp = 8'd100;
        settle(10);
        begin : t5
            integer i;
            integer mism;
            reg signed [31:0] sample32, expect32;
            mism = 0;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                sample32 = $signed(sample);                // sign-extend to 32-bit
                expect32 = ($signed(ref_aligned) * 50) / 100; // 32-bit signed context
                if (sample32 !== expect32) mism = mism + 1;
            end
            chk("ord=0 ignored (base 50 == sine*0.5)", mism <= 5);
        end

        // 6) 谐波相位锁相 + 偶次 MSB 翻转: s1 == (acc0*2)[31:24]^0x80(ord=2 偶次)
        //    (板上实测偶次谐波反相 180°, 功能仿真同相; ^0x80 补偿。奇次不翻转见 t8)
        fword = 32'd85899;
        base = 8'd50;
        h1_ord = 4'd0;
        h1_amp = 8'd0;
        h2_ord = 4'd0; h2_amp = 8'd0;
        h3_ord = 4'd0; h3_amp = 8'd0;
        h4_ord = 4'd0; h4_amp = 8'd0;
        settle(10000);   // acc0 积累相位
        begin : t6
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            h1_ord = 4'd2;
            h1_amp = 8'd50;
            for (i = 0; i < 500; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 2;
                if (uut.s1 !== (exp1[31:24] ^ 8'h80)) mism = mism + 1;
            end
            chk("s1 even-ord == (2*acc0)[31:24]^0x80", mism == 0);
        end
        // 7) 相位锁相在稳态持续保持(不因时间漂移)
        begin : t7
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 2000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 2;
                if (uut.s1 !== (exp1[31:24] ^ 8'h80)) mism = mism + 1;
            end
            chk("s1 even-ord stays locked (no drift)", mism == 0);
        end
        // 8) 奇次谐波不翻转: ord=3 -> s1 == (acc0*3)[31:24](无 ^0x80)
        begin : t8
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            h1_ord = 4'd3;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 3;
                if (uut.s1 !== exp1[31:24]) mism = mism + 1;
            end
            chk("s1 odd-ord == (3*acc0)[31:24] (no flip)", mism == 0);
        end
        // 9) PM: 基波地址偏移 + 谐波偏移 ×ord,先加偏移再偶次翻转
        fword = 32'd85899;
        base = 8'd50;
        h1_ord = 4'd2; h1_amp = 8'd50;
        h2_ord = 4'd0; h2_amp = 8'd0;
        h3_ord = 4'd0; h3_amp = 8'd0;
        h4_ord = 4'd0; h4_amp = 8'd0;
        pm_off = 9'sd64;
        settle(10);
        begin : t9
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 2;                 // ph1
                // s1 = (ph1 + ord×64) ^ 0x80(偶次翻转, 2×64=128 mod 256)
                if (uut.s1 !== ((exp1[31:24] + 8'd128) ^ 8'h80)) mism = mism + 1;
                if (uut.s0 !== (uut.acc0[31:24] + 8'sd64))       mism = mism + 1;
            end
            chk("PM even s1=(ph1+128)^0x80", mism == 0);
        end
        // 10) 奇次谐波 PM: 无翻转
        h1_ord = 4'd3;
        begin : t10
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 3;
                // 3×64 = 192 mod 256
                if (uut.s1 !== (exp1[31:24] + 8'd192)) mism = mism + 1;
            end
            chk("PM odd s1=ph1+ord*64", mism == 0);
        end
        // 11) 负 pm_off 回绕: off1 = -64×3 = -192 ≡ 64 mod 256(奇次不翻转),
        //     s0 = acc0[31:24] + (-64 mod 256 = 192)
        h1_ord = 4'd3;
        pm_off = -9'sd64;
        settle(10);
        begin : t11
            integer i;
            integer mism;
            reg [35:0] exp1;
            mism = 0;
            for (i = 0; i < 1000; i = i + 1) begin
                @(posedge clk); #1;
                exp1 = uut.acc0 * 3;
                if (uut.s1 !== (exp1[31:24] + 8'd64))  mism = mism + 1;
                if (uut.s0 !== (uut.acc0[31:24] + 8'd192)) mism = mism + 1;
            end
            chk("PM neg s1=ph1+64 s0=acc0+192", mism == 0);
        end

        if (errs == 0) $display("PASS harm_synth_tb");
        else $display("FAIL harm_synth_tb: %0d errors", errs);
        $finish;
    end
endmodule
