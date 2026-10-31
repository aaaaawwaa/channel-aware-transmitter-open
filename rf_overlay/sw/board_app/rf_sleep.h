/* rf_sleep.h -- one bounded, *reportable* sleep.
 *
 * Why this is its own unit rather than a static function inside the board-only
 * half of rf_daemon_main.c: that half is excluded by PLATFORM_HOST, so a sleep
 * living there was never compiled -- let alone tested -- on the development
 * machine.  A wait whose failure mode is unverified is exactly what this feature
 * exists to avoid, so the logic AND the adapter the board runs are both compiled
 * here, into BOTH builds.  (MinGW provides nanosleep, so the real adapter is
 * genuinely exercised on the host rather than re-implemented for it.)
 *
 * The contract that matters: a sleep either elapses IN FULL or reports failure.
 * It never returns success having waited less than asked -- the trace reports the
 * REQUESTED duration next to the measured one, and a short wait reported as a
 * complete one would be a false measurement.
 */
#ifndef RF_SLEEP_H_
#define RF_SLEEP_H_

#include <stdint.h>

/* How many syscall attempts a single bounded wait may take.  A signal storm must
 * not turn a bounded wait into an unbounded one, so repeatedly interrupted waits
 * fail rather than resume forever.  64 is far above anything a 5 ms wait needs. */
#define RF_SLEEP_MAX_ATTEMPTS 64u

/* One underlying sleep attempt.
 *
 * `*ns` is in/out: the caller asks for that many nanoseconds, and the call writes
 * back how much is STILL OWED.  A completed attempt writes 0; an interrupted one
 * writes the remainder, which the loop resumes -- deliberately not restarting, so
 * a signal cannot stretch a bounded wait.  A no-progress return is tolerated (a
 * signal can arrive before any time is consumed); it is bounded by
 * RF_SLEEP_MAX_ATTEMPTS rather than forbidden.
 *
 * Returns 0 on a completed or interrupted attempt, -1 on a hard error. */
typedef int (*rf_sleep_syscall_t)(uint64_t *ns);

/* Wait for `us` microseconds and return 0 only when the full duration elapsed.
 * Returns -1 when the syscall reported a hard error, when no attempt completed,
 * when the attempt budget ran out, or when `syscall` is NULL. */
int rf_sleep_us(rf_sleep_syscall_t syscall, uint64_t us);

/* What ONE attempt leaves outstanding, given how the underlying sleep returned.
 *
 * This is separated from the syscall precisely so it can be tested without a real
 * signal: the branch that must NOT report a completed wait on an interruption is
 * the false-measurement mode this whole unit exists to prevent, and on a platform
 * whose sleep cannot be interrupted (MinGW) that branch is otherwise unreachable
 * from any test.
 *
 * `completed` is the syscall's success ("the full request elapsed").  On success
 * the outstanding time becomes 0.  On an interruption it becomes what the sleep
 * wrote back, CLAMPED to what was asked so an attempt can never make the wait
 * longer than requested.  Returns 0 when the caller should continue (success or
 * interruption) and -1 on a hard error.  `*ns` is left untouched on -1. */
int rf_sleep_attempt_outcome(uint64_t *ns, int completed, int err,
                             uint64_t tv_sec, long tv_nsec);

/* The real adapter: nanosleep, resumed across EINTR, kept being writable back to
 * the caller so the wait that runs on the board is the one the host suite can
 * drive.  Returns 0 when the duration elapsed, -1 otherwise. */
int rf_sleep_default(uint64_t us);

#endif /* RF_SLEEP_H_ */
