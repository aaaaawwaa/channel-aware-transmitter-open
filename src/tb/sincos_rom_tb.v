`timescale 1ns / 1ps
// sincos_rom TB —— 结构性检查: 同步读、四个象限定点、对称关系、sin^2+cos^2 恒定。
//
// **逐项表比对不在这里做**: 2048 个 16 位常数不适合内联进 Verilog, 抄错一个数
// 这种 TB 也发现不了(它只是把同样的错误抄两遍)。穷举比对由
// host/tests/test_channel.py::test_sincos_table_matches_rtl_file 解析本目录的
// .v 文件完成。这里只钉那些"表生成错了必然违反"的性质 —— 象限点错、符号错、
// 对称性错、幅度错都会被抓到。
module sincos_rom_tb;
    reg  clk = 0;
    reg  [9:0] addr = 10'd0;
    wire signed [15:0] cos_out, sin_out;

    sincos_rom uut (.clk(clk), .addr(addr), .cos_out(cos_out), .sin_out(sin_out));

    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;
    integer i, j;

    reg signed [15:0] cs [0:1023];
    reg signed [15:0] sn [0:1023];

    task chk;
        input [127:0] name;
        input signed [31:0] got;
        input signed [31:0] want;
        begin
            if (got !== want) begin
                $display("FAIL %0s: got %0d want %0d", name, got, want);
                errs = errs + 1;
            end
        end
    endtask

    // sin^2+cos^2 与 32767^2 的相对偏差; 用 64 位中间量避免溢出
    reg signed [63:0] mag2, ref2, dev;

    initial begin
        $dumpfile("sincos_rom_tb.vcd"); $dumpvars(0, sincos_rom_tb);

        // ---- 1. 同步读: 地址变化不得立刻出现在输出, 必须隔一拍 ----
        addr = 10'd0;
        @(posedge clk); #1;
        chk("cos origin", cos_out, 16'sd32767);
        chk("sin origin", sin_out, 16'sd0);

        @(negedge clk); addr = 10'd256; #1;
        chk("not combinational (holds prev)", cos_out, 16'sd32767);
        @(posedge clk); #1;
        chk("cos quarter", cos_out, 16'sd0);
        chk("sin quarter", sin_out, 16'sd32767);

        // ---- 2. 走遍全部 1024 个地址, 存下来 ----
        for (i = 0; i < 1024; i = i + 1) begin
            @(negedge clk); addr = i & 10'h3FF;
            @(posedge clk); #1;
            cs[i] = cos_out;
            sn[i] = sin_out;
        end

        // ---- 3. 象限定点 ----
        chk("cos half",   cs[512], -16'sd32767);
        chk("sin half",   sn[512],  16'sd0);
        chk("cos 3/4",    cs[768],  16'sd0);
        chk("sin 3/4",    sn[768], -16'sd32767);

        // ---- 4. 对称关系(表的四分之一对称性; 生成式错必然违反) ----
        for (i = 1; i < 1024; i = i + 1) begin
            j = (1024 - i) & 10'h3FF;
            if (cs[j] !== cs[i]) begin
                $display("FAIL cos even symmetry at %0d", i);
                errs = errs + 1;
            end
            if (sn[j] !== -sn[i]) begin
                $display("FAIL sin odd symmetry at %0d", i);
                errs = errs + 1;
            end
            j = (i + 512) & 10'h3FF;
            if (cs[j] !== -cs[i]) begin
                $display("FAIL cos half-turn sign at %0d", i);
                errs = errs + 1;
            end
            if (sn[j] !== -sn[i]) begin
                $display("FAIL sin half-turn sign at %0d", i);
                errs = errs + 1;
            end
        end

        // ---- 5. sin^2+cos^2 恒定(16 位量化地板内) ----
        ref2 = 64'sd32767 * 64'sd32767;
        for (i = 0; i < 1024; i = i + 1) begin
            mag2 = $signed(cs[i]) * $signed(cs[i]) + $signed(sn[i]) * $signed(sn[i]);
            dev  = mag2 - ref2;
            if (dev < 0) dev = -dev;
            if (dev * 1000 > ref2) begin   // 相对偏差 > 0.1%
                $display("FAIL |sin^2+cos^2-1| too big at %0d: %0d", i, dev);
                errs = errs + 1;
            end
        end

        if (errs == 0) $display("PASS sincos_rom (%0d checks)", 4 + 4 * 1023 + 1024);
        else           $display("FAIL sincos_rom (%0d)", errs);
        $finish;
    end
endmodule
