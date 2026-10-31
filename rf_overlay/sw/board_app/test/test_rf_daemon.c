/* test_rf_daemon.c -- framing, dispatch and disconnect-safety tests.
 *
 * The plan's Step 3 asks for socket framing tests written against
 * `socketpair()`.  This toolchain cannot do that: MinGW-w64 has no
 * <sys/socket.h>, so a socketpair-based test could not be compiled -- let alone
 * run -- on the development machine, which is where the whole point of a host
 * test is.  The daemon therefore takes an `rf_transport_t` vtable, and this file
 * drives it with a scripted byte stream.  That is strictly stronger for the
 * cases the plan names: a short read, a timeout mid-payload and a disconnect
 * mid-payload are scripted exactly, where a real socket would produce them only
 * by luck.  (Recorded as a plan amendment, D2.)
 *
 * Every check here is about framing and gates.  Nothing here claims the RF
 * signal path works -- that is Task 10's layered board acceptance, and until it
 * runs the answer for hardware is NOT RUN.
 */
#include "rf_daemon.h"
#include "rf_daemon_main.h"
#include "rf_transport_policy.h"
#include "rf_core.h"
#include "rf_iq.h"
#include "rf_version.h"
#include "platform_rf.h"
#include "fake_pl.h"

#include <stdio.h>
#include <string.h>

/* The physical-loopback gate approves exactly one attenuation, owned by
 * RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB (rf_state.h: the teaching/environment domain
 * single source).  Every physical request below names it through this expansion, so a
 * domain move cannot leave a request in this file quietly asking for the old value --
 * which is exactly how the 2026-10-18 mismatch survived: the requests here still said
 * 40000 while the gate had moved to 46000. */
#define TEST_STRINGIFY_(value) #value
#define TEST_STRINGIFY(value) TEST_STRINGIFY_(value)
#define LOOPBACK_ATTENUATION_ARG \
    "\"attenuation_mdB\":" TEST_STRINGIFY(RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB)

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

/* ------------------------------------------------------- scripted transport */

typedef struct {
    /* The scripted stream IS a transport, so the daemon is driven through its
     * real interface rather than a mock of it. */
    rf_transport_t t;
    uint8_t in[8192];
    size_t in_len, in_pos;
    /* Per-call read plan: >0 = offer up to that many bytes, 0 = EOF, <0 = the
     * transport error code to return.  When exhausted, the remaining input is
     * offered whole and then EOF. */
    int plan[512];
    int plan_n, plan_i;
    uint8_t out[32768];
    size_t out_len;
    int write_fail_at;   /* -1 = never; otherwise fail once out_len reaches it */
    int closed;
} script_t;

/* A scripted read boundary can model external shutdown/watchdog activity while
 * a connection remains live; callbacks are disabled in every other fixture. */
static void (*timing_before_read)(void *ctx);
static void *timing_read_ctx;
static size_t timing_read_offset;

static int s_read(void *ctx, void *buf, size_t len)
{
    script_t *s = (script_t *)ctx;

    if (timing_before_read && s->in_pos == timing_read_offset) {
        void (*action)(void *) = timing_before_read;
        timing_before_read = NULL;
        action(timing_read_ctx);
    }

    if (s->plan_i < s->plan_n) {
        int v = s->plan[s->plan_i++];
        size_t n;
        if (v < 0)
            return v;
        if (v == 0)
            return 0;
        n = (size_t)v;
        if (n > len)
            n = len;
        if (n > s->in_len - s->in_pos)
            n = s->in_len - s->in_pos;
        if (n == 0)
            return 0;
        memcpy(buf, s->in + s->in_pos, n);
        s->in_pos += n;
        return (int)n;
    }
    if (s->in_pos < s->in_len) {
        size_t n = s->in_len - s->in_pos;
        if (n > len)
            n = len;
        memcpy(buf, s->in + s->in_pos, n);
        s->in_pos += n;
        return (int)n;
    }
    return 0;
}

static int s_write(void *ctx, const void *buf, size_t len)
{
    script_t *s = (script_t *)ctx;

    if (s->write_fail_at >= 0 && (int)s->out_len >= s->write_fail_at)
        return -1;
    if (s->out_len + len + 1 > sizeof s->out)
        return -1;
    memcpy(s->out + s->out_len, buf, len);
    s->out_len += len;
    s->out[s->out_len] = '\0';
    return 0;
}

static void s_close(void *ctx)
{
    script_t *s = (script_t *)ctx;
    s->closed = 1;
}

static void script_init(script_t *s)
{
    memset(s, 0, sizeof *s);
    s->write_fail_at = -1;
    s->t.ctx = s;
    s->t.read = s_read;
    s->t.write_all = s_write;
    s->t.close = s_close;
}

/* The one place the scripted stream is presented as a transport. */
static int serve(rf_daemon_t *d, script_t *s)
{
    return rf_daemon_serve(d, &s->t);
}

/* Offer the stream in chunks of 1..max_chunk bytes, for the whole input. */
static void plan_chunks(script_t *s, int max_chunk)
{
    int i, total = 0, need;

    for (i = 0; i < (int)(sizeof s->plan / sizeof s->plan[0]); ++i)
        s->plan[i] = (i % max_chunk) + 1;
    s->plan_n = (int)(sizeof s->plan / sizeof s->plan[0]);
    /* Enough entries to walk the whole input, so the chunking is what ends the
     * stream rather than the plan running dry mid-line. */
    need = 0;
    for (i = 0; i < s->plan_n && (size_t)total < s->in_len; ++i)
        total += s->plan[i];
    (void)need;
    s->plan_i = 0;
}

static void feed(script_t *s, const char *text)
{
    size_t n = strlen(text);
    memcpy(s->in + s->in_len, text, n);
    s->in_len += n;
}

static void feed_bytes(script_t *s, const void *p, size_t n)
{
    memcpy(s->in + s->in_len, p, n);
    s->in_len += n;
}

static void req(script_t *s, long id, const char *op, const char *args)
{
    char line[512];
    snprintf(line, sizeof line, "{\"id\":%ld,\"op\":\"%s\",\"args\":%s}\n", id, op,
             args);
    feed(s, line);
}

static int contains(const script_t *s, const char *needle)
{
    return strstr((const char *)s->out, needle) != NULL;
}

static int occurrences(const script_t *s, const char *needle)
{
    const char *p = (const char *)s->out;
    size_t n = strlen(needle);
    int c = 0;

    while ((p = strstr(p, needle)) != NULL) {
        ++c;
        p += n;
    }
    return c;
}

/* The refusal of request `id`, exactly as rf_proto_write_error serialises it.  Keyed
 * on the id as well as the code, so a criterion cannot pass on a refusal of some OTHER
 * request in the same script -- which is what a bare "ok:false is somewhere" would do. */
static int refused_with(const script_t *s, long id, const char *code)
{
    char needle[96];

    snprintf(needle, sizeof needle,
             "\"id\":%ld,\"ok\":false,\"error\":{\"code\":\"%s\"", id, code);
    return contains(s, needle);
}

/* ------------------------------------------------------------------- daemon */

static unsigned timing_clock_calls;
static unsigned timing_bad_at;
static bool timing_ordered;
static bool timing_reverse;
static uint64_t timing_clock_base = 1000000000ull;

static uint64_t test_now_ns(void)
{
    ++timing_clock_calls;
    if (timing_bad_at == timing_clock_calls)
        return timing_reverse ? 1ull : 0ull;
    if (timing_ordered)
        return timing_clock_base + (uint64_t)timing_clock_calls * 100ull;
    /* A fixed value on purpose: a frame that reports a timestamp must report one
     * it actually got, and a deterministic clock makes "it got it" checkable. */
    return 1234567890123ull;
}

/* The injected settling wait.  It does NOT sleep: what can be wrong is whether
 * the requested duration reached an injected call at all, and that is asserted
 * here rather than re-measured by spending wall-clock in a unit test.
 * sleep_at_ctrl_writes records how many core-register writes had happened when
 * the wait was taken, so "the wait is inside the capture transaction" is proved
 * against the fake PL rather than read off the source. */
static unsigned sleep_calls;
static uint64_t sleep_total_us;
static uint64_t last_sleep_us;
static int sleep_at_ctrl_writes;
static int sleep_forces_failure;

static int test_sleep_us(uint64_t us)
{
    ++sleep_calls;
    last_sleep_us = us;
    sleep_total_us += us;
    sleep_at_ctrl_writes = fake_ctrl_writes;
    return sleep_forces_failure ? -1 : 0;
}

/* The release identity these tests use: the two hashes the released manifest
 * (rf_overlay/out/rf_release_manifest.json) actually carries, so the identity
 * under test has the shape of a real one rather than a placeholder. */
#define TEST_BIT_SHA256 \
    "7381804856d1af90400d80c6583587847deac5435234a0d074bfeb93875d1dc3"
#define TEST_HWH_SHA256 \
    "205bd728b4e878ecec113e3b4aaa1108a829b47833e4adfa90e5ce470459e901"

/* Every daemon this file builds gets a known identity, so device.info always
 * reports a value and never reads an uninitialized config field. */
static void fill_identity(rf_daemon_config_t *cfg)
{
    memset(cfg, 0, sizeof *cfg);
    cfg->mode = RF_DAEMON_CONTROL_TEST;
    cfg->max_samples = RF_BRAM_WORDS;
    cfg->port = 0u;
    cfg->now_ns = test_now_ns;
    cfg->sleep_us = test_sleep_us;
    strcpy(cfg->identity.bit_sha256, TEST_BIT_SHA256);
    strcpy(cfg->identity.hwh_sha256, TEST_HWH_SHA256);
}

static rf_core_t *healthy_core(void)
{
    rf_core_t *core = NULL;
    rf_core_open_args_t open_args = { false, false };
    rf_init_request_t request = { true, 10000u, 80000000ull, 60000000u, 1u };
    rf_readback_t readback;

    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    if (rf_core_create(&open_args, &core) != RF_ERR_OK ||
        rf_core_open(core, &open_args) != RF_ERR_OK ||
        rf_core_init(core, &request, &readback) != RF_ERR_OK) {
        rf_core_destroy(core);
        return NULL;
    }
    return core;
}

/* An OPEN but NOT initialized part: the state the daemon meets at startup, before
 * the first rf.init.  It is drivable by nothing yet -- not even a stop. */
static rf_core_t *opened_core(void)
{
    rf_core_t *core = NULL;
    rf_core_open_args_t open_args = { false, false };

    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    if (rf_core_create(&open_args, &core) != RF_ERR_OK ||
        rf_core_open(core, &open_args) != RF_ERR_OK) {
        rf_core_destroy(core);
        return NULL;
    }
    return core;
}

static rf_daemon_t *new_daemon(rf_core_t *core)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;

    fill_identity(&cfg);
    if (rf_daemon_create(&cfg, &d) != 0)
        return NULL;
    if (core)
        rf_daemon_attach_core(d, core);
    return d;
}

/* ------------------------------------------------------- release identity */

static const char *IDENTITY_MANIFEST = "test_rf_daemon_identity.json";

static void write_identity_manifest(void)
{
    FILE *file = fopen(IDENTITY_MANIFEST, "wb");

    if (!file)
        return;
    fprintf(file,
            "{\n  \"product\": \"ac820-ad9361-rf\",\n"
            "  \"protocol_major\": %u,\n  \"daemon_version\": \"%s\",\n"
            "  \"bit_sha256\": \"" TEST_BIT_SHA256 "\",\n"
            "  \"hwh_sha256\": \"" TEST_HWH_SHA256 "\"\n}\n",
            (unsigned)RF_PROTOCOL_MAJOR, RF_DAEMON_VERSION);
    fclose(file);
}

/* The opener injected into the real startup path: a healthy core over the fake
 * PL.  A pointer is all this test needs back, and startup's contract is "opened
 * but NOT initialized", which healthy_core() satisfies (it calls rf_core_init on
 * its own account, exactly as the daemon's own tests already do). */
static int open_healthy_core(void *ctx, rf_core_t **out)
{
    (void)ctx;
    *out = healthy_core();
    return *out ? 0 : -1;
}

/* A daemon whose identity came through the real path: manifest file ->
 * rf_daemon_startup -> config -> device.info.  Assembling the config by hand
 * would leave the plumbing this finding is about untested. */
static rf_daemon_t *new_daemon_with_identity(rf_core_t **out_core)
{
    rf_release_identity_t identity;
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    rf_core_t *core = NULL;
    char why[256];

    write_identity_manifest();
    if (rf_daemon_startup(IDENTITY_MANIFEST, open_healthy_core, NULL, &core,
                          &identity, why, sizeof why) != 0)
        return NULL;
    fill_identity(&cfg);
    cfg.identity = identity;
    if (rf_daemon_create(&cfg, &d) != 0) {
        rf_core_destroy(core);
        return NULL;
    }
    rf_daemon_attach_core(d, core);
    if (out_core)
        *out_core = core;
    return d;
}

/* The operation names out of a device.info response's
 * "capabilities":{"ops":[...]}, so the list under test is the one the daemon
 * actually emitted and not a copy that could drift from it.  Returns the number
 * found, 0 when the response does not have the promised shape. */
static int parse_capability_ops(const char *json, char (*out)[RF_PROTO_OP_MAX],
                                int max)
{
    const char *p = strstr(json, "\"capabilities\":{\"ops\":[");
    int n = 0;

    if (!p)
        return 0;
    p += strlen("\"capabilities\":{\"ops\":[");
    while (*p == '"') {
        const char *end = strchr(p + 1, '"');
        size_t len;

        if (!end)
            return 0;
        len = (size_t)(end - (p + 1));
        if (len == 0 || len >= (size_t)RF_PROTO_OP_MAX || n >= max)
            return 0;
        memcpy(out[n], p + 1, len);
        out[n][len] = '\0';
        ++n;
        p = end + 1;
        if (*p == ',')
            ++p;
    }
    return *p == ']' ? n : 0;
}

static void hello(script_t *s, long id)
{
    req(s, id, "protocol.hello", "{\"major\":1,\"minor\":0}");
}

/* What ONE scripted connection costs in CORE-REGISTER writes (fake_ctrl_writes), run to
 * completion.  Two reasons it is not fake_mmio_writes:
 *
 *   - every connection ends with the disconnect rule -- the stop path, which has its own
 *     criteria -- so "what did this REQUEST write?" is a comparison between two
 *     connections that differ only in the request, not a total;
 *   - a READBACK is a read that this PL's SPI master performs by programming its own
 *     control register in the spi window, so a count of every MMIO write would make
 *     "the part was not programmed again" fail because the daemon READ the part.  The
 *     criterion is about programming the part, and that is the axi_ctrl_0 window. */
static int connection_ctrl_cost(rf_daemon_t *d, script_t *s, const char *configure)
{
    int before = fake_ctrl_writes;

    script_init(s);
    hello(s, 1);
    if (configure)
        req(s, 2, "rf.configure", configure);
    req(s, configure ? 3 : 2, "protocol.close", "{}");
    (void)serve(d, s);
    return fake_ctrl_writes - before;
}

/* --------------------------------------------------------------------- main */

typedef struct {
    uint32_t failed_addr;
    uint32_t status_word_override;
    int use_status_word_override;
} status_read_ctx_t;
static int status_read(void *ctx, uint32_t address, uint32_t *value)
{
    status_read_ctx_t *r = (status_read_ctx_t *)ctx;
    if (address == r->failed_addr)
        return -1;
    if (r->use_status_word_override && address == RF_CTRL_A(RF_REG_STATUS)) {
        *value = r->status_word_override;
        return 0;
    }
    *value = fake_pl_read(address);
    return 0;
}

static void test_status_only(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    rf_pl_status_reader_t *reader;
    status_read_ctx_t read_ctx = { 0 };
    script_t s;
    int writes;

    read_ctx.status_word_override = 0x00010017u;
    read_ctx.use_status_word_override = 1;
    fake_pl_reset();
    fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG), RF_SRC_SEL);
    fake_pl_write(RF_CTRL_A(RF_REG_SMP_LEN), 73u);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG), RF_CAP_ARM);
    fake_pl_write(RF_CTRL_A(RF_REG_STATUS), 0x00010017u);
    fake_pl_write(RF_CTRL_A(RF_REG_PHY_MODE), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 25u);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_RATIO), 2u);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_LEN), 4096u);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_STATUS), 0x00000000u);
    reader = rf_pl_status_reader_from_callback(status_read, &read_ctx);
    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = reader;
    writes = fake_mmio_writes;
    check("status-only daemon starts with no PL write",
          reader != NULL && rf_daemon_create(&cfg, &d) == 0 &&
          fake_mmio_writes == writes);
    if (!d) {
        rf_pl_status_reader_close(reader);
        return;
    }

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "device.info", "{}");
    req(&s, 3, "state.get", "{}");
    req(&s, 4, "status.subscribe", "{}");
    req(&s, 5, "pl.status.get", "{}");
    req(&s, 6, "protocol.close", "{}");
    check("six allowed status-only operations answer",
          serve(d, &s) == 1 &&
          occurrences(&s, "\"ok\":true") == 6);
    check("capability list contains exactly seven operations",
          contains(&s, "\"ops\":[\"protocol.hello\",\"protocol.close\","
                       "\"device.info\",\"state.get\",\"status.subscribe\","
                       "\"pl.status.get\",\"rf.interface_status\"]"));
    check("status-only device.info has no chip readback",
          contains(&s, "\"mode\":\"status_only\"") &&
          contains(&s, "\"core\":false,\"readback\":null"));
    check("PL snapshot reports the injected clock and register values",
          contains(&s, "\"sampled_at_monotonic_ns\":1234567890123") &&
          contains(&s, "\"io_ok\":true") &&
          contains(&s, "\"source_selected\":true,\"length_words\":73") &&
          contains(&s, "\"armed\":true"));
    check("optional diagnostics expose raw register reads",
          contains(&s, "\"diagnostics\"") &&
          contains(&s, "\"status\":{\"valid\":true,\"raw\":65559,\"clk_seen\":true") &&
          contains(&s, "\"phy_mode\":{\"valid\":true,\"raw\":127") &&
          contains(&s, "\"idelay_en\":{\"valid\":true,\"raw\":127") &&
          contains(&s, "\"idelay_tap\":{\"valid\":true,\"raw\":25") &&
          contains(&s, "\"cap_ratio\":{\"valid\":true,\"raw\":2") &&
          contains(&s, "\"cap_len\":{\"valid\":true,\"raw\":4096") &&
          contains(&s, "\"cap_status\":{\"valid\":true,\"raw\":8192,\"done\":true") &&
          contains(&s, "\"unavailable\":[\"rx_frame_valid\",\"idelay_cntvalueout\"]"));
    check("allowed queries and close wrote no PL register",
          fake_mmio_writes == writes);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{}");
    req(&s, 3, "rf.configure", "{}");
    req(&s, 4, "tx.load", "{}");
    req(&s, 5, "tx.start", "{}");
    req(&s, 6, "rx.capture", "{}");
    req(&s, 7, "loopback.physical.start", "{}");
    req(&s, 8, "tx.stop", "{}");
    check("all RF control routes refuse before handlers",
          serve(d, &s) == 0 &&
          occurrences(&s, "UNSUPPORTED") == 7 &&
          fake_mmio_writes == writes);
    check("EOF releases the logical lease", !rf_daemon_lease_held(d));

    read_ctx.failed_addr = RF_CTRL_A(RF_REG_CAP_STATUS);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "pl.status.get", "{}");
    check("failed register read is null and io_ok false",
          serve(d, &s) == 0 &&
          contains(&s, "\"io_ok\":false") &&
          contains(&s, "\"done\":null,\"position_words\":null") &&
          fake_mmio_writes == writes);

    /* Malformed and unknown requests are refused without entering any RF
     * handler or touching the aperture. */
    script_init(&s);
    hello(&s, 1);
    feed(&s, "{not-json}\n");
    req(&s, 2, "unknown.operation", "{}");
    check("malformed and unknown status requests cannot write",
          serve(d, &s) == 0 &&
          contains(&s, "INVALID_ARGUMENT") &&
          contains(&s, "UNSUPPORTED") &&
          fake_mmio_writes == writes);

    ((rf_session_t *)rf_daemon_session(d))->state = RF_STATE_READY;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "state.get", "{}");
    check("logical READY EOF releases lease without stop writes",
          serve(d, &s) == 0 && fake_mmio_writes == writes &&
          !rf_daemon_lease_held(d));

    /* Simulate inherited active state, then exercise timeout and teardown.
     * No status-only path may run the writable disconnect rule. */
    ((rf_session_t *)rf_daemon_session(d))->state = RF_STATE_TX_ACTIVE;
    script_init(&s);
    s.plan[0] = RF_TRANSPORT_TIMEOUT;
    s.plan_n = 1;
    check("status-only timeout never performs safety writes",
          serve(d, &s) == -1 && fake_mmio_writes == writes &&
          !rf_daemon_lease_held(d));
    rf_daemon_destroy(d);
    rf_pl_status_reader_close(reader);
    check("status-only service stop wrote no PL register",
          fake_mmio_writes == writes);
}

/* ------------------------------------------------------------ arming gate */

static int test_token_ok(const char *token)
{
    return token && strcmp(token, "s3cret") == 0 ? 0 : -1;
}

/* Phase 2's core property, behaviourally: an unarmed status-only daemon refuses every
 * control operation before its handler; a token the injected verifier rejects does not
 * change that; an accepted token arms it and lets the ENABLED control operations reach
 * their handlers while a later phase's operations stay refused; disarming returns it to
 * the unarmed state.  The writes counter is checked across the refused phases only --
 * an armed rf.init is supposed to touch the part. */
