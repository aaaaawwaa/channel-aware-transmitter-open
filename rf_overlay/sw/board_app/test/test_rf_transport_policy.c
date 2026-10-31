#include "rf_transport_policy.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int checks, failures;
static void check(const char *name, int ok) {
    ++checks; if (!ok) { ++failures; printf("FAIL %s\n", name); }
}
/* Faults at each real startup syscall boundary, including partial pipe setup. */
typedef struct {
    int fail_at, call, closes, restores, installed, published;
    int flags[2], reset_before_close, listener, served, core;
} startup_t;
static int step(startup_t *s) { return ++s->call == s->fail_at ? -1 : 0; }
static int pipe_op(void *ctx, int fd[2]) {
    startup_t *s=ctx; fd[0]=41; fd[1]=42; return step(s);
}
static int get_op(void *ctx, int fd) {
    startup_t *s=ctx; (void)fd; return step(s)<0 ? -1 : 16;
}
static int set_op(void *ctx, int fd, int flags) {
    startup_t *s=ctx; if(step(s)<0) return -1;
    s->flags[fd==41?0:1]=flags; return 0;
}
static int install_op(void *ctx, unsigned which) {
    startup_t *s=ctx; (void)which;
    if(step(s)<0) return -1;
    if(s->flags[0]!=16 || s->flags[1]!=16) s->reset_before_close=0;
    ++s->installed; return 0;
}
static void restore_op(void *ctx, unsigned which) {
    startup_t *s=ctx; (void)which; ++s->restores;
}
static void publish_op(void *ctx, int r, int w) {
    startup_t *s=ctx; s->published=(r>=0 || w>=0);
}
static void close_op(void *ctx, int fd) {
    startup_t *s=ctx; (void)fd; ++s->closes;
    if(s->published) s->reset_before_close=0;
}
static rf_stop_ops_t stop_ops(startup_t *s) {
    rf_stop_ops_t o={s,pipe_op,get_op,set_op,install_op,restore_op,publish_op,close_op}; return o;
}
static void startup_tests(void) {
    int fault;
    for(fault=1;fault<=7;++fault) {
        startup_t s; rf_stop_channel_t ch; rf_stop_ops_t o; int rc; char name[100];
        memset(&s,0,sizeof s); s.fail_at=fault; s.reset_before_close=1; o=stop_ops(&s);
        rc=rf_stop_channel_init(&ch,&o);
        /* Same hard-gate order as main: no listener/serve/control after failure. */
        if(rc==0) { ++s.listener; ++s.served; ++s.core; }
        snprintf(name,sizeof name,"startup syscall %d failure refuses service",fault);
        check(name,rc==-1 && !s.listener && !s.served && !s.core);
        snprintf(name,sizeof name,"startup syscall %d closes partial pipe",fault);
        check(name,s.closes==2 && ch.fd[0]==-1 && ch.fd[1]==-1);
        snprintf(name,sizeof name,"startup syscall %d restores installed handlers",fault);
        check(name,s.restores==s.installed && ch.installed==0);
        check("failed startup unpublishes before close",!s.published && s.reset_before_close);
        rf_stop_channel_cleanup(&ch,&o);
        check("failed startup cleanup is idempotent",s.closes==2);
    }
    {
        startup_t s; rf_stop_channel_t ch; rf_stop_ops_t o;
        memset(&s,0,sizeof s); s.reset_before_close=1; o=stop_ops(&s);
        check("successful init checks all seven syscalls",rf_stop_channel_init(&ch,&o)==0 && s.call==7);
        check("successful init has two configured fds and handlers",s.published && s.installed==2 && s.reset_before_close);
        rf_stop_channel_cleanup(&ch,&o);
        check("normal cleanup unpublishes before close and restores",s.closes==2 && s.restores==2 && !s.published && s.reset_before_close);
        rf_stop_channel_cleanup(&ch,&o);
        check("normal cleanup is idempotent",s.closes==2 && s.restores==2);
    }
}

