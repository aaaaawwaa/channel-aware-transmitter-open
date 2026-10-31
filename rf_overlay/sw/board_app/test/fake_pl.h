#ifndef FAKE_PL_H_
#define FAKE_PL_H_

#include <stdint.h>

/* Install the model: subsequent platform_rf.c MMIO goes here, not to /dev/mem. */
void     fake_pl_reset(void);
void fake_pl_set_capture_step(uint32_t n);
uint32_t fake_pl_read(uint32_t off);
void     fake_pl_write(uint32_t off, uint32_t val);

/* Device-side observability */
extern int      fake_spi_rejected;   /* writes the AD9361 dropped (bad CNT) */
extern int      fake_mmio_writes; /* every fake PL write, including BRAM */
/* Only the writes that land in the axi_ctrl_0 window -- the registers that ARE the
 * part's configuration for this design (IDELAY, PHY mode, sample path, capture).
 * Deliberately narrower than fake_mmio_writes, because a READBACK is a read that this
 * PL's SPI master performs by programming its own control register in a DIFFERENT
 * window, so "did this request program the part?" cannot be asked of a count that
 * includes the SPI master's own bookkeeping.  See the idempotence criterion in
 * test_rf_daemon.c. */
extern int      fake_ctrl_writes;
/* Writes that SELECT the TX source (a write to RF_REG_SRC_CFG with RF_SRC_SEL set),
 * however they land.  A research OFF attempt is an ordinary RX capture with the
 * source OFF, so it must perform NO such write at all -- and only the SELECTING
 * writes are counted, because the teardown's clearing write is a different act and
 * a level read after the capture cannot tell the two apart. */
extern int      fake_src_sel_writes;
/* Ordered axi_ctrl_0 write log for sequence tests (test-only witness). */
extern uint32_t fake_ctrl_log_addr[64];
extern uint32_t fake_ctrl_log_value[64];
extern int      fake_ctrl_log_count;
extern int      fake_spi_writes;     /* writes the AD9361 accepted */
extern uint8_t  fake_reg[0x400];     /* the modelled AD9361 register file */
extern int      fake_last_burst_len; /* bytes clocked in the last burst */
extern int      fake_last_ntx, fake_last_nrx;

/* The DATA_CLK-side values the reader/capture are actually looping over.  These
 * update only on a commit TRANSITION, as rf_tx_source.v / rf_sample_capture.v
 * do -- so they, not the written registers, are what a level-written commit bit
 * fails to change. */
uint32_t fake_pl_get_smp_len_lat(void);
uint32_t fake_pl_get_cap_len_lat(void);
uint32_t fake_pl_get_cap_ratio_lat(void);

/* Model controls */
void fake_pl_set_product_id(uint8_t v);
void fake_pl_set_status(uint32_t v);   /* the read-only PL STATUS word */
void fake_pl_set_status_window_stuck(int on);
void fake_pl_set_spi_init_failure(int on);
void fake_pl_set_reject_all_writes(int on);

/* Fault injection used by shared-core tests.  Each switch models the failure
 * at the aperture boundary rather than changing the core's expected values. */
void fake_pl_set_read_failure(uint32_t off, int on);
void fake_pl_set_tx_position_stuck(int on);
void fake_pl_set_capture_done_stuck(int on);
void fake_pl_set_stop_active(int on);
void fake_pl_set_loopback_stuck(int on);
/* A capture latch that will not release: once RF_CAP_ARM has been raised, the
 * 1->0 write still lands but the bit stays high, so the READBACK is what
 * disagrees.  This is the failure the finite-capture teardown's confirmation
 * exists to catch -- a clear that "was written" but did not take. */
void fake_pl_set_capture_arm_stuck(int on);
void fake_pl_set_spi_read_failure(uint16_t addr, int on);
/* Change one register after its next successful read (one-shot AGC model). */
void fake_pl_set_spi_read_change(uint16_t addr, uint8_t after);

/* Read-side WITNESS for one address: how many read bursts the part was asked
 * for it, and the value RF_CAP_CFG held at the LAST of those reads.
 *
 * The value a sampled register returns says WHAT was read; it cannot say WHERE
 * in a transaction the read happened, because the model's register file does not
 * change on its own.  The physical capture window IS the interval in which
 * RF_CAP_ARM is raised (rf_core_start_rx raises it; rf_core_finish_rx clears it
 * and verifies the clear), so the CAP_CFG value at the moment of the read is
 * exactly the witness that separates "sampled inside the window" from "sampled
 * before start_rx or after finish_rx" -- the two placements whose values are
 * otherwise identical. */
int      fake_pl_spi_read_count(uint16_t addr);
uint32_t fake_pl_spi_read_cap_cfg(uint16_t addr);


#endif
