/* rf_iq.c -- explicit, checked IQ wire/BRAM conversion. */
#include "rf_iq.h"
#include "platform_rf.h"

#include <stddef.h>
#include <stdint.h>

static int wire_size(size_t samples, size_t *out)
{
    if (samples > SIZE_MAX / RF_IQ_WIRE_BYTES_PER_SAMPLE)
        return -1;
    *out = samples * RF_IQ_WIRE_BYTES_PER_SAMPLE;
    return 0;
}

static int wire_s16_le(const uint8_t *p)
{
    uint16_t value = (uint16_t)p[0] | ((uint16_t)p[1] << 8);

    /* Decode explicitly instead of relying on the host's int16 representation. */
    return (value & 0x8000u) ? (int)value - 0x10000 : (int)value;
}

static void wire_put_s16_le(uint8_t *p, int value)
{
    uint16_t encoded = (uint16_t)value;

    p[0] = (uint8_t)(encoded & 0xFFu);
    p[1] = (uint8_t)(encoded >> 8);
}

int rf_iq_validate(int i, int q)
{
    return (i < RF_IQ_MIN || i > RF_IQ_MAX ||
            q < RF_IQ_MIN || q > RF_IQ_MAX) ? -1 : 0;
}

uint32_t rf_iq_pack12(int i, int q)
{
    /* The existing platform helper is the authoritative 12-bit two's-complement
     * packing rule.  Callers that accept external values must validate first. */
    return rf_iq_pack(i, q);
}

void rf_iq_unpack12(uint32_t word, int *i, int *q)
{
    if (i)
        *i = rf_iq_i(word);
    if (q)
        *q = rf_iq_q(word);
}

size_t rf_iq_wire_bytes(size_t samples)
{
    size_t bytes;

    return wire_size(samples, &bytes) == 0 ? bytes : 0;
}

int rf_iq_wire_to_bram(const uint8_t *src, size_t bytes,
                       uint32_t *dst, size_t words)
{
    size_t expected;
    size_t n;

    if (words > RF_BRAM_WORDS)
        return -1;
    if (wire_size(words, &expected) != 0 || bytes != expected)
        return -1;
    if (words != 0 && (!src || !dst))
        return -1;

    /* Validate the complete frame before touching BRAM.  A bad sample therefore
     * cannot leave a partially loaded TX buffer behind. */
    for (n = 0; n < words; n++) {
        int i = wire_s16_le(src + n * RF_IQ_WIRE_BYTES_PER_SAMPLE);
        int q = wire_s16_le(src + n * RF_IQ_WIRE_BYTES_PER_SAMPLE + 2u);

        if (rf_iq_validate(i, q) != 0)
            return -1;
    }

    for (n = 0; n < words; n++) {
        int i = wire_s16_le(src + n * RF_IQ_WIRE_BYTES_PER_SAMPLE);
        int q = wire_s16_le(src + n * RF_IQ_WIRE_BYTES_PER_SAMPLE + 2u);

        dst[n] = rf_iq_pack12(i, q);
    }
    return 0;
}

int rf_iq_bram_to_wire(const uint32_t *src, size_t words,
                       uint8_t *dst, size_t capacity)
{
    size_t expected;
    size_t n;

    if (words > RF_BRAM_WORDS)
        return -1;
    if (wire_size(words, &expected) != 0 || capacity < expected)
        return -1;
    if (words != 0 && (!src || !dst))
        return -1;

    for (n = 0; n < words; n++) {
        int i;
        int q;

        rf_iq_unpack12(src[n], &i, &q);
        wire_put_s16_le(dst + n * RF_IQ_WIRE_BYTES_PER_SAMPLE, i);
        wire_put_s16_le(dst + n * RF_IQ_WIRE_BYTES_PER_SAMPLE + 2u, q);
    }
    return 0;
}
