`timescale 1ns / 1ps
// Deterministic USER frame playback controller.
module playback_ctrl (
    input wire clk,
    input wire rst_n,
    input wire manual_mode,
    input wire trigger_external,
    input wire trigger_polarity,
    input wire trigger_supported,
    input wire trigger_in,
    input wire source_user,
    input wire user_committed,
    input wire user_loop,
    input wire mode_valid,
    input wire source_ready,
    input wire frame_done_pulse,
    input wire drain_active,
    input wire drain_done_pulse,
    input wire frame_underflow,
    input wire [2:0] cmd_code,
    input wire cmd_epoch,
    input wire [15:0] repeat_cfg,
    output reg [2:0] state,
    output reg frame_done,
    output reg underflow,
    output reg trigger_seen,
    output reg stop_pending,
    output reg error_valid,
    output reg [15:0] repeat_remaining,
    output reg [7:0] error_code,
    output wire run_enable,
    output reg restart
);
    localparam [2:0] IDLE = 3'd0;
    localparam [2:0] ARMED = 3'd1;
    localparam [2:0] STARTING = 3'd2;
    localparam [2:0] RUNNING = 3'd3;
    localparam [2:0] DRAIN = 3'd4;
    localparam [2:0] DONE = 3'd5;
    localparam [2:0] ERROR = 3'd6;

    localparam [2:0] CMD_ARM = 3'd1;
    localparam [2:0] CMD_START = 3'd2;
    localparam [2:0] CMD_STOP = 3'd3;
    localparam [2:0] CMD_CLEAR = 3'd4;

    localparam [7:0] ERR_BAD_MODE = 8'h01;
    localparam [7:0] ERR_SOURCE = 8'h02;
    localparam [7:0] ERR_TRIGGER = 8'h03;
    localparam [7:0] ERR_STATE = 8'h04;
    localparam [7:0] ERR_UNDERFLOW = 8'h05;

    reg cmd_epoch_seen;
    reg trigger_prev;
    wire trigger_level = trigger_polarity ? trigger_in : !trigger_in;
    wire trigger_edge = trigger_level && !trigger_prev;
    wire command_event = cmd_epoch != cmd_epoch_seen;

    assign run_enable = (state == STARTING) || (state == RUNNING) || (state == DRAIN);

    task enter_error;
        input [7:0] code;
        begin
            state <= ERROR;
            error_valid <= 1'b1;
            error_code <= code;
            stop_pending <= 1'b0;
        end
    endtask

    task finish_frame;
        begin
            frame_done <= 1'b1;
            stop_pending <= 1'b0;
            if (repeat_remaining == 16'd0) begin
                state <= STARTING;
                restart <= 1'b1;
            end else if (repeat_remaining > 16'd1) begin
                repeat_remaining <= repeat_remaining - 1'b1;
                state <= STARTING;
                restart <= 1'b1;
            end else begin
                repeat_remaining <= 16'd0;
                state <= DONE;
            end
        end
    endtask

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            frame_done <= 1'b0;
            underflow <= 1'b0;
            trigger_seen <= 1'b0;
            stop_pending <= 1'b0;
            error_valid <= 1'b0;
            repeat_remaining <= 16'd0;
            error_code <= 8'd0;
            cmd_epoch_seen <= 1'b0;
            trigger_prev <= 1'b0;
            restart <= 1'b0;
        end else begin
            restart <= 1'b0;
            trigger_prev <= trigger_level;

            if (command_event) begin
                cmd_epoch_seen <= cmd_epoch;
                case (cmd_code)
                    CMD_CLEAR: begin
                        state <= IDLE;
                        frame_done <= 1'b0;
                        underflow <= 1'b0;
                        trigger_seen <= 1'b0;
                        stop_pending <= 1'b0;
                        error_valid <= 1'b0;
                        repeat_remaining <= 16'd0;
                        error_code <= 8'd0;
                    end
                    CMD_ARM: begin
                        if (state != IDLE && state != DONE && state != ERROR)
                            enter_error(ERR_STATE);
                        else if (!manual_mode)
                            enter_error(ERR_BAD_MODE);
                        else if (!source_user || !user_committed || user_loop)
                            enter_error(ERR_SOURCE);
                        else if (!mode_valid)
                            enter_error(ERR_BAD_MODE);
                        else if (trigger_external && !trigger_supported)
                            enter_error(ERR_TRIGGER);
                        else begin
                            state <= ARMED;
                            frame_done <= 1'b0;
                            underflow <= 1'b0;
                            trigger_seen <= 1'b0;
                            stop_pending <= 1'b0;
                            error_valid <= 1'b0;
                            error_code <= 8'd0;
                            repeat_remaining <= repeat_cfg;
                        end
                    end
                    CMD_START: begin
                        if (state != ARMED)
                            enter_error(ERR_STATE);
                        else if (trigger_external)
                            state <= ARMED;
                        else begin
                            state <= STARTING;
                            restart <= 1'b1;
                        end
                    end
                    CMD_STOP: begin
                        if (state == ARMED) begin
                            stop_pending <= 1'b0;
                            repeat_remaining <= 16'd0;
                            state <= DONE;
                        end else if (state == STARTING) begin
                            if (source_ready) begin
                                stop_pending <= 1'b0;
                                repeat_remaining <= 16'd0;
                                state <= DONE;
                            end else begin
                                stop_pending <= 1'b1;
                            end
                        end else if (state == RUNNING) begin
                            if (frame_done_pulse) begin
                                if (drain_active) begin
                                    stop_pending <= 1'b1;
                                    state <= DRAIN;
                                end else begin
                                    stop_pending <= 1'b0;
                                    repeat_remaining <= 16'd0;
                                    state <= DONE;
                                    frame_done <= 1'b1;
                                end
                            end else begin
                                stop_pending <= 1'b1;
                            end
                        end else if (state == DRAIN) begin
                            if (drain_done_pulse) begin
                                stop_pending <= 1'b0;
                                repeat_remaining <= 16'd0;
                                state <= DONE;
                                frame_done <= 1'b1;
                            end else begin
                                stop_pending <= 1'b1;
                            end
                        end else
                            enter_error(ERR_STATE);
                    end
                    default: enter_error(ERR_STATE);
                endcase
            end else begin
                if (trigger_external && state == ARMED && trigger_edge) begin
                    trigger_seen <= 1'b1;
                    state <= STARTING;
                    restart <= 1'b1;
                end
                case (state)
                    STARTING: begin
                        if (frame_underflow) begin
                            underflow <= 1'b1;
                            enter_error(ERR_UNDERFLOW);
                        end else if (stop_pending && source_ready) begin
                            stop_pending <= 1'b0;
                            repeat_remaining <= 16'd0;
                            state <= DONE;
                        end else if (source_ready) begin
                            state <= RUNNING;
                        end
                    end
                    RUNNING: begin
                        if (frame_underflow) begin
                            underflow <= 1'b1;
                            enter_error(ERR_UNDERFLOW);
                        end else if (frame_done_pulse) begin
                            if (drain_active === 1'b1)
                                state <= DRAIN;
                            else if (stop_pending) begin
                                stop_pending <= 1'b0;
                                repeat_remaining <= 16'd0;
                                state <= DONE;
                                frame_done <= 1'b1;
                            end else
                                finish_frame;
                        end
                    end
                    DRAIN: begin
                        if (frame_underflow) begin
                            underflow <= 1'b1;
                            enter_error(ERR_UNDERFLOW);
                        end else if (drain_done_pulse === 1'b1) begin
                            if (stop_pending) begin
                                stop_pending <= 1'b0;
                                repeat_remaining <= 16'd0;
                                state <= DONE;
                                frame_done <= 1'b1;
                            end else
                                finish_frame;
                        end
                    end
                    default: begin end
                endcase
            end
        end
    end
endmodule
