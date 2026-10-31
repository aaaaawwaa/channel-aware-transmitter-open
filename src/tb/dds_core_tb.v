`timescale 1ns / 1ps
module dds_core_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] fword = 0;
    reg [2:0]  wave_sel = 0;
    reg [7:0]  pulse_duty = 50;
    reg signed [8:0] pm_off = 0;
    reg phase_ext_valid = 0;
    reg [31:0] phase_ext = 0;
    wire [7:0] sample;

    dds_core uut (
        .clk(clk), .rst_n(rst_n),
        .fword(fword), .wave_sel(wave_sel), .pulse_duty(pulse_duty), .pm_off(pm_off),
        .phase_ext_valid(phase_ext_valid), .phase_ext(phase_ext),
        .phase_acc_out(), .sample(sample)
    );
    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer i;

    task chk;
        input [1023:0] name;
        input condition;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL %0s", name);
                errs = errs + 1;
            end
        end
    endtask

    function [7:0] sine_expected;
        input [7:0] a;
        real x;
        begin
            x = 127.0 * $sin(2.0 * 3.14159265358979 * a / 256.0);
            if (x >= 0.0) sine_expected = $rtoi(x + 0.5);
            else          sine_expected = $rtoi(x - 0.5);
        end
    endfunction

    // wait one clk edge, then check sample (registered at posedge)
    task step;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        #100 rst_n = 1;
        #100;

        // --- sine: fword = 2^24 -> one LUT step per clk ---
        wave_sel = 3'b00;
        fword = 32'h01000000;
        for (i = 0; i < 512; i = i + 1) begin
            step;
            if ($signed(sample) !== $signed(sine_expected(i[7:0]))) begin
                $display("FAIL sine step %0d: sample=%h exp=%h", i, sample, sine_expected(i[7:0]));
                errs = errs + 1;
            end
        end

        // --- square ---
        wave_sel = 3'b01;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b01;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if (i < 128) begin
                if (sample !== 8'h7F) begin
                    $display("FAIL square step %0d: %h", i, sample); errs = errs + 1;
                end
            end else begin
                if (sample !== 8'h81) begin
                    $display("FAIL square step %0d: %h", i, sample); errs = errs + 1;
                end
            end
        end

        // --- triangle ---
        wave_sel = 3'b10;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b10;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] tri_p;
                reg [7:0] exp_v;
                // mirror DUT: tri_p = phase[31] ? ~phase[30:23] : phase[30:23]
                // with phase = i<<24: phase[30:23] = {i[6:0], 1'b0}
                tri_p = i[7] ? (~{i[6:0], 1'b0}) : {i[6:0], 1'b0};
                exp_v = tri_p - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL tri step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- sawtooth ---
        wave_sel = 3'b11;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b11;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] exp_v;
                exp_v = i[7:0] - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL saw step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- noise: deterministic LFSR output changes with the phase address ---
        wave_sel = 3'b101;
        fword = 32'h01000000;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b101;
        uut.noise_lfsr = 8'h01;
        uut.noise_phase = 8'd0;
        begin : tnoise
            integer noise_changes, noise_pos, noise_neg;
            reg [7:0] prev_noise;
            noise_changes = 0;
            noise_pos = 0;
            noise_neg = 0;
            prev_noise = 8'h00;
            for (i = 0; i < 1024; i = i + 1) begin
                step;
                if (sample !== prev_noise) noise_changes = noise_changes + 1;
                if ($signed(sample) > 0) noise_pos = noise_pos + 1;
                if ($signed(sample) < 0) noise_neg = noise_neg + 1;
                prev_noise = sample;
            end
            chk("noise LFSR changes", noise_changes > 900);
            chk("noise LFSR bipolar", noise_pos > 300 && noise_neg > 300);
        end

        // --- pulse: DUTY=25% board-high for the first quarter of the phase ---
        pulse_duty = 8'd25;
        pm_off = 9'sd0;
        uut.pm_off_r = 9'sd0;
        wave_sel = 3'b100;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b100;
        uut.pulse_duty_r = 8'd25;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if (i < 64) begin
                if (sample !== 8'h81) begin
                    $display("FAIL pulse 25%% high step %0d: %h", i, sample); errs = errs + 1;
                end
            end else if (sample !== 8'h7F) begin
                $display("FAIL pulse 25%% low step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- pulse: DUTY=50% has the square-wave boundary (board-high first, 127 high, 128 low) ---
        pulse_duty = 8'd50;
        pm_off = 9'sd0;
        uut.pm_off_r = 9'sd0;
        uut.pulse_duty_r = 8'd50;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ((i < 128 && sample !== 8'h81) ||
                (i >= 128 && sample !== 8'h7F)) begin
                $display("FAIL pulse 50%% boundary step %0d: %h", i, sample);
                errs = errs + 1;
            end
        end

        // --- pulse: DUTY=0% is continuously board-low ---
        pulse_duty = 8'd0;
        uut.pulse_duty_r = 8'd0;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if (sample !== 8'h7F) begin
                $display("FAIL pulse 0%% step %0d: %h", i, sample);
                errs = errs + 1;
            end
        end

        // --- pulse: PM offset rotates the board-high duty boundary ---
        pulse_duty = 8'd25;
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        uut.pulse_duty_r = 8'd25;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if (i >= 192) begin
                if (sample !== 8'h81) begin
                    $display("FAIL pulse PM high step %0d: %h", i, sample);
                    errs = errs + 1;
                end
            end else if (sample !== 8'h7F) begin
                $display("FAIL pulse PM low step %0d: %h", i, sample);
                errs = errs + 1;
            end
        end

        // --- pulse: DUTY=100% is continuously board-high ---
        pulse_duty = 8'd100;
        uut.pulse_duty_r = 8'd100;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if (sample !== 8'h81) begin
                $display("FAIL pulse 100%% step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- PM 端口寄存通路: pm_off 仅经端口驱动, 验证 pm_off_r <= pm_off ---
        // 前一段直接 poke 了 PM 偏移, 这里先恢复寄存器以隔离端口采样延迟。
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd0;
        wave_sel = 3'b00;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b00;
        fword = 32'h01000000;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            // 第 0 拍: pm_off_r 尚未寄存 64, 样本无偏移 = sine(0);
            // 第 1 拍起: pm_off_r=64, 样本 = phase_acc[31:24](=i)+64 = sine(i+64)
            if (i == 0) begin
                if ($signed(sample) !== $signed(sine_expected(8'd0))) begin
                    $display("FAIL pm port-reg step %0d: %h exp %h", i, sample, sine_expected(8'd0));
                    errs = errs + 1;
                end
            end else if ($signed(sample) !== $signed(sine_expected((i + 64) % 256))) begin
                $display("FAIL pm port-reg step %0d: %h exp %h", i, sample, sine_expected((i + 64) % 256));
                errs = errs + 1;
            end
        end

        // --- PM: 负偏移 -64 -> 低字节截断 192, mod 256 回绕 ---
        pm_off = -9'sd64;
        uut.pm_off_r = -9'sd64;        // 9'b1_1100_0000, [7:0] = 192
        wave_sel = 3'b00;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b00;
        fword = 32'h01000000;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ($signed(sample) !== $signed(sine_expected((i + 192) % 256))) begin
                $display("FAIL pm neg-off step %0d: %h exp %h", i, sample, sine_expected((i + 192) % 256));
                errs = errs + 1;
            end
        end

        // --- PM: pm_off 偏移查表地址(正弦) ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 3'b00;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b00;
        fword = 32'h01000000;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ($signed(sample) !== $signed(sine_expected((i + 64) % 256))) begin
                $display("FAIL pm sine step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- PM: pm_off=128 -> 正弦反相 ---
        pm_off = 9'sd128;
        uut.pm_off_r = 9'sd128;
        uut.phase_acc = 32'd0;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            if ($signed(sample) !== -$signed(sine_expected(i[7:0]))) begin
                $display("FAIL pm invert step %0d: %h", i, sample); errs = errs + 1;
            end
        end

        // --- PM: 方波符号随 p_off[7] ---
        pm_off = 9'sd128;
        uut.pm_off_r = 9'sd128;
        wave_sel = 3'b01;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b01;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] exp_v;
                exp_v = (((i + 128) % 256) < 128) ? 8'h7F : 8'h81;
                if (sample !== exp_v) begin
                    $display("FAIL pm square step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- PM: 三角波对偏移相位做相同旋转(phase_acc=0 -> 位23=0) ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 3'b10;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b10;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] poff8, tri_pm, tri_p, exp_v;
                poff8 = (i + 64) % 256;
                tri_pm = {poff8[6:0], 1'b0};
                tri_p  = poff8[7] ? ~tri_pm : tri_pm;
                exp_v  = tri_p - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL pm tri step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        // --- PM: 锯齿地址 = p_off - 0x80 ---
        pm_off = 9'sd64;
        uut.pm_off_r = 9'sd64;
        wave_sel = 3'b11;
        uut.phase_acc = 32'd0;
        uut.wave_sel_r = 3'b11;
        for (i = 0; i < 256; i = i + 1) begin
            step;
            begin
                reg [7:0] exp_v;
                exp_v = ((i + 64) % 256) - 8'h80;
                if (sample !== exp_v) begin
                    $display("FAIL pm saw step %0d: %h exp %h", i, sample, exp_v);
                    errs = errs + 1;
                end
            end
        end

        if (errs == 0) $display("PASS dds_core");
        else $display("FAIL dds_core: %0d errors", errs);
        $finish;
    end
endmodule
