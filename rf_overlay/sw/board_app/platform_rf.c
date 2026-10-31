/* platform_rf.c -- the vendor driver's platform layer, mapped onto this design.
 *
 * Only these functions are ours; every vendor file in vendor/ is verbatim.  The
 * reference platform.c cannot be reused because it drives XSpiPs and puts the
 * nine control pins on PS EMIO GPIO, whereas here they are a 9-bit PL aperture
 * and the SPI port is our own PL master.
 *
 * With -DPLATFORM_HOST the MMIO goes to test/fake_pl.c instead of /dev/mem, so
 * the exact same source runs both on the board and in the host test.
 */
#include "platform_rf.h"
#include "platform.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifdef PLATFORM_HOST
/* Relative to this file, not "fake_pl.h": the host Makefile's -I paths are
 * ". vendor" and the header lives in test/.  The test sources get away with
 * the bare name only because quote-include searches the includer's own
 * directory first. */
#include "test/fake_pl.h"
#define MMIO_READ(a)    fake_pl_read((a))
#define MMIO_WRITE(a,v) fake_pl_write((a),(v))
#else
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/mman.h>

/* One mmap cache, keyed by page base, serving all three apertures -- the same
 * shape spi_probe.py's Mmio uses.  Access is by ABSOLUTE address so that the
 * host model can be a single function; see platform_rf.h for why.
 *
 * The cache is bounded and EVICTS instead of dying: every mapping is used
 * transiently by a single mmio_read/mmio_write, so when the cache is full the
 * oldest entry is unmapped and its slot reused.  A session may touch more than
 * RF_MAX_MAPS distinct 4 KiB pages (TX/RX sample buffers, control/status
 * apertures, the SPI page), and the daemon must stay up for the board's whole
 * lifetime regardless of how many distinct configs it is asked to run. */
#define RF_PAGE_SIZE 4096u
#define RF_MAX_MAPS  16

static int      mem_fd = -1;
static uint8_t *map_base[RF_MAX_MAPS];
static uint32_t map_addr[RF_MAX_MAPS];
static int      map_count;

static uint8_t *map_region(uint32_t addr)
{
	uint32_t base = addr & ~(RF_PAGE_SIZE - 1u);
	int i;

	for (i = 0; i < map_count; i++)
		if (map_addr[i] == base) return map_base[i];

	if (map_count >= RF_MAX_MAPS) {
		/* Evict the oldest mapping: the cache is a pure address-translation
		 * cache and every mapping is used transiently, so this is safe. */
		munmap(map_base[0], RF_PAGE_SIZE);
		memmove(&map_base[0], &map_base[1],
		        (unsigned)(RF_MAX_MAPS - 1) * sizeof(map_base[0]));
		memmove(&map_addr[0], &map_addr[1],
		        (unsigned)(RF_MAX_MAPS - 1) * sizeof(map_addr[0]));
		map_count--;
	}
	if (mem_fd < 0) {
		mem_fd = open("/dev/mem", O_RDWR | O_SYNC);
		if (mem_fd < 0) { perror("open /dev/mem"); exit(2); }
	}
	map_base[map_count] = (uint8_t *)mmap(NULL, RF_PAGE_SIZE,
	                                      PROT_READ | PROT_WRITE, MAP_SHARED,
	                                      mem_fd, base);
	if (map_base[map_count] == MAP_FAILED) {
		fprintf(stderr, "mmap 0x%08X failed\n", base);
		exit(2);
	}
	map_addr[map_count] = base;
	return map_base[map_count++];
}

static uint32_t mmio_read(uint32_t addr)
{
	uint8_t *p = map_region(addr);
	return *(volatile uint32_t *)(p + (addr & (RF_PAGE_SIZE - 1u)));
}

static void mmio_write(uint32_t addr, uint32_t v)
{
	uint8_t *p = map_region(addr);
	*(volatile uint32_t *)(p + (addr & (RF_PAGE_SIZE - 1u))) = v;
}

#define MMIO_READ(a)     mmio_read((a))
#define MMIO_WRITE(a,v)  mmio_write((a),(v))
#endif /* PLATFORM_HOST */

