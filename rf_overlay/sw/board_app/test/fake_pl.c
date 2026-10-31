/* fake_pl.c -- a C model of the PL registers AND of the AD9361's SPI decode.
 *
 * The PL half mirrors rf_overlay/rtl/pl_spi_master.v: one CSN-low burst of
 * 2 + N_TX + N_RX bytes, byte0 = INSTR[15:8], DONE sticky until the next START.
 *
 * The device half is the part that matters.  The PL master does NOT interpret
 * the CNT field -- it just clocks bytes -- so a send-side-only model cannot
 * catch an encoding error.  This models the AD9361's rule: a write must carry
 * exactly CNT+1 data bytes or the part drops the whole command, and a
 * multi-byte write takes the LAST data byte.
 */
#include "fake_pl.h"
#include "platform_rf.h"
#include <string.h>
#include <stdio.h>

int     fake_spi_rejected;
int     fake_spi_writes;
int     fake_mmio_writes;
int     fake_ctrl_writes;
int     fake_src_sel_writes;
uint32_t fake_ctrl_log_addr[64];
uint32_t fake_ctrl_log_value[64];
int      fake_ctrl_log_count;
int     fake_last_burst_len;
int     fake_last_ntx, fake_last_nrx;
uint8_t fake_reg[0x400];

static uint32_t spi_ctrl, spi_instr, spi_txd0, spi_txd1, spi_rxd0, spi_rxd1;
static uint32_t spi_status, spi_div;
static uint32_t gpio_data, gpio_tri;
static uint32_t ctrl_idelay_en, ctrl_idelay_tap, ctrl_phase_inc, ctrl_phy_mode, ctrl_wave_sel;
static uint32_t ctrl_src_cfg, ctrl_smp_len, ctrl_smp_pos;
static uint32_t ctrl_cap_cfg, ctrl_cap_ratio, ctrl_cap_len, ctrl_cap_status;
/* Values the DATA_CLK-side consumers actually see.  They are latched on a
 * TRANSITION of the commit bit, not on the write, because that is what
 * rf_tx_source.v:144 and rf_sample_capture.v:77 do.  Without this the fake
 * models commit as a level and cannot see a level-written commit bit, which
 * silently turns every commit after the first into a no-op. */
static uint32_t ctrl_smp_len_lat, ctrl_smp_wrap_lat, ctrl_smp_commit_prev;
static uint32_t ctrl_cap_ratio_lat, ctrl_cap_len_lat, ctrl_cap_cont_lat;
static uint32_t ctrl_cap_commit_prev;
static uint32_t capture_step=1;
void fake_pl_set_capture_step(uint32_t n){capture_step=n?n:1;}
static uint32_t status_word = 0;    /* the read-only PL STATUS register */
static unsigned status_reads;
static int      status_window_stuck;
static int      spi_init_failure;
static uint8_t  product_id = 0x0Au;
static int      reject_all_writes;
static int      read_fail[0x40];
static int      tx_position_stuck;
static int      capture_done_stuck;
static int      stop_active;
static int      loopback_stuck;
static int      capture_arm_stuck;
static int      spi_read_fail[0x400];
/* See fake_pl.h: per-address read witness.  spi_read_cap_cfg[addr] is the value
 * RF_CAP_CFG held when addr was last read, which is what locates the read inside
 * (or outside) a capture window. */
static int      spi_read_count[0x400];
static uint32_t spi_read_cap_cfg[0x400];
static int      spi_read_change_pending;
static uint16_t spi_read_change_addr;
static uint8_t  spi_read_change_after;
static uint32_t fake_tx_bram[RF_BRAM_WORDS];
static uint32_t fake_rx_bram[RF_BRAM_WORDS];

