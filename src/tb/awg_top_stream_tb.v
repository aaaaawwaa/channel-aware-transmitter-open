`timescale 1ns / 1ps

module awg_top_stream_tb;
    reg sys_clk = 1'b0;
    reg status_clk = 1'b0;
    reg [31:0] gpio0_in = 32'd0;
    reg [31:0] gpio1_in = 32'd0;
    reg [31:0] gpio2_in = 32'd0;
    reg [31:0] gpio3_in = 32'd0;
    reg [31:0] gpio4_in = 32'd0;
    reg [31:0] gpio5_in = 32'd0;
    reg [31:0] gpio6_in = 32'd0;
    reg [31:0] gpio7_in = 32'd0;
    reg [31:0] gpio8_in = 32'd0;
    reg [31:0] gpio9_in = 32'd0;
    reg [31:0] gpio10_in = 32'd0;
    reg [31:0] gpio11_in = 32'd0;
    reg [31:0] gpio12_in = 32'd85899345;
    reg [31:0] gpio13_in = 32'd0;
    reg [31:0] play_ctrl_in = 32'd0;
    reg [31:0] play_cfg_in = 32'd0;
    wire [31:0] play_status_out;
    wire [31:0] gpio14_out;
    wire bram_b_clk, bram_b_en;
    wire [31:0] bram_b_addr;
    reg [31:0] bram_b_rdata = 32'd0;
    reg [31:0] ch1_chan_word = 32'd0, ch2_chan_word = 32'd0;
    reg [31:0] tap_b_rdata = 32'd0;
    wire [5:0] tap_b_addr;
    wire tap_b_en;
    reg user_stream_enable = 1'b1;
    reg [31:0] user_stream_length = 32'd8;
    reg [31:0] user_stream_tdata = 32'h000000C0;
    reg [3:0] user_stream_tkeep = 4'b0001;
    reg user_stream_tvalid = 1'b0;
    wire user_stream_tready;
    reg user_stream_tlast = 1'b1;
    wire [7:0] led, dac0_db, dac1_db;
    wire daclk0, daclk1;

    integer errors = 0;
    integer symbols = 0;
    integer guard;

    awg_top dut (
        .sys_clk(sys_clk), .status_clk(status_clk),
        .gpio0_in(gpio0_in), .gpio1_in(gpio1_in),
        .gpio2_in(gpio2_in), .gpio3_in(gpio3_in),
        .gpio4_in(gpio4_in), .gpio5_in(gpio5_in),
        .gpio6_in(gpio6_in), .gpio7_in(gpio7_in),
        .gpio8_in(gpio8_in), .gpio9_in(gpio9_in),
        .gpio10_in(gpio10_in), .gpio11_in(gpio11_in),
        .gpio12_in(gpio12_in), .gpio13_in(gpio13_in),
        .play_ctrl_in(play_ctrl_in), .play_cfg_in(play_cfg_in),
        .play_status_out(play_status_out), .gpio14_out(gpio14_out),
        .bram_b_clk(bram_b_clk), .bram_b_en(bram_b_en),
        .bram_b_addr(bram_b_addr), .bram_b_rdata(bram_b_rdata),
        .ch1_chan_word(ch1_chan_word), .ch2_chan_word(ch2_chan_word),
        .tap_b_addr(tap_b_addr), .tap_b_en(tap_b_en),
        .tap_b_rdata(tap_b_rdata),
        .user_stream_enable(user_stream_enable),
        .user_stream_length(user_stream_length),
        .user_stream_tdata(user_stream_tdata),
        .user_stream_tkeep(user_stream_tkeep),
        .user_stream_tvalid(user_stream_tvalid),
        .user_stream_tready(user_stream_tready),
        .user_stream_tlast(user_stream_tlast),
        .led(led), .dac0_db(dac0_db), .dac1_db(dac1_db),
        .daclk0(daclk0), .daclk1(daclk1)
    );

    always #10 sys_clk = ~sys_clk;
    always #10 status_clk = ~status_clk;

    task check;
        input [255:0] name;
        input condition;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL %0s", name);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        gpio11_in = (32'd1 << 17); // BPSK, RECT
        gpio13_in = 32'd1;         // USER source
        wait (dut.rst_n == 1'b1);
        repeat (8) @(posedge sys_clk);
        check("top selects RECT stream source", dut.rect_active === 1'b1);
        check("top exposes stream ready", user_stream_tready === 1'b1);

        user_stream_tvalid = 1'b1;
        guard = 0;
        while (!user_stream_tready && guard < 100) begin
            @(posedge sys_clk);
            guard = guard + 1;
        end
        check("top stream word accepted", guard < 100);
        @(posedge sys_clk);
        #1 user_stream_tvalid = 1'b0;

        guard = 0;
        while (!dut.iq_user_done && guard < 1000) begin
            @(posedge sys_clk);
            #1;
            guard = guard + 1;
            if (dut.u_dig.symbol_valid) begin
                symbols = symbols + 1;
                if (symbols <= 2)
                    check("top stream negative BPSK", dut.dig_i === -8'sd127 && dut.dig_q === 8'sd0);
                else
                    check("top stream positive BPSK", dut.dig_i === 8'sd127 && dut.dig_q === 8'sd0);
            end
        end
        check("top stream done", dut.iq_user_done === 1'b1);
        check("top stream no underflow", dut.iq_user_underflow === 1'b0);
        check("top emitted eight symbols", symbols == 8);
        check("top stream ready clears", dut.iq_user_ready === 1'b0);
        check("top stream guard", guard < 1000);
        guard = 0;
        while (!gpio14_out[6] && guard < 20) begin
            @(posedge status_clk);
            #1;
            guard = guard + 1;
        end
        check("top stream overall done", gpio14_out[6] === 1'b1);

        user_stream_enable = 1'b0;
        guard = 0;
        while (gpio14_out[6] && guard < 20) begin
            @(posedge status_clk);
            #1;
            guard = guard + 1;
        end
        check("top stream overall done clears", gpio14_out[6] === 1'b0);

        if (errors == 0)
            $display("PASS awg_top_stream_tb symbols=%0d", symbols);
        else
            $display("FAIL awg_top_stream_tb: %0d errors", errors);
        $finish;
    end
endmodule
