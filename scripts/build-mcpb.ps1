param([string]$Output = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Get-SourceVersion([string]$RootPath) {
    $initFile = Join-Path $RootPath "neo_localmcp\__init__.py"
    $content = Get-Content -Raw -LiteralPath $initFile
    if ($content -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    throw "Could not determine neo-localmcp version from $initFile"
}

$Stage = Join-Path $env:TEMP "neo-localmcp-mcpb"
if (Test-Path $Stage) { Remove-Item -Recurse -Force -LiteralPath $Stage }
Copy-Item -Recurse -Force (Join-Path $Root "packages\claude-desktop\mcpb") $Stage
Copy-Item -Recurse -Force (Join-Path $Root "neo_localmcp") (Join-Path $Stage "neo_localmcp")
Copy-Item -Force (Join-Path $Root "pyproject.toml") (Join-Path $Stage "pyproject.toml")
Copy-Item -Force (Join-Path $Root "README.md") (Join-Path $Stage "README.md")
$Version = Get-SourceVersion -RootPath $Root
$Target = if ($Output) { $Output } else { Join-Path $Root "packages\claude-desktop\neo-localmcp-v$Version.mcpb" }
Push-Location $Stage
try {
    npx --yes @anthropic-ai/mcpb pack . $Target
    if ($LASTEXITCODE -ne 0) { throw "mcpb pack failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
Write-Host "Built $Target"
