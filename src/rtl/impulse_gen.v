`timescale 1ns / 1ps
// 周期冲激干扰: 单计数器 cnt 在 0..period-1 循环, cnt < width 时输出幅度。
// 幅值 ampl 是静态 BRAM 参数, 其缩放(ampl×127/100)是**常量查表**, 不在每样点路径上——
// 只有 enable 标志需要走流水, 与 fade_fir / noise_gen 对齐。
//
// 早先版本把缩放写成 function 里的 ((a*127)+50)/100, 综合器把它推断成一条 ~72 位
// 的 restoring 除法进位链(全设计最差路径 19.909ns / 33 级逻辑, 占满 20ns 周期的余量)。
// 除法是常量除以 100, 但符号分支(a>=0 ? … : …)让它无法化成乘倒数。ampl 只有 8 位,
// 于是直接把映射做成 256 项常量表: 零算术、零附加延迟, 且与原 function 逐位等价
// (impulse_gen_tb 的 golden 不改即可通过)。地址即 ampl 的 8 位原码。
//
// 表值 = 有符号四舍五入(远离零)的 level = sign(a)*floor((|a|*127 + 50)/100), 低 8 位;
// 与 host/siggen/channel.py::_round_div_signed(ampl*127, 100) 同法。
module impulse_gen (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [15:0] period,
    input  wire [7:0]  width,
    input  wire signed [7:0] ampl,
    output reg  signed [7:0] impulse_out
);

    reg [15:0] cnt;
    wire       period_end = (period == 16'd0) || (cnt >= period - 16'd1);
    wire       in_window  = enable && (cnt < {8'd0, width});

    always @(posedge clk or negedge rst_n)
        if (!rst_n) cnt <= 16'd0;
        else        cnt <= period_end ? 16'd0 : cnt + 16'd1;

    reg [7:0] level_rom [0:255];
    initial begin

        level_rom[  0] = 8'h00; level_rom[  1] = 8'h01; level_rom[  2] = 8'h03; level_rom[  3] = 8'h04;
        level_rom[  4] = 8'h05; level_rom[  5] = 8'h06; level_rom[  6] = 8'h08; level_rom[  7] = 8'h09;
        level_rom[  8] = 8'h0A; level_rom[  9] = 8'h0B; level_rom[ 10] = 8'h0D; level_rom[ 11] = 8'h0E;
        level_rom[ 12] = 8'h0F; level_rom[ 13] = 8'h11; level_rom[ 14] = 8'h12; level_rom[ 15] = 8'h13;
        level_rom[ 16] = 8'h14; level_rom[ 17] = 8'h16; level_rom[ 18] = 8'h17; level_rom[ 19] = 8'h18;
        level_rom[ 20] = 8'h19; level_rom[ 21] = 8'h1B; level_rom[ 22] = 8'h1C; level_rom[ 23] = 8'h1D;
        level_rom[ 24] = 8'h1E; level_rom[ 25] = 8'h20; level_rom[ 26] = 8'h21; level_rom[ 27] = 8'h22;
        level_rom[ 28] = 8'h24; level_rom[ 29] = 8'h25; level_rom[ 30] = 8'h26; level_rom[ 31] = 8'h27;
        level_rom[ 32] = 8'h29; level_rom[ 33] = 8'h2A; level_rom[ 34] = 8'h2B; level_rom[ 35] = 8'h2C;
        level_rom[ 36] = 8'h2E; level_rom[ 37] = 8'h2F; level_rom[ 38] = 8'h30; level_rom[ 39] = 8'h32;
        level_rom[ 40] = 8'h33; level_rom[ 41] = 8'h34; level_rom[ 42] = 8'h35; level_rom[ 43] = 8'h37;
        level_rom[ 44] = 8'h38; level_rom[ 45] = 8'h39; level_rom[ 46] = 8'h3A; level_rom[ 47] = 8'h3C;
        level_rom[ 48] = 8'h3D; level_rom[ 49] = 8'h3E; level_rom[ 50] = 8'h40; level_rom[ 51] = 8'h41;
        level_rom[ 52] = 8'h42; level_rom[ 53] = 8'h43; level_rom[ 54] = 8'h45; level_rom[ 55] = 8'h46;
        level_rom[ 56] = 8'h47; level_rom[ 57] = 8'h48; level_rom[ 58] = 8'h4A; level_rom[ 59] = 8'h4B;
        level_rom[ 60] = 8'h4C; level_rom[ 61] = 8'h4D; level_rom[ 62] = 8'h4F; level_rom[ 63] = 8'h50;
        level_rom[ 64] = 8'h51; level_rom[ 65] = 8'h53; level_rom[ 66] = 8'h54; level_rom[ 67] = 8'h55;
        level_rom[ 68] = 8'h56; level_rom[ 69] = 8'h58; level_rom[ 70] = 8'h59; level_rom[ 71] = 8'h5A;
        level_rom[ 72] = 8'h5B; level_rom[ 73] = 8'h5D; level_rom[ 74] = 8'h5E; level_rom[ 75] = 8'h5F;
        level_rom[ 76] = 8'h61; level_rom[ 77] = 8'h62; level_rom[ 78] = 8'h63; level_rom[ 79] = 8'h64;
        level_rom[ 80] = 8'h66; level_rom[ 81] = 8'h67; level_rom[ 82] = 8'h68; level_rom[ 83] = 8'h69;
        level_rom[ 84] = 8'h6B; level_rom[ 85] = 8'h6C; level_rom[ 86] = 8'h6D; level_rom[ 87] = 8'h6E;
        level_rom[ 88] = 8'h70; level_rom[ 89] = 8'h71; level_rom[ 90] = 8'h72; level_rom[ 91] = 8'h74;
        level_rom[ 92] = 8'h75; level_rom[ 93] = 8'h76; level_rom[ 94] = 8'h77; level_rom[ 95] = 8'h79;
        level_rom[ 96] = 8'h7A; level_rom[ 97] = 8'h7B; level_rom[ 98] = 8'h7C; level_rom[ 99] = 8'h7E;
        level_rom[100] = 8'h7F; level_rom[101] = 8'h80; level_rom[102] = 8'h82; level_rom[103] = 8'h83;
        level_rom[104] = 8'h84; level_rom[105] = 8'h85; level_rom[106] = 8'h87; level_rom[107] = 8'h88;
        level_rom[108] = 8'h89; level_rom[109] = 8'h8A; level_rom[110] = 8'h8C; level_rom[111] = 8'h8D;
        level_rom[112] = 8'h8E; level_rom[113] = 8'h90; level_rom[114] = 8'h91; level_rom[115] = 8'h92;
        level_rom[116] = 8'h93; level_rom[117] = 8'h95; level_rom[118] = 8'h96; level_rom[119] = 8'h97;
        level_rom[120] = 8'h98; level_rom[121] = 8'h9A; level_rom[122] = 8'h9B; level_rom[123] = 8'h9C;
        level_rom[124] = 8'h9D; level_rom[125] = 8'h9F; level_rom[126] = 8'hA0; level_rom[127] = 8'hA1;
        level_rom[128] = 8'h5D; level_rom[129] = 8'h5F; level_rom[130] = 8'h60; level_rom[131] = 8'h61;
        level_rom[132] = 8'h63; level_rom[133] = 8'h64; level_rom[134] = 8'h65; level_rom[135] = 8'h66;
        level_rom[136] = 8'h68; level_rom[137] = 8'h69; level_rom[138] = 8'h6A; level_rom[139] = 8'h6B;
        level_rom[140] = 8'h6D; level_rom[141] = 8'h6E; level_rom[142] = 8'h6F; level_rom[143] = 8'h70;
        level_rom[144] = 8'h72; level_rom[145] = 8'h73; level_rom[146] = 8'h74; level_rom[147] = 8'h76;
        level_rom[148] = 8'h77; level_rom[149] = 8'h78; level_rom[150] = 8'h79; level_rom[151] = 8'h7B;
        level_rom[152] = 8'h7C; level_rom[153] = 8'h7D; level_rom[154] = 8'h7E; level_rom[155] = 8'h80;
        level_rom[156] = 8'h81; level_rom[157] = 8'h82; level_rom[158] = 8'h84; level_rom[159] = 8'h85;
        level_rom[160] = 8'h86; level_rom[161] = 8'h87; level_rom[162] = 8'h89; level_rom[163] = 8'h8A;
        level_rom[164] = 8'h8B; level_rom[165] = 8'h8C; level_rom[166] = 8'h8E; level_rom[167] = 8'h8F;
        level_rom[168] = 8'h90; level_rom[169] = 8'h92; level_rom[170] = 8'h93; level_rom[171] = 8'h94;
        level_rom[172] = 8'h95; level_rom[173] = 8'h97; level_rom[174] = 8'h98; level_rom[175] = 8'h99;
        level_rom[176] = 8'h9A; level_rom[177] = 8'h9C; level_rom[178] = 8'h9D; level_rom[179] = 8'h9E;
        level_rom[180] = 8'h9F; level_rom[181] = 8'hA1; level_rom[182] = 8'hA2; level_rom[183] = 8'hA3;
        level_rom[184] = 8'hA5; level_rom[185] = 8'hA6; level_rom[186] = 8'hA7; level_rom[187] = 8'hA8;
        level_rom[188] = 8'hAA; level_rom[189] = 8'hAB; level_rom[190] = 8'hAC; level_rom[191] = 8'hAD;
        level_rom[192] = 8'hAF; level_rom[193] = 8'hB0; level_rom[194] = 8'hB1; level_rom[195] = 8'hB3;
        level_rom[196] = 8'hB4; level_rom[197] = 8'hB5; level_rom[198] = 8'hB6; level_rom[199] = 8'hB8;
        level_rom[200] = 8'hB9; level_rom[201] = 8'hBA; level_rom[202] = 8'hBB; level_rom[203] = 8'hBD;
        level_rom[204] = 8'hBE; level_rom[205] = 8'hBF; level_rom[206] = 8'hC0; level_rom[207] = 8'hC2;
        level_rom[208] = 8'hC3; level_rom[209] = 8'hC4; level_rom[210] = 8'hC6; level_rom[211] = 8'hC7;
        level_rom[212] = 8'hC8; level_rom[213] = 8'hC9; level_rom[214] = 8'hCB; level_rom[215] = 8'hCC;
        level_rom[216] = 8'hCD; level_rom[217] = 8'hCE; level_rom[218] = 8'hD0; level_rom[219] = 8'hD1;
        level_rom[220] = 8'hD2; level_rom[221] = 8'hD4; level_rom[222] = 8'hD5; level_rom[223] = 8'hD6;
        level_rom[224] = 8'hD7; level_rom[225] = 8'hD9; level_rom[226] = 8'hDA; level_rom[227] = 8'hDB;
        level_rom[228] = 8'hDC; level_rom[229] = 8'hDE; level_rom[230] = 8'hDF; level_rom[231] = 8'hE0;
        level_rom[232] = 8'hE2; level_rom[233] = 8'hE3; level_rom[234] = 8'hE4; level_rom[235] = 8'hE5;
        level_rom[236] = 8'hE7; level_rom[237] = 8'hE8; level_rom[238] = 8'hE9; level_rom[239] = 8'hEA;
        level_rom[240] = 8'hEC; level_rom[241] = 8'hED; level_rom[242] = 8'hEE; level_rom[243] = 8'hEF;
        level_rom[244] = 8'hF1; level_rom[245] = 8'hF2; level_rom[246] = 8'hF3; level_rom[247] = 8'hF5;
        level_rom[248] = 8'hF6; level_rom[249] = 8'hF7; level_rom[250] = 8'hF8; level_rom[251] = 8'hFA;
        level_rom[252] = 8'hFB; level_rom[253] = 8'hFC; level_rom[254] = 8'hFD; level_rom[255] = 8'hFF;
    end
    reg signed [7:0] level;
    wire [7:0] level_addr = ampl;   // 地址取 ampl 的 8 位原码: 有符号负值直接当数组下标
                                    // 在仿真里会越界返回 x, 必须先重解释成无符号。
    always @(*) level = level_rom[level_addr];

    reg in_r1, in_r2;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin in_r1 <= 1'b0; in_r2 <= 1'b0; end
        else        begin in_r1 <= in_window; in_r2 <= in_r1; end

    always @(posedge clk or negedge rst_n)
        if (!rst_n) impulse_out <= 8'sd0;
        else        impulse_out <= in_r2 ? level : 8'sd0;
endmodule
