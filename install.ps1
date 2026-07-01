param([switch]$DryRun, [switch]$Repair)
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$BinDir = Join-Path $AppHome "bin"
$ExistingCmd = Join-Path $BinDir "neo-localmcp.cmd"

function Get-SourceVersion([string]$Root) {
    $initFile = Join-Path $Root "neo_localmcp\__init__.py"
    $content = Get-Content -Raw -LiteralPath $initFile
    if ($content -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    throw "Could not determine neo-localmcp version from $initFile"
}

# One venv per version, not one per install run (1.0.8+). Since install.ps1 can now
# gracefully stop a running server before touching its files (1.0.7's P7b/P7c), the
# old "always create a new timestamped dir, never touch an existing one" scheme is
# no longer needed for safety -- it was only there because an in-place upgrade
# risked a locked-file failure against a live server, and that's now handled
# directly instead of worked around by never reusing a directory.
$Version = Get-SourceVersion -Root $RootDir
$VenvDir = Join-Path $AppHome ".venv-nlm-v$Version"

if ($DryRun) {
    Write-Host "Would install or repair neo-localmcp $Version from $RootDir"
    Write-Host "Application home: $AppHome"
    Write-Host "Target virtual environment: $VenvDir"
    exit 0
}

function Get-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = "py"; Args = @("-3") } }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Exe = "python"; Args = @() } }
    throw "Python 3.10+ is required. Install Python, then rerun this script."
}

