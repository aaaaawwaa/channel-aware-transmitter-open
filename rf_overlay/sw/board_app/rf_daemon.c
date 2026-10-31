/* rf_daemon.c -- one control client at a time, one command at a time.
 *
 * The framing rules this file implements, and why each is where it is:
 *
 *   - A malformed LINE is answered with a no-id error and the connection
 *     CONTINUES.  The line was consumed whole, so the stream is still in sync;
 *     killing the connection over one bad command would make a client's typo
 *     look like a link failure.
 *   - A line that runs past RF_PROTO_LINE_MAX is OVERFLOW and ENDS the
 *     connection.  Nothing consumed it whole, so there is no boundary left to
 *     resynchronise on.
 *   - A framing failure INSIDE a declared payload (EOF or timeout before
 *     `samples * 4` bytes) is an id-bearing error and ENDS the connection: the
 *     client declared a length, so a short payload is a real desync rather than
 *     a typo.
 *   - A partial line followed by EOF is a clean end (return 0) with no response.
 *     A half-line has no declared length, so there is nothing to report and
 *     nobody left to report it to.
 *
 * The safety rules:
 *
 *   - A payload is read into a scratch buffer and converted and validated IN
 *     FULL before a single word reaches the TX BRAM.  rf_core_write_tx writes
 *     word by word, so validating as it goes would leave a partial frame in the
 *     BRAM on every mid-payload disconnect -- which is precisely the case this
 *     daemon exists to survive.
 *   - Every hardware call happens inside the state machine's `pending` window,
 *     so a second command cannot interleave with a half-finished one.
 *   - The disconnect rule is: stop what is live, and if the TX was live, put it
 *     at the vendor's ceiling attenuation and read it back.  Nothing is
 *     re-initialised or re-configured -- a dropped client must not silently
 *     reprogram the part.
 *
 * One limitation is recorded rather than hidden: leaving a CONTINUOUS RX
 * capture disarms the RX (which is what makes the hardware safe) but the core's
 * own state stays RX_CAPTURING, because the only core-level ops that return it
 * to READY require either a completed capture or a TX-active state.  A later
 * rx.capture after a continuous-capture disconnect is therefore refused until
 * the part is re-initialised.  It is called out in the plan ledger for Task 10
 * rather than worked around here, since the workaround would be a core change
 * and rf_core.c's gates are deliberately fixed.
 */
#include "rf_daemon.h"

#include "platform_rf.h"   /* RF_BRAM_WORDS, RF_SRC_SEL, RF_CAP_ARM, RF_CAP_DONE */
#include "rf_iq.h"         /* the wire<->BRAM converter owns the sample range */
#include "rf_version.h"
#include "rf_headroom.h"
#include "rf_sha256.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Assemble and write whole responses. The largest info result (including gain,
 * timing, identity and both operation lists) fits 3072 bytes; wrapper/newline
 * use the same 4096-byte bound as the client. Every append checks its capacity. */
#define RF_RESP_CAP 4096u
/* The receive window.  Deliberately larger than a line: a burst can span the end
 * of one line and the start of the next, and reframing must not depend on how
 * the transport happens to chunk. */
#define RF_CONN_BUF (RF_PROTO_LINE_MAX + 256u)

#define RF_NO_CORE "no RF core is attached"

typedef struct {
    bool available;
    long capture_request_id;
    uint64_t capture_timestamp_ns;
    unsigned samples, ratio;
    /* A requested settling wait, and the pair of clock reads that prove it ran.
     * Both brackets are non-zero exactly when a wait was requested. */
    unsigned settle_wait_requested_us;
    uint64_t settle_begin_ns, settle_end_ns;
    uint64_t brackets[8];
} capture_timing_t;

typedef struct {
    bool attempted, confirmed;
    int release_code, shutdown_code;
    rf_hw_status_t status;
} headroom_close_t;

struct rf_daemon {
    rf_daemon_config_t cfg;
    rf_core_t *core;
    rf_session_t session;
    rf_headroom_t headroom;
    bool initialization_seen; /* Process lifetime, including released cores. */
    headroom_close_t headroom_close;
    bool headroom_closing;
    bool lease;
    /* Runtime arming state.  NOT part of cfg on purpose: control is off unless a
     * request presented a token this process accepted (cfg.verify_control_token),
     * so installing or starting a release never arms anything. */
    bool armed;
    /* True when `core` came from cfg.control_hw.acquire and must be handed back
     * through cfg.control_hw.release.  The harness's borrowed core is NOT owned and
     * is never released here. */
    bool core_owned;
    /* Control expiry on the injected monotonic clock. Monitoring and refused
     * requests never renew arming; only successful control responses do. */
    uint64_t armed_at_ns;
    uint64_t last_control_ns;
    capture_timing_t last_capture_timing;
    bool capture_timing_clock_unavailable;
    /* The shape of the last accepted tx.load.  tx.start re-prepares from these
     * rather than from anything that arrives alongside tx.start, so the length
     * and the data that is actually in the BRAM cannot disagree. */
    unsigned tx_count;
    bool tx_wrap;
    uint8_t *wire;      /* cfg.max_samples * 4: the payload staging area */
    uint32_t *words;    /* cfg.max_samples: the converted frame */
    uint8_t *frame;     /* header + payload, written in ONE call */
    size_t frame_cap;
};

typedef struct {
    rf_daemon_t *d;
    rf_transport_t *t;
    uint8_t buf[RF_CONN_BUF];
    size_t buf_len;
    size_t buf_pos;
    /* The line being assembled, NUL-terminated after each read so the parser
     * sees a C string.  One byte of the +2 is the terminator; the extra byte
     * lets a line that exactly fills the limit still be terminated. */
    uint8_t line[RF_PROTO_LINE_MAX + 2u];
    size_t line_len;
    bool hello_done;
    bool control_request;
} conn_t;

typedef int (*dispatch_handler_t)(conn_t *c, const rf_proto_request_t *rq);

typedef struct {
    const char *name;
    dispatch_handler_t handler;
    bool closes;
    bool read_only;   /* kept in status-only mode: no RF register is written */
    bool control_op;  /* exactly one of read_only/control_op is true per row */
} dispatch_entry_t;

static int headroom_close(rf_daemon_t *d);
static const dispatch_entry_t dispatch_table[];
static size_t dispatch_count(void);
static bool control_op_advertised(const rf_daemon_t *d, const char *name);
static int apply_disconnect_rule(rf_daemon_t *d);
static void release_owned_core(rf_daemon_t *d);

static void clear_capture_timing(rf_daemon_t *d)
{
    memset(&d->last_capture_timing, 0, sizeof d->last_capture_timing);
    d->capture_timing_clock_unavailable = false;
}

/* Clock reads are strictly opt-in. Equal brackets are legal on a coarse clock;
 * zero (the production clock's failure sentinel) and backwards clocks are not. */
static bool capture_timing_valid(const capture_timing_t *timing)
{
    size_t i;
    if (!timing->capture_timestamp_ns)
        return false;
    for (i = 0; i < 8u; ++i)
        if (!timing->brackets[i] ||
            (i && timing->brackets[i] < timing->brackets[i - 1u]))
            return false;
    if (timing->capture_timestamp_ns < timing->brackets[7])
        return false;
    /* A requested wait must be PROVABLE: both brackets present, ordered, and
     * inside the TX-on..RX-arm gap they were taken from.  An unprovable wait is
     * not a trace we publish -- the wait exists to be measured, so a trace that
     * cannot show it is worse than no trace at all. */
    if (timing->settle_wait_requested_us &&
        (!timing->settle_begin_ns || !timing->settle_end_ns ||
         timing->settle_begin_ns < timing->brackets[1] ||
         timing->settle_end_ns < timing->settle_begin_ns ||
         timing->settle_end_ns > timing->brackets[2]))
        return false;
    return true;
}

/* --------------------------------------------------------------- lifecycle */

int rf_daemon_create(const rf_daemon_config_t *config, rf_daemon_t **out)
{
    rf_daemon_t *d;

    if (!out)
        return -1;
    *out = NULL;
    if (!config || !config->now_ns || config->max_samples == 0u ||
        config->max_samples > RF_BRAM_WORDS ||
        (config->mode != RF_DAEMON_STATUS_ONLY &&
         config->mode != RF_DAEMON_CONTROL_TEST))
        return -1;
    d = (rf_daemon_t *)calloc(1, sizeof *d);
    if (!d)
        return -1;
    d->cfg = *config;
    d->wire = (uint8_t *)malloc((size_t)config->max_samples * 4u);
    d->words = (uint32_t *)malloc((size_t)config->max_samples * sizeof(uint32_t));
    d->frame_cap = (size_t)config->max_samples * 4u + RF_RESP_CAP;
    d->frame = (uint8_t *)malloc(d->frame_cap);
    if (!d->wire || !d->words || !d->frame) {
        rf_daemon_destroy(d);
        return -1;
    }
    rf_state_init(&d->session);
    *out = d;
    return 0;
}

void rf_daemon_destroy(rf_daemon_t *daemon)
{
    if (!daemon)
        return;
    /* Defensive: every exit path disarms first, so this is normally a no-op.  It
     * exists so a caller that forgot cannot leak the O_RDWR mapping (and leave the
     * part enabled) until the process dies. */
    release_owned_core(daemon);
    free(daemon->wire);
    free(daemon->words);
    free(daemon->frame);
    free(daemon);
}

void rf_daemon_attach_core(rf_daemon_t *daemon, rf_core_t *core)
{
    /* An OWNED core is not replaceable by a borrower: the borrower would leak the
     * mapping the daemon is responsible for releasing. */
    if (daemon && !daemon->core_owned &&
        daemon->cfg.mode == RF_DAEMON_CONTROL_TEST)
    {
        if(rf_core_initialization_attempted(daemon->core) ||
           rf_core_initialization_attempted(core)) daemon->initialization_seen=true;
        daemon->core = core;
    }
}

/* The lease is the protocol's single-client rule; session.lease_held is the same
 * fact in the form the state machine gates on, and the two are set together so
 * they cannot disagree. */
int rf_daemon_lease_acquire(rf_daemon_t *daemon)
{
    if (!daemon || daemon->lease)
        return -1;
    daemon->lease = true;
    daemon->session.lease_held = true;
    return 0;
}

void rf_daemon_lease_release(rf_daemon_t *daemon)
{
    if (!daemon)
        return;
    daemon->lease = false;
    daemon->session.lease_held = false;
}

bool rf_daemon_lease_held(const rf_daemon_t *daemon)
{
    return daemon && daemon->lease;
}

/* Hand back an OWNED core: stop the part first, then drop the write-capable
 * hardware.  The order is not a style choice.  rf_core_close() releases the mmap and
 * the SPI/GPIO callbacks but DELIBERATELY does not stop the part or drive a pin
 * (rf_core.c: "stopping is the explicit job of rf_core_safe_shutdown()").  Closing
 * first would therefore leave the part enabled and uncommanded.  Idempotent: a
 * daemon with no owned core does nothing, which is what keeps the harness and every
 * unarmed path unchanged. */
static void release_owned_core(rf_daemon_t *d)
{
    rf_core_t *owned;
    rf_hw_status_t final_status={true,true,false};
    int final_shutdown;

    if (!d || !d->core_owned || !d->core)
        return;
    (void)apply_disconnect_rule(d);
    owned = d->core;
    if(rf_core_initialization_attempted(owned))d->initialization_seen=true;
    /* Normal control setters reject FAULT. A capture timeout can therefore
     * leave SRC_SEL or CAP_ARM high even after do_stop. This owned core still
     * holds the singleton and mappings: use its fault-tolerant shutdown before
     * returning those apertures, including on disconnect and watchdog expiry. */
    final_shutdown=rf_core_safe_shutdown(owned,&final_status);
    if (final_shutdown != RF_ERR_OK)
        fprintf(stderr, "rf_daemon: hardware shutdown could not be confirmed\n");
    /* The last shutdown occurs while the mapping is still owned. Research
     * closure is attested only AFTER this result and its actual path readback,
     * never from the earlier shutdown whose safe state may no longer hold. */
    if(d->headroom_closing) {
        if(d->headroom_close.shutdown_code==RF_ERR_OK)
            d->headroom_close.shutdown_code=final_shutdown;
        d->headroom_close.status=final_status;
    }
    d->core = NULL;
    d->core_owned = false;
    if (d->cfg.control_hw.release)
        d->cfg.control_hw.release(d->cfg.control_hw.ctx, owned);
    /* Control is off and the write aperture is closed, so the daemon no longer holds
     * a part: its state record is void and must not be left claiming READY.  A later
     * arm acquires a FRESH core (OFFLINE) and adopt_core_state() only reconciles when
     * the session is OFFLINE, so a stale READY made the next session unable to init
     * ("READY"), stop ("not initialized") or configure (core not READY) until the
     * daemon restarted.  Measured on the board in Task 12 Step 5a (record §34).  The
     * lease is untouched -- rf_state_detach() preserves it. */
    rf_state_detach(&d->session);
}

/* Turning arming off is one state change with one audit line, wherever it comes
 * from: rf_daemon.h says why it is a function rather than a field assignment at
 * each caller.  Bringing an OWNED core to rest and releasing the write-capable
 * hardware belongs here too (see rf_daemon.h): the exit paths call this function and
 * only this function, so a release that lived anywhere else would leak the O_RDWR
 * mapping on the paths nobody tested.  A core the daemon does not own is left alone.
 *
 * The reason string is the caller's: it is what makes the audit trail answer "who
 * ended control?" -- an operator's control.disarm, the unit stopping the daemon, a
 * fatal error, or the watchdog. */
int rf_daemon_disarm(rf_daemon_t *daemon, const char *reason)
{
    if (daemon) {
        clear_capture_timing(daemon);
        if (daemon->headroom.active) {
            /* headroom_close seals before it calls disarm, so this is not
             * recursive. Watchdog/direct disarm must do the full close too. */
            return headroom_close(daemon)==RF_ERR_OK?1:-1;
        }
    }
    if (!daemon || !daemon->armed)
        return 0;
    release_owned_core(daemon);
    daemon->armed = false;
    daemon->armed_at_ns = 0u;
    daemon->last_control_ns = 0u;
    fprintf(stderr, "rf_daemon: control disarmed (%s)\n",
            reason && reason[0] ? reason : "unspecified");
    return 1;
}

/* The armed-client watchdog: the declared timeout, in one place, on the injected
 * clock.  It returns whatever rf_daemon_disarm returns, so "1" means the same thing it
 * means everywhere else -- the state changed and one audit line was written -- and
 * there is still exactly one disarm entry point (pinned by
 * rf_overlay/sw/tests/test_control_split_contract.py).  It does NOT touch the session
 * or the hardware: control being off is a statement about this process. */
int rf_daemon_watchdog_tick(rf_daemon_t *daemon, uint64_t now_ns)
{
    uint64_t since;

    if (!daemon || !daemon->armed)
        return 0;
    /* last_control_ns records successful control; armed_at_ns is the
     * fallback for a daemon armed before any client sentence was recorded. */
    since = daemon->last_control_ns ? daemon->last_control_ns : daemon->armed_at_ns;
    if (now_ns - since < RF_DAEMON_ARMED_IDLE_TIMEOUT_NS)
        return 0;
    return rf_daemon_disarm(daemon, "watchdog: the armed client went silent");
}

const rf_session_t *rf_daemon_session(const rf_daemon_t *daemon)
{
    return daemon ? &daemon->session : NULL;
}

/* ------------------------------------------------------------------ output */

static int send_all(conn_t *c, const void *p, size_t n)
{
    if (n == 0)
        return 0;
    return c->t->write_all(c->t->ctx, p, n) == 0 ? 0 : -1;
}

/* Build the response, append its newline, send it once. */
static int respond(conn_t *c, const char *text)
{
    char line[RF_RESP_CAP];
    size_t n = strlen(text);

    if (n + 1u >= sizeof line)
        return -1;
    memcpy(line, text, n);
    line[n] = '\n';
    return send_all(c, line, n + 1u);
}

