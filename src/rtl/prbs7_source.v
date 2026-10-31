`timescale 1ns / 1ps
module prbs7_source (
 input wire clk, input wire rst_n, input wire reset_seed,
 input wire [6:0] seed_in,
 input wire advance, input wire [2:0] advance_bits,
 output wire [5:0] symbol_bits
);
    localparam [6:0] PRBS7_SEED = 7'h7f;

    // The current state is also the first symbol bit.  Keeping this state
    // visible as a single register makes reset and variable-length advances
    // unambiguous to the source's consumers.
    reg [6:0] state;

    function [6:0] lfsr_step;
        input [6:0] current;
        begin
            lfsr_step = {current[5:0], current[6] ^ current[5]};
        end
    endfunction

    function [6:0] advance_state;
        input [6:0] current;
        input [2:0] count;
        integer i;
        begin
            advance_state = current;
            case (count)
                3'd1, 3'd2, 3'd4, 3'd6: begin
                    for (i = 0; i < count; i = i + 1)
                        advance_state = lfsr_step(advance_state);
                end
                default: advance_state = current;
            endcase
        end
    endfunction

    // Six output bits are the current bit6 followed by five look-ahead bits.
    wire [6:0] state_1 = lfsr_step(state);
    wire [6:0] state_2 = lfsr_step(state_1);
    wire [6:0] state_3 = lfsr_step(state_2);
    wire [6:0] state_4 = lfsr_step(state_3);
    wire [6:0] state_5 = lfsr_step(state_4);

    assign symbol_bits = {
        state[6], state_1[6], state_2[6],
        state_3[6], state_4[6], state_5[6]
    };

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= (seed_in == 7'd0) ? PRBS7_SEED : seed_in;
        else if (reset_seed)
            state <= (seed_in == 7'd0) ? PRBS7_SEED : seed_in;
        else if (advance)
            state <= advance_state(state, advance_bits);
    end
endmodule
