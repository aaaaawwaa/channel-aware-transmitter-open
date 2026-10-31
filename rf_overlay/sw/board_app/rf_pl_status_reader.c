#include "rf_pl_status_reader.h"
#include "platform_rf.h"
#include <stdlib.h>
#include <string.h>
#ifndef PLATFORM_HOST
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#endif

struct rf_pl_status_reader {
    rf_pl_read_fn read;
    void *ctx;
#ifndef PLATFORM_HOST
    int fd;
    void *page;
#endif
};

#ifndef PLATFORM_HOST
static int read_mapped(void *ctx, uint32_t address, uint32_t *value)
{
    rf_pl_status_reader_t *reader = (rf_pl_status_reader_t *)ctx;
    if (!reader->page || address < RF_AXI_CTRL_BASE ||
        address >= RF_AXI_CTRL_BASE + 4096u || !value)
        return -1;
    *value = *(volatile const uint32_t *)((const unsigned char *)reader->page +
                                          (address - RF_AXI_CTRL_BASE));
    return 0;
}
#endif

rf_pl_status_reader_t *rf_pl_status_reader_from_callback(rf_pl_read_fn read,
                                                         void *ctx)
{
    rf_pl_status_reader_t *reader = calloc(1, sizeof *reader);
    if (!reader)
        return NULL;
#ifndef PLATFORM_HOST
    /* calloc zeroes fd, and 0 is a VALID descriptor: stdin.  A reader built
     * this way (rf_pl_status_reader_open's own first step, or a test callback)
     * would, if closed without a real open, take the process's stdin with it --
     * rf_pl_status_reader_close only skips fd when it is negative. */
    reader->fd = -1;
#endif
    reader->read = read;
    reader->ctx = ctx;
    return reader;
}

rf_pl_status_reader_t *rf_pl_status_reader_open(void)
{
#ifndef PLATFORM_HOST
    rf_pl_status_reader_t *reader = rf_pl_status_reader_from_callback(NULL, NULL);
    if (!reader)
        return NULL;
    reader->fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (reader->fd < 0) {
        free(reader);
        return NULL;
    }
    reader->page = mmap(NULL, 4096u, PROT_READ, MAP_SHARED, reader->fd,
                        RF_AXI_CTRL_BASE);
    if (reader->page == MAP_FAILED) {
        close(reader->fd);
        free(reader);
        return NULL;
    }
    reader->read = read_mapped;
    reader->ctx = reader;
    return reader;
#else
    return rf_pl_status_reader_from_callback(NULL, NULL);
#endif
}

void rf_pl_status_reader_close(rf_pl_status_reader_t *reader)
{
    if (!reader)
        return;
#ifndef PLATFORM_HOST
    if (reader->page && reader->page != MAP_FAILED)
        munmap(reader->page, 4096u);
    if (reader->fd >= 0)
        close(reader->fd);
#endif
    free(reader);
}

static int read_reg(rf_pl_status_reader_t *reader, uint32_t address,
                    uint32_t *value)
{
    return reader && reader->read &&
           reader->read(reader->ctx, address, value) == 0;
}

void rf_pl_status_sample(rf_pl_status_reader_t *reader, uint64_t now_ns,
                         rf_pl_status_t *out)
{
    uint32_t value;
    if (!out)
        return;
    memset(out, 0, sizeof *out);
    out->sampled_at_monotonic_ns = now_ns;
    if (read_reg(reader, RF_CTRL_A(RF_REG_SRC_CFG), &value)) {
        out->tx_source_valid = true;
        out->tx_source_selected = (value & RF_SRC_SEL) != 0;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_SMP_LEN), &value)) {
        out->tx_length_valid = true;
        out->tx_length_words = value & 0x1fffu;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_SMP_POS), &value)) {
        out->tx_position_valid = true;
        out->tx_position_words = value & 0x1fffu;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_CFG), &value)) {
        out->rx_armed_valid = true;
        out->rx_armed = (value & RF_CAP_ARM) != 0;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_STATUS), &value)) {
        out->rx_done_valid = out->rx_position_valid = true;
        out->rx_done = RF_CAP_DONE(value) != 0;
        out->rx_position_words = RF_CAP_POS(value);
    }
    /* Optional read-only diagnostics.  Read failures leave the corresponding
     * valid flag false; they must never become a fabricated zero. */
    if (read_reg(reader, RF_CTRL_A(RF_REG_STATUS), &value)) {
        out->status_valid = true;
        out->status_word = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_PHY_MODE), &value)) {
        out->phy_mode_valid = true;
        out->phy_mode = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_IDELAY_EN), &value)) {
        out->idelay_en_valid = true;
        out->idelay_en = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_IDELAY_TAP), &value)) {
        out->idelay_tap_valid = true;
        out->idelay_tap = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_SRC_CFG), &value)) {
        out->src_cfg_valid = true;
        out->src_cfg = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_CFG), &value)) {
        out->cap_cfg_valid = true;
        out->cap_cfg = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_RATIO), &value)) {
        out->cap_ratio_valid = true;
        out->cap_ratio = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_LEN), &value)) {
        out->cap_len_valid = true;
        out->cap_len = value;
    }
    if (read_reg(reader, RF_CTRL_A(RF_REG_CAP_STATUS), &value)) {
        out->cap_status_valid = true;
        out->cap_status = value;
    }
    out->io_ok = out->tx_source_valid && out->tx_length_valid &&
                 out->tx_position_valid && out->rx_armed_valid &&
                 out->rx_done_valid && out->rx_position_valid;
}
