#include "rf_sleep.h"

#include <errno.h>
#include <time.h>

int rf_sleep_us(rf_sleep_syscall_t syscall, uint64_t us)
{
    uint64_t ns = us * 1000ull;
    unsigned attempts = 0u;

    if (!syscall)
        return -1;
    /* A zero-length wait is a success and touches nothing, so the default capture
     * path stays free of any sleep call at all. */
    while (ns > 0ull) {
        /* The budget is checked BEFORE the call, so a wait that completes on the
         * LAST permitted attempt succeeds.  Checking after the call (the earlier
         * shape) failed a wait that had in fact elapsed in full. */
        if (attempts >= RF_SLEEP_MAX_ATTEMPTS)
            return -1;
        ++attempts;
        if (syscall(&ns) != 0)
            return -1;
    }
    return 0;
}

int rf_sleep_attempt_outcome(uint64_t *ns, int completed, int err,
                             uint64_t tv_sec, long tv_nsec)
{
    uint64_t asked, left;

    if (!ns)
        return -1;
    asked = *ns;
    if (completed) {
        *ns = 0ull;
        return 0;
    }
    if (err != EINTR)
        return -1;
    /* Deliberately NOT `*ns = 0`: a sleep that reported success on an interruption
     * waited less than asked, and the trace publishes the REQUESTED duration next
     * to the measured one -- so that would be a false measurement, not a shorter
     * one.  The remaining time is what the sleep wrote back, clamped so an attempt
     * can never EXTEND the wait either. */
    left = (uint64_t)tv_sec * 1000000000ull + (uint64_t)tv_nsec;
    *ns = left < asked ? left : asked;
    return 0;
}

/* nanosleep writes the REMAINING time back into its timespec on EINTR, so
 * re-passing the same struct resumes the wait rather than restarting it. */
static int nanosleep_ns(uint64_t *ns)
{
    struct timespec ts;
    uint64_t asked = *ns;
    int rc;

    ts.tv_sec = (time_t)(asked / 1000000000ull);
    ts.tv_nsec = (long)(asked % 1000000000ull);
    rc = nanosleep(&ts, &ts);
    return rf_sleep_attempt_outcome(ns, rc == 0, errno,
                                    (uint64_t)ts.tv_sec, ts.tv_nsec);
}

int rf_sleep_default(uint64_t us)
{
    return rf_sleep_us(nanosleep_ns, us);
}