static void test_control_arm_gate(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    rf_core_t *core;
    script_t s;
    int writes;

    printf("=== control arm gate ===\n");
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    core = opened_core();
    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    cfg.verify_control_token = test_token_ok;
    check("a status-only daemon with a token source starts",
          core != NULL && rf_daemon_create(&cfg, &d) == 0);
    if (!d) {
        rf_core_close(core);
        return;
    }
    rf_daemon_attach_core(d, core);

    writes = fake_mmio_writes;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 3, "tx.start", "{}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("unarmed control operations are refused before their handlers",
          occurrences(&s, "operation disabled in status-only mode") == 2);
    check("the refused phase wrote no PL register", fake_mmio_writes == writes);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"wrong\"}");
    req(&s, 3, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("a rejected token is reported and leaves the daemon unarmed",
          contains(&s, "control token rejected") &&
          occurrences(&s, "operation disabled in status-only mode") == 1);
    /* Task 1 Step 2: "the token was wrong" is a permission failure, not a state
     * failure.  Asserting the code and the ABSENCE of the old one keeps this from
     * passing on a response that happens to carry both. */
    check("a rejected token is a PERMISSION failure, not a state failure",
          contains(&s, "\"code\":\"PERMISSION\"") &&
          !contains(&s, "INVALID_STATE"));

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("an accepted token arms the daemon", contains(&s, "\"armed\":true"));

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 3, "tx.start", "{}");
    req(&s, 4, "loopback.physical.start", "{}");
    req(&s, 5, "protocol.close", "{}");
    serve(d, &s);
    check("armed: the enabled operation reaches its handler",
          occurrences(&s, "operation disabled in status-only mode") == 0);
    check("armed: the later-phase physical loopback is still refused",
          occurrences(&s, "operation not enabled in this phase") == 1);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.disarm", "{}");
    req(&s, 3, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("after disarm the control operation is refused again",
          occurrences(&s, "operation disabled in status-only mode") == 1);

    rf_daemon_destroy(d);
    rf_core_close(core);
}

/* Phase 2 Task 1 Step 2: every way of failing to present an acceptable token is
 * PERMISSION, and the daemon stays unarmed.  Three shapes: no verifier at all (the
 * fail-closed shipped default), a verifier but no token in the request, and a token
 * the verifier rejects (covered in test_control_arm_gate). */
static void test_arm_permission_failures(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    script_t s;

    printf("=== arm permission failures ===\n");

    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    check("a daemon with no token source starts",
          rf_daemon_create(&cfg, &d) == 0);
    if (!d)
        return;

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"whatever\"}");
    req(&s, 3, "device.info", "{}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("with no token source, arming is PERMISSION and not INVALID_STATE",
          contains(&s, "\"code\":\"PERMISSION\"") &&
          !contains(&s, "INVALID_STATE"));
    check("and the refusal left the daemon unarmed",
          contains(&s, "\"armed\":false"));

    rf_daemon_destroy(d);
    d = NULL;

    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    cfg.verify_control_token = test_token_ok;
    check("a daemon with a token source starts",
          rf_daemon_create(&cfg, &d) == 0);
    if (!d)
        return;

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{}");
    req(&s, 3, "device.info", "{}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("a request with no token at all is PERMISSION too",
          contains(&s, "\"code\":\"PERMISSION\"") &&
          !contains(&s, "INVALID_STATE") &&
          contains(&s, "\"armed\":false"));

    rf_daemon_destroy(d);
}

/* Phase 2 Task 3/4: validation must happen BEFORE the hardware is touched, so an
 * out-of-range or incomplete request cannot leave a half-applied configuration.  The
 * assertions count answers instead of matching the refusal text: the parameter check and
 * the range check live in different modules and their wording is not this criterion's
 * business -- that exactly three of the four requests answer ok (hello, arm, close) is. */
static void test_control_validation_precedes_hardware(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    rf_core_t *core;
    script_t s;

    printf("=== control validation precedes hardware ===\n");
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    core = opened_core();
    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    cfg.verify_control_token = test_token_ok;
    check("a status-only daemon with a token source starts",
          core != NULL && rf_daemon_create(&cfg, &d) == 0);
    if (!d) {
        rf_core_close(core);
        return;
    }
    rf_daemon_attach_core(d, core);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "rf.configure", "{\"lo_hz\":100000000,\"sample_rate_hz\":1000000,"
                              "\"rx_bandwidth_hz\":1000000,\"tx_bandwidth_hz\":1000000,"
                              "\"attenuation_mdB\":90000}");   /* above the vendor ceiling */
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("an attenuation above the vendor ceiling is refused",
          occurrences(&s, "\"ok\":true") == 3);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure", "{\"attenuation_mdB\":40000}");   /* lo_hz and the rest absent */
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("an incomplete configuration is refused", occurrences(&s, "\"ok\":true") == 2);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{}");                                /* attenuation is mandatory */
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("rf.init without an explicit attenuation is refused",
          occurrences(&s, "\"ok\":true") == 2);

    rf_daemon_destroy(d);
    rf_core_close(core);
}

/* Phase 2 Task 2 Step 3, the offline half.  The daemon must not be able to exit while
 * control is armed: SIGTERM (the unit stopping or restarting), SIGINT, and a fatal
 * accept error all end in the same place.  The board's signal handling is inside
 * #ifndef PLATFORM_HOST and cannot be driven from here, so what IS driven is the exact
 * function those paths call -- rf_daemon_disarm -- and the criterion the plan names:
 * after it runs, rf.init is refused again.  That the paths actually call it, and that
 * they call only it, is pinned by
 * rf_overlay/sw/tests/test_control_split_contract.py.
 *
 * The second half is the PL-reload re-check's offline half: a reload restarts this
 * process, and a restarted daemon must come up status-only, unarmed, and refusing. */
static void test_disarm_on_exit_and_restart(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    rf_daemon_t *restarted = NULL;
    rf_core_t *core;
    script_t s;
    int writes;

    printf("=== disarm on exit and restart ===\n");
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    core = opened_core();
    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    cfg.verify_control_token = test_token_ok;
    check("a status-only daemon with a token source starts",
          core != NULL && rf_daemon_create(&cfg, &d) == 0);
    if (!d) {
        if (core)
            rf_core_close(core);
        return;
    }
    rf_daemon_attach_core(d, core);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("the daemon is armed before the shutdown", contains(&s, "\"armed\":true"));

    /* A restart while the first daemon is still armed: same config, a NEW process.  No
     * state is shared, so this is what a PL reload plus a unit restart produces. */
    writes = fake_mmio_writes;
    check("a restarted daemon is created from the same config",
          rf_daemon_create(&cfg, &restarted) == 0);
    if (restarted) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
        req(&s, 3, "device.info", "{}");
        req(&s, 4, "state.get", "{}");
        req(&s, 5, "protocol.close", "{}");
        serve(restarted, &s);
        check("a restarted daemon is status-only, unarmed and refuses control",
              contains(&s, "\"mode\":\"status_only\"") &&
              contains(&s, "\"armed\":false") &&
              contains(&s, "operation disabled in status-only mode") &&
              contains(&s, "\"control_ops\":[\"control.arm\"]"));
        check("and a daemon that just came up has no operation left pending",
              contains(&s, "\"pending\":false") &&
              contains(&s, "\"pending_op\":null"));
        check("and a restarted daemon wrote no PL register", fake_mmio_writes == writes);
        rf_daemon_destroy(restarted);
    }

    /* The one call every exit path makes. */
    check("the shutdown disarms and reports that it changed the state",
          rf_daemon_disarm(d, "SIGTERM") == 1);
    check("a second shutdown is a no-op, not a second state change",
          rf_daemon_disarm(d, "SIGTERM") == 0);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 3, "device.info", "{}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("after the shutdown rf.init is refused again",
          contains(&s, "operation disabled in status-only mode"));
    check("and device.info reports an unarmed daemon with nothing enabled",
          contains(&s, "\"armed\":false") &&
          contains(&s, "\"control_ops\":[\"control.arm\"]"));

    rf_daemon_destroy(d);
    rf_core_close(core);
}

/* A NON-DEFAULT configuration: every field differs from what healthy_core()'s
 * rf_core_init left on the part (LO 80 MHz -> 90 MHz, attenuation 10 dB -> 40 dB), and
 * the IDELAY registers are moved off their power-on value, which is what makes the
 * idempotence counter below meaningful rather than trivially zero. */
#define NON_DEFAULT_CONFIG \
    "{\"lo_hz\":90000000,\"sample_rate_hz\":60000000,\"rx_bandwidth_hz\":30000000," \
    "\"tx_bandwidth_hz\":30000000,\"attenuation_mdB\":40000," \
    "\"phy_mode_1r1t\":1,\"idelay_en\":1,\"idelay_tap\":5}"

/* Phase 2 Task 4 Steps 3-4, the offline half.
 *
 * Step 3, idempotence: the same rf.configure twice is two successes whose second answer
 * costs no register write.  The counter is the fake PL's own count of writes, so the
 * criterion is about the bus and not about a flag the daemon keeps for itself.
 *
 * Step 3, readback: what the daemon reports is what it READ BACK from the part, never
 * what the request asked for.  Those two only differ if the part ends up holding
 * something no request through the daemon asked for, so that case is constructed here
 * with the real caller that does it -- the safe-attenuation path.  A readback the part
 * cannot give is reported as unavailable rather than filled in from the request.
 *
 * Step 4, safe state: the non-transmitting safe state (TX not sourcing, RX not armed, a
 * TX that was live at the vendor ceiling) is reachable from a NON-DEFAULT configuration,
 * and reaching it does not reset the configuration. */
static void test_configure_idempotence_readback_and_safe_state(void)
{
    rf_daemon_t *d = NULL;
    rf_core_t *core;
    rf_readback_t rb;
    script_t s;
    int base_before, base_after, first, second;
    char ceiling[64];
    uint32_t words[4] = { 0x00100200u, 0x00300400u, 0x00500600u, 0x00700800u };
    uint8_t wire[16];

    printf("=== configure idempotence, readback and the safe state ===\n");
    snprintf(ceiling, sizeof ceiling, "\"attenuation_mdB\":%u",
             (unsigned)RF_SAFE_ATTENUATION_MDB);

    /* ------------------------------------------------ idempotence and readback */
    core = healthy_core();
    check("a daemon over an initialized part starts", core != NULL);
    if (!core)
        return;
    d = new_daemon(core);
    check("the daemon holds the part", d != NULL);
    if (!d) {
        rf_core_destroy(core);
        return;
    }

    base_before = connection_ctrl_cost(d, &s, NULL);
    first = connection_ctrl_cost(d, &s, NON_DEFAULT_CONFIG);
    check("the first rf.configure is accepted and answered from the part",
          contains(&s, "\"ok\":true") && contains(&s, "\"attenuation_mdB\":40000"));
    base_after = connection_ctrl_cost(d, &s, NULL);
    second = connection_ctrl_cost(d, &s, NON_DEFAULT_CONFIG);
    check("the second identical rf.configure is still a success",
          contains(&s, "\"ok\":true") && contains(&s, "\"attenuation_mdB\":40000"));
    check("the per-connection stop cost is stable, so the baseline is usable",
          base_before == base_after);
    check("the first rf.configure programmed core registers (the counter is live)",
          first > base_before);
    check("the second identical rf.configure programmed no core register",
          second == base_after);

    /* Something other than this daemon's configure now holds the part: the real caller
     * is the disconnect rule putting a live TX at the vendor ceiling.  The last value
     * REQUESTED through rf.configure is still 40000. */
    check("the safe-attenuation path takes on the part",
          rf_core_set_tx_attenuation_safe(core, RF_SAFE_ATTENUATION_MDB, NULL) ==
          RF_ERR_OK);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "device.info", "{}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("device.info reports the attenuation it read back, not the one requested",
          contains(&s, ceiling) &&
          !contains(&s, "\"attenuation_mdB\":40000"));

    check("the readback can be made to fail",
          rf_core_host_set_vendor_readback_fault(core, 1) == RF_ERR_OK);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "device.info", "{}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("an unavailable readback is null with a reason, not the last request",
          contains(&s, "\"readback\":null") &&
          contains(&s, "readback_unavailable") && !contains(&s, ceiling));
    (void)rf_core_host_set_vendor_readback_fault(core, 0);

    rf_daemon_destroy(d);
    rf_core_destroy(core);

    /* ------------------------------------------------------------- safe state */
    core = healthy_core();
    check("a second daemon over an initialized part starts", core != NULL);
    if (!core)
        return;
    d = new_daemon(core);
    if (!d) {
        rf_core_destroy(core);
        return;
    }

    /* An explicit stop from a non-default configuration: the part must end up with TX
     * not sourcing, and the stop must NOT reset what was configured. */
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
    req(&s, 3, "tx.stop", "{}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("a stop from a non-default configuration is accepted",
          contains(&s, "\"id\":3") && contains(&s, "\"ok\":true"));
    check("and it left the TX not sourcing",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u);
    memset(&rb, 0, sizeof rb);
    check("and it did not reset the configuration the stop ran under",
          rf_core_get_readback(core, &rb) == RF_ERR_OK &&
          rb.lo_hz == 90000000ull && rb.attenuation_mdB == 40000u &&
          rb.sample_rate_hz == 60000000u);

    /* The same configuration with the TX live and the client gone: this is the path
     * that reaches the full safe state (ceiling attenuation included). */
    rf_iq_bram_to_wire(words, 4, wire, sizeof wire);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
    req(&s, 3, "tx.load",
        "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
        "\"direction\":\"tx\",\"wrap\":true}");
    feed_bytes(&s, wire, sizeof wire);
    req(&s, 4, "tx.start", "{}");
    check("the TX can be made live from that configuration",
          serve(d, &s) == 0 && contains(&s, "\"id\":4") && contains(&s, "\"ok\":true"));
    check("the safe state: TX not sourcing and RX not armed",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u &&
          (fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG)) & RF_CAP_ARM) == 0u);
    memset(&rb, 0, sizeof rb);
    check("and a TX that was live ended at the vendor ceiling, read back",
          rf_core_get_readback(core, &rb) == RF_ERR_OK &&
          rb.attenuation_mdB == RF_SAFE_ATTENUATION_MDB &&
          rb.lo_hz == 90000000ull);

    /* Reachable again, not just once: the configuration this ran under is still usable.
     * serve() returns 1 here because the script ends with protocol.close -- a clean end
     * the client asked for, not a framing failure. */
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
    req(&s, 3, "protocol.close", "{}");
    check("the configuration is reachable again after a safe stop",
          serve(d, &s) == 1 && contains(&s, "\"ok\":true") &&
          contains(&s, "\"attenuation_mdB\":40000"));

    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

/* Phase 2 Task 7 Step 1: the control path's failure taxonomy, driven end to end.
 *
 * The plan requires of every failure mode: a code, a criterion that REACHES it, and a
 * declared state afterwards.  Several modes already had a daemon-level criterion before
 * this round and are deliberately not duplicated here -- BUSY (the single-lease
 * criterion), UNSUPPORTED ("operation not enabled in this phase", driven from a
 * STATUS_ONLY daemon in test_control_arm_gate) and daemon-restart-mid-operation
 * (test_disarm_on_exit_and_restart).  What had NO daemon-level criterion at all are the
 * three below: TIMEOUT, a bus/aperture error, and -- the case Step 1 singles out -- a
 * PARTIAL WRITE.
 *
 * Why the partial-write criterion is shaped the way it is: Step 1 says it must fail on
 * an implementation that leaves a half-applied configuration and does not say so.
 * Asserting only "ok:false" cannot do that, so this criterion FIRST proves the half
 * write really landed (the PL register moves to the requested tap) and only then demands
 * that the daemon call it a failure and declare FAULT.  An implementation that swallowed
 * the failed verify read and answered ok:true would fail the second check, while the
 * first would show there was something real to report -- which is what makes the red
 * reachable rather than assumed.
 *
 * Two drafts were DROPPED from this function because a mutation could not redden them:
 * "the refusal left the session untouched" and "it was refused before the part was
 * touched".  The disconnect rule runs at the end of every CONTROL_TEST connection and
 * normalises the session, so dirtying it earlier changed nothing observable -- a
 * criterion that cannot fail is exactly what the plan forbids, so they are gone rather
 * than kept for their reassuring wording.  What remains of "validation precedes
 * hardware" is the existing test_control_validation_precedes_hardware. */
static void test_control_failure_taxonomy(void)
{
    rf_daemon_t *d = NULL;
    rf_core_t *core;
    script_t s;
    int writes;

    printf("=== control failure taxonomy ===\n");

    /* ------------------------------------------- TIMEOUT.  rf.init cannot prove the
     * DATA_CLK window is fresh, so it must time out rather than proceed on a stale
     * STATUS word -- and it must leave the part in FAULT, not mid-INITIALIZING,
     * because the part was never programmed. */
    core = opened_core();
    if (core) {
        fake_pl_set_status_window_stuck(1);  /* AFTER opened_core(), which resets it */
        d = new_daemon(core);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.init", "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,"
                              "\"attenuation_mdB\":40000}");
        req(&s, 3, "state.get", "{}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("an unprovable DATA_CLK window is TIMEOUT, not a generic hardware error",
              refused_with(&s, 2, "TIMEOUT") &&
              occurrences(&s, "\"ok\":false") == 1);
        check("and the timeout declared FAULT, on both the core and the session",
              rf_core_state(core) == RF_STATE_FAULT &&
              rf_daemon_session(d)->state == RF_STATE_FAULT &&
              contains(&s, "\"state\":\"FAULT\""));
        rf_daemon_destroy(d);
        rf_core_close(core);
    } else {
        check("a part with a stuck status window can still be opened", 0);
    }
    fake_pl_reset();

    /* -------------------------- PARTIAL WRITE / bus error.  The IDELAY write lands and
     * the confirmation read of the very same register comes back unusable, which is
     * exactly a configuration applied in half. */
    core = healthy_core();
    check("a configured part starts for the fault cases", core != NULL);
    if (core) {
        uint32_t tap_before = fake_pl_read(RF_CTRL_A(RF_REG_IDELAY_TAP));

        d = new_daemon(core);
        writes = fake_ctrl_writes;
        /* The register INDEX, not RF_CTRL_A(index): fake_pl_set_read_failure takes the
         * offset inside the aperture.  Passing the absolute address silently armed
         * nothing, which is how the first draft of this criterion passed vacuously. */
        fake_pl_set_read_failure(RF_REG_IDELAY_TAP, 1);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
        req(&s, 3, "state.get", "{}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("a configuration that could not be confirmed is refused, never ok:true",
              refused_with(&s, 2, "HARDWARE_IO") &&
              occurrences(&s, "\"ok\":false") == 1);
        check("and the daemon declares it: the core and the session end in FAULT",
              rf_core_state(core) == RF_STATE_FAULT &&
              rf_daemon_session(d)->state == RF_STATE_FAULT &&
              contains(&s, "\"state\":\"FAULT\""));
        /* Restore the aperture first: while the fault is on, this read would report
         * the injected failure rather than the register. */
        fake_pl_set_read_failure(RF_REG_IDELAY_TAP, 0);
        check("the half-applied write did land on the PL, so the criterion is not vacuous",
              fake_pl_read(RF_CTRL_A(RF_REG_IDELAY_TAP)) == 5u &&
              tap_before != 5u && fake_ctrl_writes > writes);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------------------------------------------------------ device absent: no core. */
    d = new_daemon(NULL);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.init", "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,"
                          "\"attenuation_mdB\":40000}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("with no core attached a control op is INVALID_STATE and names the missing core",
          refused_with(&s, 2, "INVALID_STATE") &&
          contains(&s, "no RF core is attached") &&
          occurrences(&s, "\"ok\":false") == 1);
    rf_daemon_destroy(d);

    /* ------------------------------ invalid parameter: refused before the hardware. */
    core = healthy_core();
    if (core) {
        d = new_daemon(core);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.configure", "{\"attenuation_mdB\":40000}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("an incomplete configuration is INVALID_ARGUMENT, not a hardware error",
              refused_with(&s, 2, "INVALID_ARGUMENT") &&
              occurrences(&s, "\"ok\":false") == 1);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }
}

/* The clock these criteria advance by hand.  test_now_ns() above is a FROZEN constant,
 * so a timeout criterion run against the default fixture would never expire and would
 * pass without proving anything -- the trap the plan calls out by name. */
static uint64_t g_watchdog_now_ns;

static uint64_t watchdog_clock(void)
{
    return g_watchdog_now_ns;
}

/* Phase 2 Task 9 Step 2: an armed daemon whose client goes away must disarm after a
 * DECLARED timeout.
 *
 * The property is deliberately NOT "the connection ended": arming is per-process and
 * survives a connection (test_control_arm_gate pins that by arming on one connection and
 * driving a control op from another), so treating the end of a connection as the
 * watchdog would break that contract rather than implement this one.
 *
 * Every criterion here advances the injected clock, for the reason above. */
static void test_armed_client_watchdog(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    script_t s;

    printf("=== armed client watchdog ===\n");

    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.pl_reader = NULL;
    cfg.verify_control_token = test_token_ok;
    cfg.now_ns = watchdog_clock;
    g_watchdog_now_ns = 1000000000ull;

    check("a status-only daemon on an injected clock starts",
          rf_daemon_create(&cfg, &d) == 0);
    if (!d)
        return;

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("the daemon is armed by the accepted token", contains(&s, "\"armed\":true"));

    /* 1) Short of the timeout, silence must NOT disarm.  A watchdog that fires early
     *    cuts off a legitimate client that is thinking, which is the failure mode this
     *    half of the criterion exists to catch. */
    g_watchdog_now_ns += RF_DAEMON_ARMED_IDLE_TIMEOUT_NS - 1ull;
    check("one nanosecond short of the declared timeout, silence does not disarm",
          rf_daemon_watchdog_tick(d, g_watchdog_now_ns) == 0);

    /* 2) The deadline is measured from the last successful control request, not from the
     *    arming: a client renewing control is not a client that went away.  The
     *    talking happens through the real serve loop, so the tick inside it is
     *    exercised too -- and at one nanosecond short it must leave arming alone. */
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    check("a client that is still talking is still armed",
          contains(&s, "\"armed\":true"));
    g_watchdog_now_ns += RF_DAEMON_ARMED_IDLE_TIMEOUT_NS - 1ull;
    check("the deadline moved with the client's last request",
          rf_daemon_watchdog_tick(d, g_watchdog_now_ns) == 0);

    /* 3) At the deadline, with nothing said since, it disarms -- once.  The 1 is the
     *    same value rf_daemon_disarm returns when it changed the state, and its own
     *    contract is that changing the state is what writes the audit line. */
    g_watchdog_now_ns += 1ull;
    check("at the declared timeout the watchdog disarms",
          rf_daemon_watchdog_tick(d, g_watchdog_now_ns) == 1);
    check("a second tick is a no-op, not a second state change",
          rf_daemon_watchdog_tick(d, g_watchdog_now_ns) == 0);

    /* 4) The daemon reports it, and control is refused again. */
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "device.info", "{}");
    req(&s, 3, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("the disarmed daemon says so and refuses control again",
          contains(&s, "\"armed\":false") &&
          contains(&s, "operation disabled in status-only mode") &&
          contains(&s, "\"control_ops\":[\"control.arm\"]"));

    rf_daemon_destroy(d);
}