static int respond_ok(conn_t *c, long id, const char *result_json)
{
    char buf[RF_RESP_CAP];

    int rc;

    if (rf_proto_write_ok(buf, sizeof buf, id, result_json) < 0)
        return -1;
    rc = respond(c, buf);
    if (rc == 0 && c->control_request && c->d->armed)
        c->d->last_control_ns = c->d->cfg.now_ns();
    return rc;
}

static int respond_err_id(conn_t *c, long id, rf_proto_code_t code,
                          const char *message)
{
    char buf[RF_RESP_CAP];

    if (rf_proto_write_error(buf, sizeof buf, id, code, message) < 0)
        return -1;
    return respond(c, buf);
}

static int respond_err(conn_t *c, rf_proto_code_t code, const char *message)
{
    char buf[RF_RESP_CAP];

    if (rf_proto_write_error_no_id(buf, sizeof buf, code, message) < 0)
        return -1;
    return respond(c, buf);
}

/* Answer a parsed request with an error.  The id is echoed when the request had
 * one, which is always for a request the parser accepted -- the no-id form is
 * reserved for messages the client cannot have asked for. */
static int refuse(conn_t *c, const rf_proto_request_t *rq, rf_proto_code_t code,
                  const char *message)
{
    /* Every refusal in an active research connection is terminal, including
     * validation performed before a research handler or by read-only handlers. */
    if(c->d->headroom.active) (void)headroom_close(c->d);
    if (rq->has_id)
        return respond_err_id(c, rq->id, code, message) == 0 ? 0 : -1;
    return respond_err(c, code, message) == 0 ? 0 : -1;
}

static int arg_error(conn_t *c, const rf_proto_request_t *rq, const char *key)
{
    char msg[160];

    snprintf(msg, sizeof msg,
             "argument '%s' is missing, of the wrong type, or out of range", key);
    return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT, msg);
}

/* Append, refusing to overflow.  A result that does not fit is an error rather
 * than a truncation: half a JSON object is not a response. */
static int append(char *buf, size_t cap, size_t *used, const char *fmt, ...)
{
    va_list ap;
    int n;

    if (*used >= cap)
        return -1;
    va_start(ap, fmt);
    n = vsnprintf(buf + *used, cap - *used, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= cap - *used)
        return -1;
    *used += (size_t)n;
    return 0;
}

/* The pending operation as a JSON literal.  It emits the quotes itself, so it is
 * a JSON value and not a bare name -- callers use it unfolded. */
static const char *op_json(rf_operation_t op)
{
    switch (op) {
    case RF_OP_NONE:              return "null";
    case RF_OP_INIT:              return "\"init\"";
    case RF_OP_CONFIGURE:         return "\"configure\"";
    case RF_OP_TX_START:          return "\"tx.start\"";
    case RF_OP_RX_CAPTURE:        return "\"rx.capture\"";
    case RF_OP_DIGITAL_LOOPBACK:  return "\"loopback.digital.start\"";
    case RF_OP_DIGITAL_CAPTURE:   return "\"loopback.digital.capture\"";
    case RF_OP_PHYSICAL_CAPTURE: return "\"loopback.physical.capture\"";
    case RF_OP_PHYSICAL_LOOPBACK: return "\"loopback.physical.start\"";
    case RF_OP_STOP:              return "\"stop\"";
    }
    return "null";
}

/* The state's fields, WITHOUT the enclosing braces, so state.get and
 * status.subscribe cannot disagree about what the state is called. */
static int state_fields(rf_daemon_t *d, char *out, size_t cap)
{
    size_t used = 0;

    if (append(out, cap, &used,
               "\"state\":\"%s\",\"pending\":%s,\"pending_op\":%s,"
               "\"lease_held\":%s,\"max_samples\":%u",
               rf_state_name(d->session.state),
               d->session.pending ? "true" : "false",
               op_json(d->session.pending_op),
               d->session.lease_held ? "true" : "false", d->cfg.max_samples) != 0)
        return -1;
    return (int)used;
}

static int state_result(rf_daemon_t *d, char *out, size_t cap)
{
    char fields[384];
    int n;

    if (state_fields(d, fields, sizeof fields) < 0)
        return -1;
    n = snprintf(out, cap, "{%s}", fields);
    if (n < 0 || (size_t)n >= cap)
        return -1;
    return n;
}

static int readback_result(const rf_readback_t *rb, char *out, size_t cap)
{
    size_t used = 0;

    if (append(out, cap, &used,
               "{\"lo_hz\":%llu,\"sample_rate_hz\":%u,\"rx_bandwidth_hz\":%u,"
               "\"tx_bandwidth_hz\":%u,\"attenuation_mdB\":%u,"
               "\"phy_mode_1r1t\":%u,\"product_id\":%u,"
               "\"hardware_verified\":%s}",
               (unsigned long long)rb->lo_hz, rb->sample_rate_hz,
               rb->rx_bandwidth_hz, rb->tx_bandwidth_hz, rb->attenuation_mdB,
               (unsigned)rb->phy_mode_1r1t, (unsigned)rb->product_id,
               rb->hardware_verified ? "true" : "false") != 0)
        return -1;
    return (int)used;
}

/* --------------------------------------------------------------- transport */

/* 0 = a byte in *out, 1 = EOF, -1 = the deadline expired, -2 = I/O error. */
static int next_byte(conn_t *c, uint8_t *out)
{
    int r;

    if (c->buf_pos < c->buf_len) {
        *out = c->buf[c->buf_pos++];
        return 0;
    }
    c->buf_pos = 0;
    c->buf_len = 0;
    r = c->t->read(c->t->ctx, c->buf, sizeof c->buf);
    if (r > 0) {
        if ((size_t)r > sizeof c->buf)
            return -2;
        c->buf_len = (size_t)r;
        c->buf_pos = 1;
        *out = c->buf[0];
        return 0;
    }
    if (r == 0)
        return 1;
    return (r == RF_TRANSPORT_TIMEOUT) ? -1 : -2;
}

/* 1 = a line is ready in c->line/c->line_len, 0 = EOF (clean, whether or not a
 * partial line was pending), -1 = timeout, -2 = I/O error, -3 = the line ran
 * past the limit. */
static int read_line(conn_t *c)
{
    /* The previous line has already been consumed whole by its handler, so this
     * is the one place a new one starts. */
    c->line_len = 0;
    for (;;) {
        uint8_t b;
        int r = next_byte(c, &b);

        if (r == 1)
            return 0;
        if (r != 0)
            return r;
        if (b == '\n') {
            if (c->line_len > 0u && c->line[c->line_len - 1u] == '\r')
                --c->line_len;
            c->line[c->line_len] = '\0';
            return 1;
        }
        if (c->line_len >= (size_t)RF_PROTO_LINE_MAX + 1u)
            return -3;
        c->line[c->line_len++] = b;
    }
}

/* 0 = all n bytes read, 1 = EOF first, -1 = timeout, -2 = I/O error. */
static int read_exact(conn_t *c, uint8_t *dst, size_t n)
{
    size_t got = 0;

    while (got < n) {
        int r;

        if (c->buf_pos < c->buf_len) {
            size_t avail = c->buf_len - c->buf_pos;
            size_t take = (avail < n - got) ? avail : (n - got);

            memcpy(dst + got, c->buf + c->buf_pos, take);
            c->buf_pos += take;
            got += take;
            continue;
        }
        c->buf_pos = 0;
        c->buf_len = 0;
        r = c->t->read(c->t->ctx, c->buf, sizeof c->buf);
        if (r > 0) {
            if ((size_t)r > sizeof c->buf)
                return -2;
            c->buf_len = (size_t)r;
            continue;
        }
        if (r == 0)
            return 1;
        return (r == RF_TRANSPORT_TIMEOUT) ? -1 : -2;
    }
    return 0;
}

/* -------------------------------------------------------------- hardware */

static rf_proto_code_t map_rf_error(int err)
{
    switch (err) {
    case RF_ERR_INVALID_ARGUMENT: return RF_PROTO_E_INVALID_ARGUMENT;
    case RF_ERR_INVALID_STATE:    return RF_PROTO_E_INVALID_STATE;
    case RF_ERR_TIMEOUT:          return RF_PROTO_E_TIMEOUT;
    case RF_ERR_HARDWARE_IO:
    case RF_ERR_NO_MEMORY:
    default:                      return RF_PROTO_E_HARDWARE_IO;
    }
}

static const char *rf_error_name(int err)
{
    switch (err) {
    case RF_ERR_INVALID_ARGUMENT: return "the core rejected the argument";
    case RF_ERR_INVALID_STATE:    return "the core is not in a state that allows this";
    case RF_ERR_TIMEOUT:          return "the hardware did not respond in time";
    case RF_ERR_NO_MEMORY:        return "the core ran out of memory";
    case RF_ERR_HARDWARE_IO:
    default:                      return "the hardware did not confirm the change";
    }
}

/* The observed PL status.  A read that fails cannot CONFIRM anything, so it
 * reports the worst case (still running) rather than a hopeful idle: the state
 * machine judges a stop from this, and an unverifiable stop is a fault. */
static int read_hw_status(rf_daemon_t *d, rf_hw_status_t *out)
{
    uint32_t src_cfg = 0, len = 0, pos = 0, cap_cfg = 0, cap_status = 0;

    out->tx_active = true;
    out->rx_busy = true;
    out->rx_done = false;
    if (!d->core)
        return -1;
    if (rf_core_get_tx_path(d->core, &src_cfg, &len, &pos) != RF_ERR_OK)
        return -1;
    if (rf_core_get_rx_path(d->core, &cap_cfg, &cap_status) != RF_ERR_OK)
        return -1;
    out->rx_done = RF_CAP_DONE(cap_status) != 0u;
    out->tx_active = (src_cfg & RF_SRC_SEL) != 0u;
    /* Armed and not finished is "still capturing".  An armed capture that HAS
     * finished is not busy -- it is a full buffer waiting to be drained. */
    out->rx_busy = (cap_cfg & RF_CAP_ARM) != 0u && !out->rx_done;
    return 0;
}

/* Adopt the core's state while the daemon has no record of its own.  A part that
 * something else brought up -- the board application's own init, or a test that
 * attaches a live core -- is usable and must not be refused for the daemon's
 * ignorance.  Once this daemon has driven the part, its session is the record of
 * what it did with it, and the core cannot know about a composition like a
 * physical loopback, which is assembled from PL-level ops that deliberately do
 * not move the core's state. */
static void adopt_core_state(rf_daemon_t *d)
{
    if (d->core && !d->session.pending && d->session.state == RF_STATE_OFFLINE)
        d->session.state = rf_core_state(d->core);
}

/* End a transaction that failed before a readback could judge it.  The session
 * lands in FAULT only when the core did: an argument or state refusal happened
 * before any hardware was touched, so it changes nothing. */
static void op_failed(rf_daemon_t *d, rf_state_error_t *err)
{
    rf_state_failed(&d->session,
                    d->core && rf_core_state(d->core) == RF_STATE_FAULT, err);
}

/* Bring the part to rest using the owner-free PL-level operations, then report
 * what the hardware actually says.  The caller must judge the result from
 * `observed` (rf_state_finish does), never from this call's own success: a stop
 * that was asked for and did not take is exactly the case the state machine
 * exists to catch.
 *
 * rf_core_safe_shutdown is deliberately NOT used: it requires rf_owner == core,
 * which the daemon never claims, so it would return INVALID_STATE instead of
 * stopping anything.
 *
 * Order matters.  rf_core_stop_tx clears src_sel AND verifies the read position
 * moved; clearing src_sel by hand first would leave tx_prepared set with no way
 * left to confirm the stop. */
static void do_stop(rf_daemon_t *d, rf_hw_status_t *observed)
{
    rf_hw_status_t before;
    int known;

    observed->tx_active = false;
    observed->rx_busy = false;
    observed->rx_done = false;
    if (!d->core)
        return;
    known = (read_hw_status(d, &before) == 0);
    if (rf_core_state(d->core) == RF_STATE_DIGITAL_LOOPBACK)
        (void)rf_core_set_digital_loopback(d->core, false, NULL);
    if (!known || before.rx_busy)
        (void)rf_core_set_rx_arm(d->core, false, NULL);
    if (!known || before.tx_active)
        (void)rf_core_stop_tx(d->core, NULL);
    if (read_hw_status(d, observed) != 0) {
        observed->tx_active = true;
        observed->rx_busy = true;
        observed->rx_done = false;
        return;
    }
    /* The PL saying "nothing is live" is not the whole of a confirmed stop: the
     * CORE must also be somewhere this daemon can work from.  A CONTINUOUS RX
     * capture is the case that breaks that.  Disarming it is what makes the
     * hardware safe, but the core keeps its RX_CAPTURING state, and there is no
     * core-level exit from it: rf_core_read_rx wants a COMPLETED capture and
     * rf_core_stop_tx wants an active TX.  rf_core.c has no stop_rx (escalated
     * separately as a core-level gap, not patched here), so the part is unusable
     * until the core is re-created.
     *
     * Reporting READY would therefore be a lie -- the next client's every command
     * would be refused -- so this stop is reported as unconfirmed and the session
     * lands in FAULT.  That is not a recovery mechanism; it is the honest state
     * for a part the daemon cannot drive.
     *
     * READY is the ONLY state that counts as stopped.  An OFFLINE core is not
     * workable either, and although op_stop refuses a stop against one before
     * reaching here, leaving it out of this test would mean re-introducing the
     * same false READY the moment any other path reaches do_stop. */
    if (rf_core_state(d->core) != RF_STATE_READY)
        observed->rx_busy = true;
}

/* The disconnect rule, and nothing more: what was live is stopped, and a TX that
 * was live is put at the vendor's ceiling attenuation and read back.  Nothing is
 * re-initialised or re-configured -- a dropped client must not reprogram the
 * part.  A disconnect while nothing is live makes no hardware call at all. */
static int apply_disconnect_rule(rf_daemon_t *d)
{
    rf_state_error_t err;
    rf_hw_status_t before, observed;
    uint32_t readback = 0;
    int known, tx_was_live;

    clear_capture_timing(d);

    err.code = RF_PROTO_E_NONE;
    err.message = NULL;
    /* No hardware to bring to rest, so the rule must not touch the session AT ALL.
     * rf_state_safe_stop() MUTATES the session -- it arms a pending STOP -- so it may
     * only be called when this function can go on to finish that transaction.  The
     * previous order called it first and then bailed out at `if (!d->core) return 0;`
     * AFTER the core had already been released by the client's own disarm, which left
     * `pending` set forever: every later operation answered BUSY ("an operation is
     * already in flight") until the daemon restarted.  Found on the board in Task 12
     * Step 5a (delivery record §32) and reproduced offline by
     * test_disarm_then_connection_end_leaves_no_pending(). */
    if (!d->core)
        return 0;
    /* Returns 1 when a stop is needed.  0 is "nothing was live", and nothing
     * else in this function has anything to do either. */
    if (rf_state_safe_stop(&d->session, &err) != 1)
        return 0;
    known = (read_hw_status(d, &before) == 0);
    tx_was_live = !known || before.tx_active;
    do_stop(d, &observed);
    (void)rf_state_finish(&d->session, observed, &err);
    /* do_stop reaches the part through the READY-gated setters, so a core that has
     * FAULTED cannot be stopped by it at all.  Measured offline: a timed-out
     * physical capture followed by a plain disconnect kept the part SOURCING while
     * CAP_ARM was released, and the source only came down when the 30 s watchdog
     * happened to expire -- not a shutoff anybody verified.
     *
     * RF_STATE_FAULT is the discriminator, and deliberately NOT "something is
     * still live".  A core stuck in RX_CAPTURING also leaves activity behind, but
     * that is the separate, known core-level gap (rf_core.c has no stop_rx): it
     * must keep reporting an unconfirmed stop, because forcing the part down would
     * be a different action wearing the same name.  A faulted core is merely
     * uncommandable through the gate, which is exactly what the state-free release
     * is for -- and it keeps the FAULT visible, which a shutdown would erase.
     *
     * It runs only in that case, so an ordinary disconnect performs no extra
     * hardware write, and it does NOT release the core: a borrowed core still
     * belongs to its owner, and an owned one is released by rf_daemon_disarm. */
    if (d->core && rf_core_state(d->core) == RF_STATE_FAULT &&
        (observed.tx_active || observed.rx_busy) &&
        rf_core_release_part_keep_state(d->core, &observed) != RF_ERR_OK)
        return -1;
    if (!tx_was_live)
        return 0;
    /* rf_core_set_tx_attenuation_safe only returns OK once IT has read the value
     * back and found it equal to the request (it faults otherwise), so the
     * confirmation lives in the core and re-comparing the returned value here
     * would be a second, unreachable copy of the same rule.  What this call's
     * failure does NOT mean: the stop itself is already confirmed above, so the
     * session stays at whatever the stop produced -- the caller learns of the
     * unset attenuation from this function's -1. */
    if (rf_core_set_tx_attenuation_safe(d->core, RF_SAFE_ATTENUATION_MDB,
                                       &readback) != RF_ERR_OK)
        return -1;
    return 0;
}

