/* rf_protocol.c -- restricted NDJSON parser/serializer.  See rf_protocol.h for
 * why nothing here may touch hardware.
 *
 * The parser is strict on purpose.  A protocol that accepts anything is one where
 * a mistyped field silently becomes a default, and a default attenuation is how a
 * transmitter comes up at full power.  So: unknown fields, missing fields,
 * duplicate fields, wrong value types and trailing bytes are all errors.
 */
#include "rf_protocol.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

const char *rf_proto_code_name(rf_proto_code_t code)
{
    switch (code) {
    case RF_PROTO_E_NONE:             return "OK";
    case RF_PROTO_E_INVALID_ARGUMENT: return "INVALID_ARGUMENT";
    case RF_PROTO_E_INVALID_STATE:    return "INVALID_STATE";
    case RF_PROTO_E_BUSY:             return "BUSY";
    case RF_PROTO_E_HARDWARE_IO:      return "HARDWARE_IO";
    case RF_PROTO_E_SPI_ERROR:        return "SPI_ERROR";
    case RF_PROTO_E_TIMEOUT:          return "TIMEOUT";
    case RF_PROTO_E_OVERFLOW:         return "OVERFLOW";
    case RF_PROTO_E_UNSUPPORTED:      return "UNSUPPORTED";
    case RF_PROTO_E_VERSION_MISMATCH: return "VERSION_MISMATCH";
    case RF_PROTO_E_PERMISSION:       return "PERMISSION";
    }
    return "UNKNOWN";
}

/* ------------------------------------------------------------------ scanners */

static const char *skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t')
        ++p;
    return p;
}

/* A string with no escape sequences, because the protocol declares none.  A
 * backslash is rejected rather than interpreted: quietly accepting "\n" here
 * would mean the wire form and the parsed form disagree. */
static int scan_string(const char **pp, char *out, size_t cap)
{
    const char *p = *pp;
    size_t n = 0;

    if (*p != '"')
        return -1;
    ++p;
    while (*p && *p != '"') {
        if (*p == '\\' || (unsigned char)*p < 0x20)
            return -1;
        if (n + 1 >= cap)
            return -1;
        out[n++] = *p++;
    }
    if (*p != '"')
        return -1;
    ++p;
    out[n] = '\0';
    *pp = p;
    return 0;
}

/* Integers only: no sign for callers that forbid it, no fraction, no exponent. */
static int scan_number(const char **pp, long long *out, int allow_negative)
{
    const char *p = *pp;
    int neg = 0;
    int digits = 0;
    unsigned long long v = 0;

    if (*p == '-') {
        if (!allow_negative)
            return -1;
        neg = 1;
        ++p;
    }
    while (*p >= '0' && *p <= '9') {
        v = v * 10ull + (unsigned long long)(*p - '0');
        if (v > 0x7FFFFFFFFFFFFFFFull)
            return -1;
        ++p;
        ++digits;
    }
    if (!digits || *p == '.' || *p == 'e' || *p == 'E')
        return -1;
    *out = neg ? -(long long)v : (long long)v;
    *pp = p;
    return 0;
}

static int scan_literal(const char **pp, const char *lit)
{
    size_t n = strlen(lit);
    if (strncmp(*pp, lit, n) != 0)
        return -1;
    *pp += n;
    return 0;
}

/* One value that the protocol declares: number, true, false or string.  Nested
 * objects and arrays are not part of the wire format and are rejected here. */
static int scan_value(const char **pp)
{
    const char *p = *pp;

    if (*p == '"') {
        const char *q = p;
        char scratch[2];
        /* Reuse scan_string with a throwaway buffer but a real length bound by
         * scanning to the closing quote first, so a very long string is not
         * rejected merely for not fitting a local array. */
        ++q;
        while (*q && *q != '"') {
            if (*q == '\\' || (unsigned char)*q < 0x20)
                return -1;
            ++q;
        }
        if (*q != '"')
            return -1;
        *pp = q + 1;
        (void)scratch;
        return 0;
    }
    if (*p == 't' || *p == 'f') {
        long long dummy;
        if (scan_literal(&p, "true") == 0) { *pp = p; return 0; }
        if (scan_literal(&p, "false") == 0) { *pp = p; return 0; }
        (void)dummy;
        return -1;
    }
    {
        long long v;
        if (scan_number(&p, &v, 1) != 0)
            return -1;
        *pp = p;
        return 0;
    }
}

/* A flat object: {"k":v,...}.  Copied verbatim so the typed getters can look a
 * key up later, and validated here so that malformed args never reach an op. */
