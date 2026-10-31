#!/usr/bin/env bash
# host_probe.sh -- run board_app's OWN dispatch on the development host, with the
# REAL vendor driver linked in.
#
# Why this exists
# ---------------
# `make host-test` covers platform_rf.c, selftest.c and the PL/SPI model in
# test/fake_pl.c -- but nothing in it ever enters rf_main.c, which is the program
# that actually runs on the board.  rf_main.c's subcommand dispatch, its exit
# codes, and its use of the vendor driver were only ever READ on the host, never
# executed.  The register audit (spec 6.1 Step 5) rests entirely on an assertion
# of that shape:
#
#     "a zeroed `struct spi_device` is a usable handle for spi_write_then_read,
#      with no ad9361_init and no driver instance behind it"
#
# Reading the vendor source suggests that is true -- ad9361_spi_read only ever
# touches spi->dev through dev_err/dev_dbg, which never evaluate their argument,
# and struct device is empty anyway.  "Suggests" is not good enough for the one
# mechanism the whole audit depends on, and the development host can settle it:
# vendor/ad9361.c compiles under MinGW once strsep is supplied, so the real
# ad9361_spi_read can be linked and called.
#
# What this script builds
# -----------------------
# Four binaries, each with rf_main.c compiled as a callable entry point
# (-Dmain=rf_main_entry; the deliverable is not modified to make this work):
#
#   1. probe_dispatch   rf_main.c + platform_rf.c + test/fake_pl.c + selftest.c
#                       + the REAL vendor driver, behind a forwarding main that
#                       hands the probe its own argv.  stdout, stderr and the
#                       exit code are therefore byte-for-byte what
#                       `board_app <args>` produces, and the assertions below
#                       parse them exactly the way Task 6's audit_report.py
#                       will (`raw.strip().split()[-1]`, then int(_, 16)).
#
#   2. probe_auditfail  The same, with spi_init and ad9361_spi_read forced to
#                       fail, so the audit's failure paths are EXECUTED rather
#                       than only read.  The property under test is that a dead
#                       transport cannot print a plausible value.
#
#   3. probe_init       rf_main.c against recording stubs, for `init`'s argument
#                       parsing and its override dispatch.  The real
#                       ad9361_init cannot complete here -- see section 4 -- so
#                       section 3 uses stubs that record what they were handed.
#
#   4. probe_repeat     plcfg + init three times in one process, against the real
#                       vendor ad9361_init.  INFORMATIONAL: see section 4.
#
# Vendor source is compiled UNMODIFIED.  MinGW's libc merely lacks strsep, a GNU
# extension vendor/ad9361.c uses, so test/strsep_shim.[ch] supplies it from
# OUTSIDE vendor/ via -include.  The tree under vendor/ is byte-checked against
# vendor/SHA256SUMS and not one byte of it changes.  (An earlier version of this
# harness did not exist as a tracked file at all, which made its claims
# unreproducible; that is the whole reason this script is in the repository.)
#
# What this is NOT
# ----------------
# A host smoke test, not board evidence.  It cannot show that /dev/mem works,
# that the SPI master clocks a byte, or that any AD9361 answers.  What it shows
# is that OUR code dispatches correctly, that its failure paths stay silent on
# stdout, and that it does not dereference junk when the vendor driver is real.
# Criteria 2.1-2.5 and Steps 1-5 on actual hardware remain Task 7's.
#
# Run from anywhere:  bash rf_overlay/sw/board_app/test/host_probe.sh

set -u

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# NOT mktemp: this machine's user name is non-ASCII, and the resulting %TEMP%
# path makes ld.exe fail to create its output file.  A repo-local scratch dir
# avoids the encoding problem entirely and is gitignored (.hostobj/).
WORK="$APP/.hostobj/probe"
rm -rf "$WORK"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- the harness
# Written out here rather than kept as separate tracked .c files, so the whole
# host probe is one artifact that either runs or does not.  Same shape as
# rf_overlay/sw/board_branch_check.sh.

cat > "$WORK/fwd.c" <<'EOF'
/* Forwarding harness: the probe binary IS `board_app <args>` -- real argv, real
 * exit code, real stdout -- so what Task 6's BoardCmd.cmd() would see is
 * captured by the shell exactly as it will be on the board.  Because this main
 * prints nothing of its own, stdout is the program's output and nothing else. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "test/fake_pl.h"

int rf_main_entry(int argc, char **argv);

int main(int argc, char **argv)
{
	const char *seed   = getenv("PROBE_SEED");
	const char *status = getenv("PROBE_STATUS");

	/* The PL STATUS model returns one constant word and never advances its
	 * window sequence number, so the freshness loop in status_read_fresh()
	 * always times out (udelay is a no-op on the host) and reports the word it
	 * started with.  Seeding it is therefore how a case selects the clock state
	 * it wants `status` and `plcfg` to be judged on. */
	if (status && *status)
		fake_pl_set_status((uint32_t)strtoul(status, NULL, 16));

	/* PROBE_SEED="3F0=A5,0C8=1F" seeds the modelled AD9361 register file, so an
	 * audit read has a known value to return.  Seeding happens BEFORE rf_main
	 * runs; rf_main never calls fake_pl_reset(), so the seed survives. */
	if (seed && *seed) {
		char *copy = (char *)malloc(strlen(seed) + 1);
		char *tok;

		if (copy == NULL) return 3;
		strcpy(copy, seed);
		for (tok = strtok(copy, ","); tok; tok = strtok(NULL, ",")) {
			unsigned a = 0, v = 0;
			if (sscanf(tok, "%x=%x", &a, &v) == 2 && a < 0x400u)
				fake_reg[a] = (uint8_t)v;
		}
		free(copy);
	}
	return rf_main_entry(argc, argv);
}
EOF

cat > "$WORK/auditfail.c" <<'EOF'
/* Force the audit's failure paths.  Nothing here models hardware: spi_init and
 * ad9361_spi_read either succeed with a fixed value or fail, nothing else.
 *
 * PROBE_FAIL=init -> spi_init fails
 * PROBE_FAIL=read -> spi_init succeeds, the register read fails
 *
 * platform_rf.c and selftest.c are deliberately NOT linked into this binary
 * (they define spi_init, which is the thing being forced), so the few remaining
 * symbols rf_main.o references are one-line stand-ins below. */
#include "platform_rf.h"
#include "platform.h"
#include "ad9361.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *fail_mode;

int32_t spi_init(uint32_t device_id, uint8_t clk_pha, uint8_t clk_pol)
{
	(void)device_id; (void)clk_pha; (void)clk_pol;
	fail_mode = getenv("PROBE_FAIL");
	return (fail_mode && !strcmp(fail_mode, "init")) ? -1 : 0;
}

int32_t ad9361_spi_read(struct spi_device *spi, uint32_t reg)
{
	(void)spi; (void)reg;
	return (fail_mode && !strcmp(fail_mode, "read")) ? -1 : 0x5A;
}

