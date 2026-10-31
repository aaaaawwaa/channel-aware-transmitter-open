/* rf_core.h -- public, vendor-independent RF core contract. */
#ifndef RF_CORE_H_
#define RF_CORE_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct rf_core rf_core_t;

typedef enum {
    RF_STATE_OFFLINE,
    RF_STATE_INITIALIZING,
    RF_STATE_READY,
    RF_STATE_TX_ACTIVE,
    RF_STATE_RX_CAPTURING,
    RF_STATE_DIGITAL_LOOPBACK,
    RF_STATE_PHYSICAL_LOOPBACK,
    RF_STATE_STOPPING,
    RF_STATE_FAULT
} rf_state_t;

typedef enum {
    RF_ERR_OK = 0,
    RF_ERR_INVALID_ARGUMENT = -1,
    RF_ERR_INVALID_STATE = -2,
    RF_ERR_HARDWARE_IO = -3,
    RF_ERR_TIMEOUT = -4,
    RF_ERR_NO_MEMORY = -5
} rf_error_t;

typedef struct {
    bool attenuation_mdB_present;
    uint32_t attenuation_mdB;
    uint64_t lo_hz;
    uint32_t sample_rate_hz;
    uint8_t phy_mode_1r1t;
} rf_init_request_t;

typedef struct {
    uint64_t lo_hz;
    uint32_t sample_rate_hz;
    uint32_t rx_bandwidth_hz;
    uint32_t tx_bandwidth_hz;
    uint32_t attenuation_mdB;
    uint8_t phy_mode_1r1t;
    uint8_t idelay_en;
    uint8_t idelay_tap;
} rf_config_t;

typedef struct {
    /* lo_hz/sample_rate_hz remain the legacy TX-side aliases.  The explicit
     * fields below keep RX and TX evidence separate for readback validation. */
    uint64_t lo_hz;
    uint64_t rx_lo_hz;
    uint64_t tx_lo_hz;
    uint32_t sample_rate_hz;
    uint32_t rx_sample_rate_hz;
    uint32_t tx_sample_rate_hz;
    uint32_t rx_bandwidth_hz;
    uint32_t tx_bandwidth_hz;
    uint32_t attenuation_mdB;
    uint8_t tx_fir_enabled;
    uint8_t rx_fir_enabled;
    bool tx_fir_config_verified;
    bool rx_fir_config_verified;
    bool hardware_verified;
    uint8_t phy_mode_1r1t;
    uint8_t product_id;
} rf_readback_t;

/* Raw RX1 observation, valid only when the dedicated API returns RF_ERR_OK.
 * Sequential reads are not an atomic AGC sample; index_stable says only that
 * the two index reads agreed. No cached mode, gain table or gain-dB mapping. */
typedef struct {
    uint8_t rx_enable_raw;
    uint8_t agc_config1_raw;
    uint8_t agc_config2_raw;
    uint8_t mode_rx1;
    uint8_t gain_index_before;
    uint8_t gain_index_after;
    uint8_t digital_gain_code;
    bool index_stable;
} rf_gain_observation_t;

/* The DATA_CLK acceptance ceiling, and the noise level it was derived from.
 *
 * The criterion asks "is there no DATA_CLK yet?" and the answer has to come
 * from the f_DATA_CLK MAGNITUDE.  RF_NOCLOCK_MAX_HZ is the acceptance ceiling:
 * at or above it the channel is seeing something it cannot explain and the
 * observation must not be trusted.
 *
 * RF_NOISE_OBSERVED_MAX_HZ is not a second threshold -- it is the highest value
 * actually MEASURED on this board with the BBPLL unprogrammed, across 25
 * windows: 0.023 to 0.266 MHz, varying window to window, which is what noise
 * looks like and what a locked clock does not.  It is an observed maximum, not
 * a bound -- a later reading may exceed it, and that is exactly why the
 * acceptance ceiling is set well above it rather than at it.  A reading between
 * the two is reported as a grey zone instead of quietly accepted, because that
 * is where a real problem would hide. */
#define RF_NOCLOCK_MAX_HZ          1000000ull
#define RF_NOISE_OBSERVED_MAX_HZ    266000ull