static int scan_object(const char **pp, char *out, size_t cap)
{
    const char *p = *pp;
    size_t n = 0;
    int depth = 0;

    if (*p != '{')
        return -1;
    for (;;) {
        char c = *p;
        if (c == '\0')
            return -1;
        if (c == '"') {
            if (n + 1 >= cap) return -1;
            out[n++] = *p++;
            while (*p && *p != '"') {
                if (*p == '\\' || (unsigned char)*p < 0x20)
                    return -1;
                if (n + 1 >= cap) return -1;
                out[n++] = *p++;
            }
            if (*p != '"') return -1;
            if (n + 1 >= cap) return -1;
            out[n++] = *p++;
            continue;
        }
        if (c == '{') ++depth;
        else if (c == '}') --depth;
        if (n + 1 >= cap) return -1;
        out[n++] = *p++;
        if (depth == 0) break;
    }
    out[n] = '\0';
    *pp = p;
    return 0;
}

/* Validate a copied flat object without needing a second copy. */
static int object_validate(const char *obj)
{
    const char *p = skip_ws(obj);
    char key[16];

    if (*p != '{')
        return -1;
    p = skip_ws(p + 1);
    if (*p == '}')
        return 0;                        /* empty object */
    for (;;) {
        if (scan_string(&p, key, sizeof key) != 0)
            return -1;
        p = skip_ws(p);
        if (*p != ':')
            return -1;
        p = skip_ws(p + 1);
        if (scan_value(&p) != 0)
            return -1;
        p = skip_ws(p);
        if (*p == ',') { p = skip_ws(p + 1); continue; }
        if (*p == '}') return 0;
        return -1;
    }
}

/* -------------------------------------------------------------- parse entry */

int rf_proto_parse_request(const char *line, size_t len,
                           rf_proto_request_t *out, rf_proto_code_t *code)
{
    const char *p;
    int seen_id = 0, seen_op = 0, seen_args = 0;

    if (code)
        *code = RF_PROTO_E_INVALID_ARGUMENT;
    if (!line || !out)
        return -1;
    memset(out, 0, sizeof *out);
    if (len == 0)
        return -1;
    if (len > RF_PROTO_LINE_MAX) {
        if (code)
            *code = RF_PROTO_E_OVERFLOW;
        return -1;
    }
    p = skip_ws(line);
    if (*p != '{')
        return -1;
    ++p;
    for (;;) {
        char key[16];

        p = skip_ws(p);
        if (*p == '}') { ++p; break; }
        if (*p != '"')
            return -1;
        if (scan_string(&p, key, sizeof key) != 0)
            return -1;
        p = skip_ws(p);
        if (*p != ':')
            return -1;
        p = skip_ws(p + 1);

        if (strcmp(key, "id") == 0) {
            long long v;
            /* The ceiling is the protocol's, not the host's: INT32_MAX, not
             * LONG_MAX.  On a 64-bit host LONG_MAX would silently widen the wire
             * format to 2^63 and accept ids the 32-bit board refuses. */
            if (seen_id || scan_number(&p, &v, 0) != 0 || v > INT32_MAX)
                return -1;
            out->has_id = true;
            out->id = (long)v;
            seen_id = 1;
        } else if (strcmp(key, "op") == 0) {
            if (seen_op || scan_string(&p, out->op, sizeof out->op) != 0)
                return -1;
            seen_op = 1;
        } else if (strcmp(key, "args") == 0) {
            if (seen_args || scan_object(&p, out->args, sizeof out->args) != 0 ||
                object_validate(out->args) != 0)
                return -1;
            seen_args = 1;
        } else {
            return -1;                   /* the protocol declares three fields */
        }

        p = skip_ws(p);
        if (*p == ',') { ++p; continue; }
        if (*p == '}') { ++p; break; }
        return -1;
    }
    p = skip_ws(p);
    if (*p != '\0')
        return -1;                       /* trailing bytes after the object */
    if (!seen_id || !seen_op || !seen_args || out->op[0] == '\0')
        return -1;
    if (code)
        *code = RF_PROTO_E_NONE;
    return 0;
}

/* ------------------------------------------------------------------ getters */

/* Walk one pair.  1 = read a pair, 0 = the closing brace, -1 = malformed.
 * The leading `{` is optional so the same iterator serves both the whole object
 * (as copied by scan_object) and a resume in the middle of one. */
