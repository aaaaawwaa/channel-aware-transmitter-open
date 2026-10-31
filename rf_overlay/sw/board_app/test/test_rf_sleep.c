/* Host tests for the bounded, reportable sleep.
 *
 * The point of these is that the DEVICE build's wait logic is compiled here too:
 * a sleep that cannot be exercised on the host is a wait whose failure mode is
 * unverified, which is the thing this whole feature is meant to avoid. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>

#include "rf_sleep.h"

static int failures;
static int checks;

static void check(const char *name, int ok)
{
    ++checks;
    if (ok) {
        printf("PASS %s\n", name);
    } else {
        ++failures;
        printf("FAIL %s\n", name);
    }
}

/* ---- scripted syscalls ---------------------------------------------------- */

static uint64_t script[8];      /* how much each attempt should consume */
static int script_n, script_i;
static int script_result[8];    /* per-attempt return value */
static int calls;
static uint64_t asked[8];

static int scripted(uint64_t *ns)
{
    int i = script_i < script_n ? script_i : script_n - 1;
    uint64_t gave = *ns;

    asked[calls < 8 ? calls : 7] = gave;
    if (calls < 8)
        ++calls;
    ++script_i;
    if (script_result[i] != 0)
        return script_result[i];
    if (script[i] >= gave)
        *ns = 0;
    else
        *ns = gave - script[i];
    return 0;
}

static void arm(const uint64_t *consume, const int *result, int n)
{
    int i;
    for (i = 0; i < 8; ++i) {
        script[i] = i < n ? consume[i] : 0;
        script_result[i] = i < n ? result[i] : 0;
    }
    script_n = n;
    script_i = 0;
    calls = 0;
}

/* A syscall that never accounts for anything: the loop must refuse to spin. */
static int stuck(uint64_t *ns)
{
    (void)ns;
    ++calls;
    return 0;
}

static int hard_failure(uint64_t *ns)
{
    (void)ns;
    ++calls;
    return -1;
}

/* Makes no progress for the first `finish_at - 1` calls, then completes. */
static int finish_at_attempt;
static int finishing(uint64_t *ns)
{
    ++calls;
    if (calls >= finish_at_attempt)
        *ns = 0ull;
    return 0;
}

