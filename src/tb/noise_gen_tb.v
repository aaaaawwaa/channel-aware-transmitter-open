`timescale 1ns / 1ps
// noise_gen TB: 与 host/siggen/channel.py::noise_samples 逐值一致。
// 端到端 3 拍流水(见 noise_gen.v), 故每组 golden 前多等 repeat (2) 个时钟沿。
// goldens 由 host 模型生成(seed 0x00C0FFEE):
//   level 50  -> noise_samples(4,50,0x00C0FFEE)  = [29, 37, -14, -67]
//   level 100 -> noise_samples(4,100,0x00C0FFEE) = [57, 74, -29, -135]
//   level 10  -> noise_samples(4,10,0x00C0FFEE)  = [5, 7, -3, -13]
module noise_gen_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg enable = 1;
    reg [6:0] level = 0;
    wire signed [10:0] noise_out;

    noise_gen #(.SEED(32'h00C0FFEE)) uut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .level(level),
        .noise_out(noise_out));
    always #10 clk = ~clk;

    // host goldens (level 50)
    localparam signed [10:0] G0 =  29;
    localparam signed [10:0] G1 =  37;
    localparam signed [10:0] G2 = -14;
    localparam signed [10:0] G3 = -67;
    localparam signed [10:0] G4 =  26;   // 5th sample: state keeps advancing while disabled
    // host goldens (level 100)
    localparam signed [10:0] H0 =  57;
    localparam signed [10:0] H1 =  74;
    localparam signed [10:0] H2 = -29;
    localparam signed [10:0] H3 = -135;
    // host goldens (level 10)
    localparam signed [10:0] K0 =   5;
    localparam signed [10:0] K1 =   7;
    localparam signed [10:0] K2 =  -3;
    localparam signed [10:0] K3 = -13;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s (got %0d)", name, noise_out); errs = errs + 1; end end
    endtask

    // 复位释放必须与 posedge 错开: 否则 rst_n=1 与 posedge clk 同拍,
    // always 块可能在本拍就看到已释放的 rst_n 而多推进一次 state(差一个样点)。
    task do_reset;
        begin
            @(negedge clk);
            rst_n = 0;
            repeat (2) @(posedge clk);
            @(negedge clk);
            rst_n = 1;
        end
    endtask

    initial begin
        #100 rst_n = 1;
        // level=0 -> 全 0
        level = 0;
        repeat (8) @(posedge clk);
        chk("level0 zero", noise_out == 0);

        // level=50 goldens: 复位后前 4 个样点
        do_reset; level = 7'd50; enable = 1;
        repeat (2) @(posedge clk);
        @(posedge clk); #1; chk("golden0", noise_out == G0);
        @(posedge clk); #1; chk("golden1", noise_out == G1);
        @(posedge clk); #1; chk("golden2", noise_out == G2);
        @(posedge clk); #1; chk("golden3", noise_out == G3);

        // level=100 goldens
        do_reset; level = 7'd100;
        repeat (2) @(posedge clk);
        @(posedge clk); #1; chk("lvl100_0", noise_out == H0);
        @(posedge clk); #1; chk("lvl100_1", noise_out == H1);
        @(posedge clk); #1; chk("lvl100_2", noise_out == H2);
        @(posedge clk); #1; chk("lvl100_3", noise_out == H3);

        // level=10 goldens
        do_reset; level = 7'd10;
        repeat (2) @(posedge clk);
        @(posedge clk); #1; chk("lvl10_0", noise_out == K0);
        @(posedge clk); #1; chk("lvl10_1", noise_out == K1);
        @(posedge clk); #1; chk("lvl10_2", noise_out == K2);
        @(posedge clk); #1; chk("lvl10_3", noise_out == K3);

        // level>100 clamps to 100 (same sequence as level 100)
        do_reset; level = 7'd127;
        repeat (2) @(posedge clk);
        @(posedge clk); #1; chk("lvl127 clamp", noise_out == H0);

        // enable=0 -> 0
        do_reset; level = 7'd50; enable = 0;
        repeat (4) @(posedge clk); #1; chk("disabled zero", noise_out == 0);
        enable = 1;

        // re-enable resumes the same deterministic stream from the held state
        enable = 1;
        repeat (2) @(posedge clk);
        @(posedge clk); #1; chk("reenable resumes", noise_out == G4);

        if (errs == 0) $display("PASS noise_gen");
        else $display("FAIL noise_gen (%0d errors)", errs);
        $finish;
    end
endmodule