void fake_pl_set_product_id(uint8_t v) { product_id = v; }
uint32_t fake_pl_get_smp_len_lat(void)   { return ctrl_smp_len_lat; }
uint32_t fake_pl_get_cap_len_lat(void)   { return ctrl_cap_len_lat; }
uint32_t fake_pl_get_cap_ratio_lat(void) { return ctrl_cap_ratio_lat; }
void fake_pl_set_status(uint32_t v)
{
	status_word = v;
	status_reads = 0;
}
void fake_pl_set_status_window_stuck(int on) { status_window_stuck = on != 0; }
void fake_pl_set_spi_init_failure(int on) { spi_init_failure = on != 0; }
void fake_pl_set_reject_all_writes(int on) { reject_all_writes = on; }
void fake_pl_set_read_failure(uint32_t off, int on)
{
	if (off < sizeof(read_fail) / sizeof(read_fail[0]))
		read_fail[off] = on != 0;
}
void fake_pl_set_tx_position_stuck(int on) { tx_position_stuck = on != 0; }
void fake_pl_set_capture_done_stuck(int on) { capture_done_stuck = on != 0; }
void fake_pl_set_stop_active(int on) { stop_active = on != 0; }
void fake_pl_set_loopback_stuck(int on) { loopback_stuck = on != 0; }
void fake_pl_set_capture_arm_stuck(int on) { capture_arm_stuck = on != 0; }
void fake_pl_set_spi_read_failure(uint16_t addr, int on)
{
	if (addr < 0x400u)
		spi_read_fail[addr] = on != 0;
}

int fake_pl_spi_read_count(uint16_t addr)
{
	return addr < 0x400u ? spi_read_count[addr] : 0;
}

uint32_t fake_pl_spi_read_cap_cfg(uint16_t addr)
{
	return addr < 0x400u ? spi_read_cap_cfg[addr] : 0u;
}

void fake_pl_set_spi_read_change(uint16_t addr, uint8_t after)
{
    spi_read_change_pending = addr < 0x400u;
    spi_read_change_addr = addr;
    spi_read_change_after = after;
}

void fake_pl_reset(void)
{
	spi_ctrl = spi_instr = spi_txd0 = spi_txd1 = 0;
	spi_rxd0 = spi_rxd1 = spi_status = 0;
	spi_div = 25;                       /* pl_spi_master's reset value */
	gpio_data = gpio_tri = 0;
	ctrl_idelay_en = ctrl_idelay_tap = ctrl_phase_inc = ctrl_phy_mode = ctrl_wave_sel = 0;
	status_word = 0;                    /* no DATA_CLK: the dead-clock state */
	status_reads = 0;
	status_window_stuck = 0;
	spi_init_failure = 0;
	fake_spi_rejected = fake_spi_writes = fake_mmio_writes = 0;
	fake_ctrl_writes = 0;
	fake_src_sel_writes = 0;
	fake_last_burst_len = 0;
	fake_last_ntx = fake_last_nrx = 0;
	reject_all_writes = 0;
	memset(read_fail, 0, sizeof(read_fail));
	memset(spi_read_fail, 0, sizeof(spi_read_fail));
	memset(spi_read_count, 0, sizeof(spi_read_count));
	memset(spi_read_cap_cfg, 0, sizeof(spi_read_cap_cfg));
	spi_read_change_pending = 0;
	tx_position_stuck = 0;
	capture_done_stuck = 0;
	stop_active = 0;
	loopback_stuck = 0;
	capture_arm_stuck = 0;
	ctrl_src_cfg = ctrl_smp_len = ctrl_smp_pos = 0;
	ctrl_cap_cfg = ctrl_cap_ratio = ctrl_cap_len = ctrl_cap_status = 0;
	ctrl_smp_len_lat = ctrl_smp_wrap_lat = ctrl_smp_commit_prev = 0;
	ctrl_cap_ratio_lat = ctrl_cap_len_lat = ctrl_cap_cont_lat = 0;
	ctrl_cap_commit_prev = 0;capture_step=1;
	fake_ctrl_log_count = 0;
	memset(fake_tx_bram, 0, sizeof(fake_tx_bram));
	memset(fake_rx_bram, 0, sizeof(fake_rx_bram));
	memset(fake_reg, 0, sizeof(fake_reg));
	fake_reg[0x037] = product_id;       /* REG_PRODUCT_ID */
	fake_reg[0x3F5u] = 0xA0u;
}