uint32_t rf_reg_read(uint32_t off)             { (void)off; return 0; }
void     rf_reg_write(uint32_t off, uint32_t v){ (void)off; (void)v; }
void     udelay(unsigned long u)               { (void)u; }
void     gpio_init(uint32_t d)                 { (void)d; }
void     gpio_set_value(unsigned g, int v)     { (void)g; (void)v; }
uint32_t rf_gpio_shadow(void)                  { return 0; }
void     rf_trace_open(const char *p)          { (void)p; }
int      rf_trace_flush(void)                  { return 0; }
void     rf_trace_overflow_report(void)        { }
int      rf_selftest(void)                     { return 0; }
unsigned long msleep_interruptible(unsigned int m) { (void)m; return 0; }
EOF

cat > "$WORK/stubs.c" <<'EOF'
/* Inert stand-ins for the vendor API, so the audit-failure binary LINKS.
 * Every one returns a constant and records nothing: the failure being exercised
 * happens in spi_init / ad9361_spi_read, before any of these is reached. */
#include "ad9361_api.h"

static struct ad9361_rf_phy dummy_phy;

int32_t ad9361_init(struct ad9361_rf_phy **p, AD9361_InitParam *ip)
{ (void)ip; *p = &dummy_phy; return 0; }

int32_t ad9361_get_en_state_machine_mode(struct ad9361_rf_phy *p, uint32_t *m)
{ (void)p; *m = 0x05; return 0; }
int32_t ad9361_get_tx_lo_freq(struct ad9361_rf_phy *p, uint64_t *l)
{ (void)p; *l = 80000000ull; return 0; }
int32_t ad9361_get_tx_attenuation(struct ad9361_rf_phy *p, uint8_t c, uint32_t *a)
{ (void)p; (void)c; *a = 0; return 0; }
int32_t ad9361_get_tx_sampling_freq(struct ad9361_rf_phy *p, uint32_t *f)
{ (void)p; *f = 60000000u; return 0; }
int32_t ad9361_get_rx_sampling_freq(struct ad9361_rf_phy *p, uint32_t *f)
{ (void)p; *f = 60000000u; return 0; }
int32_t ad9361_set_tx_sampling_freq(struct ad9361_rf_phy *p, uint32_t f)
{ (void)p; (void)f; return 0; }
int32_t ad9361_set_rx_sampling_freq(struct ad9361_rf_phy *p, uint32_t f)
{ (void)p; (void)f; return 0; }
int32_t ad9361_get_trx_path_clks(struct ad9361_rf_phy *p, uint32_t *rx, uint32_t *tx)
{ (void)p; (void)rx; (void)tx; return 0; }
int32_t ad9361_set_tx_attenuation(struct ad9361_rf_phy *p, uint8_t c, uint32_t a)
{ (void)p; (void)c; (void)a; return 0; }
int32_t ad9361_set_tx_lo_freq(struct ad9361_rf_phy *p, uint64_t f)
{ (void)p; (void)f; return 0; }
int32_t ad9361_set_rx_lo_freq(struct ad9361_rf_phy *p, uint64_t f)
{ (void)p; (void)f; return 0; }
int32_t ad9361_set_tx_fir_config(struct ad9361_rf_phy *p, AD9361_TXFIRConfig c)
{ (void)p; (void)c; return 0; }
int32_t ad9361_set_rx_fir_config(struct ad9361_rf_phy *p, AD9361_RXFIRConfig c)
{ (void)p; (void)c; return 0; }
int32_t ad9361_set_tx_fir_en_dis(struct ad9361_rf_phy *p, uint8_t e)
{ (void)p; (void)e; return 0; }
int32_t ad9361_set_rx_fir_en_dis(struct ad9361_rf_phy *p, uint8_t e)
{ (void)p; (void)e; return 0; }
EOF

cat > "$WORK/initstub.c" <<'EOF'
/* Recording stand-ins for the vendor API + platform layer, so `init`'s ARGUMENT
 * PARSING and its override dispatch can be executed on the host.
 *
 * Nothing here models the AD9361.  Every call records what it was handed and
 * returns a constant.  The point is to prove that `init atten 30000 lo 85000000`
 * really reaches ad9361_set_tx_attenuation(phy, 0, 30000) and
 * ad9361_set_tx_lo_freq(phy, 85000000) on the LIVE phy, in the run that read the
 * values back -- which is what criterion 2.4 and the 4.4/4.5 measurement points
 * require, and what ruling R7 folded these knobs into `init` to make possible.
 *
 * The setters also keep a "live" value that the getters return, so a readback
 * that appears in the output is a readback of something actually set rather than
 * a hardcoded constant. */
#include "platform_rf.h"
#include "platform.h"
#include "ad9361_api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct recorded {
	int      init_calls;      /* how many times ad9361_init ran */
	int      atten_calls;     /* set_tx_attenuation calls */
	uint32_t atten_arg[8];
	uint8_t  atten_ch[8];
	int      lo_calls;
	uint64_t lo_arg[8];
	int      fir_tx, fir_rx;  /* set_tx/rx_fir_en_dis values */
	char     trace_path[128]; /* what rf_trace_open was handed */
	int      flush_calls;     /* rf_trace_flush calls: 2 iff I1's second flush exists */
	int      dump_reads;      /* get_trx_path_clks calls: 2.5 (1) + dump (1) */
	int      fs_reads;        /* get_tx_sampling_freq calls: 2.5 (1) + dump (1) */
	int      fs_rx_reads;     /* get_rx_sampling_freq calls: 2.5 ONLY (always 1) */
	int      fs_set_calls;    /* set_{rx,tx}_sampling_freq calls: 2 iff R14's fix exists */
	uint32_t fs_set_arg[4];
};
struct recorded rec;

static struct ad9361_rf_phy dummy_phy;

static uint32_t live_atten = 0;             /* what a setter last stored */
static uint64_t live_lo    = 80000000ull;   /* the init_param value */

/* 40 MSPS, NOT 60.  This is what the struct's PRE-recompute path clocks encode
 * (index 5 = 40 MHz), and it is the rate the part actually runs at when the two
 * ad9361_set_*_sampling_freq calls are absent -- ad9361_init programs the chain
 * straight from the struct (vendor/ad9361.c:5329-5330).  Starting the model
 * here rather than at 60000000 is what makes criterion 2.5 discriminating:
 * delete those two calls and the readback drops to 40 MSPS, 2.5 prints
 * MISMATCH, and the assertions below go red. */
static uint32_t live_fs = 40000000u;

int32_t ad9361_init(struct ad9361_rf_phy **p, AD9361_InitParam *ip)
{
	(void)ip;
	rec.init_calls++;
	live_lo = 80000000ull;
	live_atten = 0;
	live_fs = 40000000u;    /* the struct's rate, before any recompute */
	*p = &dummy_phy;
	return 0;
}

int32_t ad9361_set_tx_attenuation(struct ad9361_rf_phy *p, uint8_t ch, uint32_t a)
{
	(void)p;
	if (rec.atten_calls < 8) {
		rec.atten_arg[rec.atten_calls] = a;
		rec.atten_ch[rec.atten_calls]  = ch;
	}
	rec.atten_calls++;
	live_atten = a;
	return 0;
}

