/* test_platform_rf.c -- host unit tests for platform_rf.c against fake_pl.c. */
#include <stdio.h>
#include <string.h>
#include "fake_pl.h"
#include "platform_rf.h"
#include "platform.h"     /* vendor prototypes */
#include "ad9361.h"       /* AD_CNT / AD_READ / AD_WRITE / AD_ADDR */

static int pass_cnt, fail_cnt;
static void chk(const char *name, int cond)
{
	if (cond) { pass_cnt++; printf("PASS %s\n", name); }
	else      { fail_cnt++; printf("FAIL %s\n", name); }
}

/* Same values ad9361.h defines; the guards keep the explicit spellings
 * without redefinition warnings. */
#ifndef REG_PRODUCT_ID
#define REG_PRODUCT_ID 0x037u
#endif
#ifndef REG_CTRL
#define REG_CTRL       0x3DFu
#endif

static struct spi_device spi;

static uint8_t reg_read(uint32_t addr)
{
	uint8_t tx[2], rx[1];
	uint16_t cmd = AD_READ | AD_CNT(1) | AD_ADDR(addr);
	tx[0] = (uint8_t)(cmd >> 8);
	tx[1] = (uint8_t)(cmd & 0xFF);
	memset(rx, 0, sizeof(rx));
	spi_write_then_read(&spi, tx, 2, rx, 1);
	return rx[0];
}

static void reg_write(uint32_t addr, uint8_t val)
{
	uint8_t tx[3];
	uint16_t cmd = AD_WRITE | AD_CNT(1) | AD_ADDR(addr);
	tx[0] = (uint8_t)(cmd >> 8);
	tx[1] = (uint8_t)(cmd & 0xFF);
	tx[2] = val;
	spi_write_then_read(&spi, tx, 3, NULL, 0);
}

