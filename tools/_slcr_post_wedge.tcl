connect
after 3000
targets -set -filter {name =~ "*APU*"}
foreach a {0xF8000170 0xF8000174 0xF8000178 0xF800017C 0xF8000180 0xF8000184 0xF8000188 0xF800018C 0xF8000240 0xF8000900 0xF800012C 0xF800016C} {
    if {[catch {mrd -force $a} r]} {
        puts "$a ERROR $r"
    } else {
        puts "$a $r"
    }
}
exit
