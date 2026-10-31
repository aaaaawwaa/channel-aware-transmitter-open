/* test_rf_core.c -- lifecycle and fault-injection tests for the shared RF core. */
#include "rf_core.h"
#include "fake_pl.h"
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

static rf_core_t *healthy_core(void)
{
    rf_core_t *core = NULL;
    rf_core_open_args_t open_args = { false, false };
    rf_init_request_t request = {
        true, 10000u, 80000000ull, 60000000u, 1u
    };
    rf_readback_t readback;

    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    /* init asserts RESETB and then requires a fresh window whose edge count says
     * "no DATA_CLK".  A zero count does not say that (a 100 MHz clock wraps the
     * 16-bit counter to 0), so the model must present this board's real pre-init
     * reading -- a few hundred noise edges -- not the all-zero reset value. */
    fake_pl_set_status((15u << 24) | (176u << 8));
    if (rf_core_create(&open_args, &core) != RF_ERR_OK ||
        rf_core_open(core, &open_args) != RF_ERR_OK ||
        rf_core_init(core, &request, &readback) != RF_ERR_OK) {
        rf_core_destroy(core);
        return NULL;
    }
    return core;
}


static void seed_gain_observation(void)
{
    fake_reg[0x003u] = 0xC0u; /* RX1 and RX2 enabled. */
    fake_reg[0x0FAu] = 0xAAu; /* RX1 slow AGC, plus unrelated bits. */
    fake_reg[0x0FBu] = 0x08u; /* Full gain table. */
    fake_reg[0x2B0u] = 42u;
    fake_reg[0x2B2u] = 0xE3u; /* Digital gain code is the low five bits. */
}