int main(void)
{
	printf("=== test_platform_rf ===\n");

	/* ---- the encoding rule itself, straight from the vendor header ---- */
	chk("AD_CNT(1) == 0 (one byte encodes CNT=0)", AD_CNT(1) == 0);
	chk("AD_CNT(2) == 1<<12",                      AD_CNT(2) == (1u << 12));
	chk("AD_CNT(8) == 7<<12",                      AD_CNT(8) == (7u << 12));

	/* ---- mode 1 is asserted, not silently accepted ---- */
	fake_pl_reset();
	chk("spi_init(pha=1, pol=0) returns success", spi_init(0, 1, 0) == 0);
	fake_pl_reset();
	chk("spi_init(pha=0, pol=0) is rejected",     spi_init(0, 0, 0) != 0);
	fake_pl_reset();
	chk("spi_init(pha=1, pol=1) is rejected",     spi_init(0, 1, 1) != 0);

	/* ---- DIV is written, not left at the PL reset value ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	chk("spi_init wrote RF_SPI_DIV_DEFAULT",
	    fake_pl_read(RF_SPI_A(RF_SPI_DIV)) == RF_SPI_DIV_DEFAULT);

	/* ---- a one-byte read of PRODUCT_ID ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	fake_pl_set_product_id(0x0Au);
	chk("one-byte read of PRODUCT_ID == 0x0A", reg_read(REG_PRODUCT_ID) == 0x0A);
	chk("read burst is 2 tx + 1 rx = 3 bytes", fake_last_burst_len == 3);

	/* ---- a one-byte write, and its readback ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	reg_write(REG_CTRL, 0x01);
	chk("write REG_CTRL 0x01 was accepted (not dropped)", fake_spi_rejected == 0);
	chk("write REG_CTRL 0x01 lands in the register file", fake_reg[REG_CTRL] == 0x01);
	chk("read back bit0 == 1", (reg_read(REG_CTRL) & 0x1) == 0x1);
	reg_write(REG_CTRL, 0x00);
	chk("read back bit0 == 0", (reg_read(REG_CTRL) & 0x1) == 0x0);

	/* ---- REVERSE CASE: a one-byte write encoded with CNT=1 must be
	 *      dropped by the device.  This is the bug that hid for a day:
	 *      reads encode 0 under either rule, so only writes died, and every
	 *      send-side model was blind to it. ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	{
		uint8_t tx[3];
		uint16_t bad = AD_WRITE | (1u << 12) | AD_ADDR(REG_CTRL); /* CNT=1 */
		tx[0] = (uint8_t)(bad >> 8);
		tx[1] = (uint8_t)(bad & 0xFF);
		tx[2] = 0x5A;
		spi_write_then_read(&spi, tx, 3, NULL, 0);
	}
	chk("reverse: CNT=1 one-byte write IS rejected by the device",
	    fake_spi_rejected == 1);
	chk("reverse: the dropped write left the register alone",
	    fake_reg[REG_CTRL] != 0x5A);

	/* Extra data bytes are just as invalid as missing bytes: CNT=1 claims
	 * exactly two data bytes, not two-or-more. */
	fake_pl_reset();
	spi_init(0, 1, 0);
	{
		uint8_t tx[5];
		uint16_t bad = AD_WRITE | AD_CNT(2) | AD_ADDR(REG_CTRL); /* CNT=1 */
		/* AD_CNT(2) is one in the field; use three payload bytes below. */
		tx[0] = (uint8_t)(bad >> 8);
		tx[1] = (uint8_t)(bad & 0xFF);
		tx[2] = 0x11;
		tx[3] = 0x22;
		tx[4] = 0x33;
		spi_write_then_read(&spi, tx, 5, NULL, 0);
	}
	chk("extra data beyond CNT+1 is rejected",
	    fake_spi_rejected == 1 && fake_reg[REG_CTRL] != 0x22);

	/* ---- multi-byte write takes the LAST byte ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	{
		uint8_t tx[4];
		uint16_t cmd = AD_WRITE | AD_CNT(2) | AD_ADDR(0x100);
		tx[0] = (uint8_t)(cmd >> 8);
		tx[1] = (uint8_t)(cmd & 0xFF);
		tx[2] = 0x11;
		tx[3] = 0x22;
		spi_write_then_read(&spi, tx, 4, NULL, 0);
	}
	chk("multi-byte write takes the last byte",
	    fake_spi_rejected == 0 && fake_reg[0x100] == 0x22);

	/* ---- multi-byte read ---- */
	fake_pl_reset();
	spi_init(0, 1, 0);
	fake_reg[0x100] = 0x33;
	{
		uint8_t tx[2], rx[2];
		uint16_t cmd = AD_READ | AD_CNT(2) | AD_ADDR(0x100);
		tx[0] = (uint8_t)(cmd >> 8);
		tx[1] = (uint8_t)(cmd & 0xFF);
		spi_write_then_read(&spi, tx, 2, rx, 2);
		chk("multi-byte read byte0 == 0x33", rx[0] == 0x33);
	}
	chk("multi-byte read clocked 2 + 2 = 4 bytes", fake_last_burst_len == 4);

	/* ---- gpio_is_valid is tightened to the 9 real pins ---- */
	chk("gpio_is_valid(0)",   gpio_is_valid(0) == true);
	chk("gpio_is_valid(8)",   gpio_is_valid(8) == true);
	chk("gpio_is_valid(9) is false",  gpio_is_valid(9) == false);
	chk("gpio_is_valid(-1) is false", gpio_is_valid(-1) == false);

	/* ---- gpio_set_value is a read-modify-write on the 9-bit aperture ---- */
	fake_pl_reset();
	gpio_init(0);
	gpio_set_value(RF_GPIO_TXNRX, 1);
	gpio_set_value(RF_GPIO_RESETB, 1);
	gpio_set_value(RF_GPIO_ENABLE, 1);
	chk("gpio RMW: txnrx|resetb|enable == 0x07",
	    fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x07u);
	gpio_set_value(RF_GPIO_RESETB, 0);
	/* 0x07 minus bit2 (resetb) = 0x03.  (The plan's literal said 0x05, which
	 * treats resetb as bit 1 and contradicts the 0x07 the previous line
	 * asserts: 0x07 = txnrx|enable|resetb.) */
	chk("gpio RMW: clearing resetb leaves the others == 0x03",
	    fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x03u);
	gpio_set_value(RF_GPIO_CTL3, 1);
	/* ctl3 is pin 8, and pin == bit per the header's mapping. */
	chk("gpio RMW: ctl3 is bit 8 == 0x103",
	    fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x103u);

	/* ---- the axi_ctrl_0 config registers, which rf_main's `status` and the
	 *      init sequence both use.  rf_reg_write/read live in platform_rf.c so
	 *      there is exactly one place that knows the apertures. ---- */
	fake_pl_reset();
	rf_reg_write(RF_REG_PHY_MODE, 1);
	chk("phy_mode round-trips through rf_reg_read",
	    rf_reg_read(RF_REG_PHY_MODE) == 1u);
	rf_reg_write(RF_REG_IDELAY_EN, 0x7F);
	rf_reg_write(RF_REG_IDELAY_TAP, 15);
	rf_reg_write(RF_REG_PHASE_INC, 128);
	chk("idelay_en/tap/phase_inc round-trip",
	    rf_reg_read(RF_REG_IDELAY_EN)  == 0x7Fu &&
	    rf_reg_read(RF_REG_IDELAY_TAP) == 15u   &&
	    rf_reg_read(RF_REG_PHASE_INC)  == 128u);

	/* ---- STATUS is read-only: a write must not change what is read ---- */
	fake_pl_reset();
	fake_pl_set_status(0x00040001u);          /* clk_seen set */
	/* Hold this manually supplied diagnostic word stable; fresh-window tests
	 * exercise the normal advancing model through fake_pl_set_status_window_stuck(0). */
	fake_pl_set_status_window_stuck(1);
	rf_reg_write(RF_REG_STATUS, 0xDEADBEEFu);
	chk("STATUS ignores writes", rf_reg_read(RF_REG_STATUS) == 0x00040001u);
	chk("STATUS decodes clk_seen",  RF_STATUS_CLK_SEEN(rf_reg_read(RF_REG_STATUS)) == 1u);
	chk("STATUS decodes win_done",  RF_STATUS_WIN_DONE(rf_reg_read(RF_REG_STATUS)) == 0u);

	/* ---- the f_DATA_CLK formula, checked against hand arithmetic ----
	 * win_cycles=19661, WIN_LOG2=15, f_FCLK0=50 MHz
	 *   -> floor(19661 * 50e6 / 32768) = floor(30000305.17) = 30000305
	 *   -> 30.000 MHz DATA_CLK.
	 * This is the number the whole B/C-layer argument rests on, so it is
	 * checked as arithmetic rather than assumed. */
	fake_pl_reset();
	fake_pl_set_status((15u << 24) | (19661u << 8));
	chk("f_DATA_CLK formula gives 30.000 MHz at 19661 cycles",
	    RF_STATUS_F_DATA_CLK_HZ(rf_reg_read(RF_REG_STATUS)) == 30000305ull);
	chk("f_DATA_CLK formula gives 0 when no clock was seen",
	    RF_STATUS_F_DATA_CLK_HZ(0u) == 0ull);

	/* ---- the alias-aware resolver, which exists because that formula WRAPS.
	 *
	 * These are the board's own numbers.  `init` at 60 MSPS printed
	 * "20.000 MHz" against a real 120 MHz DATA_CLK, and the word it read was
	 * win_cycles=13107 with WIN_LOG2=15.  The window is 2^15 FCLK0 cycles, so
	 * consecutive aliases sit 65536*50e6/32768 = 100 MHz apart, and 120 MHz is
	 * the second member of the family that 13107 belongs to. */
	chk("alias step at WIN_LOG2=15 is exactly 100 MHz",
	    rf_f_data_clk_alias_step(15u << 24) == 100000000ull);

	{
		/* 78643 edges, delivered wrapped as 78643 - 65536 = 13107. */
		uint32_t sw = (15u << 24) | (13107u << 8);
		int      n  = -1;

		chk("resolver recovers 120 MHz from the wrapped 13107 when told to "
		    "expect 120 MHz",
		    rf_f_data_clk_resolve(sw, 120000000ull, &n) == 119999694ull
		    && n == 1);
		/* The same word against a 20 MHz expectation must still answer
		 * 20 MHz.  The resolution comes from the caller's prior and
		 * nothing else, so a criterion that needs an independent rate
		 * cannot get one from here -- that is the point of the split
		 * between this function and the plain macro. */
		chk("the same word answers 20 MHz against a 20 MHz expectation",
		    rf_f_data_clk_resolve(sw, 20000000ull, &n) == 19999694ull
		    && n == 0);
		chk("an absent expectation takes the lowest alias",
		    rf_f_data_clk_resolve(sw, 0ull, &n) == 19999694ull && n == 0);
	}

	/* Below the wrap there is nothing to resolve, and here the resolver must
	 * agree to the hertz with the plain formula the pre-init negative check
	 * still uses -- otherwise the two readings of one register disagree. */
	{
		uint32_t sw = (15u << 24) | (19661u << 8);
		int      n  = -1;

		chk("resolver agrees with the plain formula at 30 MHz (no wrap)",
		    rf_f_data_clk_resolve(sw, 30000000ull, &n) == 30000305ull
		    && n == 0);
		chk("resolver returns 0 when no clock was seen",
		    rf_f_data_clk_resolve(0u, 120000000ull, NULL) == 0ull);
	}

	printf("=== %d PASS, %d FAIL ===\n", pass_cnt, fail_cnt);
	return fail_cnt ? 1 : 0;
}
