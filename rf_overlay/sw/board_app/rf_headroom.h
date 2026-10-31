#ifndef RF_HEADROOM_H_
#define RF_HEADROOM_H_
#include <stdbool.h>
#include <stdint.h>
#include "rf_protocol.h"
typedef struct { bool used, active, waiting, loaded; unsigned attempt; char session_id[33]; long frame_request_id; uint64_t timestamp_ns; } rf_headroom_t;
const char *rf_headroom_scenario(unsigned attempt);
const char *rf_headroom_payload_sha(unsigned attempt);
const char *rf_headroom_declaration_sha(unsigned attempt);
int rf_headroom_identity(const char *args, const char *session_id);
int rf_headroom_begin(rf_headroom_t *h,const char *args);
int rf_headroom_load_declaration(const char *args,unsigned attempt);
void rf_headroom_seal(rf_headroom_t *h);
#endif
