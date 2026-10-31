/* rf_daemon_main.c -- the on-board daemon executable.
 *
 * Two things live here and they are deliberately separable:
 *
 *   rf_daemon_manifest_check / rf_daemon_startup
 *       Pure (no hardware) and compiled into the host test.  The startup order is
 *       a function with the hardware side INJECTED, so "the release manifest is
 *       checked before any hardware is opened" is asserted by a test rather than
 *       read off the source: a fake opener records whether it was reached.
 *
 *   main() and the TCP transport
 *       Board-only (POSIX sockets), excluded from the host build by PLATFORM_HOST.
 *
 * The daemon never initializes the part, never selects a TX source and never
 * starts a transmission on its own: rf.init/tx.start are client requests, and an
 * uninitialized part is exactly what the launcher hands it.  The disconnect rule
 * (stop what is live, put a live TX at the ceiling attenuation) is the only
 * unattended hardware action, and it belongs to rf_daemon.c.
 */
#include "rf_daemon.h"
#include "rf_daemon_main.h"
#include "rf_core.h"
#include "rf_version.h"
#include "platform_rf.h"   /* RF_BRAM_WORDS */
#include "rf_sleep.h"
#include "rf_transport_policy.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --------------------------------------------------------- manifest reading */
/*
 * Gate fields live in the TOP-LEVEL JSON object. Merged releases also carry
 * nested software/model declarations, whose schema_version is unrelated to the
 * release schema. Scan complete JSON values, respecting strings and nesting,
 * and require each requested top-level key exactly once. Unsupported/malformed
 * syntax must fail before hardware is opened; no last-wins or nested fallback.
 */

static const char *skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
        ++p;
    return p;
}

/* These scanners validate syntax while skipping fields the daemon does not
 * consume. Escapes are accepted in values; top-level keys are plain ASCII in
 * this release contract and escaped keys are refused to prevent aliasing a
 * required key (e.g. schema_versi\u006fn) past its duplicate check. */
static int json_hex(char ch)
{
    return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') ||
           (ch >= 'A' && ch <= 'F');
}

static int skip_json_string(const char **cursor)
{
    const char *p = *cursor;
    if (*p++ != '"')
        return -1;
    while (*p && *p != '"') {
        if ((unsigned char)*p < 0x20u)
            return -1;
        if (*p++ == '\\') {
            unsigned i;
            if (!*p)
                return -1;
            if (*p == 'u') {
                ++p;
                for (i = 0; i < 4u; ++i) {
                    if (!json_hex(*p))
                        return -1;
                    ++p;
                }
            } else {
                if (!strchr("\"\\/bfnrt", *p))
                    return -1;
                ++p;
            }
        }
    }
    if (*p != '"')
        return -1;
    *cursor = p + 1;
    return 0;
}

/* Bound recursion even for a malicious manifest; release declarations need
 * only a handful of levels. No allocation or hardware callbacks here. */
static int skip_json_value(const char **cursor, unsigned depth)
{
    const char *p = skip_ws(*cursor);
    if (depth > 32u)
        return -1;
    if (*p == '"') {
        if (skip_json_string(&p) != 0)
            return -1;
    } else if (*p == '{' || *p == '[') {
        int object = *p == '{';
        char closing = object ? '}' : ']';
        p = skip_ws(p + 1);
        if (*p != closing) {
            for (;;) {
                if (object) {
                    if (skip_json_string(&p) != 0)
                        return -1;
                    p = skip_ws(p);
                    if (*p++ != ':')
                        return -1;
                }
                if (skip_json_value(&p, depth + 1u) != 0)
                    return -1;
                p = skip_ws(p);
                if (*p == closing)
                    break;
                if (*p++ != ',')
                    return -1;
                p = skip_ws(p);
            }
        }
        ++p;
    } else if (strncmp(p, "true", 4) == 0) {
        p += 4;
    } else if (strncmp(p, "false", 5) == 0) {
        p += 5;
    } else if (strncmp(p, "null", 4) == 0) {
        p += 4;
    } else {
        if (*p == '-')
            ++p;
        if (*p == '0') {
            ++p;
        } else {
            if (*p < '1' || *p > '9')
                return -1;
            do { ++p; } while (*p >= '0' && *p <= '9');
        }
        if (*p == '.') {
            ++p;
            if (*p < '0' || *p > '9')
                return -1;
            do { ++p; } while (*p >= '0' && *p <= '9');
        }
        if (*p == 'e' || *p == 'E') {
            ++p;
            if (*p == '+' || *p == '-')
                ++p;
            if (*p < '0' || *p > '9')
                return -1;
            do { ++p; } while (*p >= '0' && *p <= '9');
        }
    }
    *cursor = p;
    return 0;
}

