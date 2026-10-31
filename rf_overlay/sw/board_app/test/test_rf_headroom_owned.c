/* Production-shaped ownership, with only the core shutdown symbol wrapped.
 * The wrapper injects a failure on the final shutdown immediately before the
 * write-capable mapping is returned. The real shutdown still runs. */
#define RF_HEADROOM_LIBRARY
#include "test_rf_headroom.c"

int headroom_actual_safe_shutdown(rf_core_t *,rf_hw_status_t *);
static unsigned shutdown_calls;
int rf_core_safe_shutdown(rf_core_t *core,rf_hw_status_t *status)
{
    ++shutdown_calls;
    if(shutdown_calls==2) {
        fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG),RF_CAP_ARM);
        fake_pl_set_capture_arm_stuck(1);
    }
    return headroom_actual_safe_shutdown(core,status);
}

static void test_owned_final_shutdown(int kind)
{
    rf_core_t *core=opened_core();rf_daemon_t *d=NULL;rf_daemon_config_t cfg;
    fill_identity(&cfg);cfg.mode=RF_DAEMON_STATUS_ONLY;cfg.max_samples=4096;
    cfg.verify_control_token=hr_token;cfg.now_ns=headroom_test_clock;
    cfg.control_hw.acquire=probe_acquire;cfg.control_hw.release=probe_release;
    probe_reset(core);fake_pl_set_capture_step(4096);rf_daemon_create(&cfg,&d);
    memset(&hs,0,sizeof hs);hs.t.ctx=&hs;hs.t.read=hread;hs.t.write_all=hwrite;hs.core=core;
    shutdown_calls=0;clock_fault_mode=kind==0?3:0;clock_fault_enabled=clock_fault_calls=0;
    hreq(1,"protocol.hello","{\"major\":1,\"minor\":6}");
    hreq(2,"control.arm","{\"token\":\"research-token\"}");hreq(3,"headroom.begin",BEGIN);
    if(kind==0)hcap(1,4);
    if(kind==1) {
        unsigned a;for(a=1;a<=5;a++){long id=10+a*3;if(a>=2&&a<=4)hload(a,id-1);hcap(a,id);hack(a,id,id+1);}
    }
    if(kind==2)hreq(4,"headroom.abort","{" COMMON "}");
    (void)rf_daemon_serve(d,&hs.t);
    check("final owned shutdown fault actually injected",shutdown_calls==2&&(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
    check("write owner released after final shutdown",probe_release_calls==1);
    if(kind==0) {
        check("owned failed capture retains original IQ",hcontains("research_iq_frame")&&hs.outn>16384u);
        check("owned final shutdown cannot attest confirmed closure",hcontains("\"closure\":{\"attempted\":true,\"confirmed\":false")&&hcontains("\"shutdown_code\":-3")&&hcontains("\"rx_busy\":true"));
    }
    if(kind==1)check("fifth acceptance refuses unconfirmed owned shutdown",hcontains("\"id\":26,\"ok\":false")&&!hcontains("\"completed\":true"));
    if(kind==2)check("abort refuses unconfirmed owned shutdown",hcontains("\"id\":4,\"ok\":false")&&!hcontains("\"aborted\":true"));
    clock_fault_mode=clock_fault_enabled=0;fake_pl_set_capture_arm_stuck(0);hdone(d,core);
}
int main(void)
{
    test_owned_final_shutdown(0);test_owned_final_shutdown(1);test_owned_final_shutdown(2);
    printf("HEADROOM_OWNED %u passed %u failed\n",pass_count,fail_count);
    return fail_count?1:0;
}
