/*
 * UDP DDR PP data-plane receiver (PS GEM0, bare-metal lwIP Raw API).
 *
 * UART1 remains the control/log plane. This module owns only the wire
 * protocol and session state; DDR and playback remain behind callbacks.
 */
#include "xparameters.h"
#include "xil_exception.h"
#include "xil_io.h"
#include "xil_printf.h"
#include "xiltimer.h"
#include "xscugic.h"
#include "lwip/opt.h"
#include "lwip/init.h"
#include "lwip/ip4_addr.h"
#include "lwip/timeouts.h"
#include "lwip/udp.h"
#include "lwip/pbuf.h"
#include "netif/xadapter.h"

#include "udp_ddr_pp.h"

/* ---- wire constants (32-byte network-order header, see host udp_ddr_pp.py) */
#define UDP_DDR_PP_PORT                 40100u
#define UDP_DDR_PP_HEADER_SIZE          32u
#define UDP_DDR_PP_MAX_PAYLOAD         1400u
#define UDP_DDR_PP_BANK_BYTES      0x00100000u
#define UDP_DDR_PP_STATUS_PAYLOAD_SIZE  20u
#define UDP_DDR_PP_SESSION_TIMEOUT      5000u
#define UDP_DDR_PP_SESSION_TIMEOUT_TICKS \
    ((XTime)(((u64)COUNTS_PER_SECOND * UDP_DDR_PP_SESSION_TIMEOUT) / 1000ULL))

#define UDP_DDR_PP_TYPE_ECHO            1u
#define UDP_DDR_PP_TYPE_BEGIN           2u
#define UDP_DDR_PP_TYPE_DATA            3u
#define UDP_DDR_PP_TYPE_STATUS          4u
#define UDP_DDR_PP_TYPE_COMMIT          5u
#define UDP_DDR_PP_TYPE_ABORT           6u
#define UDP_DDR_PP_TYPE_REPLY           0x80u

#define UDP_DDR_PP_ECHO_BANK            0xffffu
#define UDP_DDR_PP_SEQ_STATUS           0xfffffffeu
#define UDP_DDR_PP_SEQ_COMMIT           0xffffffffu
#define UDP_DDR_PP_SEQ_ABORT            0xfffffffdu

static unsigned char gem0_mac[6] = {0x02, 0xac, 0x82, 0x00, 0x00, 0x02};
static struct netif gem0_netif;
static XScuGic gem0_gic;
static struct udp_pcb *gem0_pcb;
static const udp_ddr_pp_ops_t *g_ops;
static int g_initialized;
static int g_last_link_up;
static u8 datagram[UDP_DDR_PP_HEADER_SIZE + UDP_DDR_PP_MAX_PAYLOAD];
static const char *g_ipv4_string = "192.0.2.10";

typedef struct {
    int active;
    u16 bank;
    u32 session;
    u32 total_bytes;
    u32 payload_crc32;
    XTime last_activity;
    u32 state;
    u32 error;
} udp_session_t;

static udp_session_t g_session;

static int gem0_irq_init(void)
{
    XScuGic_Config *gic_config;
    int status;

    gic_config = XScuGic_LookupConfig(XPAR_SCUGIC_SINGLE_DEVICE_ID);
    if (gic_config == NULL)
        return -1;

    Xil_ExceptionDisable();
    status = XScuGic_CfgInitialize(&gem0_gic, gic_config,
                                   gic_config->CpuBaseAddress);
    if (status != XST_SUCCESS)
        return -1;

    Xil_ExceptionInit();
    Xil_ExceptionRegisterHandler(
        XIL_EXCEPTION_ID_INT,
        (Xil_ExceptionHandler)XScuGic_InterruptHandler,
        &gem0_gic);
    XScuGic_SetPriorityTriggerType(&gem0_gic, XPAR_XEMACPS_0_INTR,
                                   0xa0u, 0x3u);
    /*
     * Clear any stale GIC active/pending for the GEM IRQ that may be carried
     * across processor-only reset deployments: `rst -processor` does NOT reset
     * the GIC, so an interrupt acked in a previous boot but never EOI'd stays
     * ACTIVE and (at equal priority) blocks every future presentation of that
     * interrupt, silently killing GEM RX dispatch. Deactivate + clear pending
     * now, while IRQ54 is not yet enabled (xemac_add() enables it later).
     */
    Xil_Out32(0xF8F00110u, (u32)XPAR_XEMACPS_0_INTR);      /* GICC_EOIR */
    Xil_Out32(0xF8F01284u,
              (u32)(1u << (XPAR_XEMACPS_0_INTR - 32u)));   /* GICD_ICPENDR1 */
    return 0;
}

