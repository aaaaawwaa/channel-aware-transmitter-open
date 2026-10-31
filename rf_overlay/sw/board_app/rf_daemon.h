/* rf_daemon.h -- the single-lease NDJSON control daemon.
 *
 * Two layers live here, deliberately separable:
 *
 *   rf_state_*  -- the state machine, in rf_state.h/.c.  Pure: no core handle,
 *                  no transport, no MMIO.  Every gate the plan asks for
 *                  ("OFFLINE cannot start TX", "pending means BUSY", "a
 *                  physical loopback without a cable confirmation is rejected")
 *                  is decided there, so it is provable without hardware -- and
 *                  its own test links only rf_state.o.
 *
 *   rf_daemon_* -- framing and dispatch over an abstract byte transport.  This
 *                  is where a request meets the core, so it is the layer that
 *                  must never report success it did not verify.
 *
 * The transport is a vtable rather than a socket.  Two reasons, both concrete:
 * the host toolchain (MinGW) has no <sys/socket.h>, so a socket here could not
 * be tested at all on the development machine; and short reads, mid-payload
 * disconnects and timeouts are exactly what the plan says must be detectable,
 * which a scripted transport models deterministically and a real socket models
 * only by luck.
 */
#ifndef RF_DAEMON_H_
#define RF_DAEMON_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "rf_core.h"
#include "rf_pl_status_reader.h"
#include "rf_protocol.h"
#include "rf_state.h"

/* ---------------------------------------------------------------- transport */

/* A read returns: >0 bytes read (may be short), 0 clean EOF, -1 I/O error,
 * -2 deadline expired.  A short read is normal -- the framing loop must keep
 * asking.  A write sends everything or reports -1; a partial write is an error,
 * because every message here is built whole and sent whole. */
#define RF_TRANSPORT_TIMEOUT (-2)

typedef struct {
    void *ctx;
    int (*read)(void *ctx, void *buf, size_t len);
    int (*write_all)(void *ctx, const void *buf, size_t len);
    void (*close)(void *ctx);
} rf_transport_t;

/* ------------------------------------------------------------------ daemon */

typedef struct rf_daemon rf_daemon_t;

/* The shape the release manifest records for a hash: 64 lowercase hexadecimal
 * characters (rf_overlay/sw/rf_release_manifest.py).  The +1 is the terminator,
 * so these are fixed arrays rather than pointers and a config is copied by value
 * with no lifetime of its own to get wrong. */
#define RF_SHA256_HEX_LEN 64u

/* The release identity this process was started with, as the startup manifest
 * check read it out of the manifest.  `device.info` reports it.
 *
 * This is an IDENTITY report, not attestation: it says which manifest this
 * process was started with.  It is NOT evidence that the .bit on disk is the
 * file that hash names -- verifying that is the deploy launcher's job
 * (rf_overlay/sw/rf_overlay_launcher.py), which hashes the artifacts.  The
 * daemon never reads the .bit or the .hwh.  Do not read these fields as proof
 * of what is loaded. */
typedef struct {
    char release_id[33];
    char bit_sha256[RF_SHA256_HEX_LEN + 1u];
    char hwh_sha256[RF_SHA256_HEX_LEN + 1u];
} rf_release_identity_t;

typedef enum { RF_DAEMON_STATUS_ONLY = 0, RF_DAEMON_CONTROL_TEST = 1 } rf_daemon_mode_t;