int32_t ad9361_get_tx_attenuation(struct ad9361_rf_phy *p, uint8_t ch, uint32_t *a)
{ (void)p; (void)ch; *a = live_atten; return 0; }

int32_t ad9361_set_tx_lo_freq(struct ad9361_rf_phy *p, uint64_t f)
{
	(void)p;
	if (rec.lo_calls < 8) rec.lo_arg[rec.lo_calls] = f;
	rec.lo_calls++;
	live_lo = f;
	return 0;
}

int32_t ad9361_set_rx_lo_freq(struct ad9361_rf_phy *p, uint64_t f)
{
	(void)p;
	if (rec.lo_calls < 8) rec.lo_arg[rec.lo_calls] = f;
	rec.lo_calls++;
	return 0;
}

int32_t ad9361_get_tx_lo_freq(struct ad9361_rf_phy *p, uint64_t *l)
{ (void)p; *l = live_lo; return 0; }

int32_t ad9361_get_en_state_machine_mode(struct ad9361_rf_phy *p, uint32_t *m)
{ (void)p; *m = 0x05; return 0; }

int32_t ad9361_get_tx_sampling_freq(struct ad9361_rf_phy *p, uint32_t *f)
{ (void)p; rec.fs_reads++; *f = live_fs; return 0; }

/* The R14 pair.  These are the calls that make the part run at 60 MSPS; the
 * model records that they happened and what they were handed, and moves
 * live_fs so criterion 2.5's readback reflects them. */
int32_t ad9361_set_tx_sampling_freq(struct ad9361_rf_phy *p, uint32_t f)
{
	(void)p;
	if (rec.fs_set_calls < 4) rec.fs_set_arg[rec.fs_set_calls] = f;
	rec.fs_set_calls++;
	live_fs = f;
	return 0;
}
int32_t ad9361_set_rx_sampling_freq(struct ad9361_rf_phy *p, uint32_t f)
{
	(void)p;
	if (rec.fs_set_calls < 4) rec.fs_set_arg[rec.fs_set_calls] = f;
	rec.fs_set_calls++;
	live_fs = f;
	return 0;
}
int32_t ad9361_get_rx_sampling_freq(struct ad9361_rf_phy *p, uint32_t *f)
{ (void)p; rec.fs_rx_reads++; *f = live_fs; return 0; }

int32_t ad9361_get_trx_path_clks(struct ad9361_rf_phy *p, uint32_t *rx, uint32_t *tx)
{
	static const uint32_t r[6] = {1280000000,320000000,160000000,80000000,40000000,40000000};
	static const uint32_t t[6] = {1280000000,160000000,160000000,80000000,40000000,40000000};
	(void)p;
	rec.dump_reads++;
	memcpy(rx, r, sizeof r); memcpy(tx, t, sizeof t);
	return 0;
}

int32_t ad9361_set_tx_fir_config(struct ad9361_rf_phy *p, AD9361_TXFIRConfig c)
{ (void)p; (void)c; return 0; }
int32_t ad9361_set_rx_fir_config(struct ad9361_rf_phy *p, AD9361_RXFIRConfig c)
{ (void)p; (void)c; return 0; }
int32_t ad9361_set_tx_fir_en_dis(struct ad9361_rf_phy *p, uint8_t e)
{ (void)p; rec.fir_tx = e; return 0; }
int32_t ad9361_set_rx_fir_en_dis(struct ad9361_rf_phy *p, uint8_t e)
{ (void)p; rec.fir_rx = e; return 0; }

/* ---- platform layer ---- */
int32_t spi_init(uint32_t d, uint8_t pha, uint8_t pol)
{ (void)d; (void)pha; (void)pol; return 0; }

/* The phy_mode assert in init must see 1, or init returns before the overrides
 * -- exactly as it would on the board if Step 1 had not run. */
uint32_t rf_reg_read(uint32_t off) { return off == RF_REG_PHY_MODE ? 1u : 0u; }
void     rf_reg_write(uint32_t off, uint32_t v) { (void)off; (void)v; }
void     udelay(unsigned long u) { (void)u; }
unsigned long msleep_interruptible(unsigned int m) { (void)m; return 0; }
void     gpio_init(uint32_t d) { (void)d; }
void     gpio_set_value(unsigned g, int v) { (void)g; (void)v; }
uint32_t rf_gpio_shadow(void) { return 0; }
void     rf_trace_open(const char *p)
{ snprintf(rec.trace_path, sizeof rec.trace_path, "%s", p ? p : "(null)"); }
/* Which rf_trace_flush call to fail, 1-based; 0 means never.  The harness sets
 * it to exercise I2, which requires a check at BOTH flush sites -- failing only
 * the first would leave the second site's check unproven. */
int      probe_fail_flush_at;
int      rf_trace_flush(void)
{
	rec.flush_calls++;
	/* The flush reports -1 and `init` must exit 1 rather than continue on to
	 * RESULT PASS while Task 6's input file does not exist. */
	return (probe_fail_flush_at != 0 && rec.flush_calls == probe_fail_flush_at)
	       ? -1 : 0;
}
void     rf_trace_overflow_report(void) { }
int      rf_selftest(void) { return 0; }
int32_t ad9361_spi_read(struct spi_device *s, uint32_t r) { (void)s; (void)r; return 0; }
EOF

cat > "$WORK/initparse.c" <<'EOF'
/* Drives `init`'s argument parsing and override dispatch, one call per case,
 * against the recording stubs in initstub.c.  Verifies ruling R7's contract:
 * the overrides reach the live phy in the SAME invocation that reads them back. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "platform_rf.h"
#include "platform.h"
#include "ad9361_api.h"

int rf_main_entry(int argc, char **argv);

struct recorded {
	int      init_calls, atten_calls;
	uint32_t atten_arg[8];
	uint8_t  atten_ch[8];
	int      lo_calls;
	uint64_t lo_arg[8];
	int      fir_tx, fir_rx;
	char     trace_path[128];
	int      flush_calls;
	int      dump_reads;
	int      fs_reads;
	int      fs_rx_reads;
	int      fs_set_calls;
	uint32_t fs_set_arg[4];
};
extern struct recorded rec;
extern int probe_fail_flush_at;   /* initstub.c: fail the Nth rf_trace_flush */

static int fails;
static int rc;

static void chk(const char *name, int cond)
{
	if (cond) printf("PASS %s\n", name);
	else { fails++; printf("FAIL %s\n", name); }
}