/* ------------------------------------------------------------------- ops */

/* parse a required non-negative integer argument */
static int get_uint(const rf_proto_request_t *rq, const char *key, long *out)
{
    if (rf_proto_arg_int(rq->args, key, out) != RF_PROTO_ARG_OK || *out < 0)
        return -1;
    return 0;
}

/* An optional integer argument.  0 = present and valid (written to *out),
 * 1 = absent, -1 = present but unusable.  Absent and invalid are different
 * answers, so a caller cannot turn a typo into a default. */
static int opt_uint(const rf_proto_request_t *rq, const char *key, long *out)
{
    int r = rf_proto_arg_int(rq->args, key, out);

    if (r == RF_PROTO_ARG_MISSING)
        return 1;
    if (r != RF_PROTO_ARG_OK || *out < 0)
        return -1;
    return 0;
}

/* An optional 0/1 argument, with the same three-way answer. */
static int opt_flag(const rf_proto_request_t *rq, const char *key, long *out)
{
    int r = opt_uint(rq, key, out);

    if (r != 0)
        return r;
    if (*out != 0 && *out != 1)
        return -1;
    return 0;
}

/* An optional boolean argument.  A JSON boolean is not an integer, so this is a
 * separate getter rather than a flag read with rf_proto_arg_int. */
static int opt_bool(const rf_proto_request_t *rq, const char *key, bool *out)
{
    int r = rf_proto_arg_bool(rq->args, key, out);

    if (r == RF_PROTO_ARG_MISSING)
        return 1;
    if (r != RF_PROTO_ARG_OK)
        return -1;
    return 0;
}

static int op_hello(conn_t *c, const rf_proto_request_t *rq)
{
    char result[256];
    char msg[128];
    long major = 0, minor = 0;

    if (get_uint(rq, "major", &major) != 0 || get_uint(rq, "minor", &minor) != 0) {
        (void)refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                     "protocol.hello requires integer major and minor");
        return -1;
    }
    if (major != (long)RF_PROTOCOL_MAJOR) {
        snprintf(msg, sizeof msg, "the daemon speaks protocol major %u",
                 (unsigned)RF_PROTOCOL_MAJOR);
        (void)refuse(c, rq, RF_PROTO_E_VERSION_MISMATCH, msg);
        return -1;
    }
    c->hello_done = true;
    snprintf(result, sizeof result,
             "{\"daemon\":\"%s\",\"protocol_major\":%u,\"protocol_minor\":%u,"
             "\"max_samples\":%u}",
             RF_DAEMON_VERSION, (unsigned)RF_PROTOCOL_MAJOR,
             (unsigned)RF_PROTOCOL_MINOR, c->d->cfg.max_samples);
    return respond_ok(c, rq->id, result) == 0 ? 0 : -1;
}

