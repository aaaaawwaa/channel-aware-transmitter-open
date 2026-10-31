connect
targets -set -nocase -filter {name =~ "ARM Cortex-A9 MPCore #0*"}
rst -processor
source <LOCAL_PATH>
ps7_init
ps7_post_config
dow <LOCAL_PATH>
con
disconnect
