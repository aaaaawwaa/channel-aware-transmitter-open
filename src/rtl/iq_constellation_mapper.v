`timescale 1ns / 1ps
module iq_constellation_mapper (
    input wire [2:0] mode, input wire [5:0] symbol_bits,
    output reg signed [7:0] i_symbol, output reg signed [7:0] q_symbol,
    output reg [2:0] consume_bits
);
    always @* begin
        i_symbol = 8'sd0;
        q_symbol = 8'sd0;
        consume_bits = 3'd0;

        case (mode)
            3'd0: begin // BPSK
                consume_bits = 3'd1;
                if (symbol_bits[5])
                    i_symbol = -8'sd127;
                else
                    i_symbol = 8'sd127;
            end

            3'd1, 3'd2: begin // QPSK and QAM4
                consume_bits = 3'd2;
                if (symbol_bits[5])
                    i_symbol = -8'sd127;
                else
                    i_symbol = 8'sd127;
                if (symbol_bits[4])
                    q_symbol = -8'sd127;
                else
                    q_symbol = 8'sd127;
            end

            3'd3: begin // QAM16
                consume_bits = 3'd4;
                case (symbol_bits[5:4])
                    2'b00: i_symbol = 8'sd126;
                    2'b01: i_symbol = 8'sd42;
                    2'b10: i_symbol = -8'sd126;
                    2'b11: i_symbol = -8'sd42;
                    default: i_symbol = 8'sd0;
                endcase
                case (symbol_bits[3:2])
                    2'b00: q_symbol = 8'sd126;
                    2'b01: q_symbol = 8'sd42;
                    2'b10: q_symbol = -8'sd126;
                    2'b11: q_symbol = -8'sd42;
                    default: q_symbol = 8'sd0;
                endcase
            end

            3'd4: begin // QAM64
                consume_bits = 3'd6;
                case (symbol_bits[5:3])
                    3'b000: i_symbol = 8'sd126;
                    3'b001: i_symbol = 8'sd90;
                    3'b010: i_symbol = 8'sd18;
                    3'b011: i_symbol = 8'sd54;
                    3'b100: i_symbol = -8'sd126;
                    3'b101: i_symbol = -8'sd90;
                    3'b110: i_symbol = -8'sd18;
                    3'b111: i_symbol = -8'sd54;
                    default: i_symbol = 8'sd0;
                endcase
                case (symbol_bits[2:0])
                    3'b000: q_symbol = 8'sd126;
                    3'b001: q_symbol = 8'sd90;
                    3'b010: q_symbol = 8'sd18;
                    3'b011: q_symbol = 8'sd54;
                    3'b100: q_symbol = -8'sd126;
                    3'b101: q_symbol = -8'sd90;
                    3'b110: q_symbol = -8'sd18;
                    3'b111: q_symbol = -8'sd54;
                    default: q_symbol = 8'sd0;
                endcase
            end

            default: begin // Illegal mode encodings
                i_symbol = 8'sd0;
                q_symbol = 8'sd0;
                consume_bits = 3'd0;
            end
        endcase
    end
endmodule
