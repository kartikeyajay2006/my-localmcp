param([switch]$RemoveData)
$ErrorActionPreference = "Stop"
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$Resolved = [System.IO.Path]::GetFullPath($AppHome)
if ([System.IO.Path]::GetFileName($Resolved) -ne ".neo-localmcp") { throw "Refusing unexpected application directory: $Resolved" }
foreach ($name in @("venv", "venvs", "bin")) {
    $Target = Join-Path $Resolved $name
    if (Test-Path $Target) { Remove-Item -Recurse -Force -LiteralPath $Target }
}
$Bundle = Join-Path $Resolved "neo-localmcp.mcpb"
if (Test-Path $Bundle) { Remove-Item -Force -LiteralPath $Bundle }
$CurrentVenv = Join-Path $Resolved "current-venv.txt"
if (Test-Path $CurrentVenv) { Remove-Item -Force -LiteralPath $CurrentVenv }
if ($RemoveData) {
    foreach ($name in @("config.yaml", "repo-context.sqlite", "repo-context.sqlite-wal", "repo-context.sqlite-shm", "ollama-supervisor.json")) {
        $Target = Join-Path $Resolved $name
        if (Test-Path $Target) { Remove-Item -Force -LiteralPath $Target }
    }
}
Write-Host "Removed neo-localmcp runtime. Configuration and index were $($(if ($RemoveData) {'removed'} else {'preserved'}))."