/* One burst.  Mirrors the PL master's byte order exactly.
 *
 * 勘误（2026-09-17 执行时发现，**这是原计划的一个真 bug**）：
 * `n_tx` 是 **数据字节数，不含那 2 个指令字节**。依据是真实 RTL：
 *   - `pl_spi_master.v:305`  `n_tx <= s_axi_wdata[7:0]` —— CTRL[7:0] 存的是
 *     `spi_write_then_read` 传下来的 `n_data`（= `n_tx - 2`）；
 *   - `pl_spi_master.v:189`  `total_bytes = 5'd2 + lat_n_tx + lat_n_rx` ——
 *     **RTL 自己把那 2 个指令字节加上去**。
 * 所以 `n_tx` 已经是"数据字节"，与 `2 + n_tx + n_rx` 是两回事。
 *
 * 原计划在下面两处**重复加了那 2 个字节**，后果是**全盘误判**：
 *   (1) 拒绝判据写成 `n_tx < 2 + need` —— 单字节写（n_tx=1, need=1）会算出
 *       `1 < 3` 为真，于是**每一条合法写都被判为"被器件拒绝"**；
 *   (2) `fake_last_burst_len = n_tx + n_rx` 漏了那 2 个字节，读事务报 1 而不是 3。
 *
 * **最阴的地方**：反向用例（CNT=1 必须被拒）会**因为错误的原因通过** ——
 * 合法写和非法写一起被拒，测试看着是绿的却什么都没证明。这正是本任务要防的
 * "判据退化成恒真"。所以这个 bug 必须修，不能靠调测试迁就。
 */
static void fake_burst(uint32_t n_data_tx, uint32_t n_rx)
{
	uint8_t tx[24], rx[16];
	uint32_t i;
	uint32_t n_tx_total = 2u + n_data_tx;   /* what the wire actually carries */
	uint16_t cmd;
	uint32_t cnt, addr, is_write;

	fake_last_ntx = (int)n_data_tx;
	fake_last_nrx = (int)n_rx;
	/* The burst on the wire is instruction (2 bytes) + data + received bytes --
	 * this is what pl_spi_master.v:189 computes as total_bytes. */
	fake_last_burst_len = (int)(n_tx_total + n_rx);

	/* byte 0/1 are the instruction word; data bytes follow.  The PL shifts out
	 * txd0[7:0] first (tx_byte = txd_full[(byte_cnt-2)*8 +: 8]), i.e. LSB first,
	 * which is what the packing below reproduces. */
	tx[0] = (uint8_t)(spi_instr >> 8);
	tx[1] = (uint8_t)(spi_instr & 0xFF);
	tx[2] = (uint8_t)(spi_txd0 & 0xFF);
	tx[3] = (uint8_t)((spi_txd0 >> 8) & 0xFF);
	tx[4] = (uint8_t)((spi_txd0 >> 16) & 0xFF);
	tx[5] = (uint8_t)((spi_txd0 >> 24) & 0xFF);
	tx[6] = (uint8_t)(spi_txd1 & 0xFF);
	tx[7] = (uint8_t)((spi_txd1 >> 8) & 0xFF);
	for (i = 8; i < sizeof(tx); i++) tx[i] = 0;
	for (i = 0; i < sizeof(rx); i++) rx[i] = 0;

	cmd      = (uint16_t)((tx[0] << 8) | tx[1]);
	is_write = (cmd >> 15) & 0x1u;
	cnt      = (cmd >> 12) & 0x7u;      /* the field as transmitted */
	addr     = cmd & 0x3FFu;

	/* The DEVICE's rule: the field is byte-count-minus-one, so the command
	 * claims cnt+1 data bytes.  A write whose burst does not carry exactly
	 * that many is silently discarded -- which is precisely how the CNT=1
	 * encoding bug hid: reads encode 0 under either rule, so only writes
	 * died. */
	if (is_write) {
		uint32_t need = cnt + 1u;       /* data bytes the command claims */
		if (reject_all_writes || n_data_tx != need) {
			fake_spi_rejected++;
			/* device drops it; register file unchanged */
		} else if (addr < 0x400u) {
			uint8_t value = tx[2u + need - 1u];
				if (addr == 0x3F5u && loopback_stuck)
					value = (uint8_t)((fake_reg[addr] & 1u) |
					                  (value & (uint8_t)~1u));
				fake_reg[addr] = value;
			fake_spi_writes++;
		}
	} else {
		/* read: the data bytes come back from the register file, starting at
		 * the addressed register and auto-incrementing, as the part does. */
		if (addr < 0x400u) {
			spi_read_count[addr]++;
			spi_read_cap_cfg[addr] = ctrl_cap_cfg;
		}
		if (addr < 0x400u && spi_read_fail[addr]) {
			spi_status |= 0x4u;       /* transport-level read fault */
		} else {
			for (i = 0; i < n_rx && i < sizeof(rx); i++)
				rx[i] = (addr + i < 0x400u) ? fake_reg[addr + i] : 0;
			if (addr == 0x037u) rx[0] = product_id;
			if (spi_read_change_pending && addr == spi_read_change_addr) {
				fake_reg[addr] = spi_read_change_after;
				spi_read_change_pending = 0;
			}
		}
	}

	spi_rxd0 = (uint32_t)rx[0]
	         | ((uint32_t)rx[1] << 8)
	         | ((uint32_t)rx[2] << 16)
	         | ((uint32_t)rx[3] << 24);
	spi_rxd1 = (uint32_t)rx[4]
	         | ((uint32_t)rx[5] << 8)
	         | ((uint32_t)rx[6] << 16)
	         | ((uint32_t)rx[7] << 24);
	spi_status |= 0x2u;                 /* DONE */
}

