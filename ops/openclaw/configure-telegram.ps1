param(
  [string]$BotTokenEnv = "OPENCLAW_TELEGRAM_BOT_TOKEN",
  [string]$AllowedUsersEnv = "OPENCLAW_TELEGRAM_ALLOWED_USERS",
  [switch]$RestartGateway
)

$ErrorActionPreference = "Stop"

$botToken = [Environment]::GetEnvironmentVariable($BotTokenEnv)
if (-not $botToken) {
  throw "Missing environment variable: $BotTokenEnv"
}

$rawUsers = [Environment]::GetEnvironmentVariable($AllowedUsersEnv)
if (-not $rawUsers) {
  throw "Missing environment variable: $AllowedUsersEnv"
}

$allowFrom = @(
  $rawUsers.Split(",") |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
)

if (-not $allowFrom.Count) {
  throw "No Telegram operator IDs found in $AllowedUsersEnv"
}

$customCommands = @(
  @{ command = "ocs-security"; description = "Audit local security" },
  @{ command = "ocs-compliance"; description = "Show compliance gaps" },
  @{ command = "ocs-runtime"; description = "Inspect runtime config" },
  @{ command = "ocs-scheduler"; description = "Audit scheduler health" },
  @{ command = "ocs-browser-health"; description = "Check browser profiles" },
  @{ command = "ocs-doctor"; description = "Check workspace readiness" },
  @{ command = "ocs-dashboard"; description = "Show dashboard summary" },
  @{ command = "ocs-status"; description = "Show recent runs" },
  @{ command = "ocs-run-now"; description = "Trigger a new run" },
  @{ command = "ocs-approve"; description = "Approve a run" },
  @{ command = "ocs-reject"; description = "Reject a run" },
  @{ command = "ocs-logs"; description = "Show run logs" },
  @{ command = "ocs-pause"; description = "Pause automation" },
  @{ command = "ocs-resume"; description = "Resume automation" },
  @{ command = "ocs-stage-publish"; description = "Prepare publish metadata" },
  @{ command = "ocs-publish"; description = "Run browser publish" },
  @{ command = "ocs-retry"; description = "Retry a failed publish" }
)

$batchPath = Join-Path $PSScriptRoot "telegram-config.batch.json"
$ops = @(
  @{ path = "channels.telegram.enabled"; value = $true },
  @{ path = "channels.telegram.dmPolicy"; value = "allowlist" },
  @{ path = "channels.telegram.allowFrom"; value = $allowFrom },
  @{ path = "channels.telegram.commands.native"; value = $true },
  @{ path = "channels.telegram.customCommands"; value = $customCommands }
)

$ops | ConvertTo-Json -Depth 8 | Set-Content -Path $batchPath -Encoding UTF8

try {
  & openclaw config set channels.telegram.botToken --ref-provider default --ref-source env --ref-id $BotTokenEnv
  & openclaw config set --batch-file $batchPath
  & openclaw config validate

  if ($RestartGateway) {
    & openclaw gateway restart
  }
} finally {
  if (Test-Path $batchPath) {
    Remove-Item $batchPath -Force
  }
}

Write-Host ""
Write-Host "Telegram channel configured."
Write-Host "Token env: $BotTokenEnv"
Write-Host "Allowed operators: $($allowFrom -join ', ')"
Write-Host "Custom commands: $($customCommands -join ', ')"