static int find_value(const char *json, const char *key, const char **out)
{
    const char *p = skip_ws(json);
    size_t key_len = strlen(key);
    int found = 0;
    if (*p++ != '{')
        return -1;
    p = skip_ws(p);
    if (*p == '}')
        return -1;
    for (;;) {
        const char *start = p, *value;
        size_t len;
        int matches;
        if (skip_json_string(&p) != 0)
            return -1;
        len = (size_t)(p - start - 2);
        if (memchr(start + 1, '\\', len))
            return -1;
        matches = len == key_len && memcmp(start + 1, key, len) == 0;
        p = skip_ws(p);
        if (*p++ != ':')
            return -1;
        value = skip_ws(p);
        if (skip_json_value(&p, 1u) != 0)
            return -1;
        if (matches) {
            if (found)
                return -1;
            *out = value;
            found = 1;
        }
        p = skip_ws(p);
        if (*p == '}') {
            if (*skip_ws(p + 1))
                return -1;
            break;
        }
        if (*p++ != ',')
            return -1;
        p = skip_ws(p);
    }
    return found ? 0 : -1;
}

/* 0 on success.  The value must be a plain quoted string with no escapes: every
 * string this project writes into a manifest is hex, a version or a timestamp. */
static int manifest_string(const char *json, const char *key, char *out,
                           size_t cap)
{
    const char *v;
    size_t n, i;

    if (!json || !key || !out || cap == 0)
        return -1;
    if (find_value(json, key, &v) != 0)
        return -1;
    if (*v != '"')
        return -1;
    ++v;
    for (n = 0; v[n] != '\0' && v[n] != '"'; ++n) {
        if (v[n] == '\\')
            return -1;
    }
    if (v[n] != '"' || n + 1 > cap)
        return -1;
    for (i = 0; i < n; ++i)
        out[i] = v[i];
    out[n] = '\0';
    return 0;
}

static int manifest_int(const char *json, const char *key, long *out)
{
    const char *v;
    char *end;
    long value;

    if (!json || !key || !out)
        return -1;
    if (find_value(json, key, &v) != 0)
        return -1;
    if (*v < '0' || *v > '9')
        return -1;
    value = strtol(v, &end, 10);
    if (end == v)
        return -1;
    if (*skip_ws(end) != ',' && *skip_ws(end) != '}')
        return -1;
    *out = value;
    return 0;
}

/* 64 lowercase hexadecimal characters -- the shape the release manifest spec
 * records for a hash.  Deliberately not "any non-empty string": a short, mixed
 * case or non-hex value is a manifest this reader does not understand, and an
 * identity report built from one would be a report of the wrong thing. */
static int is_sha256_hex(const char *value)
{
    size_t i;

    if (strlen(value) != (size_t)RF_SHA256_HEX_LEN)
        return 0;
    for (i = 0; i < (size_t)RF_SHA256_HEX_LEN; ++i) {
        char ch = value[i];

        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
            return 0;
    }
    return 1;
}

int rf_daemon_manifest_check(const char *json, rf_release_identity_t *identity,
                             char *why, size_t why_cap)
{
    char version[64];
    long major = 0;

    if (!identity) {
        snprintf(why, why_cap, "no identity was supplied to receive the "
                 "release manifest's hashes");
        return -1;
    }
    if (manifest_string(json, "daemon_version", version, sizeof version) != 0) {
        snprintf(why, why_cap, "the release manifest has no usable "
                 "'daemon_version'; refusing before touching hardware");
        return -1;
    }
    if (manifest_int(json, "protocol_major", &major) != 0) {
        snprintf(why, why_cap, "the release manifest has no usable "
                 "'protocol_major'; refusing before touching hardware");
        return -1;
    }
    if (strcmp(version, RF_DAEMON_VERSION) != 0) {
        snprintf(why, why_cap, "daemon version mismatch: the manifest was built "
                 "for %s, this binary is %s", version, RF_DAEMON_VERSION);
        return -1;
    }
    if (major != (long)RF_PROTOCOL_MAJOR) {
        snprintf(why, why_cap, "protocol major mismatch: the manifest says %ld, "
                 "this binary speaks %u", major, (unsigned)RF_PROTOCOL_MAJOR);
        return -1;
    }
    /* The identity fields come after the version gates: a manifest from a
     * different release is refused for that reason first, which is the reason
     * that tells an operator which artifact is stale.  Required rather than
     * optional -- an identity report that can come back empty is not one, and
     * the manifest contract already carries both fields. */
    if (manifest_string(json, "bit_sha256", identity->bit_sha256,
                        sizeof identity->bit_sha256) != 0 ||
        !is_sha256_hex(identity->bit_sha256)) {
        snprintf(why, why_cap, "the release manifest has no usable "
                 "'bit_sha256'; refusing before touching hardware");
        return -1;
    }
    if (manifest_string(json, "hwh_sha256", identity->hwh_sha256,
                        sizeof identity->hwh_sha256) != 0 ||
        !is_sha256_hex(identity->hwh_sha256)) {
        snprintf(why, why_cap, "the release manifest has no usable "
                 "'hwh_sha256'; refusing before touching hardware");
        return -1;
    }
    return 0;
}

