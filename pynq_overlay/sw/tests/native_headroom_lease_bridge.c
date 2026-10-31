/* Test-only bridge outside the frozen native build input map. The daemon and
 * its native transport/state/close paths run unchanged against fake hardware. */
#define main old_daemon_test_main
#include "test_rf_daemon.c"
#undef main
#include <stdlib.h>

static rf_daemon_t *lease_daemon;
static script_t second_client;

static void second_attempt(void *unused)
{
    (void)unused;
    script_init(&second_client);
    hello(&second_client, 1);
    (void)serve(lease_daemon, &second_client);
}

int main(int argc, char **argv)
{
    FILE *input;
    rf_daemon_config_t cfg;
    rf_core_t *core;
    script_t first;
    status_read_ctx_t read_ctx = {0};
    rf_pl_status_reader_t *reader;
    int rc;
    if (argc != 2 || !(input = fopen(argv[1], "rb"))) return 2;
    script_init(&first);
    first.in_len = fread(first.in, 1, sizeof first.in, input);
    if (ferror(input) || !feof(input)) { fclose(input); return 3; }
    fclose(input);
    core = opened_core(); /* Opens the fake core without RF initialization. */
    if (!core) return 4;
    probe_reset(core);
    lease_daemon = new_wired_daemon(&cfg);
    reader = rf_pl_status_reader_from_callback(status_read, &read_ctx);
    /* Recreate to supply the production read-only status aperture. */
    rf_daemon_destroy(lease_daemon);
    cfg.pl_reader = reader;
    if (!reader || rf_daemon_create(&cfg, &lease_daemon) != 0) return 5;
    timing_before_read = second_attempt;
    timing_read_ctx = NULL;
    timing_read_offset = 0;
    rc = serve(lease_daemon, &first);
    fwrite(first.out, 1, first.out_len, stdout);
    fprintf(stderr, "LEASE_BRIDGE {\"serve_rc\":%d,\"second_closed\":%d,"
            "\"second_busy\":%d,\"acquire_calls\":%d,\"release_calls\":%d,"
            "\"lease_after_disconnect\":%s}\n", rc, second_client.closed,
            contains(&second_client, "BUSY"), probe_acquire_calls,
            probe_release_calls,
            rf_daemon_session(lease_daemon)->lease_held ? "true" : "false");
    rf_daemon_destroy(lease_daemon);
    rf_pl_status_reader_close(reader);
    rf_core_destroy(core);
    return rc < 0 ? 6 : 0;
}
