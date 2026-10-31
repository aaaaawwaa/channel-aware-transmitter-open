#define main old_daemon_test_main
#include "test_rf_daemon.c"
#undef main
#include "rf_headroom.h"
#include "rf_sha256.h"
#include "headroom_payloads.h"
#include <stdlib.h>
#define SID "0123456789abcdef0123456789abcdef"
#define COMMON "\"study\":\"HEADROOM-1\",\"session_id\":\"" SID "\""
#define BEGIN "{" COMMON ",\"cable_confirmed\":true,\"lo_hz\":80000000,\"sample_rate_hz\":60000000,\"rx_bandwidth_hz\":30000000,\"tx_bandwidth_hz\":30000000,\"attenuation_mdB\":46000,\"phy_mode_1r1t\":0,\"idelay_en\":1,\"idelay_tap\":29}"
typedef struct {rf_transport_t t;unsigned char in[131072],out[262144];size_t n,pos,outn;int tx_on,tx_on_during_off,frame_fail,mode; rf_core_t *core;} hs_t;
static hs_t hs;
/* 0 = off; 1 = the TX2 getter fails; 2 = it disagrees.  Injected when the BEGIN
 * reply goes out, because rf_core_init memsets the vendor model: a fault set before
 * the request is wiped by the initialization it is meant to be observed after. */
static int getter_fault_arm;
static int clock_fault_mode,clock_fault_calls,clock_fault_enabled;
static uint64_t headroom_test_clock(void)
{
    uint64_t value=test_now_ns();
    if(clock_fault_enabled) {
        ++clock_fault_calls;
        /* First call is run_loop's pre-capture watchdog. Remaining calls
         * bracket source, arm, drain, release and the captured timestamp. */
        if(clock_fault_mode==1 && clock_fault_calls==9)
            rf_core_host_set_headroom_tx2_fault(hs.core,1);
        if(clock_fault_mode==2 && clock_fault_calls==8)
            fake_pl_set_capture_arm_stuck(1);
        if(clock_fault_mode==3 && clock_fault_calls==10)return 0;
        if(clock_fault_mode==4 && clock_fault_calls==10)return 1;
        if(clock_fault_mode==5 && clock_fault_calls==2)
            fake_pl_set_capture_done_stuck(1);
    }
    return value;
}
static int hhas(const char *buf,size_t n,const char *needle){size_t m=strlen(needle),i;for(i=0;i+m<=n;i++)if(!memcmp(buf+i,needle,m))return 1;return 0;}
static int hread(void *p,void *buf,size_t n){hs_t *s=p;size_t avail=s->n-s->pos;
 if(!avail)return 0;if(n>avail)n=avail;memcpy(buf,s->in+s->pos,n);s->pos+=n;return (int)n;}
static int hwrite(void *p,const void *buf,size_t n){hs_t *s=p;
 if(s->frame_fail&&n>16000)return -1; if(n<16000)fwrite(buf,1,n,stderr);
 if(s->outn+n>sizeof s->out)return -1;memcpy(s->out+s->outn,buf,n);s->outn+=n;
 if(getter_fault_arm&&s->core&&hhas(buf,n,"\"initializations\":1")) {
     if(getter_fault_arm==3)rf_core_host_set_vendor_readback_fault(s->core,1);
     else rf_core_host_set_headroom_tx2_fault(s->core,getter_fault_arm);
 }
 if(clock_fault_mode&&hhas(buf,n,"\"initializations\":1")) {clock_fault_enabled=1;clock_fault_calls=0;}
 return 0;}
