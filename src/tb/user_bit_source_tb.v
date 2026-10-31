`timescale 1ns / 1ps
// user_bit_source 单元测试: MSB-first 顺序、跨 word/byte、consume 1/2/4/6、
// done(恰好末尾)/underflow(尾段不足)、loop 回绕、commit_toggle 重启、bank 切换。
// symbol_bits = {win[0..5]} 为 6 位前瞻窗口(与 prbs7_source 一致), 流最前字节 = 字面量最高字节。
module user_bit_source_tb;
    reg clk = 0;
    reg rst_n = 0;
    reg active = 0;
    reg loop_en = 0;
    reg commit_toggle = 0;
    reg [12:0] bit_length = 0;
    reg active_bank = 0;
    reg advance = 0;
    reg [2:0] advance_bits = 1;
    wire [5:0] symbol_bits;
    wire ready, done, underflow;
    wire [7:0] b_addr;
    wire b_en;
    wire [31:0] b_rdata;

    user_bit_source uut (
        .clk(clk), .rst_n(rst_n), .active(active), .loop_en(loop_en),
        .commit_toggle(commit_toggle), .bit_length(bit_length),
        .active_bank(active_bank), .advance(advance), .advance_bits(advance_bits),
        .symbol_bits(symbol_bits), .ready(ready), .done(done), .underflow(underflow),
        .b_addr(b_addr), .b_en(b_en), .b_rdata(b_rdata)
    );

    // ---- BRAM B 口模型: 1 拍读延迟, 双 bank 512 字节, 32-bit word 小端 lane ----
    reg [7:0] bank0 [0:511];
    reg [7:0] bank1 [0:511];
    reg [31:0] b_rdata_q;
    always @(posedge clk) if (b_en) begin
        if (b_addr[7])
            b_rdata_q <= {bank1[{b_addr[6:0], 2'b11}], bank1[{b_addr[6:0], 2'b10}],
                          bank1[{b_addr[6:0], 2'b01}], bank1[{b_addr[6:0], 2'b00}]};
        else
            b_rdata_q <= {bank0[{b_addr[6:0], 2'b11}], bank0[{b_addr[6:0], 2'b10}],
                          bank0[{b_addr[6:0], 2'b01}], bank0[{b_addr[6:0], 2'b00}]};
    end
    assign b_rdata = b_rdata_q;

    always #10 clk = ~clk;   // 50 MHz

    integer errs = 0;

    task chk;
        input string name;
        input ok;
        begin
            if (ok) $display("PASS %0s", name);
            else begin $display("FAIL %0s", name); errs = errs + 1; end
        end
    endtask

    task load_pat;
        input integer bn;
        input [8:0] off;
        input [8:0] nbytes;
        input [511:0] data;   // 最高字节 = 流最前字节(byte0); bank[off+k] = data[((nbytes-1-k)*8) +: 8]
        integer k;
        begin
            for (k = 0; k < nbytes; k = k + 1)
                if (bn == 0) bank0[off + k] = data[((nbytes-1-k)*8) +: 8];
                else         bank1[off + k] = data[((nbytes-1-k)*8) +: 8];
        end
    endtask

    // 等待 ready, 采样当前 lookahead, 单拍 advance
    task emit;
        output [5:0] bits;
        integer w;
        begin
            w = 0;
            while (!ready && w < 400) begin @(posedge clk); w = w + 1; end
            if (w >= 400) begin
                $display("TIMEOUT emit: ready=%b done=%b underflow=%b win_cnt=%0d bit_ptr=%0d",
                         ready, uut.done_r, uut.underflow_r, uut.win_cnt, uut.bit_ptr);
                $finish;
            end
            #1;
            bits = symbol_bits;
            advance = 1'b1;
            @(posedge clk); #1;
            advance = 1'b0;
        end
    endtask

    // 清空双 bank(避免残留)
    task clear_banks;
        integer i;
        begin
            for (i = 0; i < 512; i = i + 1) begin
                bank0[i] = 8'h00;
                bank1[i] = 8'h00;
            end
        end
    endtask

    // 复位源: 拉低 active 一拍, 再拉高
    task restart_src;
        begin
            active = 1'b0;
            @(posedge clk); #1;
            active = 1'b1;
        end
    endtask

    task setup;
        input l;    // loop_en
        input bk;   // active_bank
        begin
            active = 1'b0;
            loop_en = l;
            commit_toggle = 1'b0;
            advance = 1'b0;
            active_bank = bk;
            restart_src;
        end
    endtask

    // ---------------------------------------------------------------
    initial begin
        clear_banks;
        #100 rst_n = 1;
        #40;

        // ==== Test A: MSB-first, consume 1, bit_length=24, 非 loop ====
        // bytes: 0xC0 0x38 0x0E -> 流 bit: 1100 0000 0011 1000 0000 1110
        load_pat(0, 0, 3, 24'hC0_38_0E);
        bit_length = 13'd24;
        advance_bits = 3'd1;
        setup(1'b0, 1'b0);
        begin : tA
            reg [5:0] b;
            integer n;
            reg [5:0] exp [0:23];
            exp[0]=6'h30; exp[1]=6'h20; exp[2]=6'h00; exp[3]=6'h00;
            exp[4]=6'h00; exp[5]=6'h01; exp[6]=6'h03; exp[7]=6'h07;
            exp[8]=6'h0e; exp[9]=6'h1c; exp[10]=6'h38; exp[11]=6'h30;
            exp[12]=6'h20; exp[13]=6'h00; exp[14]=6'h00; exp[15]=6'h01;
            exp[16]=6'h03; exp[17]=6'h07; exp[18]=6'h0e; exp[19]=6'h1c;
            exp[20]=6'h38; exp[21]=6'h30; exp[22]=6'h20; exp[23]=6'h00;
            for (n = 0; n < 24; n = n + 1) begin
                emit(b);
                chk($sformatf("A symbol %0d bits=0x%02x", n, b), b === exp[n]);
            end
            chk("A done after 24 consumes", done === 1'b1);
            chk("A ready false after done", ready === 1'b0);
            advance = 1'b1;
            @(posedge clk); #1;
            advance = 1'b0;
            chk("A advance after done no-op", done === 1'b1 && ready === 1'b0);
        end

        // ==== Test B: consume 6 (QAM64), bit_length=24, 非 loop ====
        bit_length = 13'd24;
        advance_bits = 3'd6;
        setup(1'b0, 1'b0);
        begin : tB
            reg [5:0] b;
            integer n;
            reg [5:0] exp [0:3];
            exp[0]=6'h30; exp[1]=6'h03; exp[2]=6'h20; exp[3]=6'h0e;
            for (n = 0; n < 4; n = n + 1) begin
                emit(b);
                chk($sformatf("B sym%0d=0x%02x exp 0x%02x", n, b, exp[n]), b === exp[n]);
            end
            chk("B done", done === 1'b1);
            chk("B not underflow", underflow === 1'b0);
        end

        // ==== Test C: consume 4 (QAM16), bit_length=16, 恰好末尾 -> done ====
        bit_length = 13'd16;
        advance_bits = 3'd4;
        setup(1'b0, 1'b0);
        begin : tC
            reg [5:0] b;
            integer n;
            reg [5:0] exp [0:3];
            exp[0]=6'h30; exp[1]=6'h00; exp[2]=6'h0e; exp[3]=6'h20;
            for (n = 0; n < 4; n = n + 1) begin
                emit(b);
                chk($sformatf("C sym%0d=0x%02x exp 0x%02x", n, b, exp[n]), b === exp[n]);
            end
            chk("C done exact end", done === 1'b1);
        end

        // ==== Test D: 跨 word 连续流(5 字节, consume 2/QPSK) ====
        // bytes: 0xC0 0x38 0x0E 0x81 0x7F
        // 流 bit: 11000000 00111000 00001110 10000001 01111111
        load_pat(0, 0, 5, 40'hC0_38_0E_81_7F);
        bit_length = 13'd40;
        advance_bits = 3'd2;
        setup(1'b0, 1'b0);
        begin : tD
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
                chk($sformatf("D sym%0d=0x%02x exp 0x%02x", n, b, exp[n]), b === exp[n]);
            end
            chk("D done", done === 1'b1);
        end

        // ==== Test E: 非 loop 尾段不足 -> underflow ====
        // bit_length=16(2 字节), consume 6: sym 48, 3, 然后 rem=4<6 -> underflow
        bit_length = 13'd16;
        advance_bits = 3'd6;
        setup(1'b0, 1'b0);
        begin : tE
            reg [5:0] b;
            emit(b);
            chk("E sym0=48", b === 6'b110000);
            emit(b);
            chk("E sym1=3", b === 6'b000011);
            #40;   // 让 underflow_cond 打拍到 underflow_r
            chk("E underflow set", underflow === 1'b1);
            chk("E done not set", done === 1'b0);
            chk("E ready false", ready === 1'b0);
        end

        // ==== Test F: loop 回绕 ====
        bit_length = 13'd16;
        advance_bits = 3'd6;
        setup(1'b1, 1'b0);   // loop on
        begin : tF
            reg [5:0] b;
            integer n;
            // 每 pass 两个符号: 48, 3; 至少 3 pass
            for (n = 0; n < 6; n = n + 1) begin
                emit(b);
                chk($sformatf("F pass%0d sym%0d=0x%02x", n/2, n%2, b),
                    (n % 2 == 0) ? (b === 6'b110000) : (b === 6'b000011));
            end
            chk("F loop never done", done === 1'b0);
            chk("F loop never underflow", underflow === 1'b0);
        end

        // ==== Test G: commit_toggle 重启 ====
        bit_length = 13'd24;
        advance_bits = 3'd6;
        setup(1'b0, 1'b0);
        begin : tG
            reg [5:0] b;
            emit(b);
            chk("G sym0=48", b === 6'b110000);
            emit(b);
            chk("G sym1=3", b === 6'b000011);
            commit_toggle = ~commit_toggle;   // 重启 -> 从头
            @(posedge clk); #1;
            emit(b);
            chk("G after commit toggle sym0=48", b === 6'b110000);
        end

        // ==== Test H: active 下降沿重启 ====
        setup(1'b0, 1'b0);   // 已重启一次
        begin : tH
            reg [5:0] b;
            emit(b);
            chk("H sym0=48", b === 6'b110000);
            active = 1'b0;
            @(posedge clk); #1;
            chk("H inactive -> not ready", ready === 1'b0);
            active = 1'b1;
            emit(b);
            chk("H re-enable sym0=48", b === 6'b110000);
        end

        // ==== Test I: bank 切换 ====
        // bank1 bytes: 0xAA 0x55 -> 流 bit: 10101010 01010101
        load_pat(1, 0, 2, 16'hAA_55);
        bit_length = 13'd16;
        advance_bits = 3'd2;
        setup(1'b0, 1'b1);   // bank1
        begin : tI
            reg [5:0] b;
            integer n;
            reg [5:0] exp [0:7];
            exp[0]=6'h2a; exp[1]=6'h2a; exp[2]=6'h29; exp[3]=6'h25;
            exp[4]=6'h15; exp[5]=6'h15; exp[6]=6'h14; exp[7]=6'h10;
            for (n = 0; n < 8; n = n + 1) begin
                emit(b);
                chk($sformatf("I bank1 sym%0d=0x%02x exp 0x%02x", n, b, exp[n]), b === exp[n]);
            end
            chk("I done bank1", done === 1'b1);
        end

        // ==== Test J: bit_length=0 -> done ====
        bit_length = 13'd0;
        advance_bits = 3'd1;
        setup(1'b0, 1'b0);
        #40;
        chk("J zero-length done", done === 1'b1);
        chk("J zero-length ready false", ready === 1'b0);

        if (errs == 0) $display("PASS user_bit_source_tb");
        else $display("FAIL user_bit_source_tb: %0d errors", errs);
        $finish;
    end
endmodule
