`timescale 1ns / 1ps
module user_stream_source_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg active = 0;
    reg [31:0] bit_length = 0;
    reg advance = 0;
    reg [2:0] advance_bits = 1;
    reg [31:0] s_axis_tdata = 0;
    reg [3:0] s_axis_tkeep = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    reg s_axis_tlast = 0;
    wire [5:0] symbol_bits;
    wire ready, done, underflow, frame_done_pulse;

    user_stream_source uut (
        .clk(clk), .rst_n(rst_n), .active(active), .bit_length(bit_length),
        .advance(advance), .advance_bits(advance_bits),
        .s_axis_tdata(s_axis_tdata), .s_axis_tkeep(s_axis_tkeep),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
        .symbol_bits(symbol_bits), .ready(ready), .done(done),
        .underflow(underflow), .frame_done_pulse(frame_done_pulse)
    );

    always #5 clk = ~clk;
    integer errs = 0;

    task chk;
        input string name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    task send_word;
        input [31:0] data;
        input [3:0] keep;
        input last;
        integer cycles;
        begin
            s_axis_tdata = data;
            s_axis_tkeep = keep;
            s_axis_tlast = last;
            s_axis_tvalid = 1'b1;
            cycles = 0;
            while (!s_axis_tready && cycles < 100) begin
                @(posedge clk); cycles = cycles + 1;
            end
            chk("stream word accepted", cycles < 100);
            @(posedge clk);
            #1;
            s_axis_tvalid = 1'b0;
            s_axis_tkeep = 4'b0000;
            s_axis_tlast = 1'b0;
        end
    endtask

    task emit;
        output [5:0] bits;
        integer cycles;
        begin
            cycles = 0;
            while (!ready && cycles < 100) begin
                @(posedge clk); cycles = cycles + 1;
            end
            chk("stream ready", cycles < 100);
            #1;
            bits = symbol_bits;
            advance = 1'b1;
            @(posedge clk);
            #1;
            advance = 1'b0;
        end
    endtask

    task reset_source;
        begin
            active = 1'b0;
            advance = 1'b0;
            s_axis_tvalid = 1'b0;
            s_axis_tkeep = 4'b0000;
            s_axis_tlast = 1'b0;
            @(posedge clk);
            active = 1'b1;
            #1;
        end
    endtask

    initial begin
        #20;
        rst_n = 1'b1;

        // Two AXI words contain five payload bytes: C0 38 0E 81 7F.
        bit_length = 13'd40;
        advance_bits = 3'd2;
        reset_source;
        send_word(32'h810E38C0, 4'b1111, 1'b0);
        send_word(32'h0000007F, 4'b0001, 1'b1);
        begin : full_payload
            reg [5:0] b;
            integer n;
            reg [5:0] exp [0:19];
            exp[0]=6'h30; exp[1]=6'h00; exp[2]=6'h00; exp[3]=6'h03;
            exp[4]=6'h0e; exp[5]=6'h38; exp[6]=6'h20; exp[7]=6'h00;
            exp[8]=6'h03; exp[9]=6'h0e; exp[10]=6'h3a; exp[11]=6'h28;
            exp[12]=6'h20; exp[13]=6'h01; exp[14]=6'h05; exp[15]=6'h17;
            exp[16]=6'h1f; exp[17]=6'h3f; exp[18]=6'h3c; exp[19]=6'h30;
            for (n = 0; n < 20; n = n + 1) begin
                emit(b);
                chk($sformatf("payload symbol %0d", n), b === exp[n]);
            end
            chk("payload done", done === 1'b1);
            chk("payload frame pulse", frame_done_pulse === 1'b1);
            chk("payload no underflow", underflow === 1'b0);
            chk("payload ready false", ready === 1'b0);
        end

        // A packet ending before bit_length must report underflow, not stall forever.
        bit_length = 13'd24;
        advance_bits = 3'd2;
        reset_source;
        send_word(32'h000038C0, 4'b0011, 1'b1);
        begin : short_packet
            reg [5:0] b;
            integer n;
            for (n = 0; n < 8; n = n + 1) emit(b);
            repeat (3) @(posedge clk);
            chk("short packet underflow", underflow === 1'b1);
            chk("short packet not done", done === 1'b0);
            chk("short packet ready false", ready === 1'b0);
        end

        // Backpressure must hold the source payload until a handshake occurs.
        bit_length = 13'd64;
        advance_bits = 3'd1;
        reset_source;
        send_word(32'h00000000, 4'b1111, 1'b0);
        send_word(32'h00000000, 4'b1111, 1'b1);
        chk("backpressure asserted", s_axis_tready === 1'b0);
        s_axis_tdata = 32'hA5A5A5A5;
        s_axis_tkeep = 4'b1111;
        s_axis_tlast = 1'b1;
        s_axis_tvalid = 1'b1;
        repeat (2) begin
            @(posedge clk);
            chk("backpressure data stable", s_axis_tdata === 32'hA5A5A5A5 && s_axis_tvalid === 1'b1);
        end
        s_axis_tvalid = 1'b0;

        // One-bit symbols must preserve MSB-first order across a partial final byte.
        bit_length = 13'd13;
        advance_bits = 3'd1;
        reset_source;
        send_word(32'h000001A6, 4'b0011, 1'b1);
        begin : one_bit_payload
            reg [5:0] b;
            reg [12:0] expected;
            integer n;
            expected = 13'b10100110_00000;
            for (n = 0; n < 13; n = n + 1) begin
                emit(b);
                chk($sformatf("one-bit payload %0d", n), b[5] === expected[12-n]);
            end
            chk("one-bit payload done", done === 1'b1);
            chk("one-bit payload no underflow", underflow === 1'b0);
            chk("one-bit frame pulse width", frame_done_pulse === 1'b1);
            @(posedge clk);
            #1;
            chk("one-bit frame pulse clears", frame_done_pulse === 1'b0);
        end

        // Four-bit symbols must cross byte and word boundaries without reordering.
        bit_length = 13'd16;
        advance_bits = 3'd4;
        reset_source;
        send_word(32'h000000C3, 4'b0001, 1'b0);
        send_word(32'h0000005A, 4'b0001, 1'b1);
        begin : four_bit_payload
            reg [5:0] b;
            integer n;
            reg [5:0] expected [0:3];
            expected[0] = 6'h30;
            expected[1] = 6'h0d;
            expected[2] = 6'h16;
            expected[3] = 6'h28;
            for (n = 0; n < 4; n = n + 1) begin
                emit(b);
                chk($sformatf("four-bit payload %0d", n), b === expected[n]);
            end
            chk("four-bit payload done", done === 1'b1);
            chk("four-bit payload no underflow", underflow === 1'b0);
        end

        // Six-bit symbols must span two bytes while retaining the final two bits.
        bit_length = 13'd12;
        advance_bits = 3'd6;
        reset_source;
        send_word(32'h00005AC3, 4'b0011, 1'b1);
        begin : six_bit_payload
            reg [5:0] b;
            integer n;
            reg [5:0] expected [0:1];
            expected[0] = 6'h30;
            expected[1] = 6'h35;
            for (n = 0; n < 2; n = n + 1) begin
                emit(b);
                chk($sformatf("six-bit payload %0d", n), b === expected[n]);
            end
            chk("six-bit payload done", done === 1'b1);
            chk("six-bit payload no underflow", underflow === 1'b0);
        end

        // A final partial six-bit symbol is zero-padded after the remaining bits.
        bit_length = 13'd8;
        advance_bits = 3'd6;
        reset_source;
        send_word(32'h000000A6, 4'b0001, 1'b1);
        begin : six_bit_tail_payload
            reg [5:0] b;
            integer n;
            reg [5:0] expected [0:1];
            expected[0] = 6'h29;
            expected[1] = 6'h20;
            for (n = 0; n < 2; n = n + 1) begin
                emit(b);
                chk($sformatf("six-bit tail payload %0d", n), b === expected[n]);
            end
            chk("six-bit tail payload done", done === 1'b1);
            chk("six-bit tail payload no underflow", underflow === 1'b0);
        end

        // Three valid lanes must be accepted as one contiguous payload beat.
        bit_length = 13'd24;
        advance_bits = 3'd6;
        reset_source;
        send_word(32'h00345AC3, 4'b0111, 1'b1);
        begin : three_lane_payload
            reg [5:0] b;
            integer n;
            for (n = 0; n < 4; n = n + 1) emit(b);
            chk("three-lane payload done", done === 1'b1);
            chk("three-lane payload no underflow", underflow === 1'b0);
        end

        // A zero-length frame completes immediately and must not accept stream data.
        bit_length = 13'd0;
        advance_bits = 3'd2;
        reset_source;
        chk("zero-length done", done === 1'b1);
        chk("zero-length no ready", s_axis_tready === 1'b0);
        chk("zero-length no underflow", underflow === 1'b0);

        // Non-contiguous TKEEP is rejected and remains a sticky underflow.
        bit_length = 13'd8;
        advance_bits = 3'd1;
        reset_source;
        s_axis_tdata = 32'h000000A6;
        s_axis_tkeep = 4'b0101;
        s_axis_tlast = 1'b1;
        s_axis_tvalid = 1'b1;
        while (!s_axis_tready) @(posedge clk);
        @(posedge clk);
        #1;
        s_axis_tvalid = 1'b0;
        chk("invalid keep underflow", underflow === 1'b1);
        chk("invalid keep not done", done === 1'b0);
        chk("invalid keep backpressure", s_axis_tready === 1'b0);

        // A length shorter than one symbol must fail instead of waiting forever.
        bit_length = 13'd3;
        advance_bits = 3'd4;
        reset_source;
        send_word(32'h000000A6, 4'b0001, 1'b1);
        repeat (2) @(posedge clk);
        chk("short symbol underflow", underflow === 1'b1);
        chk("short symbol not done", done === 1'b0);

        // A frame without TLAST is still complete when bit_length is consumed.
        bit_length = 13'd8;
        advance_bits = 3'd2;
        reset_source;
        send_word(32'h000000A6, 4'b0001, 1'b0);
        begin : no_tlast_payload
            reg [5:0] b;
            integer n;
            for (n = 0; n < 4; n = n + 1) emit(b);
            chk("no-TLAST payload done", done === 1'b1);
            chk("no-TLAST payload no underflow", underflow === 1'b0);
        end

        // An empty TLAST beat is a malformed short packet, not a completed frame.
        bit_length = 13'd8;
        advance_bits = 3'd1;
        reset_source;
        send_word(32'h00000000, 4'b0000, 1'b1);
        chk("empty TLAST underflow", underflow === 1'b1);
        chk("empty TLAST not done", done === 1'b0);

        if (errs == 0) $display("PASS user_stream_source_tb");
        else $display("FAIL user_stream_source_tb: %0d errors", errs);
        $finish;
    end
endmodule
