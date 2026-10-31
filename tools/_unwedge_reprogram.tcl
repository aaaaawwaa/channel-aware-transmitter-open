# Attempt to unwedge: reprogram PL via JTAG while CPU is stuck on a
# never-completing GPV read (Test C wedge). The PL config chain is
# independent of PS CPU. If the wedged read completes once the slave
# answers, Linux should resume and the serial console should come back.
# Serial listener is running in parallel (testc_unwedge_sniff.txt).
connect
after 2000
targets -set -filter {name =~ "xc7z020*"}
puts "=== reprogram PL (ledbtn.bit) ==="
fpga -file <LOCAL_PATH>
after 3000
puts "=== PL reprogrammed; now also write LVL_SHFTR_EN? NOT yet - observe first ==="
puts "UNWEDGE-ATTEMPT-DONE"
exit