static void run(const char *label, int argc, char **argv)
{
	int i;
	memset(&rec, 0, sizeof rec);
	printf("\n########## %s ##########\n", label);
	rc = rf_main_entry(argc, argv);
	printf(">>> exit=%d init_calls=%d fir_tx=%d fir_rx=%d trace=[%s] "
	       "flush=%d fs_set=%d fs_read=%d/%d dump_reads=%d\n",
	       rc, rec.init_calls, rec.fir_tx, rec.fir_rx, rec.trace_path,
	       rec.flush_calls, rec.fs_set_calls, rec.fs_rx_reads, rec.fs_reads,
	       rec.dump_reads);
	printf(">>> atten_calls=%d", rec.atten_calls);
	for (i = 0; i < rec.atten_calls && i < 8; i++)
		printf(" ch%u=%u", rec.atten_ch[i], rec.atten_arg[i]);
	printf("  lo_calls=%d", rec.lo_calls);
	for (i = 0; i < rec.lo_calls && i < 8; i++)
		printf(" %llu", (unsigned long long)rec.lo_arg[i]);
	printf("  fs_set_args=");
	for (i = 0; i < rec.fs_set_calls && i < 4; i++) printf(" %u", rec.fs_set_arg[i]);
	printf("\n");
}

int main(void)
{
	char *a_bare[]    = { "board_app", "init", NULL };
	char *a_trace[]   = { "board_app", "init", "trace.txt", NULL };
	char *a_atten[]   = { "board_app", "init", "atten", "30000", NULL };
	char *a_lo[]      = { "board_app", "init", "lo", "85000000", NULL };
	char *a_all[]     = { "board_app", "init", "trace.txt", "atten", "30000",
	                      "lo", "85000000", "dump", NULL };
	char *a_reorder[] = { "board_app", "init", "lo", "85000000", "atten", "30000", NULL };
	char *a_noatten[] = { "board_app", "init", "atten", NULL };
	char *a_nolo[]    = { "board_app", "init", "lo", NULL };
	char *a_onearg[]  = { "board_app", "init", "bogus", NULL };
	char *a_bad[]     = { "board_app", "init", "trace.txt", "bogus", NULL };
	char *a_dump[]    = { "board_app", "dump", NULL };
	char *a_a0[]      = { "board_app", "atten", "0", NULL };
	char *a_l0[]      = { "board_app", "lo", "85000000", NULL };
	/* M1: the parse boundary.  Every one of these used to exit 0. */
	char *a_neg[]     = { "board_app", "init", "atten", "-5", NULL };
	char *a_ajunk[]   = { "board_app", "init", "atten", "12x", NULL };
	char *a_anum[]    = { "board_app", "init", "atten", "1e5", NULL };
	char *a_lotxt[]   = { "board_app", "init", "lo", "atten", NULL };
	char *a_lojunk[]  = { "board_app", "init", "lo", "85000000x", NULL };
	char *a_loempty[] = { "board_app", "init", "lo", "", NULL };
	char *a_loneg[]   = { "board_app", "init", "lo", "-5", NULL };

	printf("=== init argument parsing / R7 override dispatch ===\n");

	/* ---- bare `init` ---- */
	run("bare init", 2, a_bare);
	chk("bare init exits 0", rc == 0);
	chk("bare init ran ad9361_init once", rec.init_calls == 1);
	chk("bare init kept the default trace name", !strcmp(rec.trace_path, "trace_init.txt"));
	chk("bare init enables both FIRs", rec.fir_tx == 1 && rec.fir_rx == 1);
	chk("bare init sets no atten", rec.atten_calls == 0);
	chk("bare init sets no LO", rec.lo_calls == 0);

	/* R14: the rate request must reach the driver on BOTH paths.  Delete either
	 * call and the part stays at the struct's 40 MSPS with no error anywhere. */
	chk("bare init requested 60 MSPS on both paths (R14)",
	    rec.fs_set_calls == 2 && rec.fs_set_arg[0] == 60000000u &&
	    rec.fs_set_arg[1] == 60000000u);

	/* I1: a SECOND flush, after the FIR / sampling / override writes.  Without
	 * it Task 6's address list omits every one of them. */
	chk("bare init flushed the trace twice (I1)", rec.flush_calls == 2);

	/* Two counters, because no single call is dump-only any more.  Criterion
	 * 2.5 now calls get_tx_sampling_freq too, so the old "fs_reads == 0" form
	 * would be vacuously true and the old "== 1" form indistinguishable from a
	 * dump.  2.5 contributes 1 to each counter; the dump adds a second get_tx,
	 * and nothing but the dump calls get_rx. */
	chk("criterion 2.5 read the rate back on both paths",
	    rec.fs_rx_reads == 1 && rec.fs_reads == 1);
	chk("bare init does not dump", rec.fs_reads == 1);

	/* ---- `init trace.txt` ---- */
	run("init trace.txt", 3, a_trace);
	chk("init trace.txt exits 0", rc == 0);
	chk("init trace.txt uses the given path", !strcmp(rec.trace_path, "trace.txt"));
	chk("init trace.txt sets no other override",
	    rec.atten_calls == 0 && rec.lo_calls == 0);

	/* ---- `init atten 30000`: a keyword is NOT taken as a trace path ---- */
	run("init atten 30000", 4, a_atten);
	chk("init atten 30000 exits 0", rc == 0);
	chk("init atten 30000 is not read as a trace path",
	    !strcmp(rec.trace_path, "trace_init.txt"));
	chk("init atten 30000 set BOTH channels to 30000",
	    rec.atten_calls == 2 && rec.atten_arg[0] == 30000 &&
	    rec.atten_arg[1] == 30000);
	chk("init atten 30000 set ch0 then ch1", rec.atten_ch[0] == 0 && rec.atten_ch[1] == 1);
	chk("init atten 30000 leaves the LO alone", rec.lo_calls == 0);

	/* ---- `init lo 85000000` ---- */
	run("init lo 85000000", 4, a_lo);
	chk("init lo 85000000 exits 0", rc == 0);
	chk("init lo 85000000 set TX then RX LO to 85000000",
	    rec.lo_calls == 2 && rec.lo_arg[0] == 85000000ull &&
	    rec.lo_arg[1] == 85000000ull);
	chk("init lo 85000000 leaves atten alone", rec.atten_calls == 0);

	/* ---- the full form ---- */
	run("init trace.txt atten 30000 lo 85000000 dump", 8, a_all);
	chk("full form exits 0", rc == 0);
	chk("full form uses the given trace path", !strcmp(rec.trace_path, "trace.txt"));
	chk("full form set both channels to 30000",
	    rec.atten_calls == 2 && rec.atten_arg[0] == 30000 && rec.atten_arg[1] == 30000);
	chk("full form set both LOs to 85000000",
	    rec.lo_calls == 2 && rec.lo_arg[0] == 85000000ull && rec.lo_arg[1] == 85000000ull);
	chk("full form ran the dump diagnostics", rec.fs_reads == 2);
	chk("full form flushed twice too", rec.flush_calls == 2);
	chk("full form requested the rate on both paths too",
	    rec.fs_set_calls == 2 && rec.fs_set_arg[0] == 60000000u);

	/* ---- order independence ---- */
	run("init lo 85000000 atten 30000 (reordered)", 6, a_reorder);
	chk("reordered form exits 0", rc == 0);
	chk("reordered form reached both overrides anyway",
	    rec.atten_calls == 2 && rec.lo_calls == 2 &&
	    rec.atten_arg[0] == 30000 && rec.lo_arg[0] == 85000000ull);

	/* ---- malformed ---- */
	run("init atten (no value)", 3, a_noatten);
	chk("init atten with no value exits 2", rc == 2);
	chk("init atten with no value never inits", rec.init_calls == 0);

	run("init lo (no value)", 3, a_nolo);
	chk("init lo with no value exits 2", rc == 2);
	chk("init lo with no value never inits", rec.init_calls == 0);

	run("init trace.txt bogus", 4, a_bad);
	chk("unknown trailing argument exits 2", rc == 2);
	chk("unknown trailing argument never inits", rec.init_calls == 0);

	/* ---- M1: the parse boundary.  Each of these used to exit 0 while doing
	 *      something the operator did not ask for. ---- */
	run("init atten -5", 4, a_neg);
	chk("atten -5 exits 2 (strtoul would have negated it to ULONG_MAX)", rc == 2);
	chk("atten -5 never inits", rec.init_calls == 0);
	chk("atten -5 reaches no setter", rec.atten_calls == 0);

	run("init atten 12x", 4, a_ajunk);
	chk("trailing junk after atten exits 2", rc == 2);
	chk("trailing junk after atten never inits", rec.init_calls == 0);

	run("init atten 1e5 (not a decimal integer)", 4, a_anum);
	chk("a non-integer atten spelling exits 2", rc == 2);

	run("init lo atten", 4, a_lotxt);
	chk("a non-numeric LO value exits 2 (was \"LO set to 0 Hz\", exit 0)", rc == 2);
	chk("a non-numeric LO value never inits", rec.init_calls == 0);
	chk("a non-numeric LO value reaches no setter", rec.lo_calls == 0);

	run("init lo 85000000x", 4, a_lojunk);
	chk("trailing junk after lo exits 2", rc == 2);

	run("init lo (empty value)", 4, a_loempty);
	chk("an empty LO value exits 2", rc == 2);

	/* The sign check is load-bearing for `lo` in a way it is not for `atten`:
	 * atten has an upper bound that happens to catch a negated value, but `lo`
	 * has none, so without the check `init lo -5` reaches
	 * ad9361_set_tx_lo_freq(phy, 18446744073709551611) and exits 0. */
	run("init lo -5", 4, a_loneg);
	chk("lo -5 exits 2 (strtoull negates it with no bound to catch it)", rc == 2);
	chk("lo -5 never inits", rec.init_calls == 0);
	chk("lo -5 reaches no setter", rec.lo_calls == 0);

	/* ---- I2: a failed flush must not print RESULT PASS and exit 0.
	 *      Both sites, separately: failing only the first would prove the
	 *      second site's check does not exist. ---- */
	probe_fail_flush_at = 1;
	run("bare init, FIRST flush fails (I2 site 1)", 2, a_bare);
	probe_fail_flush_at = 0;
	chk("a failed first flush exits 1", rc == 1);
	chk("a failed first flush still ran ad9361_init", rec.init_calls == 1);
	chk("a failed first flush stopped before the FIR config (site 1 is real)",
	    rec.flush_calls == 1 && rec.fir_tx == 0 && rec.fs_set_calls == 0);

	probe_fail_flush_at = 2;
	run("bare init, SECOND flush fails (I2 site 2)", 2, a_bare);
	probe_fail_flush_at = 0;
	chk("a failed second flush exits 1", rc == 1);
	chk("a failed second flush ran the FIR and the rate request first "
	    "(site 2 is real)",
	    rec.flush_calls == 2 && rec.fir_tx == 1 && rec.fir_rx == 1 &&
	    rec.fs_set_calls == 2);

	/* A non-keyword first argument IS a trace path, by design. */
	run("init bogus (bogus is a trace filename, not a keyword)", 3, a_onearg);
	chk("a non-keyword argv[2] is taken as the trace path",
	    rc == 0 && !strcmp(rec.trace_path, "bogus"));

	/* ---- the deleted subcommands are gone ---- */
	run("dump (standalone, now deleted)", 2, a_dump);
	chk("standalone dump is no longer a subcommand", rc == 2 && rec.init_calls == 0);

	run("atten 0 (standalone, now deleted)", 3, a_a0);
	chk("standalone atten is no longer a subcommand", rc == 2 && rec.init_calls == 0);

	run("lo 85000000 (standalone, now deleted)", 3, a_l0);
	chk("standalone lo is no longer a subcommand", rc == 2 && rec.init_calls == 0);

	printf("\n=== %s ===\n", fails ? "INIT-PARSE PROBE FAIL" : "INIT-PARSE PROBE OK");
	return fails ? 1 : 0;
}
EOF