/* Phase 2 Task 8 Step 2, the offline half: "no operation is left pending".
 *
 * `state.get` has carried `pending`/`pending_op` all along, but only ONE criterion ever
 * read them -- a refusal that happened before any hardware was touched (F-T7a).  The
 * gap matters because a stranded `pending` is not cosmetic: rf_state_begin() refuses the
 * NEXT operation with BUSY, so a single abandoned transaction bricks the daemon until it
 * is restarted.  The three ways a transaction can end are covered here: it SUCCEEDED, it
 * FAILED against the hardware, and the client DISAPPEARED mid-flight.
 *
 * The last one is where a leak would be easiest to miss: the disconnect rule abandons
 * whatever was in flight by driving rf_state_safe_stop -- which sets pending_op to STOP
 * itself -- and it is the only path that must finish an operation NOBODY is waiting for.
 *
 * This is the criterion the plan asks for after "stop plus a PL reload"; the reload half
 * is the restarted daemon in test_disarm_on_exit_and_restart, which now asserts the same
 * two fields. */
static void test_no_operation_is_left_pending(void)
{
    rf_daemon_t *d;
    rf_core_t *core;
    script_t s;

    printf("=== no operation is left pending ===\n");

    /* (a) A transaction that SUCCEEDED must clear it. */
    core = healthy_core();
    check("a configured part starts for the pending criteria", core != NULL);
    if (core) {
        d = new_daemon(core);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
        req(&s, 3, "state.get", "{}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("after a successful configure nothing is left pending",
              contains(&s, "\"ok\":true") &&
              contains(&s, "\"pending\":false") &&
              contains(&s, "\"pending_op\":null") &&
              !rf_daemon_session(d)->pending);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* (b) A transaction that FAILED against the hardware is abandoned: the core
     *     faulted, so the session lands in FAULT -- but it must NOT still be in
     *     flight, or the next request would be refused with BUSY instead of being
     *     judged against the (faulted) part. */
    core = healthy_core();
    if (core) {
        d = new_daemon(core);
        check("the vendor readback can be made to fail",
              rf_core_host_set_vendor_readback_fault(core, 1) == RF_ERR_OK);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
        req(&s, 3, "state.get", "{}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("a hardware-failed op is abandoned, not left in flight",
              contains(&s, "\"code\":\"HARDWARE_IO\"") &&
              contains(&s, "\"state\":\"FAULT\"") &&
              contains(&s, "\"pending\":false") &&
              contains(&s, "\"pending_op\":null") &&
              !rf_daemon_session(d)->pending);
        (void)rf_core_host_set_vendor_readback_fault(core, 0);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* (c) The client DISAPPEARED mid-transaction.  A continuous capture leaves the
     *     CORE capturing (rf_core.c has no stop_rx, so the daemon cannot bring it
     *     back); the disconnect rule therefore ends the session in FAULT.  What must
     *     not also happen is the abandoned operation staying in flight: the next
     *     client has to be able to ASK, even if it will not like the answer. */
    core = healthy_core();
    if (core) {
        d = new_daemon(core);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rx.capture",
            "{\"ratio\":1,\"samples\":4,\"continuous\":true}");
        serve(d, &s);                    /* the connection ends mid-capture */
        check("the core is still capturing after its client vanished",
              rf_core_state(core) == RF_STATE_RX_CAPTURING);
        check("and the abandoned transaction did not leave the session pending",
              !rf_daemon_session(d)->pending);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("so the next client is answered with an explicit empty pending",
              contains(&s, "\"id\":2") &&
              contains(&s, "\"pending\":false") &&
              contains(&s, "\"pending_op\":null"));
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }
}

/* ------------------------------------------- production control wiring (P1/P2) */

/* The fake write-capable hardware source.  It counts the calls and records what it
 * saw at release time, so both "was the O_RDWR path opened at all?" and "in what
 * order?" are observable without a board. */
static int probe_acquire_calls;
static int probe_release_calls;
static int probe_acquire_fail;
static rf_core_t *probe_handed_out;
static rf_core_t *probe_released;
static int probe_writes_at_release;

static int probe_acquire(void *ctx, rf_core_t **out)
{
    (void)ctx;
    ++probe_acquire_calls;
    if (probe_acquire_fail || !probe_handed_out)
        return RF_ERR_HARDWARE_IO;          /* the RF_ERR_* contract, not a bare -1 */
    *out = probe_handed_out;
    return RF_ERR_OK;
}

static void probe_release(void *ctx, rf_core_t *core)
{
    (void)ctx;
    ++probe_release_calls;
    probe_released = core;
    /* Ordering evidence: the register-write count AT THE MOMENT the write-capable
     * hardware was handed back.  If release happened before the stop, this equals
     * the count taken before the disarm. */
    probe_writes_at_release = fake_ctrl_writes;
}

static void probe_reset(rf_core_t *core)
{
    probe_acquire_calls = 0;
    probe_release_calls = 0;
    probe_acquire_fail = 0;
    probe_handed_out = core;
    probe_released = NULL;
    probe_writes_at_release = -1;
}

/* A production-shaped daemon (STATUS_ONLY, token verifier) whose write-capable
 * hardware is the probe above. */
static rf_daemon_t *new_wired_daemon(rf_daemon_config_t *cfg)
{
    rf_daemon_t *d = NULL;

    fill_identity(cfg);
    cfg->mode = RF_DAEMON_STATUS_ONLY;
    cfg->pl_reader = NULL;
    cfg->verify_control_token = test_token_ok;
    cfg->control_hw.ctx = NULL;
    cfg->control_hw.acquire = probe_acquire;
    cfg->control_hw.release = probe_release;
    if (rf_daemon_create(cfg, &d) != 0)
        return NULL;
    return d;
}

/* Phase 2 production wiring: the daemon opens the O_RDWR aperture only after a token
 * the verifier accepted, and hands it back on disarm -- stopping the part FIRST,
 * because rf_core_close() deliberately does not stop anything.
 *
 * Every criterion here is about the WRITE path.  The read-only/status path has its
 * own criteria (test_status_only_contract.py, test_separate_pl_reader_is_read_only_at_os_boundary)
 * and is not restated. */
static void test_production_control_wiring(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    rf_core_t *core;
    script_t s;
    int writes;

    printf("=== production control wiring ===\n");

    /* (1) Unarmed: never acquire, and never write a PL register. */
    probe_reset(NULL);
    d = new_wired_daemon(&cfg);
    check("a wired status-only daemon starts", d != NULL);
    if (d) {
        writes = fake_mmio_writes;
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("unarmed: the write-capable hardware is never acquired",
              probe_acquire_calls == 0);
        check("unarmed: a control op is refused before any PL register is written",
              fake_mmio_writes == writes &&
              contains(&s, "operation disabled in status-only mode"));
        rf_daemon_destroy(d);
    }

    /* (2) An accepted token acquires exactly once, and the armed daemon can drive. */
    core = opened_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "rf.init",
            "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,\"attenuation_mdB\":40000}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("arming acquired the write-capable hardware exactly once",
              probe_acquire_calls == 1);
        check("and the armed daemon really drove the part",
              contains(&s, "\"ok\":true") && rf_core_state(core) == RF_STATE_READY);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (3) A rejected token must not reach the hardware at all. */
    core = opened_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"wrong\"}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("a rejected token never reaches the write-capable hardware",
              probe_acquire_calls == 0 && contains(&s, "control token rejected"));
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (4) An arm that cannot open the aperture is a refusal, not a success, and
     *     leaves nothing acquired. */
    core = opened_core();
    probe_reset(core);
    probe_acquire_fail = 1;
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "rf.init", "{\"attenuation_mdB\":40000}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("an arm that cannot open the hardware is refused, not reported as success",
              probe_acquire_calls == 1 &&
              contains(&s, "\"code\":\"HARDWARE_IO\"") &&
              !contains(&s, "\"armed\":true"));
        check("and the failed arm released nothing and stayed unarmed",
              probe_release_calls == 0 &&
              contains(&s, "operation disabled in status-only mode"));
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (5) Arming twice does not acquire twice. */
    core = opened_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("a second arm does not acquire a second core",
              probe_acquire_calls == 1 && occurrences(&s, "\"already\":true") == 1);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (6) Disarm hands it back exactly once, and the daemon loses the capability. */
    core = opened_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "control.disarm", "{}");
        req(&s, 4, "rf.init", "{\"attenuation_mdB\":40000}");
        req(&s, 5, "protocol.close", "{}");
        serve(d, &s);
        check("disarm handed the write-capable hardware back exactly once",
              probe_release_calls == 1 && probe_released == core);
        check("and the daemon can no longer drive the part",
              contains(&s, "operation disabled in status-only mode"));
        check("and releasing an already-released core is a no-op",
              rf_daemon_disarm(d, "test again") == 0 &&
              probe_release_calls == 1);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (7) ORDER: the part is brought to rest while the aperture is still open.  A
     *     failing read makes the stop path attempt a register write, and the release
     *     hook records the write count it saw. */
    core = healthy_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("armed for the ordering criterion", probe_acquire_calls == 1);
        /* Make the part LIVE so the stop path has a register to clear.  A CONTINUOUS
         * capture is required: a one-shot capture auto-completes on the fake PL, so
         * rx_busy becomes false and do_stop has nothing to write. */
        check("the part can be made live for the ordering criterion",
              rf_core_prepare_rx(core, 1, 4, true) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK);
        writes = fake_ctrl_writes;
        check("disarm stops the part BEFORE releasing the write-capable hardware",
              rf_daemon_disarm(d, "ordering") == 1 &&
              probe_release_calls == 1 && probe_writes_at_release > writes);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }

    /* (8) The disconnect rule is no longer harness-only: a production daemon that
     *     owns a core must stop what is live when a client disappears. */
    core = healthy_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("the part can be made live for the disconnect rule",
              rf_core_prepare_rx(core, 1, 4, true) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK);
        writes = fake_ctrl_writes;
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "protocol.close", "{}");
        serve(d, &s);
        check("the disconnect rule also runs for a status-only daemon that owns a core",
              fake_ctrl_writes > writes);
        rf_daemon_disarm(d, "test");
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }
}

/* Phase 2 Task 12 Step 5a regression -- found on the board, delivery record §32.
 *
 * A client that disarms inside its own connection must not leave the daemon poisoned
 * when that connection then ends.  The defect: apply_disconnect_rule() arms a pending
 * STOP through rf_state_safe_stop() BEFORE it knows whether it can finish one, and the
 * old order then bailed out at `if (!d->core) return 0;`.  At connection end the core
 * has already been released by the client's own disarm, so `pending` stayed set forever
 * and every later operation answered BUSY ("an operation is already in flight").
 *
 * Reachable only after the production wiring made disarm release the core AND made the
 * rule unconditional; the §25 criteria missed it because they exercise the rule with the
 * core still attached, never the connection-end-after-disarm path. */
static void test_disarm_then_connection_end_leaves_no_pending(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    rf_core_t *core;
    script_t s;

    printf("=== disarm then connection end leaves no pending ===\n");

    core = healthy_core();                 /* READY, so the session is adopted as READY */
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("a wired daemon over a READY part starts", d != NULL && core != NULL);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "control.disarm", "{}");
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("the client disarmed inside its own connection",
              contains(&s, "\"armed\":false") && probe_release_calls == 1);

        /* A NEW connection: the daemon must not be stuck mid-transaction. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("the next connection sees no operation left in flight",
              contains(&s, "\"pending\":false") && contains(&s, "\"pending_op\":null"));
        /* CRITERION UPDATE (Bug B fix, §34): handing the part back now deliberately
         * puts the session in OFFLINE -- the daemon has no aperture, so claiming READY
         * is what made the next arm unusable.  This check used to pin READY. */
        check("and releasing the part left the session in the declared OFFLINE state",
              contains(&s, "\"state\":\"OFFLINE\""));

        /* ... and it is usable again: re-arm, then an op that needs the part. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "rf.configure", NON_DEFAULT_CONFIG);
        req(&s, 4, "protocol.close", "{}");
        serve(d, &s);
        check("re-arming works and the next operation is not refused as BUSY",
              contains(&s, "\"armed\":true") && !contains(&s, "BUSY"));
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }
}

/* Phase 2 Task 12 Step 5a, second defect (Bug B) -- found on the board, record §34.
 *
 * After a `control.disarm` the daemon gives the core back.  The NEXT arm acquires a
 * FRESH core, which is OFFLINE as far as the core knows, while the session still said
 * READY -- and adopt_core_state() only reconciles when the session is OFFLINE.  The
 * result was a daemon that could not init ("READY"), could not stop ("not
 * initialized") and could not configure (the core is not READY) until it restarted.
 *
 * This criterion re-arms after a full session and insists the second rf.init is
 * accepted.  It hands out a genuinely fresh core for the second arm, exactly as
 * board_acquire_control_core() does. */
static void test_rearm_after_disarm_is_usable_again(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    rf_core_t *c1, *c2;
    script_t s;

    printf("=== re-arm after disarm is usable again ===\n");

    c1 = opened_core();
    probe_reset(c1);
    d = new_wired_daemon(&cfg);
    check("a wired daemon starts for the re-arm criterion", d != NULL && c1 != NULL);
    if (!d || !c1) {
        if (d)
            rf_daemon_destroy(d);
        if (c1) { rf_core_close(c1); rf_core_destroy(c1); }
        return;
    }

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "rf.init",
        "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,\"attenuation_mdB\":40000}");
    req(&s, 4, "control.disarm", "{}");
    req(&s, 5, "protocol.close", "{}");
    serve(d, &s);
    check("the first session initialized the part and then disarmed",
          contains(&s, "\"ok\":true") && contains(&s, "\"armed\":false") &&
          probe_release_calls == 1);

    /* A genuinely fresh core, exactly what the second arm acquires on the board. */
    rf_core_close(c1);
    rf_core_destroy(c1);
    c2 = opened_core();
    probe_handed_out = c2;
    check("a fresh (OFFLINE) core was opened for the second arm", c2 != NULL);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "rf.init",
        "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,\"attenuation_mdB\":40000}");
    req(&s, 4, "protocol.close", "{}");
    serve(d, &s);
    check("the second arm can initialize the part again (no stale READY)",
          contains(&s, "\"armed\":true") && contains(&s, "\"ok\":true") &&
          !contains(&s, "INVALID_STATE"));

    rf_daemon_destroy(d);
    if (c2) { rf_core_close(c2); rf_core_destroy(c2); }
}


/* Task 12 Step 4: the board observed a tap write before the vendor rejected
 * attenuation 90000. Use the production acquire path, not attach_core in
 * STATUS_ONLY (which is a no-op), and subtract the connection-end stop cost. */
static void test_configure_range_with_owned_core(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    script_t s;
    int base, rejected;
    uint32_t tap;

    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("range test has an owned initialized core", d != NULL && core != NULL);
    if (d && core) {
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("range test really acquired the core", probe_acquire_calls == 1);
        tap = rf_reg_read(RF_REG_IDELAY_TAP);
        base = connection_ctrl_cost(d, &s, NULL);
        rejected = connection_ctrl_cost(d, &s,
            "{\"lo_hz\":100000000,\"sample_rate_hz\":60000000,"
            "\"rx_bandwidth_hz\":30000000,\"tx_bandwidth_hz\":30000000,"
            "\"attenuation_mdB\":90000,\"phy_mode_1r1t\":1,"
            "\"idelay_en\":1,\"idelay_tap\":11}");
        check("owned-core configure rejects excessive attenuation as INVALID_ARGUMENT",
              refused_with(&s, 2, "INVALID_ARGUMENT"));
        check("rejected configure programs zero core registers",
              rejected == base && rf_reg_read(RF_REG_IDELAY_TAP) == tap);
        check("rejected configure preserves READY without pending",
              rf_daemon_session(d)->state == RF_STATE_READY &&
              !rf_daemon_session(d)->pending);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }
}


static void test_idelay_configure_protocol(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    script_t s;
    int spi_before;

    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("idelay protocol fixture has owned initialized core", d != NULL && core != NULL);
    if (d && core) {
        fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
        fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "idelay.configure", "{\"mask\":2,\"tap\":25}");
        req(&s, 4, "idelay.configure", "{\"mask\":128,\"tap\":25}");
        req(&s, 5, "idelay.configure", "{\"mask\":2,\"tap\":32}");
        req(&s, 6, "protocol.close", "{}");
        spi_before = fake_spi_writes;
        check("idelay.configure is accepted and reports PL config readback",
              serve(d, &s) == 1 &&
              contains(&s, "\"id\":3,\"ok\":true") &&
              contains(&s, "\"idelay_en\":2,\"idelay_tap\":25") &&
              contains(&s, "pl_config_register_not_cntvalueout") &&
              refused_with(&s, 4, "INVALID_ARGUMENT") &&
              refused_with(&s, 5, "INVALID_ARGUMENT") &&
              fake_spi_writes == spi_before);
        check("idelay.configure final PL state is lane1/tap25",
              rf_reg_read(RF_REG_IDELAY_EN) == 2u &&
              rf_reg_read(RF_REG_IDELAY_TAP) == 25u);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }
}

/* Counters taken at a READ boundary INSIDE a live connection: every request in the
 * script has been handled and the disconnect rule has NOT run yet.  This is what
 * isolates one request's writes from the connection's own tidy-up; checking after
 * serve() returns cannot tell the two apart. */
static int idelay_snap_mmio, idelay_snap_ctrl, idelay_snap_spi, idelay_snap_rejected;

static void idelay_snapshot(void *ctx)
{
    (void)ctx;
    idelay_snap_mmio = fake_mmio_writes;
    idelay_snap_ctrl = fake_ctrl_writes;
    idelay_snap_spi = fake_spi_writes;
    idelay_snap_rejected = fake_spi_rejected;
}

/* Serve one whole connection in a single transport read and snapshot at the next
 * READ boundary (EOF), which is after the last request was answered and before the
 * disconnect rule.  The plan is what makes the boundary exact.  The measured
 * scripts must NOT end with protocol.close: that op ends run_loop without another
 * read, so the boundary would never be reached; EOF is the clean end here. */
static void serve_to_read_boundary(rf_daemon_t *d, script_t *s)
{
    timing_read_offset = s->in_len;
    s->plan[0] = (int)s->in_len;
    s->plan_n = 1;
    idelay_snap_mmio = idelay_snap_ctrl = idelay_snap_spi = idelay_snap_rejected = -1;
    timing_read_ctx = NULL;
    timing_before_read = idelay_snapshot;
    (void)serve(d, s);
    timing_before_read = NULL;
}

/* The unarmed gate and the protocol-boundary argument gates.  Every one of these is
 * a COUNTEREXAMPLE request: the assertion is that it is refused with the operation's
 * own argument or state error, and that the refusal performs zero PL writes. */
static void test_rx_data_delay_protocol(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    script_t s;
    int spi_before;

    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("rx data delay protocol fixture has owned initialized core", d != NULL && core != NULL);
    if (d && core) {
        fake_reg[0x006u] = 0x34u;
        fake_reg[0x007u] = 0x70u;
        fake_reg[0x010u] = 0x50u;
        fake_reg[0x011u] = 0x0Cu;
        fake_reg[0x012u] = 0x10u;
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "rf.interface_status", "{}");
        req(&s, 4, "rf.rx_data_delay.set", "{\"value\":5}");
        req(&s, 5, "rf.rx_data_delay.set", "{\"value\":16}");
        req(&s, 6, "protocol.close", "{}");
        spi_before = fake_spi_writes;
        check("fixed interface read and restricted RX delay set are supported",
              serve(d, &s) == 1 &&
              contains(&s, "\"id\":3,\"ok\":true") &&
              contains(&s, "\"reg_006\":52") &&
              contains(&s, "\"rx_data_delay\":4") &&
              contains(&s, "\"id\":4,\"ok\":true") &&
              contains(&s, "\"reg_006\":53") &&
              contains(&s, "\"other_registers_unchanged\":true") &&
              refused_with(&s, 5, "INVALID_ARGUMENT") &&
              fake_spi_writes == spi_before + 1);
        check("restricted RX delay changed only 0x006 low nibble",
              fake_reg[0x006u] == 0x35u && fake_reg[0x007u] == 0x70u &&
              fake_reg[0x010u] == 0x50u && fake_reg[0x011u] == 0x0Cu &&
              fake_reg[0x012u] == 0x10u);
        rf_daemon_destroy(d);
    }
    if (core) { rf_core_close(core); rf_core_destroy(core); }
}