static int is_release_id(const char *value)
{
    size_t i;
    if (strlen(value) != 32u)
        return 0;
    for (i = 0; i < 32u; ++i) {
        char ch = value[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
            return 0;
    }
    return 1;
}

int rf_daemon_merged_manifest_check(const char *json,
                                    rf_release_identity_t *identity,
                                    char *why, size_t why_cap)
{
    char version[64];
    long schema, protocol;
    if (!identity || !json || !why || !why_cap)
        return -1;
    memset(identity, 0, sizeof *identity);
    if (manifest_int(json, "schema_version", &schema) != 0 || schema != 1 ||
        manifest_int(json, "protocol_version", &protocol) != 0 ||
        protocol != (long)RF_PROTOCOL_MAJOR) {
        snprintf(why, why_cap, "merged release schema/protocol mismatch");
        return -1;
    }
    if (manifest_string(json, "daemon_version", version, sizeof version) != 0 ||
        strcmp(version, RF_DAEMON_VERSION) != 0) {
        snprintf(why, why_cap, "merged release daemon_version mismatch");
        return -1;
    }
    if (manifest_string(json, "release_id", identity->release_id,
                        sizeof identity->release_id) != 0 ||
        !is_release_id(identity->release_id) ||
        manifest_string(json, "bit_sha256", identity->bit_sha256,
                        sizeof identity->bit_sha256) != 0 ||
        !is_sha256_hex(identity->bit_sha256) ||
        manifest_string(json, "hwh_sha256", identity->hwh_sha256,
                        sizeof identity->hwh_sha256) != 0 ||
        !is_sha256_hex(identity->hwh_sha256)) {
        snprintf(why, why_cap, "merged release identity/hash invalid");
        return -1;
    }
    return 0;
}

int rf_daemon_manifest_check_file(const char *path,
                                  rf_release_identity_t *identity,
                                  char *why, size_t why_cap)
{
    FILE *file;
    char *text;
    long size;
    int rc;

    if (!path) {
        snprintf(why, why_cap, "no release manifest was given (--manifest)");
        return -1;
    }
    file = fopen(path, "rb");
    if (!file) {
        snprintf(why, why_cap, "cannot open the release manifest %s", path);
        return -1;
    }
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) < 0 ||
        fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        snprintf(why, why_cap, "cannot size the release manifest %s", path);
        return -1;
    }
    text = (char *)malloc((size_t)size + 1);
    if (!text) {
        fclose(file);
        snprintf(why, why_cap, "out of memory reading %s", path);
        return -1;
    }
    if (fread(text, 1, (size_t)size, file) != (size_t)size) {
        free(text);
        fclose(file);
        snprintf(why, why_cap, "short read on the release manifest %s", path);
        return -1;
    }
    text[size] = '\0';
    fclose(file);
    rc = rf_daemon_manifest_check(text, identity, why, why_cap);
    free(text);
    return rc;
}

int rf_daemon_merged_manifest_check_file(const char *path,
                                         rf_release_identity_t *identity,
                                         char *why, size_t why_cap)
{
    FILE *file;
    char *text;
    long size;
    int rc;
    if (!path || !(file = fopen(path, "rb"))) {
        snprintf(why, why_cap, "cannot open merged release manifest");
        return -1;
    }
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) < 0 ||
        size > 65536 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        snprintf(why, why_cap, "invalid merged manifest size");
        return -1;
    }
    text = malloc((size_t)size + 1u);
    if (!text) {
        fclose(file);
        return -1;
    }
    if (fread(text, 1, (size_t)size, file) != (size_t)size) {
        fclose(file);
        free(text);
        return -1;
    }
    fclose(file);
    text[size] = '\0';
    rc = rf_daemon_merged_manifest_check(text, identity, why, why_cap);
    free(text);
    return rc;
}

int rf_daemon_release_path_matches(const char *path, const char *release_id)
{
    const char *leaf = "/merged_release_manifest.json";
    const char *last, *dir;
    size_t n;
    if (!path || !release_id || !is_release_id(release_id))
        return 0;
    n = strlen(path);
    if (n <= strlen(leaf) || strcmp(path + n - strlen(leaf), leaf) != 0)
        return 0;
    last = path + n - strlen(leaf);
    dir = last;
    while (dir > path && dir[-1] != '/')
        --dir;
    return (size_t)(last - dir) == 32u &&
           strncmp(dir, release_id, 32u) == 0 &&
           dir >= path + 10u &&
           strncmp(dir - 10u, "/releases/", 10u) == 0;
}

