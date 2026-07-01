# neo-localmcp interactive setup wizard (Windows).
#
# A guided front door over install.ps1/uninstall.ps1 -- it does not reimplement
# their logic, it detects current state, asks the granular questions those
# scripts otherwise need flags for, and calls them. Power users/CI can still call
# install.ps1/uninstall.ps1 directly.
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$InstallScript = Join-Path $RootDir "install.ps1"
$UninstallScript = Join-Path $RootDir "uninstall.ps1"
$DesktopExtensionPath = Join-Path $env:APPDATA "Claude\Claude Extensions\local.mcpb.neo-localmcp.neo-localmcp"

function Get-InstalledVenv {
    if (-not (Test-Path $AppHome)) { return $null }
    Get-ChildItem -Directory -Path $AppHome -Filter ".venv-nlm-v*" -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Get-InstalledVersion([System.IO.DirectoryInfo]$Venv) {
    if (-not $Venv) { return $null }
    if ($Venv.Name -match '^\.venv-nlm-v(.+)$') { return $Matches[1] }
    return $null
}

function Get-SourceVersion {
    $initFile = Join-Path $RootDir "neo_localmcp\__init__.py"
    if (-not (Test-Path $initFile)) { return "unknown" }
    $content = Get-Content -Raw -LiteralPath $initFile
    if ($content -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    return "unknown"
}

function Show-Status {
    Write-Host ""
    Write-Host "=== neo-localmcp status ===" -ForegroundColor Cyan
    Write-Host "Application home: $AppHome"
    Write-Host "Source tree version: $(Get-SourceVersion)"

    $venv = Get-InstalledVenv
    if ($venv) {
        $version = Get-InstalledVersion $venv
        $cmd = Join-Path $AppHome "bin\neo-localmcp.cmd"
        Write-Host "CLI install: v$version at $($venv.FullName)" -ForegroundColor Green
        if (Test-Path $cmd) { Write-Host "  Launcher: $cmd" }
    } else {
        $legacyVenvs = Join-Path $AppHome "venvs"
        if (Test-Path $legacyVenvs) {
            Write-Host "CLI install: legacy pre-1.0.8 side-by-side venvs found under $legacyVenvs (will be replaced on next install)" -ForegroundColor Yellow
        } else {
            Write-Host "CLI install: not installed" -ForegroundColor DarkGray
        }
    }

    if (Test-Path $DesktopExtensionPath) {
        Write-Host "Claude Desktop extension: INSTALLED at $DesktopExtensionPath" -ForegroundColor Yellow
        Write-Host "  This wizard cannot install/remove it -- that's Claude Desktop's own UI." -ForegroundColor DarkGray
        Write-Host "  If its own Uninstall fails, use menu option 3 first." -ForegroundColor DarkGray
    } else {
        Write-Host "Claude Desktop extension: not found" -ForegroundColor DarkGray
    }

    $configPath = Join-Path $AppHome "config.yaml"
    $dbPath = Join-Path $AppHome "repo-context.sqlite"
    Write-Host "Config: $(if (Test-Path $configPath) { 'present' } else { 'absent' })"
    Write-Host "Repo index/memory database: $(if (Test-Path $dbPath) { 'present' } else { 'absent' })"
    Write-Host ""
}

function Confirm-Destructive([string]$Prompt, [string]$Phrase = "DELETE") {
    Write-Host ""
    Write-Host $Prompt -ForegroundColor Yellow
    $typed = Read-Host "Type $Phrase to confirm, or anything else to cancel"
    return $typed -ceq $Phrase
}

function Show-DesktopExtensionNotice {
    if (-not (Test-Path $DesktopExtensionPath)) { return }
    Write-Host ""
    Write-Host "Note: a Claude Desktop extension is also installed at:" -ForegroundColor Yellow
    Write-Host "  $DesktopExtensionPath"
    Write-Host "This wizard only manages the CLI install (~/.neo-localmcp) -- it never touches"
    Write-Host "the Desktop extension. To remove that too, use Claude Desktop's own"
    Write-Host "Settings > Extensions > Uninstall. If that hangs or fails, use option 3 on the"
    Write-Host "main menu first (Prepare Claude Desktop extension for uninstall) -- Claude"
    Write-Host "Desktop's own uninstall usually fails because a subprocess Claude never"
    Write-Host "directly spawned (uv's child interpreter, and its own child) is still holding"
    Write-Host "the extension's files locked; stopping that tree first fixes it without a"
    Write-Host "full PC restart."
}

function Get-ProcessTree([int]$RootProcessId, [Microsoft.Management.Infrastructure.CimInstance[]]$AllProcesses) {
    # BFS from $RootProcessId through Win32_Process's ParentProcessId links. Windows
    # process termination does not cascade to children by itself -- Claude Desktop
    # killing only the process it directly spawned (uv.exe) leaves this tree's
    # descendants alive, which is the actual root cause being worked around here.
    $found = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootProcessId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($found.Contains($current)) { continue }
        $found.Add($current)
        foreach ($child in ($AllProcesses | Where-Object { $_.ParentProcessId -eq $current })) {
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    return $found
}

function Find-DesktopExtensionProcessTree {
    $allProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $roots = $allProcs | Where-Object {
        $_.Name -eq "uv.exe" -and $_.CommandLine -and $_.CommandLine -like "*Claude Extensions\local.mcpb.neo-localmcp.neo-localmcp*"
    }
    $allPids = [System.Collections.Generic.List[int]]::new()
    foreach ($root in $roots) {
        foreach ($p in (Get-ProcessTree -RootProcessId ([int]$root.ProcessId) -AllProcesses $allProcs)) {
            if (-not $allPids.Contains($p)) { $allPids.Add($p) }
        }
    }
    return $allPids
}

function Invoke-StopDesktopExtension {
    $treePids = Find-DesktopExtensionProcessTree
    if (-not $treePids -or $treePids.Count -eq 0) {
        Write-Host "No running Claude Desktop extension process found -- nothing to stop." -ForegroundColor DarkGray
        return
    }
    Write-Host ""
    Write-Host "Found $($treePids.Count) process(es) belonging to the Claude Desktop extension's" -ForegroundColor Yellow
    Write-Host "full subprocess tree (uv.exe plus its descendants, which Claude Desktop does"
    Write-Host "not track or terminate directly -- that's why its own uninstall fails)."
    Write-Host "Stopping this will disconnect Claude Desktop's current session with the"
    Write-Host "extension; it typically auto-restarts, or restart Claude Desktop to reconnect."
    $confirm = Read-Host "Stop these $($treePids.Count) process(es) now? [y/N]"
    if ($confirm -notmatch '^[Yy]') {
        Write-Host "Cancelled." -ForegroundColor DarkGray
        return
    }
    foreach ($p in $treePids) {
        try {
            Stop-Process -Id $p -Force -ErrorAction Stop
            Write-Host "  Stopped PID $p"
        } catch {
            Write-Host "  PID $p already gone"
        }
    }
    Start-Sleep -Seconds 1
    Write-Host ""
    Write-Host "Done. Its files should no longer be locked -- you can now use Claude Desktop's" -ForegroundColor Green
    Write-Host "Settings > Extensions > Uninstall normally." -ForegroundColor Green
}

function Invoke-Install {
    Write-Host ""
    $venv = Get-InstalledVenv
    $sourceVersion = Get-SourceVersion
    $sameVersionAlreadyInstalled = $false
    if ($venv) {
        $installedVersion = Get-InstalledVersion $venv
        if ($installedVersion -eq $sourceVersion) {
            $sameVersionAlreadyInstalled = $true
            Write-Host "neo-localmcp $sourceVersion is already installed." -ForegroundColor Green
            $repair = Read-Host "Force a rebuild anyway? [y/N]"
            if ($repair -match '^[Yy]') {
                & $InstallScript -Repair
            } else {
                & $InstallScript
            }
        } else {
            Write-Host "Upgrading from v$installedVersion to v$sourceVersion ..."
            & $InstallScript
        }
    } else {
        Write-Host "Installing neo-localmcp $sourceVersion ..."
        & $InstallScript
    }

    # Offer client registration regardless of which branch above ran (skip, repair,
    # upgrade, or fresh install) -- it's independent of whether the venv itself
    # changed. Skip the offer only if this looked like a no-op status check, i.e.
    # never actually prompt again mid-loop if the user is just re-checking.
    if (-not $sameVersionAlreadyInstalled -or $repair -match '^[Yy]') {
        Write-Host ""
        $registerClients = Read-Host "Register neo-localmcp with Claude Code / Codex now (neo-localmcp setup --client all)? [Y/n]"
        if ($registerClients -notmatch '^[Nn]') {
            $cmd = Join-Path $AppHome "bin\neo-localmcp.cmd"
            if (Test-Path $cmd) { & $cmd setup --client all }
        }
    }
}

function Invoke-Uninstall {
    $venv = Get-InstalledVenv
    $legacyVenvs = Join-Path $AppHome "venvs"
    if (-not $venv -and -not (Test-Path $legacyVenvs)) {
        Write-Host "No CLI install found under $AppHome -- nothing to uninstall." -ForegroundColor DarkGray
        return
    }

    Show-DesktopExtensionNotice

    Write-Host ""
    Write-Host "This removes the CLI venv, bin launchers, and PATH entry."
    $removeData = Read-Host "Also delete config.yaml and the repo index/memory database (all indexed repos, retrieval memory, cached summaries)? [y/N]"
    $wipeData = $removeData -match '^[Yy]'

    if ($wipeData) {
        $confirmed = Confirm-Destructive "This permanently deletes config.yaml and repo-context.sqlite -- every indexed repo, retrieval-memory record, and cached summary. This cannot be undone."
        if (-not $confirmed) {
            Write-Host "Cancelled." -ForegroundColor DarkGray
            return
        }
        & $UninstallScript -RemoveData
    } else {
        & $UninstallScript
    }
}

function Show-Menu {
    Write-Host "What would you like to do?"
    Write-Host "  1) Install / Upgrade"
    Write-Host "  2) Uninstall (CLI)"
    Write-Host "  3) Prepare Claude Desktop extension for uninstall (stop its process tree)"
    Write-Host "  4) Show status"
    Write-Host "  5) Exit"
    Read-Host "Choice"
}

Write-Host "neo-localmcp setup wizard" -ForegroundColor Cyan
Show-Status

$running = $true
while ($running) {
    switch (Show-Menu) {
        "1" { Invoke-Install; Show-Status }
        "2" { Invoke-Uninstall; Show-Status }
        "3" { Invoke-StopDesktopExtension; Show-Status }
        "4" { Show-Status }
        "5" { $running = $false }
        default { Write-Host "Please enter 1-5." -ForegroundColor Yellow }
    }
}
Write-Host "Done."
