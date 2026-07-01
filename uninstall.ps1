param([switch]$RemoveData)
$ErrorActionPreference = "Stop"
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$Resolved = [System.IO.Path]::GetFullPath($AppHome)
if ([System.IO.Path]::GetFileName($Resolved) -ne ".neo-localmcp") { throw "Refusing unexpected application directory: $Resolved" }

# Ask any running server(s) to exit gracefully before removing the files they're
# running from. Best-effort: a pre-1.0.7 server predates the stop watcher and won't
# be reachable this way (see the note on the Remove-Item catch below).
$ExistingCmd = Join-Path $Resolved "bin\neo-localmcp.cmd"
if (Test-Path $ExistingCmd) {
    try {
        & $ExistingCmd stop --match-executable $Resolved --timeout 10 2>$null | Out-Null
    } catch {
        Write-Host "(graceful stop attempt skipped: $($_.Exception.Message))"
    }
}

function Remove-ItemWithRetry([string]$Target) {
    # Even after a process is confirmed exited, Windows can hold a DLL file lock for
    # a brief moment longer (deferred handle release / AV scan-on-close). A graceful
    # stop that genuinely succeeded can still see one transient Remove-Item failure
    # immediately afterward -- retry briefly before treating it as a real lock.
    $attempts = 5
    for ($i = 1; $i -le $attempts; $i++) {
        try {
            Remove-Item -Recurse -Force -LiteralPath $Target -ErrorAction Stop
            return
        } catch {
            if ($i -eq $attempts) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}

# 1.0.8+: a single ".venv-nlm-v<version>" directory (no side-by-side timestamped
# venvs to enumerate); "venv" is kept for pre-1.0.5 compatibility.
$VenvTargets = @(Get-ChildItem -Directory -Path $Resolved -Filter ".venv-nlm-v*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$VenvTargets += @("venv", "venvs", "bin") | ForEach-Object { Join-Path $Resolved $_ }
foreach ($Target in ($VenvTargets | Select-Object -Unique)) {
    if (Test-Path $Target) {
        try {
            Remove-ItemWithRetry -Target $Target
        } catch {
            throw "Could not remove $Target -- a server process is still holding a file lock on it. If this is a pre-1.0.7 install, it predates the graceful-stop mechanism and needs to be stopped manually (Task Manager / Stop-Process) before uninstalling. Original error: $($_.Exception.Message)"
        }
    }
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