static void test_gain_observation(void)
{
    rf_core_t *core;
    rf_gain_observation_t observation, zero;
    const uint16_t spi_regs[] = { 0x003u, 0x0FAu, 0x0FBu, 0x2B0u, 0x2B2u };
    const uint32_t path_regs[] = { RF_REG_SRC_CFG, RF_REG_CAP_CFG };
    rf_core_open_args_t args = { false, false, NULL };
    unsigned i;
    int spi_writes, ctrl_writes;

    memset(&zero, 0, sizeof zero);
    memset(&observation, 0xA5, sizeof observation);
    check("gain observation rejects null arguments",
          rf_core_get_gain_observation(NULL, &observation) == RF_ERR_INVALID_ARGUMENT &&
          memcmp(&observation, &zero, sizeof zero) == 0 &&
          rf_core_get_gain_observation(NULL, NULL) == RF_ERR_INVALID_ARGUMENT);
    core = healthy_core();
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("gain observation rejects null output on an initialized core",
          rf_core_get_gain_observation(core, NULL) == RF_ERR_INVALID_ARGUMENT &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes);
    rf_core_destroy(core);

    fake_pl_reset();
    core = NULL;
    check("gain observation core created but not initialized",
          rf_core_create(&args, &core) == RF_ERR_OK &&
          rf_core_open(core, &args) == RF_ERR_OK);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("gain observation refuses pre-init without modifying the part",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_OFFLINE);
    rf_core_destroy(core);

    core = healthy_core(); seed_gain_observation();
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("gain observation returns actual raw registers and masks digital code",
          core != NULL && rf_core_get_gain_observation(core, &observation) == RF_ERR_OK &&
          observation.rx_enable_raw == 0xC0u && observation.agc_config1_raw == 0xAAu &&
          observation.agc_config2_raw == 0x08u && observation.mode_rx1 == 2u &&
          observation.gain_index_before == 42u && observation.gain_index_after == 42u &&
          observation.digital_gain_code == 3u && observation.index_stable);
    check("gain observation writes neither chip nor data path and keeps READY",
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_READY);
    fake_reg[0x2B0u] = 0u;
    fake_reg[0x2B2u] = 31u;
    check("gain observation accepts zero index and digital code 31 endpoints",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_OK &&
          observation.gain_index_before == 0u && observation.digital_gain_code == 31u);
    for (i = 0; i < 4; ++i) {
        fake_reg[0x0FAu] = (uint8_t)(0xA8u | i);
        check("gain observation mode comes from hardware for every two-bit encoding",
              rf_core_get_gain_observation(core, &observation) == RF_ERR_OK &&
              observation.mode_rx1 == i);
    }
    seed_gain_observation();
    fake_reg[0x2B0u] = 76u;
    check("gain observation permits gain index 76 endpoint",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_OK &&
          observation.gain_index_before == 76u);
    fake_reg[0x2B0u] = 42u;
    fake_pl_set_spi_read_change(0x2B0u, 43u);
    check("gain observation preserves valid changing indices without pretending atomicity",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_OK &&
          observation.gain_index_before == 42u && observation.gain_index_after == 43u &&
          !observation.index_stable);
    fake_pl_set_spi_read_change(0x2B0u, 77u);
    check("gain observation rejects second index out of bounds",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_HARDWARE_IO &&
          memcmp(&observation, &zero, sizeof zero) == 0 &&
          rf_core_state(core) == RF_STATE_READY);
    fake_reg[0x2B0u] = 77u;
    memset(&observation, 0xA5, sizeof observation);
    check("gain observation rejects index 77 without faulting or stale output",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_HARDWARE_IO &&
          memcmp(&observation, &zero, sizeof zero) == 0 &&
          rf_core_state(core) == RF_STATE_READY);
    seed_gain_observation(); fake_reg[0x003u] = 0x80u;
    check("gain observation refuses RX1 disabled",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE);
    seed_gain_observation(); fake_reg[0x0FBu] = 0u;
    check("gain observation refuses split table without touching its address",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes);
    seed_gain_observation(); fake_pl_set_spi_read_change(0x0FBu, 0u);
    check("gain observation refuses table mode changing to unsupported split",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          memcmp(&observation, &zero, sizeof zero) == 0);
    seed_gain_observation(); fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    check("gain observation rejects AGC mode changing during sequential reads",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_HARDWARE_IO &&
          memcmp(&observation, &zero, sizeof zero) == 0);
    rf_core_destroy(core);

    for (i = 0; i < sizeof spi_regs / sizeof spi_regs[0]; ++i) {
        core = healthy_core(); seed_gain_observation();
        fake_pl_set_spi_read_failure(spi_regs[i], 1);
        spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
        memset(&observation, 0xA5, sizeof observation);
        check("gain observation SPI error is unavailable, not FAULT or zero measurement",
              rf_core_get_gain_observation(core, &observation) == RF_ERR_HARDWARE_IO &&
              memcmp(&observation, &zero, sizeof zero) == 0 &&
              fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
              rf_core_state(core) == RF_STATE_READY);
        rf_core_destroy(core);
    }
    for (i = 0; i < sizeof path_regs / sizeof path_regs[0]; ++i) {
        core = healthy_core(); seed_gain_observation();
        fake_pl_set_read_failure(path_regs[i], 1);
        spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
        check("gain observation PL sentinel is unavailable without changing state",
              rf_core_get_gain_observation(core, &observation) == RF_ERR_HARDWARE_IO &&
              fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
              rf_core_state(core) == RF_STATE_READY);
        rf_core_destroy(core);
    }

    core = healthy_core(); seed_gain_observation();
    rf_reg_write(RF_REG_SRC_CFG, RF_SRC_SEL);
    fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("gain observation refuses live SRC_SEL even when software remains READY",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          fake_reg[0x0FAu] == 0xAAu);
    rf_core_destroy(core);

    core = healthy_core(); seed_gain_observation();
    check("gain observation armed RX setup", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
          rf_core_set_rx_arm(core, true, NULL) == RF_ERR_OK);
    /* The fake capture advances one sample per status read. */
    for (i = 0; i < 4; ++i)
        (void)rf_reg_read(RF_REG_CAP_STATUS);
    check("gain observation completed RX still armed",
          RF_CAP_DONE(rf_reg_read(RF_REG_CAP_STATUS)) != 0u);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    check("gain observation refuses CAP_ARM even with DONE",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          fake_reg[0x0FAu] == 0xAAu);
    rf_core_destroy(core);

    core = healthy_core(); seed_gain_observation();
    check("gain observation active TX setup", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    check("gain observation refuses TX_ACTIVE without writing",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_TX_ACTIVE && fake_reg[0x0FAu] == 0xAAu);
    rf_core_destroy(core);

    core = healthy_core(); seed_gain_observation();
    check("gain observation active RX setup", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
          rf_core_start_rx(core) == RF_ERR_OK);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    check("gain observation refuses RX_CAPTURING before chip reads",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_RX_CAPTURING && fake_reg[0x0FAu] == 0xAAu);
    rf_core_destroy(core);

    core = healthy_core(); seed_gain_observation();
    fake_pl_set_read_failure(RF_REG_WAVE_SEL, 1);
    check("gain observation FAULT setup",
          rf_core_set_wave_sel(core, 1u, NULL) == RF_ERR_HARDWARE_IO &&
          rf_core_state(core) == RF_STATE_FAULT);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    fake_pl_set_spi_read_change(0x0FAu, 0xA9u);
    check("gain observation refuses FAULT without trying recovery or reading chip",
          rf_core_get_gain_observation(core, &observation) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_FAULT && fake_reg[0x0FAu] == 0xAAu);
    rf_core_destroy(core);
}

static void test_configure_attenuation_range(void)
{
    rf_core_t *core = healthy_core();
    rf_config_t cfg = { 90000000ull, 60000000u, 30000000u, 30000000u,
                        90000u, 1u, 1u, 11u };
    int writes;
    uint32_t tap;
    int rc;

    check("core range test is initialized", core != NULL);
    if (!core)
        return;
    writes = fake_ctrl_writes;
    tap = rf_reg_read(RF_REG_IDELAY_TAP);
    rc = rf_core_configure(core, &cfg, NULL);
    check("core refuses excessive attenuation before programming",
          rc == RF_ERR_INVALID_ARGUMENT);
    check("core invalid configure leaves registers and READY unchanged",
          fake_ctrl_writes == writes && rf_reg_read(RF_REG_IDELAY_TAP) == tap &&
          rf_core_state(core) == RF_STATE_READY);
    cfg.attenuation_mdB = 89750u;
    check("configure accepts the exact vendor ceiling",
          rf_core_configure(core, &cfg, NULL) == RF_ERR_OK);
    writes = fake_ctrl_writes;
    cfg.attenuation_mdB = 89751u;
    check("configure refuses one mdB above ceiling with no writes",
          rf_core_configure(core, &cfg, NULL) == RF_ERR_INVALID_ARGUMENT &&
          fake_ctrl_writes == writes && rf_core_state(core) == RF_STATE_READY);
    rf_core_destroy(core);
}

/* --------------------------- the in-window gain sample (2026-10-19) ---------
 *
 * rf_core_get_gain_observation deliberately refuses to run unless the capture path
 * is idle (SRC_SEL and CAP_ARM both clear): it is the frame-BOUNDARY reading.  The
 * in-window sample needs the opposite -- it must run while the capture is ARMED --
 * so it is a separate, narrower entry point: ONE checked SPI read, no atomicity
 * claim, no write, no state change, and a failure that is REPORTED (with the
 * caller's output left untouched) rather than turned into a number.
 */
static void test_in_window_gain_read(void)
{
    rf_core_t *core;
    rf_core_open_args_t open_args = { false, false, NULL };
    uint32_t words[4];
    uint8_t index;
    int spi_writes, ctrl_writes;
    static const uint8_t values[3] = { 0u, 42u, 76u };
    unsigned k;

    printf("=== in-window RX1 gain read ===\n");
    index = 0xA5u;
    check("in-window gain read rejects a null core and leaves the output alone",
          rf_core_read_rx1_gain_index(NULL, &index) == RF_ERR_INVALID_ARGUMENT &&
          index == 0xA5u);

    core = healthy_core();
    check("in-window gain read rejects a null output",
          core && rf_core_read_rx1_gain_index(core, NULL) == RF_ERR_INVALID_ARGUMENT);
    rf_core_destroy(core);

    /* Opened but never initialized: the part has not been configured, so an index
     * read off it would be a number a reader could mistake for a measurement. */
    fake_pl_reset();
    core = NULL;
    check("in-window gain read core created but not initialized",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK);
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes; index = 0xA5u;
    check("in-window gain read refuses pre-init without touching the part",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_INVALID_STATE &&
          index == 0xA5u && fake_spi_writes == spi_writes &&
          fake_ctrl_writes == ctrl_writes && rf_core_state(core) == RF_STATE_OFFLINE);
    rf_core_destroy(core);

    /* The value is the part's, for every seeded index including both endpoints. */
    core = healthy_core();
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    for (k = 0; k < 3u; ++k) {
        char name[96];
        fake_reg[0x2B0u] = values[k];
        index = 0xA5u;
        snprintf(name, sizeof name,
                 "in-window gain read returns the part's index %u", values[k]);
        check(name, core && rf_core_read_rx1_gain_index(core, &index) == RF_ERR_OK &&
                    index == values[k]);
    }
    fake_reg[0x2B0u] = (uint8_t)(0x80u | 42u);
    index = 0xA5u;
    check("in-window gain read masks the register to the seven index bits",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_OK && index == 42u);
    check("in-window gain read writes nothing and keeps READY",
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_READY);
    rf_core_destroy(core);

    /* Failure and out-of-range are REPORTED, named by the caller's output, and are
     * NOT fatal: the observation may not fault the core it observes. */
    core = healthy_core();
    fake_reg[0x2B0u] = 77u;
    index = 0xA5u;
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("in-window gain read rejects an out-of-range index without a stale output",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_HARDWARE_IO &&
          index == 0xA5u && fake_spi_writes == spi_writes &&
          fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_READY);
    fake_reg[0x2B0u] = 76u;
    fake_pl_set_spi_read_failure(0x2B0u, 1);
    index = 0xA5u;
    check("an unanswered gain read is HARDWARE_IO and does not fault the core",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_HARDWARE_IO &&
          index == 0xA5u && rf_core_state(core) == RF_STATE_READY);
    fake_pl_set_spi_read_failure(0x2B0u, 0);
    index = 0xA5u;
    check("the same core still answers after the failed read",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_OK && index == 76u);
    rf_core_destroy(core);

    /* The whole point of the entry point: it is reachable with the capture ARMED,
     * which is exactly the state rf_core_get_gain_observation refuses. */
    core = healthy_core();
    fake_reg[0x2B0u] = 42u;
    fake_reg[0x003u] = 0xC0u;
    fake_reg[0x0FAu] = 0xAAu;
    fake_reg[0x0FBu] = 0x08u;
    index = 0xA5u;
    check("the idle-gated observation refuses the armed state",
          rf_core_prepare_rx(core, 1u, 4u, false) == RF_ERR_OK &&
          rf_core_start_rx(core) == RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_RX_CAPTURING);
    check("the in-window read answers while the capture is armed",
          rf_core_read_rx1_gain_index(core, &index) == RF_ERR_OK && index == 42u &&
          rf_core_state(core) == RF_STATE_RX_CAPTURING);
    check("the observed capture still finishes and releases the arm",
          rf_core_finish_rx(core, words, 4u) == RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_READY &&
          (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) == 0u);
    rf_core_destroy(core);
}

static void test_idelay_lane_update(void)
{
    rf_core_t *core = healthy_core();
    uint32_t en = 0, tap = 0;
    int spi_before;

    check("tap-only rejects a null core and invalid mask/tap on a READY core", core != NULL &&
          rf_core_set_idelay_lanes(NULL, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
          rf_core_set_idelay_lanes(core, 0u, 25u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
          rf_core_set_idelay_lanes(core, 0x80u, 25u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
          rf_core_set_idelay_lanes(core, 0x02u, 32u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT);
    if (!core) return;

    /* Pretend the ordinary configure left all lanes at tap29. */
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
    fake_ctrl_log_count = 0;
    spi_before = fake_spi_writes;
    check("tap-only lane1 update succeeds with expected three-write sequence", 
          rf_core_set_idelay_lanes(core, 0x02u, 25u, &en, &tap) == RF_ERR_OK &&
          en == 0x02u && tap == 25u &&
          fake_ctrl_log_count == 3 &&
          fake_ctrl_log_addr[0] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[0] == 0u &&
          fake_ctrl_log_addr[1] == RF_CTRL_A(RF_REG_IDELAY_TAP) &&
          fake_ctrl_log_value[1] == 25u &&
          fake_ctrl_log_addr[2] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[2] == 0x02u &&
          fake_spi_writes == spi_before);
    check("tap-only lane1 update leaves the core READY",
          rf_core_state(core) == RF_STATE_READY);

    fake_ctrl_log_count = 0;
    check("tap-only restore all lanes uses close-then-tap-then-all",
          rf_core_set_idelay_lanes(core, 0x7Fu, 29u, &en, &tap) == RF_ERR_OK &&
          en == 0x7Fu && tap == 29u &&
          fake_ctrl_log_count == 3 &&
          fake_ctrl_log_value[0] == 0u &&
          fake_ctrl_log_value[1] == 29u &&
          fake_ctrl_log_value[2] == 0x7Fu);

    fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG), RF_SRC_SEL);
    fake_ctrl_log_count = 0;
    check("tap-only refuses when TX source is selected before any write",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
          fake_ctrl_log_count == 0);
    fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG), 0u);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG), RF_CAP_ARM);
    fake_ctrl_log_count = 0;
    check("tap-only refuses armed RX before any write",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
          fake_ctrl_log_count == 0);
    fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG), 0u);

    fake_pl_set_read_failure(RF_REG_IDELAY_EN, 1);
    check("tap-only readback failure faults the core",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    fake_pl_set_read_failure(RF_REG_IDELAY_EN, 0);
    rf_core_destroy(core);
}

