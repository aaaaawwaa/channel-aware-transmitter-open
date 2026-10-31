/* rf_state.c -- the daemon's state machine.  See rf_state.h for why it is its
 * own translation unit.
 *
 * The guiding rule: a state transition is decided from evidence, never from
 * intent.  rf_state_finish lands in FAULT whenever the hardware readback does
 * not confirm the thing that was asked for, and the caller is handed -1 so it
 * cannot report success.  An operation that merely "did not report failure" is
 * not a success here.
 */
#include "rf_state.h"

#include <string.h>

/* The refusal messages name the attenuation the gate compares against, and they
 * are built from the gate's own macro rather than typed next to it: a message
 * that disagreed with the comparison it explains would send the operator after
 * the wrong number, which is the failure this text exists to prevent.  (The
 * value has no integer suffix precisely so this expansion is "46000".) */
#define RF_STATE_STRINGIFY_(value) #value
#define RF_STATE_STRINGIFY(value) RF_STATE_STRINGIFY_(value)
#define RF_STATE_LOOPBACK_ATTENUATION_TEXT \
    RF_STATE_STRINGIFY(RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB)

static void set_error(rf_state_error_t *error, rf_proto_code_t code,
                      const char *message)
{
    if (!error)
        return;
    error->code = (int)code;
    error->message = message;
}

static const char *state_name(rf_state_t state)
{
    switch (state) {
    case RF_STATE_OFFLINE:            return "OFFLINE";
    case RF_STATE_INITIALIZING:       return "INITIALIZING";
    case RF_STATE_READY:              return "READY";
    case RF_STATE_TX_ACTIVE:          return "TX_ACTIVE";
    case RF_STATE_RX_CAPTURING:       return "RX_CAPTURING";
    case RF_STATE_DIGITAL_LOOPBACK:   return "DIGITAL_LOOPBACK";
    case RF_STATE_PHYSICAL_LOOPBACK:  return "PHYSICAL_LOOPBACK";
    case RF_STATE_STOPPING:           return "STOPPING";
    case RF_STATE_FAULT:              return "FAULT";
    }
    return "UNKNOWN";
}

const char *rf_state_name(rf_state_t state)
{
    return state_name(state);
}

void rf_state_init(rf_session_t *session)
{
    if (!session)
        return;
    memset(session, 0, sizeof *session);
    session->state = RF_STATE_OFFLINE;
}

void rf_state_detach(rf_session_t *session)
{
    bool lease;

    if (!session)
        return;
    /* The lease outlives the part: this is called while the client that armed is
     * still connected and still holds it. */
    lease = session->lease_held;
    rf_state_init(session);
    session->lease_held = lease;
}

int rf_state_can_start(rf_state_t state, rf_operation_t op)
{
    /* Stop is the recovery action: available from every state, including FAULT,
     * because a stop that requires the part to be healthy cannot recover it. */
    if (op == RF_OP_STOP)
        return 1;
    /* init is the transition OUT of the offline states, so it is the one op the
     * READY gate must refuse: the part is programmed once, and re-running init
     * on a live part is what the daemon's "already up" refusal is made of. */
    if (op == RF_OP_INIT)
        return state == RF_STATE_OFFLINE || state == RF_STATE_FAULT;
    /* Everything else needs the part up, configured, and doing nothing else.
     * READY is the only such state, which is what makes TX and RX mutually
     * exclusive: there is no state in which two of them are both startable. */
    if (state != RF_STATE_READY)
        return 0;
    switch (op) {
    case RF_OP_CONFIGURE:
    case RF_OP_TX_START:
    case RF_OP_RX_CAPTURE:
    case RF_OP_DIGITAL_LOOPBACK:
    case RF_OP_DIGITAL_CAPTURE:
    case RF_OP_PHYSICAL_CAPTURE:
    case RF_OP_PHYSICAL_LOOPBACK:
        return 1;
    default:
        return 0;
    }
}

int rf_state_check_physical_loopback(rf_session_t *session,
                                     const rf_loopback_request_t *request,
                                     rf_state_error_t *error)
{
    /* Clear first: an approval must never survive a later refusal. */
    if (session)
        session->physical_approved = false;
    if (!session || !request) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT, "missing loopback request");
        return -1;
    }
    if (!request->cable_confirmed) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT,
                  "physical loopback requires explicit cable confirmation");
        return -1;
    }
    /* Exactly the stated value, not "at least": the request has to name the
     * attenuation it wants, and a loopback is only approved at the one value the
     * domain fixes (rf_state.h: the teaching/environment single source).  The
     * message below is built from that same macro, so it names the value being
     * enforced and not a remembered one. */
    if (request->attenuation_mdB != (long)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT,
                  "physical loopback requires attenuation_mdB "
                  RF_STATE_LOOPBACK_ATTENUATION_TEXT);
        return -1;
    }
    session->physical_approved = true;
    return 0;
}

int rf_state_begin(rf_session_t *session, rf_operation_t op,
                   rf_state_error_t *error)
{
    if (!session) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT, "no session");
        return -1;
    }
    if (!session->lease_held) {
        set_error(error, RF_PROTO_E_BUSY, "another client holds the lease");
        return -1;
    }
    if (session->pending) {
        set_error(error, RF_PROTO_E_BUSY, "an operation is already in flight");
        return -1;
    }
    /* Before the state gate: an unapproved physical loopback is a bad request
     * whatever the state is, and it must never be reported as merely "wrong
     * time". */
    if (op == RF_OP_PHYSICAL_LOOPBACK && !session->physical_approved) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT,
                  "physical loopback was not approved by a cable-confirmed "
                  "request at " RF_STATE_LOOPBACK_ATTENUATION_TEXT " mdB");
        return -1;
    }
    if (!rf_state_can_start(session->state, op)) {
        set_error(error, RF_PROTO_E_INVALID_STATE, state_name(session->state));
        return -1;
    }
    session->pending = true;
    session->pending_op = op;
    return 0;
}

int rf_state_finish(rf_session_t *session, rf_hw_status_t status,
                    rf_state_error_t *error)
{
    rf_operation_t op;

    if (!session) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT, "no session");
        return -1;
    }
    if (!session->pending) {
        set_error(error, RF_PROTO_E_INVALID_STATE, "no operation is in flight");
        return -1;
    }
    op = session->pending_op;
    session->pending = false;
    session->pending_op = RF_OP_NONE;
    /* The approval was for one transaction. */
    session->physical_approved = false;

    switch (op) {
    case RF_OP_TX_START:
        /* "TX is active" is the success evidence: a start whose readback says
         * the PL is not sourcing did not start. */
        session->state = status.tx_active ? RF_STATE_TX_ACTIVE : RF_STATE_FAULT;
        break;
    case RF_OP_RX_CAPTURE:
        /* A one-shot capture that COMPLETED has already been drained out of the
         * BRAM by the time this is called, so the part is idle again and the
         * next operation may start.  Only a capture that is still running (the
         * continuous case) leaves it capturing.  Nothing at all means the arm
         * never took, which is a fault. */
        session->state = status.rx_done ? RF_STATE_READY
                       : (status.rx_busy ? RF_STATE_RX_CAPTURING : RF_STATE_FAULT);
        break;
    case RF_OP_STOP:
        /* The stop succeeded only if nothing is left running. */
        session->state = (status.tx_active || status.rx_busy) ? RF_STATE_FAULT
                                                             : RF_STATE_READY;
        break;
    case RF_OP_CONFIGURE:
        session->state = RF_STATE_READY;
        break;
    case RF_OP_INIT:
        /* init has no readback of its own to judge: a core that did not come up
         * faults itself and the caller calls rf_state_failed instead.  Reaching
         * here means the part answered, so it is up. */
        session->state = RF_STATE_READY;
        break;
    case RF_OP_DIGITAL_LOOPBACK:
        session->state = RF_STATE_DIGITAL_LOOPBACK;
        break;
    case RF_OP_DIGITAL_CAPTURE:
        /* The handler clears the AD9361 data-port loop before it finishes, so a
         * COMPLETED digital capture leaves the part READY like any other one-shot
         * capture.  A capture that did not complete is a fault: the tap was open
         * and the samples never came back. */
        session->state = status.rx_done ? RF_STATE_READY : RF_STATE_FAULT;
        break;
    case RF_OP_PHYSICAL_CAPTURE:
        /* The handler stops the TX source before it finishes, so a COMPLETED
         * physical capture leaves the part READY.  A capture that did not complete
         * is a fault. */
        session->state = status.rx_done ? RF_STATE_READY : RF_STATE_FAULT;
        break;
    case RF_OP_PHYSICAL_LOOPBACK:
        session->state = RF_STATE_PHYSICAL_LOOPBACK;
        break;
    default:
        session->state = RF_STATE_FAULT;
        break;
    }

    if (session->state == RF_STATE_FAULT) {
        set_error(error, RF_PROTO_E_HARDWARE_IO,
                  "hardware readback did not confirm the requested change");
        return -1;
    }
    return 0;
}

int rf_state_failed(rf_session_t *session, bool faulted, rf_state_error_t *error)
{
    if (!session) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT, "no session");
        return -1;
    }
    if (!session->pending) {
        set_error(error, RF_PROTO_E_INVALID_STATE, "no operation is in flight");
        return -1;
    }
    session->pending = false;
    session->pending_op = RF_OP_NONE;
    session->physical_approved = false;
    /* Only a core that actually faulted moves the session.  A request the core
     * refused on its merits -- a bad argument, a wrong state -- happened before
     * any hardware was touched, so condemning the part for it would turn every
     * typo into a fault that needs a re-init to clear. */
    if (faulted)
        session->state = RF_STATE_FAULT;
    return 0;
}

int rf_state_safe_stop(rf_session_t *session, rf_state_error_t *error)
{
    if (!session) {
        set_error(error, RF_PROTO_E_INVALID_ARGUMENT, "no session");
        return -1;
    }
    /* Already at rest: nothing to stop, and nothing to claim. */
    if (session->state == RF_STATE_OFFLINE) {
        session->pending = false;
        session->pending_op = RF_OP_NONE;
        session->physical_approved = false;
        return 0;
    }
    /* Anything else means the part is up, so the disconnect rule always drives
     * it back to a known state rather than leaving it as whoever was connected
     * left it -- including from READY, where nothing is sourcing but the part is
     * still enabled.  An operation that was in flight is abandoned: its client
     * is gone, so nothing will ever finish it, and the only thing that matters
     * is that the hardware stops. */
    session->pending = true;
    session->pending_op = RF_OP_STOP;
    session->physical_approved = false;
    return 1;
}
