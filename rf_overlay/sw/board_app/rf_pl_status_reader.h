#ifndef RF_PL_STATUS_READER_H_
#define RF_PL_STATUS_READER_H_
#include <stdbool.h>
#include <stdint.h>

typedef struct rf_pl_status_reader rf_pl_status_reader_t;
typedef int (*rf_pl_read_fn)(void *ctx, uint32_t address, uint32_t *value);
typedef struct {
    uint64_t sampled_at_monotonic_ns;
    bool io_ok;
    bool tx_source_valid, tx_length_valid, tx_position_valid;
    bool rx_armed_valid, rx_done_valid, rx_position_valid;
    bool tx_source_selected, rx_armed, rx_done;
    uint32_t tx_length_words, tx_position_words, rx_position_words;
    /* Optional read-only diagnostics.  These are raw/config register reads, not
     * a live IDELAYE2 CNTVALUEOUT or an RX frame-valid signal.  Each field has
     * its own validity so a partial /dev/mem read is never reported as zero. */
    bool status_valid, phy_mode_valid, idelay_en_valid, idelay_tap_valid;
    bool src_cfg_valid, cap_cfg_valid, cap_ratio_valid, cap_len_valid;
    bool cap_status_valid;
    uint32_t status_word, phy_mode, idelay_en, idelay_tap;
    uint32_t src_cfg, cap_cfg, cap_ratio, cap_len, cap_status;
} rf_pl_status_t;

/* This module exposes only reads. Production uses O_RDONLY /dev/mem and
 * PROT_READ mappings; host tests inject a read callback. */
rf_pl_status_reader_t *rf_pl_status_reader_open(void);
rf_pl_status_reader_t *rf_pl_status_reader_from_callback(rf_pl_read_fn read,
                                                         void *ctx);
void rf_pl_status_reader_close(rf_pl_status_reader_t *reader);
void rf_pl_status_sample(rf_pl_status_reader_t *reader, uint64_t now_ns,
                         rf_pl_status_t *out);
#endif