static int op_device_info(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_readback_t rb;
    rf_gain_observation_t gain;
    bool observe_gain = false;
    bool observe_timing = false;
    int gain_rc = RF_ERR_INVALID_STATE;
    uint64_t gain_started = 0, gain_finished = 0;
    char rbjson[256];
    /* Gain and timing can both be requested; append checks every addition.
     * The full uint64/identity/all-ops fixture is 1812 bytes; raising samples
     * 64->4096 adds two characters, comfortably below both bounds. */
    char out[3072];
    size_t used = 0;
    size_t emitted = 0;
    size_t i;

    if (opt_bool(rq, "observe_rx_gain", &observe_gain) < 0)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "observe_rx_gain requires a boolean");
    if (opt_bool(rq, "observe_timing", &observe_timing) < 0)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "observe_timing requires a boolean");
    if (observe_gain) {
        adopt_core_state(d);
        if (d->core && d->session.state == RF_STATE_READY && !d->session.pending) {
            gain_started = d->cfg.now_ns();
            gain_rc = rf_core_get_gain_observation(d->core, &gain);
            gain_finished = d->cfg.now_ns();
        }
    }
    if (append(out, sizeof out, &used,
               "{\"daemon\":\"%s\",\"protocol_major\":%u,\"protocol_minor\":%u,"
               "\"max_samples\":%u,\"mode\":\"%s\",\"core\":%s",
               RF_DAEMON_VERSION, (unsigned)RF_PROTOCOL_MAJOR,
               (unsigned)RF_PROTOCOL_MINOR, d->cfg.max_samples,
               d->cfg.mode == RF_DAEMON_STATUS_ONLY ? "status_only" : "control_test",
               d->core ? "true" : "false") != 0)
        return -1;
    if (d->cfg.mode == RF_DAEMON_STATUS_ONLY) {
        if (append(out, sizeof out, &used, ",\"readback\":null") != 0)
            return -1;
    } else if (d->core && rf_core_get_readback(d->core, &rb) == RF_ERR_OK) {
        snprintf(rbjson, sizeof rbjson,
                 "{\"product_id\":%u,\"lo_hz\":%llu,\"sample_rate_hz\":%u,"
                 "\"attenuation_mdB\":%u}",
                 (unsigned)rb.product_id, (unsigned long long)rb.lo_hz,
                 rb.sample_rate_hz, rb.attenuation_mdB);
        if (append(out, sizeof out, &used, ",\"readback\":%s", rbjson) != 0)
            return -1;
    } else if (d->core) {
        /* An unavailable readback is reported as unavailable, with the reason.
         * It is never filled in with a zero that a reader would take for a
         * measurement. */
        if (append(out, sizeof out, &used,
                   ",\"readback\":null,\"readback_unavailable\":"
                   "\"the part did not answer the readback\"") != 0)
            return -1;
    }
    if (gain_rc == RF_ERR_OK) {
        if (append(out, sizeof out, &used,
                   ",\"gain_observation\":{\"available\":true,\"source\":\"hardware\","
                   "\"scope\":\"sequential_reads_not_atomic\","
                   "\"started_at_monotonic_ns\":%llu,\"finished_at_monotonic_ns\":%llu,"
                   "\"rx_enable_raw\":%u,\"agc_config1_raw\":%u,\"agc_config2_raw\":%u,"
                   "\"mode_rx1\":%u,\"gain_index_before\":%u,\"gain_index_after\":%u,"
                   "\"digital_gain_code\":%u,\"index_stable\":%s}",
                   (unsigned long long)gain_started, (unsigned long long)gain_finished,
                   (unsigned)gain.rx_enable_raw, (unsigned)gain.agc_config1_raw,
                   (unsigned)gain.agc_config2_raw, (unsigned)gain.mode_rx1,
                   (unsigned)gain.gain_index_before, (unsigned)gain.gain_index_after,
                   (unsigned)gain.digital_gain_code, gain.index_stable ? "true" : "false") != 0)
            return -1;
    } else {
        const char *reason = !observe_gain ? "NOT_REQUESTED" :
                             gain_rc == RF_ERR_HARDWARE_IO ? "HARDWARE_IO" : "NOT_READY_OR_UNSUPPORTED";
        if (append(out, sizeof out, &used,
                   ",\"gain_observation\":null,\"gain_observation_unavailable\":\"%s\"",
                   reason) != 0)
            return -1;
    }
    if (observe_timing && d->last_capture_timing.available) {
        const capture_timing_t *timing = &d->last_capture_timing;
        static const char *const keys[8] = {
            "tx_on_begin_ns", "tx_on_end_ns", "rx_arm_begin_ns", "rx_arm_end_ns",
            "drain_begin_ns", "drain_end_ns", "tx_off_begin_ns", "tx_off_end_ns"
        };
        if (append(out, sizeof out, &used,
                   ",\"last_capture_timing\":{\"available\":true,"
                   "\"scope\":\"monotonic_api_brackets_not_sample_edges\","
                   "\"capture_request_id\":%ld,\"capture_timestamp_ns\":%llu,"
                   "\"samples\":%u,\"ratio\":%u",
                   timing->capture_request_id,
                   (unsigned long long)timing->capture_timestamp_ns,
                   timing->samples, timing->ratio) != 0)
            return -1;
        for (i = 0; i < 8u; ++i)
            if (append(out, sizeof out, &used, ",\"%s\":%llu", keys[i],
                       (unsigned long long)timing->brackets[i]) != 0)
                return -1;
        /* Additive: the requested wait is always reported, and its brackets only
         * when one was requested.  A reader that ignores these fields sees the
         * minor-4 shape unchanged. */
        if (append(out, sizeof out, &used, ",\"settle_wait_requested_us\":%u",
                   timing->settle_wait_requested_us) != 0)
            return -1;
        if (timing->settle_wait_requested_us &&
            append(out, sizeof out, &used,
                   ",\"settle_begin_ns\":%llu,\"settle_end_ns\":%llu",
                   (unsigned long long)timing->settle_begin_ns,
                   (unsigned long long)timing->settle_end_ns) != 0)
            return -1;
        if (append(out, sizeof out, &used, "}") != 0)
            return -1;
    } else {
        const char *reason = !observe_timing ? "NOT_REQUESTED" :
                             d->capture_timing_clock_unavailable ?
                             "CLOCK_UNAVAILABLE" : "NO_CAPTURE_TIMING";
        if (append(out, sizeof out, &used,
                   ",\"last_capture_timing\":null,\"capture_timing_unavailable\":\"%s\"",
                   reason) != 0)
            return -1;
    }
    /* The release identity the startup manifest check read, carried here in the
     * config so no file is touched at request time.  This is an IDENTITY report
     * -- which manifest this process was started with -- NOT attestation: it is
     * not evidence that the .bit on disk, or the one loaded in the PL, is the
     * file these hashes name.  Comparing the artifacts against them is the
     * deploy launcher's gate (rf_overlay/sw/rf_overlay_launcher.py); this daemon
     * never reads the .bit or the .hwh.  Do not read these as proof of what is
     * running. */
    if (append(out, sizeof out, &used,
               ",\"release_id\":\"%s\",\"bit_sha256\":\"%s\",\"hwh_sha256\":\"%s\"",
               d->cfg.identity.release_id, d->cfg.identity.bit_sha256,
               d->cfg.identity.hwh_sha256) != 0)
        return -1;
    if (append(out, sizeof out, &used, ",\"armed\":%s",
               d->armed ? "true" : "false") != 0)
        return -1;
    if (append(out, sizeof out, &used, ",\"capabilities\":{\"ops\":[") != 0)
        return -1;
    for (i = 0; i < dispatch_count(); ++i) {
        if (d->cfg.mode == RF_DAEMON_STATUS_ONLY && dispatch_table[i].control_op)
            continue;
        if (append(out, sizeof out, &used, "%s\"%s\"", emitted++ ? "," : "",
                   dispatch_table[i].name) != 0)
            return -1;
    }
    /* The control operations reachable right now, advertised separately so the
     * read-only `ops` list stays exactly the status-only allowlist the client
     * verifies.  A client therefore knows whether it may arm without having to
     * try it, and a phase-1 client that ignores this field is unaffected. */
    if (append(out, sizeof out, &used, "],\"control_ops\":[") != 0)
        return -1;
    emitted = 0;
    for (i = 0; i < dispatch_count(); ++i) {
        if (!dispatch_table[i].control_op ||
            !control_op_advertised(d, dispatch_table[i].name))
            continue;
        if (append(out, sizeof out, &used, "%s\"%s\"", emitted++ ? "," : "",
                   dispatch_table[i].name) != 0)
            return -1;
    }
    if (append(out, sizeof out, &used,
        "],\"headroom\":{\"study\":\"HEADROOM-1\",\"protocol_minor\":6,"
        "\"initialization_budget\":1,\"attempt_budget\":5,\"used\":%s,"
        "\"active\":%s,\"operations\":[\"headroom.begin\",\"headroom.load\","
        "\"headroom.capture\",\"headroom.accept\",\"headroom.abort\"]}}",
        d->headroom.used?"true":"false",d->headroom.active?"true":"false") != 0)
        return -1;
    if (append(out, sizeof out, &used, "}") != 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_state_get(conn_t *c, const rf_proto_request_t *rq)
{
    char out[384];

    if (state_result(c->d, out, sizeof out) < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_status_subscribe(conn_t *c, const rf_proto_request_t *rq)
{
    char fields[384];
    char out[512];
    int n;

    if (state_fields(c->d, fields, sizeof fields) < 0)
        return -1;
    n = snprintf(out, sizeof out, "{%s,\"subscribed\":true}", fields);
    if (n < 0 || (size_t)n >= sizeof out)
        return -1;
    /* The subscription is this connection's wish only.  No unsolicited event is
     * pushed yet, so there is nothing to arm on the daemon side. */
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_pl_status_get(conn_t *c, const rf_proto_request_t *rq)
{
    rf_pl_status_t st;
    char out[2048];
    char tx_source[8], tx_length[24], tx_position[24];
    char rx_armed[8], rx_done[8], rx_position[24];
    char status_diag[224], phy_mode_diag[64], idelay_en_diag[64];
    char idelay_tap_diag[64], src_cfg_diag[64], cap_cfg_diag[64];
    char cap_ratio_diag[64], cap_len_diag[64], cap_status_diag[128];
    int n;
    rf_pl_status_sample(c->d->cfg.pl_reader, c->d->cfg.now_ns(), &st);
    snprintf(tx_source, sizeof tx_source, "%s",
             st.tx_source_valid ? (st.tx_source_selected ? "true" : "false") : "null");
    snprintf(tx_length, sizeof tx_length, st.tx_length_valid ? "%u" : "null",
             st.tx_length_words);
    snprintf(tx_position, sizeof tx_position, st.tx_position_valid ? "%u" : "null",
             st.tx_position_words);
    snprintf(rx_armed, sizeof rx_armed, "%s",
             st.rx_armed_valid ? (st.rx_armed ? "true" : "false") : "null");
    snprintf(rx_done, sizeof rx_done, "%s",
             st.rx_done_valid ? (st.rx_done ? "true" : "false") : "null");
    snprintf(rx_position, sizeof rx_position, st.rx_position_valid ? "%u" : "null",
             st.rx_position_words);
    /* Optional, additive diagnostic fields.  These are raw register reads from
     * the same read-only PL aperture; they do not change or gate the result
     * above.  Config bytes are NOT IDELAYE2 CNTVALUEOUT and no RX frame-valid
     * signal is available here, so those two are named as unavailable. */
    if (st.status_valid) {
        snprintf(status_diag, sizeof status_diag,
                 "{\"valid\":true,\"raw\":%u,\"clk_seen\":%s,\"win_done\":%s,"
                 "\"rso\":%s,\"win_active\":%s,\"win_seq\":%u,"
                 "\"win_cycles\":%u,\"win_log2\":%u}",
                 st.status_word,
                 RF_STATUS_CLK_SEEN(st.status_word) ? "true" : "false",
                 RF_STATUS_WIN_DONE(st.status_word) ? "true" : "false",
                 RF_STATUS_RSO(st.status_word) ? "true" : "false",
                 RF_STATUS_WIN_ACTIVE(st.status_word) ? "true" : "false",
                 RF_STATUS_WIN_SEQ(st.status_word),
                 RF_STATUS_WIN_CYCLES(st.status_word),
                 RF_STATUS_WIN_LOG2(st.status_word));
    } else {
        snprintf(status_diag, sizeof status_diag, "{\"valid\":false,\"raw\":null}");
    }
    if (st.phy_mode_valid)
        snprintf(phy_mode_diag, sizeof phy_mode_diag,
                 "{\"valid\":true,\"raw\":%u}", st.phy_mode);
    else
        snprintf(phy_mode_diag, sizeof phy_mode_diag, "{\"valid\":false,\"raw\":null}");
    if (st.idelay_en_valid)
        snprintf(idelay_en_diag, sizeof idelay_en_diag,
                 "{\"valid\":true,\"raw\":%u}", st.idelay_en);
    else
        snprintf(idelay_en_diag, sizeof idelay_en_diag, "{\"valid\":false,\"raw\":null}");
    if (st.idelay_tap_valid)
        snprintf(idelay_tap_diag, sizeof idelay_tap_diag,
                 "{\"valid\":true,\"raw\":%u}", st.idelay_tap);
    else
        snprintf(idelay_tap_diag, sizeof idelay_tap_diag, "{\"valid\":false,\"raw\":null}");
    if (st.src_cfg_valid)
        snprintf(src_cfg_diag, sizeof src_cfg_diag,
                 "{\"valid\":true,\"raw\":%u}", st.src_cfg);
    else
        snprintf(src_cfg_diag, sizeof src_cfg_diag, "{\"valid\":false,\"raw\":null}");
    if (st.cap_cfg_valid)
        snprintf(cap_cfg_diag, sizeof cap_cfg_diag,
                 "{\"valid\":true,\"raw\":%u}", st.cap_cfg);
    else
        snprintf(cap_cfg_diag, sizeof cap_cfg_diag, "{\"valid\":false,\"raw\":null}");
    if (st.cap_ratio_valid)
        snprintf(cap_ratio_diag, sizeof cap_ratio_diag,
                 "{\"valid\":true,\"raw\":%u}", st.cap_ratio);
    else
        snprintf(cap_ratio_diag, sizeof cap_ratio_diag, "{\"valid\":false,\"raw\":null}");
    if (st.cap_len_valid)
        snprintf(cap_len_diag, sizeof cap_len_diag,
                 "{\"valid\":true,\"raw\":%u}", st.cap_len);
    else
        snprintf(cap_len_diag, sizeof cap_len_diag, "{\"valid\":false,\"raw\":null}");
    if (st.cap_status_valid)
        snprintf(cap_status_diag, sizeof cap_status_diag,
                 "{\"valid\":true,\"raw\":%u,\"done\":%s,\"position_words\":%u}",
                 st.cap_status,
                 RF_CAP_DONE(st.cap_status) ? "true" : "false",
                 RF_CAP_POS(st.cap_status));
    else
        snprintf(cap_status_diag, sizeof cap_status_diag,
                 "{\"valid\":false,\"raw\":null}");
    n = snprintf(out, sizeof out,
                 "{\"sampled_at_monotonic_ns\":%llu,\"io_ok\":%s,"
                 "\"tx\":{\"source_selected\":%s,\"length_words\":%s,"
                 "\"position_words\":%s},"
                 "\"rx\":{\"armed\":%s,\"done\":%s,"
                 "\"position_words\":%s},"
                 "\"diagnostics\":{\"status\":%s,\"phy_mode\":%s,"
                 "\"idelay_en\":%s,\"idelay_tap\":%s,\"src_cfg\":%s,"
                 "\"cap_cfg\":%s,\"cap_ratio\":%s,\"cap_len\":%s,"
                 "\"cap_status\":%s,"
                 "\"unavailable\":[\"rx_frame_valid\",\"idelay_cntvalueout\"]}}",
                 (unsigned long long)st.sampled_at_monotonic_ns,
                 st.io_ok ? "true" : "false",
                 tx_source, tx_length, tx_position, rx_armed, rx_done, rx_position,
                 status_diag, phy_mode_diag, idelay_en_diag, idelay_tap_diag,
                 src_cfg_diag, cap_cfg_diag, cap_ratio_diag, cap_len_diag,
                 cap_status_diag);
    if (n < 0 || (size_t)n >= sizeof out)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_rf_init(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_init_request_t request;
    rf_readback_t rb;
    rf_state_error_t err;
    rf_hw_status_t idle;
    char out[512];
    long v;
    int rc, n;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (get_uint(rq, "lo_hz", &v) != 0)
        return arg_error(c, rq, "lo_hz");
    if (v == 0)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "lo_hz must be a positive frequency");
    memset(&request, 0, sizeof request);
    request.lo_hz = (uint64_t)v;
    if (get_uint(rq, "sample_rate_hz", &v) != 0 || v == 0)
        return arg_error(c, rq, "sample_rate_hz");
    request.sample_rate_hz = (uint32_t)v;
    /* No default and no fallback: an omitted attenuation on init would mean full
     * TX power, so the client must name it. */
    if (get_uint(rq, "attenuation_mdB", &v) != 0)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "rf.init requires an explicit attenuation_mdB; omitting it "
                      "would mean full TX power");
    request.attenuation_mdB_present = true;
    request.attenuation_mdB = (uint32_t)v;
    {
        long phy = 0;
        int r = opt_flag(rq, "phy_mode_1r1t", &phy);

        if (r < 0)
            return arg_error(c, rq, "phy_mode_1r1t");
        if (r == 0)
            request.phy_mode_1r1t = (uint8_t)phy;
    }
    /* begin() owns the "already up" refusal: INIT is startable only from OFFLINE
     * or FAULT, so a second rf.init cannot silently re-run the sequence. */
    if (rf_state_begin(&d->session, RF_OP_INIT, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    d->initialization_seen = true;
    rc = rf_core_init(d->core, &request, &rb);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    idle.tx_active = false;
    idle.rx_busy = false;
    idle.rx_done = false;
    if (rf_state_finish(&d->session, idle, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    n = readback_result(&rb, out, sizeof out);
    if (n < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_idelay_configure(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    char out[256];
    long mask = 0, tap = 0;
    uint32_t en_rb = 0, tap_rb = 0;
    int rc, n;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (get_uint(rq, "mask", &mask) != 0)
        return arg_error(c, rq, "mask");
    if (get_uint(rq, "tap", &tap) != 0)
        return arg_error(c, rq, "tap");
    if (mask <= 0 || mask > 0x7F || tap > 31)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "mask must be 1..127 and tap 0..31");
    rc = rf_core_set_idelay_lanes(d->core, (uint8_t)mask, (uint8_t)tap,
                                  &en_rb, &tap_rb);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    n = snprintf(out, sizeof out,
                 "{\"idelay_en\":%u,\"idelay_tap\":%u,"
                 "\"readback\":{\"idelay_en\":%u,\"idelay_tap\":%u},"
                 "\"readback_kind\":\"pl_config_register_not_cntvalueout\"}",
                 (unsigned)mask, (unsigned)tap, en_rb, tap_rb);
    if (n < 0 || (size_t)n >= sizeof out)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int interface_result_json(const rf_interface_status_t *s,
                                    char *out, size_t cap)
{
    size_t used = 0;

    if (!s || !s->valid)
        return -1;
    if (append(out, cap, &used,
               "{\"valid\":true,"
               "\"registers\":{\"0x006\":%u,\"0x007\":%u,"
               "\"0x010\":%u,\"0x011\":%u,\"0x012\":%u},"
               "\"fields\":{\"rx_data_delay\":%u,\"data_clk_delay\":%u,"
               "\"tx_data_delay\":%u,\"fb_clk_delay\":%u},"
               "\"decoded\":{\"lvds_mode\":%s,\"single_data_rate\":%s,"
               "\"half_duplex_mode\":%s,\"single_port_mode\":%s,"
               "\"full_port\":%s,\"swap_ports\":%s,"
               "\"fdd_alt_word_order\":%s,\"fdd_rx_rate_2tx_rate\":%s}}",
               (unsigned)s->reg_006, (unsigned)s->reg_007,
               (unsigned)s->reg_010, (unsigned)s->reg_011, (unsigned)s->reg_012,
               (unsigned)s->rx_data_delay, (unsigned)s->data_clk_delay,
               (unsigned)s->tx_data_delay, (unsigned)s->fb_clk_delay,
               s->lvds_mode ? "true" : "false",
               s->single_data_rate ? "true" : "false",
               s->half_duplex_mode ? "true" : "false",
               s->single_port_mode ? "true" : "false",
               s->full_port ? "true" : "false",
               s->swap_ports ? "true" : "false",
               s->fdd_alt_word_order ? "true" : "false",
               s->fdd_rx_rate_2tx_rate ? "true" : "false") != 0)
        return -1;
    return (int)used;
}

static int op_rf_interface_status(conn_t *c, const rf_proto_request_t *rq)
{
    rf_interface_status_t status;
    char out[512] = {0};
    int rc, n;

    if (!c->d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    rc = rf_core_read_interface_status(c->d->core, &status);
    if (rc != RF_ERR_OK)
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    n = interface_result_json(&status, out, sizeof out);
    if (n < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_rf_rx_data_delay_set(conn_t *c, const rf_proto_request_t *rq)
{
    rf_interface_status_t before, after;
    char out[1024] = {0};
    long value = 0;
    int rc, n;

    if (!c->d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (get_uint(rq, "value", &value) != 0)
        return arg_error(c, rq, "value");
    if (value > 15)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "RX data delay value must be 0..15; it is not clamped or wrapped");
    memset(&before, 0, sizeof before);
    memset(&after, 0, sizeof after);
    rc = rf_core_set_rx_data_delay(c->d->core, (uint8_t)value, &before, &after);
    if (rc != RF_ERR_OK)
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    n = snprintf(out, sizeof out,
                 "{\"register\":6,"
                 "\"field\":\"RX_DATA_DELAY[3:0]\","
                 "\"before\":{\"reg_006\":%u,\"rx_data_delay\":%u,"
                 "\"data_clk_delay\":%u},"
                 "\"after\":{\"reg_006\":%u,\"rx_data_delay\":%u,"
                 "\"data_clk_delay\":%u},"
                 "\"changed\":%s,"
                 "\"other_registers_unchanged\":%s,"
                 "\"readback\":",
                 (unsigned)before.reg_006, (unsigned)before.rx_data_delay,
                 (unsigned)before.data_clk_delay,
                 (unsigned)after.reg_006, (unsigned)after.rx_data_delay,
                 (unsigned)after.data_clk_delay,
                 before.rx_data_delay != after.rx_data_delay ? "true" : "false",
                 (after.reg_007 == before.reg_007 &&
                  after.reg_010 == before.reg_010 &&
                  after.reg_011 == before.reg_011 &&
                  after.reg_012 == before.reg_012) ? "true" : "false");
    if (n < 0 || (size_t)n >= sizeof out)
        return -1;
    {
        char sub[512] = {0};
        int m = interface_result_json(&after, sub, sizeof sub);
        if (m < 0 || (size_t)n + (size_t)m + 2u >= sizeof out)
            return -1;
        memcpy(out + n, sub, (size_t)m);
        n += m;
        out[n++] = '}';
        out[n] = '\0';
    }
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_rf_configure(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_config_t config;
    rf_readback_t rb;
    rf_state_error_t err;
    rf_hw_status_t idle;
    char out[512];
    long v;
    int rc, n;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    memset(&config, 0, sizeof config);
    if (get_uint(rq, "lo_hz", &v) != 0)
        return arg_error(c, rq, "lo_hz");
    config.lo_hz = (uint64_t)v;
    if (get_uint(rq, "sample_rate_hz", &v) != 0)
        return arg_error(c, rq, "sample_rate_hz");
    config.sample_rate_hz = (uint32_t)v;
    if (get_uint(rq, "rx_bandwidth_hz", &v) != 0)
        return arg_error(c, rq, "rx_bandwidth_hz");
    config.rx_bandwidth_hz = (uint32_t)v;
    if (get_uint(rq, "tx_bandwidth_hz", &v) != 0)
        return arg_error(c, rq, "tx_bandwidth_hz");
    config.tx_bandwidth_hz = (uint32_t)v;
    if (get_uint(rq, "attenuation_mdB", &v) != 0)
        return arg_error(c, rq, "attenuation_mdB");
    /* Reject before rf_state_begin and every hardware access. The vendor
     * setter checks too late: IDELAY and LO may already have changed. */
    if ((uint32_t)v > RF_SAFE_ATTENUATION_MDB)
        return arg_error(c, rq, "attenuation_mdB");
    config.attenuation_mdB = (uint32_t)v;
    {
        long o = 0;
        int r;

        r = opt_flag(rq, "phy_mode_1r1t", &o);
        if (r < 0)
            return arg_error(c, rq, "phy_mode_1r1t");
        if (r == 0)
            config.phy_mode_1r1t = (uint8_t)o;
        r = opt_flag(rq, "idelay_en", &o);
        if (r < 0)
            return arg_error(c, rq, "idelay_en");
        if (r == 0)
            config.idelay_en = (uint8_t)o;
        r = opt_uint(rq, "idelay_tap", &o);
        if (r < 0)
            return arg_error(c, rq, "idelay_tap");
        if (r == 0)
            config.idelay_tap = (uint8_t)o;
    }
    if (rf_state_begin(&d->session, RF_OP_CONFIGURE, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    rc = rf_core_configure(d->core, &config, &rb);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    idle.tx_active = false;
    idle.rx_busy = false;
    idle.rx_done = false;
    if (rf_state_finish(&d->session, idle, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    n = readback_result(&rb, out, sizeof out);
    if (n < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

/* A tx.load is a FRAME, not a line: its payload follows immediately and its
 * length is declared by the header.  A refusal that happens before that payload
 * has been consumed therefore cannot leave the stream in sync -- the binary
 * payload would be parsed as if it were request lines, swallowing whatever the
 * client sent next.  Such a refusal ends the connection, and the client
 * reconnects and resends.  Refusals that happen AFTER the payload is drained (an
 * out-of-range sample, a BRAM write that did not read back) are ordinary
 * recoverable errors and do keep the connection. */
static int refuse_frame(conn_t *c, const rf_proto_request_t *rq,
                        rf_proto_code_t code, const char *message)
{
    (void)refuse(c, rq, code, message);
    return -1;
}

static int op_tx_load(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_proto_code_t code = RF_PROTO_E_INVALID_ARGUMENT;
    unsigned samples = 0;
    bool wrap = false;
    int r;
    char result[128];

    if (!d->core)
        return refuse_frame(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (d->session.state != RF_STATE_READY)
        return refuse_frame(c, rq, RF_PROTO_E_INVALID_STATE,
                           "tx.load requires the part READY, with no TX or RX "
                           "running");
    if (rf_proto_iq_declare(rq->args, "tx", d->cfg.max_samples, &samples, &code) != 0)
        return refuse_frame(c, rq, code,
                           "the tx.load declaration is not usable: samples must be "
                           "1..max_samples, bytes must equal samples*4, format must "
                           "be s16_iq_le and direction must be tx");
    if (rf_proto_arg_bool(rq->args, "wrap", &wrap) == RF_PROTO_ARG_BAD)
        return refuse_frame(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                           "the wrap argument must be true or false");

    /* Read the whole payload, then convert and validate the whole payload,
     * before a single word reaches the BRAM. */
    r = read_exact(c, d->wire, (size_t)samples * 4u);
    if (r != 0) {
        rf_proto_code_t code2 = (r == -1) ? RF_PROTO_E_TIMEOUT : RF_PROTO_E_INVALID_ARGUMENT;
        const char *msg = (r == -1)
            ? "the payload did not arrive before the deadline"
            : "the connection ended inside the declared payload";

        /* The length was declared, so a short payload is a real desync: report
         * it and end.  Nothing has been written to the BRAM either way. */
        (void)refuse(c, rq, code2, msg);
        return -1;
    }
    if (rf_iq_wire_to_bram(d->wire, (size_t)samples * 4u, d->words, samples) != 0)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "an IQ sample is outside -2048..2047; nothing was written");
    if (rf_core_write_tx(d->core, d->words, samples) != RF_ERR_OK)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the TX BRAM write did not read back");
    d->tx_count = samples;
    d->tx_wrap = wrap;
    snprintf(result, sizeof result,
             "{\"samples\":%u,\"bytes\":%u,\"wrap\":%s}", samples, samples * 4u,
             wrap ? "true" : "false");
    return respond_ok(c, rq->id, result) == 0 ? 0 : -1;
}

static int op_tx_start(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    char out[384];
    int rc;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (rf_state_begin(&d->session, RF_OP_TX_START, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    if (d->tx_count == 0u) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "tx.start needs a tx.load first: there is nothing to source");
    }
    rc = rf_core_prepare_tx(d->core, d->tx_count, d->tx_wrap);
    if (rc == RF_ERR_OK)
        rc = rf_core_start_tx(d->core);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    if (read_hw_status(d, &observed) != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the start could not be confirmed by the readback");
    }
    /* The status is judged by the state machine: a start whose readback says the
     * PL is not sourcing did not start, whatever the write returned. */
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    if (state_result(d, out, sizeof out) < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

/* tx.stop and loopback.stop are the same operation: leave the digital loopback,
 * disarm RX, stop TX, and let the observed status say whether it worked. */
static int op_stop(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    char out[384];

    /* Stop is permitted from every STATE, but not from a daemon with no part
     * attached: reporting "nothing is live, you are READY" would be a success the
     * daemon did not verify.  It is also how the session gets poisoned -- READY
     * with no core refuses both rf.init and every hardware op. */
    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    /* An OPEN but never-initialized part is the same trap for the same reason:
     * nothing has run, so there is nothing to stop, and answering READY would
     * take the session out of the one state rf.init accepts (INIT is startable
     * only from OFFLINE or FAULT) while the core itself still refuses every
     * op.  The daemon meets this state at startup, before the first rf.init. */
    if (rf_core_state(d->core) == RF_STATE_OFFLINE)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE,
                      "there is nothing to stop: the part is not initialized");
    if (rf_state_begin(&d->session, RF_OP_STOP, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    do_stop(d, &observed);
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    if (state_result(d, out, sizeof out) < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

static int op_rx_capture(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    rf_readback_t rb;
    unsigned samples = 0, sample_rate = 0, ratio = 1u;
    bool continuous = false;
    long v;
    int rc, n;
    size_t i;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (get_uint(rq, "samples", &v) != 0 || v < 1 ||
        (unsigned long)v > (unsigned long)d->cfg.max_samples)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "samples must be 1..max_samples");
    samples = (unsigned)v;
    {
        long r = 0;
        int s = opt_uint(rq, "ratio", &r);

        /* 0..4 is the protocol's own range for the capture decimation select;
         * naming it here keeps a stray value from being silently truncated into
         * a legal one. */
        if (s < 0 || (s == 0 && r > 4))
            return arg_error(c, rq, "ratio");
        if (s == 0)
            ratio = (unsigned)r;
    }
    {
        bool b = false;
        int s = opt_bool(rq, "continuous", &b);

        if (s < 0)
            return arg_error(c, rq, "continuous");
        if (s == 0)
            continuous = b;
    }

    if (rf_state_begin(&d->session, RF_OP_RX_CAPTURE, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    rc = rf_core_prepare_rx(d->core, (uint8_t)ratio, samples, continuous);
    if (rc == RF_ERR_OK)
        rc = rf_core_start_rx(d->core);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    if (!continuous) {
        /* The FINITE capture is finished at the CORE, not here: freeze the
         * samples, lower RF_CAP_ARM and confirm the real register.  The core's
         * own read_rx deliberately leaves the arm high, and a teardown that
         * stopped there would let this request answer "done" while the wire still
         * said "armed" -- a host could not tell the two apart, and the next
         * capture would inherit the arm.  A release that cannot be confirmed is a
         * refusal, and nothing is sent: a frame the part does not agree is
         * finished is not a frame. */
        rc = rf_core_finish_rx(d->core, d->words, samples);
        if (rc != RF_ERR_OK) {
            op_failed(d, &err);
            return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
        }
        observed.tx_active = false;
        observed.rx_busy = false;
        observed.rx_done = true;
    } else {
        for (i = 0; i < samples; ++i) {
            rc = rf_core_read_rx_word(d->core, i, &d->words[i]);
            if (rc != RF_ERR_OK) {
                op_failed(d, &err);
                return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
            }
        }
        observed.tx_active = false;
        observed.rx_busy = true;
        observed.rx_done = false;
    }
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);

    if (rf_iq_bram_to_wire(d->words, samples, d->wire, (size_t)samples * 4u) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the capture could not be converted to the wire format");
    /* The sample rate reported is the one the part is actually configured for,
     * which is why it is read back rather than echoed from the request. */
    if (rf_core_get_readback(d->core, &rb) == RF_ERR_OK)
        sample_rate = rb.sample_rate_hz;
    n = rf_proto_write_iq_frame((char *)d->frame, RF_RESP_CAP, rq->id, "rx",
                                samples, sample_rate, ratio, d->cfg.now_ns());
    if (n < 0 || (size_t)n + 1u + (size_t)samples * 4u > d->frame_cap)
        return -1;
    d->frame[n] = '\n';
    memcpy(d->frame + (size_t)n + 1u, d->wire, (size_t)samples * 4u);
    /* Header and payload in ONE write: with a single-threaded loop nothing can
     * interleave, and sending them together makes that structural. */
    return send_all(c, d->frame, (size_t)n + 1u + (size_t)samples * 4u);
}

static int op_loopback_digital_start(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    uint8_t spi_readback = 0;
    char out[384];
    int rc;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (rf_state_begin(&d->session, RF_OP_DIGITAL_LOOPBACK, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    if (d->tx_count == 0u) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "the digital loopback needs a tx.load first: there is "
                      "nothing to loop back");
    }
    /* The loop-test bit is what makes the part send its TX data port back to its
     * RX data port; the TX path then has to be sourcing for anything to travel.
     * Each step is read back by the core, so a bit that did not take is a fault
     * before this returns. */
    rc = rf_core_set_digital_loopback(d->core, true, &spi_readback);
    if (rc == RF_ERR_OK)
        rc = rf_core_configure_tx_path(d->core, d->tx_count, d->tx_wrap, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_set_tx_source(d->core, true, NULL);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    if (read_hw_status(d, &observed) != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the digital loopback could not be confirmed");
    }
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    if (state_result(d, out, sizeof out) < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}

/* One reviewable transaction for the on-chip TX->RX loop.
 *
 * `loopback.digital.start` followed by `rx.capture` cannot work: the state machine
 * admits RX capture only from READY, and a stop closes the loop before it stops.  This
 * op is the sequence the board CLI proved (the one loopback_criteria.capture() encodes)
 * expressed as a single command whose exit path ALWAYS takes the TX source off the
 * buffer and clears the AD9361 data-port loop bit.
 *
 * The loop bit does not move core->state: the part stays READY while the tap is open,
 * so the ordinary READY-gated one-shot capture below is legal, and so is the cleanup --
 * including after a capture that faulted.  The returned frame is the same iq_frame
 * `rx.capture` sends, so the wire format is not a second implementation. */
static int op_loopback_digital_capture(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    rf_readback_t rb;
    unsigned samples = 0, sample_rate = 0, ratio = 1u;
    uint8_t spi_readback = 0;
    long v;
    int rc, n;
    bool loop_open = false;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (get_uint(rq, "samples", &v) != 0 || v < 1 ||
        (unsigned long)v > (unsigned long)d->cfg.max_samples)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "samples must be 1..max_samples");
    samples = (unsigned)v;
    {
        long r = 0;
        int s = opt_uint(rq, "ratio", &r);

        if (s < 0 || (s == 0 && r > 4))
            return arg_error(c, rq, "ratio");
        if (s == 0)
            ratio = (unsigned)r;
    }
    if (d->tx_count == 0u)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "the digital loopback needs a tx.load first: there is "
                      "nothing to loop back");
    if (rf_state_begin(&d->session, RF_OP_DIGITAL_CAPTURE, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);

    rc = rf_core_set_data_port_loop(d->core, true, &spi_readback);
    if (rc == RF_ERR_OK)
        loop_open = true;
    if (rc == RF_ERR_OK)
        rc = rf_core_configure_tx_path(d->core, d->tx_count, d->tx_wrap, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_set_tx_source(d->core, true, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_prepare_rx(d->core, (uint8_t)ratio, samples, false);
    if (rc == RF_ERR_OK)
        rc = rf_core_start_rx(d->core);
    if (rc == RF_ERR_OK)
        rc = rf_core_finish_rx(d->core, d->words, samples);

    /* The tap and the TX source exist only for this capture, so both go away --
     * and a loop left open after a FAILED capture is exactly the case that would
     * be hardest to notice.  They are ATTEMPTED only once the capture itself was
     * confirmed: the core's setters refuse to write to a part it has faulted, so
     * an attempt there would be a no-op dressed up as a cleanup.  A fault is
     * recovered by the existing safe shutdown on the disarm/disconnect path --
     * the one place that clears SRC_SEL and CAP_ARM without a state gate. */
    if (loop_open && rc == RF_ERR_OK) {
        (void)rf_core_set_tx_source(d->core, false, NULL);
        (void)rf_core_set_data_port_loop(d->core, false, NULL);
    }
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    observed.tx_active = false;
    observed.rx_busy = false;
    observed.rx_done = true;
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);

    if (rf_iq_bram_to_wire(d->words, samples, d->wire, (size_t)samples * 4u) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the capture could not be converted to the wire format");
    if (rf_core_get_readback(d->core, &rb) == RF_ERR_OK)
        sample_rate = rb.sample_rate_hz;
    n = rf_proto_write_iq_frame((char *)d->frame, RF_RESP_CAP, rq->id, "rx",
                                samples, sample_rate, ratio, d->cfg.now_ns());
    if (n < 0 || (size_t)n + 1u + (size_t)samples * 4u > d->frame_cap)
        return -1;
    d->frame[n] = '\n';
    memcpy(d->frame + (size_t)n + 1u, d->wire, (size_t)samples * 4u);
    return send_all(c, d->frame, (size_t)n + 1u + (size_t)samples * 4u);
}

/* The in-window observation object for a physical capture's OWN frame header.
 *
 * Two independent readings, each with its own availability, so a value this path
 * could not read is reported as null with a NAMED reason and is never replaced by a
 * request-side number, a default or a reading taken somewhere else.  The reason is
 * the protocol's error-code name (HARDWARE_IO / INVALID_STATE / TIMEOUT), the same
 * vocabulary a refusal uses.
 *
 * The scope string is part of the object on purpose: "one read inside the capture
 * window" and "the chip's own attenuation read back afterwards" are claims about
 * WHERE each number came from, and a reader that ignores them would be reading two
 * unlike measurements as if they were alike. */
static int capture_observation_json(char *out, size_t cap, int gain_rc,
                                    uint8_t gain_index, int chip_rc,
                                    uint32_t chip_tx1_mdb, uint32_t chip_tx2_mdb)
{
    size_t used = 0;

    if (append(out, cap, &used,
               "\"capture_observation\":{\"scope\":\"single_read_in_capture_window_"
               "plus_chip_attenuation_readback\"") != 0)
        return -1;
    if (gain_rc == RF_ERR_OK) {
        if (append(out, cap, &used, ",\"in_window_rx_gain_index\":%u",
                   (unsigned)gain_index) != 0)
            return -1;
    } else if (append(out, cap, &used,
                      ",\"in_window_rx_gain_index\":null,"
                      "\"in_window_rx_gain_unavailable\":\"%s\"",
                      rf_proto_code_name(map_rf_error(gain_rc))) != 0) {
        return -1;
    }
    if (chip_rc == RF_ERR_OK) {
        if (append(out, cap, &used, ",\"chip_tx_attenuation_mdb\":[%u,%u]",
                   (unsigned)chip_tx1_mdb, (unsigned)chip_tx2_mdb) != 0)
            return -1;
    } else if (append(out, cap, &used,
                      ",\"chip_tx_attenuation_mdb\":null,"
                      "\"chip_tx_attenuation_unavailable\":\"%s\"",
                      rf_proto_code_name(map_rf_error(chip_rc))) != 0) {
        return -1;
    }
    return append(out, cap, &used, "}") == 0 ? (int)used : -1;
}

/* One reviewable transaction for the physical TX->RX loop: the operator has TX1
 * cabled directly (coax) to RX1.  Source the loaded TX buffer, run a one-shot RX
 * capture, drain it, then take the TX source off -- exactly the digital-capture
 * transaction minus the AD9361 data-port loop bit. */
static int op_loopback_physical_capture(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_state_error_t err;
    rf_hw_status_t observed;
    rf_readback_t rb;
    unsigned samples = 0, sample_rate = 0, ratio = 1u;
    long v, atten;
    rf_loopback_request_t request = {0};
    int rc, n;
    bool observe_timing = false;
    /* What this transaction actually turned on.  The cleanup below keys off these
     * rather than off `rc`, so a failure AFTER a resource was brought up still
     * releases it. */
    bool tx_source_on = false, rx_started = false;
    long settle_us = 0;
    capture_timing_t timing = {0};
    /* The two diagnostics this transaction reports beside its samples.  Neither can
     * change `rc`: a reading that fails must not be able to end the capture, and a
     * capture that failed for its own (pre-existing) reason is still refused by the
     * pre-existing criteria rather than by this one. */
    uint8_t in_window_gain_index = 0;
    int in_window_gain_rc = RF_ERR_INVALID_STATE;
    rf_headroom_readback_t chip_readback;
    int chip_rc = RF_ERR_INVALID_STATE;
    char observation[256];

    if (opt_bool(rq, "observe_timing", &observe_timing) < 0)
        return arg_error(c, rq, "observe_timing");
    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    if (rf_proto_arg_int(rq->args, "attenuation_mdB", &atten) != RF_PROTO_ARG_OK)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "a physical capture requires an explicit attenuation_mdB");
    if (rf_proto_arg_bool(rq->args, "cable_confirmed", &request.cable_confirmed) == RF_PROTO_ARG_BAD)
        return arg_error(c, rq, "cable_confirmed");
    request.attenuation_mdB = atten;
    if (rf_state_check_physical_loopback(&d->session, &request, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    if (get_uint(rq, "samples", &v) != 0 || v < 1 ||
        (unsigned long)v > (unsigned long)d->cfg.max_samples)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "samples must be 1..max_samples");
    samples = (unsigned)v;
    {
        long r = 0;
        int st = opt_uint(rq, "ratio", &r);
        if (st < 0 || (st == 0 && r > 4))
            return arg_error(c, rq, "ratio");
        if (st == 0)
            ratio = (unsigned)r;
    }
    /* Validated here, before rf_state_begin and before any hardware write, so a
     * refused wait leaves the part untouched. */
    {
        long settle = 0;
        int st = opt_uint(rq, "settle_wait_us", &settle);
        if (st < 0 || (st == 0 && settle > (long)RF_SETTLE_WAIT_MAX_US))
            return arg_error(c, rq, "settle_wait_us");
        if (st == 1)
            settle = 0;
        /* A wait nobody can measure is not evidence.  Requiring the observable
         * path makes "every wait is provable" structural rather than a promise. */
        if (settle > 0 && !observe_timing)
            return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                          "a settling wait must be observable: settle_wait_us "
                          "requires observe_timing");
        if (settle > 0 && !d->cfg.sleep_us)
            return refuse(c, rq, RF_PROTO_E_INVALID_STATE,
                          "this daemon has no wait facility");
        settle_us = settle;
    }
    if (d->tx_count == 0u)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "the physical loopback needs a tx.load first: there is "
                      "nothing to loop back");
    if (rf_state_begin(&d->session, RF_OP_PHYSICAL_CAPTURE, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);

    rc = rf_core_set_tx_attenuation_safe(d->core, request.attenuation_mdB, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_configure_tx_path(d->core, d->tx_count, d->tx_wrap, NULL);
    if (rc == RF_ERR_OK) {
        if (observe_timing)
            timing.brackets[0] = d->cfg.now_ns();
        rc = rf_core_set_tx_source(d->core, true, NULL);
        if (rc == RF_ERR_OK)
            tx_source_on = true;
        if (observe_timing && rc == RF_ERR_OK)
            timing.brackets[1] = d->cfg.now_ns();
    }
    /* The settling wait sits between the TX source coming on and the RX being
     * prepared, which is where a gain that has not settled would otherwise be
     * captured.  TX is sourcing for the whole wait, so the AGC has signal to
     * settle on -- a wait taken before TX came on would let it settle towards
     * noise instead.  A requested wait always implies observe_timing (enforced
     * at argument parsing), so these two reads are exactly the ones that make it
     * provable. */
    if (rc == RF_ERR_OK && settle_us > 0) {
        timing.settle_wait_requested_us = (unsigned)settle_us;
        timing.settle_begin_ns = d->cfg.now_ns();
        /* A wait that did not complete FAILS the transaction.  The trace publishes
         * the requested duration beside the measured one, so a frame whose wait
         * never ran would be a false measurement rather than a shorter one. */
        if (d->cfg.sleep_us((uint64_t)settle_us) != 0)
            rc = RF_ERR_TIMEOUT;
        timing.settle_end_ns = d->cfg.now_ns();
    }
    if (rc == RF_ERR_OK)
        rc = rf_core_prepare_rx(d->core, (uint8_t)ratio, samples, false);
    if (rc == RF_ERR_OK) {
        if (observe_timing)
            timing.brackets[2] = d->cfg.now_ns();
        rc = rf_core_start_rx(d->core);
        if (rc == RF_ERR_OK)
            rx_started = true;
        if (observe_timing && rc == RF_ERR_OK)
            timing.brackets[3] = d->cfg.now_ns();
    }
    if (rc == RF_ERR_OK) {
        /* THE one in-window observation: one checked read of the RX1 gain index,
         * taken after the capture is armed and before it is finished.  This is the
         * only place in the daemon that samples the RX gain with the window open --
         * rf_core_get_gain_observation (device.info) refuses that state on purpose,
         * so an in-window trajectory cannot be read from it at all.  One read, not a
         * poll: a loop here would spend the capture window on the observation.  The
         * result cannot change `rc` -- see in_window_gain_rc.
         *
         * The read sits BETWEEN brackets[3] (rx_arm_end) and brackets[4]
         * (drain_begin), so the drain bracket keeps the meaning it had before the
         * observation existed -- the interval rf_core_finish_rx spends draining --
         * and this read's wall time is not folded into it.  The host's injected
         * clock cannot see the difference; the board's monotonic clock can, which
         * is why the placement itself has to say this out loud. */
        in_window_gain_rc =
            rf_core_read_rx1_gain_index(d->core, &in_window_gain_index);
        if (observe_timing)
            timing.brackets[4] = d->cfg.now_ns();
        rc = rf_core_finish_rx(d->core, d->words, samples);
        if (observe_timing && rc == RF_ERR_OK)
            timing.brackets[5] = d->cfg.now_ns();
    }

    /* The TX source exists only for this capture, so it is taken off and the
     * release CONFIRMED -- rf_core_set_tx_source reads SRC_SEL back and faults on
     * a mismatch, so an unconfirmed clear fails this transaction instead of
     * leaving the part sourcing.  It is attempted only once the capture was
     * confirmed for the same reason as the digital cleanup above: nothing is
     * written to a part the core has already faulted; that case is recovered by
     * the existing safe shutdown on the disarm/disconnect path. */
    /* ...but the source exists only for THIS capture, so it comes off whenever it
     * was actually brought up -- including when the transaction has ALREADY
     * failed.  Keying the clear off `rc == RF_ERR_OK` left the part sourcing for
     * the rest of the connection after a failed settling wait; measured
     * mid-connection with the connection deliberately held open: SRC_SEL=1 while
     * CAP_ARM was already 0.  Skipping the clear is defensible only for a core the
     * core itself has faulted, where every setter refuses -- a failed wait is not
     * that case and the core is still READY.
     *
     * The FIRST failure is what the caller hears: a capture that failed for its
     * own reason must not be re-reported as the cleanup's error. */
    if (tx_source_on && rf_core_state(d->core) != RF_STATE_FAULT) {
        int off_rc;
        if (observe_timing)
            timing.brackets[6] = d->cfg.now_ns();
        off_rc = rf_core_set_tx_source(d->core, false, NULL);
        if (observe_timing && off_rc == RF_ERR_OK)
            timing.brackets[7] = d->cfg.now_ns();
        if (rc == RF_ERR_OK)
            rc = off_rc;
    }
    /* An RX that was started and left armed by a FAILED transaction is disarmed on
     * the same terms.  Deliberately keyed on `rc != RF_ERR_OK`: on the SUCCESS path
     * rf_core_finish_rx has already released the arm and verified it, so writing it
     * again would add a hardware operation to the normal path for nothing -- which
     * is exactly what the earlier shape of this block did.
     *
     * Note this is defensive rather than reached today: every failure that lands
     * here after start_rx has already faulted the core (read_rx faults on an
     * incomplete capture, and its non-faulting errors are not reachable from this
     * transaction), so the state guard skips it.  It is kept because the guard is
     * the only thing making it unreachable, and that is worth stating. */
    if (rc != RF_ERR_OK && rx_started &&
        rf_core_state(d->core) != RF_STATE_FAULT &&
        rf_core_set_rx_arm(d->core, false, NULL) != RF_ERR_OK)
        rc = RF_ERR_HARDWARE_IO;
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    observed.tx_active = false;
    observed.rx_busy = false;
    observed.rx_done = true;
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);

    if (rf_iq_bram_to_wire(d->words, samples, d->wire, (size_t)samples * 4u) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the capture could not be converted to the wire format");
    if (rf_core_get_readback(d->core, &rb) == RF_ERR_OK)
        sample_rate = rb.sample_rate_hz;
    timing.capture_timestamp_ns = d->cfg.now_ns();
    /* The chip's OWN TX1/TX2 attenuation, on the existing dual-channel readback path
     * (the one HEADROOM already uses), and deliberately AFTER the window has closed:
     * that composite readback also walks the FIR pair, and a few hundred SPI reads
     * inside a 4096-sample window would perturb the interval being observed.  It
     * writes nothing, and a failure is NAMED in the frame rather than replaced by the
     * value that was requested. */
    chip_rc = rf_core_get_headroom_readback(d->core, &chip_readback);
    if (capture_observation_json(observation, sizeof observation,
                                 in_window_gain_rc, in_window_gain_index, chip_rc,
                                 chip_readback.tx1_attenuation_mdB,
                                 chip_readback.tx2_attenuation_mdB) < 0)
        return -1;
    n = rf_proto_write_iq_frame_observed((char *)d->frame, RF_RESP_CAP, rq->id, "rx",
                                         samples, sample_rate, ratio,
                                         timing.capture_timestamp_ns, observation);
    if (n < 0 || (size_t)n + 1u + (size_t)samples * 4u > d->frame_cap)
        return -1;
    d->frame[n] = '\n';
    memcpy(d->frame + (size_t)n + 1u, d->wire, (size_t)samples * 4u);
    rc = send_all(c, d->frame, (size_t)n + 1u + (size_t)samples * 4u);
    if (rc == 0 && observe_timing) {
        timing.capture_request_id = rq->id;
        timing.samples = samples;
        timing.ratio = ratio;
        timing.available = capture_timing_valid(&timing);
        if (timing.available)
            d->last_capture_timing = timing;
        else
            d->capture_timing_clock_unavailable = true;
    }
    return rc;
}

static int op_loopback_physical_start(conn_t *c, const rf_proto_request_t *rq)
{
    rf_daemon_t *d = c->d;
    rf_loopback_request_t request;
    rf_state_error_t err;
    rf_hw_status_t observed;
    unsigned samples = 0, ratio = 1u;
    bool wrap = false, continuous = false;
    uint32_t atten_readback = 0;
    long v, atten;
    char out[384];
    int rc;

    if (!d->core)
        return refuse(c, rq, RF_PROTO_E_INVALID_STATE, RF_NO_CORE);
    /* The safety-critical two are parsed and judged BEFORE anything else: a
     * request that does not name both the cable confirmation and the exact
     * attenuation is refused without ever reaching the hardware. */
    if (rf_proto_arg_int(rq->args, "attenuation_mdB", &atten) != RF_PROTO_ARG_OK)
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "a physical loopback must name its attenuation_mdB");
    {
        bool confirmed = false;

        if (rf_proto_arg_bool(rq->args, "cable_confirmed", &confirmed) ==
            RF_PROTO_ARG_BAD)
            return arg_error(c, rq, "cable_confirmed");
        request.cable_confirmed = confirmed;
    }
    request.attenuation_mdB = atten;
    if (rf_state_check_physical_loopback(&d->session, &request, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    if (rf_state_begin(&d->session, RF_OP_PHYSICAL_LOOPBACK, &err) != 0)
        return refuse(c, rq, (rf_proto_code_t)err.code, err.message);
    /* There must be something in the TX BRAM to loop back, and this is checked
     * after the transaction is open (so it cannot interleave) and abandoned with
     * op_failed before the refusal (so the session does not stay BUSY).  The two
     * safety-critical arguments above are judged first on purpose: a request that
     * does not name its attenuation or its cable is refused for that reason, not
     * for what happens to be in the BRAM. */
    if (d->tx_count == 0u) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "the physical loopback needs a tx.load first: there is "
                      "nothing to loop back");
    }

    if (get_uint(rq, "samples", &v) != 0 || v < 1 ||
        (unsigned long)v > (unsigned long)d->cfg.max_samples) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                      "samples must be 1..max_samples");
    }
    samples = (unsigned)v;
    /* The remaining arguments are parsed after the approval, so an unusable one
     * has to abandon the transaction that begin() already opened. */
    {
        long r = 0;
        bool b = false;
        const char *bad = NULL;
        int s;

        s = opt_uint(rq, "ratio", &r);
        if (s < 0 || (s == 0 && r > 4))
            bad = "ratio";
        else if (s == 0)
            ratio = (unsigned)r;
        if (!bad) {
            s = opt_bool(rq, "wrap", &b);
            if (s < 0)
                bad = "wrap";
            else if (s == 0)
                wrap = b;
        }
        if (!bad) {
            s = opt_bool(rq, "continuous", &b);
            if (s < 0)
                bad = "continuous";
            else if (s == 0)
                continuous = b;
        }
        if (bad) {
            op_failed(d, &err);
            return arg_error(c, rq, bad);
        }
    }

    /* Compose the loop: attenuate first (nothing is transmitting yet, and the
     * cable is what makes the attenuation matter), then source the TX and arm
     * the RX.  Every value is read back by the call that set it. */
    rc = rf_core_set_tx_attenuation_safe(d->core, request.attenuation_mdB,
                                         &atten_readback);
    if (rc == RF_ERR_OK && atten_readback != (uint32_t)request.attenuation_mdB)
        rc = RF_ERR_HARDWARE_IO;
    if (rc == RF_ERR_OK)
        rc = rf_core_configure_tx_path(d->core, samples, wrap, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_set_tx_source(d->core, true, NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_set_rx_config(d->core, (uint8_t)ratio, samples, continuous,
                                  NULL);
    if (rc == RF_ERR_OK)
        rc = rf_core_set_rx_arm(d->core, true, NULL);
    if (rc != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, map_rf_error(rc), rf_error_name(rc));
    }
    if (read_hw_status(d, &observed) != RF_ERR_OK) {
        op_failed(d, &err);
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO,
                      "the physical loopback could not be confirmed");
    }
    if (rf_state_finish(&d->session, observed, &err) != 0)
        return refuse(c, rq, RF_PROTO_E_HARDWARE_IO, err.message);
    if (state_result(d, out, sizeof out) < 0)
        return -1;
    return respond_ok(c, rq->id, out) == 0 ? 0 : -1;
}


/* HEADROOM-1 has a separate process-lived budget, never a teaching approval. */
static int headroom_close(rf_daemon_t *d)
{
    headroom_close_t *close=&d->headroom_close;
    int rc=RF_ERR_OK;
    memset(close,0,sizeof *close);
    close->attempted=true;
    d->headroom.used=true; /* An invalid preflight is terminal, never retried. */
    rf_headroom_seal(&d->headroom);
    if(d->core) {
        if(rf_core_initialization_attempted(d->core))d->initialization_seen=true;
        close->release_code=rf_core_release_part_keep_state(d->core,&close->status);
        close->shutdown_code=rf_core_safe_shutdown(d->core,&close->status);
        if(close->release_code!=RF_ERR_OK)rc=close->release_code;
        if(close->shutdown_code!=RF_ERR_OK)rc=close->shutdown_code;
    }
    d->headroom_closing=true;
    (void)rf_daemon_disarm(d,"headroom terminal");
    d->headroom_closing=false;
    if(close->shutdown_code!=RF_ERR_OK)rc=close->shutdown_code;
    close->confirmed=rc==RF_ERR_OK && !close->status.tx_active && !close->status.rx_busy;
    return rc;
}
static int headroom_refuse(conn_t *c,const rf_proto_request_t *rq,const char *why)
{
    (void)headroom_close(c->d);
    return refuse(c,rq,RF_PROTO_E_INVALID_STATE,why);
}
static bool hr_int(const char *a,const char *k,long want)
{long v;return rf_proto_arg_int(a,k,&v)==RF_PROTO_ARG_OK&&v==want;}
static bool hr_str(const char *a,const char *k,const char *want)
{char v[128];return rf_proto_arg_str(a,k,v,sizeof v)==RF_PROTO_ARG_OK&&!strcmp(v,want);}
static bool hr_rf_fixed(const rf_headroom_readback_t *r)
{
    const rf_readback_t *b=&r->rf;
    bool attested=b->hardware_verified;
#ifdef PLATFORM_HOST
    attested=!b->hardware_verified; /* Host simulations can never attest hardware. */
#endif
    return attested && b->rx_lo_hz==80000000 && b->tx_lo_hz==80000000 &&
        b->rx_sample_rate_hz==60000000 && b->tx_sample_rate_hz==60000000 &&
        b->rx_bandwidth_hz==30000000 && b->tx_bandwidth_hz==30000000 &&
        b->attenuation_mdB==46000 && r->tx1_attenuation_mdB==46000 && r->tx2_attenuation_mdB==46000 &&
        b->phy_mode_1r1t==0 && b->tx_fir_enabled==1 && b->rx_fir_enabled<=1 &&
        b->tx_fir_config_verified && b->rx_fir_config_verified &&
        r->pl.phy_mode==0 && r->pl.idelay_en==127 && r->pl.idelay_tap==29 &&
        !(r->pl.src_cfg&RF_SRC_SEL) && !(r->pl.cap_cfg&RF_CAP_ARM);
}
static int hr_rb_json(char *out,size_t cap,const rf_headroom_readback_t *r)
{
    return snprintf(out,cap,"{\"rx_lo_hz\":%llu,\"tx_lo_hz\":%llu,\"rx_sample_rate_hz\":%u,\"tx_sample_rate_hz\":%u,\"rx_bandwidth_hz\":%u,\"tx_bandwidth_hz\":%u,\"attenuation_mdB\":%u,\"tx1_attenuation_mdB\":%u,\"tx2_attenuation_mdB\":%u,\"hardware_verified\":%s,\"phy_mode_1r1t\":%u,\"tx_fir_enabled\":%u,\"rx_fir_enabled\":%u,\"tx_fir_config_verified\":%s,\"rx_fir_config_verified\":%s,\"pl\":{\"phy_mode\":%u,\"idelay_en\":%u,\"idelay_tap\":%u,\"src_cfg\":%u,\"cap_cfg\":%u,\"cap_ratio\":%u,\"cap_len\":%u}}",
      (unsigned long long)r->rf.rx_lo_hz,(unsigned long long)r->rf.tx_lo_hz,
      r->rf.rx_sample_rate_hz,r->rf.tx_sample_rate_hz,r->rf.rx_bandwidth_hz,r->rf.tx_bandwidth_hz,
      r->rf.attenuation_mdB,r->tx1_attenuation_mdB,r->tx2_attenuation_mdB,r->rf.hardware_verified?"true":"false",
      r->rf.phy_mode_1r1t,r->rf.tx_fir_enabled,r->rf.rx_fir_enabled,r->rf.tx_fir_config_verified?"true":"false",r->rf.rx_fir_config_verified?"true":"false",
      r->pl.phy_mode,r->pl.idelay_en,r->pl.idelay_tap,r->pl.src_cfg,r->pl.cap_cfg,r->pl.cap_ratio,r->pl.cap_len);
}
static int op_headroom_begin(conn_t *c,const rf_proto_request_t *rq)
{
    rf_daemon_t *d=c->d; rf_headroom_readback_t actual; rf_readback_t rb;
    rf_init_request_t init={true,46000,80000000,60000000,0};
    rf_config_t config={80000000,60000000,30000000,30000000,46000,0,1,29};
    rf_pl_snapshot_t idle; char result[1024],readback[800]; int rc;
    if(!d->armed||!d->lease||!d->core||rf_core_state(d->core)!=RF_STATE_OFFLINE||d->cfg.max_samples<4096||!d->cfg.now_ns)
        return headroom_refuse(c,rq,"HEADROOM needs armed safe OFFLINE core and4096 capacity");
    if(d->initialization_seen || rf_core_initialization_attempted(d->core))
        return headroom_refuse(c,rq,"HEADROOM refuses a prior initialization attempt");
    /* Inspect the original state before any cleanup writes: unsafe input is a
     * refusal followed by cleanup, never repair-and-continue. */
    if(rf_core_get_pl_snapshot(d->core,&idle)!=RF_ERR_OK ||
       (idle.src_cfg&RF_SRC_SEL) || (idle.cap_cfg&RF_CAP_ARM))
        return headroom_refuse(c,rq,"HEADROOM idle readback failed");
    if(rf_headroom_begin(&d->headroom,rq->args)!=0)
        return headroom_refuse(c,rq,"HEADROOM begin identity/fixedRF/once-only refused");
    /* used is already consumed before the first initialization call. */
    d->initialization_seen=true;
    rc=rf_core_init(d->core,&init,&rb);
    if(rc==RF_ERR_OK)rc=rf_core_configure(d->core,&config,&rb);
    if(rc==RF_ERR_OK)rc=rf_core_get_headroom_readback(d->core,&actual);
    if(rc!=RF_ERR_OK||!hr_rf_fixed(&actual))return headroom_refuse(c,rq,"HEADROOM initialization/readback failed; budget consumed");
    adopt_core_state(d);hr_rb_json(readback,sizeof readback,&actual);
    snprintf(result,sizeof result,"{\"study\":\"HEADROOM-1\",\"session_id\":\"%s\",\"initializations\":1,\"attempt_budget\":5,\"actual_rf\":%s}",d->headroom.session_id,readback);
    return respond_ok(c,rq->id,result);
}
static bool hr_next(rf_daemon_t *d,const rf_proto_request_t *rq)
{
    unsigned a=d->headroom.attempt+1;
    return d->armed&&d->core&&d->headroom.active&&!d->headroom.waiting&&a<=5&&
      rf_headroom_identity(rq->args,d->headroom.session_id)&&hr_int(rq->args,"attempt",a)&&
      hr_str(rq->args,"scenario",rf_headroom_scenario(a));
}
static int op_headroom_load(conn_t *c,const rf_proto_request_t *rq)
{
    rf_daemon_t *d=c->d;unsigned a=d->headroom.attempt+1;char digest[65];int rc;
    if(!hr_next(d,rq)||d->headroom.loaded||rf_headroom_load_declaration(rq->args,a)!=0) {
        (void)headroom_close(d);return refuse_frame(c,rq,RF_PROTO_E_INVALID_ARGUMENT,"research declaration/order refused before BRAM");
    }
    rc=read_exact(c,d->wire,10160);
    rf_sha256_hex(d->wire,10160,digest);
    if(rc!=0||strcmp(digest,rf_headroom_payload_sha(a))||rf_iq_wire_to_bram(d->wire,10160,d->words,2540)!=0) {
        (void)headroom_close(d);return refuse_frame(c,rq,RF_PROTO_E_INVALID_ARGUMENT,"research payload incomplete or SHA mismatch");
    }
    if(rf_core_write_tx(d->core,d->words,2540)!=RF_ERR_OK)return headroom_refuse(c,rq,"research BRAM write failed");
    d->headroom.loaded=true;
    return respond_ok(c,rq->id,"{\"loaded\":true,\"samples\":2540}");
}
static int op_headroom_capture(conn_t *c,const rf_proto_request_t *rq)
{
    rf_daemon_t *d=c->d;unsigned a=d->headroom.attempt+1,i,rails=0;int rc=RF_ERR_OK,cleanup,n,postrc;
    bool have_iq=false,on=a>=2&&a<=4;rf_headroom_readback_t before,after;uint64_t t[8]={0},stamp;
    int capture_code,clock_code=RF_ERR_OK,close_code=RF_ERR_OK;
    char bj[800]="null",aj[800]="null",iqsha[65],errors[512],closure[256];
    const char *terminal="AWAIT_ACCEPT",*error_stage="NONE";
    if(!hr_next(d,rq)||!hr_int(rq->args,"samples",4096)||!hr_int(rq->args,"ratio",0)||!hr_int(rq->args,"settle_wait_us",0)||on!=d->headroom.loaded)
        return headroom_refuse(c,rq,"research capture order/fixed arguments/load refused");
    d->headroom.attempt=a;d->headroom.loaded=false; /* attempt consumed before hardware */
    rc=rf_core_get_headroom_readback(d->core,&before);
    if(rc==RF_ERR_OK&&!hr_rf_fixed(&before))rc=RF_ERR_HARDWARE_IO;
    if(rc==RF_ERR_OK)hr_rb_json(bj,sizeof bj,&before);
    if(rc==RF_ERR_OK&&on)rc=rf_core_configure_tx_path(d->core,2540,true,NULL);
    t[0]=d->cfg.now_ns();
    if(rc==RF_ERR_OK&&on)rc=rf_core_set_tx_source(d->core,true,NULL);
    t[1]=d->cfg.now_ns();
    if(rc==RF_ERR_OK)rc=rf_core_prepare_rx(d->core,0,4096,false);
    t[2]=d->cfg.now_ns();if(rc==RF_ERR_OK)rc=rf_core_start_rx(d->core);t[3]=d->cfg.now_ns();
    t[4]=d->cfg.now_ns();
    /* Freeze original BRAM before cleanup, so a teardown failure cannot destroy evidence. */
    if(rc==RF_ERR_OK){rc=rf_core_read_rx(d->core,d->words,4096);have_iq=rc==RF_ERR_OK;}
    t[5]=d->cfg.now_ns();t[6]=d->cfg.now_ns();
    capture_code=rc;
    cleanup=rf_core_release_part_keep_state(d->core,NULL);t[7]=d->cfg.now_ns();
    if(rc==RF_ERR_OK&&cleanup!=RF_ERR_OK)rc=cleanup;
    postrc=rf_core_get_headroom_readback(d->core,&after);
    if(postrc==RF_ERR_OK){hr_rb_json(aj,sizeof aj,&after);if(!hr_rf_fixed(&after)||after.pl.cap_ratio!=0||after.pl.cap_len!=4096)postrc=RF_ERR_HARDWARE_IO;}
    if(rc==RF_ERR_OK)rc=postrc;
    stamp=d->cfg.now_ns();
    for(i=0;i<8;i++)if(t[i]==0||(i&&t[i]<t[i-1]))clock_code=RF_ERR_HARDWARE_IO;
    if(!stamp||stamp<t[7])clock_code=RF_ERR_HARDWARE_IO;
    if(rc==RF_ERR_OK)rc=clock_code;
    if(capture_code!=RF_ERR_OK)error_stage="CAPTURE_OR_CONFIG";
    else if(cleanup!=RF_ERR_OK)error_stage="RELEASE";
    else if(postrc!=RF_ERR_OK)error_stage="POST_READBACK";
    else if(clock_code!=RF_ERR_OK)error_stage="CLOCK";
    if(!have_iq) {
        close_code=headroom_close(d);
        snprintf(errors,sizeof errors,"{\"code\":%d,\"message\":\"research original IQ unavailable\","
            "\"stage\":\"%s\",\"capture_code\":%d,\"release_code\":%d,"
            "\"post_readback_code\":%d,\"clock_code\":%d,\"close_code\":%d,"
            "\"shutdown_code\":%d,\"close_confirmed\":%s,\"iq_available\":false}",
            rc,error_stage,capture_code,cleanup,postrc,clock_code,close_code,
            d->headroom_close.shutdown_code,d->headroom_close.confirmed?"true":"false");
        n=snprintf((char*)d->frame,RF_RESP_CAP,"{\"id\":%ld,\"ok\":false,\"error\":%s}",rq->id,errors);
        if(n<0||n>=RF_RESP_CAP)return -1;
        return respond(c,(char*)d->frame);
    }
    if(rf_iq_bram_to_wire(d->words,4096,d->wire,16384)!=0)return headroom_refuse(c,rq,"research IQ conversion failed");
    for(i=0;i<8192;i++){int16_t v=(int16_t)((uint16_t)d->wire[i*2]|((uint16_t)d->wire[i*2+1]<<8));if(v==-2048||v==2047)rails++;}
    rf_sha256_hex(d->wire,16384,iqsha);
    if(rails)terminal="CLIPPED";else if(rc!=RF_ERR_OK)terminal="ERROR";
    d->headroom.frame_request_id=rq->id;d->headroom.timestamp_ns=stamp;
    if(rc!=RF_ERR_OK||rails)close_code=headroom_close(d);else d->headroom.waiting=true;
    snprintf(errors,sizeof errors,"{\"stage\":\"%s\",\"code\":%d,\"capture_code\":%d,"
        "\"release_code\":%d,\"post_readback_code\":%d,\"clock_code\":%d,\"close_code\":%d}",
        error_stage,rc,capture_code,cleanup,postrc,clock_code,close_code);
    if(rc!=RF_ERR_OK||rails)snprintf(closure,sizeof closure,
        "{\"attempted\":true,\"confirmed\":%s,\"release_code\":%d,\"shutdown_code\":%d,\"tx_active\":%s,\"rx_busy\":%s}",
        d->headroom_close.confirmed?"true":"false",d->headroom_close.release_code,
        d->headroom_close.shutdown_code,d->headroom_close.status.tx_active?"true":"false",
        d->headroom_close.status.rx_busy?"true":"false");
    else strcpy(closure,"{\"attempted\":false,\"confirmed\":false}");
    n=snprintf((char *)d->frame,RF_RESP_CAP,"{\"type\":\"research_iq_frame\",\"id\":%ld,\"direction\":\"rx\",\"format\":\"s16_iq_le\",\"samples\":4096,\"bytes\":16384,\"sample_rate_hz\":60000000,\"ratio\":0,\"timestamp_ns\":%llu,\"headroom\":{\"study\":\"HEADROOM-1\",\"session_id\":\"%s\",\"attempt\":%u,\"scenario\":\"%s\",\"capture_request_id\":%ld,\"source_sha256\":\"%s\",\"declaration_sha256\":\"%s\",\"iq_sha256\":\"%s\",\"source_enabled\":%s,\"terminal\":\"%s\",\"rail_count\":%u,\"settle_wait_us\":0,\"actual_rf_before\":%s,\"actual_rf_after\":%s,\"timing_ns\":[%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu]}}",
      rq->id,(unsigned long long)stamp,d->headroom.session_id,a,rf_headroom_scenario(a),rq->id,rf_headroom_payload_sha(a),rf_headroom_declaration_sha(a),iqsha,on?"true":"false",terminal,rails,bj,aj,
      (unsigned long long)t[0],(unsigned long long)t[1],(unsigned long long)t[2],(unsigned long long)t[3],(unsigned long long)t[4],(unsigned long long)t[5],(unsigned long long)t[6],(unsigned long long)t[7]);
    /* Add error/closure evidence without changing the existing payload framing.
     * Named brackets are API-clock observations, not DATA_CLK/sample edges. */
    if(n>1 && n<RF_RESP_CAP) {
        int extra=snprintf((char*)d->frame+n-2,RF_RESP_CAP-(size_t)n+2,
            ",\"native_error\":%s,\"closure\":%s,\"timing_scope\":\"API_MONOTONIC_NOT_SAMPLE_EDGES\","
            "\"timing_order\":[\"source_begin\",\"source_end\",\"rx_arm_begin\",\"rx_arm_end\","
            "\"read_begin\",\"read_end\",\"release_begin\",\"release_end\"]}}",errors,closure);
        if(extra<0 || (size_t)extra>=RF_RESP_CAP-(size_t)n+2)n=RF_RESP_CAP;
        else n=n-2+extra;
    }
    if(n<0||n>=RF_RESP_CAP){(void)headroom_close(d);return -1;}
    d->frame[n]='\n';memcpy(d->frame+n+1,d->wire,16384);
    if(send_all(c,d->frame,n+1+16384)!=0){(void)headroom_close(d);return -1;}
    if(d->armed)d->last_control_ns=d->cfg.now_ns();
    return 0;
}
static int op_headroom_accept(conn_t *c,const rf_proto_request_t *rq)
{
    rf_daemon_t *d=c->d;bool accepted=false;char stamp[32];
    snprintf(stamp,sizeof stamp,"%llu",(unsigned long long)d->headroom.timestamp_ns);
    if(!d->headroom.active||!d->headroom.waiting||!rf_headroom_identity(rq->args,d->headroom.session_id)||
       !hr_int(rq->args,"attempt",d->headroom.attempt)||!hr_int(rq->args,"frame_id",d->headroom.frame_request_id)||
       !hr_str(rq->args,"timestamp_ns",stamp)||rf_proto_arg_bool(rq->args,"accepted",&accepted)!=0||!accepted)
        return headroom_refuse(c,rq,"research acceptance rejected or wrong frame");
    d->headroom.waiting=false;
    if(d->headroom.attempt==5){if(headroom_close(d)!=0)return refuse(c,rq,RF_PROTO_E_HARDWARE_IO,"research completion stop unconfirmed");return respond_ok(c,rq->id,"{\"completed\":true,\"attempts\":5,\"armed\":false}");}
    return respond_ok(c,rq->id,"{\"accepted\":true}");
}
static int op_headroom_abort(conn_t *c,const rf_proto_request_t *rq)
{
    if(!c->d->headroom.used||!rf_headroom_identity(rq->args,c->d->headroom.session_id))return headroom_refuse(c,rq,"research abort identity mismatch");
    return headroom_close(c->d)==0?respond_ok(c,rq->id,"{\"aborted\":true,\"armed\":false}"):refuse(c,rq,RF_PROTO_E_HARDWARE_IO,"research abort stop unconfirmed");
}

/* ------------------------------------------------------------------ dispatch */

static int op_protocol_close(conn_t *c, const rf_proto_request_t *rq)
{
    (void)respond_ok(c, rq->id, "{\"closed\":true}");
    return 1;
}

/* The control operations enabled once the daemon is armed.  `control_op` in the
 * table says an operation MAY write; this list says which writes have a reviewed
 * contract.  Marking a row control_op does NOT enable it.
 *
 * Phase 2 enabled control.disarm / rf.init / rf.configure / tx.stop / loopback.stop.
 * The sample-path phase adds exactly two more -- the minimum that yields a closed
 * TX->RX transaction without external wiring:
 *   tx.load                  writes the TX BRAM: no RF, nothing radiated
 *   loopback.digital.capture the on-chip AD9361 data-port loop, one capture, and the
 *                            tap closed again before the op returns
 * tx.start and rx.capture are enabled for the operator-requested physical tests:
 * TX1/TX2 were cabled to the oscilloscope, and now TX1 is looped by coax to RX1, so a
 * receiver exists and RX data capture is meaningful.  Everything else stays absent on
 * purpose: loopback.digital.start (would leave the tap open) and
 * loopback.physical.start (needs the 40 dB attenuator attestation). */
static bool control_op_enabled(const char *name)
{
    static const char *const enabled[] = {
        "control.disarm", "rf.init", "rf.configure",
        "headroom.begin", "headroom.load", "headroom.capture", "headroom.accept", "headroom.abort",
        "tx.load", "tx.start", "rx.capture",
        "tx.stop", "loopback.stop",
        "loopback.digital.capture", "loopback.physical.capture",
        "idelay.configure", "rf.rx_data_delay.set",
    };
    size_t i;

    for (i = 0; i < sizeof enabled / sizeof enabled[0]; ++i) {
        if (strcmp(name, enabled[i]) == 0)
            return true;
    }
    return false;
}

/* Arming is a REQUEST, never a start-up flag: the shipped unit carries no control
 * argument, so installing or starting a release cannot arm anything.  The token is
 * checked by an injected verifier whose production implementation reads a file the
 * operator provisions OUTSIDE every release and manifest; with no verifier configured
 * arming is always refused (fail-closed).
 *
 * A rejected token is PERMISSION, not INVALID_STATE.  "This caller may not arm" is a
 * different failure from "the daemon is in the wrong state"; Task 7 classifies failures
 * by code, so the two had to stop sharing one.  The code was appended and the wire
 * revision moved only minor (rf_version.h), so a phase-1 client is unaffected. */
static int op_control_arm(conn_t *c, const rf_proto_request_t *rq)
{
    char token[128];
    int have = rf_proto_arg_str(rq->args, "token", token, sizeof token);

    if (c->d->armed)
        return respond_ok(c, rq->id, "{\"armed\":true,\"already\":true}") == 0 ? 0 : -1;
    if (!c->d->cfg.verify_control_token || have != RF_PROTO_ARG_OK ||
        c->d->cfg.verify_control_token(token) != 0) {
        fprintf(stderr, "rf_daemon: control.arm refused (%s)\n",
                !c->d->cfg.verify_control_token ? "no token source configured" :
                have != RF_PROTO_ARG_OK ? "no token presented" : "token rejected");
        return refuse(c, rq, RF_PROTO_E_PERMISSION,
                      !c->d->cfg.verify_control_token ?
                      "this daemon has no control token source, so control cannot be armed" :
                      "control token rejected");
    }
    /* Acquire the write-capable hardware BEFORE arming: an arm that cannot open the
     * aperture must not report success, or the operator would believe control is
     * available while every hardware op still refuses.  A failure here leaves the
     * daemon unarmed and owns nothing. */
    if (c->d->cfg.control_hw.acquire) {
        rf_core_t *core = NULL;
        int acq = c->d->cfg.control_hw.acquire(c->d->cfg.control_hw.ctx, &core);

        if (acq != RF_ERR_OK || !core) {
            fprintf(stderr, "rf_daemon: control.arm refused (%s)\n",
                    "the write-capable RF hardware could not be opened");
            return refuse(c, rq,
                          map_rf_error(acq != RF_ERR_OK ? acq : RF_ERR_HARDWARE_IO),
                          "control was not armed: the write-capable RF hardware "
                          "could not be opened");
        }
        c->d->core = core;
        c->d->core_owned = true;
    }
    c->d->armed = true;
    c->d->armed_at_ns = c->d->cfg.now_ns();
    c->d->last_control_ns = c->d->armed_at_ns;
    fprintf(stderr, "rf_daemon: control armed by an accepted token\n");
    return respond_ok(c, rq->id, "{\"armed\":true}") == 0 ? 0 : -1;
}

static int op_control_disarm(conn_t *c, const rf_proto_request_t *rq)
{
    /* The same call every exit path makes, so an operator's disarm and a shutdown
     * are one state change with one kind of audit line (rf_daemon_disarm). */
    (void)rf_daemon_disarm(c->d, "control.disarm request");
    return respond_ok(c, rq->id, "{\"armed\":false}") == 0 ? 0 : -1;
}

/* What device.info advertises as reachable RIGHT NOW: arming while unarmed, and the
 * enabled control set plus disarming while armed.  Advertising is not permission --
 * dispatch() enforces the same conditions independently. */
static bool control_op_advertised(const rf_daemon_t *d, const char *name)
{
    if (strcmp(name, "control.arm") == 0)
        return !d->armed;
    if (!d->armed)
        return false;
    return control_op_enabled(name);
}

/* This is the single source of truth for both dispatch and device.info.  The
 * list describes only the daemon's control-plane surface; it makes no claim
 * about TX/RX data-plane competence.
 *
 * `read_only` marks the operations status-only mode keeps.  A positional rule
 * ("the first six rows") was used before and is deliberately gone: it made the
 * security property depend on table ORDER, so a read-only op appended later was
 * silently disabled and a control op moved up was silently allowed.  With the
 * flag, status-only dispatch refuses every row that is not marked, and
 * device.info advertises exactly the marked rows -- one declaration per op.
 * The client's own allowlist (rf_status_client.READ_ONLY_OPS) must match the
 * marked rows in order, which rf_overlay/sw/tests/test_status_only_contract.py
 * checks against the rows below.
 *
 * `control_op` is the complement and exists so that "which operations are
 * control" is DATA rather than "everything the read-only flag is missing".
 * The two sets must be disjoint and cover the table (checked by
 * rf_overlay/sw/tests/test_control_split_contract.py), and phase 2 refuses every
 * control_op row unless the daemon is explicitly armed -- see
 * docs/superpowers/plans/2026-09-29-merged-control-phase2.md.  Marking a row
 * control_op does NOT enable it: the arming gate and the per-phase enable list
 * decide that separately. */
static const dispatch_entry_t dispatch_table[] = {
    { "protocol.hello", op_hello, false, true, false },
    { "protocol.close", op_protocol_close, true, true, false },
    { "device.info", op_device_info, false, true, false },
    { "state.get", op_state_get, false, true, false },
    { "status.subscribe", op_status_subscribe, false, true, false },
    { "pl.status.get", op_pl_status_get, false, true, false },
    { "control.arm", op_control_arm, false, false, true },
    { "control.disarm", op_control_disarm, false, false, true },
    { "rf.init", op_rf_init, false, false, true },
    { "rf.configure", op_rf_configure, false, false, true },
    { "tx.load", op_tx_load, false, false, true },
    { "tx.start", op_tx_start, false, false, true },
    { "tx.stop", op_stop, false, false, true },
    { "rx.capture", op_rx_capture, false, false, true },
    { "loopback.digital.start", op_loopback_digital_start, false, false, true },
    { "loopback.digital.capture", op_loopback_digital_capture, false, false, true },
    { "loopback.physical.capture", op_loopback_physical_capture, false, false, true },
    { "loopback.physical.start", op_loopback_physical_start, false, false, true },
    { "loopback.stop", op_stop, false, false, true },
    /* The research operations are appended, not inserted.  device.info advertises
     * control_ops in table order, so putting them anywhere but last would reorder a
     * list existing clients already read -- and the additive revision exists to make
     * this move invisible to them. */
    { "headroom.begin", op_headroom_begin, false, false, true },
    { "headroom.load", op_headroom_load, false, false, true },
    { "headroom.capture", op_headroom_capture, false, false, true },
    { "headroom.accept", op_headroom_accept, false, false, true },
    { "headroom.abort", op_headroom_abort, false, false, true },
    /* Additive tap-only control path: read-only PL registers after the ordinary
     * arm/session gate; it does not rerun AD9361 configuration. */
    { "idelay.configure", op_idelay_configure, false, false, true },
    { "rf.interface_status", op_rf_interface_status, false, true, false },
    { "rf.rx_data_delay.set", op_rf_rx_data_delay_set, false, false, true },
};

static size_t dispatch_count(void)
{
    return sizeof dispatch_table / sizeof dispatch_table[0];
}

/* 0 = keep serving, 1 = the client asked to close, -1 = end with a failure (the
 * response for it, if any, is already written). */
static int dispatch(conn_t *c, const rf_proto_request_t *rq)
{
    size_t i;

    for (i = 0; i < dispatch_count(); ++i) {
        if (strcmp(rq->op, dispatch_table[i].name) == 0) {
            c->control_request = dispatch_table[i].control_op;
            if(c->d->headroom.active && c->control_request && strncmp(rq->op,"headroom.",9)!=0) {
                if(strcmp(rq->op,"control.disarm")==0 || strcmp(rq->op,"tx.stop")==0 || strcmp(rq->op,"loopback.stop")==0) {
                    int closed=headroom_close(c->d);
                    return closed==0?respond_ok(c,rq->id,"{\"armed\":false,\"headroom_terminal\":true}"):refuse(c,rq,RF_PROTO_E_HARDWARE_IO,"research stop unconfirmed");
                }
                (void)headroom_close(c->d);
                return refuse(c,rq,RF_PROTO_E_INVALID_STATE,"ordinary control seals active research");
            }
            if (c->control_request)
                clear_capture_timing(c->d);
            if (strcmp(rq->op, "device.info") != 0 &&
                strcmp(rq->op, "loopback.physical.capture") != 0) {
                bool unused;
                if (rf_proto_arg_bool(rq->args, "observe_timing", &unused) !=
                    RF_PROTO_ARG_MISSING) {
                    if(c->d->headroom.active || strncmp(rq->op,"headroom.",9)==0)
                        (void)headroom_close(c->d);
                    return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                                  "observe_timing is only supported by physical capture and device.info");
                }
            }
            /* The settling wait belongs to the one transaction that has a TX-on
             * and an RX-arm to put a wait between.  Anywhere else it is a typo,
             * and a typo must not become a silent no-op. */
            if (strcmp(rq->op, "loopback.physical.capture") != 0 && strcmp(rq->op,"headroom.capture") != 0) {
                long unused_wait;
                if (opt_uint(rq, "settle_wait_us", &unused_wait) != 1) {
                    if(c->d->headroom.active || strncmp(rq->op,"headroom.",9)==0)
                        (void)headroom_close(c->d);
                    return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                                  "settle_wait_us is only supported by physical capture");
                }
            }
            if (c->d->cfg.mode == RF_DAEMON_STATUS_ONLY &&
                dispatch_table[i].control_op) {
                /* Arming is the one control operation reachable while unarmed: it is how
                 * a request arms, and its handler enforces the token.  Everything else
                 * needs an armed daemon AND a contract reviewed for this phase.
                 *
                 * The gate is scoped to STATUS_ONLY on purpose.  RF_DAEMON_CONTROL_TEST
                 * is the harness mode: the C suite configures it to unit-test the write
                 * handlers, and it is unreachable in production because main() accepts
                 * only `--mode status-only` (asserted by test_rf_daemon_main.c) and the
                 * shipped unit passes no control flag (asserted by
                 * rf_overlay/sw/tests/test_control_split_contract.py).  The control
                 * property therefore holds where it has to: in the mode the board runs. */
                if (strcmp(dispatch_table[i].name, "control.arm") == 0)
                    return dispatch_table[i].handler(c, rq);
                if (!c->d->armed)
                    return refuse(c, rq, RF_PROTO_E_UNSUPPORTED,
                                  "operation disabled in status-only mode");
                if (!control_op_enabled(dispatch_table[i].name))
                    return refuse(c, rq, RF_PROTO_E_UNSUPPORTED,
                                  "operation not enabled in this phase");
            }
            return dispatch_table[i].handler(c, rq);
        }
    }
    if(c->d->headroom.active) (void)headroom_close(c->d);
    return refuse(c, rq, RF_PROTO_E_UNSUPPORTED,
                  "this daemon does not implement that operation");
}

static int run_loop(conn_t *c)
{
    for (;;) {
        rf_proto_request_t rq;
        rf_proto_code_t code = RF_PROTO_E_NONE;
        int r, d;

        /* A controlled termination must be noticed DURING a connection: the read
         * below can block for the transport's whole timeout, and the service's stop
         * budget is shorter than that.  Ending the connection cleanly here runs the
         * same confirmed-shutdown path an ordinary EOF does -- rather than leaving
         * the part to be brought to rest by the final watchdog after a kill. */
        if (c->d->cfg.should_stop && c->d->cfg.should_stop() != 0)
            return 0;
        /* An armed daemon whose client has gone silent must not stay armed for as long
         * as nobody connects: give the watchdog its turn before waiting again. */
        (void)rf_daemon_watchdog_tick(c->d, c->d->cfg.now_ns());
        r = read_line(c);
        if (r == 0)
            return 0;                 /* EOF: a clean end */
        if (r == -3) {
            (void)respond_err(c, RF_PROTO_E_OVERFLOW,
                              "the line exceeds the protocol line limit");
            return -1;
        }
        if (r == -1) {
            (void)respond_err(c, RF_PROTO_E_TIMEOUT,
                              "the client stopped sending");
            return -1;
        }
        if (r == -2)
            return -1;

        /* A complete request that arrived just as a stop was requested must not be
         * dispatched.  The check before the read covers a signal that lands while
         * idle; this one covers the opposite race -- the line was already in the
         * buffer, so returning from the read says nothing about intention to
         * continue.  The caller asked for the part to come to rest, not for one
         * more command to run, and ending here takes the SAME ordinary confirmed
         * shutdown path an EOF does. */
        if (c->d->cfg.should_stop && c->d->cfg.should_stop() != 0)
            return 0;

        if (rf_proto_parse_request((const char *)c->line, c->line_len, &rq,
                                  &code) != 0) {
            if (code == RF_PROTO_E_OVERFLOW) {
                (void)respond_err(c, RF_PROTO_E_OVERFLOW,
                                  "the line exceeds the protocol line limit");
                return -1;
            }
            if(c->d->headroom.active) (void)headroom_close(c->d);
            /* The line was consumed whole, so the stream is still in sync and a
             * malformed command is recoverable. */
            if (respond_err(c, RF_PROTO_E_INVALID_ARGUMENT,
                            "the line is not a well-formed request") != 0)
                return -1;
            continue;
        }
        if (!c->hello_done && strcmp(rq.op, "protocol.hello") != 0) {
            (void)refuse(c, &rq, RF_PROTO_E_INVALID_ARGUMENT,
                         "the first request on a connection must be protocol.hello");
            return -1;
        }
        /* A blocking read may have crossed the expiry deadline. Close the
         * control gate before this request can reach a hardware handler. */
        (void)rf_daemon_watchdog_tick(c->d, c->d->cfg.now_ns());

        /* The core is the authority on what the hardware is doing; reconcile
         * before the command is judged against this daemon's own record. */
        adopt_core_state(c->d);
        d = dispatch(c, &rq);
        if (d != 0)
            return d;
    }
}

int rf_daemon_serve(rf_daemon_t *daemon, rf_transport_t *transport)
{
    conn_t c;
    int rc;

    if (!daemon || !transport || !transport->read || !transport->write_all)
        return -1;
    if (rf_daemon_lease_acquire(daemon) != 0) {
        /* The live client's request ids are its own, so the refusal carries no
         * id.  A refused acquire touches no hardware and preempts nobody. */
        char buf[256];
        char line[320];
        int n = rf_proto_write_error_no_id(buf, sizeof buf, RF_PROTO_E_BUSY,
                                          "another client holds the lease");

        if (n > 0 && (size_t)n + 1u < sizeof line) {
            memcpy(line, buf, (size_t)n);
            line[n] = '\n';
            (void)transport->write_all(transport->ctx, line, (size_t)n + 1u);
        }
        if (transport->close)
            transport->close(transport->ctx);
        return 0;
    }

    memset(&c, 0, sizeof c);
    c.d = daemon;
    c.t = transport;
    rc = run_loop(&c);
    /* The disconnect rule runs before the lease is released, so the part is
     * brought to rest as this client's last act rather than as the next one's
     * problem.  If that could not be CONFIRMED the caller has to hear about it:
     * a shutoff nobody verified is not a clean end, so it is reported even
     * though the connection itself is over. */
    /* Unconditional, and deliberately not scoped to the harness any more.  The rule
     * returns 0 immediately when there is no core (the unarmed production path) and
     * when the session is OFFLINE, so this stays a zero-write no-op there.  But once
     * a successful arm can hand the daemon a real core, a production client that
     * disappears mid-operation is exactly the case the rule exists for: stop what is
     * live before the next client inherits it. */
    if (daemon->headroom.active && headroom_close(daemon) != 0) rc=-1;
    if (apply_disconnect_rule(daemon) != 0)
        rc = -1;
    rf_daemon_lease_release(daemon);
    if (transport->close)
        transport->close(transport->ctx);
    return rc;
}