uint32_t fake_pl_read(uint32_t off)
{
	if (off >= RF_AXI_CTRL_BASE && off < RF_AXI_CTRL_BASE + 0x40u &&
	    read_fail[off - RF_AXI_CTRL_BASE])
		return 0xDEADBEEFu;
	if (off >= RF_BRAM_TX_BASE && off < RF_BRAM_TX_BASE + RF_BRAM_BYTES &&
	    ((off - RF_BRAM_TX_BASE) % 4u) == 0u)
		return fake_tx_bram[(off - RF_BRAM_TX_BASE) / 4u];
	if (off >= RF_BRAM_RX_BASE && off < RF_BRAM_RX_BASE + RF_BRAM_BYTES &&
	    ((off - RF_BRAM_RX_BASE) % 4u) == 0u)
		return fake_rx_bram[(off - RF_BRAM_RX_BASE) / 4u];
	if (off == RF_CTRL_A(RF_REG_SMP_POS)) {
		if ((ctrl_src_cfg & RF_SRC_SEL) && !tx_position_stuck) {
			if (ctrl_smp_len_lat > 1u)
				ctrl_smp_pos = (ctrl_smp_pos + 1u) % ctrl_smp_len_lat;
		}
		return ctrl_smp_pos;
	}
	if (off == RF_CTRL_A(RF_REG_CAP_STATUS)) {
		if ((ctrl_cap_cfg & RF_CAP_ARM) && !ctrl_cap_cont_lat) {
			if (ctrl_cap_status < ctrl_cap_len_lat)
				ctrl_cap_status += capture_step;
			if (!capture_done_stuck && ctrl_cap_status >= ctrl_cap_len_lat)
				ctrl_cap_status = ctrl_cap_len_lat | (1u << 13);
		}
		return ctrl_cap_status;
	}
	switch (off) {
	case RF_SPI_A(RF_SPI_CTRL):   return spi_ctrl & ~0x10000u;
	case RF_SPI_A(RF_SPI_INSTR):  return spi_instr;
	case RF_SPI_A(RF_SPI_TXD0):   return spi_txd0;
	case RF_SPI_A(RF_SPI_TXD1):   return spi_txd1;
	case RF_SPI_A(RF_SPI_RXD0):   return spi_rxd0;
	case RF_SPI_A(RF_SPI_RXD1):   return spi_rxd1;
	case RF_SPI_A(RF_SPI_STATUS):
		return spi_init_failure ? 0xDEADBEEFu : spi_status;
	case RF_SPI_A(RF_SPI_DIV):    return spi_div;
	case RF_GPIO_A(RF_GPIO_DATA): return gpio_data;
	case RF_GPIO_A(RF_GPIO_TRI):  return gpio_tri;
	/* The config half of axi_ctrl_0 must be readable for the write side of
	 * the model to mean anything: the cases below store into the ctrl_*
	 * variables, and rf_main's `status`/init round-trips read them back. */
	case RF_CTRL_A(RF_REG_IDELAY_EN):  return ctrl_idelay_en;
	case RF_CTRL_A(RF_REG_IDELAY_TAP): return ctrl_idelay_tap;
	case RF_CTRL_A(RF_REG_PHASE_INC):  return ctrl_phase_inc;
	case RF_CTRL_A(RF_REG_PHY_MODE):   return ctrl_phy_mode;
	case RF_CTRL_A(RF_REG_SRC_CFG):    return ctrl_src_cfg;
	case RF_CTRL_A(RF_REG_SMP_LEN):    return ctrl_smp_len;
	case RF_CTRL_A(RF_REG_SMP_POS):    return ctrl_smp_pos;
	case RF_CTRL_A(RF_REG_CAP_CFG):    return ctrl_cap_cfg;
	case RF_CTRL_A(RF_REG_CAP_RATIO):  return ctrl_cap_ratio;
	case RF_CTRL_A(RF_REG_CAP_LEN):    return ctrl_cap_len;
	case RF_CTRL_A(RF_REG_CAP_STATUS): return ctrl_cap_status;
	case RF_CTRL_A(RF_REG_WAVE_SEL):   return ctrl_wave_sel;
	case RF_CTRL_A(RF_REG_STATUS):
		if (!status_window_stuck && ++status_reads >= 2u) {
			uint32_t seq = (status_word >> 4) & 0xFu;
			status_word = (status_word & ~0xF2u) |
			              (((seq + 1u) & 0xFu) << 4) | 0x2u;
			status_reads = 0;
		}
		return status_word;
	default:                      return 0xDEADBEEFu;  /* unmapped, as on the board */
	}
}