static void hreq(long id,const char *op,const char *args){int n=snprintf((char*)hs.in+hs.n,sizeof hs.in-hs.n,"{\"id\":%ld,\"op\":\"%s\",\"args\":%s}\n",id,op,args);hs.n+=(size_t)n;}
static int hcontains_from(size_t from,const char *x){size_t i,n=strlen(x);for(i=from;i+n<=hs.outn;i++)if(!memcmp(hs.out+i,x,n))return 1;return 0;}
static int hcontains(const char *x){return hcontains_from(0,x);}
static int hr_token(const char *s){return strcmp(s,"research-token");}
static rf_daemon_t *hfixture(rf_core_t **core){rf_daemon_config_t c;rf_daemon_t *d=NULL;*core=opened_core();fill_identity(&c);c.max_samples=4096;c.verify_control_token=hr_token;c.now_ns=headroom_test_clock;
 fake_pl_set_capture_step(4096);rf_daemon_create(&c,&d);rf_daemon_attach_core(d,*core);memset(&hs,0,sizeof hs);hs.t.ctx=&hs;hs.t.read=hread;hs.t.write_all=hwrite;hs.core=*core;
 hreq(1,"protocol.hello","{\"major\":1,\"minor\":6}");hreq(2,"control.arm","{\"token\":\"research-token\"}");return d;}
static void hcap(unsigned a,long id){char args[300];snprintf(args,sizeof args,"{" COMMON ",\"attempt\":%u,\"scenario\":\"%s\",\"samples\":4096,\"ratio\":0,\"settle_wait_us\":0}",a,rf_headroom_scenario(a));hreq(id,"headroom.capture",args);}
static void hack(unsigned a,long frame,long id){char args[300];snprintf(args,sizeof args,"{" COMMON ",\"attempt\":%u,\"frame_id\":%ld,\"timestamp_ns\":\"1234567890123\",\"accepted\":true}",a,frame);hreq(id,"headroom.accept",args);}
static const char *load_normal="{\"study\":\"HEADROOM-1\",\"session_id\":\"0123456789abcdef0123456789abcdef\",\"attempt\":2,\"scenario\":\"normal\",\"samples\":2540,\"bytes\":10160,\"format\":\"s16_iq_le\",\"direction\":\"tx\",\"wrap\":true,\"a\":60,\"formula\":\"x=A*s+j\",\"ps\":2540,\"sy\":127,\"max\":100,\"prbs\":1,\"pa\":0,\"pp\":2540,\"ph\":0,\"start\":600,\"width\":80,\"quant\":\"numpy.rint / signed12 / no global scaling\",\"fs\":60000000,\"seed\":1,\"shape\":\"RECT\",\"rs\":3000000,\"ta\":0,\"bin\":254,\"fa\":6000000,\"fr\":6000000,\"tp\":0,\"truth\":\"normal\",\"v\":\"rf-controlled-scenarios-v1\",\"source_sha256\":\"38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6\",\"decl_sha256\":\"788e61dc71ab5d6ffbe28f67f7797457539819920c955daff2ad96ca2d200a6b\"}";
static const char *load_narrowband="{\"study\":\"HEADROOM-1\",\"session_id\":\"0123456789abcdef0123456789abcdef\",\"attempt\":3,\"scenario\":\"narrowband\",\"samples\":2540,\"bytes\":10160,\"format\":\"s16_iq_le\",\"direction\":\"tx\",\"wrap\":true,\"a\":60,\"formula\":\"x=A*s+j\",\"ps\":2540,\"sy\":127,\"max\":100,\"prbs\":1,\"pa\":0,\"pp\":2540,\"ph\":0,\"start\":600,\"width\":80,\"quant\":\"numpy.rint / signed12 / no global scaling\",\"fs\":60000000,\"seed\":1,\"shape\":\"RECT\",\"rs\":3000000,\"ta\":40,\"bin\":254,\"fa\":6000000,\"fr\":6000000,\"tp\":0,\"truth\":\"narrowband\",\"v\":\"rf-controlled-scenarios-v1\",\"source_sha256\":\"4693355b00a98acc26851df3c86e38da49672fb7713983b29edd061627845f73\",\"decl_sha256\":\"b02d0cdd464109d9df9a3dc13eb38b0d9b0b8075b88819c96a52a48c061b780a\"}";
static const char *load_pulse="{\"study\":\"HEADROOM-1\",\"session_id\":\"0123456789abcdef0123456789abcdef\",\"attempt\":4,\"scenario\":\"pulse\",\"samples\":2540,\"bytes\":10160,\"format\":\"s16_iq_le\",\"direction\":\"tx\",\"wrap\":true,\"a\":60,\"formula\":\"x=A*s+j\",\"ps\":2540,\"sy\":127,\"max\":100,\"prbs\":1,\"pa\":40,\"pp\":2540,\"ph\":0,\"start\":600,\"width\":80,\"quant\":\"numpy.rint / signed12 / no global scaling\",\"fs\":60000000,\"seed\":1,\"shape\":\"RECT\",\"rs\":3000000,\"ta\":0,\"bin\":254,\"fa\":6000000,\"fr\":6000000,\"tp\":0,\"truth\":\"pulse\",\"v\":\"rf-controlled-scenarios-v1\",\"source_sha256\":\"22463cf461cd2141fbd3641d28294a740a39aa985c7943bea5aeb2bfcb13bbee\",\"decl_sha256\":\"e65d79dc4aa4912fdd84f5f41602590774d25ab1b96616f65f83ddea01d9c53b\"}";

