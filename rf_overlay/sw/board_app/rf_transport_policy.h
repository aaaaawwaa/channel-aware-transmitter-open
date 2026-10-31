/* Portable policy shared by the production POSIX adapters and syscall tests.
 * Adapters must never block in send_nonblocking, and wait_writable_stop must
 * wait on BOTH socket writability and the configured nonblocking stop pipe. */
#ifndef RF_TRANSPORT_POLICY_H
#define RF_TRANSPORT_POLICY_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    int fd[2];
    unsigned installed;
} rf_stop_channel_t;
typedef struct {
    void *ctx;
    int (*make_pipe)(void *, int [2]);
    int (*get_flags)(void *, int);
    int (*set_nonblocking)(void *, int, int);
    int (*install_signal)(void *, unsigned);
    void (*restore_signal)(void *, unsigned);
    void (*publish)(void *, int, int);
    void (*close_fd)(void *, int);
} rf_stop_ops_t;
int rf_stop_channel_init(rf_stop_channel_t *, const rf_stop_ops_t *);
void rf_stop_channel_cleanup(rf_stop_channel_t *, const rf_stop_ops_t *);

/* Negative send results distinguish retryable syscall errors. A zero send is
 * a failure; it must not spin. Wait returns READY, STOP, TIMEOUT or an error. */
enum { RF_SEND_ERROR = -1, RF_SEND_AGAIN = -2, RF_SEND_INTERRUPTED = -3 };
enum { RF_WAIT_ERROR = -1, RF_WAIT_INTERRUPTED = -2,
       RF_WAIT_TIMEOUT = 0, RF_WAIT_READY = 1, RF_WAIT_STOP = 2 };
#define RF_SEND_TIMEOUT_MS 1000u

typedef struct {
    void *ctx;
    int (*should_stop)(void *);
    int (*now_ns)(void *, uint64_t *);
    int64_t (*send_nonblocking)(void *, const void *, size_t);
    int (*wait_writable_stop)(void *, unsigned);
} rf_write_ops_t;
/* One absolute deadline per write_all, never extended by partial progress.
 * 0 only after the whole buffer is sent without observing stop; -1 otherwise. */
int rf_transport_write_all(const rf_write_ops_t *, const void *, size_t,
                           unsigned timeout_ms);
#endif