static int object_pair(const char **pp, char *key, size_t kcap,
                       const char **vstart, const char **vend)
{
    const char *p = skip_ws(*pp);

    if (*p == '{')
        p = skip_ws(p + 1);
    if (*p == '}') { *pp = p + 1; return 0; }
    if (*p == ',') p = skip_ws(p + 1);
    if (scan_string(&p, key, kcap) != 0)
        return -1;
    p = skip_ws(p);
    if (*p != ':')
        return -1;
    p = skip_ws(p + 1);
    *vstart = p;
    if (scan_value(&p) != 0)
        return -1;
    *vend = p;
    *pp = p;
    return 1;
}

static int value_is_string(const char *vs) { return *vs == '"'; }
static int value_is_bool(const char *vs) { return *vs == 't' || *vs == 'f'; }

int rf_proto_arg_int(const char *args, const char *key, long *out)
{
    const char *p, *vs, *ve;
    char k[16];
    int r;

    if (!args || !key || !out)
        return RF_PROTO_ARG_BAD;
    p = args;
    while ((r = object_pair(&p, k, sizeof k, &vs, &ve)) == 1) {
        if (strcmp(k, key) != 0)
            continue;
        {
            long long v;
            const char *q = vs;
            if (value_is_string(vs) || value_is_bool(vs))
                return RF_PROTO_ARG_BAD;
            if (scan_number(&q, &v, 1) != 0 || q != ve ||
                v < INT32_MIN || v > INT32_MAX)
                return RF_PROTO_ARG_BAD;
            *out = (long)v;
        }
        return RF_PROTO_ARG_OK;
    }
    return RF_PROTO_ARG_MISSING;
}

int rf_proto_arg_bool(const char *args, const char *key, bool *out)
{
    const char *p, *vs, *ve;
    char k[16];
    int r;

    if (!args || !key || !out)
        return RF_PROTO_ARG_BAD;
    p = args;
    while ((r = object_pair(&p, k, sizeof k, &vs, &ve)) == 1) {
        if (strcmp(k, key) != 0)
            continue;
        if ((size_t)(ve - vs) == 4 && strncmp(vs, "true", 4) == 0) {
            *out = true;
            return RF_PROTO_ARG_OK;
        }
        if ((size_t)(ve - vs) == 5 && strncmp(vs, "false", 5) == 0) {
            *out = false;
            return RF_PROTO_ARG_OK;
        }
        return RF_PROTO_ARG_BAD;
    }
    return RF_PROTO_ARG_MISSING;
}

int rf_proto_arg_str(const char *args, const char *key, char *out, size_t cap)
{
    const char *p, *vs, *ve;
    char k[16];
    int r;

    if (!args || !key || !out || cap == 0)
        return RF_PROTO_ARG_BAD;
    p = args;
    while ((r = object_pair(&p, k, sizeof k, &vs, &ve)) == 1) {
        if (strcmp(k, key) != 0)
            continue;
        if (!value_is_string(vs))
            return RF_PROTO_ARG_BAD;
        {
            const char *q = vs;
            if (scan_string(&q, out, cap) != 0 || q != ve)
                return RF_PROTO_ARG_BAD;
        }
        return RF_PROTO_ARG_OK;
    }
    return RF_PROTO_ARG_MISSING;
}

/* ----------------------------------------------------------- frame contract */

int rf_proto_iq_declare(const char *args, const char *want_direction,
                        unsigned max_samples, unsigned *samples,
                        rf_proto_code_t *code)
{
    long ns, nb;
    char fmt[16], dir[16];

    if (code)
        *code = RF_PROTO_E_INVALID_ARGUMENT;
    if (!args || !want_direction || !samples)
        return -1;
    if (rf_proto_arg_int(args, "samples", &ns) != RF_PROTO_ARG_OK ||
        ns < 1 || (unsigned long)ns > (unsigned long)max_samples)
        return -1;
    if (rf_proto_arg_int(args, "bytes", &nb) != RF_PROTO_ARG_OK || nb < 0)
        return -1;
    if (rf_proto_arg_str(args, "format", fmt, sizeof fmt) != RF_PROTO_ARG_OK ||
        strcmp(fmt, "s16_iq_le") != 0)
        return -1;
    if (rf_proto_arg_str(args, "direction", dir, sizeof dir) != RF_PROTO_ARG_OK ||
        strcmp(dir, want_direction) != 0)
        return -1;
    /* The length is derived, never trusted: a declaration whose byte count does
     * not match its sample count could otherwise bound a read that runs past the
     * buffer it is supposed to describe. */
    if ((long long)nb != (long long)ns * 4)
        return -1;
    *samples = (unsigned)ns;
    if (code)
        *code = RF_PROTO_E_NONE;
    return 0;
}

/* ---------------------------------------------------------------- responses */

static int finish(char *buf, size_t cap, int n)
{
    if (n < 0 || (size_t)n >= cap)
        return -1;
    return n;
}

