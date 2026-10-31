/* test_rf_protocol.c -- host tests for the restricted NDJSON wire format.
 *
 * This file is deliberately built with NO hardware headers and NO PLATFORM_HOST:
 * it links only rf_protocol.o.  So the plan's "the parser performs no hardware
 * callback" is not asserted here -- it is enforced by the link.  A parser that
 * grew a call into platform_rf.c or rf_core.c would fail to link this target,
 * which is a stronger statement than any runtime spy could make.
 */
#include "rf_protocol.h"

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

/* Parse a NUL-terminated line, returning the status and leaving the code in
 * *code.  len is the byte count the reader would have passed, newline excluded. */
static int parse(const char *line, rf_proto_request_t *req, rf_proto_code_t *code)
{
    return rf_proto_parse_request(line, strlen(line), req, code);
}

int main(void)
{
    rf_proto_request_t req;
    rf_proto_code_t code;
    char buf[512];
    int n;

    printf("=== test_rf_protocol ===\n");

    /* --------------------------------------------------------- happy path */

    code = RF_PROTO_E_INVALID_ARGUMENT;
    check("hello parses",
          parse("{\"id\":1,\"op\":\"protocol.hello\",\"args\":{\"major\":1,\"minor\":0}}",
                &req, &code) == 0 &&
          code == RF_PROTO_E_NONE && req.has_id && req.id == 1 &&
          strcmp(req.op, "protocol.hello") == 0);
    {
        long major = -1, minor = -1;
        check("typed getters read the hello args",
              rf_proto_arg_int(req.args, "major", &major) == RF_PROTO_ARG_OK &&
              rf_proto_arg_int(req.args, "minor", &minor) == RF_PROTO_ARG_OK &&
              major == 1 && minor == 0);
    }

    /* An unknown op is a DISPATCH error, not a parse error: the parser has to
     * preserve the name so the dispatcher can name it back to the client. */
    check("an unknown op still parses and is preserved verbatim",
          parse("{\"id\":2,\"op\":\"reg.write\",\"args\":{\"addr\":0}}",
                &req, &code) == 0 &&
          strcmp(req.op, "reg.write") == 0);

    check("an empty args object is a valid request",
          parse("{\"id\":3,\"op\":\"state.get\",\"args\":{}}", &req, &code) == 0 &&
          strcmp(req.args, "{}") == 0);

    check("whitespace between tokens is tolerated",
          parse("  { \"id\" : 4 , \"op\" : \"device.info\" , \"args\" : { } }  ",
                &req, &code) == 0 && req.id == 4);

    check("an id at the protocol maximum round-trips",
          parse("{\"id\":2147483647,\"op\":\"state.get\",\"args\":{}}",
                &req, &code) == 0 && req.id == 2147483647L);
    /* 2147483647 is INT32_MAX, the protocol's ceiling on every target -- not the
     * host's `long` ceiling.  The bound is a protocol constant, so LP32, LLP64
     * and LP64 all accept this value and refuse the next.  An earlier version of
     * this comment claimed the bound was "documented rather than discovered": it
     * was neither, it was whatever the compiler's `long` happened to be, which is
     * why a 64-bit host accepted 2147483648 while the board refused it. */
    check("an id past the protocol maximum is refused",
          parse("{\"id\":2147483648,\"op\":\"state.get\",\"args\":{}}",
                &req, &code) != 0);

    /* ------------------------------------------------------------ refusals */

    check("a missing id is refused",
          parse("{\"op\":\"state.get\",\"args\":{}}", &req, &code) != 0 &&
          code == RF_PROTO_E_INVALID_ARGUMENT);
    check("a missing op is refused",
          parse("{\"id\":1,\"args\":{}}", &req, &code) != 0);
    check("a missing args is refused",
          parse("{\"id\":1,\"op\":\"state.get\"}", &req, &code) != 0);
    check("an empty op is refused",
          parse("{\"id\":1,\"op\":\"\",\"args\":{}}", &req, &code) != 0);
    check("a negative id is refused",
          parse("{\"id\":-1,\"op\":\"state.get\",\"args\":{}}", &req, &code) != 0);
    check("a non-integer id is refused",
          parse("{\"id\":1.5,\"op\":\"state.get\",\"args\":{}}", &req, &code) != 0);
    check("a string id is refused",
          parse("{\"id\":\"1\",\"op\":\"state.get\",\"args\":{}}", &req, &code) != 0);
    check("a duplicate field is refused",
          parse("{\"id\":1,\"id\":2,\"op\":\"state.get\",\"args\":{}}",
                &req, &code) != 0);
    check("an unknown top-level field is refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{},\"reg\":0}",
                &req, &code) != 0);
    check("trailing bytes after the object are refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{}} extra",
                &req, &code) != 0);
    check("two objects on one line are refused",
          parse("{\"id\":1,\"op\":\"a\",\"args\":{}}{\"id\":2,\"op\":\"b\",\"args\":{}}",
                &req, &code) != 0);

    check("an unclosed brace is refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{", &req, &code) != 0);
    check("a quote inside a string is refused",
          parse("{\"id\":1,\"op\":\"sta\"te.get\",\"args\":{}}", &req, &code) != 0);
    check("a backslash escape is refused rather than interpreted",
          parse("{\"id\":1,\"op\":\"state.\\u0020get\",\"args\":{}}",
                &req, &code) != 0);
    check("a bare control character is refused",
          parse("{\"id\":1,\"op\":\"state.\tget\",\"args\":{}}", &req, &code) != 0);
    check("an array value is refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{\"a\":[1]}}",
                &req, &code) != 0);
    check("a nested object value is refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{\"a\":{\"b\":1}}}",
                &req, &code) != 0);
    check("a value with no key is refused",
          parse("{\"id\":1,\"op\":\"state.get\",\"args\":{\"a\"}}",
                &req, &code) != 0);
    check("a missing colon is refused",
          parse("{\"id\" 1,\"op\":\"state.get\",\"args\":{}}", &req, &code) != 0);
    check("a bare word instead of the object is refused",
          parse("not json", &req, &code) != 0);
    check("an empty line is refused",
          parse("", &req, &code) != 0);
    check("whitespace only is refused",
          parse("   ", &req, &code) != 0);

    /* --------------------------------------------------------- line limit */

    check("a line one byte over the limit is OVERFLOW",
          rf_proto_parse_request("{\"id\":1,\"op\":\"state.get\",\"args\":{}}",
                                 RF_PROTO_LINE_MAX + 1u, &req, &code) != 0 &&
          code == RF_PROTO_E_OVERFLOW);
    check("a line exactly at the limit is not refused for length",
          rf_proto_parse_request("{\"id\":1,\"op\":\"state.get\",\"args\":{}}",
                                 RF_PROTO_LINE_MAX, &req, &code) == 0);
    {
        /* A real over-length line, not just an inflated length argument. */
        static char long_line[RF_PROTO_LINE_MAX + 64];
        size_t i;
        const char *head = "{\"id\":1,\"op\":\"state.get\",\"args\":{\"pad\":\"";
        const char *tail = "\"}}";
        size_t n = 0;

        memcpy(long_line + n, head, strlen(head));
        n += strlen(head);
        for (i = 0; i < RF_PROTO_LINE_MAX; ++i)
            long_line[n++] = 'x';
        memcpy(long_line + n, tail, strlen(tail));
        n += strlen(tail);
        long_line[n] = '\0';
        check("an over-length line is refused and reports OVERFLOW",
              rf_proto_parse_request(long_line, n, &req, &code) != 0 &&
              code == RF_PROTO_E_OVERFLOW);
    }

    /* ------------------------------------------------------- typed getters */

    {
        long v = 0;
        bool b = false;

        check("a missing key is MISSING, not OK and not BAD",
              rf_proto_arg_int("{\"a\":1}", "b", &v) == RF_PROTO_ARG_MISSING);
        check("a wrong-type key is BAD",
              rf_proto_arg_int("{\"a\":\"1\"}", "a", &v) == RF_PROTO_ARG_BAD);
        check("a boolean read as an int is BAD",
              rf_proto_arg_int("{\"a\":true}", "a", &v) == RF_PROTO_ARG_BAD);
        check("an int read as a boolean is BAD",
              rf_proto_arg_bool("{\"a\":1}", "a", &b) == RF_PROTO_ARG_BAD);
        check("a negative int is read",
              rf_proto_arg_int("{\"a\":-7}", "a", &v) == RF_PROTO_ARG_OK && v == -7);
        check("both booleans are read",
              rf_proto_arg_bool("{\"a\":true,\"b\":false}", "a", &b) == RF_PROTO_ARG_OK &&
              b &&
              rf_proto_arg_bool("{\"a\":true,\"b\":false}", "b", &b) == RF_PROTO_ARG_OK &&
              !b);
        check("the last key in the object is reachable",
              rf_proto_arg_int("{\"a\":1,\"z\":9}", "z", &v) == RF_PROTO_ARG_OK && v == 9);
    }
    {
        char s[16];
        check("a string is copied without its quotes",
              rf_proto_arg_str("{\"m\":\"QPSK\"}", "m", s, sizeof s) == RF_PROTO_ARG_OK &&
              strcmp(s, "QPSK") == 0);
        check("a short destination is refused rather than truncated",
              rf_proto_arg_str("{\"m\":\"ABCDEFGH\"}", "m", s, 4) == RF_PROTO_ARG_BAD);
    }

    /* ------------------------------------------------------- IQ declaration */

    code = RF_PROTO_E_NONE;
    {
        unsigned samples = 0;
        check("a well-formed TX declaration is accepted",
              rf_proto_iq_declare("{\"samples\":16,\"bytes\":64,\"format\":\"s16_iq_le\","
                                  "\"direction\":\"tx\"}", "tx", 4096, &samples,
                                  &code) == 0 && samples == 16);
        check("a byte count that disagrees with the sample count is refused",
              rf_proto_iq_declare("{\"samples\":16,\"bytes\":63,\"format\":\"s16_iq_le\","
                                  "\"direction\":\"tx\"}", "tx", 4096, &samples,
                                  &code) != 0 && code == RF_PROTO_E_INVALID_ARGUMENT);
        check("a wrong format is refused",
              rf_proto_iq_declare("{\"samples\":16,\"bytes\":64,\"format\":\"be32\","
                                  "\"direction\":\"tx\"}", "tx", 4096, &samples,
                                  &code) != 0);
        check("a direction mismatch is refused",
              rf_proto_iq_declare("{\"samples\":16,\"bytes\":64,\"format\":\"s16_iq_le\","
                                  "\"direction\":\"rx\"}", "tx", 4096, &samples,
                                  &code) != 0);
        check("a missing field is refused",
              rf_proto_iq_declare("{\"samples\":16,\"format\":\"s16_iq_le\","
                                  "\"direction\":\"tx\"}", "tx", 4096, &samples,
                                  &code) != 0);
        check("zero samples is refused",
              rf_proto_iq_declare("{\"samples\":0,\"bytes\":0,\"format\":\"s16_iq_le\","
                                  "\"direction\":\"tx\"}", "tx", 4096, &samples,
                                  &code) != 0);
        check("more samples than the ceiling is refused",
              rf_proto_iq_declare("{\"samples\":4097,\"bytes\":16388,"
                                  "\"format\":\"s16_iq_le\",\"direction\":\"tx\"}",
                                  "tx", 4096, &samples, &code) != 0);
        check("the ceiling itself is accepted",
              rf_proto_iq_declare("{\"samples\":4096,\"bytes\":16384,"
                                  "\"format\":\"s16_iq_le\",\"direction\":\"tx\"}",
                                  "tx", 4096, &samples, &code) == 0 && samples == 4096);
    }

    /* ----------------------------------------------------------- responses */

    check("every error code has its spec name",
          strcmp(rf_proto_code_name(RF_PROTO_E_INVALID_ARGUMENT), "INVALID_ARGUMENT") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_INVALID_STATE), "INVALID_STATE") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_BUSY), "BUSY") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_HARDWARE_IO), "HARDWARE_IO") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_SPI_ERROR), "SPI_ERROR") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_TIMEOUT), "TIMEOUT") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_OVERFLOW), "OVERFLOW") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_UNSUPPORTED), "UNSUPPORTED") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_VERSION_MISMATCH), "VERSION_MISMATCH") == 0 &&
          strcmp(rf_proto_code_name(RF_PROTO_E_PERMISSION), "PERMISSION") == 0);

    n = rf_proto_write_ok(buf, sizeof buf, 17, "{\"state\":\"READY\"}");
    check("an ok response carries the same id and the result verbatim",
          n > 0 && strcmp(buf, "{\"id\":17,\"ok\":true,\"result\":{\"state\":\"READY\"}}") == 0 &&
          n == (int)strlen(buf));

    n = rf_proto_write_ok(buf, sizeof buf, 17, NULL);
    check("a null result becomes JSON null, not a broken document",
          n > 0 && strcmp(buf, "{\"id\":17,\"ok\":true,\"result\":null}") == 0);

    n = rf_proto_write_error(buf, sizeof buf, 17, RF_PROTO_E_BUSY, "TX is active");
    check("an error response names the code and the message",
          n > 0 &&
          strcmp(buf, "{\"id\":17,\"ok\":false,\"error\":{\"code\":\"BUSY\","
                      "\"message\":\"TX is active\"}}") == 0);

    n = rf_proto_write_error(buf, sizeof buf, 17, RF_PROTO_E_BUSY, "say \"hi\"\\now");
    check("a message is escaped so the document stays parseable",
          n > 0 && strstr(buf, "\\\"hi\\\"") != NULL && strstr(buf, "\\\\now") != NULL);

    n = rf_proto_write_error_no_id(buf, sizeof buf, RF_PROTO_E_OVERFLOW, "line too long");
    check("a message with no id omits the id field entirely",
          n > 0 && strstr(buf, "\"id\"") == NULL &&
          strstr(buf, "\"code\":\"OVERFLOW\"") != NULL);

    n = rf_proto_write_event(buf, sizeof buf, "state", "{\"state\":\"TX_ACTIVE\"}");
    check("an event carries a type and no id",
          n > 0 && strcmp(buf, "{\"type\":\"state\",\"data\":{\"state\":\"TX_ACTIVE\"}}") == 0 &&
          strstr(buf, "\"id\"") == NULL);

    n = rf_proto_write_iq_frame(buf, sizeof buf, 9, "rx", 4096, 60000000u, 1u,
                                123456789ull);
    check("an iq_frame header states what was captured",
          n > 0 &&
          strcmp(buf, "{\"id\":9,\"type\":\"iq_frame\",\"direction\":\"rx\","
                      "\"format\":\"s16_iq_le\",\"samples\":4096,\"bytes\":16384,"
                      "\"sample_rate_hz\":60000000,\"ratio\":1,"
                      "\"timestamp_ns\":123456789}") == 0);

    check("a too-small response buffer is reported, not overflowed",
          rf_proto_write_ok(buf, 8, 17, "{}") == -1 &&
          rf_proto_write_error(buf, 8, 17, RF_PROTO_E_BUSY, "x") == -1 &&
          rf_proto_write_iq_frame(buf, 8, 1, "rx", 4096, 1u, 1u, 1ull) == -1);

    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    printf("REAL BOARD: NOT RUN\n");
    printf("RESULT %s\n", fail_count ? "FAIL" : "PASS");
    return fail_count ? 1 : 0;
}
