/* test_rf_iq.c -- host tests for the RF IQ wire/BRAM contract. */
#include "rf_iq.h"
#include "platform_rf.h"

#include <stdint.h>
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

static void put_s16_le(uint8_t *p, int value)
{
    uint16_t encoded = (uint16_t)value;

    p[0] = (uint8_t)(encoded & 0xFFu);
    p[1] = (uint8_t)(encoded >> 8);
}

static int get_s16_le(const uint8_t *p)
{
    uint16_t encoded = (uint16_t)p[0] | ((uint16_t)p[1] << 8);

    return (encoded & 0x8000u) ? (int)encoded - 0x10000 : (int)encoded;
}

int main(void)
{
    static const int values[] = { -2048, -1, 0, 2047 };
    uint32_t words[4];
    uint32_t untouched[2] = { 0xAAAAAAAAu, 0xBBBBBBBBu };
    uint8_t wire[sizeof(words)];
    uint8_t expected_wire[sizeof(wire)];
    uint8_t invalid_wire[sizeof(wire)];
    static uint8_t oversized_wire[(RF_BRAM_WORDS + 1u) *
                                   RF_IQ_WIRE_BYTES_PER_SAMPLE];
    static uint32_t oversized_words[RF_BRAM_WORDS + 1u];
    size_t i;

    printf("=== test_rf_iq ===\n");

    for (i = 0; i < sizeof(values) / sizeof(values[0]); ++i) {
        size_t q;
        for (q = 0; q < sizeof(values) / sizeof(values[0]); ++q) {
            int unpacked_i = 0;
            int unpacked_q = 0;
            uint32_t word = rf_iq_pack12(values[i], values[q]);

            rf_iq_unpack12(word, &unpacked_i, &unpacked_q);
            check("12-bit pack/unpack boundary round-trip",
                  unpacked_i == values[i] && unpacked_q == values[q]);
        }
    }

    check("minimum values are accepted",
          rf_iq_validate(-2048, -2048) == 0);
    check("maximum values are accepted",
          rf_iq_validate(2047, 2047) == 0);
    check("I below range is rejected", rf_iq_validate(-2049, 0) != 0);
    check("Q below range is rejected", rf_iq_validate(0, -2049) != 0);
    check("I above range is rejected", rf_iq_validate(2048, 0) != 0);
    check("Q above range is rejected", rf_iq_validate(0, 2048) != 0);

    check("wire bytes are four per sample",
          rf_iq_wire_bytes(4) == 16 && rf_iq_wire_bytes(0) == 0);

    for (i = 0; i < 4; ++i) {
        words[i] = rf_iq_pack12(values[i], values[3 - i]);
        put_s16_le(expected_wire + i * 4u, values[i]);
        put_s16_le(expected_wire + i * 4u + 2u, values[3 - i]);
    }

    check("BRAM to wire uses I then Q little-endian s16",
          rf_iq_bram_to_wire(words, 4, wire, sizeof(wire)) == 0 &&
          memcmp(wire, expected_wire, sizeof(wire)) == 0);
    check("wire sign extension preserves -2048 and -1",
          get_s16_le(wire) == -2048 && get_s16_le(wire + 4) == -1);

    memset(words, 0, sizeof(words));
    check("wire to BRAM round-trips the boundary frame",
          rf_iq_wire_to_bram(expected_wire, sizeof(expected_wire), words, 4) == 0);
    for (i = 0; i < 4; ++i) {
        int got_i = 0;
        int got_q = 0;
        rf_iq_unpack12(words[i], &got_i, &got_q);
        check("wire/BRAM sample round-trip",
              got_i == values[i] && got_q == values[3 - i]);
    }

    check("short wire payload is rejected",
          rf_iq_wire_to_bram(expected_wire, sizeof(expected_wire) - 1u,
                             untouched, 4) != 0);
    check("long wire payload is rejected",
          rf_iq_wire_to_bram(expected_wire, sizeof(expected_wire) + 1u,
                             untouched, 4) != 0);
    check("short output capacity is rejected",
          rf_iq_bram_to_wire(words, 4, wire, sizeof(wire) - 1u) != 0);
    check("invalid length leaves destination untouched",
          untouched[0] == 0xAAAAAAAAu && untouched[1] == 0xBBBBBBBBu);

    memcpy(invalid_wire, expected_wire, sizeof(invalid_wire));
    put_s16_le(invalid_wire + 4u, 2048); /* I of sample 1 is out of range. */
    untouched[0] = 0xAAAAAAAAu;
    untouched[1] = 0xBBBBBBBBu;
    check("out-of-range wire sample is rejected",
          rf_iq_wire_to_bram(invalid_wire, sizeof(invalid_wire),
                             untouched, 4) != 0);
    check("out-of-range frame performs no partial BRAM write",
          untouched[0] == 0xAAAAAAAAu && untouched[1] == 0xBBBBBBBBu);

    check("null pointers are rejected for non-empty frames",
          rf_iq_wire_to_bram(NULL, 4, untouched, 1) != 0 &&
          rf_iq_bram_to_wire(untouched, 1, NULL, 4) != 0);
    check("wire to BRAM rejects more than the BRAM window",
          rf_iq_wire_to_bram(oversized_wire, sizeof(oversized_wire),
                             oversized_words, RF_BRAM_WORDS + 1u) != 0);
    check("BRAM to wire rejects more than the BRAM window",
          rf_iq_bram_to_wire(oversized_words, RF_BRAM_WORDS + 1u,
                             oversized_wire, sizeof(oversized_wire)) != 0);
    check("empty frame is accepted",
          rf_iq_wire_to_bram(NULL, 0, NULL, 0) == 0 &&
          rf_iq_bram_to_wire(NULL, 0, NULL, 0) == 0);

    printf("=== %u PASS, %u FAIL ===\n", pass_count, fail_count);
    printf("RESULT %s\n", fail_count ? "FAIL" : "PASS");
    return fail_count ? 1 : 0;
}
