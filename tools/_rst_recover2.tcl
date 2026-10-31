connect
after 2000
targets
targets -set -filter {name =~ "xc7z020*"}
puts "RST: [catch {rst -system} err] / $err"
after 3000
puts "RST done, releasing"
con
after 1000
targets
puts "RST-DONE"
exit
