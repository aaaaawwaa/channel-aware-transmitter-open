/* rf_protocol.h -- the restricted NDJSON control protocol and the IQ frame
 * metadata that follows it.
 *
 * This header and rf_protocol.c are deliberately HARDWARE-FREE: neither includes
 * platform_rf.h, rf_core.h or any vendor header, so a parser bug cannot reach a
 * register.  The protocol test links only rf_protocol.o (and the strsep shim), so
 * if this file ever grew a hardware call the link would fail -- that is the
 * structural form of the plan's "the parser performs no hardware callback".
 *
 * The parser is not a general JSON parser and must not become one.  It accepts
 * exactly three top-level fields -- id, op, args -- with args a flat object of
 * integers, booleans and strings.  There is no expression evaluation, no nesting
 * and no arbitrary register access anywhere in the wire format.
 */
#ifndef RF_PROTOCOL_H_
#define RF_PROTOCOL_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* One control line.  Payloads are not lines and are bounded separately by the
 * frame length, which the daemon caps at the BRAM size. */
#define RF_PROTO_LINE_MAX   2048u
#define RF_PROTO_OP_MAX     40u
#define RF_PROTO_ARGS_MAX   1024u

/* The fixed error vocabulary (spec 3.3).  These codes are shared by the daemon's
 * state layer so that a failure has exactly one name on the wire and in a test. */
typedef enum {
    RF_PROTO_E_NONE = 0,
    RF_PROTO_E_INVALID_ARGUMENT,
    RF_PROTO_E_INVALID_STATE,
    RF_PROTO_E_BUSY,
    RF_PROTO_E_HARDWARE_IO,
    RF_PROTO_E_SPI_ERROR,
    RF_PROTO_E_TIMEOUT,
    RF_PROTO_E_OVERFLOW,
    RF_PROTO_E_UNSUPPORTED,
    RF_PROTO_E_VERSION_MISMATCH,
    /* Appended, not inserted.  The codes are serialised by NAME, but a code that
     * compared raw enum values would be renumbered by an insert, so the vocabulary
     * only ever grows at the end.  PERMISSION is the fail-closed arming refusal
     * ("this caller may not do this"), deliberately distinct from INVALID_STATE
     * ("the daemon is not in a state where this is legal") -- Task 7's failure
     * taxonomy needs the two to be tellable apart. */
    RF_PROTO_E_PERMISSION
} rf_proto_code_t;

const char *rf_proto_code_name(rf_proto_code_t code);

/* ---------------------------------------------------------------- requests */

typedef struct {
    bool has_id;
    long id;
    char op[RF_PROTO_OP_MAX];
    char args[RF_PROTO_ARGS_MAX];   /* the args object, verbatim */
} rf_proto_request_t;

/* Parse one line (without its newline) into a request.  Returns 0, or -1 with
 * *code set.  Strict by design: an unknown top-level field, a missing id, a
 * missing args, a duplicate field, trailing bytes or a value type the protocol
 * does not declare are all INVALID_ARGUMENT.  No hardware is touched. */
int rf_proto_parse_request(const char *line, size_t len,
                           rf_proto_request_t *out, rf_proto_code_t *code);

/* Typed argument access.  A key that is absent is MISSING; a key whose value is
 * the wrong type or out of the getter's range is BAD.  Both are failures, and the
 * daemon turns either into INVALID_ARGUMENT -- there is no "default to 0".
 *
 * The accepted integer range is a fixed 32-bit signed one (INT32_MIN..INT32_MAX),
 * a protocol constant rather than the width of `long`: a 64-bit host parses
 * exactly what the 32-bit board parses.  `long` is still the carrier type only
 * because every value in that range fits it on every target.  A separate u64
 * getter would read as if it accepted 64-bit values while silently rejecting
 * anything past INT32_MAX, so there is none. */
#define RF_PROTO_ARG_OK      0
#define RF_PROTO_ARG_MISSING (-1)
#define RF_PROTO_ARG_BAD     (-2)

int rf_proto_arg_int(const char *args, const char *key, long *out);
int rf_proto_arg_bool(const char *args, const char *key, bool *out);
int rf_proto_arg_str(const char *args, const char *key, char *out, size_t cap);

/* Validate an IQ frame declaration.  `want_direction` is "tx" or "rx".  On
 * success *samples is set.  Enforces: samples in 1..max_samples, bytes ==
 * samples*4, format == "s16_iq_le", direction == want_direction.  A declaration
 * is metadata only -- this proves nothing about the bytes that follow it.
 *
 * The 12-bit sample range and the "no partial write" guarantee live in rf_iq.c
 * (rf_iq_wire_to_bram / rf_iq_bram_to_wire) and are deliberately NOT duplicated
 * here: two definitions of the converter range is how they drift apart. */
int rf_proto_iq_declare(const char *args, const char *want_direction,
                        unsigned max_samples, unsigned *samples,
                        rf_proto_code_t *code);

/* --------------------------------------------------------------- responses */

/* Every writer returns the number of bytes written (excluding the terminator),
 * or -1 if the buffer is too small.  A response is built whole and then sent
 * whole, so a truncated response cannot be produced by construction. */
int rf_proto_write_ok(char *buf, size_t cap, long id, const char *result_json);
int rf_proto_write_error(char *buf, size_t cap, long id, rf_proto_code_t code,
                         const char *message);
int rf_proto_write_error_no_id(char *buf, size_t cap, rf_proto_code_t code,
                               const char *message);
int rf_proto_write_event(char *buf, size_t cap, const char *type,
                         const char *data_json);

/* The RX frame header.  `samples`/`bytes`/`ratio`/`timestamp_ns` are the values
 * that were ACTUALLY captured, not the ones requested. */
int rf_proto_write_iq_frame(char *buf, size_t cap, long id, const char *direction,
                            unsigned samples, unsigned sample_rate_hz,
                            unsigned ratio, uint64_t timestamp_ns);

/* The same header with ONE additive member list appended before the closing brace:
 * 'observation_json' is a complete JSON member list WITHOUT the enclosing braces and
 * WITHOUT a leading comma (the writer inserts the separating comma itself), or
 * NULL/empty for the frozen shape, byte for byte.  The shape of the frame header is
 * the protocol's business, so the framing stays here; the daemon composes the values,
 * exactly as it already does for the research frame.  A caller that ignores the
 * addition reads the same header it always read. */
int rf_proto_write_iq_frame_observed(char *buf, size_t cap, long id,
                                     const char *direction, unsigned samples,
                                     unsigned sample_rate_hz, unsigned ratio,
                                     uint64_t timestamp_ns,
                                     const char *observation_json);

#endif /* RF_PROTOCOL_H_ */