/* The WRITE-capable RF hardware, injected rather than opened here.
 *
 * rf_daemon.c must stay hardware-free: the host suite links it directly, and a
 * direct /dev/mem call in it could not be compiled -- let alone tested -- on the
 * development machine.  This is the same injection shape as `verify_control_token`,
 * and for the same reason: the decision stays in the tested code and the OS call
 * stays in the board-only half.
 *
 * `acquire` is called EXACTLY ONCE, and only from a `control.arm` whose token the
 * verifier accepted.  It opens the O_RDWR aperture and hands back a core the daemon
 * may drive.  It returns 0, or an RF_ERR_* code for the refusal.
 *
 * `release` undoes one successful `acquire`.  It is called exactly once per acquire,
 * from rf_daemon_disarm.
 *
 * NULL `acquire` means "this process has no write-capable hardware" -- the C suite's
 * harness, and any deployment today.  Arming still succeeds there (the token is the
 * gate, and changing that would break the existing criteria), but the armed daemon
 * has no core and every hardware operation refuses INVALID_STATE.  That is the
 * fail-closed shape: no hardware, no capability. */
typedef struct {
    void *ctx;
    int (*acquire)(void *ctx, rf_core_t **out_core);
    void (*release)(void *ctx, rf_core_t *core);
} rf_control_hw_t;

/* The ceiling on an opt-in settling wait (`settle_wait_us` on a physical
 * capture).  Bounded so a request cannot turn into an unbounded stall of the
 * single control client.  Public because the host suite and the client both have
 * to reject the same boundary the daemon does. */
#define RF_SETTLE_WAIT_MAX_US 5000u

typedef struct {
    rf_daemon_mode_t mode;
    rf_pl_status_reader_t *pl_reader;
    /* The ceiling for tx.load and rx.capture.  Must be <= RF_BRAM_WORDS; it is
     * configured rather than assumed so a request cannot use the frame length
     * to walk past the BRAM window. */
    unsigned max_samples;
    unsigned port;
    /* Injected so the host test can produce deterministic frame timestamps.  A
     * frame that reports a timestamp must report one it actually got, so this
     * has no default. */
    uint64_t (*now_ns)(void);
    /* Sleeps at least `us` microseconds and REPORTS whether it did: 0 when the
     * full duration elapsed, -1 when it did not.  Injected for the same reason as
     * now_ns -- and because the caller has to be able to tell a completed wait
     * from a failed one.  A void callback made "the wait never ran" and "the wait
     * ran" indistinguishable, so a failed wait silently became a shorter capture.
     *
     * NULL means "this process cannot wait".  A capture that asks for a settling
     * wait is then REFUSED, never silently run without one.  Same fail-closed
     * shape as verify_control_token and control_hw. */
    int (*sleep_us)(uint64_t us);
    /* Asked by the serve loop so a CONTROLLED termination is noticed DURING a
     * connection, not only between connections.  A blocking receive can tie the
     * loop up for the transport's whole timeout, which is longer than the
     * service's stop budget -- without this the process is killed before it can
     * bring the part to rest and CONFIRM the stop, and "asked to stop" is not the
     * same thing as "safely stopped".
     *
     * Injected for the same reason as now_ns: the DECISION belongs in the code the
     * host suite links, and only the flag and the signal wiring belong to the
     * board-only half.  NULL means "nothing can ask", which is the pre-existing
     * behaviour byte for byte. */
    int (*should_stop)(void);
    /* Decides whether a `control.arm` request may arm the daemon; the production
     * implementation (rf_daemon_main.c) compares the presented token against the
     * operator-provisioned file, which is deliberately OUTSIDE every release and
     * manifest, so a release cannot arm itself.  NULL means "no token source":
     * arming is then always refused, which is the fail-closed default for a
     * process started without one.  The daemon is never armed by installing or
     * starting it -- only by a request that this verifier accepts. */
    int (*verify_control_token)(const char *token);
    /* The identity to report.  rf_daemon_startup fills it from the manifest it
     * validated, so device.info needs no file I/O at request time; there is no
     * default, because a daemon without an identity cannot answer device.info
     * with one. */
    rf_release_identity_t identity;
    /* See rf_control_hw_t.  Zero-initialised means "no write-capable hardware",
     * which is what every existing criterion and the harness rely on. */
    rf_control_hw_t control_hw;
} rf_daemon_config_t;

int rf_daemon_create(const rf_daemon_config_t *config, rf_daemon_t **out);
void rf_daemon_destroy(rf_daemon_t *daemon);

/* The core is borrowed, not owned: the caller creates and destroys it.  A
 * daemon with no core answers device.info and state.get and refuses everything
 * that would touch hardware. */
void rf_daemon_attach_core(rf_daemon_t *daemon, rf_core_t *core);

/* The lease is the single-client rule.  acquire returns 0, or -1 when another
 * client holds it -- it never preempts, and a refused acquire touches no
 * hardware. */
int rf_daemon_lease_acquire(rf_daemon_t *daemon);
void rf_daemon_lease_release(rf_daemon_t *daemon);
bool rf_daemon_lease_held(const rf_daemon_t *daemon);

/* Turn arming off, and say so in the audit log.
 *
 * This is the ONE place arming is cleared, so every path that ends control --
 * a `control.disarm` request, a shutdown, a termination signal, a fatal error --
 * makes the same state change and leaves the same kind of audit line.  A caller
 * that is not sure whether anything was armed can call it unconditionally: an
 * already-unarmed daemon is left alone and produces no second line.
 *
 * Returns 1 when this call changed the state, 0 when it was already unarmed.
 *
 * CONTRACT CHANGE (production wiring): it now also brings an OWNED core's part to
 * rest and releases the write-capable hardware (rf_control_hw_t::release).  The
 * order is the point -- the stop runs FIRST, while the mapping is still open,
 * because rf_core_close() deliberately does not stop the part (rf_core.c).  It
 * remains the ONE entry point and still writes exactly one audit line; a core the
 * daemon does not own (the harness's borrowed one) is left alone.
 *
 * It lives here, outside every board-only guard, because the host test must be
 * able to drive the exact function the signal path drives: a disarm that no test
 * exercises is the piece of "the daemon cannot exit while armed" that would rot
 * unnoticed.  That the board's exit paths call it, and call only it, is pinned by
 * rf_overlay/sw/tests/test_control_split_contract.py. */
int rf_daemon_disarm(rf_daemon_t *daemon, const char *reason);

/* How long control may remain armed without a successful control request.
 * Read-only polling, malformed lines and refused requests do not renew it.
 * The timeout is declared once and measured on the injected monotonic clock.
 * A successful repeated control.arm can keep control alive between operations.
 */
#define RF_DAEMON_ARMED_IDLE_TIMEOUT_NS (30ull * 1000000000ull)

/* Advance the armed-client watchdog to `now_ns`.  The clock is the CALLER's monotonic
 * one -- never read here -- so a host test can advance it and the board uses real time
 * through the same decision.  Disarms and returns 1 when the daemon has been armed and
 * silent for RF_DAEMON_ARMED_IDLE_TIMEOUT_NS; returns 0 when nothing changed.
 *
 * It is portable and lives outside every board guard on purpose: a watchdog checked
 * only in the board accept loop cannot be tested on the host at all, and an untested
 * timeout is precisely what this step must not ship.  The serve loop calls it on every
 * request and the board's accept loop calls it while it waits, so a scripted transport
 * and a real socket drive the same code. */
int rf_daemon_watchdog_tick(rf_daemon_t *daemon, uint64_t now_ns);

/* Serve one connection to completion (EOF, error, or protocol.close).  Takes the
 * lease; if it cannot, the transport is answered with BUSY and closed, and the
 * live client is left alone.  Returns 0 when the connection ended cleanly, -1 on
 * a transport or framing failure OR when the disconnect safety rule could not be
 * confirmed -- an unverified shutoff is not a clean end.  On any exit the lease
 * is released and the disconnect rule is applied. */
int rf_daemon_serve(rf_daemon_t *daemon, rf_transport_t *transport);

/* Exposed for tests and for a caller that already owns the lease. */
const rf_session_t *rf_daemon_session(const rf_daemon_t *daemon);

#endif /* RF_DAEMON_H_ */