/* Escape into a fixed buffer, truncating rather than failing: an error message
 * that is too long must not turn into invalid JSON, and it is a diagnostic, not
 * data. */
static void json_escape(char *dst, size_t dcap, const char *src)
{
    size_t n = 0;

    if (dcap == 0)
        return;
    for (; src && *src; ++src) {
        unsigned char c = (unsigned char)*src;
        const char *rep = NULL;
        char tmp[8];

        if (c == '"')       rep = "\\\"";
        else if (c == '\\') rep = "\\\\";
        else if (c == '\n') rep = "\\n";
        else if (c == '\r') rep = "\\r";
        else if (c == '\t') rep = "\\t";
        else if (c < 0x20) {
            snprintf(tmp, sizeof tmp, "\\u%04X", (unsigned)c);
            rep = tmp;
        }
        if (rep) {
            size_t rl = strlen(rep);
            if (n + rl + 1 > dcap)
                break;
            memcpy(dst + n, rep, rl);
            n += rl;
        } else {
            if (n + 2 > dcap)
                break;
            dst[n++] = (char)c;
        }
    }
    dst[n] = '\0';
}

int rf_proto_write_ok(char *buf, size_t cap, long id, const char *result_json)
{
    return finish(buf, cap,
                  snprintf(buf, cap, "{\"id\":%ld,\"ok\":true,\"result\":%s}",
                           id, result_json ? result_json : "null"));
}

int rf_proto_write_error(char *buf, size_t cap, long id, rf_proto_code_t code,
                         const char *message)
{
    char msg[256];

    json_escape(msg, sizeof msg, message ? message : "");
    return finish(buf, cap,
                  snprintf(buf, cap,
                           "{\"id\":%ld,\"ok\":false,\"error\":{\"code\":\"%s\","
                           "\"message\":\"%s\"}}",
                           id, rf_proto_code_name(code), msg));
}

int rf_proto_write_error_no_id(char *buf, size_t cap, rf_proto_code_t code,
                               const char *message)
{
    char msg[256];

    json_escape(msg, sizeof msg, message ? message : "");
    return finish(buf, cap,
                  snprintf(buf, cap,
                           "{\"ok\":false,\"error\":{\"code\":\"%s\","
                           "\"message\":\"%s\"}}",
                           rf_proto_code_name(code), msg));
}

int rf_proto_write_event(char *buf, size_t cap, const char *type,
                         const char *data_json)
{
    char t[64];

    json_escape(t, sizeof t, type ? type : "");
    return finish(buf, cap,
                  snprintf(buf, cap, "{\"type\":\"%s\",\"data\":%s}", t,
                           data_json ? data_json : "null"));
}

int rf_proto_write_iq_frame(char *buf, size_t cap, long id, const char *direction,
                            unsigned samples, unsigned sample_rate_hz,
                            unsigned ratio, uint64_t timestamp_ns)
{
    /* The frozen shape is this function with no observation: one code path, so an
     * unobserved header cannot drift from an observed one. */
    return rf_proto_write_iq_frame_observed(buf, cap, id, direction, samples,
                                            sample_rate_hz, ratio, timestamp_ns,
                                            NULL);
}

int rf_proto_write_iq_frame_observed(char *buf, size_t cap, long id,
                                     const char *direction, unsigned samples,
                                     unsigned sample_rate_hz, unsigned ratio,
                                     uint64_t timestamp_ns,
                                     const char *observation_json)
{
    /* direction is one of two literals chosen by the daemon, never client text,
     * so it needs no escaping here.  The member list is composed by the daemon from
     * typed values and fixed literals, the same way the research frame already is. */
    int n = snprintf(buf, cap,
                     "{\"id\":%ld,\"type\":\"iq_frame\",\"direction\":\"%s\","
                     "\"format\":\"s16_iq_le\",\"samples\":%u,\"bytes\":%u,"
                     "\"sample_rate_hz\":%u,\"ratio\":%u,\"timestamp_ns\":%llu",
                     id, direction, samples, samples * 4u, sample_rate_hz,
                     ratio, (unsigned long long)timestamp_ns);
    if (finish(buf, cap, n) < 0)
        return -1;
    if (observation_json && *observation_json) {
        int m = snprintf(buf + n, cap - (size_t)n, ",%s}", observation_json);
        if (finish(buf + n, cap - (size_t)n, m) < 0)
            return -1;
        return n + m;
    }
    if ((size_t)n + 2u > cap)
        return -1;
    buf[n++] = '}';
    buf[n] = '\0';
    return n;
}