/* ---- explicit big-endian field access (never cast an unaligned struct) ---- */
static u32 be32(const u8 *p)
{
    return ((u32)p[0] << 24) | ((u32)p[1] << 16) |
           ((u32)p[2] << 8) | (u32)p[3];
}

static u16 be16(const u8 *p)
{
    return (u16)(((u16)p[0] << 8) | (u16)p[1]);
}

static void put32(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}

static void put16(u8 *p, u16 v)
{
    p[0] = (u8)(v >> 8);
    p[1] = (u8)v;
}

static u32 udp_ddr_pp_crc32(const u8 *data, u32 len)
{
    static u32 table[256];
    static int tab_init;
    u32 crc = 0xffffffffu;
    u32 i;
    if (!tab_init) {
        u32 n, k;
        for (n = 0; n < 256u; n++) {
            u32 r = n;
            for (k = 0; k < 8u; k++)
                r = (r >> 1u) ^ (0xedb88320u & (0u - (r & 1u)));
            table[n] = r;
        }
        tab_init = 1;
    }
    for (i = 0; i < len; i++)
        crc = table[(crc ^ data[i]) & 0xffu] ^ (crc >> 8u);
    return ~crc;
}

/* ---- reply builder: REPLY = 32-byte header + 20-byte status payload ---- */
static void send_reply(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                       u16 bank, u32 session, u32 sequence,
                       u32 total_bytes, u32 crc32_value,
                       const udp_ddr_pp_status_t *st)
{
    u8 wire[UDP_DDR_PP_HEADER_SIZE + UDP_DDR_PP_STATUS_PAYLOAD_SIZE];
    struct pbuf *p;

    wire[0] = 'A'; wire[1] = 'D'; wire[2] = 'P'; wire[3] = 'P';
    wire[4] = 1;
    wire[5] = UDP_DDR_PP_TYPE_REPLY;
    put16(wire + 6, bank);
    put32(wire + 8, session);
    put32(wire + 12, sequence);
    put32(wire + 16, 0);
    put32(wire + 20, total_bytes);
    put32(wire + 24, UDP_DDR_PP_STATUS_PAYLOAD_SIZE);
    put32(wire + 28, crc32_value);
    put32(wire + 32, st->state);
    put32(wire + 36, st->error);
    put32(wire + 40, st->received_bytes);
    put32(wire + 44, st->first_missing_offset);
    put32(wire + 48, st->first_missing_length);

    p = pbuf_alloc(PBUF_TRANSPORT, sizeof(wire), PBUF_RAM);
    if (p == NULL)
        return;
    if (pbuf_take(p, wire, sizeof(wire)) == ERR_OK)
        udp_sendto(pcb, p, addr, port);
    pbuf_free(p);
}

static void send_error(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                       u16 bank, u32 session, u32 sequence, u32 total_bytes,
                       u32 error)
{
    udp_ddr_pp_status_t st;
    st.received_bytes = 0;
    st.first_missing_offset = 0;
    st.first_missing_length = 0;
    st.state = UDP_DDR_PP_STATE_REJECTED;
    st.error = error;
    send_reply(pcb, addr, port, bank, session, sequence, total_bytes, 0, &st);
}