/* ------------------------------------------------------------------ timebase
 *
 * udelay/mdelay busy-wait on CLOCK_MONOTONIC rather than calling nanosleep.
 * The driver uses them for short polls inside the VCO calibration sequence,
 * where a scheduler round-trip would both stretch init and risk missing a
 * timing window; a spin costs a few hundred microseconds of CPU and nothing
 * else.  msleep_interruptible is only used for 100 ms and 1 ms waits outside
 * any calibration window, so it can afford to sleep.
 */
#ifndef PLATFORM_HOST
static uint64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
#endif

void udelay(unsigned long usecs)
{
#ifdef PLATFORM_HOST
	/* The host model's "hardware" is instantaneous, so the delays exist only
	 * to exercise the call paths.  Burning real CPU here would make the test
	 * suite take minutes for nothing -- so this whole function is a no-op on
	 * the host, and now_ns() is not even compiled in. */
	(void)usecs;
#else
	uint64_t end = now_ns() + (uint64_t)usecs * 1000ull;
	while (now_ns() < end)
		;
#endif
}

void mdelay(unsigned long msecs)
{
	udelay(msecs * 1000ul);
}

unsigned long msleep_interruptible(unsigned int msecs)
{
#ifdef PLATFORM_HOST
	(void)msecs;
	return 0;
#else
	struct timespec ts;
	ts.tv_sec  = (time_t)(msecs / 1000u);
	ts.tv_nsec = (long)(msecs % 1000u) * 1000000L;
	nanosleep(&ts, NULL);
	return 0;
#endif
}

/* ------------------------------------------------------------- axi_ctrl_0
 *
 * Our own registers, not the vendor's.  Exposed through platform_rf.h so that
 * rf_main.c and selftest.c never spell an aperture base themselves.
 */
uint32_t rf_reg_read(uint32_t off)
{
	return MMIO_READ(RF_CTRL_A(off));
}

void rf_reg_write(uint32_t off, uint32_t val)
{
	MMIO_WRITE(RF_CTRL_A(off), val);
}

/*
 * Sample buffer access.  Takes the aperture base because there are two of them
 * and neither is the control aperture, so RF_CTRL_A's single-base assumption
 * does not apply.
 *
 * `word` is a WORD index and is scaled by 4 here rather than at the call site.
 * The BRAM port is byte addressed (the BD fixes it that way and the PL side
 * matches with word << 2), and an unscaled index would still produce a valid
 * address -- just the wrong one -- so the shift belongs in exactly one place.
 */
uint32_t rf_bram_read(uint32_t base, uint32_t word)
{
	if (word >= RF_BRAM_WORDS)
		return 0;
	return MMIO_READ(base + word * 4u);
}

void rf_bram_write(uint32_t base, uint32_t word, uint32_t val)
{
	if (word >= RF_BRAM_WORDS)
		return;
	MMIO_WRITE(base + word * 4u, val);
}

/*
 * Turn the PL's window edge count into a DATA_CLK rate, and say which alias it
 * chose.
 *
 * dclk_now and win_base are both 16-bit (axi_ctrl_v1_0_S00_AXI.v), so the
 * published win_cycles is a MODULAR difference: the true count is
 * win_cycles + n*65536.  Every rate therefore has aliases spaced
 * 65536 * 50e6 / 2^win_log2 apart -- 100 MHz for the L=15 window -- and a
 * 120 MHz DATA_CLK genuinely arrives as 13107, the same word a 20 MHz clock
 * produces.  This is not hypothetical: reporting the n=0 member is exactly how
 * `board_app init` printed "20.000 MHz" for a 120 MHz clock.
 *
 * expect_hz is the DATA_CLK the configured interface implies (2*fs for 1R1T,
 * 4*fs for 2R2T), or 0 to take n=0.  The member nearest expect_hz wins.  That
 * choice USES the rate it is meant to measure, so the result corroborates the
 * configuration; it is not independent evidence of it, and callers must not
 * treat it as such.
 *
 * Returns 0 when there is nothing to resolve (no count, or an implausible
 * win_log2, which would make the shift undefined).
 */
