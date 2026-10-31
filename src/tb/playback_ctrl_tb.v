`timescale 1ns/1ps
module playback_ctrl_tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0;
    reg manual_mode = 1;
    reg trigger_external = 0;
    reg trigger_polarity = 1;
    reg trigger_supported = 1;
    reg trigger_in = 0;
    reg source_user = 1;
    reg user_committed = 1;
    reg user_loop = 0;
    reg mode_valid = 1;
    reg source_ready = 1;
    reg frame_done_pulse = 0;
    reg frame_underflow = 0;
    reg [2:0] cmd_code = 0;
    reg cmd_epoch = 0;
    reg [15:0] repeat_cfg = 0;
    wire [2:0] state;
    wire frame_done;
    wire underflow;
    wire trigger_seen;
    wire stop_pending;
    wire error_valid;
    wire [15:0] repeat_remaining;
    wire [7:0] error_code;
    reg drain_active = 1'b0;
    reg drain_done_pulse = 1'b0;
    wire run_enable;
    wire restart;

    playback_ctrl dut (
        .clk(clk), .rst_n(rst_n),
        .manual_mode(manual_mode), .trigger_external(trigger_external),
        .trigger_polarity(trigger_polarity), .trigger_supported(trigger_supported),
        .trigger_in(trigger_in), .source_user(source_user),
        .user_committed(user_committed), .user_loop(user_loop),
        .mode_valid(mode_valid), .source_ready(source_ready),
        .frame_done_pulse(frame_done_pulse), .drain_active(drain_active),
        .drain_done_pulse(drain_done_pulse), .frame_underflow(frame_underflow),
        .cmd_code(cmd_code), .cmd_epoch(cmd_epoch), .repeat_cfg(repeat_cfg),
        .state(state), .frame_done(frame_done), .underflow(underflow),
        .trigger_seen(trigger_seen), .stop_pending(stop_pending),
        .error_valid(error_valid), .repeat_remaining(repeat_remaining),
        .error_code(error_code), .run_enable(run_enable), .restart(restart)
    );

    task command;
        input [2:0] code;
        begin
            @(negedge clk);
            cmd_code = code;
            cmd_epoch = ~cmd_epoch;
            @(negedge clk);
            cmd_code = 0;
        end
    endtask

    task check_state;
        input [2:0] expected;
        begin
            if (state !== expected) begin
                $display("FAIL state=%0d expected=%0d", state, expected);
                $finish;
            end
        end
    endtask

    initial begin
        #20;
        rst_n = 1;
        repeat_cfg = 2;
        command(3'd1);
        check_state(3'd1); // ARMED
        command(3'd2);
        check_state(3'd2); // STARTING
        if (!run_enable || !restart) begin
            $display("FAIL start outputs run=%0d restart=%0d", run_enable, restart);
            $finish;
        end
        @(posedge clk); #1;
        check_state(3'd3); // RUNNING
        if (!run_enable || restart) begin
            $display("FAIL start transition run=%0d restart=%0d", run_enable, restart);
            $finish;
        end

        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd2); // next repeat waits for source restart
        if (repeat_remaining !== 16'd1 || !restart || !frame_done) begin
            $display("FAIL repeat remaining=%0d restart=%0d done=%0d",
                     repeat_remaining, restart, frame_done);
            $finish;
        end
        @(posedge clk); #1;
        check_state(3'd3);
        if (!frame_done || restart) begin
            $display("FAIL sticky frame_done/restart=%0d/%0d", frame_done, restart);
            $finish;
        end

        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd5); // DONE
        if (!frame_done || restart || repeat_remaining !== 16'd0) begin
            $display("FAIL done=%0d restart=%0d remaining=%0d",
                     frame_done, restart, repeat_remaining);
            $finish;
        end
        @(posedge clk); #1;
        if (!frame_done) begin
            $display("FAIL frame_done must remain sticky");
            $finish;
        end

        command(3'd4);
        check_state(3'd0);
        if (frame_done || error_valid) begin
            $display("FAIL clear state");
            $finish;
        end

        // ARM is rejected with BAD_MODE unless MANUAL playback is selected.
        manual_mode = 0;
        command(3'd1);
        check_state(3'd6);
        if (!error_valid || error_code !== 8'h01) begin
            $display("FAIL legacy ARM error valid=%0d code=%h", error_valid, error_code);
            $finish;
        end
        manual_mode = 1;
        command(3'd4);

        // STOP is boundary-safe: it remains pending until frame completion.
        repeat_cfg = 0;
        command(3'd1);
        command(3'd2);
        @(posedge clk); #1;
        check_state(3'd3);
        command(3'd3);
        if (state !== 3'd3 || !stop_pending) begin
            $display("FAIL stop must wait for frame boundary");
            $finish;
        end
        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd5);
        if (!frame_done || stop_pending || repeat_remaining !== 16'd0) begin
            $display("FAIL stop completion done=%0d pending=%0d remaining=%0d",
                     frame_done, stop_pending, repeat_remaining);
            $finish;
        end

        // STOP and a non-RRC frame boundary in the same cycle completes immediately.
        command(3'd4);
        repeat_cfg = 0;
        command(3'd1);
        command(3'd2);
        @(posedge clk); #1;
        frame_done_pulse = 1;
        @(negedge clk);
        cmd_code = 3'd3;
        cmd_epoch = ~cmd_epoch;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        cmd_code = 0;
        check_state(3'd5);
        if (!frame_done || stop_pending || repeat_remaining !== 16'd0) begin
            $display("FAIL concurrent stop completion done=%0d pending=%0d remaining=%0d",
                     frame_done, stop_pending, repeat_remaining);
            $finish;
        end

        command(3'd4);
        // Infinite repeat keeps RUNNING/STARTING and leaves remaining at zero.
        repeat_cfg = 0;
        command(3'd1);
        command(3'd2);
        @(posedge clk); #1;
        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd2);
        if (repeat_remaining !== 16'd0 || !restart || !frame_done) begin
            $display("FAIL infinite repeat");
            $finish;
        end
        @(posedge clk); #1;
        check_state(3'd3);
        command(3'd3);
        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd5);
        if (stop_pending) begin
            $display("FAIL infinite repeat stop pending");
            $finish;
        end

        command(3'd4);
        repeat_cfg = 1;
        drain_active = 1;
        command(3'd1);
        check_state(3'd1);
        command(3'd2);
        check_state(3'd2);
        @(posedge clk); #1;
        check_state(3'd3);
        frame_done_pulse = 1;
        @(posedge clk); #1;
        frame_done_pulse = 0;
        check_state(3'd4); // DRAIN
        if (frame_done || !run_enable) begin
            $display("FAIL drain entry done=%0d run=%0d", frame_done, run_enable);
            $finish;
        end
        drain_done_pulse = 1;
        @(posedge clk); #1;
        drain_done_pulse = 0;
        check_state(3'd5);
        if (!frame_done || restart) begin
            $display("FAIL drain completion done=%0d restart=%0d", frame_done, restart);
            $finish;
        end
        drain_active = 0;

        command(3'd4);
        trigger_external = 1;
        trigger_supported = 0;
        user_committed = 1;
        command(3'd1);
        check_state(3'd6);
        if (!error_valid || error_code !== 8'h03) begin
            $display("FAIL unsupported trigger error valid=%0d code=%h", error_valid, error_code);
            $finish;
        end
        trigger_external = 0;
        trigger_supported = 1;

        command(3'd4);
        user_committed = 0;
        command(3'd1);
        check_state(3'd6); // ERROR
        if (!error_valid || error_code !== 8'h02) begin
            $display("FAIL missing source error valid=%0d code=%h", error_valid, error_code);
            $finish;
        end
        $display("PASS playback_ctrl_tb");
        $finish;
    end
endmodule
