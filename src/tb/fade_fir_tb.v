`timescale 1ns / 1ps
module fade_fir_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg [7:0] sample_in = 0;
    reg [3*9+8:0] tap_gain = 0;    // 4 x 9-bit Q8 signed (unity = 255)
    reg [3*9+8:0] tap_delay = 0;   // 4 x 9-bit
    wire [7:0] sample_out;

    fade_fir uut (.clk(clk), .rst_n(rst_n), .sample_in(sample_in),
                  .tap_gain(tap_gain), .tap_delay(tap_delay),
                  .sample_out(sample_out));
    always #10 clk = ~clk;

    integer errs = 0;
    task chk; input [255:0] name; input ok;
        begin if (ok) $display("PASS %0s", name);
              else begin $display("FAIL %0s", name); errs = errs + 1; end end
    endtask

    // 设抽头
    task set_taps; input [8:0] g0,g1,g2,g3; input [8:0] d0,d1,d2,d3;
        begin tap_gain = {g3,g2,g1,g0}; tap_delay = {d3,d2,d1,d0}; end
    endtask

    // 复位后把写指针推过最大延迟, 使延迟抽头读到已写过的零地址(而非未初始化 mem[x])
    task prime; input integer n;
        integer j;
        begin sample_in = 8'd0; for (j = 0; j < n; j = j + 1) @(posedge clk); end
    endtask

    // 复位: 拉低跨过 2 个时钟沿, 再在负沿释放, 避免与时钟沿竞争
    task do_reset;
        begin rst_n = 0; repeat (2) @(posedge clk); @(negedge clk); rst_n = 1; end
    endtask

    integer i;
    initial begin
        #100 rst_n = 1;
        // 单位抽头: 逐位透传
        set_taps(9'sd255, 0, 0, 0, 0, 0, 0, 0);
        prime(4);
        @(negedge clk);
        for (i = 0; i < 8; i = i + 1) begin
            sample_in = (i * 37 - 100);
            repeat (3) @(negedge clk);   // 负沿驱动 -> 等 3 拍流水到输出
            chk("unity passthrough", $signed(sample_out) == $signed(sample_in));
            sample_in = 0; repeat (2) @(negedge clk);
        end
        // 两点回声 g=[255,128] d=[0,2] 于冲激序列
        do_reset; prime(4);
        set_taps(9'sd255, 9'sd128, 0, 0, 9'd0, 9'd2, 0, 0);
        @(negedge clk); sample_in = 8'd40;      // 冲激只保持一拍
        @(negedge clk); sample_in = 8'd0;
        repeat (2) @(negedge clk); chk("echo direct", $signed(sample_out) == 40);
        @(negedge clk); chk("echo n+1", $signed(sample_out) == 0);
        @(negedge clk); chk("echo n+2", $signed(sample_out) == 20);
        // 饱和: 两个抽头同址 g=[255,255] d=[0,0] on +100 -> 200 -> 127
        do_reset; prime(4);
        set_taps(9'sd255, 9'sd255, 0, 0, 0, 0, 0, 0);
        @(negedge clk); sample_in = 8'd100;
        @(negedge clk); sample_in = 8'd0;
        repeat (2) @(negedge clk); chk("saturate +", $signed(sample_out) == 127);
        if (errs == 0) $display("PASS fade_fir");
        else $display("FAIL fade_fir (%0d errors)", errs);
        $finish;
    end
endmodule
