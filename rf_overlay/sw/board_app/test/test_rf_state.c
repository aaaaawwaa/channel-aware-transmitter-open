/* test_rf_state.c -- host tests for the daemon's state machine.
 *
 * Built with NO PLATFORM_HOST and linked against rf_state.o only.  The state
 * machine therefore cannot reach the PL, the SPI or the vendor driver even by
 * accident: a hardware call would fail to link.  Every hardware fact here is a
 * value the test hands in.
 */
#include "rf_state.h"

#include <stdio.h>
#include <string.h>

static unsigned pass_count;
static unsigned fail_count;

static void check(const char *name, int condition)
{
    if (condition) {
        ++pass_count;
        printf("PASS %s\n", name);
    } else {
        ++fail_count;
        printf("FAIL %s\n", name);
    }
}

static rf_hw_status_t hw(bool tx, bool busy, bool done)
{
    rf_hw_status_t s;

    s.tx_active = tx;
    s.rx_busy = busy;
    s.rx_done = done;
    return s;
}

/* A session that holds the lease and sits in `state`, i.e. the position a real
 * client is in when it sends a command. */
static rf_session_t session_in(rf_state_t state)
{
    rf_session_t s;

    rf_state_init(&s);
    s.state = state;
    s.lease_held = true;
    return s;
}

