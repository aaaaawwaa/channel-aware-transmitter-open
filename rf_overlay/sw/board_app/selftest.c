/* selftest.c -- the C transport-layer self-check, run before any vendor code.
 *
 * This reproduces, byte for byte, the three criteria spi_probe.py already
 * passed on this board (docs/rf/boardtest.md L1-a/L1-b).  The point is not to
 * design a new test but to remove a variable: every later failure has two
 * possible sources -- the vendor driver's register sequence, and our C
 * transport layer.  Proving the latter separately leaves only one.
 *
 * Run on the board:
 *     sudo ./board_app selftest
 * Run on the host (same code, fake_pl instead of /dev/mem):
 *     make host-test
 */
#include "platform_rf.h"
#include "platform.h"
#include "ad9361.h"

#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ 判据表
 *
 * Written down here, before any measurement, per the project's report
 * discipline.  Each entry prints PASS/FAIL; nothing is inferred afterwards.
 *
 *   S1  PRODUCT_ID bits[7:3] == 0x08      (this board answers 0x0A = rev 2)
 *   S2  enable=1 released BEFORE resetb, and PRODUCT_ID is POLLED after the
 *       release because the part NACKs during its own self-check window
 *   S3  REG_CTRL (0x3DF) bit0 round-trips: write 0x01 -> read bit0 == 1,
 *       write 0x00 -> read bit0 == 0
 *
 * S1 is a MASK test, not equality: the vendor driver tests
 * (ret & 0xF8) != 0x08, where 0x08 is an in-place mask constant.  An equality
 * test against one specific byte would reject every other silicon revision.
 *
 * S3's target is REG_CTRL, not a scratch register: this part has no scratch
 * register.  0x3F5 is REG_OBSERVE_CONFIG, which the driver only ever writes
 * back what it just read -- using it as a scratch register produced a false
 * "the write path is broken" verdict on this board once already.
 */

/* Both registers are already defined in ad9361.h with the same values; the
 * ifndef guards keep the explicit spellings without redefining anything. */
#ifndef REG_PRODUCT_ID
#define REG_PRODUCT_ID   0x037u
#endif
#define AD9361_ID_MASK   0xF8u
#define AD9361_ID_9361   0x08u
#define AD9361_REV_MASK  0x07u
#ifndef REG_CTRL
#define REG_CTRL         0x3DFu
#endif

static int st_pass, st_fail;

static void st_chk(const char *name, int cond)
{
	if (cond) { st_pass++; printf("PASS %s\n", name); }
	else      { st_fail++; printf("FAIL %s\n", name); }
}

/* One-byte register access through the vendor's own instruction-word macros,
 * so the encoding under test is the vendor's, not a local re-derivation.
 * Named st_reg_* (not rf_reg_*): those names belong to platform_rf.h's
 * axi_ctrl_0 accessors, and colliding with them would not compile. */
static uint8_t st_reg_read(struct spi_device *spi, uint32_t addr)
{
	uint8_t tx[2], rx[1];
	uint16_t cmd = AD_READ | AD_CNT(1) | AD_ADDR(addr);
	tx[0] = (uint8_t)(cmd >> 8);
	tx[1] = (uint8_t)(cmd & 0xFF);
	rx[0] = 0;
	spi_write_then_read(spi, tx, 2, rx, 1);
	return rx[0];
}

static int st_reg_write(struct spi_device *spi, uint32_t addr, uint8_t val)
{
	uint8_t tx[3];
	uint16_t cmd = AD_WRITE | AD_CNT(1) | AD_ADDR(addr);
	tx[0] = (uint8_t)(cmd >> 8);
	tx[1] = (uint8_t)(cmd & 0xFF);
	tx[2] = val;
	return spi_write_then_read(spi, tx, 3, NULL, 0);
}

/* The mode-pin sequence, in the order the reference main.c uses it:
 * gpio_initial() leaves txnrx=1, enable=0, resetb=1, sync=0, en_agc=0,
 * ctl0-3=0; then the driver drives resetb itself through ad9361_reset().
 * Here we drive it explicitly so the ordering is a criterion rather than a
 * side effect of the vendor driver. */
