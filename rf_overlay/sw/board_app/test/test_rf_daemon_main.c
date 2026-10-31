/* test_rf_daemon_main.c -- the daemon executable's startup ORDER.
 *
 * Compiled with PLATFORM_HOST, so rf_daemon_main.c contributes no main() and no
 * POSIX socket transport: what is under test is the sequence that runs before a
 * register can be written.
 *
 * The point of this file is that the ordering claim is TESTABLE rather than
 * readable.  "The release manifest is checked before any hardware is opened" is
 * only meaningful if something can observe that the opener was not reached; here
 * the opener is injected and records its own invocation, so a manifest that
 * should have been refused but was not shows up as "the opener ran" instead of
 * as a comment nobody re-read.
 *
 * The manifest parser here is deliberately a small key/value reader, NOT a JSON
 * library, and every one of its refusals is asserted: a manifest is a gate, so
 * "did not understand it" has to mean failure.  The negative controls matter
 * most -- a duplicated key (which a last-wins reader would silently accept), a
 * key that only appears inside a value, a string form where a number is
 * required, and a number form where a string is required.  The release
 * identity's two hashes are required in the shape the manifest spec records,
 * so absent, one short, one long, uppercase and non-hex all refuse: anything
 * other than 64 lowercase hex is something this reader has not understood, and
 * an identity report built from it would report the wrong thing.
 */
#include "rf_daemon.h"
#include "rf_daemon_main.h"
#include "rf_version.h"
#include "platform_rf.h"

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

/* ------------------------------------------------------------- the opener */

typedef struct {
    int calls;
    int fail;
} opener_t;

/* Reaching here at all is the fact under test: it means authorization passed.
 * It never touches hardware -- a host build has none -- and it hands back a
 * pointer that only proves "a core was produced". */
static int fake_open(void *ctx, rf_core_t **out)
{
    opener_t *state = (opener_t *)ctx;

    ++state->calls;
    if (state->fail) {
        *out = NULL;
        return -1;
    }
    *out = (rf_core_t *)state;      /* opaque to this test; never dereferenced */
    return 0;
}

/* ------------------------------------------------------------------ files */

static const char *MANIFEST = "test_rf_daemon_main.json";

/* The two hashes a compliant manifest carries, in the shape the release manifest
 * spec records: 64 lowercase hexadecimal characters. */
#define GOOD_BIT_SHA256 \
    "7381804856d1af90400d80c6583587847deac5435234a0d074bfeb93875d1dc3"
#define GOOD_HWH_SHA256 \
    "205bd728b4e878ecec113e3b4aaa1108a829b47833e4adfa90e5ce470459e901"

static void write_file(const char *text)
{
    FILE *file = fopen(MANIFEST, "wb");

    fputs(text, file);
    fclose(file);
}

/* A manifest whose identity fields are given verbatim, so a case can leave one
 * out, shorten one, or change its case.  Each argument is a raw JSON value (a
 * quoted string for the valid cases); an empty string leaves that key out
 * entirely. */
static void write_manifest_identity(const char *bit_json, const char *hwh_json)
{
    char text[1024];

    snprintf(text, sizeof text,
             "{\n  \"product\": \"ac820-ad9361-rf\",\n"
             "  \"protocol_major\": %u,\n"
             "  \"daemon_version\": \"%s\"%s%s%s%s\n}\n",
             (unsigned)RF_PROTOCOL_MAJOR, RF_DAEMON_VERSION,
             bit_json[0] ? ",\n  \"bit_sha256\": " : "", bit_json,
             hwh_json[0] ? ",\n  \"hwh_sha256\": " : "", hwh_json);
    write_file(text);
}

/* A manifest with the versions this binary actually speaks, an identity it
 * must carry through startup, and a cnn-bpsk-v1 software contract + nested
 * software map (M4 Chunk C): every "good" startup case proves the parser
 * tolerates the current contract content, not just the legacy identity keys. */
