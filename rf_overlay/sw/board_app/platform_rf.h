/* platform_rf.h -- the single source of truth for addresses, pin order and the
 * handful of shapes the vendor driver expects.  Nothing else in board_app may
 * hardcode an address.
 */
#ifndef PLATFORM_RF_H_
#define PLATFORM_RF_H_

#include <stdint.h>

/* ---- PL apertures.  Taken from the merged host_bd .hwh (2026-09-21);
 * addresses move when the BD changes, so re-read the .hwh after every rebuild.
 *
 * These five are pinned by scripts/create_bd.tcl's bd_addr_map, not by
 * assign_bd_address, which is heuristic (adding the two RF BRAMs once relocated
 * an AWG peripheral by 32 MiB with no warning).  Every one of them moved in the
 * merge: the standalone RF overlay had 0x40000000 / 0x40001000 / 0x41200000 /
 * 0x44000000 / 0x42000000, and all five of those collide with an AWG peripheral
 * in the merged design.  Do not copy the old numbers back. */
#define RF_AXI_CTRL_BASE   0x43000000u   /* axi_ctrl_0       */
#define RF_PL_SPI_BASE     0x43010000u   /* pl_spi_0         */
#define RF_GPIO_CTRL_BASE  0x43020000u   /* axi_gpio_ctrl    */
/* The two sample buffers.  Both appeared in the 2026-09-19 rebuild and were
 * repinned by the merge.  TX is PS-written, RX is PL-written. */
#define RF_BRAM_TX_BASE    0x43030000u   /* axi_bram_ctrl_tx, PS writes it   */
#define RF_BRAM_RX_BASE    0x43040000u   /* axi_bram_ctrl_rx, PL writes it   */

/* Every register access is an ABSOLUTE address, composed here.  It has to be
 * absolute rather than "base plus offset" passed around separately: three
 * different apertures each have a register at offset 0x00 (IDELAY_EN, SPI_CTRL,
 * GPIO_DATA), so a bare offset does not identify a register.  On the board this
 * lets one mmap cache serve all three apertures, exactly like spi_probe.py's
 * Mmio does; in the host model it lets fake_pl.c dispatch on one integer. */
#define RF_CTRL_A(o)   (RF_AXI_CTRL_BASE  + (o))
#define RF_SPI_A(o)    (RF_PL_SPI_BASE    + (o))
#define RF_GPIO_A(o)   (RF_GPIO_CTRL_BASE + (o))

/* axi_ctrl_0 register offsets (word addresses) */
#define RF_REG_IDELAY_EN   0x00u
#define RF_REG_IDELAY_TAP  0x04u
#define RF_REG_PHASE_INC   0x08u
#define RF_REG_PHY_MODE    0x0Cu
#define RF_REG_STATUS      0x10u   /* read-only, see the plan's bit table */
#define RF_REG_WAVE_SEL    0x28u   /* reg10[0]: 0 = sine, 1 = square.  0x28 not
                                    * 0x14 because reg10 was the free one; see
                                    * axi_ctrl_v1_0_S00_AXI.v's assign. */

/* Sample-path registers, added by the 2026-09-19 rebuild.  reg7 and reg13 are
 * read-only and have no write arm in the RTL: writing them stores nothing that
 * can be read back. */
#define RF_REG_SRC_CFG     0x14u   /* reg5: [0] src_sel, [1] smp_wrap, [2] smp_commit */
#define RF_REG_SMP_LEN     0x18u   /* reg6: [12:0] smp_len (samples) */
#define RF_REG_SMP_POS     0x1Cu   /* reg7: RO, [12:0] the reader's address */
#define RF_REG_CAP_CFG     0x24u   /* reg9: [0] cap_arm, [1] cap_cont, [2] cap_commit */
#define RF_REG_CAP_RATIO   0x2Cu   /* reg11: [2:0] decimation select 0..4 */
#define RF_REG_CAP_LEN     0x30u   /* reg12: [12:0] rx_len in DECIMATED samples */
#define RF_REG_CAP_STATUS  0x34u   /* reg13: RO, [13] done, [12:0] pos */

#define RF_SRC_SEL         (1u << 0)
#define RF_SMP_WRAP        (1u << 1)
#define RF_SMP_COMMIT      (1u << 2)

#define RF_CAP_ARM         (1u << 0)
#define RF_CAP_CONT        (1u << 1)
#define RF_CAP_COMMIT      (1u << 2)

#define RF_CAP_DONE(w)     (((w) >> 13) & 0x1u)
#define RF_CAP_POS(w)      ((w) & 0x1FFFu)

/* ---- The sample buffers.  Each is a 16 KiB window: 4096 words of 32 bits,
 * one I/Q sample per word, I in [15:0] and Q in [31:16], each sign-extended
 * from 12 bits.  Byte addressed, matching the AXI BRAM controller's own port.
 *
 * The word format is a contract, not a coincidence: sign extension is what
 * makes a captured -1 read as 0xFFFF.  Zero padding would read 0x0FFF -- a
 * large positive number where a small negative one belongs, in every negative
 * sample, with nothing anywhere reporting an error (criterion C10). */
#define RF_BRAM_WORDS      4096u
#define RF_BRAM_BYTES      (RF_BRAM_WORDS * 4u)

