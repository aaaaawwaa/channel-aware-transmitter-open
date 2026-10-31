$env:PATH = "<LOCAL_PATH>;<LOCAL_PATH>;" + $env:PATH
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:XDG_CACHE_HOME = "$PSScriptRoot\generated\cache"
& '<LOCAL_PATH>' -X utf8 -B '<LOCAL_PATH>' "$PSScriptRoot\bpsk_preview.grc"
exit $LASTEXITCODE