int main(void)
{
    printf("=== test_rf_sleep ===\n");

    check("a missing syscall is a failure, not a silent no-op",
          rf_sleep_us(NULL, 1000) == -1);

    {
        const uint64_t consume[1] = { 0 };
        const int result[1] = { 0 };
        arm(consume, result, 1);
        check("a zero wait succeeds without calling the syscall",
              rf_sleep_us(scripted, 0) == 0 && calls == 0);
    }

    {
        const uint64_t consume[1] = { 1000000 };
        const int result[1] = { 0 };
        arm(consume, result, 1);
        check("a completed wait reports success after exactly one call",
              rf_sleep_us(scripted, 1000) == 0 && calls == 1);
        check("the request reaches the syscall in nanoseconds",
              asked[0] == 1000000ull);
    }

    {
        /* Three interruptions that each consume a third, then the remainder. */
        const uint64_t consume[4] = { 200000, 200000, 200000, 400000 };
        const int result[4] = { 0, 0, 0, 0 };
        arm(consume, result, 4);
        check("an interrupted wait RESUMES rather than restarting",
              rf_sleep_us(scripted, 1000) == 0 && calls == 4);
        check("the resumed wait asks only for what is left",
              asked[0] == 1000000ull && asked[1] == 800000ull &&
              asked[2] == 600000ull && asked[3] == 400000ull);
    }

    {
        const uint64_t consume[1] = { 0 };
        const int result[1] = { -1 };
        arm(consume, result, 1);
        check("a hard syscall error is reported, not swallowed",
              rf_sleep_us(scripted, 1000) == -1);
    }

    {
        /* First attempt consumes half, the SECOND fails hard: the failure must
         * still be reported even though a legitimate interruption preceded it. */
        const uint64_t consume[2] = { 500000, 0 };
        const int result[2] = { 0, -1 };
        arm(consume, result, 2);
        check("a hard error after an interruption is still reported",
              rf_sleep_us(scripted, 1000) == -1 && calls == 2);
    }

    {
        /* A signal can arrive before any time is consumed.  That is not a
         * failure -- the attempt budget, not a strict progress rule, is what
         * keeps a bounded wait bounded. */
        const uint64_t consume[3] = { 0, 0, 1000000 };
        const int result[3] = { 0, 0, 0 };
        arm(consume, result, 3);
        check("an interruption that consumed no time is tolerated",
              rf_sleep_us(scripted, 1000) == 0 && calls == 3);
    }

    calls = 0;
    check("a syscall that never finishes fails after the attempt budget",
          rf_sleep_us(stuck, 1000) == -1 && calls == (int)RF_SLEEP_MAX_ATTEMPTS);
    calls = 0;
    check("a syscall that always errors fails immediately",
          rf_sleep_us(hard_failure, 5000) == -1 && calls == 1);

    {
        const uint64_t consume[1] = { 5000000 };
        const int result[1] = { 0 };
        arm(consume, result, 1);
        check("the ceiling value converts to the right nanosecond request",
              rf_sleep_us(scripted, 5000) == 0 && asked[0] == 5000000ull);
    }

    /* The adapter's interruption decision is separated from the syscall so that a
     * platform whose sleep cannot be interrupted can still test it.  These are the
     * assertions that forbid the false measurement the header names. */
    {
        uint64_t ns = 1000000ull;
        check("a completed attempt leaves nothing outstanding",
              rf_sleep_attempt_outcome(&ns, 1, 0, 0, 0) == 0 && ns == 0ull);
    }
    {
        uint64_t ns = 1000000ull;
        check("an interrupted attempt owes what the sleep wrote back",
              rf_sleep_attempt_outcome(&ns, 0, EINTR, 0, 400000L) == 0 &&
              ns == 400000ull);
    }
    {
        uint64_t ns = 1000000ull;
        check("an interruption that consumed nothing still owes the whole request",
              rf_sleep_attempt_outcome(&ns, 0, EINTR, 0, 1000000L) == 0 &&
              ns == 1000000ull);
    }
    {
        uint64_t ns = 1000000ull;
        check("a bogus remainder can never EXTEND the wait",
              rf_sleep_attempt_outcome(&ns, 0, EINTR, 1, 500000000L) == 0 &&
              ns == 1000000ull);
    }
    {
        uint64_t ns = 1000000ull;
        check("a hard error is reported and leaves the request untouched",
              rf_sleep_attempt_outcome(&ns, 0, EIO, 0, 0) == -1 && ns == 1000000ull);
    }
    check("a missing request pointer is rejected",
          rf_sleep_attempt_outcome(NULL, 0, EINTR, 0, 0) == -1);

    /* The budget must not fail a wait that COMPLETED on its last permitted
     * attempt: that reports a finished wait as a failure. */
    calls = 0; finish_at_attempt = (int)RF_SLEEP_MAX_ATTEMPTS;
    check("a wait completing on the last permitted attempt succeeds",
          rf_sleep_us(finishing, 1000) == 0 && calls == (int)RF_SLEEP_MAX_ATTEMPTS);
    calls = 0; finish_at_attempt = (int)RF_SLEEP_MAX_ATTEMPTS + 1;
    check("a wait needing one attempt MORE than the budget fails",
          rf_sleep_us(finishing, 1000) == -1 && calls == (int)RF_SLEEP_MAX_ATTEMPTS);

    /* The adapter the BOARD runs is the same one compiled here: that is the whole
     * reason rf_sleep.c is outside the PLATFORM_HOST guard. */
    check("the real nanosleep adapter is compiled into this host build",
          rf_sleep_default(0) == 0);
    check("the real adapter completes a short bounded wait",
          rf_sleep_default(1000) == 0);

    printf("=== %d PASS, %d FAIL ===\n", checks - failures, failures);
    return failures ? 1 : 0;
}