/* Fixed AD9361 digital-interface registers, read as one coherent snapshot.
 *
 * Only these five registers are exposed.  They are the complete input to the
 * restricted RX_DATA_DELAY experiment:
 *   0x006 RX clock/data delay (DATA_CLK_DELAY[7:4], RX_DATA_DELAY[3:0])
 *   0x007 TX clock/data delay (FB_CLK_DELAY[7:4], TX_DATA_DELAY[3:0])
 *   0x010..0x012 parallel-port configuration 1..3
 *
 * `valid` is true only when all five reads succeeded.  A partial snapshot is
 * reported as invalid and is never used for a write. */
typedef struct {
    bool valid;
    uint8_t reg_006;
    uint8_t reg_007;
    uint8_t reg_010;
    uint8_t reg_011;
    uint8_t reg_012;
    uint8_t rx_data_delay;
    uint8_t data_clk_delay;
    uint8_t tx_data_delay;
    uint8_t fb_clk_delay;
    /* decoded 0x012 fixed fields, verbatim vendor header bits */
    bool lvds_mode;
    bool single_data_rate;
    bool half_duplex_mode;
    bool single_port_mode;
    bool full_port;
    bool swap_ports;
    bool fdd_alt_word_order;
    bool fdd_rx_rate_2tx_rate;
    /* 0x010/0x011 raw configuration bits needed to prove the rest is kept */
    uint8_t pp1;
    uint8_t pp2;
} rf_interface_status_t;

typedef struct {
    uint32_t status;
    uint32_t phy_mode;
    uint32_t idelay_en;
    uint32_t idelay_tap;
    uint32_t phase_inc;
    uint32_t wave_sel;
    uint32_t src_cfg;
    uint32_t smp_len;
    uint32_t smp_pos;
    uint32_t cap_cfg;
    uint32_t cap_ratio;
    uint32_t cap_len;
    uint32_t cap_status;
    /* STATUS decoded once, so a caller that only formats the word does not need
     * the register layout -- and cannot disagree with the core about it.
     * `status_win_log2` is read from the word rather than assumed: the RTL fixes
     * it at 15 today, but the window length is the measurement's own definition
     * and a report has to state the one that was actually in force. */
    uint16_t status_win_cycles;
    uint8_t status_win_log2;
    uint8_t status_win_seq;
    uint8_t status_clk_seen;
    uint8_t status_win_done;
    uint8_t status_win_active;
    uint8_t status_rst_source_ok;
    /* The n=0 alias -- `count * 50 MHz >> log2` -- which is the quantity the
     * pre-init negative criterion judges.  Deliberately NOT `data_clk_hz`: that
     * one is resolved against an expected interface rate, and on a quiet line it
     * lands on a 100 MHz alias, so judging the ceiling with it would fail a
     * healthy board. */
    uint64_t data_clk_base_hz;
    uint64_t data_clk_hz;
    uint64_t data_clk_alias_step_hz;
    int data_clk_alias_index;
    bool status_fresh;
} rf_pl_snapshot_t;

typedef struct {
    bool tx_active;
    bool rx_busy;
    bool rx_done;
} rf_hw_status_t;

typedef struct {
    bool done;
    bool busy;
    uint16_t position;
} rf_capture_status_t;

typedef struct {
    bool two_rx_two_tx;
    bool trace_enabled;
    const char *trace_path;
} rf_core_open_args_t;

int rf_core_create(const rf_core_open_args_t *args, rf_core_t **out_core);
void rf_core_destroy(rf_core_t *core);
int rf_core_open(rf_core_t *core, const rf_core_open_args_t *args);
int rf_core_init(rf_core_t *core, const rf_init_request_t *request,
                 rf_readback_t *readback);
int rf_core_configure(rf_core_t *core, const rf_config_t *config,
                      rf_readback_t *readback);
int rf_core_write_tx(rf_core_t *core, const uint32_t *words, size_t count);
int rf_core_prepare_tx(rf_core_t *core, size_t count, bool wrap);
int rf_core_start_tx(rf_core_t *core);
int rf_core_stop_tx(rf_core_t *core, rf_hw_status_t *status);
int rf_core_prepare_rx(rf_core_t *core, uint8_t ratio, size_t count,
                       bool continuous);
int rf_core_start_rx(rf_core_t *core);
int rf_core_poll_rx(rf_core_t *core, rf_capture_status_t *status);
int rf_core_read_rx(rf_core_t *core, uint32_t *words, size_t count);
/* Complete a FINITE capture in one step: FREEZE the drained samples, then lower
 * RF_CAP_ARM and verify the real register.
 *
 * The finite counterpart of rf_core_read_rx, which deliberately leaves the arm
 * high -- a completed capture that HAS finished is a full buffer waiting to be
 * drained, which is the honest reading for a CLI but leaves a host unable to tell
 * "armed and waiting" from "done and drained".  Here the arm goes down, so the two
 * are different words on the wire.
 *
 * The order is the contract: the samples are copied out of the BRAM while the
 * completed capture is still held, and only then is the capture released.  A clear
 * that does not take is a hardware fault, not a warning, so the frozen frame is
 * never returned as a success.  Continuous captures are refused (INVALID_STATE):
 * their arm must stay up. */
