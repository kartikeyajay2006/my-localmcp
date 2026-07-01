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

function Get-ClientStatus([string]$Cmd) {
    # Reuses client_setup.py's client_status() (exposed as `neo-localmcp clients`)
    # rather than re-deriving paths here -- this wizard must never drift from what
    # the actual setup code will do.
    if (-not (Test-Path $Cmd)) { return $null }
    try {
        $json = & $Cmd clients 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
        return ($json -join "") | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Show-SurfacePreview([string]$Surface, $Status) {
    switch ($Surface) {
        "claude-code" {
            Write-Host "  Claude Code:"
            if ($Status) {
                $entry = $Status.mcp_server_block.PSObject.Properties | Select-Object -First 1
                Write-Host "    Slash commands  -> $($Status.paths.claude_code_commands.path)"
                Write-Host "    MCP registration -> claude mcp add --scope user $($entry.Name) -- $($entry.Value.command) (falls back to project scope if the 'claude' CLI isn't found)"
            } else {
                Write-Host "    ~/.claude/commands/neo-localmcp/ plus 'claude mcp add --scope user neo-localmcp'"
            }
        }
        "codex" {
            Write-Host "  Codex CLI/Desktop:"
            if ($Status) {
                Write-Host "    $($Status.paths.codex_cli_config.path)  (shared by Codex app, CLI, and IDE -- one marked, replaceable block)"
            } else {
                Write-Host "    ~/.codex/config.toml (shared by Codex app, CLI, and IDE)"
            }
        }
        "claude-desktop" {
            Write-Host "  Claude Desktop:"
            Write-Host "    Cannot be automated -- install manually from $(Join-Path $AppHome 'neo-localmcp.mcpb') via Settings > Extensions > Advanced settings > Install Extension."
        }
    }
}

function Invoke-ClientSurfaceSelection([string]$Cmd) {
    Write-Host ""
    Write-Host "Which client surfaces would you like to set up? (CLI is already installed above.)"
    Write-Host "  1) Claude Code"
    Write-Host "  2) Codex CLI/Desktop"
    Write-Host "  3) Claude Desktop (manual instructions only -- this wizard cannot install it)"
    Write-Host "  4) All of the above"
    Write-Host "  5) None / skip"
    $raw = Read-Host "Enter comma-separated numbers (e.g. 1,2)"
    $numbers = @($raw -split "[,\s]+" | Where-Object { $_ -match '^\d+$' })
    if ($numbers.Count -eq 0 -or $numbers -contains "5") {
        Write-Host "Skipping client registration." -ForegroundColor DarkGray
        return
    }

    $surfaces = [System.Collections.Generic.List[string]]::new()
    if ($numbers -contains "4") {
        $surfaces.AddRange([string[]]@("claude-code", "codex", "claude-desktop"))
    } else {
        if ($numbers -contains "1") { $surfaces.Add("claude-code") }
        if ($numbers -contains "2") { $surfaces.Add("codex") }
        if ($numbers -contains "3") { $surfaces.Add("claude-desktop") }
    }
    if ($surfaces.Count -eq 0) {
        Write-Host "No valid selection -- skipping client registration." -ForegroundColor Yellow
        return
    }

    $status = Get-ClientStatus -Cmd $Cmd
    Write-Host ""
    Write-Host "This will touch:"
    foreach ($surface in $surfaces) { Show-SurfacePreview -Surface $surface -Status $status }
    $confirm = Read-Host "Proceed? [Y/n]"
    if ($confirm -match '^[Nn]') {
        Write-Host "Cancelled -- no client files were changed." -ForegroundColor DarkGray
        return
    }

    $setupArgs = @("setup")
    foreach ($surface in $surfaces) { $setupArgs += @("--client", $surface) }
    & $Cmd @setupArgs
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
        $cmd = Join-Path $AppHome "bin\neo-localmcp.cmd"
        if (Test-Path $cmd) { Invoke-ClientSurfaceSelection -Cmd $cmd }
    }
}

function Invoke-RemoveClientSurface([string]$Cmd, [string]$Client) {
    if (-not (Test-Path $Cmd)) {
        Write-Host "  CLI launcher not found -- cannot deregister $Client (its venv may already be gone; remove client config manually)." -ForegroundColor Yellow
        return
    }
    & $Cmd remove-client --client $Client
}

function Invoke-UninstallCliState {
    # Granular keep/delete for everything under ~/.neo-localmcp, replacing the old
    # single "also delete config + database?" prompt. Each destructive data category
    # gets its own typed-DELETE gate so nothing irreversible is bundled behind one
    # blanket confirmation.
    $venv = Get-InstalledVenv
    $legacyVenvs = Join-Path $AppHome "venvs"
    if (-not $venv -and -not (Test-Path $legacyVenvs)) {
        Write-Host "No CLI install found under $AppHome -- nothing to remove." -ForegroundColor DarkGray
        return
    }

    Write-Host ""
    Write-Host "Choose what to delete under $AppHome (press Enter to accept each default):" -ForegroundColor Cyan

    # Splat via a HASHTABLE, not an array. Array-splatting "-Switch" strings into a
    # PowerShell script binds them as positional VALUES, not switch parameters, so
    # every switch is silently dropped and uninstall.ps1 runs on its defaults
    # (verified: array splat -> RemoveConfig=False; hashtable splat -> True).
    $switchParams = @{}

    $removeVenv = (Read-Host "  Remove the CLI runtime (venv, bin launchers)? [Y/n]") -notmatch '^[Nn]'
    if (-not $removeVenv) { $switchParams["KeepVenv"] = $true }

    $removeMcpb = (Read-Host "  Remove the local neo-localmcp.mcpb copy? [Y/n]") -notmatch '^[Nn]'
    if (-not $removeMcpb) { $switchParams["KeepMcpb"] = $true }

    $removeServers = (Read-Host "  Remove the servers/ registry directory (stale after stop)? [y/N]") -match '^[Yy]'
    if ($removeServers) { $switchParams["RemoveServers"] = $true }

    $removeConfig = (Read-Host "  Delete config.yaml (your Ollama endpoint/model settings)? [y/N]") -match '^[Yy]'

    Write-Host ""
    Write-Host "  NOTE: the repo index/memory database is ONE shared file for EVERY repo you" -ForegroundColor Yellow
    Write-Host "  have ever indexed -- not just this directory. Deleting it wipes all indexes," -ForegroundColor Yellow
    Write-Host "  retrieval memory, and cached summaries across every repo." -ForegroundColor Yellow
    $removeDatabase = (Read-Host "  Delete the shared repo index/memory database (repo-context.sqlite)? [y/N]") -match '^[Yy]'

    # Per-category typed-DELETE gate for the genuinely destructive data categories.
    if ($removeConfig) {
        if (Confirm-Destructive "About to permanently delete config.yaml (custom Ollama endpoint/model settings). It will be recreated with defaults on next use.") {
            $switchParams["RemoveConfig"] = $true
        } else {
            Write-Host "  Keeping config.yaml." -ForegroundColor DarkGray
        }
    }
    if ($removeDatabase) {
        if (Confirm-Destructive "About to permanently delete repo-context.sqlite -- EVERY indexed repo, all retrieval memory, and all cached summaries across your whole machine. This cannot be undone.") {
            $switchParams["RemoveDatabase"] = $true
        } else {
            Write-Host "  Keeping the repo index/memory database." -ForegroundColor DarkGray
        }
    }

    Write-Host ""
    & $UninstallScript @switchParams
}

function Invoke-Uninstall {
    $cmd = Join-Path $AppHome "bin\neo-localmcp.cmd"

    Write-Host ""
    Write-Host "Which surfaces would you like to uninstall / deregister?"
    Write-Host "  1) Claude Code (deregister MCP + remove slash commands)"
    Write-Host "  2) Codex CLI/Desktop (strip its config.toml block)"
    Write-Host "  3) Claude Desktop (manual -- instructions only)"
    Write-Host "  4) CLI + local state under ~/.neo-localmcp (venv, config, database, ...)"
    Write-Host "  5) All of the above"
    Write-Host "  6) Cancel"
    $raw = Read-Host "Enter comma-separated numbers (e.g. 1,4)"
    $numbers = @($raw -split "[,\s]+" | Where-Object { $_ -match '^\d+$' })
    if ($numbers.Count -eq 0 -or $numbers -contains "6") {
        Write-Host "Cancelled." -ForegroundColor DarkGray
        return
    }

    $doClaudeCode = ($numbers -contains "1") -or ($numbers -contains "5")
    $doCodex      = ($numbers -contains "2") -or ($numbers -contains "5")
    $doDesktop    = ($numbers -contains "3") -or ($numbers -contains "5")
    $doCli        = ($numbers -contains "4") -or ($numbers -contains "5")

    if ($doClaudeCode) {
        Write-Host ""
        Write-Host "== Claude Code ==" -ForegroundColor Cyan
        Invoke-RemoveClientSurface -Cmd $cmd -Client "claude-code"
    }
    if ($doCodex) {
        Write-Host ""
        Write-Host "== Codex CLI/Desktop ==" -ForegroundColor Cyan
        Invoke-RemoveClientSurface -Cmd $cmd -Client "codex"
    }
    if ($doDesktop) {
        Write-Host ""
        Write-Host "== Claude Desktop ==" -ForegroundColor Cyan
        Show-DesktopExtensionNotice
        Write-Host "Reminder: if Claude Desktop's own uninstall hangs, run menu option 3" -ForegroundColor DarkGray
        Write-Host "(Prepare Claude Desktop extension for uninstall) first to free its file locks." -ForegroundColor DarkGray
    }
    if ($doCli) {
        Write-Host ""
        Write-Host "== CLI + local state ==" -ForegroundColor Cyan
        # Deregistering the CLI's own bin from clients before deleting the venv avoids
        # leaving client configs pointing at a launcher that's about to vanish.
        if (-not $doClaudeCode -or -not $doCodex) {
            Write-Host "(Note: you are removing the CLI runtime; any client still registered against it" -ForegroundColor DarkGray
            Write-Host " will point at a missing launcher until you also deregister it above.)" -ForegroundColor DarkGray
        }
        Invoke-UninstallCliState
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
