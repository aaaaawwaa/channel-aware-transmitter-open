`timescale 1ns / 1ps
module axis_dma_benchmark_sink (
    input  wire        clk,
    input  wire        status_clk,
    input  wire        benchmark_enable,
    input  wire [31:0] s_axis_tdata,
    input  wire [3:0]  s_axis_tkeep,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    output wire [31:0] m_axis_tdata,
    output wire [3:0]  m_axis_tkeep,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast,
    output wire [31:0] bench_status,
    output wire [31:0] bench_checksum,
    input  wire [12:0] fifo_rd_count,
    input  wire [31:0] stream_control,
    input  wire        stream_underflow,
    output wire [31:0] bench_first_valid_cycle,
    output wire [12:0] bench_fifo_min_count,
    output wire [12:0] bench_fifo_max_count,
    output wire [31:0] bench_fifo_watermark
);
    reg benchmark_enable_meta_r = 1'b0;
    reg benchmark_enable_clk_r = 1'b0;
    reg [31:0] stream_control_meta_r = 32'd0;
    reg [31:0] stream_control_clk_r = 32'd0;
    reg pp_mode_prev_r = 1'b0;
    reg pp_enable_prev_r = 1'b0;
    wire bench = benchmark_enable_clk_r;
    wire pp_mode = (stream_control_clk_r[2] === 1'b1);
    wire pp_enable = (stream_control_clk_r[0] === 1'b1);
    wire pp_bank = (stream_control_clk_r[1] === 1'b1);
    wire accept = bench && (s_axis_tvalid === 1'b1);
    wire keep_valid = (s_axis_tkeep == 4'b0001) ||
                      (s_axis_tkeep == 4'b0011) ||
                      (s_axis_tkeep == 4'b0111) ||
                      (s_axis_tkeep == 4'b1111);
    wire [2:0] keep_bytes = {2'd0, s_axis_tkeep[0]} +
                            {2'd0, s_axis_tkeep[1]} +
                            {2'd0, s_axis_tkeep[2]} +
                            {2'd0, s_axis_tkeep[3]};

    always @(posedge clk) begin
        benchmark_enable_meta_r <= (benchmark_enable === 1'b1);
        benchmark_enable_clk_r <= benchmark_enable_meta_r;
    end

    function [31:0] crc32_byte;
        input [31:0] crc_in;
        input [7:0] data;
        integer bit_index;
        reg [31:0] value;
        begin
            value = crc_in ^ {24'd0, data};
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                value = value[0] ? (value >> 1) ^ 32'hedb88320 : value >> 1;
            crc32_byte = value;
        end
    endfunction

    function [31:0] crc32_beat;
        input [31:0] crc_in;
        input [31:0] data;
        input [3:0] keep;
        integer lane;
        reg [31:0] value;
        begin
            value = crc_in;
            for (lane = 0; lane < 4; lane = lane + 1)
                if (keep[lane])
                    value = crc32_byte(value, data[lane * 8 +: 8]);
            crc32_beat = value;
        end
    endfunction

    reg [20:0] byte_count_r = 21'd0;
    reg [31:0] crc_r = 32'hffff_ffff;
    reg [31:0] checksum_r = 32'd0;
    reg done_r = 1'b0;
    reg last_r = 1'b0;
    reg error_r = 1'b0;
    reg [31:0] bench_status_sync_r = 32'd0;
    reg [31:0] bench_checksum_sync_r = 32'd0;
    // Result payload is held after TLAST; the request toggle gives it two destination-clock cycles to settle.
    reg [31:0] bench_result_status_r = 32'd0;
    reg [31:0] bench_result_checksum_r = 32'd0;
    reg [31:0] bench_result_first_valid_cycle_r = 32'd0;
    reg [12:0] bench_result_fifo_min_count_r = 13'd0;
    reg [12:0] bench_result_fifo_max_count_r = 13'd0;
    reg bench_result_req_r = 1'b0;
    reg bench_result_req_meta_r = 1'b0;
    reg bench_result_req_sync_r = 1'b0;
    reg bench_result_req_seen_r = 1'b0;
    reg [31:0] bench_first_valid_cycle_sync_r = 32'd0;
    reg [12:0] bench_fifo_min_count_sync_r = 13'd0;
    reg [12:0] bench_fifo_max_count_sync_r = 13'd0;
    reg pp_mode_status_meta_r = 1'b0;
    reg pp_mode_status_r = 1'b0;
    reg pp_bank_status_meta_r = 1'b0;
    reg pp_bank_status_r = 1'b0;
    reg pp_result_req_meta_r = 1'b0;
    reg pp_result_req_sync_r = 1'b0;
    reg pp_result_req_seen_r = 1'b0;
    reg pp_result_valid0_sync_r = 1'b0;
    reg pp_result_valid1_sync_r = 1'b0;
    reg [31:0] pp_result_status0_sync_r = 32'd0;
    reg [31:0] pp_result_status1_sync_r = 32'd0;
    reg [31:0] pp_result_checksum0_sync_r = 32'd0;
    reg [31:0] pp_result_checksum1_sync_r = 32'd0;
    reg [12:0] pp_result_fifo_min0_sync_r = 13'd0;
    reg [12:0] pp_result_fifo_min1_sync_r = 13'd0;
    reg [12:0] pp_result_fifo_max0_sync_r = 13'd0;
    reg [12:0] pp_result_fifo_max1_sync_r = 13'd0;
    reg [31:0] pp_gap_cycles_sync_r = 32'd0;
    reg [31:0] first_valid_cycle_r = 32'd0;
    reg first_valid_seen_r = 1'b0;
    reg [12:0] fifo_min_count_r = 13'h1fff;
    reg [12:0] fifo_max_count_r = 13'd0;
    reg fifo_count_seen_r = 1'b0;
    reg [31:0] cycle_count_r = 32'd0;

    // PP monitor records AXI4-Stream TLAST/TKEEP and source underflow.
    reg pp_capture_active_r = 1'b0;
    reg pp_bank_active_r = 1'b0;
    reg pp_first_seen_r = 1'b0;
    reg pp_last_seen_r = 1'b0;
    reg pp_keep_error_r = 1'b0;
    reg pp_underflow_seen_r = 1'b0;
    reg [3:0] pp_last_keep_r = 4'd0;
    reg [20:0] pp_byte_count_r = 21'd0;
    reg [31:0] pp_crc_r = 32'hffff_ffff;
    reg [12:0] pp_fifo_min_count_r = 13'd0;
    reg [12:0] pp_fifo_max_count_r = 13'd0;
    reg pp_fifo_count_seen_r = 1'b0;
    reg [31:0] pp_cycle_count_r = 32'd0;
    reg pp_last_tlast_valid_r = 1'b0;
    reg [31:0] pp_last_tlast_cycle_r = 32'd0;
    reg [31:0] pp_gap_cycles_r = 32'd0;
    reg pp_result_valid0_r = 1'b0;
    reg pp_result_valid1_r = 1'b0;
    reg [31:0] pp_result_status0_r = 32'd0;
    reg [31:0] pp_result_status1_r = 32'd0;
    reg [31:0] pp_result_checksum0_r = 32'd0;
    reg [31:0] pp_result_checksum1_r = 32'd0;
    reg [12:0] pp_result_fifo_min0_r = 13'd0;
    reg [12:0] pp_result_fifo_min1_r = 13'd0;
    reg [12:0] pp_result_fifo_max0_r = 13'd0;
    reg [12:0] pp_result_fifo_max1_r = 13'd0;
    reg pp_result_req_r = 1'b0;
    wire pp_accept = !bench && pp_mode && pp_capture_active_r &&
                     (s_axis_tvalid === 1'b1) && (m_axis_tready === 1'b1);
    wire pp_keep_valid = (s_axis_tkeep == 4'b0001) ||
                         (s_axis_tkeep == 4'b0011) ||
                         (s_axis_tkeep == 4'b0111) ||
                         (s_axis_tkeep == 4'b1111);
    wire [2:0] pp_keep_bytes = {2'd0, s_axis_tkeep[0]} +
                               {2'd0, s_axis_tkeep[1]} +
                               {2'd0, s_axis_tkeep[2]} +
                               {2'd0, s_axis_tkeep[3]};
    wire [31:0] pp_crc_after_beat = crc32_beat(pp_crc_r, s_axis_tdata, s_axis_tkeep);
    wire [21:0] pp_byte_count_after_beat =
        {1'b0, pp_byte_count_r} + {{19{1'b0}}, pp_keep_bytes};
    wire pp_beat_error = !pp_keep_valid || pp_keep_bytes == 3'd0 ||
                         pp_byte_count_after_beat > 22'h1f_ffff;
    wire pp_mode_rise = pp_mode && !pp_mode_prev_r;
    wire pp_enable_rise = pp_enable && !pp_enable_prev_r;
    wire pp_enable_fall = !pp_enable && pp_enable_prev_r;
    wire [31:0] crc_after_beat = crc32_beat(crc_r, s_axis_tdata, s_axis_tkeep);
    wire [21:0] byte_count_after_beat =
        {1'b0, byte_count_r} + {{19{1'b0}}, keep_bytes};
    wire beat_error = !keep_valid || keep_bytes == 3'd0 ||
                      byte_count_after_beat > 22'h1f_ffff;
    wire [20:0] byte_count_next = byte_count_after_beat[20:0];
    wire error_next = error_r || beat_error;
    wire [12:0] fifo_min_next = (!fifo_count_seen_r || fifo_rd_count < fifo_min_count_r) ?
                                 fifo_rd_count : fifo_min_count_r;
    wire [12:0] fifo_max_next = (!fifo_count_seen_r || fifo_rd_count > fifo_max_count_r) ?
                                 fifo_rd_count : fifo_max_count_r;
    wire [31:0] first_valid_cycle_next = first_valid_seen_r ?
                                         first_valid_cycle_r : cycle_count_r + 32'd1;

    always @(posedge clk) begin
        if (!bench) begin
            byte_count_r <= 21'd0;
            crc_r <= 32'hffff_ffff;
            checksum_r <= 32'd0;
            done_r <= 1'b0;
            last_r <= 1'b0;
            error_r <= 1'b0;
            first_valid_cycle_r <= 32'd0;
            first_valid_seen_r <= 1'b0;
            fifo_min_count_r <= 13'h1fff;
            fifo_max_count_r <= 13'd0;
            fifo_count_seen_r <= 1'b0;
            cycle_count_r <= 32'd0;
        end else begin
            cycle_count_r <= cycle_count_r + 32'd1;
            if (accept) begin
                byte_count_r <= byte_count_next;
                crc_r <= crc_after_beat;
                error_r <= error_next;
                if (!first_valid_seen_r) begin
                    first_valid_seen_r <= 1'b1;
                    first_valid_cycle_r <= cycle_count_r + 32'd1;
                end
                fifo_min_count_r <= fifo_min_next;
                fifo_max_count_r <= fifo_max_next;
                fifo_count_seen_r <= 1'b1;
                if (s_axis_tlast === 1'b1) begin
                    done_r <= 1'b1;
                    last_r <= 1'b1;
                    checksum_r <= ~crc_after_beat;
                    bench_result_status_r <= {1'b0, 1'b1, 1'b1, error_next,
                                               7'd0, byte_count_next};
                    bench_result_checksum_r <= ~crc_after_beat;
                    bench_result_first_valid_cycle_r <= first_valid_cycle_next;
                    bench_result_fifo_min_count_r <= fifo_min_next;
                    bench_result_fifo_max_count_r <= fifo_max_next;
                    bench_result_req_r <= ~bench_result_req_r;
                end
            end
        end
    end

    wire pp_selected_valid = pp_bank_status_r ? pp_result_valid1_sync_r :
                              pp_result_valid0_sync_r;
    wire [31:0] pp_selected_status = pp_bank_status_r ? pp_result_status1_sync_r :
                                      pp_result_status0_sync_r;
    wire [31:0] pp_selected_checksum = pp_bank_status_r ? pp_result_checksum1_sync_r :
                                        pp_result_checksum0_sync_r;
    wire [12:0] pp_selected_fifo_min = pp_bank_status_r ? pp_result_fifo_min1_sync_r :
                                        pp_result_fifo_min0_sync_r;
    wire [12:0] pp_selected_fifo_max = pp_bank_status_r ? pp_result_fifo_max1_sync_r :
                                        pp_result_fifo_max0_sync_r;
    assign s_axis_tready = bench ? 1'b1 : m_axis_tready;
    assign m_axis_tdata = s_axis_tdata;
    assign m_axis_tkeep = s_axis_tkeep;
    assign m_axis_tvalid = !bench && (s_axis_tvalid === 1'b1);
    assign m_axis_tlast = s_axis_tlast;
    assign bench_status = (benchmark_enable === 1'b1) ?
                          {1'b1, bench_status_sync_r[30:0]} :
                          (pp_mode_status_r && pp_selected_valid ? pp_selected_status : 32'd0);
    assign bench_checksum = (benchmark_enable === 1'b1) ?
                            bench_checksum_sync_r :
                            (pp_mode_status_r && pp_selected_valid ? pp_selected_checksum : 32'd0);
    assign bench_first_valid_cycle = (benchmark_enable === 1'b1) ?
                                     bench_first_valid_cycle_sync_r :
                                     (pp_mode_status_r && pp_selected_valid ? pp_gap_cycles_sync_r : 32'd0);
    assign bench_fifo_min_count = (benchmark_enable === 1'b1) ?
                                  bench_fifo_min_count_sync_r :
                                  (pp_mode_status_r && pp_selected_valid ? pp_selected_fifo_min : 13'd0);
    assign bench_fifo_max_count = (benchmark_enable === 1'b1) ?
                                  bench_fifo_max_count_sync_r :
                                  (pp_mode_status_r && pp_selected_valid ? pp_selected_fifo_max : 13'd0);
    assign bench_fifo_watermark = (benchmark_enable === 1'b1) ?
                                  {6'd0, bench_fifo_max_count_sync_r,
                                   bench_fifo_min_count_sync_r} :
                                  (pp_mode_status_r && pp_selected_valid ?
                                   {6'd0, pp_selected_fifo_max, pp_selected_fifo_min} : 32'd0);

    always @(posedge status_clk) begin
        bench_result_req_meta_r <= bench_result_req_r;
        bench_result_req_sync_r <= bench_result_req_meta_r;
        pp_mode_status_meta_r <= (stream_control[2] === 1'b1);
        pp_mode_status_r <= pp_mode_status_meta_r;
        pp_bank_status_meta_r <= (stream_control[1] === 1'b1);
        pp_bank_status_r <= pp_bank_status_meta_r;
        pp_result_req_meta_r <= pp_result_req_r;
        pp_result_req_sync_r <= pp_result_req_meta_r;
        if (!benchmark_enable) begin
            bench_result_req_seen_r <= bench_result_req_sync_r;
            bench_status_sync_r <= 32'd0;
            bench_checksum_sync_r <= 32'd0;
            bench_first_valid_cycle_sync_r <= 32'd0;
            bench_fifo_min_count_sync_r <= 13'd0;
            bench_fifo_max_count_sync_r <= 13'd0;
        end else if (bench_result_req_sync_r != bench_result_req_seen_r) begin
            bench_status_sync_r <= bench_result_status_r;
            bench_checksum_sync_r <= bench_result_checksum_r;
            bench_first_valid_cycle_sync_r <= bench_result_first_valid_cycle_r;
            bench_fifo_min_count_sync_r <= bench_result_fifo_min_count_r;
            bench_fifo_max_count_sync_r <= bench_result_fifo_max_count_r;
            bench_result_req_seen_r <= bench_result_req_sync_r;
        end
        if (pp_result_req_sync_r != pp_result_req_seen_r) begin
            pp_result_valid0_sync_r <= pp_result_valid0_r;
            pp_result_valid1_sync_r <= pp_result_valid1_r;
            pp_result_status0_sync_r <= pp_result_status0_r;
            pp_result_status1_sync_r <= pp_result_status1_r;
            pp_result_checksum0_sync_r <= pp_result_checksum0_r;
            pp_result_checksum1_sync_r <= pp_result_checksum1_r;
            pp_result_fifo_min0_sync_r <= pp_result_fifo_min0_r;
            pp_result_fifo_min1_sync_r <= pp_result_fifo_min1_r;
            pp_result_fifo_max0_sync_r <= pp_result_fifo_max0_r;
            pp_result_fifo_max1_sync_r <= pp_result_fifo_max1_r;
            pp_gap_cycles_sync_r <= pp_gap_cycles_r;
            pp_result_req_seen_r <= pp_result_req_sync_r;
        end
    end

    always @(posedge clk) begin
        stream_control_meta_r <= stream_control;
        stream_control_clk_r <= stream_control_meta_r;
    end

    always @(posedge clk) begin
        pp_mode_prev_r <= pp_mode;
        pp_enable_prev_r <= pp_enable;
        if (!pp_mode) begin
            pp_capture_active_r <= 1'b0;
            pp_cycle_count_r <= 32'd0;
        end else begin
            pp_cycle_count_r <= pp_cycle_count_r + 32'd1;
            if (pp_mode_rise) begin
                pp_result_valid0_r <= 1'b0;
                pp_result_valid1_r <= 1'b0;
                pp_gap_cycles_r <= 32'd0;
                pp_last_tlast_valid_r <= 1'b0;
            end
            if (pp_enable_rise) begin
                pp_capture_active_r <= 1'b1;
                pp_bank_active_r <= pp_bank;
                pp_first_seen_r <= 1'b0;
                pp_last_seen_r <= 1'b0;
                pp_keep_error_r <= 1'b0;
                pp_underflow_seen_r <= 1'b0;
                pp_last_keep_r <= 4'd0;
                pp_byte_count_r <= 21'd0;
                pp_crc_r <= 32'hffff_ffff;
                pp_fifo_min_count_r <= 13'd0;
                pp_fifo_max_count_r <= 13'd0;
                pp_fifo_count_seen_r <= 1'b0;
            end
            if (pp_capture_active_r && pp_enable) begin
                if (pp_fifo_count_seen_r) begin
                    if (fifo_rd_count < pp_fifo_min_count_r)
                        pp_fifo_min_count_r <= fifo_rd_count;
                    if (fifo_rd_count > pp_fifo_max_count_r)
                        pp_fifo_max_count_r <= fifo_rd_count;
                end
                if (stream_underflow === 1'b1)
                    pp_underflow_seen_r <= 1'b1;
                if (pp_accept) begin
                    if (!pp_fifo_count_seen_r) begin
                        pp_fifo_min_count_r <= fifo_rd_count;
                        pp_fifo_max_count_r <= fifo_rd_count;
                        pp_fifo_count_seen_r <= 1'b1;
                    end
                    pp_first_seen_r <= 1'b1;
                    if (!pp_first_seen_r && pp_last_tlast_valid_r)
                        pp_gap_cycles_r <= (pp_cycle_count_r + 32'd1) -
                                           pp_last_tlast_cycle_r;
                    if (pp_beat_error)
                        pp_keep_error_r <= 1'b1;
                    pp_last_keep_r <= s_axis_tkeep;
                    pp_byte_count_r <= pp_byte_count_after_beat[20:0];
                    pp_crc_r <= pp_crc_after_beat;
                    if (s_axis_tlast === 1'b1) begin
                        pp_last_seen_r <= 1'b1;
                        pp_last_tlast_valid_r <= 1'b1;
                        pp_last_tlast_cycle_r <= pp_cycle_count_r + 32'd1;
                    end
                end
            end
            if (pp_capture_active_r && (pp_enable_fall || !pp_mode)) begin
                if (!pp_bank_active_r) begin
                    pp_result_valid0_r <= 1'b1;
                    pp_result_status0_r <= {1'b1, pp_last_seen_r,
                        pp_keep_error_r,
                        (pp_underflow_seen_r || (stream_underflow === 1'b1)),
                        pp_last_keep_r, pp_bank_active_r, 2'b00,
                        pp_byte_count_r};
                    pp_result_checksum0_r <= ~pp_crc_r;
                    pp_result_fifo_min0_r <= pp_fifo_count_seen_r ?
                                              pp_fifo_min_count_r : 13'd0;
                    pp_result_fifo_max0_r <= pp_fifo_count_seen_r ?
                                              pp_fifo_max_count_r : 13'd0;
                end else begin
                    pp_result_valid1_r <= 1'b1;
                    pp_result_status1_r <= {1'b1, pp_last_seen_r,
                        pp_keep_error_r,
                        (pp_underflow_seen_r || (stream_underflow === 1'b1)),
                        pp_last_keep_r, pp_bank_active_r, 2'b00,
                        pp_byte_count_r};
                    pp_result_checksum1_r <= ~pp_crc_r;
                    pp_result_fifo_min1_r <= pp_fifo_count_seen_r ?
                                              pp_fifo_min_count_r : 13'd0;
                    pp_result_fifo_max1_r <= pp_fifo_count_seen_r ?
                                              pp_fifo_max_count_r : 13'd0;
                end
                pp_result_req_r <= ~pp_result_req_r;
                pp_capture_active_r <= 1'b0;
            end
        end
    end

endmodule