void fake_pl_write(uint32_t off, uint32_t val)
{
	fake_mmio_writes++;
	/* The axi_ctrl_0 window only: the part's configuration registers.  Writes to the
	 * PL SPI master's own registers are bookkeeping for a transfer (including a READ)
	 * and are deliberately not counted here -- see fake_pl.h. */
	if (off >= RF_AXI_CTRL_BASE && off < RF_AXI_CTRL_BASE + 4096u) {
		fake_ctrl_writes++;
		if (fake_ctrl_log_count < 64) {
			fake_ctrl_log_addr[fake_ctrl_log_count] = off;
			fake_ctrl_log_value[fake_ctrl_log_count] = val;
			fake_ctrl_log_count++;
		}
	}
	if (off == RF_CTRL_A(RF_REG_SRC_CFG) && (val & RF_SRC_SEL))
		fake_src_sel_writes++;
	if (off >= RF_BRAM_TX_BASE && off < RF_BRAM_TX_BASE + RF_BRAM_BYTES &&
	    ((off - RF_BRAM_TX_BASE) % 4u) == 0u) {
		fake_tx_bram[(off - RF_BRAM_TX_BASE) / 4u] = val;
		return;
	}
	if (off >= RF_BRAM_RX_BASE && off < RF_BRAM_RX_BASE + RF_BRAM_BYTES &&
	    ((off - RF_BRAM_RX_BASE) % 4u) == 0u) {
		fake_rx_bram[(off - RF_BRAM_RX_BASE) / 4u] = val;
		return;
	}
	switch (off) {
	case RF_SPI_A(RF_SPI_CTRL):
		spi_status &= ~0x6u;            /* DONE/error clear on next START */
		spi_ctrl = val;
		if (val & 0x10000u) {
			uint32_t n_tx = val & 0xFFu;
			uint32_t n_rx = (val >> 8) & 0xFFu;
			fake_burst(n_tx, n_rx);
		}
		break;
	case RF_SPI_A(RF_SPI_INSTR):  spi_instr = val; break;
	case RF_SPI_A(RF_SPI_TXD0):   spi_txd0 = val;  break;
	case RF_SPI_A(RF_SPI_TXD1):   spi_txd1 = val;  break;
	case RF_SPI_A(RF_SPI_DIV):    spi_div = val;   break;
	case RF_GPIO_A(RF_GPIO_DATA): gpio_data = val & 0x1FFu; break;
	case RF_GPIO_A(RF_GPIO_TRI):  gpio_tri = val;  break;
	case RF_CTRL_A(RF_REG_IDELAY_EN):  ctrl_idelay_en  = val; break;
	case RF_CTRL_A(RF_REG_IDELAY_TAP): ctrl_idelay_tap = val; break;
	case RF_CTRL_A(RF_REG_PHASE_INC):  ctrl_phase_inc  = val; break;
	case RF_CTRL_A(RF_REG_PHY_MODE):   ctrl_phy_mode   = val; break;
	case RF_CTRL_A(RF_REG_WAVE_SEL):   ctrl_wave_sel = val & 1u; break;
	case RF_CTRL_A(RF_REG_SMP_LEN):    ctrl_smp_len = val & 0x1FFFu; break;
	case RF_CTRL_A(RF_REG_SRC_CFG): {
		/* The register keeps every bit, commit included (slv_reg5 is stored
		 * unmasked and read back whole), but the reader latches smp_len/
		 * smp_wrap only on a commit TRANSITION.  A level write is therefore
		 * a no-op for the latched pair -- which is exactly what makes a
		 * level-written commit bit fail to re-configure the reader. */
		uint32_t commit = val & RF_SMP_COMMIT;
		uint32_t stored = val & (RF_SRC_SEL | RF_SMP_WRAP | RF_SMP_COMMIT);
		if (stop_active && !(val & RF_SRC_SEL))
			stored = (stored & ~RF_SRC_SEL) | RF_SRC_SEL;
		ctrl_src_cfg = stored;
		if (commit != ctrl_smp_commit_prev) {
			ctrl_smp_len_lat = ctrl_smp_len;
			ctrl_smp_wrap_lat = stored & RF_SMP_WRAP;
			ctrl_smp_pos = 0;
			ctrl_smp_commit_prev = commit;
		}
		break;
	}
	case RF_CTRL_A(RF_REG_CAP_RATIO): ctrl_cap_ratio = val & 7u; break;
	case RF_CTRL_A(RF_REG_CAP_LEN): ctrl_cap_len = val & 0x1FFFu; break;
	case RF_CTRL_A(RF_REG_CAP_CFG): {
		uint32_t commit = val & RF_CAP_COMMIT;
		uint32_t stored = val & (RF_CAP_ARM | RF_CAP_CONT | RF_CAP_COMMIT);
		/* A latch that will not release: the write LANDS (the register file
		 * and the commit edge behave normally) but a raised arm bit survives
		 * the 1->0 write, so it is the READBACK that disagrees.  That is the
		 * only way to inject the real failure the teardown confirms against:
		 * a clear that was written and did not take. */
		if (capture_arm_stuck && (ctrl_cap_cfg & RF_CAP_ARM))
			stored = (stored & ~RF_CAP_ARM) | RF_CAP_ARM;
		ctrl_cap_cfg = stored;
		if (commit != ctrl_cap_commit_prev) {
			ctrl_cap_ratio_lat = ctrl_cap_ratio;
			ctrl_cap_len_lat = ctrl_cap_len;
			ctrl_cap_cont_lat = ctrl_cap_cfg & RF_CAP_CONT;
			ctrl_cap_commit_prev = commit;
			ctrl_cap_status = 0;
		}
		if (!(ctrl_cap_cfg & RF_CAP_ARM))
			ctrl_cap_status = 0;
		break;
	}
	/* RF_REG_STATUS is deliberately absent: it is read-only, and a write that
	 * lands nowhere is exactly what the board does. */
	default: break;
	}
}
