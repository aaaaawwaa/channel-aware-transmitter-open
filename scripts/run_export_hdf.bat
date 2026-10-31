@echo off
cd /d <LOCAL_PATH>
echo ===== START export_hdf =====
"<LOCAL_PATH>" -mode batch -source scripts/export_hdf.tcl -tclargs <LOCAL_PATH>
echo ===== EXIT CODE: %ERRORLEVEL% =====