int rf_daemon_active_manifest_path(const char *marker, char *path,
                                   size_t cap, char *why, size_t why_cap)
{
    FILE *file;
    char line[1024];
    size_t n;
    const char *leaf = "/merged_release_manifest.json";
    if (!marker || !path || !cap || !(file = fopen(marker, "rb"))) {
        snprintf(why, why_cap, "cannot open active-release marker");
        return -1;
    }
    if (!fgets(line, sizeof line, file) || fgetc(file) != EOF) {
        fclose(file);
        snprintf(why, why_cap, "active-release marker is incomplete");
        return -1;
    }
    fclose(file);
    n = strcspn(line, "\r\n");
    line[n] = '\0';
    if (n < 2u || line[0] != '/' || strstr(line, "/../") ||
        strstr(line, "/./") || line[n - 1] == '/' ||
        n + strlen(leaf) + 1u > cap) {
        snprintf(why, why_cap, "active-release path is invalid");
        return -1;
    }
    snprintf(path, cap, "%s%s", line, leaf);
    return 0;
}

/* ---------------------------------------------------------------- startup */

int rf_daemon_startup(const char *manifest_path,
                      int (*open_core)(void *ctx, rf_core_t **out), void *ctx,
                      rf_core_t **out_core, rf_release_identity_t *out_identity,
                      char *why, size_t why_cap)
{
    rf_core_t *core = NULL;

    if (out_core)
        *out_core = NULL;
    if (!open_core) {
        snprintf(why, why_cap, "no core opener was supplied");
        return -1;
    }
    if (!out_identity) {
        snprintf(why, why_cap, "no identity output was supplied");
        return -1;
    }
    /* Everything that can refuse must refuse HERE, before the opener runs: the
     * version gate exists precisely so a mismatched release cannot put a
     * register write on the bus. */
    if (rf_daemon_manifest_check_file(manifest_path, out_identity, why,
                                      why_cap) != 0)
        return -1;
    if (open_core(ctx, &core) != 0 || !core) {
        snprintf(why, why_cap, "the RF core could not be opened "
                 "(manifest accepted, so this is a hardware/driver failure)");
        return -1;
    }
    /* Deliberately no rf_core_init() and no source selection: the part stays
     * uninitialized and silent until a client asks for rf.init, and a client's
     * rf.init must name its attenuation explicitly (see rf_daemon.c). */
    if (out_core)
        *out_core = core;
    return 0;
}

/* Strict numeric argument: -1 on anything malformed, which the caller maps to
 * the CLI's exit 2.  A silently-zeroed port binds an ephemeral port and looks
 * like "the daemon is up but the client cannot connect".
 *
 * Non-static and declared in rf_daemon_main.h because the host test compiles this
 * translation unit separately from rf_daemon_main.c and links the two: a static
 * function could not be called from that test, and a copy inside the test would
 * be a different function from the one that runs on the board. */
int parse_uint(const char *text, unsigned min, unsigned max, unsigned *out)
{
    char *end;
    unsigned long value;

    if (!text || *text == '\0')
        return -1;
    value = strtoul(text, &end, 10);
    if (*end != '\0' || value < min || value > max)
        return -1;
    *out = (unsigned)value;
    return 0;
}

/* The control-arming token check, deliberately PORTABLE and outside the
 * #ifndef PLATFORM_HOST block below: that block is not compiled into the host test, and
 * a token check no test exercises is exactly the piece of the gate that would rot
 * unnoticed.  The token file is provisioned by the operator OUTSIDE every release and
 * manifest, so installing or starting a release cannot arm control; anything
 * unreadable, empty, or different is a refusal (fail-closed).
 *
 * The comparison is a plain strcmp: the token never leaves the board, the request
 * arrives over a loopback socket, and this is a provisioning gate rather than a
 * network-facing secret.  Better to say what it is than to imply more. */
int rf_control_token_check_file(const char *path, const char *token)
{
    char buf[128];
    FILE *f;
    size_t n;

    if (!path || !token || token[0] == '\0')
        return -1;
    f = fopen(path, "rb");
    if (!f)
        return -1;
    n = fread(buf, 1, sizeof buf - 1u, f);
    fclose(f);
    if (n == 0u)
        return -1;
    buf[n] = '\0';
    while (n > 0u && (buf[n - 1u] == '\n' || buf[n - 1u] == '\r'))
        buf[--n] = '\0';
    if (n == 0u)
        return -1;
    return strcmp(buf, token) == 0 ? 0 : -1;
}

#ifndef PLATFORM_HOST
/* ------------------------------------------------- board-only: TCP transport */

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define RF_DEFAULT_PORT 5000u
/* A client that stops talking must not hold the single lease forever: on this
 * deadline the transport reports RF_TRANSPORT_TIMEOUT, which ends the connection
 * and applies the disconnect rule (stop, then ceiling attenuation if TX was live). */
#define RF_CLIENT_TIMEOUT_S 30

/* How long the idle accept() waits before the loop gives the armed-client watchdog a
 * turn.  Shorter than the watchdog's own timeout, so the disarm lands on schedule
 * rather than up to one tick late. */
#define RF_WATCHDOG_TICK_S 1

