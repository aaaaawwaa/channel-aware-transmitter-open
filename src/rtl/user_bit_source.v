`timescale 1ns / 1ps
// 用户 bit 源: 从双 bank BRAM B 口(32-bit word, 8 位字地址)流式读 raw bitstream。
// - 字节 MSB-first: byte bit7 是流中第一个逻辑 bit, 对应 symbol_bits[5]。
// - 32-bit word 内小端 byte lane: word[7:0]=byte0(流最前), word[31:24]=byte3(流最后)。
// - 内部 64-bit 右对齐窗口(win[k]=bit_ptr+k 处 bit) + 单 word 在途读取(1 拍延迟)。
// - commit_toggle 边沿 / active 下降沿 / loop 回绕 -> 从 active bank bit0 重启。
// - 非 loop 恰好消费最后完整符号 -> done; 剩余不足 -> underflow; loop -> 尾段丢弃回绕。
// - ready = 缓冲 >= advance_bits 且不跨末尾; 调制器在 ready 时才能发符号。
module user_bit_source (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        active,          // 使能与数据源有效(enable && source_user)
    input  wire        loop_en,
    input  wire        commit_toggle,   // 边沿触发重启
    input  wire [12:0] bit_length,      // 总位数 0..4096(字节对齐, SDK 保证)
    input  wire        active_bank,
    input  wire        advance,         // 消费一拍(调制器在 ready 时断言)
    input  wire [2:0]  advance_bits,    // 每次消费位数 1/2/4/6
    output wire [5:0]  symbol_bits,     // [5]=当前位(与 prbs7_source 输出顺序一致)
    output wire        ready,
    output wire        done,
    output wire        underflow,
    output wire [7:0]  b_addr,          // {active_bank, word_in_bank[6:0]}
    output wire        b_en,
    input  wire [31:0] b_rdata
);
    function [7:0] rev8;
        input [7:0] x;
        begin rev8 = {x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]}; end
    endfunction

    reg [12:0] bit_ptr;
    reg [63:0] win;
    reg [6:0]  win_cnt;      // 窗口有效位数 0..64
    reg [7:0]  next_wid;     // 下一个待取 word 索引 0..total_w(可达 128)
    reg        pend_valid;   // 已取回待追加
    reg [31:0] pend_word;    // 已按流序转换
    reg        read_pipe;    // b_rdata 下一拍有效
    reg        done_r, underflow_r;
    reg        commit_d, active_d;

    wire [8:0] byte_len   = bit_length >> 3;                 // 0..512
    wire [7:0] total_w    = (byte_len + 9'd3) >> 2;          // 需读取 word 数 0..128
    wire [12:0] advw      = {10'd0, advance_bits};
    wire        cross_end = (bit_ptr + advw > bit_length);
    wire        wrap_now  = active && loop_en && cross_end;
    wire [6:0]  fetch_wid = (loop_en && (next_wid >= total_w)) ? 7'd0 : next_wid[6:0];
    wire        fetch_ok  = loop_en ? (total_w != 8'd0) : (next_wid < total_w);

    wire need_read = active && !done_r && !underflow_r && !read_pipe && !pend_valid &&
                     (win_cnt <= 6'd32) && fetch_ok;

    // 消费(右移)与追加(FEED32 填入 win_cnt 处)合并
    wire        consuming = advance && !cross_end;
    wire [63:0] win_c     = consuming ? (win >> advance_bits) : win;
    wire [6:0]  win_cnt_c = consuming ? (win_cnt - {4'd0, advance_bits}) : win_cnt;
    wire        appending = pend_valid && (win_cnt_c <= 6'd32);
    reg  [63:0] win_n;
    reg  [6:0]  win_cnt_n;
    always @* begin
        win_n     = win_c;
        win_cnt_n = win_cnt_c;
        if (appending) begin
            win_n[win_cnt_c +: 32] = pend_word;
            win_cnt_n = win_cnt_c + 6'd32;
        end
    end

    wire underflow_cond = active && !loop_en && !done_r && !underflow_r &&
                          cross_end && (bit_ptr < bit_length);

    assign b_addr  = {active_bank, fetch_wid};
    assign b_en    = need_read;
    assign ready   = active && !done_r && !underflow_r && !cross_end &&
                     (win_cnt >= {4'd0, advance_bits});
    assign done    = done_r || (active && !loop_en && (bit_length == 13'd0));
    assign underflow = underflow_r;
    assign symbol_bits = {win[0], win[1], win[2], win[3], win[4], win[5]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_ptr <= 13'd0;
            win <= 64'd0;
            win_cnt <= 7'd0;
            next_wid <= 8'd0;
            pend_valid <= 1'b0;
            pend_word <= 32'd0;
            read_pipe <= 1'b0;
            done_r <= 1'b0;
            underflow_r <= 1'b0;
            commit_d <= 1'b0;
            active_d <= 1'b0;
        end else begin
            commit_d <= commit_toggle;
            active_d <= active;
            // 重启: commit_toggle 边沿 / active 下降沿 / loop 回绕
            if ((commit_toggle != commit_d) || (!active && active_d) || wrap_now) begin
                bit_ptr <= 13'd0;
                win <= 64'd0;
                win_cnt <= 7'd0;
                next_wid <= 8'd0;
                pend_valid <= 1'b0;
                pend_word <= 32'd0;
                read_pipe <= 1'b0;
                done_r <= 1'b0;
                underflow_r <= 1'b0;
            end else if (active) begin
                if (consuming) begin
                    bit_ptr <= bit_ptr + advw;
                    if (!loop_en && (bit_ptr + advw == bit_length))
                        done_r <= 1'b1;
                end
                if (underflow_cond)
                    underflow_r <= 1'b1;
                if (appending) begin
                    pend_valid <= 1'b0;
                    next_wid <= (loop_en && (next_wid == total_w)) ? 8'd0 : next_wid + 8'd1;
                end
                if (read_pipe) begin
                    pend_word <= {rev8(b_rdata[31:24]), rev8(b_rdata[23:16]),
                                  rev8(b_rdata[15:8]), rev8(b_rdata[7:0])};
                    pend_valid <= 1'b1;
                    read_pipe <= 1'b0;
                end
                if (need_read)
                    read_pipe <= 1'b1;
                win <= win_n;
                win_cnt <= win_cnt_n;
            end
        end
    end
endmodule
