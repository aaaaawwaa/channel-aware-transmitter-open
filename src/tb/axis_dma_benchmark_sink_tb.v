`timescale 1ns / 1ps
module axis_dma_benchmark_sink_tb;
    reg clk = 0;
    reg status_clk = 0;
    reg benchmark_enable = 0;
    reg [31:0] s_axis_tdata = 0;
    reg [3:0] s_axis_tkeep = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    reg s_axis_tlast = 0;
    wire [31:0] m_axis_tdata;
    wire [3:0] m_axis_tkeep;
    wire m_axis_tvalid;
    reg m_axis_tready = 0;
    wire m_axis_tlast;
    wire [31:0] bench_status;
    wire [31:0] bench_checksum;
    reg [12:0] fifo_rd_count = 13'd0;
    reg [31:0] stream_control = 32'd0;
    reg stream_underflow = 1'b0;
    wire [31:0] bench_first_valid_cycle;
    wire [12:0] bench_fifo_min_count;
    wire [12:0] bench_fifo_max_count;
    wire [31:0] bench_fifo_watermark;
    integer tb_cycle = 0;
    integer benchmark_disable_cycle = 0;
    integer benchmark_restart_cycle = 0;
    integer reload_gap_cycles = 0;
    reg saw_benchmark = 1'b0;
    integer errors = 0;

    axis_dma_benchmark_sink uut (
        .clk(clk), .status_clk(status_clk), .benchmark_enable(benchmark_enable),
        .s_axis_tdata(s_axis_tdata), .s_axis_tkeep(s_axis_tkeep),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tlast(s_axis_tlast), .m_axis_tdata(m_axis_tdata),
        .m_axis_tkeep(m_axis_tkeep), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready), .m_axis_tlast(m_axis_tlast),
        .bench_status(bench_status), .bench_checksum(bench_checksum),
        .fifo_rd_count(fifo_rd_count), .stream_control(stream_control),
        .stream_underflow(stream_underflow),
        .bench_first_valid_cycle(bench_first_valid_cycle),
        .bench_fifo_min_count(bench_fifo_min_count),
        .bench_fifo_max_count(bench_fifo_max_count),
        .bench_fifo_watermark(bench_fifo_watermark)
    );

    always #5 clk = ~clk;
    always #7 status_clk = ~status_clk;

    always @(posedge clk) begin
        tb_cycle = tb_cycle + 1;
        if (benchmark_enable)
            saw_benchmark = 1'b1;
        else if (saw_benchmark && benchmark_disable_cycle == 0)
            benchmark_disable_cycle = tb_cycle;
        if (benchmark_disable_cycle != 0 && benchmark_enable &&
            benchmark_restart_cycle == 0)
            benchmark_restart_cycle = tb_cycle;
        if (benchmark_restart_cycle != 0)
            reload_gap_cycles = benchmark_restart_cycle - benchmark_disable_cycle;
    end

    task check;
        input ok;
        input [8*64-1:0] name;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errors = errors + 1; end
        end
    endtask

    initial begin
        #12;
        s_axis_tdata = 32'hA5000000;
        s_axis_tkeep = 4'b1111;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b0;
        #1;
        check(s_axis_tready === 1'b0, "normal backpressure");
        check(m_axis_tvalid === 1'b1, "normal forward valid");
        m_axis_tready = 1'b1;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        benchmark_enable = 1'b1;
        m_axis_tready = 1'b0;
        repeat (2) @(posedge clk);
        #1;
        s_axis_tdata = 32'hA5000000;
        s_axis_tkeep = 4'b1111;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b0;
        fifo_rd_count = 13'd5;
        #1;
        check(s_axis_tready === 1'b1, "benchmark no backpressure");
        check(m_axis_tvalid === 1'b0, "benchmark consumes locally");
        @(posedge clk);
        #1;
        s_axis_tdata = 32'h00001234;
        s_axis_tkeep = 4'b0011;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b1;
        fifo_rd_count = 13'd2;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        repeat (3) @(posedge status_clk);
        #1;
        check(bench_status[31] === 1'b1, "benchmark active");
        check(bench_status[30] === 1'b1, "benchmark done");
        check(bench_status[29] === 1'b1, "benchmark tlast");
        check(bench_status[28] === 1'b0, "benchmark no error");
        check(bench_status[20:0] === 21'd6, "benchmark byte count");
        check(bench_checksum === 32'h26e2bc97, "benchmark crc32");
        check(bench_first_valid_cycle > 32'd0, "first_valid_cycle measured");
        check(bench_fifo_min_count === 13'd2, "fifo_min_count measured");
        check(bench_fifo_max_count === 13'd5, "fifo_max_count measured");
        check(bench_fifo_watermark === {6'd0, 13'd5, 13'd2}, "fifo_watermark packed");
        benchmark_enable = 1'b0;
        repeat (2) @(posedge clk);
        #1;
        check(bench_status === 32'd0, "benchmark reset");
        benchmark_enable = 1'b1;
        repeat (2) @(posedge clk);
        s_axis_tdata = 32'h0000005a;
        s_axis_tkeep = 4'b0001;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b1;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        repeat (3) @(posedge status_clk);
        #1;
        check(bench_status[31] === 1'b1, "benchmark restart active");
        check(bench_status[30] === 1'b1, "benchmark restart done");
        check(bench_status[29] === 1'b1, "benchmark restart tlast");
        check(bench_status[28] === 1'b0, "benchmark restart no error");
        check(bench_status[20:0] === 21'd1, "benchmark restart byte count");
        check(bench_checksum === 32'h59bc5767, "benchmark restart crc32");
        check(reload_gap_cycles > 0, "reload_gap_cycles measured");
        benchmark_enable = 1'b0;
        repeat (2) @(posedge clk);
        #1;
        check(bench_status === 32'd0, "benchmark final reset");

        // PP normal-mode observation: bank 0 accepts two beats with TLAST on
        // the partial final beat, then bank 1 accepts one byte beat.
        m_axis_tready = 1'b1;
        stream_control = 32'b101;
        repeat (4) @(posedge clk);
        s_axis_tdata = 32'h11223344;
        s_axis_tkeep = 4'b1111;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b0;
        fifo_rd_count = 13'd7;
        @(posedge clk);
        #1;
        s_axis_tdata = 32'h0000A1B2;
        s_axis_tkeep = 4'b0011;
        s_axis_tlast = 1'b1;
        fifo_rd_count = 13'd4;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        s_axis_tlast = 1'b0;
        stream_control = 32'b100;
        repeat (4) @(posedge clk);
        repeat (6) @(posedge status_clk);
        check(bench_status[31] === 1'b1, "PP bank0 valid");
        check(bench_status[30] === 1'b1, "PP bank0 tlast");
        check(bench_status[29] === 1'b0, "PP bank0 keep valid");
        check(bench_status[28] === 1'b0, "PP bank0 no underflow");
        check(bench_status[27:24] === 4'b0011, "PP bank0 last keep");
        check(bench_status[23] === 1'b0, "PP bank0 page");
        check(bench_status[20:0] === 21'd6, "PP bank0 byte count");
        check(bench_checksum === 32'hA377DA39, "PP bank0 crc32");
        check(bench_fifo_min_count === 13'd4, "PP bank0 fifo min");
        check(bench_fifo_max_count === 13'd7, "PP bank0 fifo max");

        stream_control = 32'b111;
        repeat (4) @(posedge clk);
        s_axis_tdata = 32'h0000005A;
        s_axis_tkeep = 4'b0001;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b1;
        fifo_rd_count = 13'd11;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        s_axis_tlast = 1'b0;
        fifo_rd_count = 13'd6;
        stream_control = 32'b100;
        repeat (4) @(posedge clk);
        repeat (6) @(posedge status_clk);
        stream_control = 32'b110;
        repeat (4) @(posedge status_clk);
        check(bench_status[31] === 1'b1, "PP bank1 valid");
        check(bench_status[30] === 1'b1, "PP bank1 tlast");
        check(bench_status[29] === 1'b0, "PP bank1 keep valid");
        check(bench_status[28] === 1'b0, "PP bank1 no underflow");
        check(bench_status[27:24] === 4'b0001, "PP bank1 last keep");
        check(bench_status[23] === 1'b1, "PP bank1 page");
        check(bench_status[20:0] === 21'd1, "PP bank1 byte count");
        check(bench_checksum === 32'h59BC5767, "PP bank1 crc32");
        check(bench_fifo_min_count === 13'd6, "PP bank1 fifo min");
        check(bench_fifo_max_count === 13'd11, "PP bank1 fifo max");
        check(bench_first_valid_cycle > 32'd0, "PP bank gap measured");

        // A fresh monitor session must expose a source underflow as an error.
        stream_control = 32'd0;
        repeat (4) @(posedge clk);
        stream_underflow = 1'b1;
        stream_control = 32'b101;
        repeat (4) @(posedge clk);
        s_axis_tdata = 32'h78563412;
        s_axis_tkeep = 4'b1111;
        s_axis_tvalid = 1'b1;
        s_axis_tlast = 1'b1;
        fifo_rd_count = 13'd3;
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        s_axis_tlast = 1'b0;
        stream_control = 32'b100;
        repeat (4) @(posedge clk);
        stream_underflow = 1'b0;
        repeat (6) @(posedge status_clk);
        check(bench_status[31] === 1'b1, "PP underflow valid");
        check(bench_status[28] === 1'b1, "PP underflow reported");

        stream_control = 32'd0;
        repeat (2) @(posedge clk);
        #1;
        check(bench_status === 32'd0, "PP final reset");
        if (errors == 0) $display("PASS axis_dma_benchmark_sink_tb");
        else $display("FAIL axis_dma_benchmark_sink_tb errors=%0d", errors);
        $finish;
    end
endmodule