cat > "$WORK/repeat.c" <<'EOF'
/* Bounded attempt at the repeatability question: run the `plcfg` + `init`
 * sequence three times in ONE process, against the real vendor ad9361_init and
 * test/fake_pl.c.
 *
 * Ruling R7 folds atten/lo into `init`, so a Task 5 run re-initialises the part
 * roughly six times.  Nothing in hand proves a second and third ad9361_init
 * succeed and land in an equivalent state, and the host cannot settle it -- see
 * the output this program prints.  What it CAN settle is that a repeat is not
 * state-dependent in our own code: no hang, no crash, no divergence between
 * runs.  That is what the assertion below checks.
 *
 * This is HARDER than the board case and is not equivalent to it: rf_main.c's
 * `init` pulses RESETB low then high before each ad9361_init, so on the board
 * every run starts from a hardware-reset part.  Here there is no reset pulse at
 * all between runs -- only fake_pl_reset(), which models the PL apertures, not
 * the chip.  fake_pl.c is a register file, not the AD9361; a PASS here would say
 * only that the driver's own software path is re-entrant, and it would NOT be
 * board evidence.  Task 7 must run `init` twice on hardware and confirm both
 * succeed with agreeing readbacks. */
#include <stdio.h>
#include "platform_rf.h"
#include "platform.h"
#include "ad9361_api.h"
#include "test/fake_pl.h"

int rf_main_entry(int argc, char **argv);

#define RUNS 3

int main(void)
{
	char *init_argv[]  = { "board_app", "init", NULL };
	char *plcfg_argv[] = { "board_app", "plcfg", NULL };
	int first_init = -1, same = 1, plcfg_ok = 1, i;

	printf("=== plcfg + init, %d times in one process ===\n", RUNS);
	for (i = 0; i < RUNS; i++) {
		int prc, irc;

		/* Task 5's per-invocation sequence: plcfg then init.  init asserts
		 * phy_mode == 1, which only plcfg writes. */
		fake_pl_reset();
		prc = rf_main_entry(2, plcfg_argv);
		irc = rf_main_entry(2, init_argv);
		printf(">>> run %d: plcfg=%d init=%d\n", i + 1, prc, irc);
		if (prc != 0) plcfg_ok = 0;
		if (first_init < 0) first_init = irc;
		else if (irc != first_init) same = 0;
	}

	printf("=== %s ===\n",
	       (plcfg_ok && same) ? "REPEAT PROBE OK" : "REPEAT PROBE FAIL");
	if (!plcfg_ok) printf("  plcfg did not succeed on every run\n");
	if (!same)     printf("  init's result differed between runs: state-dependent\n");
	return (plcfg_ok && same) ? 0 : 1;
}
EOF

