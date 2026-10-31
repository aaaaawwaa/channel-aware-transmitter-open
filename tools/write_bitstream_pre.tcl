# The user BRAM is populated at runtime over AXI; no ELF is merged into it.
# Vivado 2018.3 otherwise emits a malformed optional BMM/MMI diagnostic for
# this module-reference hierarchy even though write_bitstream succeeds.
set_msg_config -id {Memdata 28-165} -suppress
set_msg_config -id {Memdata 28-122} -suppress
set_msg_config -id {Memdata 28-147} -suppress
set_msg_config -id {Memdata 28-96} -suppress
