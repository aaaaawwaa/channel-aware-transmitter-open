# xsct: recover a poisoned/wedged board by resetting via the DAP target,
# selected by position (target 1) because its name contains parentheses
# that break -filter matching.
connect
after 2000
catch {targets 1} sel
puts "SELECTED=$sel"
catch {current_target} cur
puts "CUR_BEFORE=$cur"
catch {rst -system} r1
puts "RST_SYSTEM_RET=$r1"
after 8000
puts "POST_TARGETS_BEGIN"
foreach t [targets] { puts "T $t" }
puts "POST_TARGETS_END"
exit
