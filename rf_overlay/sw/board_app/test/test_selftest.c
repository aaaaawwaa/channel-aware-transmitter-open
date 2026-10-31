/* test_selftest.c -- run the board's own self-check on the host, against
 * fake_pl.c.  Same source file, same criteria; only the MMIO target differs.
 *
 * Three scenarios, because a self-check that only ever passes proves nothing:
 *   1. a healthy part must produce RESULT PASS
 *   2. a part that ignores writes (the CNT bug's signature) must produce FAIL
 *   3. a part that is not an AD9361 must fail S1 and write nothing
 * Scenarios 2 and 3 are what make scenario 1 meaningful. */
#include <stdio.h>
#include "fake_pl.h"
#include "platform_rf.h"

int rf_selftest(void);

static int fails;

int main(void)
{
	int rc;

	printf("=== test_selftest ===\n");

	/* NOTE on reading this output: scenarios 2 and 3 below deliberately drive
	 * rf_selftest() into failure, so its OWN per-check lines print "FAIL ..."
	 * (e.g. "FAIL S2b PRODUCT_ID answered inside the polling window") even
	 * when everything is working.  Those inner FAIL lines are the EXPECTED
	 * inner verdict; the outer PASS line on the next line is the assertion
	 * that they happened.  Read the outer lines and the final summary -- a
	 * bare inner "FAIL" here is not a regression. */

	/* ---- 1. healthy ---- */
	fake_pl_reset();
	fake_pl_set_product_id(0x0Au);        /* this board's rev 2 */
	rc = rf_selftest();
	if (rc == 0) printf("PASS healthy part -> RESULT PASS\n");
	else       { fails++; printf("FAIL healthy part should pass, rc=%d\n", rc); }

	/* ---- 2. the CNT bug's signature: the part answers reads but drops every
	 *      write. ---- */
	fake_pl_reset();
	fake_pl_set_product_id(0x0Au);
	fake_pl_set_reject_all_writes(1);
	rc = rf_selftest();
	if (rc != 0) printf("PASS write-dropping part -> RESULT FAIL\n");
	else         { fails++; printf("FAIL write-dropping part must not pass\n"); }
	fake_pl_set_reject_all_writes(0);

	/* ---- 3. a part that is not an AD9361 must fail S1 without any write ---- */
	fake_pl_reset();
	fake_pl_set_product_id(0x00u);        /* bits[7:3] = 0, not 0x08 */
	rc = rf_selftest();
	if (rc != 0) printf("PASS non-AD9361 -> RESULT FAIL\n");
	else         { fails++; printf("FAIL non-AD9361 must not pass\n"); }

	printf("=== %s ===\n", fails ? "SELFTEST HARNESS FAIL" : "SELFTEST HARNESS OK");
	return fails ? 1 : 0;
}
