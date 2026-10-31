#include "rf_headroom.h"
#include <string.h>
#include <ctype.h>
static int integer(const char *a,const char *k,long want){long v;return rf_proto_arg_int(a,k,&v)==0&&v==want;}
static int string(const char *a,const char *k,const char *want){char v[128];return rf_proto_arg_str(a,k,v,sizeof v)==0&&!strcmp(v,want);}
const char *rf_headroom_scenario(unsigned a){static const char *s[]={"","off-before","normal","narrowband","pulse","off-after"};return a<=5?s[a]:"";}
const char *rf_headroom_payload_sha(unsigned a){static const char *s[]={"", "","38b3f36ae20b6fe232d68d677b9e5e00c0b246c3617d0ff27239e89d0c3ef7f6","4693355b00a98acc26851df3c86e38da49672fb7713983b29edd061627845f73","22463cf461cd2141fbd3641d28294a740a39aa985c7943bea5aeb2bfcb13bbee",""};return a<=5?s[a]:"";}
const char *rf_headroom_declaration_sha(unsigned a){static const char *s[]={"", "","788e61dc71ab5d6ffbe28f67f7797457539819920c955daff2ad96ca2d200a6b","b02d0cdd464109d9df9a3dc13eb38b0d9b0b8075b88819c96a52a48c061b780a","e65d79dc4aa4912fdd84f5f41602590774d25ab1b96616f65f83ddea01d9c53b",""};return a<=5?s[a]:"";}
int rf_headroom_identity(const char *a,const char *sid){return string(a,"study","HEADROOM-1")&&string(a,"session_id",sid);}
void rf_headroom_seal(rf_headroom_t *h){h->active=false;h->waiting=false;h->loaded=false;}
int rf_headroom_begin(rf_headroom_t *h,const char *a){char sid[33];bool cable;unsigned i;
 if(h->used||!string(a,"study","HEADROOM-1")||rf_proto_arg_str(a,"session_id",sid,sizeof sid)!=0||strlen(sid)!=32||rf_proto_arg_bool(a,"cable_confirmed",&cable)!=0||!cable)return -1;
 for(i=0;i<32;i++)if(!((sid[i]>='0'&&sid[i]<='9')||(sid[i]>='a'&&sid[i]<='f')))return -1;
 if(!integer(a,"lo_hz",80000000)||!integer(a,"sample_rate_hz",60000000)||!integer(a,"rx_bandwidth_hz",30000000)||!integer(a,"tx_bandwidth_hz",30000000)||!integer(a,"attenuation_mdB",46000)||!integer(a,"phy_mode_1r1t",0)||!integer(a,"idelay_en",1)||!integer(a,"idelay_tap",29))return -1;
 h->used=true;h->active=true;h->attempt=0;strcpy(h->session_id,sid);return 0;
}
int rf_headroom_load_declaration(const char *a,unsigned attempt){bool wrap;
 if(attempt<2||attempt>4||!string(a,"scenario",rf_headroom_scenario(attempt))||!integer(a,"attempt",attempt)||!integer(a,"samples",2540)||!integer(a,"bytes",10160)||!string(a,"format","s16_iq_le")||!string(a,"direction","tx")||rf_proto_arg_bool(a,"wrap",&wrap)!=0||!wrap)return -1;
 if(!string(a,"v","rf-controlled-scenarios-v1")||!string(a,"truth",rf_headroom_scenario(attempt))||!string(a,"shape","RECT")||!string(a,"formula","x=A*s+j")||!string(a,"quant","numpy.rint / signed12 / no global scaling"))return -1;
 if(!integer(a,"a",60)||!integer(a,"ps",2540)||!integer(a,"sy",127)||!integer(a,"max",100)||!integer(a,"prbs",1)||!integer(a,"seed",1)||!integer(a,"fs",60000000)||!integer(a,"rs",3000000)||!integer(a,"pa",attempt==4?40:0)||!integer(a,"pp",2540)||!integer(a,"ph",0)||!integer(a,"start",600)||!integer(a,"width",80)||!integer(a,"ta",attempt==3?40:0)||!integer(a,"bin",254)||!integer(a,"fa",6000000)||!integer(a,"fr",6000000)||!integer(a,"tp",0))return -1;
 return string(a,"source_sha256",rf_headroom_payload_sha(attempt))&&string(a,"decl_sha256",rf_headroom_declaration_sha(attempt))?0:-1;
}