/* The termination flag, the self-pipe that makes it WAITABLE, and the reader
 * conn_read uses.  Declared up here because conn_read (below) polls the pipe;
 * the handler and reader are defined next to their comment further down.  The
 * self-pipe exists so a signal delivered between the flag check and the blocking
 * call -- or between two fragments of one line -- is not lost for 30 s: the
 * handler writes a byte (write() is async-signal-safe) and conn_read polls the
 * socket AND the pipe. */
static volatile sig_atomic_t rf_terminate_requested;
static int rf_stop_pipe[2] = { -1, -1 };
/* Only this sig_atomic_t descriptor is accessed from the signal handler. */
static volatile sig_atomic_t rf_stop_write_fd = -1;
static rf_stop_channel_t rf_stop_channel;
static struct sigaction rf_old_stop_action[2];
static int board_should_stop(void);

static int conn_read(void *ctx, void *buf, size_t len)
{
    int fd = *(int *)ctx;

    for (;;) {
        struct pollfd fds[2];
        int n;

        /* Checked here as well as in the serve loop: a signal can land at any
         * point, and this is the call that would otherwise block. */
        if (board_should_stop())
            return 0;
        fds[0].fd = fd;
        fds[0].events = POLLIN;
        fds[0].revents = 0;
        fds[1].fd = rf_stop_pipe[0];
        fds[1].events = POLLIN;
        fds[1].revents = 0;
        n = poll(fds, (nfds_t)(rf_stop_pipe[0] >= 0 ? 2 : 1), RF_CLIENT_TIMEOUT_S * 1000);
        if (n < 0) {
            if (errno == EINTR)
                continue;               /* loop re-checks the flag first */
            return -1;
        }
        if (n == 0)
            return RF_TRANSPORT_TIMEOUT;
        if (fds[1].revents & (POLLIN | POLLHUP | POLLERR)) {
            char drain[64];
            while (read(rf_stop_pipe[0], drain, sizeof drain) > 0)
                ;                       /* non-blocking: drains then EAGAINs */
            return 0;                   /* the ordinary clean-close path */
        }
        {
            ssize_t got = recv(fd, buf, len, 0);
            if (got > 0)
                return (int)got;
            if (got == 0)
                return 0;               /* the client closed */
            if (errno == EINTR)
                continue;               /* loop re-checks the flag first */
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                return RF_TRANSPORT_TIMEOUT;
            return -1;
        }
    }
}

/* The policy owns the absolute deadline. These POSIX adapters only translate
 * syscall results and never call the capture/trace clock callback. */
static int send_should_stop(void *ctx)
{
    (void)ctx;
    return board_should_stop();
}

static int send_now_ns(void *ctx, uint64_t *out)
{
    struct timespec ts;
    (void)ctx;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return -1;
    *out = (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
    return 0;
}

static int64_t send_nonblocking(void *ctx, const void *buf, size_t len)
{
    ssize_t n = send(*(int *)ctx, buf, len, MSG_NOSIGNAL | MSG_DONTWAIT);
    if (n >= 0)
        return (int64_t)n;
    if (errno == EINTR)
        return RF_SEND_INTERRUPTED;
    if (errno == EAGAIN || errno == EWOULDBLOCK)
        return RF_SEND_AGAIN;
    return RF_SEND_ERROR;
}

static int wait_writable_stop(void *ctx, unsigned timeout_ms)
{
    struct pollfd fds[2];
    int result;
    fds[0].fd = *(int *)ctx;
    fds[0].events = POLLOUT;
    fds[0].revents = 0;
    fds[1].fd = rf_stop_pipe[0];
    fds[1].events = POLLIN;
    fds[1].revents = 0;
    if (board_should_stop())
        return RF_WAIT_STOP;
    result = poll(fds, 2, (int)timeout_ms);
    if (board_should_stop())
        return RF_WAIT_STOP;
    if (result < 0)
        return errno == EINTR ? RF_WAIT_INTERRUPTED : RF_WAIT_ERROR;
    if (result == 0)
        return RF_WAIT_TIMEOUT;
    /* Stop dominates socket readiness/error, including a signal in the gap
     * between the stop check and poll. No drain is needed on this exit path. */
    if (fds[1].revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL))
        return RF_WAIT_STOP;
    if (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL))
        return RF_WAIT_ERROR;
    return (fds[0].revents & POLLOUT) ? RF_WAIT_READY : RF_WAIT_ERROR;
}

static int conn_write_all(void *ctx, const void *buf, size_t len)
{
    const rf_write_ops_t ops = {
        ctx, send_should_stop, send_now_ns, send_nonblocking, wait_writable_stop
    };
    /* Transport response budget only; unrelated to capture/settling timing. */
    return rf_transport_write_all(&ops, buf, len, RF_SEND_TIMEOUT_MS);
}

static void conn_close(void *ctx)
{
    int fd = *(int *)ctx;
    if (fd >= 0)
        close(fd);
    *(int *)ctx = -1;
}

/* A frame that reports a timestamp must report one it actually got, and
 * rf_daemon_create() refuses a null clock rather than letting the daemon call
 * through a null pointer on the first rx.capture. */
