`timescale 1ns / 1ps
module iq_constellation_mapper_tb;
    reg [2:0] mode;
    reg [5:0] symbol_bits;
    wire signed [7:0] i_symbol;
    wire signed [7:0] q_symbol;
    wire [2:0] consume_bits;

    integer errors = 0;
    integer checks = 0;
    integer i_code;
    integer q_code;

    iq_constellation_mapper uut (
        .mode(mode),
        .symbol_bits(symbol_bits),
        .i_symbol(i_symbol),
        .q_symbol(q_symbol),
        .consume_bits(consume_bits)
    );

    function integer qam16_level;
        input [1:0] code;
        begin
            case (code)
                2'b00: qam16_level = 126;
                2'b01: qam16_level = 42;
                2'b10: qam16_level = -126;
                2'b11: qam16_level = -42;
                default: qam16_level = 0;
            endcase
        end
    endfunction

    function integer qam64_level;
        input [2:0] code;
        begin
            case (code)
                3'b000: qam64_level = 126;
                3'b001: qam64_level = 90;
                3'b010: qam64_level = 18;
                3'b011: qam64_level = 54;
                3'b100: qam64_level = -126;
                3'b101: qam64_level = -90;
                3'b110: qam64_level = -18;
                3'b111: qam64_level = -54;
                default: qam64_level = 0;
            endcase
        end
    endfunction

    task check_mapping;
        input [2:0] check_mode;
        input [5:0] check_bits;
        input integer expected_i;
        input integer expected_q;
        input [2:0] expected_consume;
        input [255:0] check_name;
        begin
            mode = check_mode;
            symbol_bits = check_bits;
            #1;
            checks = checks + 1;
            if (($signed(i_symbol) !== expected_i) ||
                ($signed(q_symbol) !== expected_q) ||
                (consume_bits !== expected_consume)) begin
                $display("FAIL %0s: mode=%0d bits=%b got I=%0d Q=%0d consume=%0d expected I=%0d Q=%0d consume=%0d",
                         check_name, check_mode, check_bits, $signed(i_symbol),
                         $signed(q_symbol), consume_bits, expected_i, expected_q,
                         expected_consume);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        check_mapping(3'd0, 6'b000000, 127, 0, 3'd1, "BPSK b0=0");
        check_mapping(3'd0, 6'b100000, -127, 0, 3'd1, "BPSK b0=1");

        check_mapping(3'd1, 6'b000000, 127, 127, 3'd2, "QPSK b0b1=00");
        check_mapping(3'd1, 6'b010000, 127, -127, 3'd2, "QPSK b0b1=01");
        check_mapping(3'd1, 6'b100000, -127, 127, 3'd2, "QPSK b0b1=10");
        check_mapping(3'd1, 6'b110000, -127, -127, 3'd2, "QPSK b0b1=11");

        check_mapping(3'd2, 6'b000000, 127, 127, 3'd2, "QAM4 b0b1=00");
        check_mapping(3'd2, 6'b010000, 127, -127, 3'd2, "QAM4 b0b1=01");
        check_mapping(3'd2, 6'b100000, -127, 127, 3'd2, "QAM4 b0b1=10");
        check_mapping(3'd2, 6'b110000, -127, -127, 3'd2, "QAM4 b0b1=11");

        for (i_code = 0; i_code < 4; i_code = i_code + 1) begin
            for (q_code = 0; q_code < 4; q_code = q_code + 1) begin
                check_mapping(3'd3, {i_code[1:0], q_code[1:0], 2'b00},
                              qam16_level(i_code[1:0]), qam16_level(q_code[1:0]),
                              3'd4, "QAM16 I/Q code");
            end
        end

        for (i_code = 0; i_code < 8; i_code = i_code + 1) begin
            for (q_code = 0; q_code < 8; q_code = q_code + 1) begin
                check_mapping(3'd4, {i_code[2:0], q_code[2:0]},
                              qam64_level(i_code[2:0]), qam64_level(q_code[2:0]),
                              3'd6, "QAM64 I/Q code");
            end
        end

        for (i_code = 5; i_code < 8; i_code = i_code + 1)
            check_mapping(i_code[2:0], 6'b101011, 0, 0, 3'd0, "illegal mode");

        if (errors == 0)
            $display("PASS iq_constellation_mapper");
        else begin
            $display("FAIL iq_constellation_mapper: %0d of %0d checks failed", errors, checks);
            $fatal(1, "FAIL iq_constellation_mapper: %0d of %0d checks failed", errors, checks);
        end
        $finish;
    end
endmodule
