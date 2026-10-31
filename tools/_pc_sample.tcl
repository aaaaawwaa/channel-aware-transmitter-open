connect
after 2000
targets -set -filter {name =~ "ARM Cortex-A9 MPCore #0"}
stop
after 500
puts "PC1: [rrd pc]"
con
after 3000
stop
after 500
puts "PC2: [rrd pc]"
con
after 3000
stop
after 500
puts "PC3: [rrd pc]"
puts "DONE-LEFT-STOPPED"
exit