int rf_core_finish_rx(rf_core_t *core, uint32_t *words, size_t count);
int rf_core_set_digital_loopback(rf_core_t *core, bool enable,
                                 uint8_t *readback);
/* Set or clear the AD9361 data-port loop bit (SPI 0x3F5 bit0) with a readback,
 * and deliberately WITHOUT touching core->state or requiring a state.
 *
 * The on-chip loop is a DATA-PATH fact, not a session state.  The
 * `loopback.digital.capture` transaction keeps the core READY while the tap is
 * open, so the ordinary READY-gated one-shot capture (rf_core_prepare_rx /
 * rf_core_start_rx / rf_core_read_rx) runs against it unchanged; and having no
 * state gate is what lets a capture that FAULTED still clear the tap. */
int rf_core_set_data_port_loop(rf_core_t *core, bool enable,
                               uint8_t *readback);
int rf_core_safe_shutdown(rf_core_t *core, rf_hw_status_t *status);

/* Release the TX source and the RX arm WITHOUT touching core->state.
 *
 * rf_core_safe_shutdown() does the same register work but ends in
 * RF_STATE_OFFLINE, so calling it on a faulted core would erase the FAULT -- the
 * diagnostic record that something went wrong.  This is the state-free half: the
 * same "bring the part to rest" register action, with the state left exactly as
 * the caller found it.  Like rf_core_set_data_port_loop it deliberately has NO
 * state gate, which is what lets it reach a part that has FAULTED and therefore
 * refuses every READY-gated setter.
 *
 * It does NOT disable the part (no gpio_init/RESETB/ENABLE): that belongs to a
 * full shutdown, where nothing is expected to command it again.  This is for the
 * case where the part must stop sourcing but the daemon still holds it.
 *
 * Returns RF_ERR_OK once BOTH registers read back clear, and
 * RF_ERR_HARDWARE_IO when the part did not release -- so a caller that cannot
 * confirm the stop reports an unconfirmed stop instead of a success. */
int rf_core_release_part_keep_state(rf_core_t *core, rf_hw_status_t *status);
void rf_core_close(rf_core_t *core);
rf_state_t rf_core_state(const rf_core_t *core);
bool rf_core_initialization_attempted(const rf_core_t *core);

/* Public command-level operations.  The CLI is deliberately limited to parsing
 * and presentation; all MMIO, BRAM and SPI access goes through these methods. */
int rf_core_selftest(void);
int rf_core_set_phy_mode_safe(rf_core_t *core, uint8_t phy_mode_1r1t,
                              uint32_t *readback);
/* Force the TX attenuation and read it back, without re-running the
 * configuration.  The safety path needs this: rf_core_configure requires READY
 * and rewrites the whole signal chain, which is neither available nor wanted
 * when a live TX must be attenuated.  Usable from any live state. */
int rf_core_set_tx_attenuation_safe(rf_core_t *core, uint32_t attenuation_mdB,
                                    uint32_t *readback);
/* Tap-only IDELAY lane update.  This intentionally does NOT rerun AD9361
 * configuration, PHY mode, reset, TX load or capture.  mask is a non-zero 7-bit
 * lane mask (bit6 = frame), tap is 0..31.  The sequence is: close all LD, write
 * the common tap, then enable only the requested lanes.  IDELAYE2 CNTVALUEOUT is
 * not readable through this API; the readback is the PL configuration register. */
/* Read-only fixed AD9361 digital interface registers.  No write, no init,
 * no refresh of any cached state.  A core that is not opened has no readable
 * values: this returns INVALID_STATE rather than initializing anything. */
int rf_core_read_interface_status(rf_core_t *core, rf_interface_status_t *out);
/* Restricted RX data delay write: only SPI 0x006[3:0].
 *
 * The other nibble (DATA_CLK_DELAY) and 0x007/0x010/0x011/0x012 are read before
 * and after the write and must be unchanged.  Requires core OPENED, state READY,
 * PL TX source off and RX not armed.  The original 0x006 must be read
 * successfully before any write.  `value` is 0..15; no clamp, wrap or scan.
 * On any failed readback the core is left FAULT and the caller gets HARDWARE_IO.
 * This does not call ad9361_init/configure, switch PHY mode, load TX or capture. */