static void hload(unsigned a,long id){const char *decl=a==2?load_normal:a==3?load_narrowband:load_pulse;const unsigned char *raw=a==2?hr_normal:a==3?hr_narrowband:hr_pulse;hreq(id,"headroom.load",decl);memcpy(hs.in+hs.n,raw,10160);hs.n+=10160;}
static void hdone(rf_daemon_t *d,rf_core_t *core){rf_daemon_destroy(d);rf_core_destroy(core);}
static void test_vectors(void){char sha[65];rf_sha256_hex("",0,sha);check("SHA256 empty",!strcmp(sha,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));rf_sha256_hex("abc",3,sha);check("SHA256 abc",!strcmp(sha,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));rf_sha256_hex(hr_normal,10160,sha);check("SHA256 frozen 159blocks",!strcmp(sha,rf_headroom_payload_sha(2)));}
static void test_full(void){rf_core_t *core;rf_daemon_t *d=hfixture(&core);unsigned a;hreq(3,"headroom.begin",BEGIN);
 for(a=1;a<=5;a++){long frame=10+a*3;if(a>=2&&a<=4)hload(a,frame-1);hcap(a,frame);hack(a,frame,frame+1);}
 hcap(6,60);hreq(61,"control.arm","{\"token\":\"research-token\"}");hreq(62,"headroom.begin",BEGIN);hreq(63,"protocol.close","{}");
/* The script ends with protocol.close, so the session's clean end is serve()==1:
 * 1 is the client-requested close, 0 is a bare EOF, -1 is a read error. */
 check("full five frames connection",rf_daemon_serve(d,&hs.t)==1);check("five accepts complete",hcontains("\"completed\":true,\"attempts\":5"));
 check("full study source payload validated",hcontains("\"loaded\":true"));check("actualRF dual and honest simulation",hcontains("\"tx2_attenuation_mdB\":46000")&&hcontains("\"hardware_verified\":false"));
 check("both OFF original IQ frames",hcontains("\"scenario\":\"off-before\"")&&hcontains("\"scenario\":\"off-after\""));check("sixth refused",hcontains("\"id\":60,\"ok\":false"));check("same process cannot rearm study",hcontains("\"id\":62,\"ok\":false"));
 check("final both paths clear",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));hdone(d,core);}
