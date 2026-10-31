@echo off
cd /d <LOCAL_PATH>
echo ===== START create_bd =====
"<LOCAL_PATH>" -mode batch -source scripts/create_bd.tcl -tclargs <LOCAL_PATH>
echo ===== EXIT CODE: %ERRORLEVEL% =====
