`timescale 1ns / 1ps
module iq_digital_mod_tb;
    localparam integer SYM_TICKS = 4;
    localparam [31:0] SYM_PHASE_STEP = 32'h4000_0000;
    localparam [2:0] BPSK = 3'd0,
                     QPSK = 3'd1,
                     QAM4 = 3'd2,
                     QAM16 = 3'd3,
                     QAM64 = 3'd4;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg enable = 1'b0;
    reg [2:0] mode = BPSK;
    reg [6:0] prbs_seed = 7'h7f;
    reg source_user = 1'b0;
    reg user_loop = 1'b0;
    reg user_commit_epoch = 1'b0;
    reg [12:0] user_length = 13'd0;
    reg user_active_bank = 1'b0;
    wire [7:0] bram_b_addr;
    wire bram_b_en;
    wire [31:0] bram_b_rdata;
    reg [31:0] bram_b_rdata_q = 32'd0;
    wire user_done, user_underflow;
    wire signed [7:0] i_sample;
    wire signed [7:0] q_sample;
    wire symbol_valid;

    integer errors = 0;
    integer symbol_index = 0;

    reg [15:0] unique_points [0:63];
    integer unique_count;
    reg [6:0] prbs_states [0:126];
    reg signed [7:0] first_bpsk_i [0:15];
    reg signed [7:0] first_bpsk_q [0:15];
    reg signed [7:0] seed_expected_i [0:3];
    reg signed [7:0] seed_expected_q [0:3];

    iq_digital_mod uut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .mode(mode),
        .prbs_seed(prbs_seed),
        .sym_phase_step(SYM_PHASE_STEP),
        .source_user(source_user), .user_loop(user_loop),
        .user_commit_epoch(user_commit_epoch), .user_length(user_length),
        .user_active_bank(user_active_bank),
        .bram_b_addr(bram_b_addr), .bram_b_en(bram_b_en),
        .bram_b_rdata(bram_b_rdata),
        .user_done(user_done), .user_underflow(user_underflow),
        .i_sample(i_sample),
        .q_sample(q_sample),
        .symbol_valid(symbol_valid)
    );

    always #10 clk = ~clk;

    // ---- 用户 BRAM B 模型: 双 bank 512B, 32-bit word 小端 lane, 1 拍读延迟 ----
    reg [7:0] umem [0:1023];
    wire [9:0] ubase = {bram_b_addr[7], 2'b00, bram_b_addr[6:0]};
    always @(posedge clk) begin
        if (bram_b_en)
            bram_b_rdata_q <= {umem[ubase+3], umem[ubase+2], umem[ubase+1], umem[ubase]};
    end
    assign bram_b_rdata = bram_b_rdata_q;

    integer ui;
    initial for (ui = 0; ui < 1024; ui = ui + 1) umem[ui] = 8'd0;

    task load_user_pat;
        input integer bn;
        input [8:0] off;
        input [8:0] nbytes;
        input [511:0] data;   // 最高字节 = 流最前字节(byte0)
        integer k;
        begin
            for (k = 0; k < nbytes; k = k + 1)
                umem[bn*512 + off + k] = data[((nbytes-1-k)*8) +: 8];
        end
    endtask

    task step;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task check;
        input condition;
        input [1023:0] name;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL %0s", name);
                errors = errors + 1;
            end
        end
    endtask

    task check_sample_range;
        begin
            check((^i_sample !== 1'bx) && (^q_sample !== 1'bx),
                  "I/Q samples are known");
            check(($signed(i_sample) >= -127) && ($signed(i_sample) <= 127) &&
                  ($signed(q_sample) >= -127) && ($signed(q_sample) <= 127),
                  "I/Q samples stay in signed 8-bit range");
        end
    endtask

    task check_initial_vector;
        input [2:0] test_mode;
        input integer index;
        input signed [7:0] actual_i;
        input signed [7:0] actual_q;
        begin
            if ((test_mode === BPSK) && (index < 4)) begin
                check(actual_i === -8'sd127,
                      "BPSK initial I vector matches the approved sequence");
                check(actual_q === 8'sd0,
                      "BPSK initial Q vector matches the approved sequence");
            end
            else if (((test_mode === QPSK) || (test_mode === QAM4)) &&
                     (index < 4)) begin
                check(actual_i === -8'sd127,
                      "QPSK/QAM4 initial I vector matches the approved sequence");
                if (index < 3)
                    check(actual_q === -8'sd127,
                          "QPSK/QAM4 initial Q vector has the approved first three values");
                else
                    check(actual_q === 8'sd127,
                          "QPSK/QAM4 initial Q vector has the approved fourth value");
            end
            else if ((test_mode === QAM16) && (index < 4)) begin
                case (index)
                    0: begin
                        check(actual_i === -8'sd42,
                              "QAM16 initial vector 0 I matches the approved value");
                        check(actual_q === -8'sd42,
                              "QAM16 initial vector 0 Q matches the approved value");
                    end
                    1: begin
                        check(actual_i === -8'sd42,
                              "QAM16 initial vector 1 I matches the approved value");
                        check(actual_q === -8'sd126,
                              "QAM16 initial vector 1 Q matches the approved value");
                    end
                    2: begin
                        check(actual_i === 8'sd126,
                              "QAM16 initial vector 2 I matches the approved value");
                        check(actual_q === 8'sd126,
                              "QAM16 initial vector 2 Q matches the approved value");
                    end
                    default: begin
                        check(actual_i === 8'sd42,
                              "QAM16 initial vector 3 I matches the approved value");
                        check(actual_q === 8'sd126,
                              "QAM16 initial vector 3 Q matches the approved value");
                    end
                endcase
            end
            else if ((test_mode === QAM64) && (index < 3)) begin
                case (index)
                    0: begin
                        check(actual_i === -8'sd54,
                              "QAM64 initial vector 0 I matches the approved value");
                        check(actual_q === -8'sd54,
                              "QAM64 initial vector 0 Q matches the approved value");
                    end
                    1: begin
                        check(actual_i === -8'sd126,
                              "QAM64 initial vector 1 I matches the approved value");
                        check(actual_q === 8'sd126,
                              "QAM64 initial vector 1 Q matches the approved value");
                    end
                    default: begin
                        check(actual_i === 8'sd18,
                              "QAM64 initial vector 2 I matches the approved value");
                        check(actual_q === 8'sd126,
                              "QAM64 initial vector 2 Q matches the approved value");
                    end
                endcase
            end
        end
    endtask

    task record_point;
        input signed [7:0] point_i;
        input signed [7:0] point_q;
        integer k;
        reg [15:0] point;
        reg found;
        begin
            point = {point_i, point_q};
            found = 1'b0;
            for (k = 0; k < unique_count; k = k + 1) begin
                if (unique_points[k] === point)
                    found = 1'b1;
            end
            if (!found) begin
                if (unique_count < 64) begin
                    unique_points[unique_count] = point;
                    unique_count = unique_count + 1;
                end
                else begin
                    check(1'b0,
                          "unique constellation point storage capacity exceeded");
                end
            end
        end
    endtask

    task run_mode;
        input [2:0] test_mode;
        input integer expected_points;
        integer n;
        integer hold;
        reg signed [7:0] captured_i;
        reg signed [7:0] captured_q;
        begin
            mode = test_mode;
            enable = 1'b0;
            step;
            step;
            check(uut.u_prbs.state === 7'h7f, "disable restores PRBS seed");

            unique_count = 0;
            symbol_index = 0;
            enable = 1'b1;

            for (n = 0; n < 512; n = n + 1) begin
                step;
                check(symbol_valid === 1'b1, "symbol_valid at symbol boundary");
                check_sample_range;

                captured_i = i_sample;
                captured_q = q_sample;
                record_point(captured_i, captured_q);

                if (test_mode === BPSK)
                    check(captured_q === 8'sd0, "BPSK Q sample is zero");

                if ((test_mode === BPSK) && (n < 16)) begin
                    first_bpsk_i[n] = captured_i;
                    first_bpsk_q[n] = captured_q;
                end

                if (symbol_index < 127) begin
                    $display("GOLD mode=%0d index=%0d i=%0d q=%0d",
                             test_mode, symbol_index,
                             $signed(i_sample), $signed(q_sample));
                    check_initial_vector(test_mode, symbol_index,
                                         captured_i, captured_q);
                    symbol_index = symbol_index + 1;
                end

                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0,
                          "symbol_valid low during symbol hold");
                    check(i_sample === captured_i,
                          "I sample stable during symbol hold");
                    check(q_sample === captured_q,
                          "Q sample stable during symbol hold");
                    check_sample_range;
                end
            end

            check(unique_count == expected_points,
                  "mode has the expected unique constellation point count");
        end
    endtask

    task check_mode_change_at_boundary;
        integer hold;
        reg signed [7:0] bpsk_i;
        reg signed [7:0] bpsk_q;
        begin
            mode = BPSK;
            enable = 1'b0;
            step;
            step;
            enable = 1'b1;

            step;
            check(symbol_valid === 1'b1,
                  "mode-change test starts on a BPSK boundary");
            bpsk_i = i_sample;
            bpsk_q = q_sample;
            check(bpsk_q === 8'sd0,
                  "mode-change test starts with BPSK Q zero");

            mode = QPSK;
            for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                step;
                check(symbol_valid === 1'b0,
                      "mode change does not create an early boundary");
                check(i_sample === bpsk_i,
                      "BPSK I remains stable after mid-symbol mode change");
                check(q_sample === bpsk_q,
                      "BPSK Q remains stable after mid-symbol mode change");
            end

            step;
            check(symbol_valid === 1'b1,
                  "new mode takes effect at the next symbol boundary");
            check(((i_sample === 8'sd127) || (i_sample === -8'sd127)) &&
                  ((q_sample === 8'sd127) || (q_sample === -8'sd127)),
                  "next boundary uses a QPSK constellation point");

            enable = 1'b0;
            step;
            step;
        end
    endtask

    task check_bpsk_prbs_period;
        integer n;
        integer k;
        integer state_count;
        reg found;
        begin
            mode = BPSK;
            enable = 1'b0;
            step;
            step;
            enable = 1'b1;
            state_count = 0;

            for (n = 0; n < 127; n = n + 1) begin
                step;
                check(symbol_valid === 1'b1,
                      "BPSK PRBS period symbol boundary");
                check((^uut.u_prbs.state) !== 1'bx,
                      "PRBS period state is known");
                if ((^uut.u_prbs.state) !== 1'bx) begin
                    check(uut.u_prbs.state != 7'h00,
                          "PRBS period state is never zero");
                    if (n < 126)
                        check(uut.u_prbs.state != 7'h7f,
                              "PRBS seed does not return before 127 symbols");
                    if (n == 126)
                        check(uut.u_prbs.state == 7'h7f,
                              "PRBS seed returns after 127 BPSK symbols");
                end

                found = 1'b0;
                for (k = 0; k < state_count; k = k + 1) begin
                    if (prbs_states[k] === uut.u_prbs.state)
                        found = 1'b1;
                end
                if (!found) begin
                    prbs_states[state_count] = uut.u_prbs.state;
                    state_count = state_count + 1;
                end

                repeat (SYM_TICKS - 1) step;
            end
            check(state_count == 127,
                  "PRBS period contains 127 unique nonzero states");

            enable = 1'b0;
            step;
            step;
        end
    endtask

    task check_restart;
        integer n;
        integer hold;
        begin
            mode = BPSK;
            enable = 1'b0;
            step;
            step;
            check(uut.u_prbs.state === 7'h7f,
                  "restart begins from the PRBS seed after two disable cycles");

            enable = 1'b1;
            for (n = 0; n < 16; n = n + 1) begin
                step;
                check(symbol_valid === 1'b1,
                      "re-enable produces each saved BPSK symbol boundary");
                check(i_sample === first_bpsk_i[n],
                      "re-enable repeats the saved BPSK I sequence");
                check(q_sample === first_bpsk_q[n],
                      "re-enable repeats the saved BPSK Q sequence");
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0,
                          "re-enable BPSK hold has no extra valid pulse");
                    check(i_sample === first_bpsk_i[n],
                          "re-enable BPSK I remains stable during symbol hold");
                    check(q_sample === first_bpsk_q[n],
                          "re-enable BPSK Q remains stable during symbol hold");
                end
            end

            enable = 1'b0;
            step;
            step;
        end
    endtask

    task check_invalid_mode;
        input [2:0] invalid_mode;
        integer n;
        integer hold;
        begin
            mode = invalid_mode;
            enable = 1'b0;
            step;
            step;
            check(uut.u_prbs.state === 7'h7f,
                  "invalid mode starts from the PRBS seed");

            enable = 1'b1;
            for (n = 0; n < 2; n = n + 1) begin
                step;
                check(symbol_valid === 1'b1,
                      "invalid mode reaches the symbol boundary");
                check(i_sample === 8'sd0,
                      "invalid mode drives I to zero");
                check(q_sample === 8'sd0,
                      "invalid mode drives Q to zero");
                check(uut.u_prbs.state === 7'h7f,
                      "invalid mode keeps PRBS at the seed");
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0,
                          "invalid mode hold has no extra valid pulse");
                    check(i_sample === 8'sd0,
                          "invalid mode hold keeps I at zero");
                    check(q_sample === 8'sd0,
                          "invalid mode hold keeps Q at zero");
                    check(uut.u_prbs.state === 7'h7f,
                          "invalid mode hold keeps PRBS at the seed");
                end
            end

            enable = 1'b0;
            step;
            step;
        end
    endtask

    task check_seed_mode;
        input [2:0] test_mode;
        input [6:0] seed;
        integer n;
        integer hold;
        begin
            mode = test_mode;
            prbs_seed = seed;
            enable = 1'b0;
            step;
            step;
            check(uut.u_prbs.state === seed,
                  "seed mode holds configured PRBS seed while disabled");
            case ({seed, test_mode})
                {7'd5, QPSK}, {7'd5, QAM4}: begin
                    seed_expected_i[0]= 8'sd127; seed_expected_q[0]= 8'sd127;
                    seed_expected_i[1]= 8'sd127; seed_expected_q[1]= 8'sd127;
                    seed_expected_i[2]=-8'sd127; seed_expected_q[2]= 8'sd127;
                    seed_expected_i[3]=-8'sd127; seed_expected_q[3]= 8'sd127;
                end
                {7'd5, QAM16}: begin
                    seed_expected_i[0]= 8'sd126; seed_expected_q[0]= 8'sd126;
                    seed_expected_i[1]=-8'sd126; seed_expected_q[1]=-8'sd126;
                    seed_expected_i[2]= 8'sd126; seed_expected_q[2]=-8'sd42;
                    seed_expected_i[3]=-8'sd42;  seed_expected_q[3]= 8'sd126;
                end
                {7'd5, QAM64}: begin
                    seed_expected_i[0]= 8'sd126; seed_expected_q[0]= 8'sd18;
                    seed_expected_i[1]=-8'sd126; seed_expected_q[1]= 8'sd54;
                    seed_expected_i[2]=-8'sd18;  seed_expected_q[2]= 8'sd18;
                    seed_expected_i[3]= 8'sd90;  seed_expected_q[3]= 8'sd54;
                end
                {7'd63, QPSK}, {7'd63, QAM4}: begin
                    seed_expected_i[0]= 8'sd127; seed_expected_q[0]=-8'sd127;
                    seed_expected_i[1]=-8'sd127; seed_expected_q[1]=-8'sd127;
                    seed_expected_i[2]=-8'sd127; seed_expected_q[2]=-8'sd127;
                    seed_expected_i[3]=-8'sd127; seed_expected_q[3]=-8'sd127;
                end
                {7'd63, QAM16}: begin
                    seed_expected_i[0]= 8'sd42;  seed_expected_q[0]=-8'sd42;
                    seed_expected_i[1]=-8'sd42;  seed_expected_q[1]=-8'sd42;
                    seed_expected_i[2]= 8'sd126; seed_expected_q[2]= 8'sd126;
                    seed_expected_i[3]= 8'sd126; seed_expected_q[3]=-8'sd126;
                end
                {7'd63, QAM64}: begin
                    seed_expected_i[0]= 8'sd54;  seed_expected_q[0]=-8'sd54;
                    seed_expected_i[1]=-8'sd18;  seed_expected_q[1]= 8'sd126;
                    seed_expected_i[2]= 8'sd90;  seed_expected_q[2]= 8'sd126;
                    seed_expected_i[3]= 8'sd90;  seed_expected_q[3]=-8'sd126;
                end
                default: begin
                    seed_expected_i[0]=8'sd0; seed_expected_q[0]=8'sd0;
                    seed_expected_i[1]=8'sd0; seed_expected_q[1]=8'sd0;
                    seed_expected_i[2]=8'sd0; seed_expected_q[2]=8'sd0;
                    seed_expected_i[3]=8'sd0; seed_expected_q[3]=8'sd0;
                end
            endcase
            enable = 1'b1;
            for (n = 0; n < 4; n = n + 1) begin
                step;
                check(symbol_valid === 1'b1, "seed mode symbol boundary");
                check(i_sample === seed_expected_i[n] &&
                      q_sample === seed_expected_q[n],
                      "seed mode symbol vector");
                check_sample_range;
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1)
                    step;
            end
            enable = 1'b0;
            step;
            step;
            prbs_seed = 7'h7f;
        end
    endtask

    // 用户数据路径集成测试: 从 BRAM B 双 bank 流式读 raw bits, 经 user_bit_source
    // 喂入 mapper, 验证 QPSK 精确序列 / loop 回绕 / QAM64 尾段 user_underflow stall。
    task check_user_qpsk;
        integer n;
        integer hold;
        reg signed [7:0] exp_i [0:7];
        reg signed [7:0] exp_q [0:7];
        begin
            // 2 bytes 0xB2 0x71 -> 8 个 QPSK 符号(每符号 2 bit: [I=sym_bits[5], Q=sym_bits[4]])
            load_user_pat(0, 0, 2, 16'hB271);
            mode = QPSK;
            source_user = 1'b1;
            user_loop = 1'b0;
            user_commit_epoch = 1'b0;
            user_active_bank = 1'b0;
            user_length = 13'd16;
            enable = 1'b0;
            step;
            step;
            enable = 1'b1;

            exp_i[0]=-8'sd127; exp_q[0]= 8'sd127;
            exp_i[1]=-8'sd127; exp_q[1]=-8'sd127;
            exp_i[2]= 8'sd127; exp_q[2]= 8'sd127;
            exp_i[3]=-8'sd127; exp_q[3]= 8'sd127;
            exp_i[4]= 8'sd127; exp_q[4]=-8'sd127;
            exp_i[5]=-8'sd127; exp_q[5]=-8'sd127;
            exp_i[6]= 8'sd127; exp_q[6]= 8'sd127;
            exp_i[7]= 8'sd127; exp_q[7]=-8'sd127;

            for (n = 0; n < 8; n = n + 1) begin
                hold = 0;
                while (symbol_valid !== 1'b1 && hold < 1000) begin
                    step;
                    hold = hold + 1;
                end
                check(hold < 1000, "user QPSK boundary");
                check(i_sample === exp_i[n] && q_sample === exp_q[n],
                      "user QPSK symbol vector");
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0, "user QPSK hold no valid");
                end
            end
            step;
            check(user_done === 1'b1, "user QPSK user_done at exact end");
            check(user_underflow === 1'b0, "user QPSK no underflow");

            enable = 1'b0;
            step;
            step;
            source_user = 1'b0;
            user_length = 13'd0;
        end
    endtask

    task check_user_qpsk_loop;
        integer n;
        integer hold;
        reg signed [7:0] exp_i [0:7];
        reg signed [7:0] exp_q [0:7];
        begin
            load_user_pat(0, 0, 2, 16'hB271);
            mode = QPSK;
            source_user = 1'b1;
            user_loop = 1'b1;
            user_commit_epoch = 1'b0;
            user_active_bank = 1'b0;
            user_length = 13'd16;
            enable = 1'b0;
            step;
            step;
            enable = 1'b1;

            exp_i[0]=-8'sd127; exp_q[0]= 8'sd127;
            exp_i[1]=-8'sd127; exp_q[1]=-8'sd127;
            exp_i[2]= 8'sd127; exp_q[2]= 8'sd127;
            exp_i[3]=-8'sd127; exp_q[3]= 8'sd127;
            exp_i[4]= 8'sd127; exp_q[4]=-8'sd127;
            exp_i[5]=-8'sd127; exp_q[5]=-8'sd127;
            exp_i[6]= 8'sd127; exp_q[6]= 8'sd127;
            exp_i[7]= 8'sd127; exp_q[7]=-8'sd127;

            // 两轮回绕: 16 符号 = sym0..7 重复两次; loop 模式不置 user_done/user_underflow
            for (n = 0; n < 16; n = n + 1) begin
                hold = 0;
                while (symbol_valid !== 1'b1 && hold < 2000) begin
                    step;
                    hold = hold + 1;
                end
                check(hold < 2000, "user loop boundary");
                check(i_sample === exp_i[n % 8] && q_sample === exp_q[n % 8],
                      "user loop symbol repeat");
                check(user_done === 1'b0, "user loop never user_done");
                check(user_underflow === 1'b0, "user loop never underflow");
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0, "user loop hold no valid");
                end
            end

            enable = 1'b0;
            step;
            step;
            source_user = 1'b0;
            user_loop = 1'b0;
            user_length = 13'd0;
        end
    endtask

    task check_user_qam64_underflow;
        integer n;
        integer hold;
        reg signed [7:0] exp_i [0:1];
        reg signed [7:0] exp_q [0:1];
        begin
            // 2 bytes 16 bit, QAM64 consume 6: 2 个完整符号 + 尾段 4 bit -> user_underflow
            load_user_pat(0, 0, 2, 16'hB271);
            mode = QAM64;
            source_user = 1'b1;
            user_loop = 1'b0;
            user_commit_epoch = 1'b0;
            user_active_bank = 1'b0;
            user_length = 13'd16;
            enable = 1'b0;
            step;
            step;
            enable = 1'b1;

            exp_i[0]=-8'sd90;  exp_q[0]=-8'sd126;
            exp_i[1]=-8'sd126; exp_q[1]=-8'sd54;

            for (n = 0; n < 2; n = n + 1) begin
                hold = 0;
                while (symbol_valid !== 1'b1 && hold < 1000) begin
                    step;
                    hold = hold + 1;
                end
                check(hold < 1000, "user QAM64 boundary");
                check(i_sample === exp_i[n] && q_sample === exp_q[n],
                      "user QAM64 symbol vector");
                for (hold = 0; hold < SYM_TICKS - 1; hold = hold + 1) begin
                    step;
                    check(symbol_valid === 1'b0, "user QAM64 hold no valid");
                end
            end

            // 尾段不足 -> 调制器 stall(不再发符号), user_underflow 置位, user_done 不置
            hold = 0;
            while (user_underflow !== 1'b1 && hold < 1000) begin
                step;
                check(symbol_valid === 1'b0, "user underflow no further symbol");
                hold = hold + 1;
            end
            check(hold < 1000, "user underflow set");
            check(user_done === 1'b0, "user underflow no done");

            enable = 1'b0;
            step;
            step;
            source_user = 1'b0;
            user_length = 13'd0;
        end
    endtask

    initial begin
        step;
        step;
        rst_n = 1'b1;

        run_mode(BPSK, 2);
        run_mode(QPSK, 4);
        run_mode(QAM4, 4);
        run_mode(QAM16, 16);
        run_mode(QAM64, 64);

        check_mode_change_at_boundary;
        check_bpsk_prbs_period;
        check_restart;
        check_invalid_mode(3'd5);
        check_invalid_mode(3'd6);
        check_invalid_mode(3'd7);

        check_seed_mode(QPSK, 7'd5);
        check_seed_mode(QAM16, 7'd5);
        check_seed_mode(QAM64, 7'd5);
        check_seed_mode(QPSK, 7'd63);

        check_user_qpsk;
        check_user_qpsk_loop;
        check_user_qam64_underflow;

        if (errors == 0) begin
            $display("PASS iq_digital_mod");
            $finish;
        end
        else begin
            $display("FAIL iq_digital_mod: %0d errors", errors);
            $fatal(1);
        end
    end
endmodule
