#ifndef UDP_DDR_PP_H
#define UDP_DDR_PP_H

#include "xil_types.h"

enum udp_ddr_pp_state {
    UDP_DDR_PP_STATE_IDLE = 0,
    UDP_DDR_PP_STATE_RECEIVING = 1,
    UDP_DDR_PP_STATE_COMMITTED = 2,
    UDP_DDR_PP_STATE_ABORTED = 3,
    UDP_DDR_PP_STATE_REJECTED = 4,
};

enum udp_ddr_pp_error {
    UDP_DDR_PP_ERROR_NONE = 0,
    UDP_DDR_PP_ERROR_HEADER = 1,
    UDP_DDR_PP_ERROR_SESSION = 2,
    UDP_DDR_PP_ERROR_BUSY = 3,
    UDP_DDR_PP_ERROR_RANGE = 4,
    UDP_DDR_PP_ERROR_CRC = 5,
    UDP_DDR_PP_ERROR_INCOMPLETE = 6,
    UDP_DDR_PP_ERROR_TIMEOUT = 7,
    UDP_DDR_PP_ERROR_CONFLICT = 8,
};

typedef struct {
    u32 received_bytes;
    u32 first_missing_offset;
    u32 first_missing_length;
    u32 state;
    u32 error;
} udp_ddr_pp_status_t;

typedef struct {
    int (*begin)(u16 bank, u32 total_bytes, u32 payload_crc32);
    int (*write)(u16 bank, u32 offset, const u8 *data, u16 length);
    int (*commit)(u16 bank, u32 total_bytes, u32 payload_crc32);
    void (*abort)(u16 bank);
    void (*status)(u16 bank, udp_ddr_pp_status_t *status);
} udp_ddr_pp_ops_t;

int udp_ddr_pp_init(const udp_ddr_pp_ops_t *ops);
void udp_ddr_pp_poll(void);
int udp_ddr_pp_is_initialized(void);
int udp_ddr_pp_link_is_up(void);
const char *udp_ddr_pp_ipv4_string(void);

#endif
