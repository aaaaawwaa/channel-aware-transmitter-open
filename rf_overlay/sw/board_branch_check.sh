#!/usr/bin/env bash
# board_branch_check.sh -- exercise platform_rf.c's BOARD branch on the host.
#
# Why this exists: platform_rf.c has two branches.  The host branch
# (-DPLATFORM_HOST) goes through test/fake_pl.c and is covered by `make
# host-test`.  The BOARD branch is the one that actually runs on the hardware,
# and it was only ever syntax-checked -- MinGW has no sys/mman.h, so it could not
# be compiled, let alone run, on the development host.  A typo there would have
# surfaced for the first time on the board, during the bring-up, which is the
# worst place to find one.
#
# This script closes that gap: it builds the board branch against small POSIX
# shims and drives it against a simulated /dev/mem page.  It is a smoke test, not
# a substitute for the board -- it proves the branch compiles and that its
# register arithmetic and pin shadow behave, not that /dev/mem works.
#
# Run from anywhere:  bash rf_overlay/sw/board_branch_check.sh

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP="$REPO/rf_overlay/sw/board_app"
# NOT mktemp: this machine's user name is non-ASCII, and the resulting
# %TEMP% path makes ld.exe fail to create its output file.  A repo-local
# scratch dir avoids the encoding problem entirely and is gitignored.
WORK="$REPO/rf_overlay/sw/.board_branch_check"
rm -rf "$WORK"
mkdir -p "$WORK/inc/sys"
trap 'rm -rf "$WORK"' EXIT

# ---- POSIX shims: types and prototypes only, never linked against ----
cat > "$WORK/inc/sys/mman.h" <<'SHIM'
#ifndef SHIM_SYS_MMAN_H
#define SHIM_SYS_MMAN_H
#include <stddef.h>
#define PROT_READ   0x1
#define PROT_WRITE  0x2
#define MAP_SHARED  0x01
#define MAP_FAILED  ((void *)-1)
extern unsigned char *shim_region;
void *shim_mmap(void *addr, size_t len, int prot, int flags, int fd, long off);
#define mmap(a,l,p,f,d,o) shim_mmap((a),(l),(p),(f),(d),(o))
#endif
SHIM

cat > "$WORK/inc/fcntl.h" <<'SHIM'
#ifndef SHIM_FCNTL_H
#define SHIM_FCNTL_H
#define O_RDWR   02
#define O_SYNC   010000
int open(const char *path, int flags, ...);
int close(int fd);
#endif
SHIM

# ---- harness: drives the board branch against a simulated /dev/mem page ----
cat > "$WORK/harness.c" <<'HARNESS'
/* Build platform_rf.c WITHOUT -DPLATFORM_HOST and drive it, so the branch that
 * runs on hardware is actually executed rather than merely parsed. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

unsigned char *shim_region;
void *shim_mmap(void *a, size_t l, int p, int f, int d, long o)
{ (void)a; (void)l; (void)p; (void)f; (void)d; (void)o; return shim_region; }

/* Intercept the /dev/mem open: it must not be static, or it will not win the
 * symbol at link time and the real one will be called instead. */
int open(const char *path, int flags, ...) { (void)path; (void)flags; return 3; }
int close(int fd) { (void)fd; return 0; }

#include "platform_rf.h"
#include "platform.h"
#include "ad9361.h"

static int fails;
static void chk(const char *n, int c)
{
	if (c) printf("PASS %s\n", n);
	else { fails++; printf("FAIL %s\n", n); }
}

int main(void)
{
	static uint32_t mem[0x1000];
	shim_region = (unsigned char *)mem;
	memset(mem, 0, sizeof mem);

	printf("=== board-branch exercise (platform_rf.c WITHOUT PLATFORM_HOST) ===\n");

	/* spi_init asserts the SPI mode and probes the aperture.  The probe must
	 * NOT read 0xDEADBEEF, which is what the RTL returns for an unmapped
	 * register -- that value is how a wrong base address shows up. */
	chk("spi_init(pha=1,pol=0) succeeds", spi_init(0, 1, 0) == 0);
	chk("spi_init(pha=0,pol=0) is rejected", spi_init(0, 0, 0) != 0);
	chk("spi_init(pha=1,pol=1) is rejected", spi_init(0, 1, 1) != 0);

	/* gpio read-modify-write through the real MMIO path.  Pin == bit, and
	 * resetb is bit 2, so enable|txnrx = 0x03 and adding resetb gives 0x07. */
	gpio_init(0);
	gpio_set_value(RF_GPIO_TXNRX, 1);
	gpio_set_value(RF_GPIO_ENABLE, 1);
	gpio_set_value(RF_GPIO_RESETB, 0);
	chk("gpio shadow after enable|txnrx == 0x03", rf_gpio_shadow() == 0x03u);
	gpio_set_value(RF_GPIO_RESETB, 1);
	chk("gpio shadow after reset release == 0x07", rf_gpio_shadow() == 0x07u);

	/* gpio_is_valid is tightened to the nine pins that exist.  The reference
	 * platform.c accepts -1, and the reference main.c passes -1 for three
	 * unused pins -- so an un-tightened version would drive a pin that is not
	 * there. */
	chk("gpio_is_valid(0)", gpio_is_valid(0) == true);
	chk("gpio_is_valid(8)", gpio_is_valid(8) == true);
	chk("gpio_is_valid(9) is false", gpio_is_valid(9) == false);
	chk("gpio_is_valid(-1) is false", gpio_is_valid(-1) == false);

	/* the axi_ctrl_0 accessors must land in the aperture and read back */
	rf_reg_write(RF_REG_PHY_MODE, 1);
	chk("rf_reg round-trip through the board MMIO path",
	    rf_reg_read(RF_REG_PHY_MODE) == 1u);

	printf("=== %s ===\n", fails ? "BOARD-BRANCH FAIL" : "BOARD-BRANCH OK");
	return fails ? 1 : 0;
}
HARNESS

echo "--- compiling the board branch ---"
# -w: the only diagnostics left are vendor/common.h's errno redefinitions, which
# are in files we copy verbatim and must not edit.
gcc -o "$WORK/harness" "$WORK/harness.c" "$APP/platform_rf.c" \
    -I"$WORK/inc" -I"$APP" -I"$APP/vendor" -w || {
    echo "BUILD FAILED -- the board branch does not compile"
    exit 1
}

echo "--- running ---"
"$WORK/harness"