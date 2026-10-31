`timescale 1ns / 1ps
// iq_impair TB: 与 host/siggen/channel.py::apply_iq_impair 逐值一致。
//
// 复位协议(整条相位通路的关键): 在**同一个 negedge** 释放 rst_n 并给出样本 0,
// 于是释放后的第一个 posedge 就是样本 0 的采样沿(此刻 phase 仍是复位值 0),
// 与模型的 n=0 -> addr 0 对齐。**多等一拍就会让整条旋转平移一个 fword** ——
// 这不是语法问题, 仿真也不会报错, 只会让所有期望值错位。
//
// 流水深度不写死: 先用单点脉冲实测, 断言等于 4, 再用实测值对齐(写死数字的话,
// RTL 少一级/多一级会被"期望值也跟着错位"掩盖 —— 与 chan_impair_tb 用 ov[n+3]
// 钉深度是同一个理由)。
//
// 索引约定: sincos_rom 是**同步**读, 样本与 ROM 输出同沿配对, 故第 n 个样本用
// n·fword 的地址(不是 tone_samples 那种 n−1)。见 channel.py 的说明。
module iq_impair_tb;
    localparam NMAX = 12;

    reg clk = 0, rst_n = 0, enable = 0;
    reg signed [7:0] i_in = 0, q_in = 0;
    reg [31:0] coef_ab = 0, coef_cd = 0, fword = 0;
    wire signed [7:0] i_out, q_out;

    iq_impair uut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .i_in(i_in), .q_in(q_in),
        .coef_ab(coef_ab), .coef_cd(coef_cd), .fword(fword),
        .i_out(i_out), .q_out(q_out)
    );

    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer j, lat = -1;

    reg signed [7:0] stim_i [0:NMAX-1];
    reg signed [7:0] stim_q [0:NMAX-1];
    reg signed [7:0] want_i [0:NMAX-1];
    reg signed [7:0] want_q [0:NMAX-1];
    reg signed [7:0] got_i  [0:NMAX+6];
    reg signed [7:0] got_q  [0:NMAX+6];

    task chk;
        input [255:0] name;
        input signed [31:0] got;
        input signed [31:0] want;
        begin
            if (got !== want) begin
                $display("FAIL %0s: got %0d want %0d", name, got, want);
                errs = errs + 1;
            end
        end
    endtask

    // 复位 + 释放同半拍给出样本 0, 然后喂满 NMAX+6 拍并逐拍采样
    task prime_reset;
        input [31:0] ca, cc, fw;
        input        en;
        begin
            @(negedge clk); rst_n = 0; enable = 0;
            i_in = 8'sd0; q_in = 8'sd0;
            coef_ab = ca; coef_cd = cc; fword = fw;
            repeat (3) @(negedge clk);
            enable = en;
            i_in = stim_i[0]; q_in = stim_q[0];
            rst_n = 1;
            @(posedge clk); #1;
            got_i[0] = i_out; got_q[0] = q_out;
            for (j = 1; j < NMAX + 7; j = j + 1) begin
                @(negedge clk);
                i_in = (j < NMAX) ? stim_i[j] : 8'sd0;
                q_in = (j < NMAX) ? stim_q[j] : 8'sd0;
                @(posedge clk); #1;
                got_i[j] = i_out; got_q[j] = q_out;
            end
        end
    endtask

    task expect_stim;
        input [255:0] tag;
        begin
            for (j = 0; j < NMAX; j = j + 1) begin
                chk({tag, " i"}, got_i[j + lat], stim_i[j]);
                chk({tag, " q"}, got_q[j + lat], stim_q[j]);
            end
        end
    endtask

    task expect_want;
        input [255:0] tag;
        begin
            for (j = 0; j < NMAX; j = j + 1) begin
                chk({tag, " i"}, got_i[j + lat], want_i[j]);
                chk({tag, " q"}, got_q[j + lat], want_q[j]);
            end
        end
    endtask

    initial begin
        $dumpfile("iq_impair_tb.vcd"); $dumpvars(0, iq_impair_tb);

        stim_i[ 0]=  8'sd0;  stim_i[ 1]= 8'sd10;  stim_i[ 2]=-8'sd10;
        stim_i[ 3]= 8'sd100; stim_i[ 4]=-8'sd127; stim_i[ 5]= 8'sd127;
        stim_i[ 6]=  8'sd1;  stim_i[ 7]=-8'sd100; stim_i[ 8]= 8'sd50;
        stim_i[ 9]=-8'sd50;  stim_i[10]=  8'sd0;  stim_i[11]= 8'sd127;
        stim_q[ 0]=  8'sd0;  stim_q[ 1]= -8'sd7;  stim_q[ 2]=  8'sd7;
        stim_q[ 3]=-8'sd100; stim_q[ 4]= 8'sd127; stim_q[ 5]=-8'sd127;
        stim_q[ 6]=  8'sd0;  stim_q[ 7]= 8'sd100; stim_q[ 8]=-8'sd50;
        stim_q[ 9]= 8'sd50;  stim_q[10]=  8'sd0;  stim_q[11]=-8'sd127;

        // ---- 0. 钉流水深度: 单位阵 + 单点脉冲(样本 0 = 100, 之后全 0) ----
        for (j = 0; j < NMAX; j = j + 1) begin
            stim_i[j] = (j == 0) ? 8'sd100 : 8'sd0;
            stim_q[j] = (j == 0) ? -8'sd100 : 8'sd0;
        end
        prime_reset(32'h00004000, 32'h40000000, 32'h0, 1'b1);
        for (j = 0; j < NMAX + 7; j = j + 1)
            if (lat < 0 && (got_i[j] !== 8'sd0 || got_q[j] !== 8'sd0))
                lat = j;
        chk("pipeline depth", lat, 4);
        chk("depth tap i", got_i[lat], 8'sd100);
        chk("depth tap q", got_q[lat], -8'sd100);
        for (j = lat + 1; j < NMAX + 7; j = j + 1) begin
            chk("tail i zero", got_i[j], 8'sd0);
            chk("tail q zero", got_q[j], 8'sd0);
        end

        // ---- 1..6 的输入序列 ----
        stim_i[ 0]=  8'sd0;  stim_i[ 1]= 8'sd10;  stim_i[ 2]=-8'sd10;
        stim_i[ 3]= 8'sd100; stim_i[ 4]=-8'sd127; stim_i[ 5]= 8'sd127;
        stim_i[ 6]=  8'sd1;  stim_i[ 7]=-8'sd100; stim_i[ 8]= 8'sd50;
        stim_i[ 9]=-8'sd50;  stim_i[10]=  8'sd0;  stim_i[11]= 8'sd127;
        stim_q[ 0]=  8'sd0;  stim_q[ 1]= -8'sd7;  stim_q[ 2]=  8'sd7;
        stim_q[ 3]=-8'sd100; stim_q[ 4]= 8'sd127; stim_q[ 5]=-8'sd127;
        stim_q[ 6]=  8'sd0;  stim_q[ 7]= 8'sd100; stim_q[ 8]=-8'sd50;
        stim_q[ 9]= 8'sd50;  stim_q[10]=  8'sd0;  stim_q[11]=-8'sd127;

        // 1. 单位阵 + 无频偏 -> 逐位不变
        prime_reset(32'h00004000, 32'h40000000, 32'h0, 1'b1);
        expect_stim("identity");

        // 2. 纯 90 度相位偏移矩阵 (host iq_impair_matrix(9000,16384,0))
        want_i[ 0]=  8'sd0;  want_i[ 1]=  8'sd7;  want_i[ 2]= -8'sd7;
        want_i[ 3]= 8'sd100; want_i[ 4]=-8'sd127; want_i[ 5]= 8'sd127;
        want_i[ 6]=  8'sd0;  want_i[ 7]=-8'sd100; want_i[ 8]= 8'sd50;
        want_i[ 9]=-8'sd50;  want_i[10]=  8'sd0;  want_i[11]= 8'sd127;
        want_q[ 0]=  8'sd0;  want_q[ 1]= 8'sd10;  want_q[ 2]=-8'sd10;
        want_q[ 3]= 8'sd100; want_q[ 4]=-8'sd127; want_q[ 5]= 8'sd127;
        want_q[ 6]=  8'sd1;  want_q[ 7]=-8'sd100; want_q[ 8]= 8'sd50;
        want_q[ 9]=-8'sd50;  want_q[10]=  8'sd0;  want_q[11]= 8'sd127;
        prime_reset(32'hC0000000, 32'h00004000, 32'h0, 1'b1);
        expect_want("phase90");

        // 3. 增益失配 0.5 (alpha = 16384/2)
        want_i[ 0]=  8'sd0;  want_i[ 1]=  8'sd5;  want_i[ 2]= -8'sd5;
        want_i[ 3]= 8'sd50;  want_i[ 4]=-8'sd64;  want_i[ 5]= 8'sd64;
        want_i[ 6]=  8'sd1;  want_i[ 7]=-8'sd50;  want_i[ 8]= 8'sd25;
        want_i[ 9]=-8'sd25;  want_i[10]=  8'sd0;  want_i[11]= 8'sd64;
        want_q[ 0]=  8'sd0;  want_q[ 1]= -8'sd7;  want_q[ 2]=  8'sd7;
        want_q[ 3]=-8'sd100; want_q[ 4]= 8'sd127; want_q[ 5]=-8'sd127;
        want_q[ 6]=  8'sd0;  want_q[ 7]= 8'sd100; want_q[ 8]=-8'sd50;
        want_q[ 9]= 8'sd50;  want_q[10]=  8'sd0;  want_q[11]=-8'sd127;
        prime_reset(32'h00002000, 32'h40000000, 32'h0, 1'b1);
        expect_want("gain0.5");

        // 4. 频偏: fword = 2^32/4 -> 每拍 +90 度
        want_i[ 0]=  8'sd0;  want_i[ 1]=  8'sd7;  want_i[ 2]= 8'sd10;
        want_i[ 3]=-8'sd100; want_i[ 4]=-8'sd127; want_i[ 5]= 8'sd127;
        want_i[ 6]= -8'sd1;  want_i[ 7]= 8'sd100; want_i[ 8]= 8'sd50;
        want_i[ 9]=-8'sd50;  want_i[10]=  8'sd0;  want_i[11]=-8'sd127;
        want_q[ 0]=  8'sd0;  want_q[ 1]= 8'sd10;  want_q[ 2]= -8'sd7;
        want_q[ 3]=-8'sd100; want_q[ 4]= 8'sd127; want_q[ 5]= 8'sd127;
        want_q[ 6]=  8'sd0;  want_q[ 7]= 8'sd100; want_q[ 8]=-8'sd50;
        want_q[ 9]=-8'sd50;  want_q[10]=  8'sd0;  want_q[11]=-8'sd127;
        prime_reset(32'h00004000, 32'h40000000, 32'h40000000, 1'b1);
        expect_want("freqoff90");

        // 5. 饱和: 四系数全 32767, 输入 (127,127)
        for (j = 0; j < NMAX; j = j + 1) begin
            stim_i[j] = 8'sd127; stim_q[j] = 8'sd127;
        end
        prime_reset(32'h7FFF7FFF, 32'h7FFF7FFF, 32'h0, 1'b1);
        for (j = 0; j < NMAX; j = j + 1) begin
            chk("sat i", got_i[j + lat], 8'sd127);
            chk("sat q", got_q[j + lat], 8'sd127);
        end

        // 6. enable=0: 逐位透传(系数与频偏都是非平凡的, 必须被旁路掉)
        stim_i[ 0]=  8'sd0;  stim_i[ 1]= 8'sd10;  stim_i[ 2]=-8'sd10;
        stim_i[ 3]= 8'sd100; stim_i[ 4]=-8'sd127; stim_i[ 5]= 8'sd127;
        stim_i[ 6]=  8'sd1;  stim_i[ 7]=-8'sd100; stim_i[ 8]= 8'sd50;
        stim_i[ 9]=-8'sd50;  stim_i[10]=  8'sd0;  stim_i[11]= 8'sd127;
        stim_q[ 0]=  8'sd0;  stim_q[ 1]= -8'sd7;  stim_q[ 2]=  8'sd7;
        stim_q[ 3]=-8'sd100; stim_q[ 4]= 8'sd127; stim_q[ 5]=-8'sd127;
        stim_q[ 6]=  8'sd0;  stim_q[ 7]= 8'sd100; stim_q[ 8]=-8'sd50;
        stim_q[ 9]= 8'sd50;  stim_q[10]=  8'sd0;  stim_q[11]=-8'sd127;
        prime_reset(32'hC0000000, 32'h00004000, 32'h40000000, 1'b0);
        expect_stim("bypass");

        // 7. 频偏从非零切回 0 -> 相位累加器必须**清零**, 不能停在旧相位上。
        //    fword = 2^22 时地址每拍 +1(0.3516 度/拍); 累到第 128 拍恰好 45 度,
        //    再切 fword=0。若只"停止累加", 输出会停在 45 度旋转 (100,0)->(71,71);
        //    清零后单位阵必须逐位透传 (100,0)。宿主模型 fword=0 即 arch=0。
        @(negedge clk); rst_n = 0; enable = 0;
        coef_ab = 32'h00004000; coef_cd = 32'h40000000;
        fword = 32'h00400000; i_in = 8'sd100; q_in = 8'sd0;
        repeat (3) @(negedge clk);
        rst_n = 1; enable = 1;
        repeat (128) @(posedge clk);          // 相位累到 addr=128 -> 45 度
        @(negedge clk); fword = 32'h0;        // 切回 0, 相位应在本拍末清零
        repeat (8) @(posedge clk); #1;        // 等 5 级流水排空
        chk("freqoff->0 phase reset i", i_out, 8'sd100);
        chk("freqoff->0 phase reset q", q_out, 8'sd0);
        // 再喂几拍, 确认不是"只对了第一拍"
        for (j = 0; j < 4; j = j + 1) begin
            @(negedge clk); i_in = 8'sd100; q_in = 8'sd0;
            @(posedge clk); #1;
            chk("freqoff->0 held i", i_out, 8'sd100);
            chk("freqoff->0 held q", q_out, 8'sd0);
        end

        if (errs == 0) $display("PASS iq_impair (lat=%0d)", lat);
        else           $display("FAIL iq_impair (%0d)", errs);
        $finish;
    end
endmodule
