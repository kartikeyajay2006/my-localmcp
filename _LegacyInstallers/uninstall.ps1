# Uninstall neo-localmcp's local state under ~/.neo-localmcp.
#
# Default (no switches) behavior is unchanged from prior releases: remove the venv,
# bin launchers, PATH entry, the local .mcpb copy and current-venv.txt, while
# PRESERVING config.yaml, the repo index/memory database, and the servers/ registry.
# 1.0.9 (P9f) adds granular switches so setup.ps1's uninstall wizard (and power
# users) can keep/delete each category independently. -RemoveData is kept as a
# back-compat alias for -RemoveConfig -RemoveDatabase (+ the ollama supervisor
# runtime file), exactly matching what it removed before.
param(
    [switch]$RemoveData,
    [switch]$KeepVenv,
    [switch]$KeepMcpb,
    [switch]$RemoveConfig,
    [switch]$RemoveDatabase,
    [switch]$RemoveServers
)
$ErrorActionPreference = "Stop"
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$Resolved = [System.IO.Path]::GetFullPath($AppHome)
if ([System.IO.Path]::GetFileName($Resolved) -ne ".neo-localmcp") { throw "Refusing unexpected application directory: $Resolved" }

# -RemoveData is the pre-1.0.9 blanket "also delete config + database" flag. Keep it
# meaning exactly what it did (config, database, and the ollama supervisor file) so
# existing callers/CI are unaffected.
$removeSupervisor = $false
if ($RemoveData) {
    $RemoveConfig = $true
    $RemoveDatabase = $true
    $removeSupervisor = $true
}
if ($RemoveServers) { $removeSupervisor = $true }

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

$removed = [System.Collections.Generic.List[string]]::new()
$kept = [System.Collections.Generic.List[string]]::new()

if (-not $KeepVenv) {
    # 1.0.8+: a single ".venv-nlm-v<version>" directory (no side-by-side timestamped
    # venvs to enumerate); "venv"/"venvs" kept for pre-1.0.8 compatibility.
    $VenvTargets = @(Get-ChildItem -Directory -Path $Resolved -Filter ".venv-nlm-v*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    $VenvTargets += @("venv", "venvs", "bin") | ForEach-Object { Join-Path $Resolved $_ }
    foreach ($Target in ($VenvTargets | Select-Object -Unique)) {
        if (Test-Path $Target) {
            try {
                Remove-ItemWithRetry -Target $Target
                $removed.Add($Target)
            } catch {
                throw "Could not remove $Target -- a server process is still holding a file lock on it. If this is a pre-1.0.7 install, it predates the graceful-stop mechanism and needs to be stopped manually (Task Manager / Stop-Process) before uninstalling. Original error: $($_.Exception.Message)"
            }
        }
    }
    $CurrentVenv = Join-Path $Resolved "current-venv.txt"
    if (Test-Path $CurrentVenv) { Remove-Item -Force -LiteralPath $CurrentVenv; $removed.Add($CurrentVenv) }
} else {
    $kept.Add("venv / bin launchers")
}

if (-not $KeepMcpb) {
    $Bundle = Join-Path $Resolved "neo-localmcp.mcpb"
    if (Test-Path $Bundle) { Remove-Item -Force -LiteralPath $Bundle; $removed.Add($Bundle) }
} else {
    $kept.Add("neo-localmcp.mcpb")
}

if ($RemoveConfig) {
    $Target = Join-Path $Resolved "config.yaml"
    if (Test-Path $Target) { Remove-Item -Force -LiteralPath $Target; $removed.Add($Target) }
} else {
    $kept.Add("config.yaml")
}

if ($RemoveDatabase) {
    foreach ($name in @("repo-context.sqlite", "repo-context.sqlite-wal", "repo-context.sqlite-shm")) {
        $Target = Join-Path $Resolved $name
        if (Test-Path $Target) { Remove-Item -Force -LiteralPath $Target; $removed.Add($Target) }
    }
} else {
    $kept.Add("repo-context.sqlite (shared repo index/memory database)")
}

if ($RemoveServers) {
    $ServersDir = Join-Path $Resolved "servers"
    if (Test-Path $ServersDir) { Remove-ItemWithRetry -Target $ServersDir; $removed.Add($ServersDir) }
} else {
    $kept.Add("servers/ registry directory")
}

if ($removeSupervisor) {
    $Target = Join-Path $Resolved "ollama-supervisor.json"
    if (Test-Path $Target) { Remove-Item -Force -LiteralPath $Target; $removed.Add($Target) }
}

if ($removed.Count -gt 0) {
    Write-Host "Removed:"
    foreach ($r in $removed) { Write-Host "  $r" }
} else {
    Write-Host "Removed nothing (everything selected was already absent or kept)."
}
if ($kept.Count -gt 0) {
    Write-Host "Preserved:"
    foreach ($k in $kept) { Write-Host "  $k" }
}