uint64_t rf_f_data_clk_resolve(uint32_t status, uint64_t expect_hz, int *n_used)
{
	int      L    = (int)RF_STATUS_WIN_LOG2(status);
	uint64_t cnt  = RF_STATUS_WIN_CYCLES(status);
	int      n, best = 0;
	uint64_t best_d = ~0ull;

	if (cnt == 0 || L > 30)
		return 0;
	for (n = 0; n < 4; n++) {
		uint64_t f = ((cnt + (uint64_t)n * 65536ull) * 50000000ull) >> L;
		uint64_t d = (f > expect_hz) ? (f - expect_hz) : (expect_hz - f);
		if (d < best_d) { best_d = d; best = n; }
	}
	if (n_used)
		*n_used = best;
	return ((cnt + (uint64_t)best * 65536ull) * 50000000ull) >> L;
}

/* Alias spacing for this window, in Hz.  65536 FCLK0 cycles' worth of edges. */
uint64_t rf_f_data_clk_alias_step(uint32_t status)
{
	int L = (int)RF_STATUS_WIN_LOG2(status);
	if (L > 30)
		return 0;
	return (65536ull * 50000000ull) >> L;
}


/* ------------------------------------------------------------------ trace
 *
 * Always on, and deliberately NOT a file write per transaction: a write() in
 * the middle of the VCO calibration's timing window would perturb exactly what
 * this round is trying to measure.  Records go to a fixed in-memory buffer and
 * are flushed once, after init has finished.
 */
#define RF_TRACE_CAP  (256 * 1024)     /* records; ~12 bytes each = ~3 MiB */
typedef struct {
	uint8_t  rw;          /* 0 = read, 1 = write */
	uint16_t addr;
	uint8_t  n;
	uint8_t  data[8];
} rf_trace_rec;

static rf_trace_rec *trace_buf;
static uint32_t      trace_len;
static uint32_t      trace_lost;
static const char   *trace_path;

static void trace_add(int rw, uint16_t addr, const uint8_t *d, unsigned n)
{
	rf_trace_rec *r;
	if (!trace_buf) return;
	if (trace_len >= RF_TRACE_CAP) { trace_lost++; return; }
	r = &trace_buf[trace_len++];
	r->rw   = (uint8_t)rw;
	r->addr = addr;
	r->n    = (uint8_t)(n > 8 ? 8 : n);
	if (d && r->n) memcpy(r->data, d, r->n);
}

void rf_trace_open(const char *path)
{
	trace_path = path;
	if (!trace_buf)
		trace_buf = (rf_trace_rec *)calloc(RF_TRACE_CAP, sizeof(*trace_buf));
	trace_len  = 0;
	trace_lost = 0;
}

/* Written as text, one transaction per line, so the audit pass and the trace
 * can be diffed by eye and by script without a binary parser. */
int rf_trace_flush(void)
{
	FILE *f;
	uint32_t i;

	if (!trace_buf) return -1;
	if (!trace_path) return -1;
	f = fopen(trace_path, "w");
	if (!f) {
		fprintf(stderr, "cannot open trace file %s\n", trace_path);
		return -1;
	}
	for (i = 0; i < trace_len; i++) {
		const rf_trace_rec *r = &trace_buf[i];
		unsigned k;
		fprintf(f, "%c %03X", r->rw ? 'W' : 'R', r->addr);
		for (k = 0; k < r->n; k++)
			fprintf(f, " %02X", r->data[k]);
		fputc('\n', f);
	}
	fclose(f);
	return (int)trace_len;
}

void rf_trace_overflow_report(void)
{
	if (trace_lost)
		fprintf(stderr,
		        "WARNING: trace buffer overflowed, %u transactions lost; "
		        "the audit pass is still complete (it re-reads the device), "
		        "but the write history is not\n", trace_lost);
}

/* --------------------------------------------------------------------- SPI
 *
 * spi_init asserts the mode rather than accepting whatever it is handed.  The
 * PL master is wired for mode 1 (CPOL=0, CPHA=1); if the driver ever asked for
 * another mode the bits would be sampled on the wrong edge and every register
 * read would return plausible garbage, which is the worst possible failure
 * shape.  Failing loudly here costs nothing.
 */
static int spi_ready;