static void write_contract_manifest(const char *contract)
{
    char text[2400];

    snprintf(text, sizeof text,
             "{\n  \"product\": \"ac820-ad9361-rf\",\n"
             "  \"protocol_major\": %u,\n"
             "  \"daemon_version\": \"%s\",\n"
             "  \"bit_sha256\": \"%s\",\n"
             "  \"hwh_sha256\": \"%s\",\n"
             "  \"software_contract\": \"%s\",\n"
             "  \"software\": {\n"
             "    \"rf_bpsk_receiver.py\": \"23b9ab9fdc1cc7647c2c40169ae73a23504c262d2270d82ebf8ea65349e16760\",\n"
             "    \"rf_bpsk_pipeline.py\": \"ad6a2d8119f74e939db056c35522f30f6bea352ce1576a87abe18b0bddc10dca\"\n"
             "  }\n}\n",
             (unsigned)RF_PROTOCOL_MAJOR, RF_DAEMON_VERSION,
             GOOD_BIT_SHA256, GOOD_HWH_SHA256, contract);
    write_file(text);
}

static void write_good_manifest(void)
{
    write_contract_manifest("cnn-bpsk-v1");
}

/* What the last startup handed back, so the assertions about the identity do not
 * have to thread an out-parameter through twenty existing call sites. */
static rf_release_identity_t last_identity;

static int startup_with_file(const char *path, opener_t *opener, char *why,
                             size_t why_cap)
{
    rf_core_t *core = NULL;

    memset(&last_identity, 0, sizeof last_identity);
    return rf_daemon_startup(path, fake_open, opener, &core, &last_identity, why,
                             why_cap);
}

/* -------------------------------------------------------------------- main */

/* The arming-token file check (rf_daemon_main.c).  It lives outside the board-only
 * block precisely so this can exist: it is the one piece of the control gate that has
 * no other behavioural coverage on the host. */
int rf_control_token_check_file(const char *path, const char *token);

static void test_control_token_file(void)
{
    const char *path = "test_rf_control_token.txt";
    FILE *f;

    printf("=== control token file ===\n");
    f = fopen(path, "wb");
    check("the token file can be created for the test", f != NULL);
    if (f) {
        fputs("s3cret\n", f);
        fclose(f);
    }
    check("a matching token is accepted",
          rf_control_token_check_file(path, "s3cret") == 0);
    check("a different token is refused",
          rf_control_token_check_file(path, "other") != 0);
    check("an empty presented token is refused",
          rf_control_token_check_file(path, "") != 0);
    check("a missing file cannot arm control",
          rf_control_token_check_file("no-such-token-file-here", "s3cret") != 0);
    remove(path);
    f = fopen(path, "wb");
    if (f) {
        fputs("\n", f);
        fclose(f);
    }
    check("a blank token file cannot arm control",
          rf_control_token_check_file(path, "") != 0);
    remove(path);
    f = fopen(path, "wb");
    if (f) {
        fputs("s3cret", f);
        fclose(f);
    }
    check("no trailing newline is accepted",
          rf_control_token_check_file(path, "s3cret") == 0);
    remove(path);
}