static void callback_status(u16 bank, udp_ddr_pp_status_t *st)
{
    st->received_bytes = 0;
    st->first_missing_offset = 0;
    st->first_missing_length = 0;
    st->state = UDP_DDR_PP_STATE_IDLE;
    st->error = UDP_DDR_PP_ERROR_NONE;
    if (g_ops != NULL && g_ops->status != NULL)
        g_ops->status(bank, st);
}

static int session_matches(u16 bank, u32 session)
{
    return g_session.bank == bank && g_session.session == session;
}

static u32 reply_total(u32 request_total)
{
    if (g_session.total_bytes != 0u &&
        (g_session.active || g_session.state != UDP_DDR_PP_STATE_IDLE))
        return g_session.total_bytes;
    return request_total;
}

static void send_session_status(struct udp_pcb *pcb, const ip_addr_t *addr,
                                u16_t port, u32 sequence, u32 request_total,
                                u32 crc32_value)
{
    udp_ddr_pp_status_t st;
    if (g_session.state == UDP_DDR_PP_STATE_COMMITTED ||
        g_session.state == UDP_DDR_PP_STATE_ABORTED) {
        st.received_bytes = g_session.state == UDP_DDR_PP_STATE_COMMITTED ?
                            g_session.total_bytes : 0u;
        st.first_missing_offset = st.received_bytes;
        st.first_missing_length = 0;
        st.state = g_session.state;
        st.error = g_session.error;
    } else {
        callback_status(g_session.bank, &st);
        st.error = g_session.error;
    }
    send_reply(pcb, addr, port, g_session.bank, g_session.session, sequence,
               reply_total(request_total), crc32_value, &st);
}

static void session_touch(void)
{
    XTime_GetTime(&g_session.last_activity);
}

static void session_abort(u32 error)
{
    if (g_session.active && g_ops != NULL && g_ops->abort != NULL)
        g_ops->abort(g_session.bank);
    g_session.active = 0;
    g_session.state = UDP_DDR_PP_STATE_ABORTED;
    g_session.error = error;
}

static int valid_transaction_header(u8 type, u16 bank, u32 session,
                                    u32 sequence, u32 offset,
                                    u32 total_bytes, u32 payload_len,
                                    u32 crc32_value)
{
    if (bank > 1u || session == 0u)
        return 0;
    if (type != UDP_DDR_PP_TYPE_DATA && offset != 0u)
        return 0;
    if (type != UDP_DDR_PP_TYPE_DATA && payload_len != 0u)
        return 0;
    if (type == UDP_DDR_PP_TYPE_BEGIN) {
        return sequence == 0u && total_bytes != 0u &&
               (total_bytes & 3u) == 0u && total_bytes <= UDP_DDR_PP_BANK_BYTES;
    }
    if (type == UDP_DDR_PP_TYPE_DATA) {
        return payload_len >= 4u && payload_len <= UDP_DDR_PP_MAX_PAYLOAD &&
               (payload_len & 3u) == 0u && (offset & 3u) == 0u &&
               total_bytes != 0u && (total_bytes & 3u) == 0u &&
               total_bytes <= UDP_DDR_PP_BANK_BYTES &&
               offset + payload_len <= total_bytes;
    }
    if (type == UDP_DDR_PP_TYPE_STATUS)
        return sequence == UDP_DDR_PP_SEQ_STATUS && crc32_value == 0u;
    if (type == UDP_DDR_PP_TYPE_COMMIT)
        return sequence == UDP_DDR_PP_SEQ_COMMIT;
    if (type == UDP_DDR_PP_TYPE_ABORT)
        return sequence == UDP_DDR_PP_SEQ_ABORT && crc32_value == 0u;
    return 0;
}

static void handle_echo(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                        u16 bank, u32 session, u32 sequence, u32 total_bytes,
                        u32 payload_len, u32 crc32_value)
{
    udp_ddr_pp_status_t st;
    if (bank != UDP_DDR_PP_ECHO_BANK || session != 0u || sequence != 0u ||
        total_bytes != 0u || payload_len != 0u || crc32_value != 0u)
        return;
    st.received_bytes = 0;
    st.first_missing_offset = 0;
    st.first_missing_length = 0;
    st.state = UDP_DDR_PP_STATE_IDLE;
    st.error = UDP_DDR_PP_ERROR_NONE;
    send_reply(pcb, addr, port, bank, session, sequence, 0, 0, &st);
}

static void handle_begin(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                         u16 bank, u32 session, u32 sequence,
                         u32 total_bytes, u32 crc32_value)
{
    int result;

    if (g_session.active) {
        if (session_matches(bank, session) &&
            g_session.total_bytes == total_bytes &&
            g_session.payload_crc32 == crc32_value) {
            session_touch();
            send_session_status(pcb, addr, port, sequence, total_bytes,
                                g_session.payload_crc32);
        } else {
            send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                       UDP_DDR_PP_ERROR_BUSY);
        }
        return;
    }
    if (session_matches(bank, session) &&
        g_session.state == UDP_DDR_PP_STATE_COMMITTED) {
        send_session_status(pcb, addr, port, sequence, total_bytes,
                            g_session.payload_crc32);
        return;
    }
    if (g_ops == NULL || g_ops->begin == NULL) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_BUSY);
        return;
    }
    result = g_ops->begin(bank, total_bytes, crc32_value);
    if (result != UDP_DDR_PP_ERROR_NONE) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   (u32)result);
        return;
    }
    g_session.active = 1;
    g_session.bank = bank;
    g_session.session = session;
    g_session.total_bytes = total_bytes;
    g_session.payload_crc32 = crc32_value;
    g_session.state = UDP_DDR_PP_STATE_RECEIVING;
    g_session.error = UDP_DDR_PP_ERROR_NONE;
    session_touch();
    send_session_status(pcb, addr, port, sequence, total_bytes,
                        g_session.payload_crc32);
}

static void handle_data(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                        u16 bank, u32 session, u32 sequence, u32 offset,
                        u32 total_bytes, u32 payload_len, u32 crc32_value)
{
    int result;
    udp_ddr_pp_status_t st;

    if (!g_session.active || !session_matches(bank, session)) {
        if (session_matches(bank, session) && g_session.state != UDP_DDR_PP_STATE_IDLE)
            send_session_status(pcb, addr, port, sequence, total_bytes, 0);
        else
            send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                       UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    if (g_session.total_bytes != total_bytes) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    if (udp_ddr_pp_crc32(datagram + UDP_DDR_PP_HEADER_SIZE, payload_len) != crc32_value) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_CRC);
        return;
    }
    if (g_ops == NULL || g_ops->write == NULL) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_BUSY);
        return;
    }
    result = g_ops->write(bank, offset, datagram + UDP_DDR_PP_HEADER_SIZE,
                          (u16)payload_len);
    if (result != UDP_DDR_PP_ERROR_NONE) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   (u32)result);
        return;
    }
    session_touch();
    callback_status(bank, &st);
    send_reply(pcb, addr, port, bank, session, sequence, total_bytes,
               g_session.payload_crc32, &st);
}

static void handle_status(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                          u16 bank, u32 session, u32 sequence, u32 total_bytes)
{
    if (!session_matches(bank, session) ||
        (g_session.state == UDP_DDR_PP_STATE_IDLE && !g_session.active)) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    send_session_status(pcb, addr, port, sequence, total_bytes,
                        g_session.payload_crc32);
    if (g_session.active)
        session_touch();
}

static void handle_commit(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                          u16 bank, u32 session, u32 sequence,
                          u32 total_bytes, u32 crc32_value)
{
    int result;
    udp_ddr_pp_status_t st;

    if (!session_matches(bank, session)) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    if (!g_session.active && g_session.state == UDP_DDR_PP_STATE_COMMITTED) {
        send_session_status(pcb, addr, port, sequence, total_bytes,
                            g_session.payload_crc32);
        return;
    }
    if (!g_session.active || g_session.total_bytes != total_bytes ||
        g_session.payload_crc32 != crc32_value) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    if (g_ops == NULL || g_ops->commit == NULL) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_BUSY);
        return;
    }
    result = g_ops->commit(bank, total_bytes, crc32_value);
    if (result != UDP_DDR_PP_ERROR_NONE) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   (u32)result);
        return;
    }
    callback_status(bank, &st);
    g_session.active = 0;
    g_session.state = UDP_DDR_PP_STATE_COMMITTED;
    g_session.error = UDP_DDR_PP_ERROR_NONE;
    st.state = UDP_DDR_PP_STATE_COMMITTED;
    st.error = UDP_DDR_PP_ERROR_NONE;
    st.first_missing_offset = total_bytes;
    st.first_missing_length = 0;
    st.received_bytes = total_bytes;
    send_reply(pcb, addr, port, bank, session, sequence, total_bytes,
               g_session.payload_crc32, &st);
}

static void handle_abort(struct udp_pcb *pcb, const ip_addr_t *addr, u16_t port,
                         u16 bank, u32 session, u32 sequence, u32 total_bytes)
{
    if (!session_matches(bank, session)) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   UDP_DDR_PP_ERROR_SESSION);
        return;
    }
    if (g_session.active)
        session_abort(UDP_DDR_PP_ERROR_NONE);
    else if (g_session.state == UDP_DDR_PP_STATE_IDLE)
        g_session.state = UDP_DDR_PP_STATE_ABORTED;
    send_session_status(pcb, addr, port, sequence, total_bytes,
                        g_session.payload_crc32);
}

/* ---- raw UDP receive callback ---- */
static void udp_rx(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                   const ip_addr_t *addr, u16_t port)
{
    u16 len;
    u8 type;
    u16 bank;
    u32 session, sequence, offset, total_bytes, payload_len, crc32_value;

    (void)arg;
    if (p == NULL)
        return;
    len = p->tot_len;
    if (len > sizeof(datagram) || len < UDP_DDR_PP_HEADER_SIZE) {
        pbuf_free(p);
        return;
    }
    if (pbuf_copy_partial(p, datagram, len, 0) != len) {
        pbuf_free(p);
        return;
    }
    pbuf_free(p);

    if (datagram[0] != 'A' || datagram[1] != 'D' ||
        datagram[2] != 'P' || datagram[3] != 'P' || datagram[4] != 1)
        return;
    type = datagram[5];
    bank = be16(datagram + 6);
    session = be32(datagram + 8);
    sequence = be32(datagram + 12);
    offset = be32(datagram + 16);
    total_bytes = be32(datagram + 20);
    payload_len = be32(datagram + 24);
    crc32_value = be32(datagram + 28);
    if (payload_len > sizeof(datagram) - UDP_DDR_PP_HEADER_SIZE ||
        len != UDP_DDR_PP_HEADER_SIZE + payload_len)
        return;

    if (type == UDP_DDR_PP_TYPE_ECHO) {
        handle_echo(pcb, addr, port, bank, session, sequence, total_bytes,
                    payload_len, crc32_value);
        return;
    }
    if (type != UDP_DDR_PP_TYPE_BEGIN && type != UDP_DDR_PP_TYPE_DATA &&
        type != UDP_DDR_PP_TYPE_STATUS && type != UDP_DDR_PP_TYPE_COMMIT &&
        type != UDP_DDR_PP_TYPE_ABORT)
        return;
    if (!valid_transaction_header(type, bank, session, sequence, offset,
                                  total_bytes, payload_len, crc32_value)) {
        send_error(pcb, addr, port, bank, session, sequence, total_bytes,
                   type == UDP_DDR_PP_TYPE_DATA ? UDP_DDR_PP_ERROR_RANGE :
                   UDP_DDR_PP_ERROR_HEADER);
        return;
    }

    switch (type) {
    case UDP_DDR_PP_TYPE_BEGIN:
        handle_begin(pcb, addr, port, bank, session, sequence, total_bytes,
                     crc32_value);
        break;
    case UDP_DDR_PP_TYPE_DATA:
        handle_data(pcb, addr, port, bank, session, sequence, offset,
                    total_bytes, payload_len, crc32_value);
        break;
    case UDP_DDR_PP_TYPE_STATUS:
        handle_status(pcb, addr, port, bank, session, sequence, total_bytes);
        break;
    case UDP_DDR_PP_TYPE_COMMIT:
        handle_commit(pcb, addr, port, bank, session, sequence, total_bytes,
                      crc32_value);
        break;
    case UDP_DDR_PP_TYPE_ABORT:
        handle_abort(pcb, addr, port, bank, session, sequence, total_bytes);
        break;
    default:
        break;
    }
}

/* ---- public API ---- */
int udp_ddr_pp_init(const udp_ddr_pp_ops_t *ops)
{
    ip_addr_t ipaddr, netmask, gw;

    if (g_initialized)
        return 0;

    /*
     * Initialize the GIC distributor/CPU interface and install the IRQ
     * exception vector BEFORE lwIP configures GEM0. The lwIP emacps port
     * (NO_SYS) writes IRQ54's handler into the GIC handler table and enables
     * the interrupt inside xemac_add(); running XScuGic_CfgInitialize()
     * afterwards would reset the GIC and re-stub that entry, so the RX
     * interrupt would never dispatch. We therefore must own only the GIC and
     * exception-vector setup, and leave IRQ54's connection/enable to the
     * driver inside xemac_add().
     */
    if (gem0_irq_init() != 0)
        return -1;

    lwip_init();

    IP4_ADDR(&ipaddr, 192, 168, 2, 2);
    IP4_ADDR(&netmask, 255, 255, 255, 0);
    IP4_ADDR(&gw, 192, 168, 2, 10);
    if (xemac_add(&gem0_netif, &ipaddr, &netmask, &gw, gem0_mac,
                  XPAR_XEMACPS_0_BASEADDR) == NULL)
        return -1;
    netif_set_default(&gem0_netif);
    netif_set_up(&gem0_netif);
    Xil_ExceptionEnable();

    gem0_pcb = udp_new();
    if (gem0_pcb == NULL)
        return -1;
    if (udp_bind(gem0_pcb, IP_ADDR_ANY, UDP_DDR_PP_PORT) != ERR_OK) {
        udp_remove(gem0_pcb);
        gem0_pcb = NULL;
        return -1;
    }
    udp_recv(gem0_pcb, udp_rx, NULL);

    g_ops = ops;
    g_initialized = 1;
    g_last_link_up = 0;
    g_session.active = 0;
    g_session.bank = 0;
    g_session.session = 0;
    g_session.total_bytes = 0;
    g_session.payload_crc32 = 0;
    g_session.last_activity = 0;
    g_session.state = UDP_DDR_PP_STATE_IDLE;
    g_session.error = UDP_DDR_PP_ERROR_NONE;
    xil_printf("NET GEM0 INIT OK IP %s UDP %d\r\n",
               g_ipv4_string, (int)UDP_DDR_PP_PORT);
    return 0;
}

void udp_ddr_pp_poll(void)
{
    int link_up;
    XTime now;

    if (!g_initialized)
        return;
    xemacif_input(&gem0_netif);
#if LWIP_TIMERS
    sys_check_timeouts();
#endif
    if (g_session.active) {
        XTime_GetTime(&now);
        if (now - g_session.last_activity > UDP_DDR_PP_SESSION_TIMEOUT_TICKS)
            session_abort(UDP_DDR_PP_ERROR_TIMEOUT);
    }
    link_up = netif_is_link_up(&gem0_netif) ? 1 : 0;
    if (link_up != g_last_link_up) {
        g_last_link_up = link_up;
        xil_printf(link_up ? "NET GEM0 LINK UP\r\n" : "NET GEM0 LINK DOWN\r\n");
    }
}

int udp_ddr_pp_is_initialized(void)
{
    return g_initialized;
}

int udp_ddr_pp_link_is_up(void)
{
    if (!g_initialized)
        return 0;
    return netif_is_link_up(&gem0_netif) ? 1 : 0;
}

const char *udp_ddr_pp_ipv4_string(void)
{
    return g_ipv4_string;
}