/* Counterexamples for the tap-only lane update.  "Before any PL write" is measured
 * with the fake PL's ordered axi_ctrl_0 write log (fake_ctrl_log_count) and its MMIO
 * write counter, never inferred from the source.  Exact register READ counts are
 * deliberately NOT asserted: fake_pl has no MMIO read counter, so this file cannot
 * prove them and does not claim to; the read side is witnessed only by which
 * injected sentinel reads are reached. */
static void test_idelay_lane_update_refusals(void)
{
    rf_core_t *core;
    rf_core_open_args_t open_args = { false, false, NULL };
    int mmio, log;

    printf("=== idelay lane update refusals ===\n");

    /* Created but never opened: the API needs `opened` as well as READY. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    core = NULL;
    check("the unopened-core refusal fixture is created",
          rf_core_create(&open_args, &core) == RF_ERR_OK && core != NULL);
    if (core) {
        mmio = fake_mmio_writes;
        log = fake_ctrl_log_count;
        check("tap-only refuses a created-but-unopened core with zero PL writes",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
              rf_core_state(core) == RF_STATE_OFFLINE &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == log);
        rf_core_destroy(core);
    }

    /* Opened but not initialized: a live state, just not READY. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    core = NULL;
    check("the opened-but-not-initialized refusal fixture opens OFFLINE",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_OFFLINE);
    if (core) {
        mmio = fake_mmio_writes;
        log = fake_ctrl_log_count;
        check("tap-only refuses an opened non-READY core with zero PL writes",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
              rf_core_state(core) == RF_STATE_OFFLINE &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == log);
        rf_core_destroy(core);
    }

    /* FAULT: produced by an unrelated setter that cannot be confused with this one. */
    core = healthy_core();
    check("the FAULT refusal fixture is a READY core", core != NULL);
    if (core) {
        fake_pl_set_read_failure(RF_REG_PHASE_INC, 1);
        (void)rf_core_set_phase_inc(core, 7u, NULL);
        fake_pl_set_read_failure(RF_REG_PHASE_INC, 0);
        mmio = fake_mmio_writes;
        log = fake_ctrl_log_count;
        check("tap-only refuses a FAULT core, preserves FAULT and writes nothing",
              rf_core_state(core) == RF_STATE_FAULT &&
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
              rf_core_state(core) == RF_STATE_FAULT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == log);
        rf_core_destroy(core);
    }

    /* Invalid arguments on a healthy READY core: all rejected before the bus. */
    core = healthy_core();
    check("the argument-refusal fixture is a READY core", core != NULL);
    if (core) {
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        check("tap-only rejects mask 0 before any PL write",
              rf_core_set_idelay_lanes(core, 0x00u, 25u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        check("tap-only rejects mask 0x80 before any PL write",
              rf_core_set_idelay_lanes(core, 0x80u, 25u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        check("tap-only rejects tap 32 before any PL write",
              rf_core_set_idelay_lanes(core, 0x02u, 32u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        check("tap-only argument refusals leave the core READY",
              rf_core_state(core) == RF_STATE_READY);

        fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG), RF_SRC_SEL);
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        check("tap-only refuses a selected TX source before any PL write and without a fault",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
              rf_core_state(core) == RF_STATE_READY &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG), 0u);

        fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG), RF_CAP_ARM);
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        check("tap-only refuses an armed RX before any PL write and without a fault",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
              rf_core_state(core) == RF_STATE_READY &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG), 0u);

        fake_pl_set_read_failure(RF_REG_SRC_CFG, 1);
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        check("tap-only turns a SRC_CFG sentinel read into HARDWARE_IO before any PL write",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
              rf_core_state(core) == RF_STATE_FAULT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        fake_pl_set_read_failure(RF_REG_SRC_CFG, 0);
        rf_core_destroy(core);

        core = healthy_core();
        fake_pl_set_read_failure(RF_REG_CAP_CFG, 1);
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        check("tap-only turns a CAP_CFG sentinel read into HARDWARE_IO before any PL write",
              core != NULL &&
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
              rf_core_state(core) == RF_STATE_FAULT &&
              fake_mmio_writes == mmio && fake_ctrl_log_count == 0);
        fake_pl_set_read_failure(RF_REG_CAP_CFG, 0);
        rf_core_destroy(core);
    }
}

/* The accepted path and the readback faults, with the write side measured exactly.
 *
 * fake_mmio_writes counts EVERY PL write, including the SPI master's own registers
 * and the BRAM ports, while fake_ctrl_log_* records only the axi_ctrl_0 window in
 * order.  "mmio delta == 3 == three logged axi_ctrl_0 writes" therefore proves both
 * that the sequence is the three documented writes and that no AD9361 transaction
 * (which must program the SPI master), no BRAM write and no restore write happened.
 *
 * NOT provable here: a NON-sentinel EN/TAP readback mismatch.  The fake PL returns
 * exactly what was written (or the 0xDEADBEEF sentinel via the injected read
 * fault), so no allowed test input can make the two disagree; the branch is
 * documented in the evidence report instead of being asserted vacuously.  Exact
 * register READ counts are likewise unobservable (no MMIO read counter). */
static void test_idelay_lane_update_write_witness(void)
{
    rf_core_t *core = healthy_core();
    uint32_t en = 0, tap = 0;
    int mmio, ctrl, spi, rejected;

    printf("=== idelay lane update write witness ===\n");
    check("the write-witness fixture is a READY core", core != NULL);
    if (!core)
        return;

    /* Pretend the ordinary configure left every lane at tap 29. */
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
    fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
    fake_ctrl_log_count = 0;
    mmio = fake_mmio_writes;
    ctrl = fake_ctrl_writes;
    spi = fake_spi_writes;
    rejected = fake_spi_rejected;
    check("lane1/tap25 succeeds and reads back exactly what was written",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, &en, &tap) == RF_ERR_OK &&
          en == 0x02u && tap == 25u && rf_core_state(core) == RF_STATE_READY);
    check("lane1/tap25 performs exactly EN=0, TAP=25, EN=2 and nothing else on MMIO",
          fake_ctrl_log_count == 3 &&
          fake_ctrl_log_addr[0] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[0] == 0u &&
          fake_ctrl_log_addr[1] == RF_CTRL_A(RF_REG_IDELAY_TAP) &&
          fake_ctrl_log_value[1] == 25u &&
          fake_ctrl_log_addr[2] == RF_CTRL_A(RF_REG_IDELAY_EN) &&
          fake_ctrl_log_value[2] == 0x02u &&
          fake_mmio_writes - mmio == 3 && fake_ctrl_writes - ctrl == 3);
    check("lane1/tap25 performs no AD9361/SPI-master, BRAM or restore write",
          fake_spi_writes == spi && fake_spi_rejected == rejected &&
          fake_mmio_writes - mmio == 3);

    /* Both ends of the accepted mask/tap domain, not just the middle. */
    fake_ctrl_log_count = 0;
    check("mask 0x01/tap 0 is accepted with exact readback",
          rf_core_set_idelay_lanes(core, 0x01u, 0u, &en, &tap) == RF_ERR_OK &&
          en == 0x01u && tap == 0u && fake_ctrl_log_count == 3);
    fake_ctrl_log_count = 0;
    check("mask 0x7F/tap 31 is accepted with exact readback (explicit restore form)",
          rf_core_set_idelay_lanes(core, 0x7Fu, 31u, &en, &tap) == RF_ERR_OK &&
          en == 0x7Fu && tap == 31u && fake_ctrl_log_count == 3);

    /* An EN readback sentinel: a fault AFTER the three writes, once. */
    fake_ctrl_log_count = 0;
    mmio = fake_mmio_writes;
    ctrl = fake_ctrl_writes;
    spi = fake_spi_writes;
    rejected = fake_spi_rejected;
    fake_pl_set_read_failure(RF_REG_IDELAY_EN, 1);
    check("an EN readback sentinel is HARDWARE_IO and FAULT after exactly three writes",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
          rf_core_state(core) == RF_STATE_FAULT &&
          fake_ctrl_log_count == 3 &&
          fake_ctrl_log_value[0] == 0u && fake_ctrl_log_value[1] == 25u &&
          fake_ctrl_log_value[2] == 0x02u &&
          fake_mmio_writes - mmio == 3 && fake_ctrl_writes - ctrl == 3);
    fake_pl_set_read_failure(RF_REG_IDELAY_EN, 0);
    check("the EN-readback fault triggers no write retry or restore and is not auto-cleared",
          rf_core_state(core) == RF_STATE_FAULT &&
          fake_ctrl_log_count == 3 &&
          fake_mmio_writes - mmio == 3 && fake_spi_writes == spi &&
          fake_spi_rejected == rejected &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x02u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 25u);
    check("a faulted core does not self-heal: a repeat call is refused with no write",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_INVALID_STATE &&
          rf_core_state(core) == RF_STATE_FAULT && fake_ctrl_log_count == 3);
    rf_core_destroy(core);

    /* The same with the TAP confirmation read failing: the EN read has already
     * succeeded and the write sequence is still exactly three. */
    core = healthy_core();
    check("the TAP-sentinel fixture is a READY core", core != NULL);
    if (core) {
        fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_EN), 0x7Fu);
        fake_pl_write(RF_CTRL_A(RF_REG_IDELAY_TAP), 29u);
        fake_ctrl_log_count = 0;
        mmio = fake_mmio_writes;
        ctrl = fake_ctrl_writes;
        spi = fake_spi_writes;
        rejected = fake_spi_rejected;
        fake_pl_set_read_failure(RF_REG_IDELAY_TAP, 1);
        check("a TAP readback sentinel is HARDWARE_IO and FAULT after exactly three writes",
              rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
              rf_core_state(core) == RF_STATE_FAULT &&
              fake_ctrl_log_count == 3 &&
              fake_mmio_writes - mmio == 3 &&
              fake_spi_writes == spi && fake_spi_rejected == rejected);
        fake_pl_set_read_failure(RF_REG_IDELAY_TAP, 0);
        rf_core_destroy(core);
    }
}