# ------------------------------------------------------------------ compiling
CFLAGS_APP="-DPLATFORM_HOST -UWIN32 -I$APP -I$APP/vendor -O1 -g -Wall -Wextra -Wno-unused-parameter"
# -w on the vendor sources: the only diagnostics left in them are
# vendor/common.h's errno redefinitions, in files copied verbatim that must not
# be edited.  -include is how test/strsep_shim.h reaches vendor/ad9361.c.
CFLAGS_VENDOR="$CFLAGS_APP -w -include $APP/test/strsep_shim.h"

echo "--- building the host probe ---"
# build <label> <out.o> <src> [extra flags...]
build() {
	local label="$1" out="$2" src="$3"
	shift 3
	if ! gcc "$@" -c "$src" -o "$out" 2>"$WORK/.cc"; then
		echo "BUILD FAILED ($label):"
		sed 's/^/    /' "$WORK/.cc"
		exit 1
	fi
}
build "rf_main.c"       "$WORK/rf_main_entry.o" "$APP/rf_main.c" \
      $CFLAGS_APP -Dmain=rf_main_entry
build "platform_rf.c"   "$WORK/platform_rf.o"   "$APP/platform_rf.c"   $CFLAGS_APP
build "selftest.c"      "$WORK/selftest.o"      "$APP/selftest.c"      $CFLAGS_APP
build "test/fake_pl.c"  "$WORK/fake_pl.o"       "$APP/test/fake_pl.c"  $CFLAGS_APP
build "vendor/ad9361.c"     "$WORK/ad9361.o"     "$APP/vendor/ad9361.c"     $CFLAGS_VENDOR
build "vendor/ad9361_api.c" "$WORK/ad9361_api.o" "$APP/vendor/ad9361_api.c" $CFLAGS_VENDOR
build "vendor/ad9361_conv.c" "$WORK/ad9361_conv.o" "$APP/vendor/ad9361_conv.c" $CFLAGS_APP
build "vendor/util.c"       "$WORK/util.o"       "$APP/vendor/util.c"       $CFLAGS_APP
build "test/strsep_shim.c"  "$WORK/strsep_shim.o" "$APP/test/strsep_shim.c" $CFLAGS_APP
build "fwd.c"           "$WORK/fwd.o"           "$WORK/fwd.c"          $CFLAGS_APP
build "auditfail.c"     "$WORK/auditfail.o"     "$WORK/auditfail.c"    $CFLAGS_APP
build "stubs.c"         "$WORK/stubs.o"         "$WORK/stubs.c"        $CFLAGS_APP
build "initstub.c"      "$WORK/initstub.o"      "$WORK/initstub.c"     $CFLAGS_APP
build "initparse.c"     "$WORK/initparse.o"     "$WORK/initparse.c"    $CFLAGS_APP
build "repeat.c"        "$WORK/repeat.o"        "$WORK/repeat.c"       $CFLAGS_APP

# The real vendor driver in every binary that is meant to exercise it.
REAL="$WORK/platform_rf.o $WORK/fake_pl.o $WORK/selftest.o \
      $WORK/ad9361.o $WORK/ad9361_api.o $WORK/ad9361_conv.o $WORK/util.o \
      $WORK/strsep_shim.o"

link() {
	# link <label> <out> <objs...>
	local label="$1"; shift
	local out="$1"; shift
	if ! gcc -O1 -o "$out" "$@" 2>"$WORK/.ld"; then
		echo "LINK FAILED ($label):"
		sed 's/^/    /' "$WORK/.ld"
		exit 1
	fi
}
# shellcheck disable=SC2086
link "probe_dispatch"  "$WORK/probe_dispatch"  $WORK/fwd.o $WORK/rf_main_entry.o $REAL
# shellcheck disable=SC2086
link "probe_auditfail" "$WORK/probe_auditfail" $WORK/fwd.o $WORK/rf_main_entry.o \
     $WORK/auditfail.o $WORK/stubs.o $WORK/fake_pl.o
link "probe_init"      "$WORK/probe_init"      $WORK/initparse.o $WORK/rf_main_entry.o \
     $WORK/initstub.o
# shellcheck disable=SC2086
link "probe_repeat"    "$WORK/probe_repeat"    $WORK/repeat.o $WORK/rf_main_entry.o $REAL

DISPATCH="$WORK/probe_dispatch"
AUDFAIL="$WORK/probe_auditfail"

echo "  the real vendor driver linked into probe_dispatch and probe_repeat"

# A real `init` calls rf_trace_open(), which on the host writes a trace file
# with a RELATIVE name -- run the probes from the scratch dir so that file lands
# somewhere the trap deletes, instead of littering the working tree.  (An
# earlier ad-hoc version of this harness left a 400 KB trace_init.txt in the
# repository; that is how this was noticed.)  Everything below uses absolute
# paths, so the cd changes nothing else.
cd "$WORK"

# ----------------------------------------------------------------- assertions
pass=0
fail=0
ok()  { pass=$((pass + 1)); printf 'PASS %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf 'FAIL %s\n' "$1"; }

chk_eq() {
	if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want [$3], got [$2])"; fi
}
chk_has() {
	if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"
	else bad "$1 (no [$3] in the output)"; fi
}
chk_not_has() {
	if printf '%s' "$2" | grep -qF -- "$3"; then bad "$1 (found [$3])"
	else ok "$1"; fi
}

# Task 6's audit_report.py reads the LAST whitespace-separated token of the
# line and does int(token, 16).  Parse the same way here, so the contract is
# checked from the consumer's side rather than from the format string.
last_tok() { local t=""; local w; for w in $1; do t="$w"; done; printf '%s' "$t"; }

# call <binary> <args...>; PROBE_SEED / PROBE_STATUS are honoured from the
# environment.  Sets CAP_OUT, CAP_ERR, CAP_RC and echoes what it saw.
call() {
	local bin="$1"; shift
	CAP_OUT="$(PROBE_SEED="${PROBE_SEED:-}" PROBE_STATUS="${PROBE_STATUS:-}" \
	           "$bin" "$@" 2>"$WORK/.stderr")"
	CAP_RC=$?
	CAP_ERR="$(cat "$WORK/.stderr")"
	printf '  $ board_app %s   [rc=%d]\n' "$*" "$CAP_RC"
	if [ -n "$CAP_OUT" ]; then printf '%s\n' "$CAP_OUT" | sed 's/^/      | /'; fi
	if [ -n "$CAP_ERR" ]; then printf '%s\n' "$CAP_ERR" | sed 's/^/      ! /'; fi
}

echo
echo "--- 1/4  dispatch + register audit, against the REAL vendor driver ---"

PROBE_SEED=""; PROBE_STATUS=""

call "$DISPATCH" audit-begin
chk_eq  "audit-begin exits 0" "$CAP_RC" "0"
chk_eq  "audit-begin announces the raw path" "$CAP_OUT" \
        "audit-begin: raw SPI path open (no driver instance involved)"

