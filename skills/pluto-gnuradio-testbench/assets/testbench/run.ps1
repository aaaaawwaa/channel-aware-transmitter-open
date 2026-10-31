param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RunArgs)
$env:PATH = "<LOCAL_PATH>;<LOCAL_PATH>;" + $env:PATH
$env:PYTHONDONTWRITEBYTECODE = '1'
& <LOCAL_PATH> -X utf8 -B "$PSScriptRoot\radio_test.py" @RunArgs
exit $LASTEXITCODE
