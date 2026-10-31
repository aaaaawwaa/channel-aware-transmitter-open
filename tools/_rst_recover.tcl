connect
after 2000
puts "TARGETS:"
puts [targets]
targets -set -filter {name =~ "*APU*"}
puts "SELECTED: [targets -target-properties]"
rst -system
after 3000
con
after 1000
puts "RESET-DONE"
exit
