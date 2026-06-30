param([switch]$DryRun, [switch]$Repair, [switch]$Upgrade)
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppHome = if ($env:NEO_LOCALMCP_HOME) { $env:NEO_LOCALMCP_HOME } else { Join-Path $HOME ".neo-localmcp" }
$VenvDir = Join-Path $AppHome "venv"
$BinDir = Join-Path $AppHome "bin"

if ($DryRun) {
    Write-Host "Would install or repair neo-localmcp from $RootDir"
    Write-Host "Application home: $AppHome"
    Write-Host "Virtual environment: $VenvDir"
    exit 0
}

function Get-PythonCommand {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = "py"; Args = @("-3") } }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Exe = "python"; Args = @() } }
    throw "Python 3.10+ is required. Install Python, then rerun this script."
}

New-Item -ItemType Directory -Force -Path $AppHome | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$Py = Get-PythonCommand
& $Py.Exe @($Py.Args) -m venv $VenvDir
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade --force-reinstall $RootDir
$McpbSource = Join-Path $RootDir "packages\claude-desktop\neo-localmcp.mcpb"
if (Test-Path $McpbSource) { Copy-Item -Force $McpbSource (Join-Path $AppHome "neo-localmcp.mcpb") }

$Cmd = Join-Path $BinDir "neo-localmcp.cmd"
$CmdContent = "@echo off`r`n`"$VenvPython`" -m neo_localmcp.cli %*`r`n"
Set-Content -Path $Cmd -Value $CmdContent -Encoding ASCII

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
if (($UserPath -split ";") -notcontains $BinDir) {
    $NewPath = if ($UserPath.Trim().Length -gt 0) { "$BinDir;$UserPath" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$BinDir;$env:Path"
}

& $Cmd init
try { & $Cmd doctor } catch { Write-Host "Doctor reported an issue; install still completed." }

Write-Host ""
Write-Host "Installed neo-localmcp."
Write-Host "Command: $Cmd"
Write-Host "Open a new terminal if 'neo-localmcp' is not immediately on PATH."
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Open a new terminal if PATH is not refreshed."
Write-Host "  2. Run setup once from anywhere:"
Write-Host "       neo-localmcp setup --client all"
Write-Host "  3. cd into the repo you want analyzed, then index/context there:"
Write-Host "       cd C:\Path\To\YourRepo"
Write-Host "       neo-localmcp index"
Write-Host '       neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync"'
Write-Host "  4. Check paths anytime with: neo-localmcp where"