int32_t spi_init(uint32_t device_id, uint8_t clk_pha, uint8_t clk_pol)
{
	(void)device_id;

	if (clk_pha != 1 || clk_pol != 0) {
		fprintf(stderr,
		        "spi_init: PL master is fixed at SPI mode 1 (CPHA=1, CPOL=0), "
		        "asked for CPHA=%u CPOL=%u\n", clk_pha, clk_pol);
		return -1;
	}

	MMIO_WRITE(RF_SPI_A(RF_SPI_DIV), RF_SPI_DIV_DEFAULT);

	/* A wrong base reads the RTL's unmapped default instead of failing, so
	 * probe for it once, here, rather than trusting the first transaction. */
	if (MMIO_READ(RF_SPI_A(RF_SPI_STATUS)) == 0xDEADBEEFu) {
		fprintf(stderr,
		        "spi_init: pl_spi_0 at 0x%08X reads 0xDEADBEEF -- wrong base "
		        "address, or the wrong bitstream is loaded\n", RF_PL_SPI_BASE);
		return -1;
	}

	spi_ready = 1;
	return 0;
}

/* Wait for DONE.  The window is generous because a single mmap read costs tens
 * of microseconds while a 18-byte burst at 2.5 MHz SCLK takes under 60. */
static int spi_wait_done(void)
{
	int i;
	for (i = 0; i < 10000; i++)
		if (MMIO_READ(RF_SPI_A(RF_SPI_STATUS)) & 0x2u)
			return 0;
	fprintf(stderr, "SPI burst timed out (no DONE)\n");
	return -1;
}

int spi_write_then_read(struct spi_device *spi, const unsigned char *txbuf,
                        unsigned n_tx, unsigned char *rxbuf, unsigned n_rx)
{
	uint16_t instr;
	uint32_t n_data, w0 = 0, w1 = 0, i;
	uint64_t full;
	uint8_t  rx[8];

	(void)spi;

	if (!spi_ready) {
		fprintf(stderr, "spi_write_then_read before spi_init\n");
		return -1;
	}
	/* The vendor always passes the two instruction bytes in txbuf, so a
	 * well-formed call has n_tx >= 2. */
	if (n_tx < 2 || n_tx > 2 + MAX_MBYTE_SPI || n_rx > MAX_MBYTE_SPI) {
		fprintf(stderr, "spi_write_then_read: bad lengths n_tx=%u n_rx=%u\n",
		        n_tx, n_rx);
		return -1;
	}
	n_data = n_tx - 2;

	instr = (uint16_t)(((uint16_t)txbuf[0] << 8) | txbuf[1]);

	for (i = 0; i < n_data && i < 4; i++)
		w0 |= (uint32_t)txbuf[2 + i] << (8 * i);
	for (i = 4; i < n_data; i++)
		w1 |= (uint32_t)txbuf[2 + i] << (8 * (i - 4));

	MMIO_WRITE(RF_SPI_A(RF_SPI_TXD0), w0);
	MMIO_WRITE(RF_SPI_A(RF_SPI_TXD1), w1);
	MMIO_WRITE(RF_SPI_A(RF_SPI_INSTR), instr);
	MMIO_WRITE(RF_SPI_A(RF_SPI_CTRL), (n_rx << 8) | n_data | (1u << 16));

	if (spi_wait_done() != 0)
		return -1;
	if (MMIO_READ(RF_SPI_A(RF_SPI_STATUS)) & 0x4u)
		return -1;

	if (n_rx) {
		/* The 64-bit form matters: RXD1 is a 32-bit read, so "<< 32" on a
		 * uint32_t intermediate would be shift-count overflow (UB). */
		full = (uint64_t)MMIO_READ(RF_SPI_A(RF_SPI_RXD0))
		     | ((uint64_t)MMIO_READ(RF_SPI_A(RF_SPI_RXD1)) << 32);
		for (i = 0; i < n_rx; i++)
			rx[i] = (uint8_t)((full >> (8 * i)) & 0xFFu);
		if (rxbuf) memcpy(rxbuf, rx, n_rx);
	}

	/* Trace the instruction word's own fields, not the raw bytes, so the log
	 * is a register-level history: W/R, address, data. */
	{
		int is_write = (instr >> 15) & 1;
		uint16_t addr = instr & 0x3FFu;
		if (is_write)
			trace_add(1, addr, &txbuf[2], n_data);
		else
			trace_add(0, addr, rx, n_rx);
	}
	return 0;
}

