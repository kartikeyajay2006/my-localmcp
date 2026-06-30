$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Get-Command py -ErrorAction SilentlyContinue
if ($Py) {
    & py -3 (Join-Path $ScriptDir "cleanup_old_neo_mcp.py") @args
    exit $LASTEXITCODE
}
$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) {
    & python (Join-Path $ScriptDir "cleanup_old_neo_mcp.py") @args
    exit $LASTEXITCODE
}
throw "Python is required to run cleanup_old_neo_mcp.py"