int main(void)
{
    rf_session_t s;
    rf_state_error_t err;
    rf_loopback_request_t req;

    printf("=== test_rf_state ===\n");

    /* ------------------------------------------------------------- the gate */

    check("OFFLINE cannot start TX",
          !rf_state_can_start(RF_STATE_OFFLINE, RF_OP_TX_START));
    check("OFFLINE cannot configure",
          !rf_state_can_start(RF_STATE_OFFLINE, RF_OP_CONFIGURE));
    check("OFFLINE cannot capture RX",
          !rf_state_can_start(RF_STATE_OFFLINE, RF_OP_RX_CAPTURE));
    check("INITIALIZING cannot start TX",
          !rf_state_can_start(RF_STATE_INITIALIZING, RF_OP_TX_START));
    check("FAULT cannot start TX",
          !rf_state_can_start(RF_STATE_FAULT, RF_OP_TX_START));
    check("STOPPING cannot start TX",
          !rf_state_can_start(RF_STATE_STOPPING, RF_OP_TX_START));
    check("TX_ACTIVE cannot capture RX",
          !rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_RX_CAPTURE));
    check("TX_ACTIVE cannot start TX again",
          !rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_TX_START));
    check("TX_ACTIVE cannot configure",
          !rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_CONFIGURE));
    check("RX_CAPTURING cannot start TX",
          !rf_state_can_start(RF_STATE_RX_CAPTURING, RF_OP_TX_START));
    check("DIGITAL_LOOPBACK cannot start TX",
          !rf_state_can_start(RF_STATE_DIGITAL_LOOPBACK, RF_OP_TX_START));
    check("PHYSICAL_LOOPBACK cannot capture RX",
          !rf_state_can_start(RF_STATE_PHYSICAL_LOOPBACK, RF_OP_RX_CAPTURE));

    check("READY can start TX, RX, configure and both loopbacks",
          rf_state_can_start(RF_STATE_READY, RF_OP_TX_START) &&
          rf_state_can_start(RF_STATE_READY, RF_OP_RX_CAPTURE) &&
          rf_state_can_start(RF_STATE_READY, RF_OP_CONFIGURE) &&
          rf_state_can_start(RF_STATE_READY, RF_OP_DIGITAL_LOOPBACK) &&
          rf_state_can_start(RF_STATE_READY, RF_OP_PHYSICAL_LOOPBACK));
    /* The combined digital loop+capture is a READY-only hardware transaction like
     * the rest: it opens the tap, captures and closes the tap inside one op, so
     * it must not be startable while the part is already doing something else. */
    check("DIGITAL_CAPTURE is READY-only",
          rf_state_can_start(RF_STATE_READY, RF_OP_DIGITAL_CAPTURE) &&
          !rf_state_can_start(RF_STATE_DIGITAL_LOOPBACK, RF_OP_DIGITAL_CAPTURE) &&
          !rf_state_can_start(RF_STATE_PHYSICAL_LOOPBACK, RF_OP_DIGITAL_CAPTURE) &&
          !rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_DIGITAL_CAPTURE) &&
          !rf_state_can_start(RF_STATE_RX_CAPTURING, RF_OP_DIGITAL_CAPTURE) &&
          !rf_state_can_start(RF_STATE_OFFLINE, RF_OP_DIGITAL_CAPTURE));
    check("stop is permitted from every state, including FAULT",
          rf_state_can_start(RF_STATE_OFFLINE, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_READY, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_RX_CAPTURING, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_DIGITAL_LOOPBACK, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_PHYSICAL_LOOPBACK, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_STOPPING, RF_OP_STOP) &&
          rf_state_can_start(RF_STATE_FAULT, RF_OP_STOP));
    check("RF_OP_NONE is never startable",
          !rf_state_can_start(RF_STATE_READY, RF_OP_NONE));

    /* ----------------------------------------------------------- the lease */

    s = session_in(RF_STATE_READY);
    s.lease_held = false;
    err.code = RF_PROTO_E_NONE;
    check("a session with no lease cannot begin anything",
          rf_state_begin(&s, RF_OP_TX_START, &err) != 0 &&
          err.code == RF_PROTO_E_BUSY && !s.pending);

    s = session_in(RF_STATE_READY);
    check("a leased READY session can begin TX",
          rf_state_begin(&s, RF_OP_TX_START, &err) == 0 && s.pending &&
          s.pending_op == RF_OP_TX_START);

    /* ------------------------------------------------------------ the BUSY */

    err.code = RF_PROTO_E_NONE;
    check("a second command while pending is BUSY, and changes nothing",
          rf_state_begin(&s, RF_OP_RX_CAPTURE, &err) != 0 &&
          err.code == RF_PROTO_E_BUSY && s.pending_op == RF_OP_TX_START);
    check("a stop while pending is BUSY too (it waits its turn)",
          rf_state_begin(&s, RF_OP_STOP, &err) != 0 && err.code == RF_PROTO_E_BUSY);

    err.code = RF_PROTO_E_NONE;
    {
        rf_session_t off = session_in(RF_STATE_OFFLINE);

        check("OFFLINE cannot begin TX and says INVALID_STATE",
              rf_state_begin(&off, RF_OP_TX_START, &err) != 0 &&
              err.code == RF_PROTO_E_INVALID_STATE);
    }
    {
        rf_session_t off = session_in(RF_STATE_OFFLINE);
        err.code = RF_PROTO_E_NONE;
        check("TX_ACTIVE cannot begin RX_CAPTURE and says INVALID_STATE",
              rf_state_begin(&off, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&off, hw(false, false, false), &err) == 0 &&
              off.state == RF_STATE_READY &&
              rf_state_begin(&off, RF_OP_TX_START, &err) == 0 &&
              rf_state_finish(&off, hw(true, false, false), &err) == 0 &&
              off.state == RF_STATE_TX_ACTIVE &&
              rf_state_begin(&off, RF_OP_RX_CAPTURE, &err) != 0 &&
              err.code == RF_PROTO_E_INVALID_STATE);
    }

    /* -------------------------------------------------- evidence, not intent */

    {
        rf_session_t tx = session_in(RF_STATE_READY);

        check("a TX start whose readback says 'not active' lands in FAULT",
              rf_state_begin(&tx, RF_OP_TX_START, &err) == 0 &&
              rf_state_finish(&tx, hw(false, false, false), &err) != 0 &&
              tx.state == RF_STATE_FAULT && err.code == RF_PROTO_E_HARDWARE_IO);
        check("FAULT is not startable afterwards",
              !rf_state_can_start(tx.state, RF_OP_TX_START));
    }
    {
        rf_session_t tx = session_in(RF_STATE_READY);

        check("a TX start whose readback says 'active' lands in TX_ACTIVE",
              rf_state_begin(&tx, RF_OP_TX_START, &err) == 0 &&
              rf_state_finish(&tx, hw(true, false, false), &err) == 0 &&
              tx.state == RF_STATE_TX_ACTIVE);
        check("a stop that leaves TX active lands in FAULT",
              rf_state_begin(&tx, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&tx, hw(true, false, false), &err) != 0 &&
              tx.state == RF_STATE_FAULT);
    }
    {
        rf_session_t tx = session_in(RF_STATE_TX_ACTIVE);

        check("a stop confirmed by the readback returns to READY",
              rf_state_begin(&tx, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&tx, hw(false, false, false), &err) == 0 &&
              tx.state == RF_STATE_READY);
        check("a stop whose readback still shows RX armed lands in FAULT",
              rf_state_begin(&tx, RF_OP_RX_CAPTURE, &err) == 0 &&
              rf_state_finish(&tx, hw(false, true, false), &err) == 0 &&
              tx.state == RF_STATE_RX_CAPTURING &&
              rf_state_begin(&tx, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&tx, hw(false, true, false), &err) != 0 &&
              tx.state == RF_STATE_FAULT);
    }
    {
        rf_session_t rx = session_in(RF_STATE_READY);

        check("an RX capture that never armed lands in FAULT",
              rf_state_begin(&rx, RF_OP_RX_CAPTURE, &err) == 0 &&
              rf_state_finish(&rx, hw(false, false, false), &err) != 0 &&
              rx.state == RF_STATE_FAULT);
    }
    {
        rf_session_t rx = session_in(RF_STATE_READY);

        check("an RX capture that is still running lands in RX_CAPTURING",
              rf_state_begin(&rx, RF_OP_RX_CAPTURE, &err) == 0 &&
              rf_state_finish(&rx, hw(false, true, false), &err) == 0 &&
              rx.state == RF_STATE_RX_CAPTURING);
        check("a completed one-shot capture leaves the part READY, not capturing "
              "(the daemon has already drained the BRAM)",
              rf_state_begin(&rx, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&rx, hw(false, false, false), &err) == 0 &&
              rf_state_begin(&rx, RF_OP_RX_CAPTURE, &err) == 0 &&
              rf_state_finish(&rx, hw(false, false, true), &err) == 0 &&
              rx.state == RF_STATE_READY);
    }
    {
        rf_session_t d = session_in(RF_STATE_READY);

        check("a digital loopback lands in DIGITAL_LOOPBACK",
              rf_state_begin(&d, RF_OP_DIGITAL_LOOPBACK, &err) == 0 &&
              rf_state_finish(&d, hw(false, false, false), &err) == 0 &&
              d.state == RF_STATE_DIGITAL_LOOPBACK);
        check("leaving the digital loopback needs a stop, then READY",
              rf_state_begin(&d, RF_OP_STOP, &err) == 0 &&
              rf_state_finish(&d, hw(false, false, false), &err) == 0 &&
              d.state == RF_STATE_READY);
    }
    {
        rf_session_t idle = session_in(RF_STATE_READY);

        check("finish with nothing pending is INVALID_STATE",
              rf_state_finish(&idle, hw(false, false, false), &err) != 0 &&
              err.code == RF_PROTO_E_INVALID_STATE);
    }

    /* ------------------------------------------- physical loopback approval */

    s = session_in(RF_STATE_READY);
    req.attenuation_mdB = (long)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB;
    req.cable_confirmed = false;
    err.code = RF_PROTO_E_NONE;
    check("physical loopback without cable confirmation is INVALID_ARGUMENT",
          rf_state_check_physical_loopback(&s, &req, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT && !s.physical_approved);
    check("and it cannot be begun anyway",
          rf_state_begin(&s, RF_OP_PHYSICAL_LOOPBACK, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT);

    req.cable_confirmed = true;
    req.attenuation_mdB = 0;
    err.code = RF_PROTO_E_NONE;
    check("physical loopback at attenuation 0 is INVALID_ARGUMENT",
          rf_state_check_physical_loopback(&s, &req, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT && !s.physical_approved);

    req.attenuation_mdB = (long)RF_SAFE_ATTENUATION_MDB;
    err.code = RF_PROTO_E_NONE;
    check("physical loopback at the maximum attenuation is still refused: the "
          "domain fixes one value, and 89750 is a different request",
          rf_state_check_physical_loopback(&s, &req, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT && !s.physical_approved);

    /* 40000 is the value this gate enforced before 2026-10-18, when the domain was
     * already 46000.  It is an ordinary wrong request now -- and it proves the named
     * refusal survived the move: the message has to name the value the domain does
     * fix, not the one that was asked for. */
    req.attenuation_mdB = 40000;
    err.code = RF_PROTO_E_NONE;
    err.message = NULL;
    check("the pre-migration 40000 is refused by a message naming the domain value",
          rf_state_check_physical_loopback(&s, &req, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT && !s.physical_approved &&
          err.message != NULL &&
          strstr(err.message, "attenuation_mdB 46000") != NULL &&
          strstr(err.message, "40000") == NULL);

    req.attenuation_mdB = (long)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB;
    check("the loopback value is the 46 dB domain, not a number of its own",
          RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB == 46000);
    check("a cable-confirmed request at exactly the domain value is approved",
          rf_state_check_physical_loopback(&s, &req, &err) == 0 &&
          s.physical_approved);
    check("and then it can be begun",
          rf_state_begin(&s, RF_OP_PHYSICAL_LOOPBACK, &err) == 0 &&
          s.pending_op == RF_OP_PHYSICAL_LOOPBACK);
    check("an approval does not survive the transaction it authorised",
          rf_state_finish(&s, hw(false, false, false), &err) == 0 &&
          s.state == RF_STATE_PHYSICAL_LOOPBACK && !s.physical_approved);
    check("a second physical loopback needs its own approval",
          rf_state_begin(&s, RF_OP_PHYSICAL_LOOPBACK, &err) != 0 &&
          err.code == RF_PROTO_E_INVALID_ARGUMENT);
    {
        /* A refusal after an approval must revoke it. */
        rf_session_t r = session_in(RF_STATE_READY);
        rf_loopback_request_t bad = req;

        bad.cable_confirmed = false;
        check("a later refusal revokes an earlier approval",
              rf_state_check_physical_loopback(&r, &req, &err) == 0 &&
              r.physical_approved &&
              rf_state_check_physical_loopback(&r, &bad, &err) != 0 &&
              !r.physical_approved);
    }

    /* ---------------------------------------------------- the disconnect rule */

    rf_state_init(&s);
    check("a disconnect while OFFLINE needs no safe stop",
          rf_state_safe_stop(&s, &err) == 0 && !s.pending);
    check("a disconnect from READY asks for a safe stop",
          (s = session_in(RF_STATE_READY), rf_state_safe_stop(&s, &err)) == 1 &&
          s.pending && s.pending_op == RF_OP_STOP);
    check("a disconnect while TX is live asks for a safe stop",
          (s = session_in(RF_STATE_TX_ACTIVE), rf_state_safe_stop(&s, &err)) == 1 &&
          s.pending_op == RF_OP_STOP);
    check("a disconnect while capturing RX asks for a safe stop",
          (s = session_in(RF_STATE_RX_CAPTURING), rf_state_safe_stop(&s, &err)) == 1);
    check("a disconnect while in a loopback asks for a safe stop",
          (s = session_in(RF_STATE_DIGITAL_LOOPBACK), rf_state_safe_stop(&s, &err)) == 1 &&
          (s = session_in(RF_STATE_PHYSICAL_LOOPBACK),
           rf_state_safe_stop(&s, &err)) == 1);
    check("a disconnect from FAULT still asks for a safe stop",
          (s = session_in(RF_STATE_FAULT), rf_state_safe_stop(&s, &err)) == 1);
    {
        /* The in-flight operation is abandoned, and the safe stop replaces it:
         * the client that would have finished it is gone. */
        rf_session_t mid = session_in(RF_STATE_READY);

        check("a disconnect mid-operation abandons it for the stop",
              rf_state_begin(&mid, RF_OP_TX_START, &err) == 0 &&
              mid.pending_op == RF_OP_TX_START &&
              rf_state_safe_stop(&mid, &err) == 1 &&
              mid.pending_op == RF_OP_STOP);
    }
    check("the safe stop then lands from the observed readback",
          rf_state_finish(&s, hw(false, false, false), &err) == 0 &&
          s.state == RF_STATE_READY && !s.pending);
    {
        rf_session_t stuck = session_in(RF_STATE_TX_ACTIVE);

        check("a safe stop that cannot confirm the stop lands in FAULT",
              rf_state_safe_stop(&stuck, &err) == 1 &&
              rf_state_finish(&stuck, hw(true, false, false), &err) != 0 &&
              stuck.state == RF_STATE_FAULT);
    }

    /* ------------------------------------------- init, and failed commands */

    check("INIT is startable only from OFFLINE and FAULT",
          rf_state_can_start(RF_STATE_OFFLINE, RF_OP_INIT) &&
          rf_state_can_start(RF_STATE_FAULT, RF_OP_INIT) &&
          !rf_state_can_start(RF_STATE_READY, RF_OP_INIT) &&
          !rf_state_can_start(RF_STATE_TX_ACTIVE, RF_OP_INIT) &&
          !rf_state_can_start(RF_STATE_DIGITAL_LOOPBACK, RF_OP_INIT));
    {
        rf_session_t off = session_in(RF_STATE_OFFLINE);

        check("a successful init lands in READY",
              rf_state_begin(&off, RF_OP_INIT, &err) == 0 && off.pending &&
              rf_state_finish(&off, hw(false, false, false), &err) == 0 &&
              off.state == RF_STATE_READY && !off.pending);
    }
    {
        rf_session_t ready = session_in(RF_STATE_READY);

        check("init on a part that is already up is INVALID_STATE",
              rf_state_begin(&ready, RF_OP_INIT, &err) != 0 &&
              err.code == RF_PROTO_E_INVALID_STATE && !ready.pending);
    }
    {
        /* The case that motivates rf_state_failed at all: a command the core
         * refused must not leave `pending` set, or every later request on the
         * connection would be answered BUSY until the client reconnects. */
        rf_session_t off = session_in(RF_STATE_OFFLINE);

        check("a refused command releases pending and leaves the state alone",
              rf_state_begin(&off, RF_OP_INIT, &err) == 0 &&
              rf_state_failed(&off, false, &err) == 0 &&
              !off.pending && off.pending_op == RF_OP_NONE &&
              off.state == RF_STATE_OFFLINE);
        check("and the next command is not BUSY",
              rf_state_begin(&off, RF_OP_INIT, &err) == 0 &&
              rf_state_failed(&off, false, &err) == 0);
    }
    {
        rf_session_t ready = session_in(RF_STATE_READY);

        check("a command that faulted the core lands the session in FAULT",
              rf_state_begin(&ready, RF_OP_RX_CAPTURE, &err) == 0 &&
              rf_state_failed(&ready, true, &err) == 0 &&
              ready.state == RF_STATE_FAULT && !ready.pending);
        check("failed with nothing in flight is INVALID_STATE",
              rf_state_failed(&ready, true, &err) != 0 &&
              err.code == RF_PROTO_E_INVALID_STATE);
    }
    {
        rf_session_t p = session_in(RF_STATE_READY);

        p.physical_approved = true;
        check("a failed command clears a carried approval",
              rf_state_begin(&p, RF_OP_TX_START, &err) == 0 &&
              rf_state_failed(&p, false, &err) == 0 && !p.physical_approved);
    }

    /* --------------------------------------------- rf_state_detach (record §34) */

    /* Handing the hardware back must forget the part but KEEP the lease: the lease
     * belongs to the connection, not to the part, and clearing it here would make the
     * still-connected client look like a second one. */
    {
        rf_session_t p = session_in(RF_STATE_READY);

        p.pending = true;
        p.pending_op = RF_OP_CONFIGURE;
        p.physical_approved = true;
        rf_state_detach(&p);
        check("detach forgets the part and lands OFFLINE",
              p.state == RF_STATE_OFFLINE && !p.pending && p.pending_op == RF_OP_NONE);
        check("detach drops a one-shot approval", !p.physical_approved);
        check("detach keeps the lease", p.lease_held);
    }

    /* ----------------------------------------------------------- state names */

    check("state names are the wire names",
          strcmp(rf_state_name(RF_STATE_OFFLINE), "OFFLINE") == 0 &&
          strcmp(rf_state_name(RF_STATE_READY), "READY") == 0 &&
          strcmp(rf_state_name(RF_STATE_TX_ACTIVE), "TX_ACTIVE") == 0 &&
          strcmp(rf_state_name(RF_STATE_RX_CAPTURING), "RX_CAPTURING") == 0 &&
          strcmp(rf_state_name(RF_STATE_DIGITAL_LOOPBACK), "DIGITAL_LOOPBACK") == 0 &&
          strcmp(rf_state_name(RF_STATE_PHYSICAL_LOOPBACK), "PHYSICAL_LOOPBACK") == 0 &&
          strcmp(rf_state_name(RF_STATE_STOPPING), "STOPPING") == 0 &&
          strcmp(rf_state_name(RF_STATE_FAULT), "FAULT") == 0 &&
          strcmp(rf_state_name(RF_STATE_INITIALIZING), "INITIALIZING") == 0);

    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    printf("REAL BOARD: NOT RUN\n");
    printf("RESULT %s\n", fail_count ? "FAIL" : "PASS");
    return fail_count ? 1 : 0;
}