/* Pack / unpack one sample.  The 12-bit field is truncated on the way in and
 * sign-extended on the way out, so the round trip is exact for any value a
 * 12-bit converter can carry. */
static inline uint32_t rf_iq_pack(int i, int q)
{
	return ((uint32_t)(q & 0xFFF) << 16) | (uint32_t)(i & 0xFFF);
}

static inline int rf_iq_i(uint32_t w)
{
	int v = (int)(w & 0xFFFu);
	return (v & 0x800) ? v - 0x1000 : v;
}

static inline int rf_iq_q(uint32_t w)
{
	int v = (int)((w >> 16) & 0xFFFu);
	return (v & 0x800) ? v - 0x1000 : v;
}

/* pl_spi_master register offsets (word addresses) */
#define RF_SPI_CTRL        0x00u   /* [7:0] N_TX, [15:8] N_RX, [16] START */
#define RF_SPI_INSTR       0x04u
#define RF_SPI_TXD0        0x08u
#define RF_SPI_TXD1        0x0Cu
#define RF_SPI_RXD0        0x10u
#define RF_SPI_RXD1        0x14u
#define RF_SPI_STATUS      0x18u   /* [0] BUSY, [1] DONE (sticky) */
#define RF_SPI_DIV         0x1Cu   /* SCLK half-period in aclk cycles */

/* axi_gpio_ctrl (9 outputs, all output).  Bit order matches the BD and also
 * matches the reference main.c's EMIO numbering, so pin number == bit number. */
#define RF_GPIO_DATA       0x00u
#define RF_GPIO_TRI        0x04u

#define RF_GPIO_TXNRX      0
#define RF_GPIO_ENABLE     1
#define RF_GPIO_RESETB     2
#define RF_GPIO_SYNC       3
#define RF_GPIO_EN_AGC     4
#define RF_GPIO_CTL0       5
#define RF_GPIO_CTL1       6
#define RF_GPIO_CTL2       7
#define RF_GPIO_CTL3       8
#define RF_GPIO_COUNT      9

/* SCLK half-period in 50 MHz aclk cycles.  10 -> 2.5 MHz SCLK, the value the
 * already-passing Python probe uses (rf_overlay/sw/spi_probe.py). */
#define RF_SPI_DIV_DEFAULT 10u

/* ---- PL STATUS word decode (offset RF_REG_STATUS) ---- */
#define RF_STATUS_CLK_SEEN(w)    (((w) >>  0) & 0x1u)
#define RF_STATUS_WIN_DONE(w)    (((w) >>  1) & 0x1u)
#define RF_STATUS_RSO(w)         (((w) >>  2) & 0x1u)
#define RF_STATUS_WIN_ACTIVE(w)  (((w) >>  3) & 0x1u)
#define RF_STATUS_WIN_SEQ(w)     (((w) >>  4) & 0xFu)
#define RF_STATUS_WIN_CYCLES(w)  (((w) >>  8) & 0xFFFFu)
#define RF_STATUS_WIN_LOG2(w)    (((w) >> 24) & 0xFu)

/* f_FCLK0 = 50 MHz, window = 2^WIN_LOG2 cycles -> f_DATA_CLK in Hz.
 *
 * ⚠ This is only correct while the true edge count fits in 16 bits.  At or
 * above 100 MHz of DATA_CLK over the L=15 window it saturates and WRAPS, and
 * the low member it returns is 100 MHz too low -- a 120 MHz clock reads
 * "20.000 MHz".  That is a real, already-observed misreading, so anything that
 * judges a criterion on the rate must use rf_f_data_clk_resolve() and deal
 * with the alias family rather than calling this macro.  It remains here for
 * the pre-init negative check, where the count is near zero by construction. */
#define RF_STATUS_F_DATA_CLK_HZ(w) \
	(((uint64_t)RF_STATUS_WIN_CYCLES(w) * 50000000ull) >> RF_STATUS_WIN_LOG2(w))

/* Alias-aware resolution of the same window.  See platform_rf.c for why the
 * count is ambiguous and what expectation the choice depends on. */
uint64_t rf_f_data_clk_resolve(uint32_t status, uint64_t expect_hz, int *n_used);
uint64_t rf_f_data_clk_alias_step(uint32_t status);

/* ---- axi_ctrl_0 accessors.  These are ours, not the vendor driver's; they live
 * in platform_rf.c so that the aperture bases appear in exactly one .c file. ---- */
uint32_t rf_reg_read(uint32_t off);
void     rf_reg_write(uint32_t off, uint32_t val);

/* Sample buffer access, by WORD index.  The byte scaling happens inside, so no
 * caller scales an address itself. */
uint32_t rf_bram_read(uint32_t base, uint32_t word);
void     rf_bram_write(uint32_t base, uint32_t word, uint32_t val);

/* ---- trace / audit ---- */
void rf_trace_open(const char *path);
int  rf_trace_flush(void);   /* returns records written, or -1 */
void rf_trace_overflow_report(void);

/* Last value written to axi_gpio_ctrl's DATA register.  The selftest's S2a
 * criterion (enable high before resetb is released) judges this, not a local
 * variable, so the check is against what was actually driven. */
uint32_t rf_gpio_shadow(void);
void rf_platform_release(void);

#endif /* PLATFORM_RF_H_ */