# The load-bearing R6 claim: ad9361_spi_read works on a zeroed struct spi_device.
PROBE_SEED="3F0=A5"
call "$DISPATCH" audit 3F0
chk_eq  "audit 3F0 exits 0 through a zeroed spi_device" "$CAP_RC" "0"
chk_eq  "audit 3F0 prints the seeded value" "$CAP_OUT" "audit 0x3F0 = 0xA5"
audit_tok="$(last_tok "$CAP_OUT")"
chk_eq  "Task 6's parser reads that line back as 165" \
        "$(( 16#${audit_tok#0x} ))" "165"

# strtoul(_, NULL, 16) accepts an 0x prefix; Task 6 must accept both spellings.
call "$DISPATCH" audit 0x3F0
chk_eq  "audit 0x3F0 (0x-prefixed argument) reads the same register" "$CAP_OUT" \
        "audit 0x3F0 = 0xA5"

# The contrast that section 2 exists for: a register that genuinely reads 0
# prints 0x00 AND exits 0, whereas a dead transport prints nothing and exits
# non-zero.  Those two must never be confusable.
call "$DISPATCH" audit 0C8
chk_eq  "an unseeded register reads as a genuine 0x00, with exit 0" \
        "$CAP_OUT|$CAP_RC" "audit 0x0C8 = 0x00|0"

call "$DISPATCH" audit
chk_eq  "audit with no address exits 2" "$CAP_RC" "2"

call "$DISPATCH" audit-end
chk_eq  "audit-end exits 0" "$CAP_RC" "0"
chk_eq  "audit-end prints its one line" "$CAP_OUT" "audit-end"

PROBE_SEED=""
# The model returns ONE CONSTANT status word and never advances win_seq, so the
# freshness loop always times out here.  That is the model being a register file
# rather than the RTL, and the consequence is a real coverage limit: the host can
# exercise the two NOT-fresh outcomes and not the fresh one.  The fresh path's
# arithmetic (RF_STATUS_F_DATA_CLK_HZ) is covered by test_platform_rf.c instead
# (test/test_platform_rf.c:191-193).  Stated here rather than papered over.
PROBE_STATUS="0F666657"   # WIN_LOG2=15, win_cycles=26214, seq=5, clk_seen=1
call "$DISPATCH" status
chk_eq  "status exits 0" "$CAP_RC" "0"
chk_has "status decodes clk_seen from the raw word" "$CAP_OUT" "clk_seen   = 1"
chk_has "status decodes win_seq from the raw word" "$CAP_OUT" "win_seq    = 5"
chk_has "status decodes win_cycles from the raw word" "$CAP_OUT" "win_cycles = 26214"

# M2: a word that claims a clock AND a completed window, but whose win_seq never
# moved, is the observation channel failing.  It must not be printed as a rate --
# Task 5's C3 parses this line to adjudicate fs against f_DATA_CLK and would
# otherwise adjudicate on a stale number.
chk_has     "a stuck window is reported STALE" "$CAP_OUT" "STALE (window never advanced)"
chk_has     "a stuck window prints no rate" "$CAP_OUT" "f_DATA_CLK = STALE"
chk_not_has "a stuck window does not print the stale 39999389 Hz" "$CAP_OUT" "39999389"

PROBE_STATUS="00000000"
call "$DISPATCH" status
chk_has     "no clock at all is a measurement, not a fault" "$CAP_OUT" "f_DATA_CLK = NO CLOCK"
chk_not_has "the no-clock case is not labelled STALE" "$CAP_OUT" "STALE"

# clk_seen set but the window counted nothing: still no clock.  A decoder that
# trusted the sticky bit alone would report a number here.
PROBE_STATUS="00000001"
call "$DISPATCH" status
chk_has "clk_seen=1 with win_cycles=0 is still NO CLOCK" "$CAP_OUT" \
        "f_DATA_CLK = NO CLOCK"

PROBE_STATUS="00000000"
call "$DISPATCH" plcfg
chk_eq  "plcfg passes its negative criterion against a silent STATUS" "$CAP_RC" "0"
chk_has "plcfg reports the negative criterion" "$CAP_OUT" \
        "RESULT PASS (negative criterion)"

# Falsify the instrument: a STATUS that claims a clock while the BBPLL is
# unprogrammed means every later reading is untrustworthy, so plcfg must FAIL.
PROBE_STATUS="0F666657"
call "$DISPATCH" plcfg
chk_eq  "plcfg FAILS against a STATUS that lies about the clock" "$CAP_RC" "1"
chk_has "plcfg says why" "$CAP_OUT" "RESULT FAIL (negative criterion)"

# --- criterion 1.5 restated as a MAGNITUDE test (plan defect E19) ------------
#
# The word below is not invented: it is the one THIS BOARD actually returned
# before the BBPLL was programmed (STATUS = 0x0F00BF3D, win_cycles = 191 over
# 2^15 FCLK0 cycles -> 0.291 MHz).  The first cut of the criterion failed it,
# because it tested `clk_seen || win_cycles != 0` and on an unterminated
# differential pair both of those trip on coupling noise.  That is a criterion
# failing a healthy instrument, and these two cases are the regression: the old
# code cannot pass the first one.
PROBE_STATUS="0F00BF3D"
call "$DISPATCH" plcfg
chk_eq  "plcfg PASSES the board's own pre-init word (E19 regression)" "$CAP_RC" "0"
chk_has "plcfg reports it as the measured magnitude" "$CAP_OUT" \
        "RESULT PASS (negative criterion)"

# The threshold itself, from both sides.  f_DATA_CLK = win_cycles * 50e6 / 2^15,
# so 655 counts is 999451 Hz (under the 1 MHz ceiling) and 656 is 1000977 Hz
# (over it).  Pinning the exact boundary means a later edit that quietly moves
# the ceiling by a factor has to also change this line.
PROBE_STATUS="0F028F01"   # win_cycles = 655 -> 0.999 MHz, just below
call "$DISPATCH" plcfg
chk_eq  "655 counts is below the ceiling" "$CAP_RC" "0"

PROBE_STATUS="0F029001"   # win_cycles = 656 -> 1.001 MHz, just above
call "$DISPATCH" plcfg
chk_eq  "656 counts is at or above the ceiling" "$CAP_RC" "1"

# Between the measured noise floor (0.252 MHz) and the ceiling: reported as a
# grey zone, NOT silently accepted, and still a PASS because it is under the
# ceiling.  The point is that it is visible in the output.
PROBE_STATUS="0F019001"   # win_cycles = 400 -> 0.610 MHz
call "$DISPATCH" plcfg
chk_eq  "a grey-zone reading is still under the ceiling" "$CAP_RC" "0"
chk_has "a grey-zone reading is reported, not smoothed over" "$CAP_OUT" \
        "grey zone"

PROBE_STATUS=""
call "$DISPATCH" phase_inc 256
chk_eq  "phase_inc exits 0 with no phy in the process" "$CAP_RC" "0"
chk_has "phase_inc writes and reads back the PL register" "$CAP_OUT" \
        "phase_inc set to 256 (readback 256)"

