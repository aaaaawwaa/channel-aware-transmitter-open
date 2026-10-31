#include "rf_transport_policy.h"

static int stop_ops_valid(const rf_stop_ops_t *o)
{
    return o && o->make_pipe && o->get_flags && o->set_nonblocking &&
           o->install_signal && o->restore_signal && o->publish && o->close_fd;
}

void rf_stop_channel_cleanup(rf_stop_channel_t *s, const rf_stop_ops_t *o)
{
    unsigned i;
    if (!s || !stop_ops_valid(o))
        return;
    /* Handler-visible descriptors disappear BEFORE restore/close. A signal
     * in cleanup can set the flag but cannot write to a closed/reused fd. */
    o->publish(o->ctx, -1, -1);
    for (i = 2u; i > 0u; --i) {
        unsigned which = i - 1u;
        if (s->installed & (1u << which)) {
            o->restore_signal(o->ctx, which);
            s->installed &= ~(1u << which);
        }
    }
    for (i = 0; i < 2u; ++i) {
        int fd = s->fd[i];
        s->fd[i] = -1;
        if (fd >= 0)
            o->close_fd(o->ctx, fd);
    }
}

int rf_stop_channel_init(rf_stop_channel_t *s, const rf_stop_ops_t *o)
{
    unsigned i;
    if (!s)
        return -1;
    s->fd[0] = s->fd[1] = -1;
    s->installed = 0;
    if (!stop_ops_valid(o))
        return -1;
    o->publish(o->ctx, -1, -1);
    if (o->make_pipe(o->ctx, s->fd) != 0 || s->fd[0] < 0 || s->fd[1] < 0)
        goto failed;
    for (i = 0; i < 2u; ++i) {
        int flags = o->get_flags(o->ctx, s->fd[i]);
        if (flags < 0 || o->set_nonblocking(o->ctx, s->fd[i], flags) != 0)
            goto failed;
    }
    /* Publish only after BOTH ends are known nonblocking; install handlers
     * afterwards so the first delivered signal already has a safe wake path. */
    o->publish(o->ctx, s->fd[0], s->fd[1]);
    for (i = 0; i < 2u; ++i) {
        if (o->install_signal(o->ctx, i) != 0)
            goto failed;
        s->installed |= 1u << i;
    }
    return 0;
failed:
    rf_stop_channel_cleanup(s, o);
    return -1;
}

int rf_transport_write_all(const rf_write_ops_t *o, const void *buf, size_t len,
                           unsigned timeout_ms)
{
    uint64_t now, start, deadline, budget;
    size_t sent = 0;
    if (!o || !o->should_stop || !o->now_ns || !o->send_nonblocking ||
        !o->wait_writable_stop || (!buf && len))
        return -1;
    if (o->should_stop(o->ctx))
        return -1;
    if (!len)
        return 0;
    if (!timeout_ms || o->now_ns(o->ctx, &start) != 0 || o->should_stop(o->ctx))
        return -1;
    budget = (uint64_t)timeout_ms * 1000000ull;
    if (start > UINT64_MAX - budget)
        return -1;
    deadline = start + budget;
    while (sent < len) {
        int64_t n;
        if (o->should_stop(o->ctx) || o->now_ns(o->ctx, &now) != 0 ||
            o->should_stop(o->ctx) || now < start || now >= deadline)
            return -1;
        n = o->send_nonblocking(o->ctx, (const char *)buf + sent, len - sent);
        /* Even completion/interruption/errors can coincide with termination. */
        if (o->should_stop(o->ctx))
            return -1;
        if (n > 0) {
            if ((uint64_t)n > (uint64_t)(len - sent))
                return -1;
            sent += (size_t)n;
            continue;
        }
        if (n == RF_SEND_INTERRUPTED)
            continue;  /* deadline and stop checked again, never refreshed */
        if (n != RF_SEND_AGAIN)
            return -1;
        if (o->now_ns(o->ctx, &now) != 0 || o->should_stop(o->ctx) ||
            now < start || now >= deadline)
            return -1;
        {
            unsigned remaining_ms = (unsigned)((deadline - now + 999999ull) / 1000000ull);
            int result = o->wait_writable_stop(o->ctx, remaining_ms);
            if (o->should_stop(o->ctx))
                return -1;
            if (result != RF_WAIT_READY && result != RF_WAIT_INTERRUPTED)
                return -1;
        }
    }
    /* A final send that consumed the remaining budget is not an on-time write. */
    if (o->now_ns(o->ctx, &now) != 0 || o->should_stop(o->ctx) ||
        now < start || now >= deadline)
        return -1;
    return 0;
}