function Stop-ServersUnder([string]$TargetPath, [string]$Cmd) {
    # Best-effort graceful stop (1.0.7+: neo-localmcp stop). A pre-1.0.7 install has
    # no 'stop' subcommand and no registry entries to find, so this is a no-op there
    # -- servers from that version simply won't be reachable this way, which is why
    # removal below tolerates a locked directory instead of failing the install.
    if (-not (Test-Path $Cmd)) { return }
    try {
        $out = & $Cmd stop --match-executable $TargetPath --timeout 10 2>&1
        if ($out) { $out | ForEach-Object { Write-Host "  $_" } }
    } catch {
        Write-Host "  (graceful stop attempt skipped: $($_.Exception.Message))"
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

function Invoke-BoundedExe([string]$Exe, [string[]]$ExeArgs, [int]$TimeoutSeconds) {
    # Any external process launched here (ollama.exe) is untrusted with respect to
    # timing -- it must never be allowed to stall the install indefinitely (same
    # "Ollama failures never block deterministic behavior" rule ollama_client.py
    # applies to context retrieval, applied here to the installer itself). A plain
    # `& $exe` has no timeout, so run it as a job and cap the wait explicitly.
    $job = Start-Job -ScriptBlock {
        param($JobExe, $JobArgs)
        $out = & $JobExe @JobArgs 2>&1
        [pscustomobject]@{ Output = $out; ExitCode = $LASTEXITCODE }
    } -ArgumentList $Exe, $ExeArgs
    $completed = Wait-Job $job -Timeout $TimeoutSeconds
    if (-not $completed) {
        Stop-Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job $job -Force -ErrorAction SilentlyContinue | Out-Null
        return @{ Completed = $false; ExitCode = -1; Output = @() }
    }
    $result = Receive-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue | Out-Null
    if (-not $result) {
        return @{ Completed = $true; ExitCode = -1; Output = @() }
    }
    return @{ Completed = $true; ExitCode = $result.ExitCode; Output = @($result.Output) }
}

function Test-OllamaSetup([string]$VenvPython) {
    # Ollama is optional preprocessing, never required (see CLAUDE.md). This only
    # reports presence/readiness/model coverage -- it never fails the install and
    # never auto-pulls a missing model, matching ollama_client.py's own rule. Every
    # external call below is time-bounded so a stuck/unresponsive ollama.exe can
    # never hang the installer -- it just gets skipped and reported as such.
    Write-Host ""
    Write-Host "Checking Ollama (optional; deterministic retrieval works without it)..."
    try {
        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollamaCmd) {
            Write-Host "  ollama not found on PATH -- skipping. Install it later from https://ollama.com if you want ranking/summarization."
            return
        }
        $list = Invoke-BoundedExe -Exe $ollamaCmd.Source -ExeArgs @("list") -TimeoutSeconds 10
        if (-not $list.Completed) {
            Write-Host "  'ollama list' did not respond within 10s -- skipping model check."
            return
        }
        if ($list.ExitCode -ne 0) {
            Write-Host "  ollama found but not responding -- starting 'ollama serve' headless (same as neo-localmcp's own auto-start)..."
            try {
                Start-Process -FilePath $ollamaCmd.Source -ArgumentList "serve" -WindowStyle Hidden `
                    -RedirectStandardOutput "NUL" -RedirectStandardError "NUL" | Out-Null
            } catch {
                Write-Host "  Could not start ollama serve: $($_.Exception.Message)"
                return
            }
            Start-Sleep -Seconds 3
            $list = Invoke-BoundedExe -Exe $ollamaCmd.Source -ExeArgs @("list") -TimeoutSeconds 10
            if (-not $list.Completed -or $list.ExitCode -ne 0) {
                Write-Host "  Ollama still not responding after starting it -- skipping model check."
                return
            }
        }
        $configCheck = Invoke-BoundedExe -Exe $VenvPython -ExeArgs @(
            "-c", "import json; from neo_localmcp.config import load_config; c = load_config()['ollama']; print(json.dumps({'fast_model': c['fast_model'], 'summary_model': c['summary_model']}))"
        ) -TimeoutSeconds 15
        if (-not $configCheck.Completed -or $configCheck.ExitCode -ne 0 -or -not $configCheck.Output) {
            Write-Host "  Could not read configured model names -- skipping model check."
            return
        }
        $configuredModels = ($configCheck.Output -join "") | ConvertFrom-Json
        $installedNames = @($list.Output | Select-Object -Skip 1 | ForEach-Object { ("$_" -split '\s+')[0] } | Where-Object { $_ })
        foreach ($role in @("fast_model", "summary_model")) {
            $model = $configuredModels.$role
            if ($installedNames -contains $model) {
                Write-Host "  [found]   $role -> $model"
            } else {
                Write-Host "  [missing] $role -> $model (not auto-installed; run 'ollama pull $model' if you want it)"
            }
        }
    } catch {
        Write-Host "  Ollama check skipped due to an unexpected error: $($_.Exception.Message)"
    }
}

function Test-VenvIntact([string]$Venv) {
    # Fast sanity check that a present venv is actually usable, not a leftover from
    # an interrupted previous run.
    (Test-Path (Join-Path $Venv "Scripts\python.exe")) -and (Test-Path (Join-Path $Venv "Scripts\neo-localmcp.exe"))
}

New-Item -ItemType Directory -Force -Path $AppHome | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# Ask any running server(s) to exit gracefully before touching venv files: any
# other version's venv (about to be removed below) and, if reinstalling this same
# version, this one too -- pip can't safely rewrite files a live server has open.
Write-Host "Requesting graceful stop of any running neo-localmcp server(s) under $AppHome ..."
Stop-ServersUnder -TargetPath $AppHome -Cmd $ExistingCmd

# Exactly one venv exists at a time -- remove every other version's venv, no
# side-by-side retention. A directory that's still locked (almost always a
# pre-1.0.7 server the graceful-stop pass above couldn't reach) is skipped with a
# warning rather than failing the whole install. Also sweep the legacy pre-1.0.8
# "venvs\<timestamp>" side-by-side layout and old singular "venv" dir wholesale, so
# an upgrade from an older install actually reclaims that space instead of leaving
# it behind forever alongside the new single versioned venv.
$OtherVenvPaths = @(Get-ChildItem -Directory -Path $AppHome -Filter ".venv-nlm-v*" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne $VenvDir } | ForEach-Object { $_.FullName })
$OtherVenvPaths += Join-Path $AppHome "venvs"
$OtherVenvPaths += Join-Path $AppHome "venv"
foreach ($old in $OtherVenvPaths) {
    if (-not (Test-Path $old)) { continue }
    try {
        Remove-ItemWithRetry -Target $old
        Write-Host "  Removed old venv: $old"
    } catch {
        Write-Host "  Could not remove old venv $old : still locked (likely a pre-1.0.7 server without a stop watcher). Safe to remove manually once that process exits."
    }
}

if ((Test-Path $VenvDir) -and (Test-VenvIntact $VenvDir) -and -not $Repair) {
    # Same version already has a working venv -- this is the common case across
    # repeated installs/CI runs and should be a fast no-op, not a full rebuild.
    # Pass -Repair to force a rebuild of the same version (e.g. iterating on the
    # installer itself without bumping the package version).
    Write-Host "neo-localmcp $Version is already installed at $VenvDir -- skipping venv rebuild."
} else {
    if (Test-Path $VenvDir) {
        Write-Host "Rebuilding $VenvDir ($(if ($Repair) { '-Repair requested' } else { 'incomplete/broken venv found' }))..."
        Remove-ItemWithRetry -Target $VenvDir
    }
    $Py = Get-PythonCommand
    & $Py.Exe @($Py.Args) -m venv $VenvDir
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install --upgrade --force-reinstall $RootDir
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# The packaged .mcpb is versioned in packages/claude-desktop/ (1.0.9+), but the
# local copy stays a fixed filename so setup.ps1's Desktop-extension instructions
# can point at a stable path without the user hunting for a version suffix.
$McpbSource = Join-Path $RootDir "packages\claude-desktop\neo-localmcp-v$Version.mcpb"
if (Test-Path $McpbSource) { Copy-Item -Force $McpbSource (Join-Path $AppHome "neo-localmcp.mcpb") }

$Cmd = Join-Path $BinDir "neo-localmcp.cmd"
$CmdContent = "@echo off`r`n`"$VenvPython`" -m neo_localmcp.cli %*`r`n"
Set-Content -Path $Cmd -Value $CmdContent -Encoding ASCII

$ServerCmd = Join-Path $BinDir "neo-localmcp-server.cmd"
$ServerCmdContent = "@echo off`r`n`"$VenvPython`" -m neo_localmcp.server`r`n"
Set-Content -Path $ServerCmd -Value $ServerCmdContent -Encoding ASCII
Set-Content -Path (Join-Path $AppHome "current-venv.txt") -Value $VenvDir -Encoding UTF8

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
if (($UserPath -split ";") -notcontains $BinDir) {
    $NewPath = if ($UserPath.Trim().Length -gt 0) { "$BinDir;$UserPath" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$BinDir;$env:Path"
}

& $Cmd init
try { & $Cmd doctor } catch { Write-Host "Doctor reported an issue; install still completed." }
Test-OllamaSetup -VenvPython $VenvPython

Write-Host ""
Write-Host "Installed neo-localmcp."
Write-Host "Command: $Cmd"
Write-Host "Open a new terminal if 'neo-localmcp' is not immediately on PATH."
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Open a new terminal if PATH is not refreshed."
Write-Host "  2. Run setup once from anywhere:"
Write-Host "       neo-localmcp setup --client all"
Write-Host "  3. Check paths anytime with: neo-localmcp where"
Write-Host ""
Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "  IMPORTANT: neo-localmcp does nothing for a repo until it's indexed." -ForegroundColor Cyan
Write-Host "  For every repo you want it to help with, run:" -ForegroundColor Cyan
Write-Host "       cd C:\Path\To\YourRepo" -ForegroundColor Cyan
Write-Host "       neo-localmcp index" -ForegroundColor Cyan
Write-Host "  ...or ask your AI agent to do it (see docs/AGENT_INTEGRATION.md)." -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan
