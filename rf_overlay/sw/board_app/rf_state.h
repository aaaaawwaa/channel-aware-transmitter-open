/* rf_state.h -- the daemon's state machine, isolated from hardware.
 *
 * This is its own translation unit for the same reason rf_protocol.c is: the
 * test for it links ONLY rf_state.o, so "no state decision reaches hardware" is
 * enforced by the link rather than asserted by a spy.  Nothing here takes a
 * core handle, and every hardware fact arrives as a value (the readback) that
 * the caller already obtained.
 */
#ifndef RF_STATE_H_
#define RF_STATE_H_

#include <stdbool.h>

#include "rf_core.h"      /* rf_state_t, rf_hw_status_t */
#include "rf_protocol.h"  /* rf_proto_code_t */

/* Two numbers that must not be confused with each other.
 *
 * RF_SAFE_ATTENUATION_MDB is the vendor's own ceiling (MAX_TX_ATTENUATION_DB,
 * 89750 mdB) and is what the daemon applies when it loses a client with the TX
 * live.  RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB is the value a cabled loopback
 * must be explicitly requested with.  Neither has a default anywhere in this
 * file -- a missing attenuation is a rejected request, never a silently
 * substituted one.
 *
 * The loopback value IS the domain attenuation, and the domain has one source:
 * pynq_overlay/sw/rf_teaching_profile.py ATTENUATION_MDB, which is also what the
 * panel builds its request from and what the client refuses to send anything
 * else.  This constant said 40000u for as long as the domain said 46000, and
 * every physical frame was refused with INVALID_ARGUMENT before any hardware
 * write (rf_daemon.c:1671, ahead of rf_state_begin and the tx_count gate).
 * scripts/tests/test_rf_state_loopback_attenuation_pin.py now holds this value
 * equal to the Python source, and it fails if either side moves alone.
 *
 * Deliberately without an integer suffix: rf_state.c builds its refusal message
 * from this macro (RF_STATE_STRINGIFY), so the message that names the required
 * number and the comparison that enforces it are one piece of text and cannot
 * disagree. */
#define RF_SAFE_ATTENUATION_MDB              89750u
#define RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB 46000

typedef enum {
    RF_OP_NONE = 0,
    RF_OP_CONFIGURE,
    RF_OP_TX_START,
    RF_OP_RX_CAPTURE,
    RF_OP_DIGITAL_LOOPBACK,
    /* One reviewable transaction for the on-chip TX->RX loop: open the AD9361
     * data-port loop, source the loaded TX buffer, run one one-shot capture,
     * drain it, and CLOSE THE TAP before returning the samples.  A single op
     * rather than "loopback.digital.start then rx.capture" for two measured
     * reasons: RF_OP_RX_CAPTURE is startable only from READY, and a stop closes
     * the tap before it stops -- so the daemon could never hold the loop open
     * and capture at the same time.  Closing on every exit path also means a
     * failed capture cannot leave the part looping. */
    RF_OP_DIGITAL_CAPTURE,
    /* Physical TX->RX capture: the operator's TX1 is cabled directly to RX1, and this
     * op sources the loaded TX buffer, runs one one-shot RX capture, drains it and
     * takes the TX source off again -- a single reviewed transaction exactly like the
     * digital version, but without the AD9361 data-port loop bit. */
    RF_OP_PHYSICAL_CAPTURE,
    RF_OP_PHYSICAL_LOOPBACK,
    /* Stop is a transaction like any other so that it cannot interleave with a
     * command in flight, but it is permitted from every state: it is the
     * recovery action, and a stop that is gated on being healthy is not a
     * recovery action. */
    RF_OP_STOP,
    /* The one transition OUT of the offline states, and therefore the one op
     * that is refused from READY and allowed from OFFLINE.  It is a
     * transaction like the rest (so two rf.init calls cannot interleave), even
     * though it does not fit the READY-gated table. */
    RF_OP_INIT
} rf_operation_t;

/* The plan names this struct rf_error_t; renamed because rf_core.h already owns
 * that name for its status enum, and rf_daemon.c includes both headers. */
typedef struct {
    int code;                 /* rf_proto_code_t */
    const char *message;
} rf_state_error_t;

typedef struct {
    /* A physical loopback is a direct cable from TX to RX at whatever the TX
     * attenuation happens to be, so both of these are demanded explicitly. */
    long attenuation_mdB;
    bool cable_confirmed;
} rf_loopback_request_t;

typedef struct {
    rf_state_t state;
    bool pending;
    bool lease_held;
    /* Which operation is in flight.  Required to interpret the readback in
     * rf_state_finish: "TX is active" is success after tx.start and failure
     * after stop, and the caller must not be the one to decide which. */
    rf_operation_t pending_op;
    /* Set only by rf_state_check_physical_loopback, for the exact request it
     * approved.  rf_state_begin refuses a physical loopback without it, so the
     * precondition cannot be skipped by a caller that forgets to ask. */
    bool physical_approved;
} rf_session_t;

void rf_state_init(rf_session_t *session);

/* Forget the part, keep the lease.  Called when the daemon hands the hardware back
 * (a `control.disarm` releases the acquired core, rf_daemon.c::release_owned_core):
 * with the write aperture closed the daemon has no part, so its state record is void
 * and OFFLINE -- the vocabulary the daemon already uses for "no part attached" -- is
 * the only honest state.  The LEASE is deliberately preserved: it belongs to the
 * connection/process, not to the part, and clearing it here would make the still
 * connected client look like a second one.  Leaving the record at READY instead made
 * the next arm unusable, because a fresh arm acquires a FRESH core (OFFLINE) and
 * adopt_core_state() only reconciles when this session is OFFLINE (delivery record
 * §34, found on the board in Task 12 Step 5a). */
void rf_state_detach(rf_session_t *session);

/* The wire name of a state, so a status event and an error message cannot
 * disagree about what the state is called. */
const char *rf_state_name(rf_state_t state);

/* Pure gate: may `op` start from `state`?  Returns 1 or 0. */
int rf_state_can_start(rf_state_t state, rf_operation_t op);

/* Approve (or refuse) a physical-loopback request.  On refusal *error carries
 * INVALID_ARGUMENT and the session's approval is cleared, so an approval can
 * never outlive the request that earned it. */
int rf_state_check_physical_loopback(rf_session_t *session,
                                     const rf_loopback_request_t *request,
                                     rf_state_error_t *error);

/* Start a transaction.  BUSY when another command is pending or the lease is
 * not held; INVALID_STATE when the gate refuses; INVALID_ARGUMENT when it is a
 * physical loopback that was never approved. */
int rf_state_begin(rf_session_t *session, rf_operation_t op,
                   rf_state_error_t *error);

/* End a transaction using what the hardware actually reported.  Landing in
 * FAULT is returned as -1 -- the caller must not report success. */
int rf_state_finish(rf_session_t *session, rf_hw_status_t status,
                    rf_state_error_t *error);

/* End a transaction that FAILED before it could be judged by a readback:
 * rf_core_* refused the request, or the readback could not be obtained at all.
 * Without this a refused command would leave `pending` set forever and every
 * later request would be answered BUSY.
 *
 * `faulted` is whether the core itself ended up in FAULT -- the caller reads it
 * from rf_core_state.  A refusal that happened before any hardware was touched
 * (a bad argument, a wrong state) changes nothing, so the session keeps the
 * state it had; only a core that faulted moves the session to FAULT.  Returns 0
 * when a transaction was abandoned, -1 when there was none. */
int rf_state_failed(rf_session_t *session, bool faulted, rf_state_error_t *error);

/* The disconnect rule, and nothing more: if anything is live, ask for a stop.
 * Returns 1 when a safe stop is required, 0 when there was nothing to stop,
 * -1 on error.  It performs no hardware action itself. */
int rf_state_safe_stop(rf_session_t *session, rf_state_error_t *error);

#endif /* RF_STATE_H_ */