/* The legacy boolean contract, unchanged by the new mask API: `idelay_en` is a
 * 0/1 flag (1 -> all seven lanes 0x7F), never a mask. */
static void test_legacy_configure_idelay_boolean_semantics(void)
{
    rf_core_t *core = healthy_core();
    rf_config_t config = {
        90000000ull, 50000000u, 30000000u, 30000000u, 20000u, 1u, 1u, 29u
    };

    printf("=== legacy configure idelay_en boolean semantics ===\n");
    check("the legacy-flag fixture is a READY core", core != NULL);
    if (!core)
        return;

    config.idelay_en = 1;
    config.idelay_tap = 29;
    check("rf_core_configure maps idelay_en=1 to all lanes 0x7F and keeps the tap",
          rf_core_configure(core, &config, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x7Fu &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);
    config.idelay_en = 0;
    check("rf_core_configure maps idelay_en=0 to closed lanes and keeps the tap",
          rf_core_configure(core, &config, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);
    /* The new lane-selective form stays separate: the legacy call is the explicit
     * all-lanes restore even after a lane-only update. */
    check("a lane-only update is superseded by the legacy all-lanes configure",
          rf_core_set_idelay_lanes(core, 0x02u, 25u, NULL, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x02u &&
          rf_core_configure(core, &config, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0u &&
          rf_reg_read(RF_REG_IDELAY_TAP) == 29u);
    rf_core_destroy(core);
}

static void test_interface_delay_control(void)
{
    rf_core_t *core;
    rf_interface_status_t before, after, readback;
    int spi_writes, ctrl_writes;

    printf("=== fixed AD9361 interface registers / restricted RX data delay ===\n");
    core = healthy_core();
    if (!core) { check("interface delay fixture is READY", 0); return; }
    fake_reg[0x006u] = 0x34u; /* DATA_CLK_DELAY=3, RX_DATA_DELAY=4 */
    fake_reg[0x007u] = 0x70u;
    fake_reg[0x010u] = 0x50u;
    fake_reg[0x011u] = 0x0Cu;
    fake_reg[0x012u] = 0x10u; /* LVDS */
    spi_writes = fake_spi_writes; ctrl_writes = fake_ctrl_writes;
    check("interface read returns all five fixed registers and decoded fields",
          rf_core_read_interface_status(core, &readback) == RF_ERR_OK &&
          readback.valid && readback.reg_006 == 0x34u && readback.reg_007 == 0x70u &&
          readback.reg_010 == 0x50u && readback.reg_011 == 0x0Cu &&
          readback.reg_012 == 0x10u && readback.rx_data_delay == 4u &&
          readback.data_clk_delay == 3u && readback.tx_data_delay == 0u &&
          readback.fb_clk_delay == 7u && readback.lvds_mode &&
          !readback.single_data_rate && !readback.full_port);
    check("interface read writes neither chip nor data path and keeps READY",
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_READY);

    check("RX data delay rejects 16 before writing",
          rf_core_set_rx_data_delay(core, 16u, NULL, NULL) == RF_ERR_INVALID_ARGUMENT &&
          fake_spi_writes == spi_writes && fake_ctrl_writes == ctrl_writes);
    check("RX data delay writes only low nibble and preserves every other register",
          rf_core_set_rx_data_delay(core, 5u, &before, &after) == RF_ERR_OK &&
          before.reg_006 == 0x34u && after.reg_006 == 0x35u &&
          after.rx_data_delay == 5u && after.data_clk_delay == 3u &&
          after.reg_007 == 0x70u && after.reg_010 == 0x50u &&
          after.reg_011 == 0x0Cu && after.reg_012 == 0x10u &&
          fake_spi_writes == spi_writes + 1 && fake_ctrl_writes == ctrl_writes &&
          rf_core_state(core) == RF_STATE_READY);
    check("RX data delay restores the actual original low nibble",
          rf_core_set_rx_data_delay(core, before.rx_data_delay, &before, &after) == RF_ERR_OK &&
          after.reg_006 == 0x34u && after.rx_data_delay == 4u &&
          fake_spi_writes == spi_writes + 2 && rf_core_state(core) == RF_STATE_READY);

    /* TX source / RX arm must be refused BEFORE the first write. */
    rf_reg_write(RF_REG_SRC_CFG, RF_SRC_SEL);
    spi_writes = fake_spi_writes;
    check("RX data delay refuses live TX source before any write",
          rf_core_set_rx_data_delay(core, 5u, NULL, NULL) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes);
    rf_reg_write(RF_REG_SRC_CFG, 0u);
    rf_reg_write(RF_REG_CAP_CFG, RF_CAP_ARM);
    spi_writes = fake_spi_writes;
    check("RX data delay refuses armed RX before any write",
          rf_core_set_rx_data_delay(core, 5u, NULL, NULL) == RF_ERR_INVALID_STATE &&
          fake_spi_writes == spi_writes);
    rf_reg_write(RF_REG_CAP_CFG, 0u);

    /* Original read failure: no write at all. */
    fake_pl_set_spi_read_failure(0x006u, 1);
    spi_writes = fake_spi_writes;
    check("RX data delay refuses an unreadable original value without writing",
          rf_core_set_rx_data_delay(core, 5u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
          fake_spi_writes == spi_writes && rf_core_state(core) == RF_STATE_READY);
    fake_pl_set_spi_read_failure(0x006u, 0);

    /* A rejected write must not become a success. */
    fake_pl_set_reject_all_writes(1);
    check("RX data delay write rejection is HARDWARE_IO/FAULT, not success",
          rf_core_set_rx_data_delay(core, 5u, NULL, NULL) == RF_ERR_HARDWARE_IO &&
          rf_core_state(core) == RF_STATE_FAULT);
    fake_pl_set_reject_all_writes(0);
    rf_core_destroy(core);

    /* A core opened but not initialized has no readable interface. */
    {
        rf_core_t *uninit = NULL;
        rf_core_open_args_t args = { false, false, NULL };
        fake_pl_reset();
        check("uninitialized core cannot read interface and is not initialized implicitly",
              rf_core_create(&args, &uninit) == RF_ERR_OK &&
              rf_core_open(uninit, &args) == RF_ERR_OK &&
              rf_core_read_interface_status(uninit, &readback) == RF_ERR_INVALID_STATE);
        rf_core_destroy(uninit);
    }
}

int main(void)
{
    rf_core_t *core;
    rf_core_t *bad;
    rf_core_t *secondary;
    rf_core_open_args_t open_args = { false, false };
    rf_init_request_t request = {
        true, 10000u, 80000000ull, 60000000u, 1u
    };
    rf_init_request_t missing_atten = request;
    rf_readback_t readback;
    rf_config_t config = {
        90000000ull, 50000000u, 30000000u, 30000000u, 20000u, 1u, 1u, 7u
    };
    uint32_t tx[4] = { 0x10001u, 0x20002u, 0x30003u, 0x40004u };
    uint32_t rx[4];
    rf_capture_status_t capture;
    rf_hw_status_t hw;
    rf_pl_snapshot_t snap;
    uint8_t loopback;
    uint32_t mode_rb;
    uint32_t tx_cfg, tx_len, tx_pos;

    printf("=== test_rf_core ===\n");
    test_idelay_lane_update();
    test_idelay_lane_update_refusals();
    test_idelay_lane_update_write_witness();
    test_legacy_configure_idelay_boolean_semantics();
    test_interface_delay_control();
    test_gain_observation();
    test_in_window_gain_read();
    test_configure_attenuation_range();
    core = healthy_core();
    config.idelay_en = 1;
    check("configure loads all six data lanes and frame lane", core != NULL &&
          rf_core_configure(core, &config, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x7Fu &&
          rf_core_configure(core, &config, NULL) == RF_ERR_OK &&
          rf_reg_read(RF_REG_IDELAY_EN) == 0x7Fu);
    rf_core_destroy(core);
    missing_atten.attenuation_mdB_present = false;
    check("create rejects null output", rf_core_create(NULL, NULL) == RF_ERR_INVALID_ARGUMENT);
    check("open rejects null core", rf_core_open(NULL, &open_args) == RF_ERR_INVALID_ARGUMENT);
    check("create succeeds", rf_core_create(&open_args, &bad) == RF_ERR_OK);
    check("init requires explicit attenuation",
          rf_core_open(bad, &open_args) == RF_ERR_OK &&
          rf_core_init(bad, &missing_atten, NULL) == RF_ERR_INVALID_ARGUMENT &&
          rf_core_state(bad) == RF_STATE_OFFLINE);
    rf_core_destroy(bad);

    fake_pl_reset();
    fake_pl_set_spi_init_failure(1);
    /* Pretend a previous command left the part running.  The preload is a
     * distinctive sentinel (all nine pins high), not the literal post-init value
     * -- that one is 0x007 -- because what this test needs is a pattern that any
     * write at all would visibly destroy.  `open` must not write the GPIO
     * aperture, so this value has to survive it.  `gpio_init()` itself would load
     * the register with 0, which asserts RESETB (bit 2, active low) and clears
     * ENABLE (bit 1) -- the exact regression this pins down.  A preload of 0
     * would make the test vacuous, because a zeroing write and no write at all
     * look the same. */
    fake_pl_write(RF_GPIO_A(RF_GPIO_DATA), 0x1FFu);
    bad = NULL;
    check("failed open releases the singleton without driving any pin",
          rf_core_create(&open_args, &bad) == RF_ERR_OK &&
          rf_core_open(bad, &open_args) == RF_ERR_HARDWARE_IO &&
          fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x1FFu);
    fake_pl_set_spi_init_failure(0);
    check("a later open reuses the released singleton",
          rf_core_open(bad, &open_args) == RF_ERR_OK &&
          rf_core_state(bad) == RF_STATE_OFFLINE);
    rf_core_destroy(bad);

    /* The CLI opens a core once per command, so a read-only command must leave
     * the part exactly as `init` left it.  Preload the running state and read a
     * snapshot: neither the open nor the read may touch the pin register. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    fake_pl_write(RF_GPIO_A(RF_GPIO_DATA), 0x1FFu);
    core = NULL;
    check("open and a read-only command leave the AD9361 pins untouched",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_get_pl_snapshot(core, &snap) == RF_ERR_OK &&
          fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x1FFu);
    rf_core_destroy(core);

    /* Fail closed: init must refuse when the pre-init window count is zero, since
     * that is also exactly what a still-running 100 MHz DATA_CLK produces.  The
     * refusal is a hardware fault, so it must land in FAULT -- not mid-init. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    check("init refuses a zero-count pre-init window and faults",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_init(core, &request, &readback) == RF_ERR_HARDWARE_IO &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    check("healthy init reaches READY", core != NULL &&
          rf_core_state(core) == RF_STATE_READY);
    /* `init` is the one command that must drive the part, so pin the sequence:
     * TXNRX and ENABLE high, RESETB released.  This guards the fix in the other
     * direction -- removing the pin writes from `open` must not remove them
     * from `init`. */
    check("init drives the part up: TXNRX/ENABLE high, RESETB released",
          core != NULL &&
          (fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) &
           ((1u << RF_GPIO_TXNRX) | (1u << RF_GPIO_ENABLE) |
            (1u << RF_GPIO_RESETB))) ==
          ((1u << RF_GPIO_TXNRX) | (1u << RF_GPIO_ENABLE) |
           (1u << RF_GPIO_RESETB)));
    check("configuration writes and reads back", core != NULL &&
          rf_core_configure(core, &config, &readback) == RF_ERR_OK &&
          readback.lo_hz == config.lo_hz &&
          readback.sample_rate_hz == config.sample_rate_hz &&
          readback.rx_lo_hz == config.lo_hz &&
          readback.tx_lo_hz == config.lo_hz &&
          readback.rx_sample_rate_hz == config.sample_rate_hz &&
          readback.tx_sample_rate_hz == config.sample_rate_hz &&
          readback.attenuation_mdB == config.attenuation_mdB &&
          readback.rx_bandwidth_hz == config.rx_bandwidth_hz &&
          readback.tx_bandwidth_hz == config.tx_bandwidth_hz &&
          readback.tx_fir_enabled == 1u && readback.rx_fir_enabled == 1u &&
          readback.tx_fir_config_verified && readback.rx_fir_config_verified &&
          !readback.hardware_verified);
    check("TX BRAM write is read back", core != NULL &&
          rf_core_write_tx(core, tx, 4) == RF_ERR_OK);
    check("TX starts and stop verifies inactive", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          rf_core_stop_tx(core, &hw) == RF_ERR_OK && !hw.tx_active);
    secondary = NULL;
    check("second core is rejected without disturbing owner",
          rf_core_create(&open_args, &secondary) == RF_ERR_OK &&
          rf_core_open(secondary, &open_args) == RF_ERR_INVALID_STATE &&
          rf_core_state(core) == RF_STATE_READY &&
          rf_core_set_phase_inc(core, 17u, NULL) == RF_ERR_OK);
    rf_core_destroy(secondary);
    rf_core_destroy(core);

    fake_pl_reset();
    fake_pl_set_status(1u);  /* sticky CLK_SEEN, current count still zero */
    /* A zero edge count is ambiguous, not evidence of a dead line: the 2^15-cycle
     * window with a 16-bit counter wraps a 100 MHz DATA_CLK (fs = 50 MSPS in 1R1T)
     * to exactly 0.  A clock that might be running is not a quiet one, so this
     * fails closed instead of accepting the count at face value. */
    check("fresh zero-count window is ambiguous and blocks PHY mode change",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_set_phy_mode_safe(core, 0u, &mode_rb) == RF_ERR_HARDWARE_IO);
    rf_core_destroy(core);

    fake_pl_reset();
    fake_pl_set_status((15u << 24) | (176u << 8));  /* measured quiet-line noise */
    check("fresh low-count noise window still allows PHY mode change",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_set_phy_mode_safe(core, 0u, &mode_rb) == RF_ERR_OK &&
          mode_rb == 0u);
    rf_core_destroy(core);

    /* The CLI formats `status`/`plstate` from decoded snapshot fields, because
     * rf_main.c deliberately includes no platform header.  Pin the decode, and
     * pin the two rates APART: `data_clk_base_hz` is the n=0 alias that the
     * pre-init negative criterion judges, while `data_clk_hz` is resolved
     * against the configured interface and on this same quiet window lands
     * 100 MHz up.  Judging the ceiling with the resolved value fails a healthy
     * board -- which is exactly what `plcfg` used to do.  win_done/win_seq are
     * not asserted here: the fake advances them as a side effect of reading. */
    core = healthy_core();
    check("snapshot decodes STATUS and separates base from resolved",
          core != NULL &&
          rf_core_get_pl_snapshot(core, &snap) == RF_ERR_OK &&
          snap.status_win_cycles == 176u && snap.status_win_log2 == 15u &&
          snap.status_clk_seen == 0u && snap.status_rst_source_ok == 0u &&
          snap.status_fresh &&
          snap.data_clk_base_hz == ((176ull * 50000000ull) >> 15) &&
          snap.data_clk_alias_step_hz == ((65536ull * 50000000ull) >> 15) &&
          snap.data_clk_alias_index == 1 &&
          snap.data_clk_base_hz < RF_NOCLOCK_MAX_HZ &&
          snap.data_clk_hz >= RF_NOCLOCK_MAX_HZ);
    rf_core_destroy(core);

    /* `plcfg` and `2r2t` must not write the pin register.  Baseline never did,
     * and the CLI opens a core per command, so a pin write there resets the part
     * the previous command left running.  After a healthy `init` the register is
     * TXNRX|ENABLE|RESETB-released = 0x007 (RESETB is active low, so released is
     * bit 2 SET); nothing below may change it.  On the round-4 code
     * `rf_core_set_phy_mode_safe` asserted RESETB and zeroed the register, so
     * this fails there. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8));
    fake_pl_write(RF_GPIO_A(RF_GPIO_DATA), 0x007u);
    core = NULL;
    check("plcfg and a mode switch leave the pin register untouched",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_configure_pl(core, 1, 1, 15, 128, &snap) == RF_ERR_OK &&
          fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x007u &&
          rf_core_set_phy_mode_safe(core, 0u, &mode_rb) == RF_ERR_OK &&
          fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) == 0x007u);
    rf_core_destroy(core);

    /* A window that never advances is a MEASUREMENT of the observation channel,
     * not a fault: `status` exists to print exactly that, so the snapshot has to
     * carry it rather than refuse -- and `status_text`'s STALE branch was dead
     * code while the snapshot faulted instead.  The word here carries a NONZERO
     * count and clk_seen set, which is the only shape that reaches that branch:
     * a zero count or a clear clk_seen takes the NO CLOCK branch instead, so a
     * test built on the all-zero reset default would never exercise STALE.  A
     * sentinel read is still a fault, because then there is no word to report. */
    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_status((15u << 24) | (176u << 8) | 1u);
    fake_pl_set_status_window_stuck(1);
    core = NULL;
    check("a stuck window is reported stale, not faulted",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_get_pl_snapshot(core, &snap) == RF_ERR_OK &&
          !snap.status_fresh && snap.status_win_cycles == 176u &&
          snap.status_clk_seen == 1u && rf_core_state(core) == RF_STATE_OFFLINE);
    rf_core_destroy(core);

    fake_pl_reset();
    fake_pl_set_product_id(0x0Au);
    fake_pl_set_read_failure(RF_REG_STATUS, 1);
    core = NULL;
    check("a sentinel STATUS read still faults the snapshot",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_get_pl_snapshot(core, &snap) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    fake_pl_reset();
    fake_pl_set_status((15u << 24) | (10000u << 8));
    check("fresh active DATA_CLK blocks PHY mode change",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_set_phy_mode_safe(core, 0u, NULL) == RF_ERR_HARDWARE_IO);
    rf_core_destroy(core);

    fake_pl_reset();
    fake_pl_set_status_window_stuck(1);
    check("stale status window blocks PHY mode change",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_open(core, &open_args) == RF_ERR_OK &&
          rf_core_set_phy_mode_safe(core, 0u, NULL) == RF_ERR_TIMEOUT);
    rf_core_destroy(core);

#ifdef PLATFORM_HOST
    core = healthy_core();
    check("fake vendor getter fault is not shadow state", core != NULL &&
          rf_core_host_set_vendor_readback_fault(core, 1) == RF_ERR_OK &&
          rf_core_get_readback(core, &readback) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_READY);
    rf_core_destroy(core);
#endif

    core = healthy_core();
    fake_pl_set_tx_position_stuck(1);
    check("stuck TX reader becomes an error and FAULT", core != NULL &&
          rf_core_write_tx(core, tx, 4) == RF_ERR_OK &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    check("stop-after-active fault is not reported as stopped", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          (fake_pl_set_stop_active(1), rf_core_stop_tx(core, &hw) != RF_ERR_OK) &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    /* rf_core_safe_shutdown is the deliberate stop path the daemon will call; the
     * CLI exposes no stop command, so without these three cases it would be an
     * unverified public lifecycle function. */
    core = healthy_core();
    check("safe shutdown clears source, drops the pins, reaches OFFLINE", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          rf_core_safe_shutdown(core, &hw) == RF_ERR_OK &&
          !hw.tx_active && !hw.rx_busy && !hw.rx_done &&
          rf_core_state(core) == RF_STATE_OFFLINE &&
          (fake_pl_read(RF_GPIO_A(RF_GPIO_DATA)) &
           ((1u << RF_GPIO_ENABLE) | (1u << RF_GPIO_RESETB))) == 0u &&
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u);
    rf_core_destroy(core);

    core = NULL;
    check("safe shutdown of a never-opened core reports stopped",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_safe_shutdown(core, &hw) == RF_ERR_OK &&
          !hw.tx_active && !hw.rx_busy && !hw.rx_done &&
          rf_core_state(core) == RF_STATE_OFFLINE);
    rf_core_destroy(core);

    core = healthy_core();
    check("safe shutdown that cannot clear source becomes FAULT", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          (fake_pl_set_stop_active(1), rf_core_safe_shutdown(core, &hw)) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    /* rf_core_release_part_keep_state is the state-free half: the disconnect rule
     * needs a part brought to rest after it has FAULTED, WITHOUT erasing the FAULT
     * that two other criteria require to stay visible.  Its contract is that the
     * STATE IS NEVER TOUCHED, which is why the assertions below expect the
     * pre-existing state to survive rather than a tidy READY. */
    core = healthy_core();
    check("release stops the part and leaves the state exactly as it found it", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_TX_ACTIVE &&
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) != 0u &&
          rf_core_release_part_keep_state(core, &hw) == RF_ERR_OK &&
          !hw.tx_active && !hw.rx_busy && !hw.rx_done &&
          (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u &&
          rf_core_state(core) == RF_STATE_TX_ACTIVE);
    rf_core_destroy(core);

    {
        uint32_t word = 0x00100020u;
        core = healthy_core();
        check("release clears a FAULTED part and keeps the FAULT as evidence", core != NULL &&
              rf_core_write_tx(core, &word, 1) == RF_ERR_OK &&
              rf_core_configure_tx_path(core, 1, true, NULL) == RF_ERR_OK &&
              rf_core_set_tx_source(core, true, NULL) == RF_ERR_OK &&
              rf_core_prepare_rx(core, 0, 1, false) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              (fake_pl_set_capture_done_stuck(1),
               rf_core_read_rx(core, rx, 1) == RF_ERR_TIMEOUT) &&
              rf_core_state(core) == RF_STATE_FAULT &&
              (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) != 0u &&
              rf_core_release_part_keep_state(core, &hw) == RF_ERR_OK &&
              !hw.tx_active && !hw.rx_busy &&
              (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) == 0u &&
              rf_core_state(core) == RF_STATE_FAULT);
        fake_pl_set_capture_done_stuck(0);
        rf_core_destroy(core);
    }

    core = healthy_core();
    check("a release that cannot clear the source reports it and changes no state", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          rf_core_start_tx(core) == RF_ERR_OK &&
          (fake_pl_set_stop_active(1),
           rf_core_release_part_keep_state(core, &hw)) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_TX_ACTIVE);
    fake_pl_set_stop_active(0);
    rf_core_destroy(core);

    /* The return code alone does NOT pin the interesting half: 0xDEADBEEF has
     * bit 0 set and RF_SRC_SEL/RF_CAP_ARM are both bit 0, so the pre-existing
     * "did it clear?" check reports HW_IO even when the guard is absent and a
     * value derived from the sentinel HAS been written.  Measured: building the
     * guardless baseline against this file gave 95 PASS / 0 FAIL until the
     * register content was asserted.  So assert the REGISTER is untouched. */
    core = healthy_core();
    {
        uint32_t src_before;
        check("the fixture brings TX up with the source selected", core != NULL &&
              rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
              rf_core_start_tx(core) == RF_ERR_OK &&
              (fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) & RF_SRC_SEL) != 0u);
        src_before = fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG));
        fake_pl_set_read_failure(RF_REG_SRC_CFG, 1);
        check("an unreadable register is reported as a failure",
              rf_core_release_part_keep_state(core, &hw) != RF_ERR_OK);
        fake_pl_set_read_failure(RF_REG_SRC_CFG, 0);
        check("no value derived from the unreadable register was written",
              fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG)) == src_before);
        check("a failed release leaves the core state untouched",
              rf_core_state(core) == RF_STATE_TX_ACTIVE);
    }
    rf_core_destroy(core);

    core = NULL;
    check("release of a never-opened core reports stopped without a write",
          rf_core_create(&open_args, &core) == RF_ERR_OK &&
          rf_core_release_part_keep_state(core, &hw) == RF_ERR_OK &&
          !hw.tx_active && !hw.rx_busy && !hw.rx_done);
    rf_core_destroy(core);

    core = healthy_core();
    check("one-shot RX waits for done before reading", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
          rf_core_start_rx(core) == RF_ERR_OK &&
          rf_core_poll_rx(core, &capture) == RF_ERR_OK && capture.done &&
          rf_core_read_rx(core, rx, 4) == RF_ERR_OK);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_capture_done_stuck(1);
    check("missing capture done becomes timeout/FAULT", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
          rf_core_start_rx(core) == RF_ERR_OK &&
          rf_core_read_rx(core, rx, 4) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    check("continuous capture cannot be read as a completed frame", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, true) == RF_ERR_OK &&
          rf_core_start_rx(core) == RF_ERR_OK &&
          rf_core_read_rx(core, rx, 4) == RF_ERR_INVALID_STATE);
    rf_core_destroy(core);

    /* The commit bit is an EDGE, not a level.  rf_tx_source.v:144 and
     * rf_sample_capture.v:77 latch their bus values on a synchronised TRANSITION
     * (`commit_s[1] ^ commit_s[0]`), so a write that leaves the bit where it
     * already was re-configures nothing -- which is exactly what a level-written
     * commit bit does after the first configure.  Baseline toggled the bit
     * (`v ^= RF_SMP_COMMIT`).  These two cases pin the toggle by observing the
     * LATCHED length and ratio: the value the DATA_CLK-side reader is actually
     * looping over, not the register contents.  On the level-written code the
     * first commit latches and the second produces no edge, so the second
     * length never lands and both cases fail. */
    core = healthy_core();
    check("a second TX commit re-latches the reader length", core != NULL &&
          rf_core_prepare_tx(core, 64, true) == RF_ERR_OK &&
          fake_pl_get_smp_len_lat() == 64u &&
          rf_core_configure_tx_path(core, 4096, true, NULL) == RF_ERR_OK &&
          fake_pl_get_smp_len_lat() == 4096u);
    rf_core_destroy(core);

    core = healthy_core();
    check("a second RX commit re-latches the capture length and ratio",
          core != NULL &&
          rf_core_prepare_rx(core, 1u, 64, false) == RF_ERR_OK &&
          fake_pl_get_cap_len_lat() == 64u &&
          fake_pl_get_cap_ratio_lat() == 1u &&
          rf_core_set_rx_config(core, 2u, 512, false, NULL) == RF_ERR_OK &&
          fake_pl_get_cap_len_lat() == 512u &&
          fake_pl_get_cap_ratio_lat() == 2u);
    rf_core_destroy(core);

    /* A second capture must RE-ARM, not inherit the first one's arm.  The board
     * measured 2026-09-30 that a completed capture leaves RF_CAP_ARM set, and a
     * prepare_rx that PRESERVED it made the next capture read the previous BRAM
     * contents instead of running ("same value x4 + 0 x60" from a 64-sample ask).
     * prepare_rx must therefore force the arm bit low; start_rx raises it, so the
     * arm is always a fresh 0->1 transition. */
    core = healthy_core();
    {
        uint32_t cfg_rb = 0, st_rb = 0;
        check("prepare_rx clears ARM so a second capture can re-arm",
              core != NULL &&
              rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
              rf_core_get_rx_path(core, &cfg_rb, &st_rb) == RF_ERR_OK &&
              (cfg_rb & RF_CAP_ARM) == 0u &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              rf_core_get_rx_path(core, &cfg_rb, &st_rb) == RF_ERR_OK &&
              (cfg_rb & RF_CAP_ARM) != 0u &&
              rf_core_read_rx(core, rx, 4) == RF_ERR_OK &&
              rf_core_prepare_rx(core, 1u, 8, false) == RF_ERR_OK &&
              rf_core_get_rx_path(core, &cfg_rb, &st_rb) == RF_ERR_OK &&
              (cfg_rb & RF_CAP_ARM) == 0u &&
              fake_pl_get_cap_len_lat() == 8u);
    }
    rf_core_destroy(core);

    /* The finite-capture TEARDOWN.  rf_core_read_rx deliberately leaves
     * RF_CAP_ARM high -- a completed capture that HAS finished is a full buffer
     * waiting to be drained -- and that is right for the CLI but wrong for a
     * host that has to tell "armed and waiting" from "done and drained".  The
     * finite counterpart FREEZES the samples first and only then releases the
     * capture: clearing the arm is what ends the capture, so the data has to be
     * out of the BRAM before that can be said.  The register is verified and not
     * assumed, so a clear that was written and did not take is a FAULT and the
     * frozen frame is not handed back as a success. */
    core = healthy_core();
    {
        uint32_t cfg_rb = 0, st_rb = 0;
        uint32_t seed[4] = { 0x00000111u, 0x00001222u, 0x00002333u, 0x00003444u };
        uint32_t frozen[4] = { 0u, 0u, 0u, 0u };
        int k;

        for (k = 0; k < 4; ++k)
            fake_pl_write(RF_BRAM_RX_BASE + (uint32_t)k * 4u, seed[k]);

        check("finish_rx freezes the samples and then lowers CAP_ARM",
              core != NULL &&
              rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              rf_core_finish_rx(core, frozen, 4) == RF_ERR_OK &&
              memcmp(frozen, seed, sizeof seed) == 0 &&
              rf_core_get_rx_path(core, &cfg_rb, &st_rb) == RF_ERR_OK &&
              (cfg_rb & RF_CAP_ARM) == 0u &&
              rf_core_state(core) == RF_STATE_READY);
        /* The release is real, not a readback artefact: the NEXT capture arms,
         * completes and is released on its own account. */
        check("and the part can arm, capture and finish again afterwards",
              rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              rf_core_finish_rx(core, frozen, 4) == RF_ERR_OK &&
              rf_core_get_rx_path(core, &cfg_rb, &st_rb) == RF_ERR_OK &&
              (cfg_rb & RF_CAP_ARM) == 0u);
    }
    rf_core_destroy(core);

    /* Negative control: the arm bit does not drop.  The write LANDS and the
     * READBACK is what disagrees, so nothing short of a verified clear can
     * catch this. */
    core = healthy_core();
    {
        uint32_t frozen[4] = { 0u, 0u, 0u, 0u };

        fake_pl_set_capture_arm_stuck(1);
        check("a capture whose release cannot be confirmed is a FAULT, not a "
              "frozen success",
              core != NULL &&
              rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              rf_core_finish_rx(core, frozen, 4) == RF_ERR_HARDWARE_IO &&
              rf_core_state(core) == RF_STATE_FAULT &&
              (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) != 0u);
    }
    rf_core_destroy(core);

    /* A CONTINUOUS capture keeps its arm: the finite teardown is for the finite
     * case and must not become a way to "finish" one that is still running. */
    core = healthy_core();
    {
        uint32_t frozen[4] = { 0u, 0u, 0u, 0u };

        check("finish_rx refuses a continuous capture instead of releasing it",
              core != NULL &&
              rf_core_prepare_rx(core, 1u, 4, true) == RF_ERR_OK &&
              rf_core_start_rx(core) == RF_ERR_OK &&
              rf_core_finish_rx(core, frozen, 4) == RF_ERR_INVALID_STATE &&
              (rf_reg_read(RF_REG_CAP_CFG) & RF_CAP_ARM) != 0u);
    }
    rf_core_destroy(core);

    core = healthy_core();
    fake_reg[0x3F5u] = 0xA0u;
    check("digital loopback changes only bit zero", core != NULL &&
          rf_core_set_digital_loopback(core, true, &loopback) == RF_ERR_OK &&
          loopback == 0xA1u && fake_reg[0x3F5u] == 0xA1u &&
          rf_core_set_digital_loopback(core, false, &loopback) == RF_ERR_OK &&
          loopback == 0xA0u);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_loopback_stuck(1);
    check("digital loopback readback failure becomes FAULT", core != NULL &&
          rf_core_set_digital_loopback(core, true, NULL) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_read_failure(RF_REG_WAVE_SEL, 1);
    check("WAVE_SEL sentinel readback becomes FAULT", core != NULL &&
          rf_core_set_wave_sel(core, 1u, NULL) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_read_failure(RF_REG_IDELAY_EN, 1);
    check("key PL readback failure becomes FAULT and closes commands", core != NULL &&
          rf_core_configure(core, &config, NULL) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT &&
          rf_core_set_phase_inc(core, 1u, NULL) == RF_ERR_INVALID_STATE &&
          rf_core_spi_read(core, 0x037u, NULL) == RF_ERR_INVALID_STATE);
    rf_core_destroy(core);

    /* A failed MMIO read returns 0xDEADBEEF, whose bit 0 is set -- and RF_SRC_SEL
     * and RF_CAP_ARM are both bit 0 while 0xDEADBEEF & 0x1FFF is a plausible
     * length/position.  A bit-only or masked test would therefore read a failed
     * access as a successful one, so every site below must test the RAW word
     * first.  Each case injects the fault at the aperture boundary. */
    core = healthy_core();
    check("SRC_CFG sentinel during TX prepare becomes FAULT", core != NULL &&
          (fake_pl_set_read_failure(RF_REG_SRC_CFG, 1), 1) &&
          rf_core_prepare_tx(core, 4, true) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_read_failure(RF_REG_SMP_LEN, 1);
    check("SMP_LEN sentinel during TX prepare becomes FAULT", core != NULL &&
          rf_core_prepare_tx(core, 4, true) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    check("SRC_CFG sentinel during TX start becomes FAULT", core != NULL &&
          rf_core_prepare_tx(core, 4, true) == RF_ERR_OK &&
          (fake_pl_set_read_failure(RF_REG_SRC_CFG, 1), 1) &&
          rf_core_start_tx(core) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    check("CAP_CFG sentinel during RX start becomes FAULT", core != NULL &&
          rf_core_prepare_rx(core, 1u, 4, false) == RF_ERR_OK &&
          (fake_pl_set_read_failure(RF_REG_CAP_CFG, 1), 1) &&
          rf_core_start_rx(core) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_read_failure(RF_REG_SMP_POS, 1);
    check("SMP_POS sentinel via get_tx_path becomes FAULT", core != NULL &&
          rf_core_get_tx_path(core, &tx_cfg, &tx_len, &tx_pos) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    core = healthy_core();
    fake_pl_set_read_failure(RF_REG_CAP_STATUS, 1);
    check("CAP_STATUS sentinel via get_rx_path becomes FAULT", core != NULL &&
          rf_core_get_rx_path(core, &tx_cfg, &tx_pos) != RF_ERR_OK &&
          rf_core_state(core) == RF_STATE_FAULT);
    rf_core_destroy(core);

    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    return fail_count ? 1 : 0;
}
