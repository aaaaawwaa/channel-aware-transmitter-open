/* rf_iq.h -- checked conversion between 12-bit IQ BRAM words and wire IQ. */
#ifndef RF_IQ_H_
#define RF_IQ_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RF_IQ_MIN (-2048)
#define RF_IQ_MAX 2047
#define RF_IQ_WIRE_BYTES_PER_SAMPLE 4u

int rf_iq_validate(int i, int q);
uint32_t rf_iq_pack12(int i, int q);
void rf_iq_unpack12(uint32_t word, int *i, int *q);
size_t rf_iq_wire_bytes(size_t samples);
int rf_iq_wire_to_bram(const uint8_t *src, size_t bytes,
                       uint32_t *dst, size_t words);
int rf_iq_bram_to_wire(const uint32_t *src, size_t words,
                       uint8_t *dst, size_t capacity);

#ifdef __cplusplus
}
#endif

#endif /* RF_IQ_H_ */