int rf_core_set_rx_data_delay(rf_core_t *core, uint8_t value,
                              rf_interface_status_t *before,
                              rf_interface_status_t *after);
int rf_core_set_idelay_lanes(rf_core_t *core, uint8_t mask, uint8_t tap,
                             uint32_t *en_readback, uint32_t *tap_readback);
int rf_core_configure_pl(rf_core_t *core, uint8_t phy_mode_1r1t,
                         uint8_t idelay_en, uint8_t idelay_tap,
                         uint32_t phase_inc, rf_pl_snapshot_t *snapshot);
int rf_core_get_pl_snapshot(rf_core_t *core, rf_pl_snapshot_t *snapshot);
int rf_core_set_phase_inc(rf_core_t *core, uint32_t phase_inc,
                          uint32_t *readback);
int rf_core_set_wave_sel(rf_core_t *core, uint8_t wave_sel,
                         uint32_t *readback);
int rf_core_set_tx_source(rf_core_t *core, bool buffer,
                          uint32_t *readback);
int rf_core_configure_tx_path(rf_core_t *core, size_t count, bool wrap,
                              uint32_t *readback);
int rf_core_get_tx_path(rf_core_t *core, uint32_t *src_cfg,
                        uint32_t *length, uint32_t *position);
int rf_core_set_rx_config(rf_core_t *core, uint8_t ratio, size_t count,
                          bool continuous, uint32_t *readback);
int rf_core_set_rx_arm(rf_core_t *core, bool arm, uint32_t *readback);
int rf_core_get_rx_path(rf_core_t *core, uint32_t *config,
                        uint32_t *status);
int rf_core_write_tx_word(rf_core_t *core, size_t index, uint32_t word,
                          uint32_t *readback);
int rf_core_read_tx_word(rf_core_t *core, size_t index, uint32_t *word);
int rf_core_read_rx_word(rf_core_t *core, size_t index, uint32_t *word);
int rf_core_spi_read(rf_core_t *core, uint32_t address, uint8_t *value);
int rf_core_spi_write(rf_core_t *core, uint32_t address, uint8_t value,
                      uint8_t *readback);
int rf_core_get_readback(rf_core_t *core, rf_readback_t *readback);
/* Research readback uses fresh dual-channel attenuation getters. */
typedef struct { rf_readback_t rf; uint32_t tx1_attenuation_mdB,tx2_attenuation_mdB; rf_pl_snapshot_t pl; } rf_headroom_readback_t;
int rf_core_get_headroom_readback(rf_core_t *core,rf_headroom_readback_t *out);
#ifdef PLATFORM_HOST
int rf_core_host_set_headroom_tx2_fault(rf_core_t *core,int on);
#endif
/* Explicit observation only: READY, initialized, SRC_SEL=0, CAP_ARM=0.
 * Failure clears the output and returns an error without faulting the core or
 * changing chip/path registers. It never calls vendor gain getters. */
int rf_core_get_gain_observation(rf_core_t *core, rf_gain_observation_t *observation);

/* ONE checked read of the RX1 full-table gain index, for use INSIDE a capture
 * window -- the state rf_core_get_gain_observation above deliberately refuses.
 *
 * Deliberately smaller than the boundary observation: one SPI read, one register,
 * no before/after pair, no mode bracket and NO stability claim -- '*out' is what
 * the part answered at that instant.  No write, no state change, and a failed read
 * does NOT fault the core: this observes a transaction it is inside, so it must not
 * be able to end it.  An index above 76 is not reported as a number (the byte did
 * not decode as a full-table index, and a reader could not tell that from a real
 * reading); it is HARDWARE_IO, like the boundary observation's range check.
 * '*out' is written only on success, so a stale value cannot be mistaken for a
 * measurement.
 *
 * WHERE the sample was taken is the CALLER's declaration, not this function's:
 * nothing here can verify that it was called between start_rx and finish_rx. */
int rf_core_read_rx1_gain_index(rf_core_t *core, uint8_t *out);
#ifdef PLATFORM_HOST
int rf_core_host_set_vendor_readback_fault(rf_core_t *core, int on);
#endif

/* The executable entry point is implemented in rf_main.c. */
int rf_core_cli_dispatch(int argc, char **argv);

#endif /* RF_CORE_H_ */