typedef struct {
    int stop, sends, waits, clock_calls, stop_on_send, stop_on_wait, stop_on_clock;
    int clock_fail, wait_result, wait_interrupts;
    int64_t result[12]; int nresult;
    uint64_t now, advance_send, advance_wait;
    unsigned timeout[12];
    size_t length[12]; char bytes[64]; size_t nbytes;
} writer_t;
static int stop_op(void *ctx) { return ((writer_t *)ctx)->stop; }
static int clock_op(void *ctx,uint64_t *out) {
    writer_t *s=ctx; ++s->clock_calls; *out=s->now;
    if(s->clock_calls==s->stop_on_clock) s->stop=1;
    return s->clock_fail ? -1 : 0;
}
static int64_t send_op(void *ctx,const void *buf,size_t len) {
    writer_t *s=ctx; int index=s->sends++; int64_t rc;
    s->length[index<12?index:11]=len; s->now+=s->advance_send;
    if(s->sends==s->stop_on_send) s->stop=1;
    rc=index<s->nresult?s->result[index]:RF_SEND_ERROR;
    if(rc>0 && (uint64_t)rc<=len) { memcpy(s->bytes+s->nbytes,buf,(size_t)rc); s->nbytes+=(size_t)rc; }
    return rc;
}
static int wait_op(void *ctx,unsigned ms) {
    writer_t *s=ctx; int index=s->waits++;
    s->timeout[index<12?index:11]=ms; s->now+=s->advance_wait;
    if(s->waits==s->stop_on_wait) s->stop=1;
    if(s->wait_interrupts>0) { --s->wait_interrupts; return RF_WAIT_INTERRUPTED; }
    return s->wait_result;
}
static rf_write_ops_t write_ops(writer_t *s) {
    rf_write_ops_t o={s,stop_op,clock_op,send_op,wait_op}; return o;
}
static void reset(writer_t *s) { memset(s,0,sizeof *s); s->wait_result=RF_WAIT_READY; s->now=100; }
static int run(writer_t *s) { rf_write_ops_t o=write_ops(s); return rf_transport_write_all(&o,"abcdef",6,10); }
static void writer_tests(void) {
    writer_t s; int rc;
    reset(&s); s.stop=1; s.result[0]=6; s.nresult=1;
    check("preexisting stop performs no clock/send/wait",run(&s)==-1 && s.clock_calls==0 && !s.sends && !s.waits);
    reset(&s); s.stop_on_clock=1; s.result[0]=6; s.nresult=1;
    check("stop between check and first send refuses write",run(&s)==-1 && !s.sends);
    reset(&s); s.result[0]=2; s.result[1]=1; s.result[2]=3; s.nresult=3;
    check("partial sends preserve bytes and offsets",run(&s)==0 && s.sends==3 && s.length[0]==6 && s.length[1]==4 && s.length[2]==3 && !memcmp(s.bytes,"abcdef",6));
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.result[1]=6; s.nresult=2;
    check("EAGAIN waits then sends remaining bytes",run(&s)==0 && s.waits==1 && s.sends==2 && s.timeout[0]==10);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.result[1]=6; s.nresult=2; s.stop_on_wait=1;
    check("stop during backpressure returns before retry",run(&s)==-1 && s.waits==1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.result[1]=6; s.nresult=2; s.wait_result=RF_WAIT_STOP;
    check("self-pipe wake alone stops write",run(&s)==-1 && s.waits==1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_INTERRUPTED; s.result[1]=6; s.nresult=2;
    check("unrelated send EINTR can retry",run(&s)==0 && s.sends==2);
    reset(&s); s.result[0]=RF_SEND_INTERRUPTED; s.result[1]=6; s.nresult=2; s.stop_on_send=1;
    check("termination EINTR stops without retry",run(&s)==-1 && s.sends==1);
    reset(&s); s.result[0]=6; s.nresult=1; s.stop_on_send=1;
    check("stop observed after final send never returns success",run(&s)==-1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.nresult=1; s.wait_result=RF_WAIT_ERROR;
    check("poll hard error refuses write",run(&s)==-1 && s.waits==1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.nresult=1; s.wait_result=RF_WAIT_TIMEOUT;
    check("poll timeout refuses write",run(&s)==-1 && s.waits==1 && s.sends==1);
    reset(&s); s.result[0]=0; s.nresult=1;
    check("zero send refuses without spinning",run(&s)==-1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_ERROR; s.nresult=1;
    check("hard send error refuses without waiting",run(&s)==-1 && s.sends==1 && !s.waits);
    reset(&s); s.result[0]=7; s.nresult=1;
    check("impossible oversize send refuses",run(&s)==-1 && s.sends==1);
    reset(&s); s.clock_fail=1; s.result[0]=6; s.nresult=1;
    check("clock failure refuses before sending",run(&s)==-1 && !s.sends);
    reset(&s); s.now=UINT64_MAX-100; s.result[0]=6; s.nresult=1;
    check("overflowing absolute deadline refuses",run(&s)==-1 && !s.sends);
    reset(&s); s.result[0]=1; s.result[1]=1; s.result[2]=4; s.nresult=3; s.advance_send=6000000;
    check("partial progress cannot refresh absolute deadline",run(&s)==-1 && s.sends==2 && s.nbytes==2);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.result[1]=RF_SEND_AGAIN; s.result[2]=6; s.nresult=3; s.advance_wait=6000000;
    rc=run(&s);
    check("repeated EAGAIN uses remaining deadline",rc==-1 && s.sends==2 && s.waits==2 && s.timeout[0]==10 && s.timeout[1]==4);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.result[1]=RF_SEND_AGAIN; s.result[2]=6; s.nresult=3; s.wait_interrupts=1;
    check("unrelated poll EINTR retries",run(&s)==0 && s.sends==3 && s.waits==2);
    reset(&s); s.result[0]=RF_SEND_AGAIN; s.nresult=1; s.wait_interrupts=1; s.stop_on_wait=1;
    check("stop accompanying poll EINTR preempts retry",run(&s)==-1 && s.waits==1 && s.sends==1);
    reset(&s); s.result[0]=RF_SEND_INTERRUPTED; s.result[1]=RF_SEND_INTERRUPTED; s.result[2]=6; s.nresult=3; s.advance_send=6000000;
    check("send EINTR cannot refresh deadline",run(&s)==-1 && s.sends==2);
    reset(&s); { rf_write_ops_t o=write_ops(&s);
        check("empty write succeeds without syscalls",rf_transport_write_all(&o,NULL,0,10)==0 && !s.clock_calls && !s.sends);
        check("null nonempty buffer refuses",rf_transport_write_all(&o,NULL,1,10)==-1);
        check("zero timeout refuses nonempty buffer",rf_transport_write_all(&o,"a",1,0)==-1);
        check("missing ops refuse",rf_transport_write_all(NULL,"a",1,10)==-1);
    }
}
int main(void) {
    printf("=== test_rf_transport_policy ===\n"); startup_tests(); writer_tests();
    printf("=== %d PASS, %d FAIL ===\n",checks-failures,failures); return failures?1:0;
}