/* The operator-provisioned arming token.  The path is fixed here and pinned by a
 * criterion; the file is deliberately part of no release, manifest or unit, so no
 * install can create or change it -- which is what makes "installing the release does
 * not arm control" true rather than aspirational. */
static const char RF_CONTROL_TOKEN_PATH[] = "/etc/rf-control.token";

static int verify_control_token_file(const char *token)
{
    return rf_control_token_check_file(RF_CONTROL_TOKEN_PATH, token);
}

/* Open the WRITE-capable RF hardware.  This is the ONLY place in this file that maps
 * the O_RDWR aperture, and rf_daemon.c calls it from exactly one site -- op_control_arm,
 * after the token verifier accepted -- so "status-only mode never opens it" is a
 * property of the call graph rather than of a runtime flag.  rf_core_open() is what
 * reaches platform_rf.c's open("/dev/mem", O_RDWR | O_SYNC) and its PROT_READ|PROT_WRITE
 * mmap.
 *
 * `open` is PLATFORM bring-up only and must not touch the GPIO aperture (rf_core.c
 * says why: gpio_init() loads the whole output register, and its RESETB bit would
 * assert reset and disable the part).  Programming the part stays `rf.init`'s job, so
 * rf_core_init is deliberately NOT called here. */
static int board_acquire_control_core(void *ctx, rf_core_t **out_core)
{
    rf_core_open_args_t args = { false, false, NULL };
    rf_core_t *core = NULL;

    (void)ctx;
    if (!out_core)
        return RF_ERR_INVALID_ARGUMENT;
    *out_core = NULL;
    if (rf_core_create(&args, &core) != RF_ERR_OK)
        return RF_ERR_HARDWARE_IO;
    if (rf_core_open(core, &args) != RF_ERR_OK) {
        rf_core_destroy(core);
        return RF_ERR_HARDWARE_IO;
    }
    *out_core = core;
    return RF_ERR_OK;
}

/* The pair of board_acquire_control_core.  rf_core_close() releases the mmap, the
 * SPI/GPIO callbacks and the trace buffer, and drops the process-global core
 * singleton.  It deliberately does NOT stop the part, so the daemon brings the part
 * to rest FIRST (rf_daemon_disarm -> release_owned_core) and only then calls this;
 * without that order the part would be left enabled with nobody able to command it. */
static void board_release_control_core(void *ctx, rf_core_t *core)
{
    (void)ctx;
    if (!core)
        return;
    rf_core_close(core);
    rf_core_destroy(core);
}

static uint64_t monotonic_ns(void)
{
    struct timespec ts;

    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "usage: %s --active-release <marker> [--port N] "
            "[--bind ADDR] [--max-samples N] [--mode status-only]\n"
            "  --bind defaults to 127.0.0.1 on purpose: the control protocol has\n"
            "  no authentication, so exposing it is an explicit decision (the\n"
            "  systemd unit passes the management address).\n", argv0);
}

/* A termination signal must not be able to leave control armed, and the handler must
 * not do the disarming itself: a signal arrives at an arbitrary point in the accept
 * loop, so the handler only sets a flag (the one thing a handler may safely do with
 * process state) and the loop below runs the same disarm-and-audit path every other
 * exit runs.  SIGTERM is what `systemctl stop` and a unit restart send; SIGINT is what
 * an operator at a console sends.  Both mean "this process is going away".
 *
 * The flag alone is not enough: it is only observed at points the loop happens to
 * return to, so the handler also pokes the self-pipe (declared above, next to
 * conn_read) to wake a blocked receive immediately. */
static int board_should_stop(void)
{
    return rf_terminate_requested != 0;
}

static void on_terminate(int signo)
{
    int saved = errno;

    (void)signo;
    rf_terminate_requested = 1;
    if (rf_stop_write_fd >= 0) {
        /* Both ends are non-blocking, so a signal storm cannot block the handler
         * on a full pipe.  The flag above is the authority either way. */
        ssize_t ignored = write((int)rf_stop_write_fd, "s", 1);
        (void)ignored;
    }
    errno = saved;
}

/* Checked startup and rollback use the SAME portable policy host tests run.
 * Signal install saves the previous disposition so partial initialization and
 * normal teardown cannot leave an installed handler owning closed fds. */
static int stop_make_pipe(void *ctx, int fd[2])
{
    (void)ctx;
    return pipe(fd);
}

static int stop_get_flags(void *ctx, int fd)
{
    (void)ctx;
    return fcntl(fd, F_GETFL, 0);
}

