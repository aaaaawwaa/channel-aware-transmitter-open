`timescale 1ns / 1ps
// Board-level wrapper: LCD pixel path plus the DDR framebuffer read master.
//
// Two clock domains, and the seam between them is deliberate:
//   * the pixel path runs on an MMCM output (33.333 MHz) fed by the board's own
//     50 MHz oscillator on Y9;
//   * the framebuffer reader runs on PS FCLK_CLK0 and reaches DDR through
//     S_AXI_HP0.
//
// The pixel clock deliberately does NOT come from PS FCLK_CLK0. PYNQ's Overlay()
// does not apply the clock settings in the .hwh -- measured on the board, a design
// declaring 50 MHz runs with FCLK0 at 75 MHz -- so anything timed off FCLK0 runs at
// whatever the image's boot default happens to be, and the panel would silently get
// the wrong pixel rate. Y9 is a fixed 50 MHz oscillator and cannot move.
// 50 MHz * 20 / 30 = 33.333 MHz, which is the rate the vendor's TFT example uses.
//
// `fb_ctrl[0]` selects the pixel source: 0 shows the built-in colour bars, 1 shows the
// DDR framebuffer at `fb_base`. `fb_base` must be 2048-byte aligned: the reader
// walks a 2048-byte row pitch so that every AXI INCR burst stays inside one 4 KB
// window (see fb_axi_reader).
//
// `fb_ctrl[1]` drives `tp_rst`, the GT911 touch controller's reset (PL pin R15). It
// lives in the same register as the source select because software writes both in one
// word and the touch controller is reset-held (this bit's reset value is 0) until
// software releases it -- which is exactly the controlled edge the GT911 wants: it
// samples its I2C address-select pin when reset is released.
//
// The m_axi_* ports carry X_INTERFACE_INFO attributes so the block design can treat
// them as an AXI4 master interface; without them a module reference exposes only
// scalar pins and cannot reach S_AXI_HP0.
module lcd_overlay_top (
    input  wire        clk50m,

    // ---- AXI clock domain (PS FCLK_CLK0) ----
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF M_AXI, ASSOCIATED_RESET aresetn" *)
    input  wire        aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input  wire        aresetn,
    input  wire [31:0] fb_base,
    input  wire [1:0]  fb_ctrl,      // [0] 帧缓冲使能, [1] 触摸复位(TP_RST)
    output wire        tp_rst,

    // ---- self-check taps, read back over a debug GPIO (read-only) ----
    output wire [31:0] dbg0,
    output wire [31:0] dbg1,
    output wire [31:0] dbg2,
    output wire [31:0] dbg3,

    // ---- AXI4 read master to DDR (PS S_AXI_HP0) ----
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXI, PROTOCOL AXI4, DATA_WIDTH 64, ADDR_WIDTH 32, READ_WRITE_MODE READ_ONLY, HAS_BURST 1, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, SUPPORTS_NARROW_BURST 0, NUM_READ_OUTSTANDING 2, MAX_BURST_LENGTH 256" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARADDR" *)  output wire [31:0] m_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLEN" *)   output wire [7:0]  m_axi_arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARSIZE" *)  output wire [2:0]  m_axi_arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARBURST" *) output wire [1:0]  m_axi_arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARID" *)    output wire [5:0]  m_axi_arid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARVALID" *) output wire        m_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARREADY" *) input  wire        m_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RDATA" *)   input  wire [63:0] m_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RID" *)     input  wire [5:0]  m_axi_rid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RRESP" *)   input  wire [1:0]  m_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RLAST" *)   input  wire        m_axi_rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RVALID" *)  input  wire        m_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RREADY" *)  output wire        m_axi_rready,

    // ---- LCD ----
    output wire        lcd_pclk,
    output wire        lcd_de,
    output wire        lcd_hs,
    output wire        lcd_vs,
    output wire [15:0] lcd_rgb,
    output wire        lcd_bl
);
    wire pclk_raw, pclk, clkfb_raw, clkfb, locked;

    MMCME2_BASE #(
        .BANDWIDTH          ("OPTIMIZED"),
        .CLKFBOUT_MULT_F    (20.0),    // VCO = 50 * 20 = 1000 MHz
        .CLKFBOUT_PHASE     (0.0),
        .CLKIN1_PERIOD      (20.0),
        .CLKOUT0_DIVIDE_F   (30.0),    // 1000 / 30 = 33.333 MHz
        .CLKOUT0_DUTY_CYCLE (0.5),
        .CLKOUT0_PHASE      (0.0),
        .DIVCLK_DIVIDE      (1),
        .REF_JITTER1        (0.0),
        .STARTUP_WAIT       ("FALSE")
    ) mmcm_i (
        .CLKOUT0  (pclk_raw),
        .CLKFBOUT (clkfb_raw),
        .CLKFBIN  (clkfb),
        .CLKIN1   (clk50m),
        .LOCKED   (locked),
        .PWRDWN   (1'b0),
        .RST      (1'b0)
    );

    BUFG bufg_pclk (.I(pclk_raw), .O(pclk));
    BUFG bufg_fb   (.I(clkfb_raw), .O(clkfb));

    // lcd_fb's reader reset is active high; the PS gives us an active-low one.
    wire areset = ~aresetn;

    lcd_fb #(
        .AXI_ADDR_WIDTH (32),
        .AXI_ID_WIDTH   (6),
        .LINE_BEATS     (200),
        .STRIDE_BYTES   (2048),
        .LINES          (480),
        .BUFS           (3)
    ) u_fb (
        .pclk         (pclk),
        .pll_locked   (locked),
        .fb_en        (fb_ctrl[0]),
        .lcd_pclk     (lcd_pclk),
        .lcd_de       (lcd_de),
        .lcd_hs       (lcd_hs),
        .lcd_vs       (lcd_vs),
        .lcd_rgb      (lcd_rgb),
        .lcd_bl       (lcd_bl),
        .aclk         (aclk),
        .areset       (areset),
        .fb_base      (fb_base),
        .dbg0         (dbg0),
        .dbg1         (dbg1),
        .dbg2         (dbg2),
        .dbg3         (dbg3),
        .m_axi_araddr (m_axi_araddr),
        .m_axi_arlen  (m_axi_arlen),
        .m_axi_arsize (m_axi_arsize),
        .m_axi_arburst(m_axi_arburst),
        .m_axi_arid   (m_axi_arid),
        .m_axi_arvalid(m_axi_arvalid),
        .m_axi_arready(m_axi_arready),
        .m_axi_rdata  (m_axi_rdata),
        .m_axi_rid    (m_axi_rid),
        .m_axi_rresp  (m_axi_rresp),
        .m_axi_rlast  (m_axi_rlast),
        .m_axi_rvalid (m_axi_rvalid),
        .m_axi_rready (m_axi_rready)
    );
    // 触摸复位: 同一个控制字的 bit1, 直接引到 PL 脚 R15(见 constraints/led_btn.xdc)。
    // 复位值 0 = 器件被按在复位里, 软件放开才活 —— 这正是 GT911 要的那个复位沿。
    assign tp_rst = fb_ctrl[1];
endmodule