/* Single-byte read on the driver's old interface.  Unused by the vendor's own
 * code (ad9361.c goes through ad9361_spi_read -> spi_write_then_read), but it
 * is in platform.h, so it is implemented rather than left to link-fail. */
int32_t spi_read(struct spi_device *spi, uint8_t *data, uint8_t bytes_number)
{
	uint8_t dummy = 0;
	(void)dummy;
	/* The reference's version sends bytes_number bytes and reads them back;
	 * nothing in this driver reaches it, so it is a one-byte passthrough. */
	return spi_write_then_read(spi, data, bytes_number, data, bytes_number);
}

/* -------------------------------------------------------------------- GPIO
 *
 * Nine outputs on one axi_gpio_ctrl aperture, bit order per create_bd.tcl:
 *   bit0 txnrx  bit1 enable  bit2 resetb  bit3 sync  bit4 en_agc  bits8:5 ctl0-3
 * which is also the reference main.c's EMIO numbering, so pin number == bit.
 *
 * The reference platform.c's gpio_is_valid is `if (number >= 0) return 1;`,
 * which accepts -1 -- and the reference main.c does pass -1 for gpio_sync,
 * gpio_cal_sw1 and gpio_cal_sw2.  Tightened here to the nine pins that exist.
 */
static uint32_t gpio_shadow;      /* last value written, for RMW */
static int      gpio_ready;

void gpio_init(uint32_t device_id)
{
	(void)device_id;
	/* All nine are outputs; TRI is written all-zero once and never touched. */
	MMIO_WRITE(RF_GPIO_A(RF_GPIO_TRI), 0);
	gpio_shadow = 0;
	MMIO_WRITE(RF_GPIO_A(RF_GPIO_DATA), gpio_shadow);
	gpio_ready = 1;
}

void gpio_direction(uint8_t pin, uint8_t direction)
{
	/* Every pin is an output; the aperture has no per-bit direction.  Kept so
	 * a stray gpio_direction(pin, 0) in driver code is visible rather than
	 * silently ignored. */
	if (!gpio_is_valid(pin)) return;
	if (direction != 1)
		fprintf(stderr, "gpio_direction: pin %u asked for input, "
		                "but all nine mode pins are outputs\n", pin);
}

bool gpio_is_valid(int number)
{
	return (number >= 0 && number < RF_GPIO_COUNT) ? true : false;
}

void gpio_set_value(unsigned gpio, int value)
{
	if (!gpio_is_valid((int)gpio)) return;
	if (!gpio_ready) gpio_init(0);
	if (value) gpio_shadow |=  (1u << gpio);
	else       gpio_shadow &= ~(1u << gpio);
	MMIO_WRITE(RF_GPIO_A(RF_GPIO_DATA), gpio_shadow);
}

uint32_t rf_gpio_shadow(void) { return gpio_shadow; }

/* The PL apertures and vendor platform callbacks are process-global because
 * the hardware is a singleton.  The RF core owns that singleton explicitly;
 * close must return every callback to an unowned/reset state before another
 * core can open it.  In particular, stale spi_ready/gpio_shadow/trace state
 * must never leak from one core instance into the next one. */
void rf_platform_release(void)
{
#ifndef PLATFORM_HOST
	int i;
	for (i = 0; i < map_count; ++i) {
		if (map_base[i] && map_base[i] != MAP_FAILED)
			munmap(map_base[i], RF_PAGE_SIZE);
		map_base[i] = NULL;
		map_addr[i] = 0;
	}
	map_count = 0;
	if (mem_fd >= 0) {
		close(mem_fd);
		mem_fd = -1;
	}
#endif
	spi_ready = 0;
	gpio_ready = 0;
	gpio_shadow = 0;
	free(trace_buf);
	trace_buf = NULL;
	trace_len = 0;
	trace_lost = 0;
	trace_path = NULL;
}