int main(void)
{
    char why[256];
    opener_t opener;

    printf("=== test_rf_daemon_main ===\n");

    test_control_token_file();

    /* ------------------------------------------- the happy path is the gate */

    write_good_manifest();
    memset(&opener, 0, sizeof opener);
    check("a matching manifest lets startup proceed to the opener",
          startup_with_file(MANIFEST, &opener, why, sizeof why) == 0 &&
          opener.calls == 1);

    /* Python validates the full software map before publishing. The C startup
     * gate must also tolerate the new contract's nested software declaration. */
    write_contract_manifest("cnn-bpsk-teaching-v1");
    memset(&opener, 0, sizeof opener);
    check("teaching software contract preserves startup identity validation",
          startup_with_file(MANIFEST, &opener, why, sizeof why) == 0 &&
          opener.calls == 1);

    /* -------------------------------------- the release identity (F-T7b) */

    write_good_manifest();
    memset(&opener, 0, sizeof opener);
    check("startup carries the manifest's bit_sha256 out verbatim",
          startup_with_file(MANIFEST, &opener, why, sizeof why) == 0 &&
          strcmp(last_identity.bit_sha256, GOOD_BIT_SHA256) == 0);
    check("startup carries the manifest's hwh_sha256 out verbatim",
          strcmp(last_identity.hwh_sha256, GOOD_HWH_SHA256) == 0);

    /* The identity is REQUIRED, and a refusal must name the field: an identity
     * report that can come back empty is not one, and a silent failure here
     * sends someone to the board to debug a mismatch that is in a text file. */
    {
        struct {
            const char *name;
            const char *bit_json;     /* raw JSON value; "" omits the key */
            const char *hwh_json;
            const char *field;
        } cases[] = {
            { "a manifest with no bit_sha256",
              "", "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "a manifest with no hwh_sha256",
              "\"" GOOD_BIT_SHA256 "\"", "", "hwh_sha256" },
            /* 63 characters: one short of the spec'd 64. */
            { "a bit_sha256 one character short",
              "\"7381804856d1af90400d80c6583587847deac5435234a0d074bfeb93875d1dc\"",
              "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            /* 65 characters: one long. */
            { "a bit_sha256 one character long",
              "\"7381804856d1af90400d80c6583587847deac5435234a0d074bfeb93875d1dc3a\"",
              "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "an uppercase bit_sha256",
              "\"7381804856D1AF90400D80C6583587847DEAC5435234A0D074BFEB93875D1DC3\"",
              "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "a bit_sha256 with a non-hex character",
              "\"7381804856d1af90400d80c6583587847deac5435234a0d074bfeb93875d1dcg\"",
              "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "an empty bit_sha256",
              "\"\"", "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "a bit_sha256 that is not a string",
              "12345", "\"" GOOD_HWH_SHA256 "\"", "bit_sha256" },
            { "an uppercase hwh_sha256",
              "\"" GOOD_BIT_SHA256 "\"",
              "\"205BD728B4E878ECEC113E3B4AAA1108A829B47833E4ADFA90E5CE470459E901\"",
              "hwh_sha256" },
        };
        size_t i;

        for (i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
            char name[192];
            int refused;

            write_manifest_identity(cases[i].bit_json, cases[i].hwh_json);
            memset(&opener, 0, sizeof opener);
            why[0] = '\0';
            refused = startup_with_file(MANIFEST, &opener, why, sizeof why) != 0;
            snprintf(name, sizeof name, "%s is refused", cases[i].name);
            check(name, refused);
            snprintf(name, sizeof name,
                     "and the opener was not reached for %s", cases[i].name);
            check(name, opener.calls == 0);
            snprintf(name, sizeof name,
                     "and the refusal names %s", cases[i].field);
            check(name, strstr(why, cases[i].field) != NULL);
        }
    }

    /* -------------------------------- every refusal must precede hardware */

    /* Missing file: the closest thing to "the build was never recorded". */
    memset(&opener, 0, sizeof opener);
    check("a missing manifest is refused",
          startup_with_file("no_such_manifest.json", &opener, why, sizeof why) != 0);
    check("and the opener was not reached for a missing manifest",
          opener.calls == 0);

    {
        char text[512];

        /* The key is genuinely absent -- the parser must not default it. */
        snprintf(text, sizeof text, "{\n  \"daemon_version\": \"%s\"\n}\n",
                 RF_DAEMON_VERSION);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a manifest without protocol_major is refused",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[512];

        snprintf(text, sizeof text, "{\n  \"daemon_version\": \"%s\",\n"
                 "  \"protocol_major\": %u\n}\n",
                 RF_DAEMON_VERSION, (unsigned)RF_PROTOCOL_MAJOR + 1u);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a protocol major this binary does not speak is refused",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[512];

        snprintf(text, sizeof text, "{\n  \"daemon_version\": \"9.9.9\",\n"
                 "  \"protocol_major\": %u\n}\n", (unsigned)RF_PROTOCOL_MAJOR);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a daemon version this binary is not is refused",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }

    /* The refusals must name the problem: a silent failure here sends someone to
     * the board to debug a mismatch that is in a text file. */
    {
        char text[512];

        snprintf(text, sizeof text, "{\n  \"daemon_version\": \"%s\",\n"
                 "  \"protocol_major\": 7\n}\n", RF_DAEMON_VERSION);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        why[0] = '\0';
        (void)startup_with_file(MANIFEST, &opener, why, sizeof why);
        check("the refusal says what was wrong, not just that something was",
              strstr(why, "protocol major") != NULL);
    }

    /* --------------------------------- parser refusals a sloppy reader allows */

    {
        char text[512];

        /* A duplicated key: a last-wins reader would take the good value and
         * pass, which is exactly the failure this negative control exists for. */
        snprintf(text, sizeof text,
                 "{\n  \"daemon_version\": \"%s\",\n"
                 "  \"protocol_major\": 7,\n"
                 "  \"protocol_major\": %u\n}\n",
                 RF_DAEMON_VERSION, (unsigned)RF_PROTOCOL_MAJOR);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a duplicated manifest key is refused rather than last-wins",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[512];

        /* The key name only occurs inside another string's value. */
        snprintf(text, sizeof text,
                 "{\n  \"note\": \"protocol_major is in here\",\n"
                 "  \"daemon_version\": \"%s\"\n}\n", RF_DAEMON_VERSION);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a key name that only appears inside a value is not a key",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[512];

        /* protocol_major as a string would need a different reader; accepting it
         * would mean the reader understood something it did not. */
        snprintf(text, sizeof text,
                 "{\n  \"daemon_version\": \"%s\",\n"
                 "  \"protocol_major\": \"1\"\n}\n", RF_DAEMON_VERSION);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a quoted protocol_major is refused rather than coerced",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[512];

        /* daemon_version as a number: same reasoning in the other direction. */
        snprintf(text, sizeof text,
                 "{\n  \"daemon_version\": 1,\n  \"protocol_major\": %u\n}\n",
                 (unsigned)RF_PROTOCOL_MAJOR);
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("an unquoted daemon_version is refused rather than coerced",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }
    {
        char text[256] = "{ \"daemon_version\": \"1.0.0\", \"protocol_major\": 1";

        /* Truncated JSON: the value never closes. */
        write_file(text);
        memset(&opener, 0, sizeof opener);
        check("a truncated manifest is refused",
              startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
              opener.calls == 0);
    }

    /* ------------------------- a manifest that passes, on hardware that fails */

    write_good_manifest();
    memset(&opener, 0, sizeof opener);
    opener.fail = 1;
    check("a matching manifest with an unopenable core still fails",
          startup_with_file(MANIFEST, &opener, why, sizeof why) != 0 &&
          opener.calls == 1);
    check("and that refusal says it is the hardware, not the manifest",
          strstr(why, "core could not be opened") != NULL);

    /* ---------------------------- the part stays silent until a client asks */

    {
        /* The startup path must not initialize the part: an uninitialized board
         * cannot transmit, and the launcher relies on handing over exactly that.
         * Nothing here can prove a negative about hardware, so what is asserted
         * is that startup's ONLY hardware action was the opener. */
        opener_t first, second;

        write_good_manifest();
        memset(&first, 0, sizeof first);
        memset(&second, 0, sizeof second);
        (void)startup_with_file(MANIFEST, &first, why, sizeof why);
        check("startup opens the core exactly once and does nothing else with it",
              first.calls == 1 && second.calls == 0);
    }

    /* ---------------------------------- the command-line numeric predicate
     *
     * This function used to live inside the board-only section, where no host
     * gate compiled it: a malformed `--port` was checked by nothing until the ARM
     * build.  It is compiled here now, and the cases below are the ones the CLI
     * convention cares about.  What these CANNOT cover is the mapping to exit 2,
     * which lives in main() inside the POSIX-only section -- see the header.
     */
    {
        struct {
            const char *text;
            unsigned min, max;
            int rc;
            unsigned value;
        } cases[] = {
            /* the grammar: nothing, not a number, or a number with junk after it */
            { "", 1u, 65535u, -1, 0u },
            { "abc", 1u, 65535u, -1, 0u },
            { "12abc", 1u, 65535u, -1, 0u },
            /* the port bounds main() passes: 1..65535 */
            { "0", 1u, 65535u, -1, 0u },
            { "1", 1u, 65535u, 0, 1u },
            { "65535", 1u, 65535u, 0, 65535u },
            { "65536", 1u, 65535u, -1, 0u },
            /* the max-samples bounds main() passes: 1..RF_BRAM_WORDS */
            { "0", 1u, RF_BRAM_WORDS, -1, 0u },
            { "4096", 1u, RF_BRAM_WORDS, 0, 4096u },
            /* a value that does not fit an unsigned long: strtoul saturates at
             * ULONG_MAX, which the RANGE check rejects.  There is no errno test,
             * so this case is evidence that saturation is caught by the range,
             * not evidence of an overflow handler. */
            { "99999999999999999999", 1u, 65535u, -1, 0u },
            /* OBSERVED behaviour, pinned so that changing it must be deliberate:
             * strtoul skips leading whitespace and accepts a sign, so these are
             * ACCEPTED (and a lone "-" saturates and is rejected by the range).
             * Recorded, not endorsed -- tightening the grammar to reject them
             * means changing these rows on purpose. */
            { " 12", 1u, 65535u, 0, 12u },
            { "+12", 1u, 65535u, 0, 12u },
            { "-1", 1u, 65535u, -1, 0u },
        };
        size_t i;

        for (i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
            char name[160];
            unsigned out = 0u;
            int rc = parse_uint(cases[i].text, cases[i].min, cases[i].max, &out);

            snprintf(name, sizeof name,
                     "parse_uint(\"%s\") in [%u,%u] returns %d",
                     cases[i].text, cases[i].min, cases[i].max, cases[i].rc);
            check(name, rc == cases[i].rc
                        && (rc != 0 || out == cases[i].value));
        }
    }
    {
        /* The ceiling case is computed from the constant instead of written as a
         * literal, so it cannot drift from the bound main() actually passes. */
        char over[32];
        unsigned out = 0u;

        snprintf(over, sizeof over, "%u", (unsigned)(RF_BRAM_WORDS + 1u));
        check("parse_uint accepts the max-samples ceiling itself",
              parse_uint("4096", 1u, RF_BRAM_WORDS, &out) == 0 &&
              out == RF_BRAM_WORDS);
        check("parse_uint rejects one past the max-samples ceiling",
              parse_uint(over, 1u, RF_BRAM_WORDS, &out) == -1);
    }

    {
        const char *release_id = "0123456789abcdef0123456789abcdef";
        const char *good =
            "{\"schema_version\":1,"
            "\"release_id\":\"0123456789abcdef0123456789abcdef\","
            "\"protocol_version\":1,\"daemon_version\":\"" RF_DAEMON_VERSION "\","
            "\"bit_sha256\":\"" GOOD_BIT_SHA256 "\","
            "\"hwh_sha256\":\"" GOOD_HWH_SHA256 "\"}";
        rf_release_identity_t identity;
        char manifest_path[1200];
        char altered[1024];
        check("merged manifest identity and version accepted",
              rf_daemon_merged_manifest_check(good, &identity, why,
                                              sizeof why) == 0 &&
              strcmp(identity.release_id, release_id) == 0);
        snprintf(altered, sizeof altered, "%s", good);
        {
            char *p = strstr(altered, "\"protocol_version\":1");
            if (p) p[strlen("\"protocol_version\":")] = '2';
        }
        check("wrong merged protocol version refused before PL mapping",
              rf_daemon_merged_manifest_check(altered, &identity, why,
                                              sizeof why) != 0);
        snprintf(altered, sizeof altered, "%s", good);
        {
            char *p = strstr(altered, "\"schema_version\":1");
            if (p) p[strlen("\"schema_version\":")] = '2';
        }
        check("wrong merged schema refused before PL mapping",
              rf_daemon_merged_manifest_check(altered, &identity, why,
                                              sizeof why) != 0);
        {
            /* cnn-v1 adds model_artifact.schema_version. Top-level gate fields
             * must not be confused with nested declaration or metadata fields. */
            struct {
                const char *name;
                const char *suffix;
                int accepted;
            } cases[] = {
                {"nested model schema accepted",
                 ",\"model_artifact\":{\"schema_version\":1,\"status\":\"experimental\"}", 1},
                {"nested wrong protocol cannot override top-level protocol",
                 ",\"model_artifact\":{\"protocol_version\":999,\"daemon_version\":\"wrong\"}", 1},
                {"nested array identities cannot override top-level identity",
                 ",\"metadata\":[{\"release_id\":\"bad\"},{\"bit_sha256\":\"bad\"}]", 1},
                {"key-like text and escaped quotes in value accepted",
                 ",\"note\":\"quoted \\\"schema_version\\\":99, { brace } [ bracket ] \\\\ slash\"", 1},
                {"same top-level schema duplicated refused",
                 ",\"schema_version\":1", 0},
                {"different top-level schema duplicated refused",
                 ",\"schema_version\":2", 0},
                {"top-level protocol duplicated refused",
                 ",\"protocol_version\":1", 0},
                {"top-level daemon version duplicated refused",
                 ",\"daemon_version\":\"1.0.0\"", 0},
                {"top-level bit identity duplicated refused",
                 ",\"bit_sha256\":\"" GOOD_BIT_SHA256 "\"", 0},
                {"unclosed nested object refused", ",\"model_artifact\":{\"schema_version\":1", 0},
                {"mismatched nested delimiters refused", ",\"metadata\":[}", 0},
                {"invalid nested value refused", ",\"metadata\":bogus", 0},
                {"nested trailing comma refused", ",\"metadata\":[1,]", 0},
                {"invalid nested escape refused", ",\"metadata\":\"\\q\"", 0},
                {"unterminated nested string refused", ",\"metadata\":\"unterminated", 0},
                {"escaped top-level gate key refused", ",\"schema_versi\\u006fn\":1", 0},
            };
            size_t i;
            for (i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
                snprintf(altered, sizeof altered, "%.*s%s}",
                         (int)strlen(good) - 1, good, cases[i].suffix);
                check(cases[i].name,
                      (rf_daemon_merged_manifest_check(altered, &identity, why,
                                                       sizeof why) == 0)
                      == cases[i].accepted);
            }
            snprintf(altered, sizeof altered, "%s garbage", good);
            check("trailing data after manifest refused",
                  rf_daemon_merged_manifest_check(altered, &identity, why, sizeof why) != 0);
            snprintf(altered, sizeof altered,
                     "{\"model_artifact\":{\"schema_version\":1},%s", good + 1);
            check("nested model declared before top-level fields accepted",
                  rf_daemon_merged_manifest_check(altered, &identity, why, sizeof why) == 0);
            snprintf(altered, sizeof altered, "%s", good);
            {
                char *p = strstr(altered, "\"schema_version\":1");
                if (p) memcpy(p + 1, "nested_schema_", strlen("schema_version"));
            }
            {
                char nested_only[1200];
                snprintf(nested_only, sizeof nested_only, "%.*s,\"model_artifact\":{\"schema_version\":1}}",
                         (int)strlen(altered) - 1, altered);
                check("nested schema cannot supply missing top-level schema",
                      rf_daemon_merged_manifest_check(nested_only, &identity, why, sizeof why) != 0);
            }
        }
        write_file("/home/xilinx/overlays/awg_merged/releases/"
                   "0123456789abcdef0123456789abcdef\n");
        check("active marker resolves to a single immutable manifest path",
              rf_daemon_active_manifest_path(MANIFEST, manifest_path,
                                             sizeof manifest_path, why,
                                             sizeof why) == 0 &&
              rf_daemon_release_path_matches(manifest_path, release_id));
        write_file("/home/xilinx/overlays/awg_merged/current\n");
        check("mutable current path cannot satisfy release identity",
              rf_daemon_active_manifest_path(MANIFEST, manifest_path,
                                             sizeof manifest_path, why,
                                             sizeof why) == 0 &&
              !rf_daemon_release_path_matches(manifest_path, release_id));
        write_file("/home/xilinx/overlays/awg_merged/releases/"
                   "0123456789abcdef0123456789abcdef\nextra\n");
        check("multi-line active marker refused",
              rf_daemon_active_manifest_path(MANIFEST, manifest_path,
                                             sizeof manifest_path, why,
                                             sizeof why) != 0);
    }

    remove(MANIFEST);
    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    printf("REAL BOARD: NOT RUN\n");
    printf("RESULT %s\n", fail_count ? "FAIL" : "PASS");
    return fail_count ? 1 : 0;
}
