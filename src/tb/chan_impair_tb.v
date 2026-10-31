`timescale 1ns / 1ps
// chan_impair TB: 旁路逐位透传、单位抽头无噪透传、噪声注入扰动,
// 以及针对 host/siggen/channel.py 生成向量的 fade+noise 对齐 golden 检查。
module chan_impair_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg enable = 0;
    reg [7:0] sample_in = 0;
    reg [35:0] tap_gain = 36'h0000_00FF;   // R2: tap0 gain=255, 其余 0 (255 为精确单位增益)
    reg [35:0] tap_delay = 36'd0;
    reg [6:0] noise_level = 0;
    wire [7:0] sample_out;

    // 新增干扰控制（默认关闭 -> 既有用例逐位不变）
    reg              tone_en = 0;
    reg [23:0]       tone_fword = 0;
    reg [6:0]        tone_ampl = 0;
    reg              impulse_en = 0;
    reg [15:0]       impulse_period = 0;
    reg [7:0]        impulse_width = 0;
    reg signed [7:0] impulse_ampl = 0;

    // 新增子用例的计数器与 golden（模块级, 见上面 ⚠️）
    integer nz, ze, n100, n127, nm27, nbad, gold2_errs;
    reg signed [7:0] tone_gold [0:15];
    initial begin
        tone_gold[0]=0;   tone_gold[1]=48;  tone_gold[2]=30;  tone_gold[3]=71;
        tone_gold[4]=-99; tone_gold[5]=-1;  tone_gold[6]=48;  tone_gold[7]=50;
        tone_gold[8]=-29; tone_gold[9]=-49; tone_gold[10]=-1; tone_gold[11]=-22;
        tone_gold[12]=30; tone_gold[13]=-29; tone_gold[14]=-49; tone_gold[15]=-1;
    end

    chan_impair uut (.clk(clk), .rst_n(rst_n), .enable(enable),
                     .sample_in(sample_in), .tap_gain(tap_gain),
                     .tap_delay(tap_delay), .noise_level(noise_level),
                     .tone_en(tone_en), .tone_fword(tone_fword), .tone_ampl(tone_ampl),
                     .impulse_en(impulse_en), .impulse_period(impulse_period),
                     .impulse_width(impulse_width), .impulse_ampl(impulse_ampl),
                     .sample_out(sample_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    // ------------------------------------------------------------------
    // Host-golden 向量: host/siggen/channel.py
    //   add_noise(multipath_fir(x, [255,128,0,0], [0,2,0,0]), 50, 0x00C0FFEE)
    // 逐时钟驱动 x, 端到端延迟 3 拍 -> ov[n+3] 对应输入 x[n]。
    // ------------------------------------------------------------------
    reg signed [7:0] xv [0:18];            // 16 个激励 + 3 个尾部 0, 把 x[15] 的输出推出
    integer          golden [0:15];
    reg signed [7:0] ov [0:18];
    integer j, n, gold_errs;

    integer m;
    // fade_fir 的分布式 RAM 无复位; 置零以匹配主机模型的"零历史"(否则尾部地址为 X)
    initial begin
        for (m = 0; m < 512; m = m + 1) begin
            uut.u_fade.g_tap[0].mem[m] = 8'd0;
            uut.u_fade.g_tap[1].mem[m] = 8'd0;
            uut.u_fade.g_tap[2].mem[m] = 8'd0;
            uut.u_fade.g_tap[3].mem[m] = 8'd0;
        end
    end

    initial begin
        xv[0]  =    0; xv[1]  =   0; xv[2]  =   0; xv[3]  = 100;
        xv[4]  =  -50; xv[5]  =   0; xv[6]  =   0; xv[7]  =  20;
        xv[8]  =    0; xv[9]  =   0; xv[10] =   0; xv[11] = -70;
        xv[12] =    0; xv[13] =   0; xv[14] =   0; xv[15] =   0;
        xv[16] =    0; xv[17] =   0; xv[18] =   0;
        golden[0]  =  29; golden[1]  =  37; golden[2]  = -14; golden[3]  =  33;
        golden[4]  = -24; golden[5]  = -44; golden[6]  = -17; golden[7]  = -75;
        golden[8]  =   2; golden[9]  =  90; golden[10] = -71; golden[11] = -79;
        golden[12] = -39; golden[13] =  48; golden[14] =  40; golden[15] = 127;
    end

    integer i;
    initial begin
        #100 rst_n = 1;
        // enable=0: 逐位透传(1 拍)
        enable = 0;
        for (i = 0; i < 6; i = i + 1) begin
            sample_in = (i * 29 - 80);
            @(posedge clk); #1;
            repeat (2) @(posedge clk);
            chk("bypass passthrough", $signed(sample_out) == $signed(sample_in));
        end
        // enable=1, noise=0, unity tap: 透传(3 拍)
        enable = 1; noise_level = 0;
        sample_in = 8'd77;
        repeat (6) @(posedge clk); #1;
        chk("unity tap noise0 passthrough", $signed(sample_out) == 77);
        // noise>0 但 unity tap: 输出非恒定(噪声注入)
        noise_level = 7'd80;
        repeat (16) @(posedge clk);
        chk("noise perturbs", $signed(sample_out) != 77);

        // ------------------------------------------------------------------
        // Directed host-golden 对齐检查: fade(tap0=255, tap1=128@d2) + noise(50)
        // ------------------------------------------------------------------
        rst_n = 0; enable = 0; noise_level = 7'd0;
        tap_gain = 36'd0; tap_delay = 36'd0; sample_in = 8'd0;
        repeat (3) @(posedge clk);
        enable      = 1'b1;
        noise_level = 7'd50;
        tap_gain    = {9'd0, 9'd0, 9'd128, 9'd255};   // tap0=255, tap1=128, tap2/3=0
        tap_delay   = {9'd0, 9'd0, 9'd2,   9'd0};     // tap1 延迟 2 样本
        sample_in   = xv[0];
        @(negedge clk);
        rst_n = 1'b1;                                 // 下一个 posedge 即输入下标 0 的采样拍
        @(posedge clk); #1; ov[0] = sample_out;
        for (j = 1; j < 19; j = j + 1) begin
            sample_in = xv[j];
            @(posedge clk); #1;
            ov[j] = sample_out;
        end
        gold_errs = 0;
        for (n = 0; n < 16; n = n + 1)
            if ($signed(ov[n + 3]) != golden[n]) begin
                $display("FAIL golden[%0d] got=%0d exp=%0d",
                         n, $signed(ov[n + 3]), golden[n]);
                gold_errs = gold_errs + 1;
            end
        chk("host golden vector 16/16 (fade+noise alignment)", gold_errs == 0);

        // ==================================================================
        // 新增 1: 单音干扰加入加法节点
        //   抽头纯透传(tap0 gain=255, 全部 delay=0), 噪声 0, 冲激关 -> fade_out = x[n-3]。
        //   S[n+3] = sat(x[n] + tone_samples(...,40,50e6)[n+1])  (见上面的推导)
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; impulse_en = 0; noise_level = 7'd0;
        sample_in = 8'd0;
        repeat (3) @(posedge clk);
        enable      = 1'b1;
        tap_gain    = {9'd0, 9'd0, 9'd0, 9'd255};    // 仅 tap0 = 255
        tap_delay   = 36'd0;                          // 全部 delay=0 -> 纯透传
        tone_en     = 1'b1;
        tone_fword  = 24'd3355443;                    // round(10e6*2^24/50e6): 地址每拍约 +51
        tone_ampl   = 7'd40;
        sample_in   = xv[0];
        @(negedge clk);
        rst_n = 1'b1;                                 // 下一个 posedge 即 x 下标 0 的采样拍
        @(posedge clk); #1;
        for (j = 1; j < 19; j = j + 1) begin
            sample_in = xv[j];
            @(posedge clk); #1;
            ov[j] = sample_out;
        end
        gold2_errs = 0;
        for (n = 0; n < 16; n = n + 1)
            if ($signed(ov[n + 3]) != tone_gold[n]) begin
                $display("FAIL tone golden[%0d] got=%0d exp=%0d",
                         n, $signed(ov[n + 3]), tone_gold[n]);
                gold2_errs = gold2_errs + 1;
            end
        chk("tone summed at adder node 16/16", gold2_errs == 0);

        // ==================================================================
        // 新增 2: 冲激项确实接进了加法节点
        //   cnt 自由运行, 相位不可预测 -> 只断言相位无关的模式:
        //   period=4/width=2 -> 稳态每 4 拍里 2 拍非零; 16 拍 = 4 个整周期 -> 恰好 8 拍非零。
        //   抽头全 0 -> fade_out = 0; 噪声 0; 单音关 -> 输出只可能是 0 或 127。
        //   ⚠ 必须先丢弃前 8 拍: 流水填充 + 首个不完整周期会让 16 拍窗口读到 7/9 而非
        //     8/8(实测如此)。丢弃后窗口完全落在稳态, 任意相位都是恰好 8/8。
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; noise_level = 7'd0;
        tap_gain = 36'd0; tap_delay = 36'd0; sample_in = 8'd0;
        impulse_en = 1'b1; impulse_period = 16'd4; impulse_width = 8'd2;
        impulse_ampl = 8'sd100;                       // level = round(100*127/100) = 127
        repeat (3) @(posedge clk);
        enable = 1'b1;
        @(negedge clk); rst_n = 1'b1;
        repeat (8) @(posedge clk); #1;                // 丢弃前 8 拍, 等流水与首个完整周期
        nz = 0; ze = 0;
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk); #1;
            if      ($signed(sample_out) == 127) nz = nz + 1;
            else if ($signed(sample_out) == 0)   ze = ze + 1;
            else begin
                $display("FAIL impulse case unexpected level: %0d", $signed(sample_out));
                errs = errs + 1;
            end
        end
        chk("impulse 2-of-4 duty in sum", nz == 8 && ze == 8);
        impulse_en = 1'b0;

        // ==================================================================
        // 新增 3: 加法节点饱和到 ±127
        //   用 12.5 MHz 单音(fword=2^22): 地址每拍 +64 -> 走遍 0/90/180/270 度。
        //   常数 x=100 + 单位抽头 -> fade_out = 100; tone_ampl=100 -> tone 峰值 ±127。
        //   稳态输出在 sat(100 + {0,127,0,-127}) = {100,127,100,-27} 间循环, 周期 4
        //   -> 每 4 拍 1 个 127、2 个 100、1 个 -27; 16 拍 = 4 周期 -> 4/8/4。
        //   ⚠ 同样是 4/8/4 而不是 4/4/4: 100 在一个周期里出现两次。
        // ==================================================================
        rst_n = 0; enable = 0;
        tone_en = 0; impulse_en = 0; noise_level = 7'd0;
        repeat (3) @(posedge clk);
        enable      = 1'b1;
        tap_gain    = {9'd0, 9'd0, 9'd0, 9'd255};
        tap_delay   = 36'd0;
        tone_en     = 1'b1;
        tone_fword  = 24'd4194304;                    // 地址每拍 +64
        tone_ampl   = 7'd100;
        sample_in   = 8'd100;
        @(negedge clk); rst_n = 1'b1;
        repeat (8) @(posedge clk); #1;                // 丢弃前 8 拍, 等流水稳定
        n100 = 0; n127 = 0; nm27 = 0; nbad = 0;
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk); #1;
            if      ($signed(sample_out) ==  100) n100 = n100 + 1;
            else if ($signed(sample_out) ==  127) n127 = n127 + 1;
            else if ($signed(sample_out) ==  -27) nm27 = nm27 + 1;
            else                                  nbad = nbad + 1;
        end
        chk("sum saturates +/-127 (4/8/4 pattern)",
            n100 == 8 && n127 == 4 && nm27 == 4 && nbad == 0);
        tone_en = 1'b0;

        if (errs == 0) $display("PASS chan_impair");
        else $display("FAIL chan_impair (%0d errors)", errs);
        $finish;
    end
endmodule
