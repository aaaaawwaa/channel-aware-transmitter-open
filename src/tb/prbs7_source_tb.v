`timescale 1ns / 1ps
module prbs7_source_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg reset_seed = 0;
    reg [6:0] seed_in = 7'h7f;
    reg advance = 0;
    reg [2:0] advance_bits = 0;
    wire [5:0] symbol_bits;

    prbs7_source uut (
        .clk(clk),
        .rst_n(rst_n),
        .reset_seed(reset_seed),
        .seed_in(seed_in),
        .advance(advance),
        .advance_bits(advance_bits),
        .symbol_bits(symbol_bits)
    );

    always #5 clk = ~clk;

    integer errs = 0;
    reg [6:0] ref_state = 7'h7f;

    function [6:0] lfsr_step;
        input [6:0] s;
        begin
            lfsr_step = {s[5:0], s[6] ^ s[5]};
        end
    endfunction

    function [6:0] advance_ref;
        input [6:0] s;
        input [2:0] n;
        integer k;
        begin
            advance_ref = s;
            case (n)
                3'd1, 3'd2, 3'd4, 3'd6:
                    for (k = 0; k < n; k = k + 1)
                        advance_ref = lfsr_step(advance_ref);
                default:
                    advance_ref = s;
            endcase
        end
    endfunction

    function [5:0] lookahead_ref;
        input [6:0] s;
        reg [6:0] t;
        integer k;
        begin
            t = s;
            lookahead_ref[5] = t[6];
            for (k = 1; k < 6; k = k + 1) begin
                t = lfsr_step(t);
                lookahead_ref[5-k] = t[6];
            end
        end
    endfunction

    task check;
        input [255:0] name;
        input condition;
        begin
            if (condition)
                $display("PASS %0s", name);
            else begin
                $display("FAIL %0s", name);
                errs = errs + 1;
            end
        end
    endtask

    task consume_and_check;
        input [2:0] n;
        input [255:0] name;
        reg [6:0] expected_state;
        begin
            expected_state = advance_ref(ref_state, n);
            advance_bits = n;
            advance = 1;
            @(posedge clk);
            #1;
            ref_state = expected_state;
            check(name,
                  uut.state === expected_state &&
                  symbol_bits === lookahead_ref(expected_state));
        end
    endtask

    integer i;
    integer j;
    integer duplicate_count;
    reg [6:0] seen_states [0:126];

    initial begin
        #1;
        check("rst_n low restores seed", uut.state === 7'h7f);
        check("seed/look-ahead", symbol_bits === lookahead_ref(7'h7f));

        rst_n = 1;
        @(posedge clk);
        #1;
        check("released reset keeps seed", uut.state === 7'h7f &&
              symbol_bits === lookahead_ref(7'h7f));

        consume_and_check(3'd1, "advance 1 consumes one bit");
        consume_and_check(3'd2, "advance 2 consumes two bits");
        consume_and_check(3'd4, "advance 4 consumes four bits");
        consume_and_check(3'd6, "advance 6 consumes six bits");

        consume_and_check(3'd3, "invalid advance 3 holds state");
        consume_and_check(3'd5, "invalid advance 5 holds state");
        consume_and_check(3'd7, "invalid advance 7 holds state");

        advance = 0;
        @(posedge clk);
        #1;
        check("advance low holds state", uut.state === ref_state &&
              symbol_bits === lookahead_ref(ref_state));

        advance_bits = 3'd6;
        advance = 1;
        reset_seed = 1;
        @(posedge clk);
        #1;
        ref_state = 7'h7f;
        check("reset_seed restarts seed", uut.state === 7'h7f &&
              symbol_bits === lookahead_ref(7'h7f));
        reset_seed = 0;
        advance = 0;

        ref_state = 7'h7f;
        reset_seed = 1;
        @(posedge clk);
        #1;
        reset_seed = 0;
        advance_bits = 3'd1;
        advance = 1;
        duplicate_count = 0;
        for (i = 0; i < 128; i = i + 1) begin
            if (i < 127) begin
                if (uut.state === 7'd0)
                    errs = errs + 1;
                for (j = 0; j < i; j = j + 1)
                    if (uut.state === seen_states[j])
                        duplicate_count = duplicate_count + 1;
                seen_states[i] = uut.state;
            end else begin
                check("128th state returns to seed", uut.state === 7'h7f);
            end
            if (i < 127) begin
                @(posedge clk);
                #1;
            end
        end
        check("127 states are nonzero and unique", duplicate_count == 0);
        check("sequence reference matches look-ahead", symbol_bits ===
              lookahead_ref(uut.state));

        // ---- 可配置 seed: 边界值、中间值、默认回退、运行中重载 ----
        advance = 0;
        seed_in = 7'd1; reset_seed = 1; @(posedge clk); #1;
        check("seed 1 loads", uut.state === 7'd1 &&
              symbol_bits === lookahead_ref(7'd1));
        reset_seed = 0; @(posedge clk); #1;
        check("seed 1 held after deassert", uut.state === 7'd1 &&
              symbol_bits === lookahead_ref(7'd1));
        ref_state = 7'd1;
        consume_and_check(3'd1, "advance from seed 1");

        advance = 0;
        seed_in = 7'd42; reset_seed = 1; @(posedge clk); #1;
        check("seed 42 loads", uut.state === 7'd42 &&
              symbol_bits === lookahead_ref(7'd42));
        reset_seed = 0; @(posedge clk); #1;
        check("seed 42 held after deassert", uut.state === 7'd42 &&
              symbol_bits === lookahead_ref(7'd42));
        ref_state = 7'd42;
        consume_and_check(3'd1, "advance from seed 42");

        advance = 0;
        seed_in = 7'd127; reset_seed = 1; @(posedge clk); #1;
        check("seed 127 loads", uut.state === 7'd127 &&
              symbol_bits === lookahead_ref(7'd127));
        reset_seed = 0; @(posedge clk); #1;
        check("seed 127 held after deassert", uut.state === 7'd127 &&
              symbol_bits === lookahead_ref(7'd127));
        ref_state = 7'd127;
        consume_and_check(3'd1, "advance from seed 127");

        advance = 0;
        seed_in = 7'd0; reset_seed = 1; @(posedge clk); #1;
        check("seed 0 loads default 127", uut.state === 7'h7f &&
              symbol_bits === lookahead_ref(7'h7f));
        reset_seed = 0; @(posedge clk); #1;
        check("seed 0 held after deassert", uut.state === 7'h7f &&
              symbol_bits === lookahead_ref(7'h7f));
        ref_state = 7'h7f;
        consume_and_check(3'd1, "advance from seed 0 default");

        advance = 0;
        seed_in = 7'd9;
        reset_seed = 1;
        @(posedge clk);
        #1;
        check("mid-run reload to seed 9", uut.state === 7'd9 &&
              symbol_bits === lookahead_ref(7'd9));
        reset_seed = 0;
        @(posedge clk);
        #1;
        ref_state = 7'd9;
        consume_and_check(3'd2, "advance 2 from reloaded seed 9");

        if (errs == 0)
            $display("PASS prbs7_source");
        else
            $display("FAIL prbs7_source: %0d errors", errs);
        $finish;
    end
endmodule
