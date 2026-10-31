`timescale 1ns / 1ps
// AXI4-Stream raw USER bit source. The low byte lane is the first payload byte;
// each byte is consumed MSB-first, matching user_bit_source.
module user_stream_source (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        active,
    input  wire [31:0] bit_length,
    input  wire        advance,
    input  wire [2:0]  advance_bits,
    input  wire [31:0] s_axis_tdata,
    input  wire [3:0]  s_axis_tkeep,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    output wire [5:0]  symbol_bits,
    output wire        ready,
    output wire        done,
    output wire        underflow,
    output wire        frame_done_pulse
);
    function [7:0] rev8;
        input [7:0] x;
        begin rev8 = {x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]}; end
    endfunction

    reg [63:0] win;
    reg [6:0] win_cnt;
    reg [31:0] bit_ptr;
    reg input_done_r;
    reg done_r;
    reg underflow_r;
    reg frame_done_r;

    wire [31:0] stream_word = {rev8(s_axis_tdata[31:24]),
                               rev8(s_axis_tdata[23:16]),
                               rev8(s_axis_tdata[15:8]),
                               rev8(s_axis_tdata[7:0])};
    wire keep_valid = (s_axis_tkeep == 4'b0001) ||
                      (s_axis_tkeep == 4'b0011) ||
                      (s_axis_tkeep == 4'b0111) ||
                      (s_axis_tkeep == 4'b1111);
    wire [2:0] adv = advance_bits;
    wire [32:0] remaining_bits = (bit_ptr < bit_length) ?
                                  ({1'b0, bit_length} - {1'b0, bit_ptr}) : 33'd0;
    wire partial_tail = (remaining_bits != 33'd0) &&
                         (remaining_bits < {30'd0, adv});
    wire initial_short = (bit_length != 32'd0) && (bit_ptr == 32'd0) &&
                          (bit_length < {29'd0, adv});
    wire [2:0] required_bits = partial_tail ? remaining_bits[2:0] : adv;
    wire [2:0] consume_bits = partial_tail ? remaining_bits[2:0] : adv;
    wire consume_now = advance && ready;
    wire stream_space = (win_cnt <= 7'd32);
    wire [5:0] symbol_bits_raw = {win[0], win[1], win[2], win[3], win[4], win[5]};
    wire [5:0] symbol_mask = partial_tail ?
                              (6'h3f << (3'd6 - remaining_bits[2:0])) : 6'h3f;

    assign ready = active && !done_r && !underflow_r && !initial_short &&
                   (win_cnt >= {4'd0, required_bits});
    assign s_axis_tready = active && (bit_length != 32'd0) &&
                           !done_r && !underflow_r && !input_done_r &&
                           stream_space;
    assign symbol_bits = symbol_bits_raw & symbol_mask;
    assign done = done_r || (active && (bit_length == 32'd0));
    assign underflow = underflow_r;
    assign frame_done_pulse = frame_done_r;

    reg [63:0] win_c;
    reg [6:0] win_cnt_c;
    reg [31:0] bit_ptr_c;
    reg [32:0] remaining_c;
    reg [2:0] required_c;
    reg input_done_c;
    reg done_c;
    reg underflow_c;
    reg frame_done_c;
    wire stream_fire = s_axis_tvalid && s_axis_tready;

    always @* begin
        win_c = win;
        win_cnt_c = win_cnt;
        bit_ptr_c = bit_ptr;
        remaining_c = 33'd0;
        required_c = 3'd0;
        input_done_c = input_done_r;
        done_c = done_r;
        underflow_c = underflow_r;
        frame_done_c = 1'b0;

        if (!active) begin
            win_c = 64'd0;
            win_cnt_c = 7'd0;
            bit_ptr_c = 32'd0;
            input_done_c = 1'b0;
            done_c = 1'b0;
            underflow_c = 1'b0;
        end else begin
            if (consume_now) begin
                win_c = win >> consume_bits;
                win_cnt_c = win_cnt - {4'd0, consume_bits};
                bit_ptr_c = bit_ptr + {29'd0, consume_bits};
            end

            if (stream_fire) begin
                if (!keep_valid) begin
                    underflow_c = 1'b1;
                end else begin
                    case (s_axis_tkeep)
                        4'b0001: begin
                            win_c[win_cnt_c +: 8] = stream_word[7:0];
                            win_cnt_c = win_cnt_c + 7'd8;
                        end
                        4'b0011: begin
                            win_c[win_cnt_c +: 16] = stream_word[15:0];
                            win_cnt_c = win_cnt_c + 7'd16;
                        end
                        4'b0111: begin
                            win_c[win_cnt_c +: 24] = stream_word[23:0];
                            win_cnt_c = win_cnt_c + 7'd24;
                        end
                        default: begin
                            win_c[win_cnt_c +: 32] = stream_word;
                            win_cnt_c = win_cnt_c + 7'd32;
                        end
                    endcase
                end
                if (s_axis_tlast)
                    input_done_c = 1'b1;
            end

            if (bit_ptr_c < bit_length) begin
                remaining_c = {1'b0, bit_length} - {1'b0, bit_ptr_c};
                if (remaining_c < {30'd0, adv})
                    required_c = remaining_c[2:0];
                else
                    required_c = adv;
            end

            if (bit_length == 32'd0) begin
                if (!done_r) begin
                    done_c = 1'b1;
                    frame_done_c = 1'b1;
                end
            end else if (consume_now && (bit_ptr_c >= bit_length)) begin
                done_c = 1'b1;
                frame_done_c = 1'b1;
            end

            if (initial_short)
                underflow_c = 1'b1;
            if (input_done_c && (bit_ptr_c < bit_length) &&
                (win_cnt_c < {4'd0, required_c}))
                underflow_c = 1'b1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            win <= 64'd0;
            win_cnt <= 7'd0;
            bit_ptr <= 32'd0;
            input_done_r <= 1'b0;
            done_r <= 1'b0;
            underflow_r <= 1'b0;
            frame_done_r <= 1'b0;
        end else begin
            win <= win_c;
            win_cnt <= win_cnt_c;
            bit_ptr <= bit_ptr_c;
            input_done_r <= input_done_c;
            done_r <= done_c;
            underflow_r <= underflow_c;
            frame_done_r <= frame_done_c;
        end
    end
endmodule
