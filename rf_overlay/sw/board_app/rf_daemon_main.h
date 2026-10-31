/* rf_daemon_main.h -- the daemon executable's startup surface.
 *
 * Declared here, and not in rf_daemon.h, because these are the EXECUTABLE's
 * rules rather than the protocol's: rf_daemon.h is the framing/lease contract,
 * this is "may this binary start at all".  The host test compiles
 * rf_daemon_main.c with PLATFORM_HOST (which excludes main() and the socket
 * transport) and drives these functions directly, so the startup ORDER is
 * asserted by a test instead of read off the source.
 *
 * Both functions are pure: neither touches hardware.  The hardware step is the
 * injected opener, which is what makes "no register was written" observable.
 */
#ifndef RF_DAEMON_MAIN_H_
#define RF_DAEMON_MAIN_H_

#include <stddef.h>

#include "rf_core.h"
#include "rf_daemon.h"   /* rf_release_identity_t, which the manifest carries */

/* May this binary serve the release described by `json`?
 *
 * Checks the manifest's daemon_version and protocol_major against this binary's
 * own (rf_version.h), and requires the release identity's `bit_sha256` and
 * `hwh_sha256` to be 64 lowercase hexadecimal characters, returning 0 on a match
 * and -1 otherwise with a human reason in `why`.  `identity` is always written on
 * success and may be left unspecified on failure; it is not optional, because a
 * startup that cannot hand the identity on would produce a daemon whose
 * device.info cannot answer with one.
 *
 * A manifest it cannot fully understand is a failure, never a default: a
 * duplicated key, a value of the wrong JSON type, a hash of the wrong shape, or a
 * truncated document all refuse.  Nothing here can write a register -- there is
 * no core handle to write through. */
int rf_daemon_manifest_check(const char *json, rf_release_identity_t *identity,
                             char *why, size_t why_cap);

/* The same check against a file on disk.  A missing or unreadable manifest is a
 * refusal: an unrecorded build must not become a running daemon. */
int rf_daemon_manifest_check_file(const char *path,
                                  rf_release_identity_t *identity,
                                  char *why, size_t why_cap);

/* The command line's numeric predicate: 0 with *out set when `text` is a decimal
 * integer inside [min, max], -1 for an empty string, a non-numeric string, a
 * string with trailing characters, or a value outside the range.
 *
 * Public (and tested on the host) because the executable's argument handling was
 * otherwise covered by nothing until the ARM build: see the note in
 * rf_daemon_main.c.  The grammar is exactly `strtoul`'s, which means leading
 * whitespace is skipped and a leading `+` is accepted -- observed behaviour that
 * test_rf_daemon_main.c pins deliberately, not a claim that it is desirable.
 *
 * ⚠ Scope of that coverage: this function does NOT exit.  The `exit 2` the CLI
 * convention calls for is the caller's mapping of -1, and the caller (main) sits
 * inside the POSIX-only section, so **no host test executes that mapping**.  The
 * predicate is host-covered; `return 2;` is board-only and unexecuted by any host
 * test.  Do not describe the coverage as "invalid arguments exit 2". */
int rf_daemon_merged_manifest_check(const char *json,
                                    rf_release_identity_t *identity,
                                    char *why, size_t why_cap);
int rf_daemon_merged_manifest_check_file(const char *path,
                                         rf_release_identity_t *identity,
                                         char *why, size_t why_cap);
int rf_daemon_release_path_matches(const char *path, const char *release_id);
int rf_daemon_active_manifest_path(const char *marker, char *path,
                                   size_t cap, char *why, size_t why_cap);

int parse_uint(const char *text, unsigned min, unsigned max, unsigned *out);

/* Check the manifest, and only then open the core.
 *
 * `open_core` is injected so the ordering is testable: it is called exactly once,
 * after the manifest check has passed, and never before.  On success *out_core
 * holds an OPEN but NOT INITIALIZED core -- no rf_core_init, no TX source
 * selection, no transmission.  The part stays silent until a client asks for
 * rf.init, and that request must name its attenuation explicitly.
 *
 * `out_identity` receives the identity the manifest check validated, for the
 * caller to put into the daemon's config; it is required for the same reason the
 * check requires the fields at all. */
int rf_daemon_startup(const char *manifest_path,
                      int (*open_core)(void *ctx, rf_core_t **out), void *ctx,
                      rf_core_t **out_core, rf_release_identity_t *out_identity,
                      char *why, size_t why_cap);

#endif /* RF_DAEMON_MAIN_H_ */
