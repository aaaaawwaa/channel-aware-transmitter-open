# Export hardware platform for SDK (2018.3 .hdf format).
# Usage: vivado -mode batch -source scripts/export_hdf.tcl -tclargs <LOCAL_PATH>
set proj_file [lindex $argv 0]
open_project $proj_file

# Write hardware definition with .hdf extension (SDK-compatible)
set hdf_file [file join [file dirname $proj_file] host_bd.hdf]
write_hwdef -force $hdf_file
puts "HDF-EXPORTED $hdf_file"

close_project