call "$DISPATCH"
chk_eq  "no subcommand exits 2" "$CAP_RC" "2"
chk_has "no subcommand prints usage on stderr" "$CAP_ERR" "usage: board_app <subcommand>"

call "$DISPATCH" bogus
chk_eq  "unknown subcommand exits 2" "$CAP_RC" "2"

# R7's deletions, checked from the outside.
call "$DISPATCH" dump
chk_eq  "the standalone \`dump\` subcommand is gone" "$CAP_RC" "2"
call "$DISPATCH" atten 0
chk_eq  "the standalone \`atten\` subcommand is gone" "$CAP_RC" "2"
call "$DISPATCH" lo 80000000
chk_eq  "the standalone \`lo\` subcommand is gone" "$CAP_RC" "2"
chk_has "usage documents the folded-in form" "$CAP_ERR" \
        "init [trace.txt] [atten <mdB>] [lo <Hz>] [dump]"

echo
echo "--- 2/4  register audit failure paths (they must NOT print a value) ---"

# 0xFF is the hazard: ad9361_spi_read returns a negative int32_t on failure and
# (int32_t)-1 & 0xFF is 0xFF, a perfectly plausible register value.  A silent
# failure would therefore be indistinguishable from a real read.
echo "  (control: with nothing forced to fail, the read succeeds)"
call "$AUDFAIL" audit 3F0
chk_eq  "the failure-injection build reads a value when nothing fails" \
        "$CAP_OUT|$CAP_RC" "audit 0x3F0 = 0x5A|0"

PROBE_FAIL_INIT=1
echo "  (spi_init forced to fail)"
CAP_OUT="$(PROBE_FAIL=init "$AUDFAIL" audit 3F0 2>"$WORK/.stderr")"
CAP_RC=$?
CAP_ERR="$(cat "$WORK/.stderr")"
printf '  $ PROBE_FAIL=init board_app audit 3F0   [rc=%d]\n' "$CAP_RC"
printf '%s\n' "$CAP_ERR" | sed 's/^/      ! /'
chk_eq  "spi_init failure exits non-zero" "$CAP_RC" "1"
chk_eq  "spi_init failure prints NOTHING on stdout" "$CAP_OUT" ""
chk_has "spi_init failure explains itself on stderr" "$CAP_ERR" "spi_init failed"

echo "  (spi_init forced to fail, via audit-begin)"
CAP_OUT="$(PROBE_FAIL=init "$AUDFAIL" audit-begin 2>"$WORK/.stderr")"
CAP_RC=$?
CAP_ERR="$(cat "$WORK/.stderr")"
printf '  $ PROBE_FAIL=init board_app audit-begin   [rc=%d]\n' "$CAP_RC"
printf '%s\n' "$CAP_ERR" | sed 's/^/      ! /'
chk_eq  "audit-begin fails loud when the SPI path will not open" "$CAP_RC" "1"
chk_eq  "audit-begin prints nothing on stdout when it fails" "$CAP_OUT" ""

echo "  (spi_init succeeds, the register read fails)"
CAP_OUT="$(PROBE_FAIL=read "$AUDFAIL" audit 3F0 2>"$WORK/.stderr")"
CAP_RC=$?
CAP_ERR="$(cat "$WORK/.stderr")"
printf '  $ PROBE_FAIL=read board_app audit 3F0   [rc=%d]\n' "$CAP_RC"
printf '%s\n' "$CAP_ERR" | sed 's/^/      ! /'
chk_eq  "a failed read exits non-zero" "$CAP_RC" "1"
chk_eq  "a failed read prints NOTHING on stdout -- in particular not 0xFF" "$CAP_OUT" ""
chk_has "a failed read says so on stderr" "$CAP_ERR" "read failed"

echo
echo "--- 3/4  init argument parsing and R7 override dispatch ---"
if "$WORK/probe_init" > "$WORK/.init" 2>&1; then init_rc=0; else init_rc=$?; fi
# The probe runs 15 invocations and rf_main prints its whole criteria block for
# each one, so the raw output is mostly repetition.  On success show the
# load-bearing lines -- the verdicts, what the stubs recorded (`>>>`), and the
# criterion 2.5 readbacks this round added, which are the evidence for R14; on
# failure show everything, because then the repetition is the diagnosis.  Either
# way the whole log is kept at $WORK/.init until the trap removes it.
if [ "$init_rc" = "0" ]; then
	grep -E '^(PASS|FAIL|>>>|=== )|^sampling rate requested|sampling rate readback|^override:|flush failed' \
	     "$WORK/.init" | sed 's/^/  /'
else
	sed 's/^/  /' "$WORK/.init"
fi
i_pass="$(grep -c '^PASS ' "$WORK/.init")"
i_fail="$(grep -c '^FAIL ' "$WORK/.init")"
pass=$((pass + i_pass))
fail=$((fail + i_fail))
if [ "$init_rc" = "0" ]; then ok "the init-parse probe exited 0"
else bad "the init-parse probe exited $init_rc"; fi

# Criterion 2.5 is judged inside C, but its verdict is printed, so it is also
# checkable from here: on every one of those invocations the readback must have
# matched the requested 60 MSPS.  This is what catches the R14 calls being
# deleted or the requested value being changed -- the stub starts the model at
# the struct's 40 MSPS, so either mutation surfaces as MISMATCH.
chk_not_has "criterion 2.5 reported MATCH on every invocation" \
            "$(cat "$WORK/.init")" "MISMATCH"

echo
echo "--- 4/4  repeatability (INFORMATIONAL -- see the note below) ---"
if "$WORK/probe_repeat" > "$WORK/.rep" 2>&1; then rep_rc=0; else rep_rc=$?; fi
if [ "$rep_rc" = "0" ]; then
	# Keep the vendor's own diagnostics and the per-run verdicts; the three
	# copies of rf_main's criteria block add nothing.
	grep -E '^(>>>|=== |RESULT |Calibration|ad9361_init)' "$WORK/.rep" | sed 's/^/  /'
else
	sed 's/^/  /' "$WORK/.rep"
fi
if [ "$rep_rc" = "0" ]; then ok "repeated plcfg+init does not diverge in our own code"
else bad "repeated plcfg+init diverged (exit $rep_rc)"; fi
cat <<'NOTE'
  NOTE: on this host the real ad9361_init stops inside the chip calibration
  sequence (Calibration TIMEOUT, then ad9361_init : AD936x initialization
  error) and returns -19, because test/fake_pl.c is a register-file model and
  not an AD9361.  That verdict says nothing about the part.  So the question
  R7 depends on -- "do a second and third ad9361_init succeed and land in an
  equivalent state, as a Task 5 run's ~6 re-inits require?" -- is
  NOT VERIFIED here and cannot be: Task 7 must run `init` twice in a row on
  hardware and confirm both succeed with agreeing readbacks.
NOTE

echo
if [ "$fail" != "0" ]; then
	echo "=== HOST-PROBE FAIL ($fail failed, $pass passed) ==="
	exit 1
fi
echo "=== HOST-PROBE OK ($pass checks) ==="
