$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath '<LOCAL_PATH>'
$resumePrompt = Get-Content -Raw -Encoding UTF8 -LiteralPath 'docs/reviews/2026-10-23-dsh-crash-resume-task84.txt'
& '<LOCAL_PATH>' headless --json $resumePrompt
exit $LASTEXITCODE