static void mode_pins_to_reset(void)
{
	gpio_set_value(RF_GPIO_TXNRX,   1);
	gpio_set_value(RF_GPIO_ENABLE,  1);   /* enable FIRST ... */
	gpio_set_value(RF_GPIO_RESETB,  0);   /* ... then assert reset ... */
	gpio_set_value(RF_GPIO_SYNC,    0);
	gpio_set_value(RF_GPIO_EN_AGC,  0);
	gpio_set_value(RF_GPIO_CTL0,    0);
	gpio_set_value(RF_GPIO_CTL1,    0);
	gpio_set_value(RF_GPIO_CTL2,    0);
	gpio_set_value(RF_GPIO_CTL3,    0);
}

int rf_selftest(void)
{
	struct spi_device spi;
	uint8_t id, rev, v;
	int i, seen = 0;

	st_pass = st_fail = 0;

	printf("=== RF transport self-check (spec 6.1 Step 0) ===\n");
	printf("axi_ctrl_0    @ 0x%08X\n", RF_AXI_CTRL_BASE);
	printf("pl_spi_0      @ 0x%08X\n", RF_PL_SPI_BASE);
	printf("axi_gpio_ctrl @ 0x%08X\n", RF_GPIO_CTRL_BASE);

	/* ---- transport bring-up.  gpio_init first: the part ignores SPI while
	 *      resetb is held, which looks exactly like a dead SPI master. ---- */
	memset(&spi, 0, sizeof(spi));
	spi.id_no = 0;

	gpio_init(0);
	mode_pins_to_reset();

	if (spi_init(0, 1, 0) != 0) {
		printf("FAIL spi_init -- cannot continue\n");
		return 1;
	}

	/* S2 ordering, asserted on the shadow value that was actually written,
	 * at the last moment BEFORE the release: resetb must still be low here
	 * (the release is the very next statement) and enable must already be
	 * high -- that is the whole meaning "enable set before resetb released"
	 * can have for a single-snapshot shadow.  (The plan's expression
	 * required resetb high at this point too, which contradicts both the
	 * mode_pins_to_reset() above and the comment's own intent; it failed on
	 * the healthy model.) */
	st_chk("S2a enable=1 is set before resetb is released",
	       (rf_gpio_shadow() & (1u << RF_GPIO_ENABLE)) != 0u &&
	       (rf_gpio_shadow() & (1u << RF_GPIO_RESETB)) == 0u);

	/* Release reset.  The part then runs its own self-check and NACKs reads
	 * for a short while -- poll, do not decide on the first attempt. */
	gpio_set_value(RF_GPIO_RESETB, 1);

	for (i = 0; i < 200; i++) {          /* up to 2 s at 10 ms */
		id = st_reg_read(&spi, REG_PRODUCT_ID);
		if ((id & AD9361_ID_MASK) == AD9361_ID_9361) { seen = 1; break; }
		msleep_interruptible(10);
	}

	st_chk("S2b PRODUCT_ID answered inside the polling window", seen);

	if (!seen) {
		printf("=== %d PASS, %d FAIL ===\n", st_pass, st_fail);
		printf("RESULT FAIL (device never answered -- no register was written)\n");
		return 1;
	}

	rev = id & AD9361_REV_MASK;
	printf("     PRODUCT_ID = 0x%02X (masked 0x%02X) rev = %u\n",
	       id, id & AD9361_ID_MASK, rev);

	/* S1: the mask test, exactly as the vendor driver does it. */
	st_chk("S1 PRODUCT_ID bits[7:3] == 0x08", (id & AD9361_ID_MASK) == AD9361_ID_9361);

	/* S3: the write path.  This is the criterion the CNT encoding bug broke,
	 * and it is the only one of the three that exercises a WRITE -- which is
	 * why a read-only probe looked perfectly healthy while every write died. */
	if (st_reg_write(&spi, REG_CTRL, 0x01) != 0) {
		printf("FAIL S3 write of REG_CTRL returned an error\n");
	} else {
		v = st_reg_read(&spi, REG_CTRL);
		st_chk("S3a write REG_CTRL 0x01 -> read bit0 == 1", (v & 0x1u) == 0x1u);

		st_reg_write(&spi, REG_CTRL, 0x00);
		v = st_reg_read(&spi, REG_CTRL);
		st_chk("S3b write REG_CTRL 0x00 -> read bit0 == 0", (v & 0x1u) == 0x0u);
	}

	printf("=== %d PASS, %d FAIL ===\n", st_pass, st_fail);
	printf("RESULT %s\n", st_fail ? "FAIL" : "PASS");
	return st_fail ? 1 : 0;
}