static void test_failures(void){int kind;for(kind=0;kind<10;kind++){rf_core_t *core;rf_daemon_t *d=hfixture(&core);hreq(3,"headroom.begin",BEGIN);
 if(kind==0)hcap(2,4); /* order */
 if(kind==1){hcap(1,4);hcap(1,5);} /* no accept */
 if(kind==2){hcap(1,4);hack(1,99,5);} /* wrong frame */
 if(kind==3)hreq(4,"rf.configure","{}"); /* mixed control */
 if(kind==4){hs.frame_fail=1;hcap(1,4);} /* send failure */
 if(kind==5){hreq(4,"control.disarm","{}");hcap(1,5);}
 if(kind==6){hcap(1,4);hreq(5,"headroom.accept","{" COMMON ",\"attempt\":1,\"frame_id\":4,\"timestamp_ns\":\"1234567890123\",\"accepted\":false}");}
 if(kind==7)hreq(4,"headroom.abort","{" COMMON "}");
 if(kind==8){hcap(1,4);hack(1,4,5);hload(2,6);hs.in[hs.n-1]^=1;}
 /* A capture under the WRONG scenario name: the fixed order is positional, so the
  * name has to match the position, not merely be a real one.  Attempt 1 needs no
  * load, so nothing refuses ahead of the guard under test. */
 if(kind==9)hreq(4,"headroom.capture","{" COMMON ",\"attempt\":1,\"scenario\":\"normal\",\"samples\":4096,\"ratio\":0,\"settle_wait_us\":0}");
 hreq(99,"protocol.close","{}");(void)rf_daemon_serve(d,&hs.t);
 check("every failure closes TX/RX before returning",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
 check("failure cannot capture next frame",!hcontains("\"completed\":true"));
 /* The corrupted-payload case has to name its own refusal: the shared checks above
  * pass even if the load is ACCEPTED, because nothing after it would run. */
 if(kind==8)check("a corrupted research payload is refused by digest",hcontains("research payload incomplete or SHA mismatch"));
 if(kind==9)check("a capture whose scenario is not the fixed order is refused",hcontains("\"id\":4,\"ok\":false"));
 hdone(d,core);}}
/* OFF is an ordinary RX capture with the source OFF, so the research path must not
 * write the TX source register at all.  fake_src_cfg_writes counts every write to
 * it; the ON control below proves the counter can move, so a zero is a result and
 * not a dead probe. */
static void test_off_never_touches_the_source(void){rf_core_t *core;rf_daemon_t *d=hfixture(&core);int before;
 hreq(3,"headroom.begin",BEGIN);before=fake_src_sel_writes;hcap(1,4);hack(1,4,5);hreq(99,"protocol.close","{}");(void)rf_daemon_serve(d,&hs.t);
 check("an OFF attempt selects no TX source",fake_src_sel_writes==before);
 hdone(d,core);}
static void test_on_attempt_does_touch_the_source(void){rf_core_t *core;rf_daemon_t *d=hfixture(&core);int before;
 hreq(3,"headroom.begin",BEGIN);hcap(1,4);hack(1,4,5);before=fake_src_sel_writes;hload(2,13);hcap(2,14);hack(2,14,15);hreq(99,"protocol.close","{}");(void)rf_daemon_serve(d,&hs.t);
 check("an ON attempt does select the TX source",fake_src_sel_writes>before);
 hdone(d,core);}

/* Both TX attenuation getters are read before the source is armed.  A getter that
 * FAILS and a getter that DISAGREES are different faults and both must refuse the
 * attempt and end the study.  Injected at the BEGIN reply, because the
 * initialization memsets the vendor model it is observed through. */
static void test_getter_faults_seal_the_study(void){int kind;for(kind=1;kind<=3;kind++){rf_core_t *core;rf_daemon_t *d=hfixture(&core);
 getter_fault_arm=kind;
 hreq(3,"headroom.begin",BEGIN);hcap(1,4);hreq(5,"headroom.begin",BEGIN);hreq(99,"protocol.close","{}");(void)rf_daemon_serve(d,&hs.t);
 getter_fault_arm=0;
 check(kind==1?"a failed TX2 getter refuses the capture":"a mismatched TX2 getter refuses the capture",hcontains("\"id\":3,\"ok\":true")&&hcontains("\"id\":4,\"ok\":false"));
 check("the getter fault sealed the study",hcontains("\"id\":5,\"ok\":false"));
 check("the refused capture left TX and RX off",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
 hdone(d,core);}}

/* A railed frame is a RESULT: it must be reported CLIPPED with its count, still be
 * delivered WITH its original IQ, and end the study -- never silently accepted and
 * never retried. */
static void test_rails_keep_the_original_iq(void){rf_core_t *core;rf_daemon_t *d=hfixture(&core);unsigned i;
 for(i=0;i<4096u;i++)fake_pl_write(RF_BRAM_RX_BASE+i*4u,0xF800F800u); /* I=Q=-2048 */
 hreq(3,"headroom.begin",BEGIN);hcap(1,4);hcap(1,5);hreq(99,"protocol.close","{}");(void)rf_daemon_serve(d,&hs.t);
 check("a railed frame is reported CLIPPED with its rail count",hcontains("\"terminal\":\"CLIPPED\"")&&hcontains("\"rail_count\":8192"));
 check("the railed frame still carries its original IQ",hcontains("\"type\":\"research_iq_frame\"")&&hs.outn>16384u);
 check("a railed capture seals the study",hcontains("\"id\":5,\"ok\":false"));
 check("the railed study left TX and RX off",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
 hdone(d,core);}

/* A connection that simply ENDS mid-study must bring the part to rest and seal the
 * study for the life of the process, exactly as an explicit abort does. */
static void test_disconnect_mid_study_seals_it(void){rf_core_t *core;rf_daemon_t *d=hfixture(&core);size_t mark;
 hreq(3,"headroom.begin",BEGIN);hcap(1,4); /* no protocol.close: the connection just ends */
 check("a connection that ends mid-study is a clean EOF",rf_daemon_serve(d,&hs.t)==0);
 check("the abandoned study left TX and RX off",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
 mark=hs.outn;
 hreq(1,"protocol.hello","{\"major\":1,\"minor\":6}");hreq(2,"control.arm","{\"token\":\"research-token\"}");hreq(3,"headroom.begin",BEGIN);hreq(99,"protocol.close","{}");
 (void)rf_daemon_serve(d,&hs.t);
 check("the same process cannot begin a second study after a disconnect",hcontains_from(mark,"\"id\":3,\"ok\":false"));
 hdone(d,core);}

/* The physical loopback approves EXACTLY the one domain value -- 46000, the same number
 * pynq_overlay/sw/rf_teaching_profile.py ATTENUATION_MDB carries -- and refuses every
 * other value BY NAME, and a refusal must still clear an approval an earlier request
 * earned.  2026-10-18: this constant was 40000 while the domain was already 46000, and
 * this test asserted that mismatch as if it were the contract.  The refusal half is NOT
 * gone: the pre-migration value is now the one that has to be refused, with the message
 * naming the value the gate actually enforces. */
static void test_physical_gate_is_exactly_the_domain_value(void){rf_session_t s;rf_state_error_t e;rf_loopback_request_t r;
 rf_state_init(&s);e.code=0;e.message=NULL;
 r.attenuation_mdB=(long)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB;r.cable_confirmed=true;
 check("the loopback value is the 46 dB domain, not a number of its own",RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB==46000);
 check("the physical loopback approves exactly the domain value",rf_state_check_physical_loopback(&s,&r,&e)==0&&s.physical_approved);
 r.attenuation_mdB=40000;e.message=NULL;
 check("the pre-migration 40000 is refused, and the message names 46000",rf_state_check_physical_loopback(&s,&r,&e)!=0&&!s.physical_approved&&e.code==RF_PROTO_E_INVALID_ARGUMENT&&e.message!=NULL&&strstr(e.message,"attenuation_mdB 46000")!=NULL&&strstr(e.message,"40000")==NULL);
 r.attenuation_mdB=46001;e.message=NULL;
 check("a neighbouring value is refused by the same named message",rf_state_check_physical_loopback(&s,&r,&e)!=0&&!s.physical_approved&&e.message!=NULL&&strstr(e.message,"attenuation_mdB 46000")!=NULL);
 r.attenuation_mdB=(long)RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB;r.cable_confirmed=false;
 check("a missing cable refuses and clears the earlier approval",rf_state_check_physical_loopback(&s,&r,&e)!=0&&!s.physical_approved);}

static void test_review_blockers(void)
{
    int kind;
    for (kind=0; kind<5; ++kind) {
        rf_core_t *core;
        rf_daemon_t *d=hfixture(&core);
        if (kind==0) {
            rf_init_request_t init={true,40000,80000000,60000000,0};
            check("prior initialization fixture",rf_core_init(core,&init,NULL)==0);
            check("prior shutdown fixture",rf_core_safe_shutdown(core,NULL)==0);
        }
        if (kind==1) {
            fake_pl_write(RF_CTRL_A(RF_REG_SRC_CFG),RF_SRC_SEL);
            fake_pl_write(RF_CTRL_A(RF_REG_CAP_CFG),RF_CAP_ARM);
        }
        hreq(3,"headroom.begin",BEGIN);
        if(kind==2)hreq(4,"headroom.capture","{" COMMON ",\"attempt\":1,\"scenario\":\"off-before\",\"samples\":4096,\"ratio\":0,\"settle_wait_us\":0,\"observe_timing\":true}");
        if(kind==3)hreq(4,"does.not.exist","{}");
        if(kind==4) {
            const char *bad="malformed\n";
            memcpy(hs.in+hs.n,bad,strlen(bad));hs.n+=strlen(bad);
        }
        if(kind>=2)hcap(1,5);
        hreq(99,"protocol.close","{}");
        (void)rf_daemon_serve(d,&hs.t);
        if(kind<2)check("unsafe/prior-init BEGIN refused",hcontains("\"id\":3,\"ok\":false"));
        if(kind==1)check("unsafe preflight refuses before any initialization",!rf_core_initialization_attempted(core)&&hcontains("HEADROOM idle readback failed"));
        if(kind>=2)check("generic rejection seals before a valid retry",hcontains("\"id\":5,\"ok\":false")&&!hcontains("research_iq_frame"));
        hdone(d,core);
    }
}
static void hreplace(char *out,size_t capacity,const char *in,const char *old,const char *replacement)
{
    const char *at=strstr(in,old);
    if(!at){fprintf(stderr,"invalid mutation fixture %s\n",old);exit(2);}
    snprintf(out,capacity,"%.*s%s%s",(int)(at-in),in,replacement,at+strlen(old));
}
static void test_begin_validation_and_failed_init(void)
{
    static const char *before[]={"HEADROOM-1",SID,"true","80000000","60000000","30000000","46000","\"phy_mode_1r1t\":0","\"idelay_en\":1","\"idelay_tap\":29","\"study\"","\"session_id\"","\"cable_confirmed\""};
    static const char *after[]={"WRONG", "g123456789abcdef0123456789abcdef","false","79000000","59000000","29000000","40000","\"phy_mode_1r1t\":1","\"idelay_en\":0","\"idelay_tap\":28","\"absent\"","\"absent\"","\"absent\""};
    unsigned i;
    for(i=0;i<sizeof before/sizeof before[0];++i) {
        rf_core_t *core;rf_daemon_t *d=hfixture(&core);char args[1024];
        hreplace(args,sizeof args,BEGIN,before[i],after[i]);hreq(3,"headroom.begin",args);
        hreq(4,"control.arm","{\"token\":\"research-token\"}");hreq(5,"headroom.begin",BEGIN);hreq(99,"protocol.close","{}");
        (void)rf_daemon_serve(d,&hs.t);
        check("bad BEGIN refused without initialization",hcontains("\"id\":3,\"ok\":false")&&!hcontains("\"initializations\":1")&&!rf_core_initialization_attempted(core));
        check("bad BEGIN cannot be retried after rearm",hcontains("\"id\":5,\"ok\":false"));hdone(d,core);
    }
    {
        rf_core_t *core;rf_daemon_t *d=hfixture(&core);
        fake_pl_set_status_window_stuck(1);
        hreq(3,"headroom.begin",BEGIN);hreq(4,"control.arm","{\"token\":\"research-token\"}");hreq(5,"headroom.begin",BEGIN);hreq(99,"protocol.close","{}");
        (void)rf_daemon_serve(d,&hs.t);
        check("failed actual init records lifetime attempt",rf_core_initialization_attempted(core)&&hcontains("\"id\":3,\"ok\":false"));
        check("failed init cannot reinitialize",hcontains("\"id\":5,\"ok\":false"));hdone(d,core);
    }
}
static void test_declaration_fields(void)
{
    static const char *fields[]={"a","ps","sy","max","prbs","pa","pp","ph","start","width","fs","seed","rs","ta","bin","fa","fr","tp","samples","bytes","attempt"};
    unsigned i;char old[64],args[1024];
    for(i=0;i<sizeof fields/sizeof fields[0];i++) {
        snprintf(old,sizeof old,"\"%s\":",fields[i]);
        hreplace(args,sizeof args,load_normal,old,"\"absent\":");
        check("all fixed declaration numeric fields required",rf_headroom_load_declaration(args,2)!=0);
    }
    {
        static const char *strings[]={"v","truth","shape","formula","quant","format","direction","source_sha256","decl_sha256","wrap","scenario"};
        for(i=0;i<sizeof strings/sizeof strings[0];i++) {
            snprintf(old,sizeof old,"\"%s\":",strings[i]);hreplace(args,sizeof args,load_normal,old,"\"absent\":");
            check("all fixed declaration textual fields required",rf_headroom_load_declaration(args,2)!=0);
        }
    }
}
static void test_failed_frame_evidence(void)
{
    int kind;
    for(kind=1;kind<=4;kind++) {
        rf_core_t *core;rf_daemon_t *d=hfixture(&core);
        clock_fault_mode=kind;clock_fault_enabled=clock_fault_calls=0;
        hreq(3,"headroom.begin",BEGIN);hcap(1,4);hcap(1,5);
        /* Check the response before protocol.close can do any cleanup. */
        (void)rf_daemon_serve(d,&hs.t);
        check("post/cleanup/clock failure retains original IQ",hcontains("research_iq_frame")&&hcontains("\"terminal\":\"ERROR\"")&&hs.outn>16384u);
        check("failed frame retains native error and named timing",hcontains("\"native_error\":{")&&hcontains("API_MONOTONIC_NOT_SAMPLE_EDGES")&&hcontains("\"timing_order\":[\"source_begin\""));
        check("failed frame forbids retry on still-open connection",hcontains("\"id\":5,\"ok\":false"));
        if(kind==1)check("post getter error identified",hcontains("\"stage\":\"POST_READBACK\"")&&hcontains("\"post_readback_code\":-3"));
        if(kind==2)check("failed cleanup retains unconfirmed shutdown",hcontains("\"stage\":\"RELEASE\"")&&hcontains("\"confirmed\":false,\"release_code\":-3,\"shutdown_code\":-3"));
        if(kind>=3)check("zero/reversed clock identified",hcontains("\"stage\":\"CLOCK\"")&&hcontains("\"clock_code\":-3"));
        clock_fault_mode=clock_fault_enabled=0;hdone(d,core);
    }
}
static void test_timeout_and_framing_failures(void)
{
    rf_core_t *core;rf_daemon_t *d=hfixture(&core);
    clock_fault_mode=5;clock_fault_enabled=clock_fault_calls=0;
    hreq(3,"headroom.begin",BEGIN);hcap(1,4);hcap(1,5);
    (void)rf_daemon_serve(d,&hs.t);
    check("capture timeout faults and reports unavailable IQ",hcontains("\"capture_code\":-4")&&hcontains("\"iq_available\":false")&&!hcontains("research_iq_frame"));
    check("FAULT capture retains confirmed closing result",hcontains("\"close_confirmed\":true")&&hcontains("\"id\":5,\"ok\":false"));
    clock_fault_mode=clock_fault_enabled=0;hdone(d,core);
    d=hfixture(&core);
    hreq(3,"headroom.begin",BEGIN);hcap(1,4);hack(1,4,5);hreq(6,"headroom.load",load_normal);
    memcpy(hs.in+hs.n,hr_normal,10000);hs.n+=10000;
    (void)rf_daemon_serve(d,&hs.t);
    check("truncated payload is refused before BRAM",hcontains("research payload incomplete or SHA mismatch")&&fake_pl_read(RF_BRAM_TX_BASE)==0);
    check("truncated payload closes the source and capture",!(fake_pl_read(RF_CTRL_A(RF_REG_SRC_CFG))&RF_SRC_SEL)&&!(fake_pl_read(RF_CTRL_A(RF_REG_CAP_CFG))&RF_CAP_ARM));
    hdone(d,core);
    d=hfixture(&core);
    hreq(3,"headroom.begin",BEGIN);hcap(1,4);hack(1,4,5);
    {char args[1024];hreplace(args,sizeof args,load_normal,"\"a\":60","\"a\":61");hreq(6,"headroom.load",args);}
    /* Bad declared framing terminates the connection without consuming a guessed
     * payload length. No BRAM write can precede declaration validation. */
    (void)rf_daemon_serve(d,&hs.t);
    check("wrong typed declaration refuses before BRAM",hcontains("research declaration/order refused before BRAM")&&fake_pl_read(RF_BRAM_TX_BASE)==0);
    hdone(d,core);
}
static void test_ordinary_init_history_and_live_capability(void)
{
    rf_core_t *core;rf_daemon_t *d=hfixture(&core);size_t mark;
    hreq(3,"device.info","{}");
    hreq(4,"rf.init","{\"lo_hz\":80000000,\"sample_rate_hz\":60000000,\"attenuation_mdB\":40000,\"phy_mode_1r1t\":0}");
    hreq(5,"tx.stop","{}");hreq(6,"control.disarm","{}");
    (void)rf_daemon_serve(d,&hs.t);
    check("ordinary rf.init fixture accepted",hcontains("\"id\":4,\"ok\":true"));
    check("live device.info capability is additive",hcontains("\"headroom\":{\"study\":\"HEADROOM-1\",\"protocol_minor\":6")&&hcontains("\"attempt_budget\":5"));
    check("ordinary initialized core shutdown",rf_core_safe_shutdown(core,NULL)==RF_ERR_OK);
    mark=hs.outn;
    hreq(1,"protocol.hello","{\"major\":1,\"minor\":6}");hreq(2,"device.info","{}");
    hreq(3,"control.arm","{\"token\":\"research-token\"}");hreq(4,"headroom.begin",BEGIN);
    (void)rf_daemon_serve(d,&hs.t);
    check("headroom capability is visible while unarmed",hcontains_from(mark,"\"control_ops\":[\"control.arm\"],\"headroom\""));
    check("ordinary init-stop-disarm-rearm cannot begin research",hcontains_from(mark,"\"id\":4,\"ok\":false")&&hcontains_from(mark,"prior initialization attempt"));
    hdone(d,core);
}
#ifndef RF_HEADROOM_LIBRARY
int main(void){test_vectors();test_full();test_failures();test_off_never_touches_the_source();test_on_attempt_does_touch_the_source();test_getter_faults_seal_the_study();test_rails_keep_the_original_iq();test_disconnect_mid_study_seals_it();test_physical_gate_is_exactly_the_domain_value();test_review_blockers();test_begin_validation_and_failed_init();test_declaration_fields();test_failed_frame_evidence();test_timeout_and_framing_failures();test_ordinary_init_history_and_live_capability();printf("HEADROOM %u passed %u failed\n",pass_count,fail_count);return fail_count?1:0;}
#endif
