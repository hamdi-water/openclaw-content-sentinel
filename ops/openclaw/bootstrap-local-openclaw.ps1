param(
  [string]$WorkspaceDir = "",
  [string]$PluginDir = "",
  [string]$PythonPath = "python",
  [string]$CliModule = "openclaw_content_sentinel.cli",
  [string]$PromptFile = "",
  [switch]$InstallGateway,
  [switch]$StartGateway
)

$ErrorActionPreference = "Stop"

if (-not $WorkspaceDir) {
  $WorkspaceDir = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
}

if (-not $PluginDir) {
  $PluginDir = Join-Path $WorkspaceDir "plugins\\sentinel-ops"
}

if (-not $PromptFile) {
  $PromptFile = Join-Path $WorkspaceDir "config\\daily_prompt_template.md"
}

if (-not (Test-Path $PluginDir)) {
  throw "Plugin directory not found: $PluginDir"
}

if (-not (Test-Path $PromptFile)) {
  throw "Prompt file not found: $PromptFile"
}

$configPath = & openclaw config file
if (-not $configPath) {
  throw "Failed to resolve the active OpenClaw config file."
}

$configPath = $configPath.Trim()
$gatewayToken = ""

if (Test-Path $configPath) {
  try {
    $existingConfig = Get-Content $configPath -Raw | ConvertFrom-Json -Depth 20
    $gatewayToken = [string]$existingConfig.gateway.auth.token
  } catch {
    $gatewayToken = ""
  }
}

if (-not $gatewayToken) {
  $gatewayToken = [guid]::NewGuid().Guid
}

$batchPath = Join-Path $PSScriptRoot "config-set.batch.json"
$ops = @(
  @{ path = "agents.defaults.workspace"; value = $WorkspaceDir },
  @{ path = "agents.defaults.repoRoot"; value = $WorkspaceDir },
  @{ path = "gateway.mode"; value = "local" },
  @{ path = "gateway.auth.token"; value = $gatewayToken },
  @{ path = "browser.profiles.openclaw-linkedin.cdpPort"; value = 18810 },
  @{ path = "browser.profiles.openclaw-linkedin.color"; value = "#0A66C2" },
  @{ path = "browser.profiles.openclaw-facebook.cdpPort"; value = 18811 },
  @{ path = "browser.profiles.openclaw-facebook.color"; value = "#1877F2" },
  @{ path = "browser.profiles.openclaw-x.cdpPort"; value = 18812 },
  @{ path = "browser.profiles.openclaw-x.color"; value = "#111111" },
  @{ path = "plugins.allow"; value = @("sentinel-ops") },
  @{ path = "plugins.load.paths"; value = @($PluginDir) },
  @{ path = "plugins.entries.sentinel-ops.enabled"; value = $true },
  @{ path = "plugins.entries.sentinel-ops.config.workspaceDir"; value = $WorkspaceDir },
  @{ path = "plugins.entries.sentinel-ops.config.pythonPath"; value = $PythonPath },
  @{ path = "plugins.entries.sentinel-ops.config.cliModule"; value = $CliModule },
  @{ path = "plugins.entries.sentinel-ops.config.promptFile"; value = $PromptFile }
)

$ops | ConvertTo-Json -Depth 8 | Set-Content -Path $batchPath -Encoding UTF8

try {
  & openclaw config set --batch-file $batchPath
  & openclaw config validate

  if ($InstallGateway) {
    & openclaw gateway install --token $gatewayToken
  }

  if ($StartGateway) {
    & openclaw gateway start
  }
} finally {
  if (Test-Path $batchPath) {
    Remove-Item $batchPath -Force
  }
}

Write-Host ""
Write-Host "OpenClaw bootstrap complete."
Write-Host "Workspace: $WorkspaceDir"
Write-Host "Plugin dir: $PluginDir"
Write-Host "Prompt file: $PromptFile"
Write-Host "Gateway token: $gatewayToken"