static int stop_set_nonblocking(void *ctx, int fd, int flags)
{
    (void)ctx;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int stop_install_signal(void *ctx, unsigned which)
{
    struct sigaction sa;
    (void)ctx;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_terminate;
    if (sigemptyset(&sa.sa_mask) != 0)
        return -1;
    /* No SA_RESTART: the flag and self-pipe are observed across interruptions. */
    return sigaction(which == 0u ? SIGTERM : SIGINT, &sa,
                     &rf_old_stop_action[which]);
}

static void stop_restore_signal(void *ctx, unsigned which)
{
    (void)ctx;
    (void)sigaction(which == 0u ? SIGTERM : SIGINT,
                    &rf_old_stop_action[which], NULL);
}

static void stop_publish(void *ctx, int read_fd, int write_fd)
{
    (void)ctx;
    rf_stop_write_fd = (sig_atomic_t)write_fd;
    rf_stop_pipe[0] = read_fd;
    rf_stop_pipe[1] = write_fd;
}

static void stop_close_fd(void *ctx, int fd)
{
    (void)ctx;
    (void)close(fd);
}

static const rf_stop_ops_t rf_stop_ops = {
    NULL, stop_make_pipe, stop_get_flags, stop_set_nonblocking,
    stop_install_signal, stop_restore_signal, stop_publish, stop_close_fd
};

int main(int argc, char **argv)
{
    const char *active_release = NULL;
    char manifest_path[1200];
    const char *bind_addr = "127.0.0.1";
    unsigned port = RF_DEFAULT_PORT;
    unsigned max_samples = RF_BRAM_WORDS;
    rf_release_identity_t identity;
    rf_daemon_config_t config;
    rf_daemon_t *daemon = NULL;
    char why[256];
    struct sockaddr_in addr;
    /* Why the accept loop ended, for the disarm's audit line.  A default rather than an
     * uninitialized value: an exit path that forgets to set it must still produce a
     * usable line instead of reading uninitialized memory. */
    const char *exit_reason = "daemon stopping";
    int listener, one = 1, i, fd;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--active-release") == 0 && i + 1 < argc)
            active_release = argv[++i];
        else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            if (parse_uint(argv[++i], 1u, 65535u, &port) != 0) {
                fprintf(stderr, "%s: --port must be 1..65535\n", argv[0]);
                return 2;
            }
        } else if (strcmp(argv[i], "--bind") == 0 && i + 1 < argc)
            bind_addr = argv[++i];
        else if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            if (strcmp(argv[++i], "status-only") != 0) {
                usage(argv[0]);
                return 2;
            }
        } else if (strcmp(argv[i], "--max-samples") == 0 && i + 1 < argc) {
            if (parse_uint(argv[++i], 1u, RF_BRAM_WORDS, &max_samples) != 0) {
                fprintf(stderr, "%s: --max-samples must be 1..%u\n", argv[0],
                        (unsigned)RF_BRAM_WORDS);
                return 2;
            }
        }
        else {
            usage(argv[0]);
            return 2;
        }
    }
    why[0] = '\0';
    if (rf_daemon_active_manifest_path(active_release, manifest_path,
                                       sizeof manifest_path, why, sizeof why) != 0 ||
        rf_daemon_merged_manifest_check_file(manifest_path, &identity, why,
                                             sizeof why) != 0 ||
        !rf_daemon_release_path_matches(manifest_path, identity.release_id)) {
        if (why[0] == '\0')
            snprintf(why, sizeof why, "active release directory does not match release_id");
        fprintf(stderr, "rf_daemon: %s\n", why);
        return 1;
    }

    /* Hard startup prerequisite, before status mapping, daemon creation,
     * listener or any request that could acquire write-capable control. */
    if (rf_stop_channel_init(&rf_stop_channel, &rf_stop_ops) != 0) {
        fprintf(stderr, "rf_daemon: cannot establish nonblocking stop channel\n");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);

    memset(&config, 0, sizeof config);
    config.mode = RF_DAEMON_STATUS_ONLY;
    config.pl_reader = rf_pl_status_reader_open();
    if (!config.pl_reader) {
        /* BOARD-ONLY: main() is not compiled under PLATFORM_HOST, so no host
         * test executes this branch (same caveat as the `return 2` above).
         * Exiting rather than listening is the point: a daemon that answers
         * protocol.hello and then reports io_ok=false looks healthy while RF is
         * unreachable, and the unit would never flap.  A failed start is the
         * honest "RF offline", and Restart=always retries once the owner has
         * published a new release. */
        fprintf(stderr,
                "rf_daemon: cannot map the read-only PL status window\n");
        rf_stop_channel_cleanup(&rf_stop_channel, &rf_stop_ops);
        return 1;
    }
    config.max_samples = max_samples;
    config.port = port;
    config.now_ns = monotonic_ns;
    /* The wait adapter lives in rf_sleep.c, OUTSIDE this board-only region, so the
     * sleep the board runs is the one the host suite compiles and exercises.
     * Keeping it here meant PLATFORM_HOST excluded it entirely and "the wait
     * completes or reports failure" was asserted about code no host build saw.
     * Wiring it is what makes an opt-in settling wait reachable at all: NULL
     * would refuse every request that asks for one. */
    config.sleep_us = rf_sleep_default;
    /* A controlled stop has to be visible while a connection is LIVE, not only
     * between connections: see board_should_stop and conn_read. */
    config.should_stop = board_should_stop;
    /* Control is off unless a request presents a token the operator provisioned.  The
     * daemon is never armed by starting it, and this assignment is the only wiring
     * between the token file and the gate. */
    config.verify_control_token = verify_control_token_file;
    /* The write-capable hardware is injected, never opened at startup: rf_daemon.c
     * calls acquire exactly once, after an accepted token, and release on disarm. */
    config.control_hw.ctx = NULL;
    config.control_hw.acquire = board_acquire_control_core;
    config.control_hw.release = board_release_control_core;
    config.identity = identity;
    if (rf_daemon_create(&config, &daemon) != 0) {
        fprintf(stderr, "rf_daemon: cannot create the daemon\n");
        rf_pl_status_reader_close(config.pl_reader);
        rf_stop_channel_cleanup(&rf_stop_channel, &rf_stop_ops);
        return 1;
    }
    /* Read-only until a request arms it: the status reader maps the PL status window
     * O_RDONLY, and the O_RDWR aperture is reached only through
     * board_acquire_control_core, which only a successful control.arm reaches.
     * Nothing is opened here. */

    listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) {
        fprintf(stderr, "rf_daemon: socket: %s\n", strerror(errno));
        rf_daemon_destroy(daemon);
        rf_pl_status_reader_close(config.pl_reader);
        rf_stop_channel_cleanup(&rf_stop_channel, &rf_stop_ops);
        return 1;
    }
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = inet_addr(bind_addr);
    if (bind(listener, (struct sockaddr *)&addr, sizeof addr) != 0 ||
        listen(listener, 1) != 0) {
        fprintf(stderr, "rf_daemon: bind/listen on %s:%u: %s\n", bind_addr, port,
                strerror(errno));
        close(listener);
        rf_daemon_destroy(daemon);
        rf_pl_status_reader_close(config.pl_reader);
        rf_stop_channel_cleanup(&rf_stop_channel, &rf_stop_ops);
        return 1;
    }
    printf("rf_daemon %s (protocol %u.%u) listening on %s:%u\n",
           RF_DAEMON_VERSION, (unsigned)RF_PROTOCOL_MAJOR,
           (unsigned)RF_PROTOCOL_MINOR, bind_addr, port);
    fflush(stdout);

    {
        struct timeval tick;

        tick.tv_sec = RF_WATCHDOG_TICK_S;
        tick.tv_usec = 0;
        /* Bound the wait in accept() so the watchdog gets a turn while the daemon is
         * idle.  Without it accept() blocks until the NEXT client arrives, so an armed
         * daemon whose client vanished would stay armed exactly as long as nobody
         * connected -- the case this step exists for.  Reuses the mechanism the
         * per-connection socket already uses for its own read timeout. */
        (void)setsockopt(listener, SOL_SOCKET, SO_RCVTIMEO, &tick, sizeof tick);
    }

    for (;;) {
        struct timeval tv;
        rf_transport_t transport;

        /* Checked before the accept so a signal that arrives while the daemon is idle
         * ends the loop instead of being absorbed by it. */
        if (rf_terminate_requested) {
            exit_reason = "termination signal";
            break;
        }
        fd = accept(listener, NULL, NULL);
        if (fd < 0) {
            if (errno == EINTR) {
                /* The handler only set the flag, so this is where a termination
                 * becomes a shutdown rather than another retry. */
                if (rf_terminate_requested) {
                    exit_reason = "termination signal";
                    break;
                }
                continue;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                /* The bounded accept() timed out: nothing has connected, so this is
                 * the idle moment the watchdog is for. */
                (void)rf_daemon_watchdog_tick(daemon, config.now_ns());
                continue;
            }
            fprintf(stderr, "rf_daemon: accept: %s\n", strerror(errno));
            exit_reason = "fatal error while accepting a connection";
            break;
        }
        tv.tv_sec = RF_CLIENT_TIMEOUT_S;
        tv.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        transport.ctx = &fd;
        transport.read = conn_read;
        transport.write_all = conn_write_all;
        transport.close = conn_close;
        (void)rf_daemon_serve(daemon, &transport);
        /* A connection may end with the daemon still armed -- that is the contract --
         * so give the watchdog its turn now instead of waiting out the next tick. */
        (void)rf_daemon_watchdog_tick(daemon, config.now_ns());
    }
    /* Every exit from the loop above -- a termination signal or a fatal accept error --
     * lands here, and this is the only place control is turned off at exit.  Disarming
     * is a statement about this process only: it does not touch the PL, so it cannot
     * surprise a client, and the audit line names the path that ended the daemon. */
    (void)rf_daemon_disarm(daemon, exit_reason);
    close(listener);
    rf_stop_channel_cleanup(&rf_stop_channel, &rf_stop_ops);
    rf_daemon_destroy(daemon);
    rf_pl_status_reader_close(config.pl_reader);
    return 1;
}
#endif /* !PLATFORM_HOST */