static void test_idelay_configure_gates(void)
{
    rf_core_t *core;
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    script_t s;
    int start_mmio, start_ctrl, start_spi, start_rejected;

    printf("=== idelay.configure gates ===\n");

    /* ---------------------------------------- unarmed: the operation is not reachable */
    core = healthy_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("the unarmed idelay fixture has a READY part and a status-only daemon",
          d != NULL && core != NULL);
    if (!d || !core) {
        if (d)
            rf_daemon_destroy(d);
        if (core) {
            rf_core_close(core);
            rf_core_destroy(core);
        }
        return;
    }

    start_mmio = fake_mmio_writes;
    start_ctrl = fake_ctrl_writes;
    start_spi = fake_spi_writes;
    start_rejected = fake_spi_rejected;
    fake_ctrl_log_count = 0;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "idelay.configure", "{\"mask\":2,\"tap\":25}");
    serve_to_read_boundary(d, &s);
    check("an unarmed daemon refuses idelay.configure as disabled, not as a bad argument",
          refused_with(&s, 2, "UNSUPPORTED") &&
          contains(&s, "operation disabled in status-only mode") &&
          !contains(&s, "mask must be 1..127 and tap 0..31"));
    check("the unarmed refusal opens no write aperture and performs zero PL writes",
          probe_acquire_calls == 0 &&
          idelay_snap_mmio - start_mmio == 0 &&
          idelay_snap_ctrl - start_ctrl == 0 &&
          idelay_snap_spi - start_spi == 0 &&
          idelay_snap_rejected - start_rejected == 0 &&
          fake_ctrl_log_count == 0);

    /* Arm once; the daemon stays armed between connections, so the argument-gate
     * requests below reach the handler and are refused by the handler itself. */
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    (void)serve(d, &s);
    check("the argument-gate fixture armed the daemon and acquired the part",
          probe_acquire_calls == 1 && contains(&s, "\"armed\":true") &&
          rf_core_state(core) == RF_STATE_READY);

    /* ------------------------- missing, negative, out-of-range, boolean, float types */
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
    start_mmio = fake_mmio_writes;
    start_ctrl = fake_ctrl_writes;
    start_spi = fake_spi_writes;
    start_rejected = fake_spi_rejected;
    fake_ctrl_log_count = 0;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "idelay.configure", "{\"tap\":25}");                   /* mask missing */
    req(&s, 3, "idelay.configure", "{\"mask\":2}");                   /* tap missing */
    req(&s, 4, "idelay.configure", "{\"mask\":-1,\"tap\":25}");       /* negative mask */
    req(&s, 5, "idelay.configure", "{\"mask\":2,\"tap\":-1}");        /* negative tap */
    req(&s, 6, "idelay.configure", "{\"mask\":0,\"tap\":25}");        /* mask out of range */
    req(&s, 7, "idelay.configure", "{\"mask\":128,\"tap\":25}");      /* mask out of range */
    req(&s, 8, "idelay.configure", "{\"mask\":2,\"tap\":32}");        /* tap out of range */
    req(&s, 9, "idelay.configure", "{\"mask\":true,\"tap\":25}");     /* JSON boolean */
    req(&s, 10, "idelay.configure", "{\"mask\":2,\"tap\":false}");    /* JSON boolean */
    req(&s, 11, "idelay.configure", "{\"mask\":2.0,\"tap\":25}");     /* JSON float */
    req(&s, 12, "idelay.configure", "{\"mask\":2,\"tap\":25.5}");     /* JSON float */
    serve_to_read_boundary(d, &s);
    check("a request without mask is INVALID_ARGUMENT naming the argument",
          refused_with(&s, 2, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'mask' is missing"));
    check("a request without tap is INVALID_ARGUMENT naming the argument",
          refused_with(&s, 3, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'tap' is missing"));
    check("a negative mask is INVALID_ARGUMENT naming the argument",
          refused_with(&s, 4, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'mask' is missing"));
    check("a negative tap is INVALID_ARGUMENT naming the argument",
          refused_with(&s, 5, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'tap' is missing"));
    check("mask 0, mask 128 and tap 32 are INVALID_ARGUMENT for the documented domain",
          refused_with(&s, 6, "INVALID_ARGUMENT") &&
          refused_with(&s, 7, "INVALID_ARGUMENT") &&
          refused_with(&s, 8, "INVALID_ARGUMENT") &&
          contains(&s, "mask must be 1..127 and tap 0..31"));
    check("a JSON boolean mask or tap is INVALID_ARGUMENT, never coerced to 1/0",
          refused_with(&s, 9, "INVALID_ARGUMENT") &&
          refused_with(&s, 10, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'mask' is missing") &&
          contains(&s, "argument 'tap' is missing"));
    check("a JSON float mask or tap is refused as a malformed request before dispatch",
          occurrences(&s, "the line is not a well-formed request") == 2 &&
          occurrences(&s, "{\"ok\":false,\"error\":") == 2);
    check("every one of the eleven counterexample requests was refused and none succeeded",
          occurrences(&s, "\"ok\":false") == 11 &&
          occurrences(&s, "\"ok\":true") == 1);
    check("the nine handler-level counterexamples are id-keyed INVALID_ARGUMENT refusals",
          refused_with(&s, 2, "INVALID_ARGUMENT") &&
          refused_with(&s, 3, "INVALID_ARGUMENT") &&
          refused_with(&s, 4, "INVALID_ARGUMENT") &&
          refused_with(&s, 5, "INVALID_ARGUMENT") &&
          refused_with(&s, 6, "INVALID_ARGUMENT") &&
          refused_with(&s, 7, "INVALID_ARGUMENT") &&
          refused_with(&s, 8, "INVALID_ARGUMENT") &&
          refused_with(&s, 9, "INVALID_ARGUMENT") &&
          refused_with(&s, 10, "INVALID_ARGUMENT"));
    check("the argument refusals perform zero PL writes and leave the part untouched",
          idelay_snap_mmio - start_mmio == 0 &&
          idelay_snap_ctrl - start_ctrl == 0 &&
          idelay_snap_spi - start_spi == 0 &&
          idelay_snap_rejected - start_rejected == 0 &&
          fake_ctrl_log_count == 0 &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x7Fu &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);

    rf_daemon_destroy(d);
    rf_core_close(core);
    rf_core_destroy(core);
}

/* The accepted lane1/tap25 path, with the request's writes measured against an
 * otherwise identical connection.  fake_mmio_writes counts EVERY PL write (the SPI
 * master's own registers and the BRAM ports included); "the request adds exactly
 * three MMIO writes and they are all axi_ctrl_0 writes" therefore excludes an
 * AD9361 transaction, a BRAM write and any restore write -- not merely a change in
 * the accepted AD9361 write count. */
static void test_idelay_configure_success_write_witness(void)
{
    rf_core_t *core;
    rf_daemon_config_t cfg;
    rf_daemon_t *d;
    script_t s;
    int start_mmio, start_ctrl, start_spi, start_rejected;
    int base_mmio, base_ctrl, base_spi, base_rejected;
    int op_mmio, op_ctrl, op_spi, op_rejected;

    printf("=== idelay.configure success and write witness ===\n");

    core = healthy_core();
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("the idelay success fixture has a READY part and a status-only daemon",
          d != NULL && core != NULL);
    if (!d || !core) {
        if (d)
            rf_daemon_destroy(d);
        if (core) {
            rf_core_close(core);
            rf_core_destroy(core);
        }
        return;
    }

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    (void)serve(d, &s);
    check("the success fixture armed the daemon and acquired the READY part",
          probe_acquire_calls == 1 && contains(&s, "\"armed\":true") &&
          rf_core_state(core) == RF_STATE_READY);

    /* Baseline: the same connection shape without the request under test. */
    start_mmio = fake_mmio_writes;
    start_ctrl = fake_ctrl_writes;
    start_spi = fake_spi_writes;
    start_rejected = fake_spi_rejected;
    script_init(&s);
    hello(&s, 1);
    serve_to_read_boundary(d, &s);
    check("the baseline connection was snapshotted inside the connection",
          idelay_snap_mmio >= 0 && idelay_snap_ctrl >= 0);
    base_mmio = idelay_snap_mmio - start_mmio;
    base_ctrl = idelay_snap_ctrl - start_ctrl;
    base_spi = idelay_snap_spi - start_spi;
    base_rejected = idelay_snap_rejected - start_rejected;

    /* Pretend the ordinary configure left every lane at tap 29. */
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
    fake_ctrl_log_count = 0;
    start_mmio = fake_mmio_writes;
    start_ctrl = fake_ctrl_writes;
    start_spi = fake_spi_writes;
    start_rejected = fake_spi_rejected;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "idelay.configure", "{\"mask\":2,\"tap\":25}");
    serve_to_read_boundary(d, &s);
    op_mmio = idelay_snap_mmio - start_mmio;
    op_ctrl = idelay_snap_ctrl - start_ctrl;
    op_spi = idelay_snap_spi - start_spi;
    op_rejected = idelay_snap_rejected - start_rejected;

    check("idelay.configure lane1/tap25 is accepted and reports the PL config readback",
          contains(&s, "\"id\":2,\"ok\":true") &&
          contains(&s, "\"idelay_en\":2,\"idelay_tap\":25") &&
          contains(&s, "\"readback\":{\"idelay_en\":2,\"idelay_tap\":25}") &&
          contains(&s, "pl_config_register_not_cntvalueout"));
    check("the accepted request adds exactly the three documented PL writes",
          op_mmio - base_mmio == 3 && op_ctrl - base_ctrl == 3 &&
          fake_ctrl_log_count == 3 &&
          fake_ctrl_log_addr[0] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[0] == 0u &&
          fake_ctrl_log_addr[1] == RF_CTRL_A(RF_REG_IDELAY_TAP) &&
          fake_ctrl_log_value[1] == 25u &&
          fake_ctrl_log_addr[2] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[2] == 0x02u);
    check("the accepted request adds no SPI-master, AD9361 or BRAM write",
          op_mmio == op_ctrl &&
          op_spi - base_spi == 0 && op_rejected - base_rejected == 0);
    check("the accepted request leaves the PL holding lane1/tap25 and the core READY",
          rf_reg_read(RF_REG_IDELAY_EN) == 0x02u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 25u &&
          rf_core_state(core) == RF_STATE_READY);

    rf_daemon_destroy(d);
    rf_core_close(core);
    rf_core_destroy(core);
}

/* The OLD `rf.configure` contract, unchanged by the additive mask API: `idelay_en`
 * stays a boolean flag (1 -> all seven lanes 0x7F, 0 -> none) and a value outside
 * {0,1} is still refused -- it was not turned into a mask. */
static void test_legacy_rf_configure_idelay_flag(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_t *d = core ? new_daemon(core) : NULL;
    script_t s;

    printf("=== legacy rf.configure idelay_en flag ===\n");
    check("the legacy idelay fixture is a READY part behind a daemon", d != NULL && core != NULL);
    if (!d || !core) {
        if (d)
            rf_daemon_destroy(d);
        if (core)
            rf_core_destroy(core);
        return;
    }

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure",
        "{\"lo_hz\":90000000,\"sample_rate_hz\":60000000,"
        "\"rx_bandwidth_hz\":30000000,\"tx_bandwidth_hz\":30000000,"
        "\"attenuation_mdB\":40000,\"phy_mode_1r1t\":1,"
        "\"idelay_en\":1,\"idelay_tap\":29}");
    req(&s, 3, "protocol.close", "{}");
    (void)serve(d, &s);
    check("rf.configure idelay_en=1 still asserts all seven lanes as 0x7F",
          contains(&s, "\"id\":2,\"ok\":true") &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x7Fu &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure",
        "{\"lo_hz\":90000000,\"sample_rate_hz\":60000000,"
        "\"rx_bandwidth_hz\":30000000,\"tx_bandwidth_hz\":30000000,"
        "\"attenuation_mdB\":40000,\"phy_mode_1r1t\":1,"
        "\"idelay_en\":0,\"idelay_tap\":29}");
    req(&s, 3, "protocol.close", "{}");
    (void)serve(d, &s);
    check("rf.configure idelay_en=0 still closes all seven lanes",
          contains(&s, "\"id\":2,\"ok\":true") &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);

    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rf.configure",
        "{\"lo_hz\":90000000,\"sample_rate_hz\":60000000,"
        "\"rx_bandwidth_hz\":30000000,\"tx_bandwidth_hz\":30000000,"
        "\"attenuation_mdB\":40000,\"phy_mode_1r1t\":1,"
        "\"idelay_en\":2,\"idelay_tap\":29}");
    req(&s, 3, "protocol.close", "{}");
    (void)serve(d, &s);
    check("rf.configure still refuses an idelay_en value outside {0,1}",
          refused_with(&s, 2, "INVALID_ARGUMENT") &&
          contains(&s, "argument 'idelay_en' is missing"));
    check("the refused legacy flag leaves both IDELAY registers unchanged",
          rf_reg_read(RF_REG_IDELAY_EN) == 0u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);

    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

/* A monitoring panel must not keep abandoned control alive. Malformed and
 * refused requests must not renew it either: only successful control counts. */
static void test_watchdog_ignores_noncontrol_traffic(void)
{
    unsigned scenario;
    for (scenario = 0; scenario < 3u; ++scenario) {
        rf_daemon_config_t cfg;
        rf_daemon_t *d = NULL;
        script_t s;
        fill_identity(&cfg);
        cfg.mode = RF_DAEMON_STATUS_ONLY;
        cfg.now_ns = watchdog_clock;
        cfg.verify_control_token = test_token_ok;
        g_watchdog_now_ns = 1000000000ull;
        check("noncontrol watchdog fixture starts", rf_daemon_create(&cfg, &d) == 0);
        if (!d)
            continue;
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        check("noncontrol watchdog fixture really armed", contains(&s, "\"armed\":true"));
        g_watchdog_now_ns += RF_DAEMON_ARMED_IDLE_TIMEOUT_NS - 1ull;
        script_init(&s);
        hello(&s, 1);
        if (scenario == 0)
            req(&s, 2, "device.info", "{}");
        else if (scenario == 1)
            feed_bytes(&s, (const uint8_t *)"malformed\n", 10u);
        else
            req(&s, 2, "rf.init", "{}"); /* refused: no core/arguments */
        req(&s, 3, "protocol.close", "{}");
        serve(d, &s);
        if (scenario == 2)
            check("failed control is actually refused", refused_with(&s, 2, "INVALID_STATE"));
        ++g_watchdog_now_ns;
        check(scenario == 0 ? "read-only polling does not renew control" :
              scenario == 1 ? "malformed traffic does not renew control" :
                              "refused control does not renew control",
              rf_daemon_watchdog_tick(d, g_watchdog_now_ns) == 1);
        rf_daemon_destroy(d);
    }
}


static int watchdog_expiry_read(void *ctx, void *buf, size_t len)
{
    script_t *s = (script_t *)ctx;
    if (s->in_pos > 0)
        g_watchdog_now_ns = 1000000000ull + RF_DAEMON_ARMED_IDLE_TIMEOUT_NS;
    return s_read(ctx, buf, len);
}

static void test_watchdog_checks_after_blocking_read(void)
{
    rf_daemon_config_t cfg;
    rf_daemon_t *d = NULL;
    script_t s;
    fill_identity(&cfg);
    cfg.mode = RF_DAEMON_STATUS_ONLY;
    cfg.now_ns = watchdog_clock;
    cfg.verify_control_token = test_token_ok;
    g_watchdog_now_ns = 1000000000ull;
    check("blocking-read watchdog fixture starts", rf_daemon_create(&cfg, &d) == 0);
    if (!d)
        return;
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
    req(&s, 3, "protocol.close", "{}");
    serve(d, &s);
    script_init(&s);
    hello(&s, 1);
    s.plan[s.plan_n++] = (int)s.in_len; /* hello arrives before expiry */
    req(&s, 2, "rf.init", "{\"attenuation_mdB\":40000}");
    req(&s, 3, "protocol.close", "{}");
    s.t.read = watchdog_expiry_read;   /* control arrives AT the deadline */
    serve(d, &s);
    check("a control received at expiry is gated before its handler",
          refused_with(&s, 2, "UNSUPPORTED") &&
          contains(&s, "operation disabled in status-only mode"));
    rf_daemon_destroy(d);
}

static void test_physical_capture_requires_explicit_safe_wiring(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_t *d = new_daemon(core);
    script_t s;
    int writes = fake_ctrl_writes;
    if (core && d) {
        script_init(&s); hello(&s, 1);
        req(&s, 2, "loopback.physical.capture", "{\"samples\":4,\"ratio\":0}");
        serve(d, &s);
        check("physical capture refuses missing attenuation before touching PL",
              contains(&s, "requires an explicit attenuation_mdB") && fake_ctrl_writes == writes);
        script_init(&s); hello(&s, 1);
        req(&s, 2, "loopback.physical.capture", "{\"samples\":4,\"ratio\":0," LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":false}");
        serve(d, &s);
        check("physical capture refuses unconfirmed cable before touching PL",
              contains(&s, "cable confirmation") && fake_ctrl_writes == writes);
    }
    rf_daemon_destroy(d); rf_core_destroy(core);
}

/* ------------------------- the finite-capture teardown (Task 1) -------------
 *
 * A one-shot capture used to leave RF_CAP_ARM high after DONE.  That made the arm
 * bit unable to say "done": a host could not tell "armed and waiting" from "held a
 * finished capture", and tx.stop only ever cleared it by BUSY.  These drive the
 * REAL request paths -- rx.capture, loopback.digital.capture and
 * loopback.physical.capture -- and read the actual PL registers back, because that
 * is the whole of what a host can observe.
 */
static void test_finite_capture_teardown(void)
{
    uint8_t payload[16];
    script_t s;
    rf_core_t *core;
    rf_daemon_t *d;
    uint32_t cfg = 0, st = 0;
    int k;

    /* Legal s16 IQ pairs: every field must be -2048..2047, so a naive byte ramp
     * would be rejected by tx.load's validator. */
    {
        static const int iq[8] = { 100, -200, 300, -400, 500, -600, 700, -800 };
        for (k = 0; k < 4; ++k) {
            payload[k * 4 + 0] = (uint8_t)(iq[k * 2 + 0] & 0xFF);
            payload[k * 4 + 1] = (uint8_t)((iq[k * 2 + 0] >> 8) & 0xFF);
            payload[k * 4 + 2] = (uint8_t)(iq[k * 2 + 1] & 0xFF);
            payload[k * 4 + 3] = (uint8_t)((iq[k * 2 + 1] >> 8) & 0xFF);
        }
    }

    /* (1) The ordinary finite capture. */
    core = healthy_core();
    d = new_daemon(core);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rx.capture", "{\"ratio\":1,\"samples\":4,\"continuous\":false}");
    check("a finite rx.capture still answers with a complete iq_frame",
          serve(d, &s) == 0 && contains(&s, "\"type\":\"iq_frame\"") &&
          contains(&s, "\"bytes\":16") &&
          contains(&s, "\"sample_rate_hz\":60000000"));
    check("and it leaves CAP_ARM low, so DONE and armed are distinguishable",
          rf_core_get_rx_path(core, &cfg, &st) == RF_ERR_OK &&
          (cfg & RF_CAP_ARM) == 0u);
    check("with the part READY on both the core and the session record",
          rf_core_state(core) == RF_STATE_READY &&
          rf_daemon_session(d)->state == RF_STATE_READY);
    rf_daemon_destroy(d);
    rf_core_destroy(core);

    /* (2) The digital loopback capture: the same teardown. */
    core = healthy_core();
    d = new_daemon(core);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "tx.load",
        "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
        "\"direction\":\"tx\",\"wrap\":false}");
    feed_bytes(&s, payload, sizeof payload);
    req(&s, 3, "loopback.digital.capture", "{\"samples\":4,\"ratio\":0}");
    check("a digital capture still answers with an iq_frame",
          serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
          contains(&s, "\"type\":\"iq_frame\""));
    check("and it leaves CAP_ARM low as well",
          rf_core_get_rx_path(core, &cfg, &st) == RF_ERR_OK &&
          (cfg & RF_CAP_ARM) == 0u);
    rf_daemon_destroy(d);
    rf_core_destroy(core);

    /* (3) The physical loopback capture: the same teardown PLUS the TX source
     *     confirmed off -- the source exists only for this one transaction. */
    core = healthy_core();
    d = new_daemon(core);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "tx.load",
        "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
        "\"direction\":\"tx\",\"wrap\":false}");
    feed_bytes(&s, payload, sizeof payload);
    req(&s, 3, "loopback.physical.capture",
        "{\"samples\":4,\"ratio\":0," LOOPBACK_ATTENUATION_ARG ","
        "\"cable_confirmed\":true}");
    check("a physical capture still answers with an iq_frame",
          serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
          contains(&s, "\"type\":\"iq_frame\""));
    check("and it leaves BOTH CAP_ARM and SRC_SEL released",
          rf_core_get_rx_path(core, &cfg, &st) == RF_ERR_OK &&
          (cfg & RF_CAP_ARM) == 0u &&
          (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) == 0u);
    rf_daemon_destroy(d);
    rf_core_destroy(core);

    /* (4) Negative control: the release was written and did not take.  The
     *     samples are frozen, but the capture is still armed -- so the frame
     *     must NOT be delivered as a success.  Keyed on this request's own id
     *     AND the hardware code, so the criterion cannot be satisfied by a
     *     refusal of something else in the same script.  serve()'s own return is
     *     deliberately not part of it: the connection's end reports the
     *     unconfirmable shutoff separately, and that is a different fact. */
    core = healthy_core();
    d = new_daemon(core);
    fake_pl_set_capture_arm_stuck(1);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "rx.capture", "{\"samples\":4,\"ratio\":0}");
    (void)serve(d, &s);
    check("a capture whose release cannot be confirmed is refused as HARDWARE_IO, "
          "and no valid IQ frame is sent",
          refused_with(&s, 2, "HARDWARE_IO") &&
          !contains(&s, "\"type\":\"iq_frame\""));
    check("and that is a hardware fault on both the core and the session",
          rf_core_state(core) == RF_STATE_FAULT &&
          rf_daemon_session(d)->state == RF_STATE_FAULT);
    rf_daemon_destroy(d);
    rf_core_destroy(core);

    /* (5) Negative control: a physical capture whose TX source will not go low.
     *     The source is hardware-safety relevant, so an unconfirmed clear is a
     *     refusal rather than a warning. */
    core = healthy_core();
    d = new_daemon(core);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "tx.load",
        "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
        "\"direction\":\"tx\",\"wrap\":false}");
    feed_bytes(&s, payload, sizeof payload);
    check("the fixture loads a TX frame before the fault is injected",
          serve(d, &s) == 0);
    /* Injected AFTER the load so it is surgical: stop_active models a reader
     * whose src_sel will not drop. */
    fake_pl_set_stop_active(1);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "loopback.physical.capture",
        "{\"samples\":4,\"ratio\":0," LOOPBACK_ATTENUATION_ARG ","
        "\"cable_confirmed\":true}");
    (void)serve(d, &s);
    check("a physical capture whose TX source cannot be released is refused as "
          "HARDWARE_IO, and no valid IQ frame is sent",
          refused_with(&s, 2, "HARDWARE_IO") &&
          !contains(&s, "\"type\":\"iq_frame\""));
    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

/* Review finding F2 -- DISPOSITION, not an omission.
 *
 * F2 said the `rf_core_state(d->core) == RF_STATE_FAULT` restriction in
 * apply_disconnect_rule was unverified: widening it to `!= RF_STATE_READY`
 * passed the whole suite.  Every attempt to build a discriminating case
 * collapsed the same way.  Measured: a continuous capture whose RX arm is
 * made unclearable does NOT leave a non-faulted core holding the arm -- the
 * disconnect comes back non-zero and the core is NOT left in RX_CAPTURING,
 * because a gated setter that cannot confirm its clear faults the core.
 * The branch is only reached when do_stop left something LIVE, and every way
 * a stop can leave the part live also faults it, so the two predicates
 * coincide on every reachable path: the restriction is UNOBSERVABLE rather
 * than wrong.  The guard is kept as an explicit statement of intent.  If a
 * future change ever makes 'non-FAULT and still live' reachable, this
 * restriction becomes load-bearing AND untested -- that is the residual risk.
 * Evidence: f2-test-green.log (the attempted criterion). */

static void test_faulted_capture_disconnect_without_disarm(void)
{
    rf_core_t *core;
    rf_daemon_t *d;
    script_t s;
    uint32_t word = 0x00100020u, rx = 0;

    printf("=== a faulted core that is never disarmed ===\n");
    core = healthy_core();
    d = new_daemon(core);
    /* A first connection, so the daemon is past its handshake. */
    script_init(&s);
    hello(&s, 1);
    check("the fault fixture opens a connection", serve(d, &s) == 0);
    /* Drive the part into the fault the disconnect rule has to survive.  The
     * capture never completes, so it times out and the CORE goes to FAULT while
     * the registers stay writable -- the case the READY-gated setters cannot
     * reach.  (A source stuck at the register level is a different, unfixable-in-
     * software fault, covered by fake_pl_set_stop_active.) */
    rf_core_write_tx(core, &word, 1);
    rf_core_configure_tx_path(core, 1, true, NULL);
    rf_core_set_tx_source(core, true, NULL);
    rf_core_prepare_rx(core, 0, 1, false);
    rf_core_start_rx(core);
    fake_pl_set_capture_done_stuck(1);
    (void)rf_core_read_rx(core, &rx, 1);
    check("the timed-out capture leaves the part sourcing and the core in FAULT",
          rf_core_state(core) == RF_STATE_FAULT &&
          (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) != 0u);
    /* A connection that merely ENDS -- no control.disarm was ever sent, which is
     * the case the disconnect rule exists for.  Leaving the source selected here
     * means the part keeps sourcing until the 30 s watchdog happens to expire,
     * and that is not a shutoff anybody verified. */
    script_init(&s);
    hello(&s, 1);
    (void)serve(d, &s);
    check("ending the connection brings the faulted part to rest",
          (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) == 0u &&
          (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) == 0u);
    /* ...and it does that WITHOUT erasing the evidence: a shutdown would have set
     * OFFLINE, which two other criteria require to stay FAULT. */
    check("the FAULT is preserved as the diagnostic record",
          rf_core_state(core) == RF_STATE_FAULT);
    fake_pl_set_capture_done_stuck(0);
    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

static void test_faulted_owned_core_is_safe_before_release(void)
{
    rf_core_t *core = healthy_core();
    rf_daemon_config_t cfg = {0};
    rf_daemon_t *d;
    script_t s;
    uint32_t word = 0x00100020u;
    uint32_t rx;
    probe_reset(core);
    d = new_wired_daemon(&cfg);
    check("fault cleanup fixture opens an owned core", core != NULL && d != NULL);
    if (core && d) {
        script_init(&s); hello(&s, 1); req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        serve(d, &s);
        rf_core_write_tx(core, &word, 1);
        rf_core_configure_tx_path(core, 1, true, NULL);
        rf_core_set_tx_source(core, true, NULL);
        rf_core_prepare_rx(core, 0, 1, false);
        rf_core_start_rx(core);
        fake_pl_set_capture_done_stuck(1);
        check("capture timeout enters FAULT with an active source",
              rf_core_read_rx(core, &rx, 1) == RF_ERR_TIMEOUT &&
              rf_core_state(core) == RF_STATE_FAULT &&
              (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL));
        rf_daemon_disarm(d, "regression: timeout");
        check("fault disarm clears source and RX arm before releasing apertures",
              probe_release_calls == 1 &&
              !(rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) &&
              !(rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM));
    }
    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

static void test_explicit_rx_gain_observation(void)
{
    rf_core_t *core=healthy_core();
    rf_daemon_t *d;
    script_t s;
    int writes,ctrl;
    check("gain observer has an initialized fake core",core!=NULL);
    if (!core)return;
    d=new_daemon(core);
    if (!d){rf_core_destroy(core);return;}
    fake_reg[0x003]=0xC0;fake_reg[0x0FA]=0xAA;fake_reg[0x0FB]=0x08;
    fake_reg[0x2B0]=42;fake_reg[0x2B2]=0xE3;
    writes=fake_spi_writes;ctrl=fake_ctrl_writes;
    script_init(&s);hello(&s,1);req(&s,2,"device.info","{}");serve(d,&s);
    check("ordinary info does not request gain reads",
          contains(&s,"\"gain_observation\":null") && contains(&s,"NOT_REQUESTED"));
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("explicit info returns hardware raw RX1 observation",
          contains(&s,"\"gain_observation\":{\"available\":true") &&
          contains(&s,"\"gain_index_before\":42") && contains(&s,"\"digital_gain_code\":3") &&
          contains(&s,"\"mode_rx1\":2") && contains(&s,"sequential_reads_not_atomic"));
    check("gain query does not write chip or control paths",
          fake_spi_writes==writes && fake_ctrl_writes==ctrl);
    fake_pl_set_spi_read_failure(0x0FA,1);
    script_init(&s);hello(&s,1);req(&s,2,"device.info","{}");serve(d,&s);
    check("default info ignores an observation-only SPI failure",contains(&s,"NOT_REQUESTED"));
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("failed gain read is null with hardware IO reason",
          contains(&s,"\"gain_observation\":null") && contains(&s,"HARDWARE_IO"));
    fake_pl_set_spi_read_failure(0x0FA,0);
    check("gain observation failure leaves core ready",rf_core_state(core)==RF_STATE_READY);
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":\"true\"}");serve(d,&s);
    check("gain observation flag must be boolean",contains(&s,"INVALID_ARGUMENT"));
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":false}");serve(d,&s);
    check("explicit false adds no observation",contains(&s,"NOT_REQUESTED"));
    fake_pl_set_spi_read_change(0x2B0,43);
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("unstable gain indices retain both read values",
          contains(&s,"\"gain_index_before\":42") && contains(&s,"\"gain_index_after\":43") &&
          contains(&s,"\"index_stable\":false") && contains(&s,"started_at_monotonic_ns") &&
          contains(&s,"finished_at_monotonic_ns"));
    ((rf_session_t *)rf_daemon_session(d))->pending=true;
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("pending daemon refuses gain observation",
          contains(&s,"\"gain_observation\":null") && contains(&s,"NOT_READY_OR_UNSUPPORTED"));
    ((rf_session_t *)rf_daemon_session(d))->pending=false;
    rf_reg_write(RF_REG_CAP_CFG,RF_CAP_ARM|(1u<<13));
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("DONE plus ARM does not expose gain data",
          contains(&s,"\"gain_observation\":null") && contains(&s,"NOT_READY_OR_UNSUPPORTED"));
    rf_reg_write(RF_REG_CAP_CFG,0);
    rf_daemon_destroy(d);rf_core_destroy(core);
    d=new_daemon(NULL);
    script_init(&s);hello(&s,1);
    req(&s,2,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
    check("no core yields null rather than zero measurement",
          contains(&s,"\"gain_observation\":null") && contains(&s,"NOT_READY_OR_UNSUPPORTED"));
    rf_daemon_destroy(d);
    {
        rf_daemon_config_t cfg;
        core=healthy_core();probe_reset(core);d=new_wired_daemon(&cfg);
        fake_reg[0x003]=0xC0;fake_reg[0x0FA]=0xAA;fake_reg[0x0FB]=0x08;
        fake_reg[0x2B0]=42;fake_reg[0x2B2]=0xE3;
        script_init(&s);hello(&s,1);
        req(&s,2,"control.arm","{\"token\":\"s3cret\"}");
        req(&s,3,"device.info","{\"observe_rx_gain\":true}");serve(d,&s);
        check("production status_only preserves null readback and explicit gain",
              contains(&s,"\"mode\":\"status_only\"") && contains(&s,"\"readback\":null") &&
              contains(&s,"\"gain_observation\":{\"available\":true"));
        rf_daemon_disarm(d,"gain test");rf_daemon_destroy(d);rf_core_destroy(core);
    }
}

/* Search past binary IQ payloads; strstr deliberately cannot do that. */
static const char *timing_find(const script_t *s, const char *key)
{
    size_t i, n = strlen(key);
    for (i = 0; i + n <= s->out_len; ++i)
        if (memcmp(s->out + i, key, n) == 0)
            return (const char *)s->out + i;
    return NULL;
}

static uint64_t timing_number(const script_t *s, const char *key)
{
    char needle[80];
    const char *p;
    unsigned long long n = 0;
    snprintf(needle, sizeof needle, "\"%s\":", key);
    p = timing_find(s, needle);
    if (p) sscanf(p + strlen(needle), "%llu", &n);
    return (uint64_t)n;
}

static void timing_load(script_t *s)
{
    static const uint8_t payload[16] = { 1,0,2,0,3,0,4,0,5,0,6,0,7,0,8,0 };
    hello(s, 1);
    req(s, 2, "tx.load", "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\",\"direction\":\"tx\",\"wrap\":false}");
    feed_bytes(s, payload, sizeof payload);
}

static void timing_capture(script_t *s, long id, const char *extra)
{
    char args[384];
    snprintf(args, sizeof args, "{\"samples\":4,\"ratio\":0," LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":true%s}", extra);
    req(s, id, "loopback.physical.capture", args);
}

static void timing_shutdown(void *ctx)
{
    (void)rf_daemon_disarm((rf_daemon_t *)ctx, "timing external shutdown test");
}

static void timing_expire(void *ctx)
{
    timing_clock_base += RF_DAEMON_ARMED_IDLE_TIMEOUT_NS;
    check("watchdog expires after read-only timing queries",
          rf_daemon_watchdog_tick((rf_daemon_t *)ctx, test_now_ns()) == 1);
}

static void test_capture_timing(void)
{
    static const char *const keys[8] = {
        "tx_on_begin_ns", "tx_on_end_ns", "rx_arm_begin_ns", "rx_arm_end_ns",
        "drain_begin_ns", "drain_end_ns", "tx_off_begin_ns", "tx_off_end_ns"
    };
    rf_core_t *core;
    rf_daemon_t *d;
    script_t s, default_frame;
    unsigned default_calls, false_calls, true_calls;
    int j;
    printf("=== opt-in physical capture timing ===\n");
    timing_ordered = false; timing_bad_at = 0; timing_reverse = false;
    for (j = 0; j < 3; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, j == 0 ? "" : j == 1 ? ",\"observe_timing\":false" : ",\"observe_timing\":true");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0;
        check("timing capture and observer keep transport usable", serve(d, &s) == 0);
        if (j == 0) {
            default_calls = timing_clock_calls; default_frame = s;
            check("default physical capture has no usable trace", timing_find(&s, "\"last_capture_timing\":null") != NULL);
        } else if (j == 1) {
            false_calls = timing_clock_calls;
            check("false timing flag adds no clock reads", false_calls == default_calls);
        } else {
            true_calls = timing_clock_calls;
            check("timing opt-in adds exactly eight clock reads", true_calls == default_calls + 8u);
            check("trace identifies its frame and narrow API scope",
                  timing_find(&s, "\"last_capture_timing\":{\"available\":true") &&
                  timing_find(&s, "monotonic_api_brackets_not_sample_edges") &&
                  timing_number(&s, "capture_request_id") == 3 &&
                  timing_number(&s, "capture_timestamp_ns") == test_now_ns() &&
                  timing_number(&s, "samples") == 4 && timing_number(&s, "ratio") == 0);
            for (int k = 0; k < 8; ++k)
                check(keys[k], timing_number(&s, keys[k]) == 1234567890123ull);
            /* Both headers have the same frozen timestamp and unchanged payload. */
            const char *a = timing_find(&s, "{\"id\":3,");
            const char *b = timing_find(&default_frame, "{\"id\":3,");
            const char *end = a ? strchr(a, '\n') : NULL;
            check("instrumentation preserves exact IQ header and payload bytes", a && b && end &&
                  memcmp(a, b, (size_t)(end - a) + 1u + 16u) == 0);
        }
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    timing_ordered = true;
    core = healthy_core(); d = new_daemon(core);
    fake_reg[0x003] = 0xC0; fake_reg[0x0FA] = 0xAA;
    fake_reg[0x0FB] = 0x08; fake_reg[0x2B0] = 42; fake_reg[0x2B2] = 0xE3;
    script_init(&s); timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
    req(&s, 4, "device.info", "{\"observe_timing\":true,\"observe_rx_gain\":true}");
    timing_clock_calls = 0; serve(d, &s);
    check("all eight brackets are ordered on the injected clock",
          timing_number(&s, keys[0]) > 0 && timing_number(&s, keys[7]) < timing_number(&s, "capture_timestamp_ns"));
    for (j = 1; j < 8; ++j)
        check("ordered bracket pair", timing_number(&s, keys[j]) > timing_number(&s, keys[j-1]));
    check("trace timestamp is the original IQ header clock value",
          timing_number(&s, "capture_timestamp_ns") == timing_number(&s, "timestamp_ns"));
    check("gain and timing both fit a complete info response",
          timing_find(&s, "\"gain_observation\":{\"available\":true") &&
          timing_find(&s, "\"control_ops\":[") && s.out[s.out_len - 1] == '\n');
    script_init(&s); hello(&s, 1); req(&s, 2, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
    check("connection end clears the previous frame trace", contains(&s, "\"last_capture_timing\":null"));
    rf_daemon_destroy(d); rf_core_destroy(core);
    timing_ordered = false;
    /* All malformed booleans are refused before hardware control writes. */
    const char *bad[] = { "1", "\"true\"", "null", "[]", "{}" };
    for (j = 0; j < 5; ++j) {
        char extra[96]; int writes, spi;
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        writes = fake_ctrl_writes; spi = fake_spi_writes;
        script_init(&s); hello(&s, 1);
        snprintf(extra, sizeof extra, ",\"observe_timing\":%s", bad[j]); timing_capture(&s, 3, extra);
        req(&s, 4, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        check("nonboolean timing capture refused without control writes", contains(&s, "INVALID_ARGUMENT") && !contains(&s, "\"type\":\"iq_frame\"") && writes == fake_ctrl_writes && spi == fake_spi_writes);
        script_init(&s); hello(&s, 1); snprintf(extra, sizeof extra, "{\"observe_timing\":%s}", bad[j]); req(&s, 2, "device.info", extra); serve(d, &s);
        check("nonboolean timing observer refused without writes", contains(&s, "INVALID_ARGUMENT") && writes == fake_ctrl_writes && spi == fake_spi_writes);
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    const char *forbidden[] = { "rx.capture", "loopback.digital.capture", "loopback.physical.start", "loopback.digital.start", "tx.start", "state.get" };
    for (j = 0; j < 6; ++j) {
        int writes, spi;
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        writes = fake_ctrl_writes; spi = fake_spi_writes;
        script_init(&s); hello(&s, 1);
        req(&s, 3, forbidden[j], "{\"samples\":4,\"ratio\":0," LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":true,\"observe_timing\":true}");
        serve(d, &s);
        check("timing flag is rejected outside physical capture and info before hardware", refused_with(&s, 3, "INVALID_ARGUMENT") && writes == fake_ctrl_writes && spi == fake_spi_writes);
        script_init(&s); hello(&s, 1); req(&s, 3, forbidden[j], "{\"observe_timing\":false}"); serve(d, &s);
        check("even false timing flag is rejected on other operations", refused_with(&s, 3, "INVALID_ARGUMENT"));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Successful timing must not survive ANY later known control dispatch. */
    const char *control[] = { "tx.stop", "rf.configure", "control.disarm", "loopback.physical.start", "rx.capture", "loopback.physical.capture" };
    for (j = 0; j < 6; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
        req(&s, 4, control[j], "{}"); req(&s, 5, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        check("known control including failed capture clears old trace", timing_find(&s, "\"last_capture_timing\":null") && !timing_find(&s, "\"last_capture_timing\":{\"available\":true"));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    for (j = 0; j < 2; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
        timing_capture(&s, 4, j ? ",\"observe_timing\":false" : "");
        req(&s, 5, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        check("default or false capture clears prior timed frame", timing_find(&s, "\"last_capture_timing\":null") != NULL);
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Clock errors are diagnostic: still send IQ and confirm hardware off. */
    for (j = 0; j < 10; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0; timing_ordered = true;
        timing_bad_at = j == 9 ? 8u : 7u + (unsigned)j;
        timing_reverse = j == 9;
        check("clock anomaly does not fail completed physical capture", serve(d, &s) == 0 && timing_find(&s, "\"type\":\"iq_frame\"") && !(rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) && !(rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM));
        check("clock anomaly returns null with explicit unavailable reason", timing_find(&s, "\"last_capture_timing\":null") && timing_find(&s, "CLOCK_UNAVAILABLE"));
        timing_bad_at = 0; timing_ordered = false; timing_reverse = false;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    {
        rf_daemon_config_t config;
        core = healthy_core(); probe_reset(core); d = new_wired_daemon(&config);
        script_init(&s); hello(&s, 1); req(&s, 8, "control.arm", "{\"token\":\"s3cret\"}");
        timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
        req(&s, 4, "device.info", "{}"); req(&s, 5, "device.info", "{\"observe_timing\":false}");
        req(&s, 6, "device.info", "{\"observe_timing\":true}");
        check("status-only capture timing returns successfully", serve(d, &s) == 0);
        check("default and false info hide trace without consuming it",
              timing_find(&s, "\"capture_timing_unavailable\":\"NOT_REQUESTED\"") && timing_find(&s, "\"last_capture_timing\":{\"available\":true"));
        check("production timing preserves readback null and read-only ops",
              timing_find(&s, "\"readback\":null") && timing_find(&s, "\"ops\":[\"protocol.hello\",\"protocol.close\",\"device.info\",\"state.get\",\"status.subscribe\",\"pl.status.get\",\"rf.interface_status\"]"));
        rf_daemon_disarm(d, "timing production fixture"); rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Faulted capture cannot publish a trace; cleanup safety stays authoritative. */
    for (j = 0; j < 3; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        if (j == 0) fake_pl_set_capture_arm_stuck(1);
        if (j == 1) fake_pl_set_stop_active(1);
        if (j == 2) fake_pl_set_capture_done_stuck(1);
        script_init(&s); hello(&s, 1); timing_capture(&s, 3, ",\"observe_timing\":true");
        req(&s, 4, "device.info", "{\"observe_timing\":true}"); (void)serve(d, &s);
        check("hardware capture fault exposes no timing or success frame",
              timing_find(&s, "\"last_capture_timing\":null") && !timing_find(&s, "\"type\":\"iq_frame\""));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Fail the frame write only, after the hello has already succeeded. */
    core = healthy_core(); d = new_daemon(core);
    script_init(&s); timing_load(&s); serve(d, &s);
    script_init(&s); hello(&s, 1); serve(d, &s);
    size_t hello_bytes = s.out_len;
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, ",\"observe_timing\":true");
    s.write_fail_at = (int)hello_bytes;
    check("IQ send failure terminates the connection", serve(d, &s) == -1 && s.out_len == hello_bytes && !contains(&s, "iq_frame"));
    script_init(&s); hello(&s, 1); req(&s, 2, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
    check("failed IQ send cannot leave reusable timing", contains(&s, "\"last_capture_timing\":null"));
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* Exercise live shutdown and watchdog, with trace present before the boundary. */
    for (j = 0; j < 2; ++j) {
        rf_daemon_config_t config;
        core = healthy_core(); fill_identity(&config); config.verify_control_token = test_token_ok;
        d = NULL; rf_daemon_create(&config, &d); rf_daemon_attach_core(d, core);
        timing_ordered = true; timing_clock_base = 1000000000ull; timing_clock_calls = 0;
        script_init(&s); hello(&s, 1); req(&s, 8, "control.arm", "{\"token\":\"s3cret\"}");
        timing_load(&s); timing_capture(&s, 3, ",\"observe_timing\":true");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_read_offset = s.in_len;
        req(&s, 5, "device.info", "{\"observe_timing\":true}");
        s.plan[0] = (int)timing_read_offset; s.plan_n = 1;
        timing_read_ctx = d; timing_before_read = j ? timing_expire : timing_shutdown;
        serve(d, &s);
        check("trace is available before the external termination",
              timing_find(&s, "\"id\":4,\"ok\":true,\"result\":") && timing_find(&s, "\"last_capture_timing\":{\"available\":true"));
        check("external shutdown or watchdog clears live trace",
              timing_find(&s, "\"last_capture_timing\":null") && timing_find(&s, "\"armed\":false"));
        timing_before_read = NULL; timing_ordered = false; timing_clock_base = 1000000000ull;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Largest trace clocks, identity, legal request id and all advertised ops,
     * with gain simultaneously requested: checked full JSON line stays bounded. */
    {
        rf_daemon_config_t config;
        core = healthy_core(); fill_identity(&config); config.verify_control_token = test_token_ok;
        memset(config.identity.release_id, 'f', 32); config.identity.release_id[32] = 0;
        d = NULL; rf_daemon_create(&config, &d); rf_daemon_attach_core(d, core);
        fake_reg[0x003] = 0xC0; fake_reg[0x0FA] = 0xAA; fake_reg[0x0FB] = 8;
        fake_reg[0x2B0] = 76; fake_reg[0x2B2] = 0xE3;
        timing_ordered = true; timing_clock_base = UINT64_MAX - 10000ull; timing_clock_calls = 0;
        script_init(&s); hello(&s, 1); req(&s, 8, "control.arm", "{\"token\":\"s3cret\"}");
        timing_load(&s);
        /* The existing fake advances one sample per status poll (the real
         * hardware advances independently); 64 completes within its 100 polls. */
        req(&s, 2147483647L, "loopback.physical.capture", "{\"samples\":64,\"ratio\":4," LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":true,\"observe_timing\":true}");
        req(&s, 9, "device.info", "{\"observe_timing\":true,\"observe_rx_gain\":true}");
        check("largest complete gain+timing response sends successfully", serve(d, &s) == 0);
        const char *info = timing_find(&s, "{\"id\":9,");
        const char *end = info ? strchr(info, '\n') : NULL;
        check("largest info is complete JSON below client line cap", info && end && end[-1] == '}' && (size_t)(end - info) < 4096u &&
              timing_find(&s, "\"last_capture_timing\":{\"available\":true") && timing_find(&s, "\"gain_observation\":{\"available\":true") && timing_find(&s, "\"control_ops\":[\"control.disarm\",\"rf.init\""));
        check("uint64 timestamps and maximum legal frame identity survive formatting",
              timing_number(&s, "capture_timestamp_ns") > UINT64_MAX - 10000ull && timing_number(&s, "capture_request_id") == 2147483647ull &&
              timing_find(&s, "\"samples\":64,\"ratio\":4") != NULL);
        printf("TIMING_MAX_INFO_LINE_BYTES %llu\n", (unsigned long long)(end && info ? end - info : 0));
        timing_ordered = false; timing_clock_base = 1000000000ull;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
}

/* Runs at a READ boundary, so the connection is still open and the
 * end-of-connection disconnect rule has NOT run.  That distinction is the whole
 * point: checking after serve() returns cannot tell a transaction that cleaned up
 * after itself from one that left the mess for the disconnect rule. */
static int mid_connection_src_sel = -1;

static void probe_source_mid_connection(void *ctx)
{
    (void)ctx;
    mid_connection_src_sel = (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) != 0u;
}

static void test_a_failed_wait_stops_the_source_inside_the_transaction(void)
{
    static const uint8_t payload[16] = { 1,0,2,0,3,0,4,0,5,0,6,0,7,0,8,0 };
    rf_core_t *core;
    rf_daemon_t *d;
    script_t s;

    printf("=== a failed wait stops the source before the connection ends ===\n");
    core = healthy_core();
    d = new_daemon(core);
    script_init(&s);
    hello(&s, 1);
    req(&s, 2, "tx.load",
        "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
        "\"direction\":\"tx\",\"wrap\":false}");
    feed_bytes(&s, payload, sizeof payload);
    check("the fixture loads a TX frame", serve(d, &s) == 0);

    sleep_forces_failure = 1;
    script_init(&s);
    hello(&s, 1);
    timing_capture(&s, 2, ",\"observe_timing\":true,\"settle_wait_us\":1000");
    /* Fire the probe at the read boundary AFTER every request is consumed but
     * BEFORE EOF, so the transaction under test has finished and the connection
     * has not. */
    timing_read_offset = s.in_len;
    s.plan[0] = (int)timing_read_offset; s.plan_n = 1;
    mid_connection_src_sel = -1;
    timing_read_ctx = NULL; timing_before_read = probe_source_mid_connection;
    (void)serve(d, &s);
    timing_before_read = NULL;
    check("the failed wait refuses the capture", !contains(&s, "\"type\":\"iq_frame\""));
    check("the source is ALREADY off inside the transaction, not at disconnect",
          mid_connection_src_sel == 0);
    sleep_forces_failure = 0;
    rf_daemon_destroy(d);
    rf_core_destroy(core);
}

/* A controlled termination must be noticed DURING a connection, not only between
 * connections: the serve loop can otherwise sit in a blocking read for the whole
 * transport timeout, which is longer than the service's stop budget, and a
 * SIGTERM then looks exactly like a hung daemon.  The decision is injected, so
 * this is provable without a board, a signal or a socket. */
static int test_stop_requested;

static int test_should_stop(void)
{
    return test_stop_requested;
}

static void test_a_controlled_stop_is_noticed_during_a_connection(void)
{
    rf_core_t *core;
    rf_daemon_t *d;
    rf_daemon_config_t cfg;
    script_t s;
    uint32_t word = 0x00100020u;
    int rc;

    printf("=== a controlled stop is noticed during a live connection ===\n");
    core = healthy_core();
    fill_identity(&cfg);
    cfg.should_stop = test_should_stop;
    d = NULL;
    check("a daemon with a stop source is constructible",
          rf_daemon_create(&cfg, &d) == 0);
    rf_daemon_attach_core(d, core);

    /* With nothing requested, the loop is exactly what it was before. */
    script_init(&s); hello(&s, 1);
    check("without a pending stop the connection is served normally",
          serve(d, &s) == 0 && contains(&s, "\"ok\":true"));

    /* Now put the part into the state a stop has to unwind, then ask for one. */
    rf_core_write_tx(core, &word, 1);
    rf_core_configure_tx_path(core, 1, true, NULL);
    rf_core_set_tx_source(core, true, NULL);
    check("the fixture has the part sourcing",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) != 0u);
    test_stop_requested = 1;
    script_init(&s); hello(&s, 1);
    rc = serve(d, &s);
    test_stop_requested = 0;
    check("a pending stop ends the connection CLEANLY, not as a timeout",
          rc == 0);
    check("...and it is noticed before the queued request is even read",
          occurrences(&s, "\"id\":1") == 0);
    check("...and the ordinary path brings the sourced part to rest",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u);
    rf_daemon_destroy(d); rf_core_destroy(core);
}

/* The OTHER half of the same window: the pre-read check above can only see a stop
 * that is already pending when the loop comes back around.  A stop that lands
 * while the request is being read is invisible to it -- the bytes were already in
 * the buffer, so returning from the read says nothing about whether to keep
 * going, and the request would be dispatched normally.  This drives the stop
 * flag from INSIDE the read callback, i.e. at the only instant that separates
 * "the line is in hand" from "the line is acted on". */
static int stop_after_read;
static int stop_read_calls;

static int s_read_then_stop(void *ctx, void *buf, size_t len)
{
    int r = s_read(ctx, buf, len);

    if (r > 0) {
        ++stop_read_calls;
        if (stop_after_read)
            test_stop_requested = 1;
    }
    return r;
}

static void test_a_stop_that_lands_after_the_read_still_ends_it(void)
{
    rf_core_t *core;
    rf_daemon_t *d;
    rf_daemon_config_t cfg;
    script_t s;
    uint32_t word = 0x00100020u;
    int rc;

    printf("=== a stop that lands after the request is read does not dispatch it ===\n");
    core = healthy_core();
    fill_identity(&cfg);
    cfg.should_stop = test_should_stop;
    d = NULL;
    check("a daemon with a stop source is constructible",
          rf_daemon_create(&cfg, &d) == 0);
    rf_daemon_attach_core(d, core);

    /* Bring the session out of OFFLINE the ordinary way first: the disconnect
     * rule deliberately leaves an OFFLINE session alone, so without a served
     * request there would be nothing for the ordinary path to stop and the last
     * assertion below would fail for a reason that has nothing to do with the
     * dispatch guard. */
    script_init(&s); hello(&s, 1);
    check("a first connection is served normally",
          serve(d, &s) == 0 && contains(&s, "\"ok\":true"));

    rf_core_write_tx(core, &word, 1);
    rf_core_configure_tx_path(core, 1, true, NULL);
    rf_core_set_tx_source(core, true, NULL);
    check("the fixture has the part sourcing",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) != 0u);

    test_stop_requested = 0;
    stop_after_read = 1;
    stop_read_calls = 0;
    script_init(&s);
    hello(&s, 1);
    s.t.read = s_read_then_stop;      /* only this test arms the hook */
    rc = serve(d, &s);
    test_stop_requested = 0;
    stop_after_read = 0;

    check("the request really was read off the wire",
          stop_read_calls >= 1);
    check("...and a stop that lands after the read does not dispatch it",
          occurrences(&s, "\"id\":1") == 0);
    check("...and the connection still ends CLEANLY, not as a timeout",
          rc == 0);
    check("...through the ordinary path that brings the sourced part to rest",
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u);
    rf_daemon_destroy(d); rf_core_destroy(core);
}

/* Same writer policy as production, driven through real daemon dispatch and
 * the fake owned core. This is not a Linux signal/socket test (G1 owns that).
 * A FAULT refusal must not let a blocked error reply postpone safe cleanup. */
static int blocked_write_mode, blocked_write_sends, blocked_write_waits;
static uint64_t blocked_write_now;
static int blocked_write_stop(void *ctx)
{
    (void)ctx; return test_stop_requested;
}
static int blocked_write_clock(void *ctx, uint64_t *out)
{
    (void)ctx; *out = blocked_write_now; return 0;
}
static int64_t blocked_write_send(void *ctx, const void *buf, size_t len)
{
    (void)ctx; (void)buf; (void)len;
    ++blocked_write_sends; return RF_SEND_AGAIN;
}
static int blocked_write_wait(void *ctx, unsigned ms)
{
    (void)ctx; (void)ms; ++blocked_write_waits;
    if (blocked_write_mode == 0) {
        test_stop_requested = 1; return RF_WAIT_INTERRUPTED;
    }
    if (blocked_write_mode == 1) {
        blocked_write_now += 1001000000ull; return RF_WAIT_READY;
    }
    return RF_WAIT_ERROR;
}
static int s_write_blocked_refusal(void *ctx, const void *buf, size_t len)
{
    /* Daemon responses are NUL-terminated; len excludes that terminator. */
    if (strstr((const char *)buf, "\"ok\":false")) {
        const rf_write_ops_t ops = {ctx, blocked_write_stop, blocked_write_clock,
                                   blocked_write_send, blocked_write_wait};
        return rf_transport_write_all(&ops, buf, len, RF_SEND_TIMEOUT_MS);
    }
    return s_write(ctx, buf, len);
}
static void test_backpressured_fault_reply_reaches_owned_cleanup(void)
{
    int mode;
    printf("=== bounded FAULT reply reaches owned-core cleanup ===\n");
    for (mode = 0; mode < 3; ++mode) {
        rf_core_t *core = healthy_core();
        rf_daemon_config_t cfg;
        rf_daemon_t *d;
        script_t s;
        uint32_t word = 0x00100020u, rx;
        int writes, rc;
        probe_reset(core); d = new_wired_daemon(&cfg);
        check("backpressure fixture has an owned-capable daemon", core && d);
        if (!core || !d) { rf_daemon_destroy(d); rf_core_destroy(core); continue; }
        test_stop_requested = 0;
        script_init(&s); hello(&s, 1);
        req(&s, 2, "control.arm", "{\"token\":\"s3cret\"}");
        check("backpressure fixture acquires the owned core", serve(d, &s) == 0 && probe_acquire_calls == 1);
        rf_core_write_tx(core, &word, 1);
        rf_core_configure_tx_path(core, 1, true, NULL);
        rf_core_set_tx_source(core, true, NULL);
        rf_core_prepare_rx(core, 0, 1, false); rf_core_start_rx(core);
        fake_pl_set_capture_done_stuck(1);
        check("backpressure starts with FAULT and both TX/RX bits live",
              rf_core_read_rx(core, &rx, 1) == RF_ERR_TIMEOUT &&
              rf_core_state(core) == RF_STATE_FAULT &&
              (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) &&
              (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM));
        blocked_write_mode = mode; blocked_write_now = 100;
        blocked_write_sends = blocked_write_waits = 0;
        script_init(&s); hello(&s, 1);
        req(&s, 2, "rf.configure", NON_DEFAULT_CONFIG);
        req(&s, 3, "device.info", "{}");
        s.t.write_all = s_write_blocked_refusal;
        writes = fake_ctrl_writes; rc = serve(d, &s);
        check("blocked FAULT refusal fails transport after one bounded wait",
              rc == -1 && blocked_write_sends == 1 && blocked_write_waits == 1 && s.closed);
        check("failed refusal sends no success/frame and dispatches no queued query",
              !contains(&s, "\"id\":2") && !contains(&s, "\"id\":3") && !contains(&s, "iq_frame"));
        check("failed reply disconnect clears TX/RX while preserving FAULT",
              !(rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) &&
              !(rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) &&
              rf_core_state(core) == RF_STATE_FAULT);
        check("main-shaped shutdown releases the owned core after safety writes",
              rf_daemon_disarm(d, "transport regression") == 1 &&
              probe_release_calls == 1 && probe_writes_at_release > writes &&
              probe_writes_at_release == fake_ctrl_writes &&
              !(rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) &&
              !(rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM));
        test_stop_requested = 0; rf_daemon_destroy(d); rf_core_destroy(core);
    }
}

static void test_settle_wait(void)
{
    rf_core_t *core;
    rf_daemon_t *d;
    script_t s, zero_frame, base_frame;
    unsigned base_clocks = 0;
    int j;

    printf("=== bounded opt-in settling wait ===\n");

    /* Absent and explicit zero must both perform NO wait and add NO clock read,
     * and explicit zero must leave the frame byte-identical to absent. */
    for (j = 0; j < 2; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, j == 0 ? ",\"observe_timing\":true"
                                     : ",\"observe_timing\":true,\"settle_wait_us\":0");
        /* The trace is only ever read back through device.info.  Without this
         * request the response is EMPTY, and the zero-wait assertions below would
         * pass on nothing: timing_number() returns 0 for a field that is not there,
         * which is the same answer it gives for a field that is there and holds 0,
         * and timing_find() returns NULL for a field that was never emitted.  That
         * is why presence is asserted separately from the value. */
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0; sleep_calls = 0; sleep_total_us = 0; last_sleep_us = 0;
        check("zero-wait capture keeps the transport usable", serve(d, &s) == 0);
        if (j == 0) { base_clocks = timing_clock_calls; base_frame = s; }
        else zero_frame = s;
        check("absent or zero settle_wait_us performs no sleep",
              sleep_calls == 0 && sleep_total_us == 0);
        check("absent or zero settle_wait_us adds no clock read",
              timing_clock_calls == base_clocks);
        check("zero wait publishes an available trace",
              timing_find(&s, "\"last_capture_timing\":{\"available\":true") != NULL);
        check("zero wait is reported as an explicit zero, not merely absent",
              timing_find(&s, "\"settle_wait_requested_us\":0") != NULL);
        check("zero wait publishes no settle bracket",
              timing_find(&s, "settle_begin_ns") == NULL &&
              timing_find(&s, "settle_end_ns") == NULL);
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    {
        const char *a = timing_find(&zero_frame, "{\"id\":3,");
        const char *b = timing_find(&base_frame, "{\"id\":3,");
        const char *end = a ? strchr(a, '\n') : NULL;
        check("explicit zero is byte-identical to absent",
              a && b && end && memcmp(a, b, (size_t)(end - a) + 1u + 16u) == 0);
    }

    /* The clock baseline must be taken in the SAME request shape as the waiting
     * cases.  The zero-wait loop above uses a different script (it also carries the
     * tx.load), and a different shape performs a different number of clock reads,
     * so comparing across shapes failed this criterion for a reason that had
     * nothing to do with the wait. */
    {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        script_init(&s); hello(&s, 1);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":0");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0;
        (void)serve(d, &s);
        base_clocks = timing_clock_calls;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* A requested wait sleeps exactly once, inside the transaction, and is
     * reported back with provable brackets.  The smallest legal wait and the
     * ceiling are both exercised, not just a comfortable middle value. */
    for (j = 0; j < 3; ++j) {
        static const long requested_levels[3] = { 1L, 1000L, (long)RF_SETTLE_WAIT_MAX_US };
        long requested = requested_levels[j];
        char extra[128];
        int writes_before;

        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        writes_before = fake_ctrl_writes;
        script_init(&s); hello(&s, 1);
        snprintf(extra, sizeof extra,
                 ",\"observe_timing\":true,\"settle_wait_us\":%ld", requested);
        timing_capture(&s, 3, extra);
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0; sleep_calls = 0; sleep_total_us = 0;
        last_sleep_us = 0; sleep_at_ctrl_writes = -1;
        check("waiting capture keeps the transport usable", serve(d, &s) == 0);
        check("exactly one injected sleep of the requested duration",
              sleep_calls == 1 && sleep_total_us == (uint64_t)requested &&
              last_sleep_us == (uint64_t)requested);
        check("the wait happens inside the capture transaction",
              sleep_at_ctrl_writes > writes_before &&
              sleep_at_ctrl_writes < fake_ctrl_writes);
        check("a waiting capture adds exactly two clock reads",
              timing_clock_calls == base_clocks + 2u);
        check("the requested wait is reported back",
              timing_find(&s, "\"settle_wait_requested_us\":") != NULL &&
              timing_number(&s, "settle_wait_requested_us") == (uint64_t)requested);
        check("settle brackets are present, ordered and inside the TX-on..RX-arm gap",
              timing_number(&s, "settle_begin_ns") >= timing_number(&s, "tx_on_end_ns") &&
              timing_number(&s, "settle_end_ns") >= timing_number(&s, "settle_begin_ns") &&
              timing_number(&s, "settle_end_ns") <= timing_number(&s, "rx_arm_begin_ns"));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* A daemon with no wait facility must REFUSE a wait, never run without it. */
    {
        rf_daemon_config_t config;
        int writes, spi;

        core = healthy_core();
        fill_identity(&config); config.sleep_us = NULL;
        d = NULL;
        check("a daemon without a wait facility is constructible",
              rf_daemon_create(&config, &d) == 0);
        rf_daemon_attach_core(d, core);
        script_init(&s); timing_load(&s); serve(d, &s);
        writes = fake_ctrl_writes; spi = fake_spi_writes;
        script_init(&s); hello(&s, 1);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
        req(&s, 4, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        check("a wait without a wait facility is refused before any write",
              contains(&s, "INVALID_STATE") && !contains(&s, "\"type\":\"iq_frame\"") &&
              writes == fake_ctrl_writes && spi == fake_spi_writes);
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* Malformed, out-of-range and unobservable waits are refused before hardware. */
    {
        static const char *const bad[] = {
            "\"1000\"", "-1", "null", "[]", "{}", "5001", "1000000", "true"
        };
        for (j = 0; j < 8; ++j) {
            char extra[128]; int writes, spi;

            core = healthy_core(); d = new_daemon(core);
            script_init(&s); timing_load(&s); serve(d, &s);
            writes = fake_ctrl_writes; spi = fake_spi_writes;
            script_init(&s); hello(&s, 1);
            snprintf(extra, sizeof extra,
                     ",\"observe_timing\":true,\"settle_wait_us\":%s", bad[j]);
            timing_capture(&s, 3, extra);
            serve(d, &s);
            check("malformed or unbounded settle_wait_us is refused without control writes",
                  contains(&s, "INVALID_ARGUMENT") &&
                  !contains(&s, "\"type\":\"iq_frame\"") &&
                  writes == fake_ctrl_writes && spi == fake_spi_writes);
            rf_daemon_destroy(d); rf_core_destroy(core);
        }
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        {
            int writes = fake_ctrl_writes, spi = fake_spi_writes;
            script_init(&s); hello(&s, 1);
            timing_capture(&s, 3, ",\"settle_wait_us\":1000");
            serve(d, &s);
            check("a wait without observe_timing is refused before any write",
                  contains(&s, "INVALID_ARGUMENT") &&
                  !contains(&s, "\"type\":\"iq_frame\"") &&
                  writes == fake_ctrl_writes && spi == fake_spi_writes);
        }
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* The parameter belongs to physical capture only. */
    {
        static const char *const other[] = { "rx.capture", "state.get", "device.info" };
        for (j = 0; j < 3; ++j) {
            int writes;

            core = healthy_core(); d = new_daemon(core);
            script_init(&s); timing_load(&s); serve(d, &s);
            writes = fake_ctrl_writes;
            script_init(&s); hello(&s, 1);
            req(&s, 2, other[j], "{\"samples\":4,\"ratio\":0,\"settle_wait_us\":1000}");
            serve(d, &s);
            check("settle_wait_us is refused outside physical capture",
                  contains(&s, "INVALID_ARGUMENT") && writes == fake_ctrl_writes);
            rf_daemon_destroy(d); rf_core_destroy(core);
        }
    }

    /* On an ordered clock the wait trace is published and its brackets are pinned
     * to the two reads the wait itself took. */
    {
        uint64_t begin, end;

        core = healthy_core(); d = new_daemon(core);
        timing_ordered = true; timing_clock_calls = 0;
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
        req(&s, 4, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        begin = timing_number(&s, "settle_begin_ns");
        end = timing_number(&s, "settle_end_ns");
        check("an ordered waiting capture publishes its trace",
              timing_find(&s, "\"last_capture_timing\":{\"available\":true") != NULL);
        check("settle brackets come from the two reads the wait took",
              begin > 0 && end - begin == 100ull);
        timing_ordered = false; timing_clock_calls = 0;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* A clock that misbehaves DURING the wait must not publish a wait trace.  The
     * eight bracket reads occupy clock indices 7..14 for this script shape; the two
     * wait reads sit between brackets[1] and brackets[2], so they are 9 and 10.  A
     * zero read on the begin, or a backwards read on the end, both break the
     * TX-on..RX-arm ordering that the wait trace exists to prove. */
    for (j = 0; j < 2; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0; timing_ordered = true;
        timing_bad_at = j == 0 ? 9u : 10u;
        timing_reverse = (j == 1);
        check("a clock anomaly during the wait still sends the IQ frame",
              serve(d, &s) == 0 &&
              timing_find(&s, "\"type\":\"iq_frame\"") != NULL);
        check("a clock anomaly during the wait publishes no wait trace",
              timing_find(&s, "\"last_capture_timing\":null") != NULL &&
              timing_find(&s, "CLOCK_UNAVAILABLE") != NULL);
        timing_bad_at = 0; timing_ordered = false; timing_reverse = false;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* A wait that did not complete must FAIL the capture.  Publishing the frame
     * instead would put a trace next to an IQ frame whose settling wait never
     * happened -- the false measurement this feature exists to prevent. */
    {
        core = healthy_core(); d = new_daemon(core);
        sleep_forces_failure = 1;
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        sleep_calls = 0; sleep_total_us = 0;
        serve(d, &s);
        check("a failed settling wait refuses the capture",
              !contains(&s, "\"type\":\"iq_frame\"") && contains(&s, "\"ok\":false"));
        check("a failed wait is never reported as an available trace",
              timing_find(&s, "\"last_capture_timing\":{\"available\":true") == NULL);
        check("the failed wait was still attempted exactly once",
              sleep_calls == 1 && sleep_total_us == 1000ull);
        /* Refusing the frame is not enough: the TX source came on before the wait
         * and must not be left selected.  NOTE: this only observes the state after
         * serve() has run, i.e. after the disconnect rule too -- so it cannot tell
         * the transaction from the cleanup, and it passed while the transaction was
         * still leaving the source selected.  The criterion that actually pins the
         * transaction is test_a_failed_wait_stops_the_source_inside_the_transaction. */
        check("a capture that failed after TX came on releases SRC_SEL",
              (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) == 0u);
        check("a capture that failed after TX came on releases CAP_ARM",
              (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) == 0u);
        sleep_forces_failure = 0;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
}

/* --------------------- the in-window capture observation (2026-10-19) -------
 *
 * The 46 dB telemetry round left two competing explanations for the session-level
 * level spread unseparated: H-A, the TX side delivers a different level session to
 * session; H-B, the RX chain's effective gain differs INSIDE the capture window.
 * No existing read path can tell them apart -- every gain observation is taken with
 * the capture disarmed (rf_core_get_gain_observation refuses a raised arm), and every
 * TX attenuation readback is session-level.  This adds exactly ONE in-window sample
 * (a single checked read of the RX1 full-table gain index, taken between
 * rf_core_start_rx and rf_core_finish_rx) plus, on the existing dual-channel readback
 * path, the chip's own TX1/TX2 attenuation.  Both ride the capture's OWN response, so
 * a reading cannot be attributed to the wrong frame.
 *
 * Each criterion below is written so that it CAN fail, and for the reason it names:
 *
 *   - the value is the PART's (three seeded registers, the 0 and 76 endpoints
 *     included), so a constant or a request-side number cannot pass;
 *   - it is ONE read taken while RF_CAP_ARM is raised.  The value alone cannot prove
 *     where the read happened (nothing in the model changes on its own), so the fake
 *     PL witnesses the CAP_CFG value at the moment of the read: that is what
 *     separates "inside the window" from "before start_rx" or "after finish_rx",
 *     whose values are otherwise identical;
 *   - the transaction's write/wait/clock footprint is the one recorded from the
 *     daemon built WITHOUT the observation, so "the sample adds reads and nothing
 *     else" is a measured claim;
 *   - a failed read is NAMED in the same response and does not fail the capture,
 *     while a capture that fails for its own (pre-existing) reason is still refused;
 *   - the frozen header shape and the drained samples are byte-identical to what the
 *     pre-existing paths produce: the observation is an appended object.
 */
#define CAPTURE_OBSERVATION_GAIN_REG 0x2B0u
/* The footprint of ONE scripted physical capture, recorded from the pre-change daemon
 * in this package's work/prechange/ build.  This is the pin: the observation may add
 * READS, never a write, a wait or a clock read.  The live numbers are printed on every
 * run so the pin stays auditable. */
#define CAPTURE_FOOTPRINT_CLOCK_READS 6u
#define CAPTURE_FOOTPRINT_CTRL_WRITES 9
#define CAPTURE_FOOTPRINT_SPI_WRITES 0
#define CAPTURE_FOOTPRINT_SRC_SEL_WRITES 1

/* The samples the capture is supposed to acquire.  Seeding the RX BRAM is what makes
 * "the frame content is unchanged" a claim that can fail, instead of an all-zero
 * comparison that cannot. */
static void seed_rx_pattern(void)
{
    static const int pattern[8] = { 100, -200, 300, -400, 500, -600, 700, -800 };
    int k;

    for (k = 0; k < 4; ++k)
        fake_pl_write(RF_BRAM_RX_BASE + (uint32_t)k * 4u,
                      rf_iq_pack(pattern[k * 2], pattern[k * 2 + 1]));
}

static void expected_rx_wire(uint8_t *out)
{
    static const int pattern[8] = { 100, -200, 300, -400, 500, -600, 700, -800 };
    int k;

    for (k = 0; k < 4; ++k) {
        out[k * 4 + 0] = (uint8_t)(pattern[k * 2 + 0] & 0xFF);
        out[k * 4 + 1] = (uint8_t)((pattern[k * 2 + 0] >> 8) & 0xFF);
        out[k * 4 + 2] = (uint8_t)(pattern[k * 2 + 1] & 0xFF);
        out[k * 4 + 3] = (uint8_t)((pattern[k * 2 + 1] >> 8) & 0xFF);
    }
}

/* The drained samples that follow a frame header: the header starts at the
 * id-keyed line and the payload begins one byte past its newline.  Binary payloads
 * make strstr unusable, so the search is the byte scan timing_find already provides. */
static const uint8_t *frame_payload_of(const script_t *s, long id)
{
    char needle[32];
    const char *p;
    const char *nl;

    snprintf(needle, sizeof needle, "{\"id\":%ld,", id);
    p = timing_find(s, needle);
    if (!p)
        return NULL;
    nl = strchr(p, '\n');
    return nl ? (const uint8_t *)(nl + 1) : NULL;
}

static void test_in_window_capture_observation(void)
{
    script_t s;
    rf_core_t *core;
    rf_daemon_t *d;
    int ctrl, spi, src, reads;
    int capture_ok, named_ok;
    const uint8_t *payload;
    uint8_t observed_payload[16];
    uint8_t expected[16];
    char needle[128];
    char name[128];
    static const unsigned seeded[3] = { 0u, 42u, 76u };
    unsigned k;

    printf("=== in-window capture observation ===\n");
    expected_rx_wire(expected);

    /* (1) The reported index is the PART's own register, read during the capture's
     *     own transaction.  Three different seeded values -- endpoints included --
     *     so neither a constant nor the request can pass. */
    for (k = 0; k < 3u; ++k) {
        core = healthy_core(); d = new_daemon(core);
        fake_reg[CAPTURE_OBSERVATION_GAIN_REG] = (uint8_t)seeded[k];
        script_init(&s); timing_load(&s); timing_capture(&s, 3, "");
        check("a physical capture still answers with an iq_frame",
              serve(d, &s) == 0 && contains(&s, "\"type\":\"iq_frame\""));
        snprintf(needle, sizeof needle, "\"in_window_rx_gain_index\":%u",
                 (unsigned)seeded[k]);
        snprintf(name, sizeof name,
                 "the in-window gain index is the part's own value (%u)", seeded[k]);
        check(name, contains(&s, needle));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }

    /* (2) ONE read, taken while the capture window is open, with the transaction's
     *     write/wait/clock footprint unchanged from the pre-change daemon, and the
     *     frozen header shape and drained samples byte-identical. */
    core = healthy_core(); d = new_daemon(core);
    fake_reg[CAPTURE_OBSERVATION_GAIN_REG] = 42u;
    seed_rx_pattern();
    script_init(&s); timing_load(&s);
    check("the footprint fixture loads a TX frame", serve(d, &s) == 0);
    reads = fake_pl_spi_read_count(CAPTURE_OBSERVATION_GAIN_REG);
    ctrl = fake_ctrl_writes; spi = fake_spi_writes; src = fake_src_sel_writes;
    timing_clock_calls = 0; sleep_calls = 0;
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, "");
    check("the observed capture completes", serve(d, &s) == 0);
    printf("[capture footprint] clock_reads=%u ctrl_writes=%d spi_writes=%d "
           "src_sel_writes=%d sleep_calls=%u\n", timing_clock_calls,
           fake_ctrl_writes - ctrl, fake_spi_writes - spi,
           fake_src_sel_writes - src, sleep_calls);
    check("exactly one read of the RX1 gain register is made in the transaction",
          fake_pl_spi_read_count(CAPTURE_OBSERVATION_GAIN_REG) - reads == 1);
    check("that read is taken while the capture window is open (CAP_ARM raised)",
          (fake_pl_spi_read_cap_cfg(CAPTURE_OBSERVATION_GAIN_REG) & RF_CAP_ARM) != 0u);
    check("the observation adds no control-window or chip write",
          fake_ctrl_writes - ctrl == CAPTURE_FOOTPRINT_CTRL_WRITES &&
          fake_spi_writes - spi == CAPTURE_FOOTPRINT_SPI_WRITES &&
          fake_src_sel_writes - src == CAPTURE_FOOTPRINT_SRC_SEL_WRITES);
    check("the observation adds no injected-clock read",
          timing_clock_calls == CAPTURE_FOOTPRINT_CLOCK_READS);
    check("the observation adds no wait", sleep_calls == 0u);
    {
        char frozen[256];
        int frozen_len = rf_proto_write_iq_frame(frozen, sizeof frozen, 3, "rx", 4u,
                                                 60000000u, 0u, 1234567890123ull);
        const char *frame = timing_find(&s, "{\"id\":3,");
        const char *end = frame ? strchr(frame, '\n') : NULL;
        check("the frozen IQ header is preserved byte for byte as a prefix",
              frozen_len > 1 && frame && end &&
              (size_t)(end - frame) > (size_t)frozen_len &&
              memcmp(frame, frozen, (size_t)frozen_len - 1u) == 0 &&
              frame[frozen_len - 1] == ',');
        check("the observation is an appended object, not a changed field",
              frame && strncmp(frame + frozen_len - 1,
                               ",\"capture_observation\":{",
                               sizeof(",\"capture_observation\":{") - 1u) == 0);
    }
    payload = frame_payload_of(&s, 3);
    check("the observed frame drains exactly the samples the part delivered",
          payload && memcmp(payload, expected, sizeof expected) == 0);
    /* The script buffer is REUSED by the next connection, so the bytes under that
     * pointer do not survive it; the frame being compared against is copied out. */
    if (payload)
        memcpy(observed_payload, payload, sizeof observed_payload);
    /* The same BRAM content through the pre-existing RX capture: same samples, and no
     * observation object anywhere in its response. */
    script_init(&s); hello(&s, 1);
    req(&s, 2, "rx.capture", "{\"ratio\":1,\"samples\":4,\"continuous\":false}");
    check("the same samples come back from a capture with no observation",
          serve(d, &s) == 0 &&
          timing_find(&s, "in_window_rx_gain_index") == NULL);
    {
        const uint8_t *other = frame_payload_of(&s, 2);
        check("both paths drain the same bytes for the same part content",
              payload && other &&
              memcmp(observed_payload, other, sizeof expected) == 0);
    }
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* (3) Per-frame binding: a value is not cached and replayed onto a later frame. */
    core = healthy_core(); d = new_daemon(core);
    script_init(&s); timing_load(&s); serve(d, &s);
    fake_reg[CAPTURE_OBSERVATION_GAIN_REG] = 11u;
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, "");
    capture_ok = serve(d, &s) == 0 && contains(&s, "\"in_window_rx_gain_index\":11");
    fake_reg[CAPTURE_OBSERVATION_GAIN_REG] = 12u;
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, "");
    check("each frame carries its own sample, never a replayed one",
          capture_ok && serve(d, &s) == 0 &&
          contains(&s, "\"in_window_rx_gain_index\":12"));
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* (4) The chip's OWN TX1/TX2 attenuation, from the existing dual-channel
     *     readback.  The existing TX2 injection moves the CHIP value only, so a
     *     request-side number cannot satisfy the second case. */
    core = healthy_core(); d = new_daemon(core);
    script_init(&s); timing_load(&s); timing_capture(&s, 3, "");
    snprintf(needle, sizeof needle, "\"chip_tx_attenuation_mdb\":[%u,%u]",
             (unsigned)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB,
             (unsigned)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB);
    check("the chip's own TX1/TX2 attenuation is reported for the frame",
          serve(d, &s) == 0 && contains(&s, needle));
    rf_daemon_destroy(d); rf_core_destroy(core);

    core = healthy_core(); d = new_daemon(core);
    rf_core_host_set_headroom_tx2_fault(core, 2);
    script_init(&s); timing_load(&s); timing_capture(&s, 3, "");
    snprintf(needle, sizeof needle, "\"chip_tx_attenuation_mdb\":[%u,%u]",
             (unsigned)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB,
             (unsigned)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB + 250u);
    check("the reported pair is the chip readback, not the requested attenuation",
          serve(d, &s) == 0 && contains(&s, needle));
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* (5) A failed in-window read is NAMED in the same response and is not fatal.
     *     It must not be replaced by the frame-boundary reading, which is a
     *     different measurement taken at a different time. */
    core = healthy_core(); d = new_daemon(core);
    fake_reg[CAPTURE_OBSERVATION_GAIN_REG] = 42u;
    /* The frame-boundary reading below has its own preconditions (RX1 enabled, full
     * gain table), which this fixture satisfies so the two readings are comparable. */
    fake_reg[0x003u] = 0xC0u; fake_reg[0x0FAu] = 0xAAu; fake_reg[0x0FBu] = 0x08u;
    seed_rx_pattern();
    script_init(&s); timing_load(&s); serve(d, &s);
    fake_pl_set_spi_read_failure(CAPTURE_OBSERVATION_GAIN_REG, 1);
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, "");
    capture_ok = serve(d, &s) == 0 && contains(&s, "\"type\":\"iq_frame\"");
    named_ok = contains(&s, "\"in_window_rx_gain_index\":null") &&
               contains(&s, "\"in_window_rx_gain_unavailable\":\"HARDWARE_IO\"");
    payload = frame_payload_of(&s, 3);
    check("a failed in-window read does not fail the capture",
          capture_ok && payload && memcmp(payload, expected, sizeof expected) == 0);
    check("a failed in-window read is named, never zero or a default", named_ok);
    check("the failed capture still releases the window and the TX source",
          (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) == 0u &&
          (rf_reg_read(RF_REG_SRC_CFG) & RF_SRC_SEL) == 0u);
    fake_pl_set_spi_read_failure(CAPTURE_OBSERVATION_GAIN_REG, 0);
    script_init(&s); hello(&s, 1); req(&s, 4, "device.info", "{\"observe_rx_gain\":true}");
    check("the frame-boundary gain reading is a separate, still-working reading",
          serve(d, &s) == 0 &&
          contains(&s, "\"gain_observation\":{\"available\":true") &&
          contains(&s, "\"gain_index_before\":42"));
    rf_daemon_destroy(d); rf_core_destroy(core);

    core = healthy_core(); d = new_daemon(core);
    rf_core_host_set_headroom_tx2_fault(core, 1);
    script_init(&s); timing_load(&s); timing_capture(&s, 3, "");
    check("an unreadable chip attenuation is named and does not fail the capture",
          serve(d, &s) == 0 && contains(&s, "\"type\":\"iq_frame\"") &&
          contains(&s, "\"chip_tx_attenuation_mdb\":null") &&
          contains(&s, "\"chip_tx_attenuation_unavailable\":\"HARDWARE_IO\""));
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* (6) The observation must not soften an EXISTING stop condition, and must never
     *     be published without the frame it belongs to. */
    core = healthy_core(); d = new_daemon(core);
    script_init(&s); timing_load(&s); serve(d, &s);
    fake_pl_set_capture_arm_stuck(1);
    script_init(&s); hello(&s, 1); timing_capture(&s, 3, "");
    (void)serve(d, &s);
    check("an existing capture refusal is unchanged by the observation",
          refused_with(&s, 3, "HARDWARE_IO") &&
          timing_find(&s, "\"type\":\"iq_frame\"") == NULL &&
          timing_find(&s, "in_window_rx_gain_index") == NULL);
    rf_daemon_destroy(d); rf_core_destroy(core);

    /* (7) Scope: the object belongs to the physical capture only. */
    core = healthy_core(); d = new_daemon(core);
    script_init(&s); timing_load(&s);
    req(&s, 5, "rx.capture", "{\"ratio\":1,\"samples\":4,\"continuous\":false}");
    req(&s, 6, "loopback.digital.capture", "{\"samples\":4,\"ratio\":0}");
    capture_ok = serve(d, &s) == 0;
    /* Every presence/absence test from here on is the BYTE SCAN, not strstr: a
     * frame response carries a binary payload, so a strstr-based search cannot see
     * past the first NUL in it and would report "absent" for anything after the
     * first frame. */
    check("the RX-only capture response carries no observation",
          capture_ok && timing_find(&s, "\"id\":5,") != NULL &&
          timing_find(&s, "in_window_rx_gain_index") == NULL &&
          timing_find(&s, "chip_tx_attenuation_mdb") == NULL);
    check("the digital loopback capture response carries no observation",
          timing_find(&s, "\"id\":6,") != NULL &&
          timing_find(&s, "in_window_rx_gain_index") == NULL &&
          timing_find(&s, "chip_tx_attenuation_mdb") == NULL);
    rf_daemon_destroy(d); rf_core_destroy(core);
}

int main(void)
{
    printf("=== test_rf_daemon ===\n");

    /* -------------------------------------------------------- hello handshake */

    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        check("hello is answered with its own id and ok:true",
              serve(d, &s) == 0 && occurrences(&s, "\"id\":1") == 1 &&
              contains(&s, "\"ok\":true"));
        check("hello reports the daemon version and the protocol major",
              contains(&s, RF_DAEMON_VERSION) &&
              contains(&s, "\"protocol_major\":1"));
        /* The additive revision.  Task 1 Step 2 grew `armed`/`control_ops` and gave a
         * rejected token its own code, so the minor moved; minor 6 then added the
         * independent HEADROOM-1 research operations.  Pinned here so the next move
         * has to be deliberate. */
        check("hello declares the additive protocol revision",
              contains(&s, "\"protocol_minor\":6"));
        check("the lease is released when the connection ends",
              !rf_daemon_lease_held(d));
        rf_daemon_destroy(d);
    }
    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        req(&s, 1, "protocol.hello", "{\"major\":2,\"minor\":0}");
        req(&s, 2, "state.get", "{}");
        check("an incompatible major is VERSION_MISMATCH and ends the connection",
              serve(d, &s) == -1 && contains(&s, "VERSION_MISMATCH") &&
              occurrences(&s, "\"id\":2") == 0);
        rf_daemon_destroy(d);
    }
    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        req(&s, 7, "state.get", "{}");
        check("a first request that is not protocol.hello is refused",
              serve(d, &s) == -1 && contains(&s, "INVALID_ARGUMENT") &&
              !contains(&s, "\"ok\":true"));
        rf_daemon_destroy(d);
    }

    /* --------------------------------------------------- request/response pairing */

    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        req(&s, 3, "device.info", "{}");
        check("every request is answered exactly once, with its own id",
              serve(d, &s) == 0 && occurrences(&s, "\"id\":1") == 1 &&
              occurrences(&s, "\"id\":2") == 1 && occurrences(&s, "\"id\":3") == 1);
        rf_daemon_destroy(d);
    }
    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        /* No id: the protocol reserves that form for asynchronous events. */
        feed(&s, "{\"op\":\"state.get\",\"args\":{}}\n");
        check("a request with no id is refused, and the stream stays usable",
              serve(d, &s) == 0 && contains(&s, "\"ok\":false") &&
              contains(&s, "\"id\":1"));
        rf_daemon_destroy(d);
    }
    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "no.such.op", "{}");
        check("an unknown op is refused as UNSUPPORTED, with its id",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "UNSUPPORTED"));
        rf_daemon_destroy(d);
    }
    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        feed(&s, "this is not json\n");
        check("a malformed line is INVALID_ARGUMENT and does not end the stream",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT") &&
              contains(&s, "\"ok\":false"));
        rf_daemon_destroy(d);
    }

    /* ------------------------------------------------------------- line limits */

    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);
        int i;

        script_init(&s);
        hello(&s, 1);
        feed(&s, "{\"id\":9,\"op\":\"");
        for (i = 0; i < (int)RF_PROTO_LINE_MAX + 8; ++i)
            feed(&s, "A");
        feed(&s, "\",\"args\":{}}\n");
        check("a line past the limit is OVERFLOW and ends the connection",
              serve(d, &s) == -1 && contains(&s, "OVERFLOW"));
        rf_daemon_destroy(d);
    }

    /* --------------------------------------------------- short reads must frame */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint32_t words[4] = { 0x00100200u, 0x00300400u, 0x00500600u, 0x00700800u };
        uint8_t wire[16];
        uint32_t w0 = 0, w3 = 0;

        rf_iq_bram_to_wire(words, 4, wire, sizeof wire);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":true}");
        feed_bytes(&s, wire, sizeof wire);
        plan_chunks(&s, 5);
        check("framing survives short reads splitting both line and payload",
              serve(d, &s) == 0 && contains(&s, "\"ok\":true"));
        rf_core_read_tx_word(core, 0, &w0);
        rf_core_read_tx_word(core, 3, &w3);
        check("the short-read-framed payload reached TX BRAM intact",
              w0 == words[0] && w3 == words[3]);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* -------------------------------------------- tx.load payload integrity */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint32_t sentinel[4] = { 0xA5A5A5A5u, 0x5A5A5A5Au, 0x12345678u, 0x9ABCDEF0u };
        uint8_t good[16];
        uint32_t words[4] = { 0x00000001u, 0x00000002u, 0x00000003u, 0x00000004u };
        uint32_t chk = 0;
        int i, intact;

        rf_iq_bram_to_wire(words, 4, good, sizeof good);
        rf_core_write_tx(core, sentinel, 4);

        /* (a) a declaration whose byte count disagrees with its sample count.
         * The refusal happens BEFORE the payload is consumed, so it must end the
         * connection: the declared length is not trustworthy, and parsing the
         * payload as lines would swallow the client's next request. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":12,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\"}");
        feed_bytes(&s, good, 12);
        check("a tx.load whose bytes != samples*4 is refused",
              serve(d, &s) == -1 && contains(&s, "INVALID_ARGUMENT"));
        intact = 1;
        for (i = 0; i < 4; ++i) {
            rf_core_read_tx_word(core, (size_t)i, &chk);
            if (chk != sentinel[i])
                intact = 0;
        }
        check("and it wrote nothing to TX BRAM (no partial write)", intact);

        /* (b) a payload with an out-of-range sample */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":2,\"bytes\":8,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\"}");
        /* I = 4096, one past the 12-bit ceiling. */
        {
            uint8_t bad[8] = { 0x00, 0x10, 0x00, 0x00, 0x01, 0x00, 0x02, 0x00 };
            feed_bytes(&s, bad, sizeof bad);
        }
        check("a tx.load with an out-of-range sample is refused",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT"));
        intact = 1;
        for (i = 0; i < 4; ++i) {
            rf_core_read_tx_word(core, (size_t)i, &chk);
            if (chk != sentinel[i])
                intact = 0;
        }
        check("and an out-of-range sample wrote nothing to TX BRAM", intact);

        /* (c) samples past the configured ceiling */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4097,\"bytes\":16388,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\"}");
        check("a tx.load past max_samples is refused",
              serve(d, &s) == -1 && contains(&s, "INVALID_ARGUMENT"));
        intact = 1;
        for (i = 0; i < 4; ++i) {
            rf_core_read_tx_word(core, (size_t)i, &chk);
            if (chk != sentinel[i])
                intact = 0;
        }
        check("and it wrote nothing to TX BRAM", intact);

        /* (d) the client disappears in the middle of the payload */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\"}");
        feed_bytes(&s, good, 7);          /* seven of the sixteen bytes */
        /* The read plan is per-CALL for the whole connection, not per-payload, so
         * the two lines have to be spelled out: everything up to the payload,
         * then the seven bytes, then EOF. */
        s.plan[0] = (int)(s.in_len - 7);
        s.plan[1] = 7;
        s.plan[2] = 0;                    /* then EOF */
        s.plan_n = 3;
        s.plan_i = 0;
        check("a disconnect mid-payload is a framing failure, not a success",
              serve(d, &s) == -1 && contains(&s, "\"id\":2") &&
              !contains(&s, "\"id\":2,\"ok\":true"));
        intact = 1;
        for (i = 0; i < 4; ++i) {
            rf_core_read_tx_word(core, (size_t)i, &chk);
            if (chk != sentinel[i])
                intact = 0;
        }
        check("and a truncated payload wrote nothing to TX BRAM", intact);

        /* (e) the payload read times out */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\"}");
        feed_bytes(&s, good, 7);
        s.plan[0] = (int)(s.in_len - 7);
        s.plan[1] = 7;
        s.plan[2] = RF_TRANSPORT_TIMEOUT;
        s.plan_n = 3;
        s.plan_i = 0;
        check("a payload timeout is reported as TIMEOUT",
              serve(d, &s) == -1 && contains(&s, "TIMEOUT"));
        intact = 1;
        for (i = 0; i < 4; ++i) {
            rf_core_read_tx_word(core, (size_t)i, &chk);
            if (chk != sentinel[i])
                intact = 0;
        }
        check("and a timed-out payload wrote nothing to TX BRAM", intact);

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------------------------------------------------- rx.capture framing */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint8_t expect[16];
        uint32_t rxw[4];
        size_t i, header_len, payload_at;
        int matched;

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rx.capture",
            "{\"ratio\":1,\"samples\":4,\"continuous\":false}");
        check("rx.capture is answered with an iq_frame header",
              serve(d, &s) == 0 && contains(&s, "\"type\":\"iq_frame\"") &&
              contains(&s, "\"direction\":\"rx\"") && contains(&s, "\"bytes\":16") &&
              contains(&s, "\"sample_rate_hz\":60000000"));
        check("and the frame carries the timestamp the clock actually gave",
              contains(&s, "1234567890123"));

        /* The payload must be exactly the 16 bytes right after the header's
         * newline, and the total output must be header + payload -- an event
         * squeezed in during the payload would show up as extra bytes.
         *
         * Anchor on THIS frame's own header, not on the first newline in the
         * stream: the hello response ends in a newline too, and anchoring there
         * would demand a zero-length frame header with the payload starting
         * immediately after the hello reply. */
        {
            char *fh = strstr((char *)s.out, "\"type\":\"iq_frame\"");
            char *nl = fh ? strchr(fh, '\n') : NULL;
            header_len = nl ? (size_t)(nl - (char *)s.out) + 1 : 0;
        }
        payload_at = header_len;
        for (i = 0; i < 4; ++i)
            rf_core_read_rx_word(core, i, &rxw[i]);
        rf_iq_bram_to_wire(rxw, 4, expect, sizeof expect);
        matched = (s.out_len == payload_at + 16) &&
                  (memcmp(s.out + payload_at, expect, 16) == 0);
        check("the RX payload follows its header immediately, with no event "
              "interleaved and no trailing bytes", matched);

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ---------- loopback.digital.capture: exact data, tap closed again ------ */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint8_t payload[16];
        uint32_t seed[4] = { 0x00000777u, 0x00001888u, 0x00000AAFu, 0x00001CCCu };
        uint8_t expect[16];
        uint8_t loop_reg = 0xFFu;
        unsigned spi_before;
        size_t i, header_len, payload_at;
        int k;

        /* The RX BRAM is where the PL capture deposits samples; the AXI controller
         * can also write it, which is how this seeds a known content without
         * pretending the chip ran.  The point of the criterion is what the daemon
         * does with the words, not how they got there. */
        for (k = 0; k < 4; ++k)
            fake_pl_write(RF_BRAM_RX_BASE + (uint32_t)k * 4u, seed[k]);

        /* Legal s16 IQ pairs: each field must be -2048..2047, so a naive
         * (i, i+1) byte ramp would be rejected by the tx.load validator -- which
         * is exactly the mistake this comment exists to stop repeating. */
        {
            static const int iq[8] = { 100, -200, 300, -400,
                                       500, -600, 700, -800 };
            for (k = 0; k < 4; ++k) {
                payload[k * 4 + 0] = (uint8_t)(iq[k * 2 + 0] & 0xFF);
                payload[k * 4 + 1] = (uint8_t)((iq[k * 2 + 0] >> 8) & 0xFF);
                payload[k * 4 + 2] = (uint8_t)(iq[k * 2 + 1] & 0xFF);
                payload[k * 4 + 3] = (uint8_t)((iq[k * 2 + 1] >> 8) & 0xFF);
            }
        }

        spi_before = (unsigned)fake_spi_writes;

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":false}");
        feed_bytes(&s, payload, sizeof payload);
        req(&s, 3, "loopback.digital.capture", "{\"samples\":4,\"ratio\":0}");
        check("loopback.digital.capture answers with an iq_frame",
              serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
              contains(&s, "\"type\":\"iq_frame\"") &&
              contains(&s, "\"bytes\":16") &&
              contains(&s, "\"sample_rate_hz\":60000000"));

        /* The payload is the 16 bytes right after THIS frame's header newline. */
        {
            char *fh = strstr((char *)s.out, "\"id\":3");
            char *fr = fh ? strstr(fh, "\"type\":\"iq_frame\"") : NULL;
            char *nl = fr ? strchr(fr, '\n') : NULL;
            header_len = nl ? (size_t)(nl - (char *)s.out) + 1 : 0;
        }
        payload_at = header_len;
        rf_iq_bram_to_wire(seed, 4, expect, sizeof expect);
        check("the loopback payload is the RX BRAM content, byte for byte",
              s.out_len == payload_at + 16 &&
              memcmp(s.out + payload_at, expect, 16) == 0);

        /* 0x3F5 bit0 is DATA_PORT_LOOP_TEST_ENABLE.  Two accepted SPI writes
         * (set, then clear) is what proves the handler really toggled the bit
         * instead of skipping the loop and reading stale BRAM. */
        check("the handler toggled the AD9361 data-port loop bit (twice)",
              fake_spi_writes >= (int)(spi_before + 2u) &&
              rf_core_spi_read(core, 0x3F5u, &loop_reg) == RF_ERR_OK);
        check("the data-port loop is clear again after the op",
              (loop_reg & 0x01u) == 0u);
        check("so the part is READY, not left looping",
              rf_core_state(core) == RF_STATE_READY &&
              rf_daemon_session(d)->state == RF_STATE_READY);

        /* Negative control: a bad ratio is rejected before any hardware. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":false}");
        feed_bytes(&s, payload, sizeof payload);
        req(&s, 3, "loopback.digital.capture", "{\"samples\":4,\"ratio\":5}");
        check("an out-of-range ratio is an argument error, not a silent clamp",
              serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
              contains(&s, "ratio"));

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* Negative control: no tx.load at all.  A FRESH daemon on purpose -- tx_count is
     * daemon state, so it survives a connection and would make this pass vacuously
     * against the daemon above. */
    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.digital.capture", "{\"samples\":4,\"ratio\":0}");
        check("a digital capture with no tx.load is refused, naming tx.load",
              serve(d, &s) == 0 && contains(&s, "\"ok\":false") &&
              contains(&s, "tx.load"));
        check("and the refusal did not move the part",
              rf_core_state(core) == RF_STATE_READY &&
              rf_daemon_session(d)->state == RF_STATE_READY);

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* --------------------- a rejected frame must not desync the stream */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint8_t payload[16];
        int i;

        /* A payload made entirely of newlines: if ANY of it is parsed as request
         * lines, each one shows up as its own spurious error response. */
        for (i = 0; i < 16; ++i)
            payload[i] = 0x0A;
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":5}");
        feed_bytes(&s, payload, sizeof payload);
        req(&s, 3, "state.get", "{}");
        check("a rejected tx.load frame ends the connection instead of parsing "
              "its payload as requests",
              serve(d, &s) == -1 && contains(&s, "\"id\":2") &&
              occurrences(&s, "\"ok\":false") == 1 &&
              !contains(&s, "\"id\":3"));
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------------------ a stop with no part attached is not a success */

    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.stop", "{}");
        check("with no core, tx.stop is refused rather than reported as a clean stop",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "\"ok\":false") && contains(&s, "INVALID_STATE"));

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.stop", "{}");
        check("with no core, loopback.stop is refused too",
              serve(d, &s) == 0 && contains(&s, "\"ok\":false"));
        check("and it did not talk the session into READY",
              rf_daemon_session(d)->state == RF_STATE_OFFLINE &&
              !contains(&s, "\"state\":\"READY\""));

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        serve(d, &s);
        check("so the daemon still reports OFFLINE afterwards",
              contains(&s, "\"state\":\"OFFLINE\""));
        rf_daemon_destroy(d);
    }

    /* ------ a stop the core cannot confirm must not be reported as READY */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);

        script_init(&s);
        hello(&s, 1);
        /* A CONTINUOUS capture: nothing will finish it, so disarming is all a
         * stop can do and the core keeps its RX_CAPTURING state. */
        req(&s, 2, "rx.capture",
            "{\"ratio\":1,\"samples\":4,\"continuous\":true}");
        check("a continuous capture starts and the core is capturing",
              serve(d, &s) == 0 && contains(&s, "\"ok\":true") &&
              rf_core_state(core) == RF_STATE_RX_CAPTURING);
        /* The disarm makes the hardware safe, but the core has no way back to
         * READY (rf_core.c has no stop_rx), so READY here would hand the next
         * client a part that refuses everything. */
        check("the disconnect rule reports the stop as unconfirmed, not READY",
              rf_daemon_session(d)->state == RF_STATE_FAULT &&
              rf_core_state(core) == RF_STATE_RX_CAPTURING);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* --- a stop against an uninitialized part must not claim READY either */

    {
        script_t s;
        rf_core_t *core = opened_core();
        rf_daemon_t *d = new_daemon(core);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.stop", "{}");
        check("a stop against an open but uninitialized part is refused, not "
              "answered READY",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "\"ok\":false") && contains(&s, "INVALID_STATE"));
        check("and the session is still OFFLINE, so the part is still drivable",
              rf_daemon_session(d)->state == RF_STATE_OFFLINE &&
              rf_core_state(core) == RF_STATE_OFFLINE);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.init",
            "{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,"
            "\"attenuation_mdB\":10000}");
        check("which is what lets the part be brought up afterwards",
              serve(d, &s) == 0 && contains(&s, "\"ok\":true") &&
              rf_core_state(core) == RF_STATE_READY);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------- a shutoff that could not be confirmed is not a clean end */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint32_t words[4] = { 0x00100200u, 0x00300400u, 0x00500600u, 0x00700800u };
        uint8_t wire[16];

        rf_iq_bram_to_wire(words, 4, wire, sizeof wire);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":true}");
        feed_bytes(&s, wire, sizeof wire);
        req(&s, 3, "tx.start", "{}");
        /* From here the vendor readback disagrees with what was written, so the
         * ceiling attenuation cannot be applied.  The TX was live and is stopped,
         * but a shutoff nobody could confirm must not be reported as a clean end.
         */
        rf_core_host_set_vendor_readback_fault(core, 1);
        check("a disconnect whose safety attenuation could not be applied is not "
              "a clean end",
              serve(d, &s) == -1 && contains(&s, "\"id\":3") &&
              contains(&s, "\"ok\":true"));
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------------------------------------------------------ the single lease */

    {
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        check("a fresh daemon holds no lease", !rf_daemon_lease_held(d));
        check("the first client takes the lease", rf_daemon_lease_acquire(d) == 0 &&
              rf_daemon_lease_held(d));
        script_init(&s);
        hello(&s, 1);
        check("a second client is told BUSY and does not take the lease",
              serve(d, &s) == 0 && contains(&s, "BUSY") &&
              rf_daemon_lease_held(d));
        check("and the refused client's transport was closed",
              s.closed == 1);
        check("the second client was answered with no id of its own",
              occurrences(&s, "\"ok\":false") == 1);
        rf_daemon_lease_release(d);
        check("releasing the lease lets a new client in",
              rf_daemon_lease_acquire(d) == 0 && rf_daemon_lease_held(d));
        rf_daemon_lease_release(d);
        rf_daemon_destroy(d);
    }

    /* ------------------------------------------------------- disconnect safety */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        uint32_t words[4] = { 0x00100200u, 0x00300400u, 0x00500600u, 0x00700800u };
        uint8_t wire[16];
        rf_readback_t rb;

        rf_iq_bram_to_wire(words, 4, wire, sizeof wire);
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.load",
            "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
            "\"direction\":\"tx\",\"wrap\":true}");
        feed_bytes(&s, wire, sizeof wire);
        req(&s, 3, "tx.start", "{}");
        /* The client then vanishes without a stop. */
        check("a client can get the TX live",
              serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
              contains(&s, "\"ok\":true"));
        check("the disconnect left the part stopped, not sourcing",
              rf_core_state(core) != RF_STATE_TX_ACTIVE &&
              rf_core_state(core) != RF_STATE_FAULT);
        memset(&rb, 0, sizeof rb);
        check("and it left the TX at the maximum attenuation, read back",
              rf_core_get_readback(core, &rb) == RF_ERR_OK &&
              rb.attenuation_mdB == RF_SAFE_ATTENUATION_MDB);

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }
    {
        /* A disconnect while nothing is live must not touch the hardware. */
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        rf_readback_t rb;
        uint32_t atten_before;

        rf_core_get_readback(core, &rb);
        atten_before = rb.attenuation_mdB;
        script_init(&s);
        hello(&s, 1);
        check("an idle disconnect is a clean end",
              serve(d, &s) == 0);
        memset(&rb, 0, sizeof rb);
        rf_core_get_readback(core, &rb);
        check("an idle disconnect leaves the attenuation alone",
              rb.attenuation_mdB == atten_before);
        check("an idle disconnect leaves the part in READY",
              rf_core_state(core) == RF_STATE_READY);
        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* ------------------------------------------------- gates that need no board */

    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.start", "{}");
        check("tx.start before any load is refused, with its id",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "\"ok\":false"));

        /* samples/ratio are supplied so that the gate under test is the ONLY
         * possible source of INVALID_ARGUMENT here: with a complete request, a
         * dropped cable or attenuation rule would let the loopback through and
         * these assertions would fail instead of passing for the wrong reason. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.physical.start",
            "{" LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":false,"
            "\"samples\":4,\"ratio\":1}");
        check("a physical loopback without cable confirmation is refused",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT"));
        check("and it performed no hardware action",
              rf_core_state(core) == RF_STATE_READY);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.physical.start",
            "{\"attenuation_mdB\":0,\"cable_confirmed\":true,"
            "\"samples\":4,\"ratio\":1}");
        check("a physical loopback at attenuation 0 is refused",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT"));
        check("and it performed no hardware action either",
              rf_core_state(core) == RF_STATE_READY);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "rf.init",
            "{\"lo_hz\":90000000,\"sample_rate_hz\":60000000,\"attenuation_mdB\":10000}");
        check("rf.init is refused while the part is already up (INVALID_STATE)",
              serve(d, &s) == 0 && contains(&s, "INVALID_STATE"));

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }
    {
        /* With no core attached, everything that would touch hardware is
         * refused, and the two read-only ops still answer. */
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "device.info", "{}");
        req(&s, 3, "state.get", "{}");
        check("with no core, device.info and state.get still answer",
              serve(d, &s) == 0 && occurrences(&s, "\"id\":2") == 1 &&
              occurrences(&s, "\"id\":3") == 1 && !contains(&s, "\"ok\":false"));

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "tx.start", "{}");
        check("with no core, a hardware op is refused rather than faked",
              serve(d, &s) == 0 && contains(&s, "\"ok\":false"));
        rf_daemon_destroy(d);
    }
    {
        /* state.get must report the session's state, and the name must be the
         * wire name the state machine uses. */
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        serve(d, &s);
        check("state.get reports OFFLINE before anything is started",
              contains(&s, "\"state\":\"OFFLINE\""));
        rf_daemon_destroy(d);
    }
    {
        /* A write failure must not be reported as a successful response. */
        script_t s;
        rf_daemon_t *d = new_daemon(NULL);

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "state.get", "{}");
        s.write_fail_at = 0;   /* the very first write fails */
        check("a transport write failure is an error, not a silent success",
              serve(d, &s) == -1 && !contains(&s, "\"ok\":true"));
        rf_daemon_destroy(d);
    }

    /* -------------------- device.info reports the release identity (F-T7b) ---
     *
     * Driven through the real startup path (manifest file -> rf_daemon_startup
     * -> config), so what is under test is the plumbing and not a value this
     * test wrote into the config itself.  The expectation is the manifest's own
     * text: device.info is an identity report, and identity means the bytes the
     * manifest carried. */
    {
        script_t s;
        rf_core_t *core = NULL;
        rf_daemon_t *d = new_daemon_with_identity(&core);
        int started = d != NULL && core != NULL;

        check("startup carries a manifest's identity into the daemon",
              started);
        if (started) {
            script_init(&s);
            hello(&s, 1);
            req(&s, 2, "device.info", "{}");
            check("device.info is still answered, with its own id",
                  serve(d, &s) == 0 && occurrences(&s, "\"id\":2") == 1 &&
                  contains(&s, "\"ok\":true"));
            check("device.info reports the manifest's bit_sha256",
                  contains(&s, "\"bit_sha256\":\"" TEST_BIT_SHA256 "\""));
            check("device.info reports the manifest's hwh_sha256",
                  contains(&s, "\"hwh_sha256\":\"" TEST_HWH_SHA256 "\""));
            /* The response grew required fields, so the revision is not the one
             * device.info answered at before.  Pinned like protocol_major, so a
             * change to it has to be deliberate. */
            check("device.info declares the additive protocol revision",
                  contains(&s, "\"protocol_minor\":6"));
            rf_daemon_destroy(d);
            rf_core_destroy(core);
        }
        remove(IDENTITY_MANIFEST);
    }

    /* ------------------------------- capabilities names a real op surface ---
     *
     * Non-tautological by construction: the names are taken from the daemon's
     * own response and then ISSUED through the real dispatch path.  A name that
     * is advertised but not routed answers UNSUPPORTED and fails; a name that is
     * not advertised must answer UNSUPPORTED, so the list cannot be a set of
     * names that merely happen to be ignorable. */
    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);
        char ops[32][RF_PROTO_OP_MAX];
        int n = 0, i;

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "device.info", "{}");
        if (serve(d, &s) == 0)
            n = parse_capability_ops((const char *)s.out, ops, 32);
        check("device.info reports capabilities as an object with a non-empty "
              "ops list",
              n > 0);

        for (i = 0; i < n; ++i) {
            script_t one;
            char name[192];

            script_init(&one);
            hello(&one, 1);
            req(&one, 2, ops[i], "{}");
            (void)serve(d, &one);
            snprintf(name, sizeof name,
                     "the advertised op %s is dispatched, not UNSUPPORTED",
                     ops[i]);
            check(name, occurrences(&one, "\"id\":2") == 1 &&
                  !contains(&one, "UNSUPPORTED"));
        }

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "regwrite", "{}");
        check("an op name that is NOT advertised answers UNSUPPORTED",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "UNSUPPORTED"));

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    /* -------------------- F-T7a: a physical loopback with nothing loaded ----- */
    {
        script_t s;
        rf_core_t *core = healthy_core();
        rf_daemon_t *d = new_daemon(core);

        /* No tx.load: the request is otherwise complete, so the gap under test is
         * the only possible source of the refusal. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.physical.start",
            "{" LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":true,"
            "\"samples\":4,\"ratio\":1}");
        req(&s, 3, "state.get", "{}");
        check("a physical loopback with no tx.load is refused as "
              "INVALID_ARGUMENT, naming the reason",
              serve(d, &s) == 0 && contains(&s, "\"id\":2") &&
              contains(&s, "INVALID_ARGUMENT") &&
              contains(&s, "nothing to loop back"));
        check("and it performed no hardware action",
              rf_core_state(core) == RF_STATE_READY);
        check("and the refusal did not leave the session BUSY",
              !rf_daemon_session(d)->pending &&
              contains(&s, "\"id\":3") && contains(&s, "\"pending\":false") &&
              contains(&s, "\"state\":\"READY\""));

        /* The safety ordering: the two safety-critical arguments are still
         * judged first, so a request that does not name them is refused for that
         * reason rather than for the empty BRAM. */
        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.physical.start",
            "{\"cable_confirmed\":true,\"samples\":4,\"ratio\":1}");
        check("a physical loopback with no attenuation is refused for the "
              "attenuation, not for the missing waveform",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT") &&
              contains(&s, "must name its attenuation_mdB") &&
              !contains(&s, "nothing to loop back"));

        script_init(&s);
        hello(&s, 1);
        req(&s, 2, "loopback.physical.start",
            "{" LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":false,"
            "\"samples\":4,\"ratio\":1}");
        check("and without cable confirmation it is refused for the cable, not "
              "for the missing waveform",
              serve(d, &s) == 0 && contains(&s, "INVALID_ARGUMENT") &&
              contains(&s, "cable confirmation") &&
              !contains(&s, "nothing to loop back"));

        /* The positive path the refusal must not break. */
        {
            uint32_t words[4] = { 0x00100200u, 0x00300400u, 0x00500600u,
                                  0x00700800u };
            uint8_t wire[16];

            rf_iq_bram_to_wire(words, 4, wire, sizeof wire);
            script_init(&s);
            hello(&s, 1);
            req(&s, 2, "tx.load",
                "{\"samples\":4,\"bytes\":16,\"format\":\"s16_iq_le\","
                "\"direction\":\"tx\",\"wrap\":true}");
            feed_bytes(&s, wire, sizeof wire);
            req(&s, 3, "loopback.physical.start",
                "{" LOOPBACK_ATTENUATION_ARG ",\"cable_confirmed\":true,"
                "\"samples\":4,\"ratio\":1}");
            /* The state is read from the RESPONSE, not from the session after
             * serve() returns: serve() applies the disconnect rule when the
             * connection ends, which stops what the loopback started.  The
             * response is what the operation itself landed the part in. */
            check("with a tx.load first the physical loopback still starts",
                  serve(d, &s) == 0 && contains(&s, "\"id\":3") &&
                  contains(&s, "\"ok\":true") &&
                  contains(&s, "\"state\":\"PHYSICAL_LOOPBACK\""));
        }

        rf_daemon_destroy(d);
        rf_core_destroy(core);
    }

    test_physical_capture_requires_explicit_safe_wiring();
    test_finite_capture_teardown();
    test_faulted_owned_core_is_safe_before_release();
    test_status_only();
    test_control_arm_gate();
    test_arm_permission_failures();
    test_control_validation_precedes_hardware();
    test_idelay_configure_protocol();
    test_rx_data_delay_protocol();
    test_idelay_configure_gates();
    test_idelay_configure_success_write_witness();
    test_legacy_rf_configure_idelay_flag();
    test_configure_range_with_owned_core();
    test_disarm_on_exit_and_restart();
    test_configure_idempotence_readback_and_safe_state();
    test_control_failure_taxonomy();
    test_armed_client_watchdog();
    test_watchdog_ignores_noncontrol_traffic();
    test_watchdog_checks_after_blocking_read();
    test_no_operation_is_left_pending();
    test_production_control_wiring();
    test_disarm_then_connection_end_leaves_no_pending();
    test_rearm_after_disarm_is_usable_again();
    test_explicit_rx_gain_observation();
    test_capture_timing();
    test_in_window_capture_observation();
    test_settle_wait();
    test_a_controlled_stop_is_noticed_during_a_connection();
    test_a_stop_that_lands_after_the_read_still_ends_it();
    test_backpressured_fault_reply_reaches_owned_cleanup();
    test_a_failed_wait_stops_the_source_inside_the_transaction();
    test_faulted_capture_disconnect_without_disarm();
    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    printf("REAL BOARD: NOT RUN\n");
    printf("RESULT %s\n", fail_count ? "FAIL" : "PASS");
    return fail_count ? 1 : 0;
}
