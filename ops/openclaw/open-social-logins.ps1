param(
  [switch]$StopAfterOpen
)

$ErrorActionPreference = "Stop"

$profiles = @(
  @{ Name = "linkedin"; Profile = "openclaw-linkedin"; Url = "https://www.linkedin.com/feed/" },
  @{ Name = "facebook"; Profile = "openclaw-facebook"; Url = "https://www.facebook.com/" },
  @{ Name = "x"; Profile = "openclaw-x"; Url = "https://x.com/home" }
)

foreach ($item in $profiles) {
  & openclaw browser --browser-profile $item.Profile start | Out-Null
  & openclaw browser --browser-profile $item.Profile open $item.Url | Out-Null
}

Write-Host ""
Write-Host "Social login windows opened for:"
foreach ($item in $profiles) {
  Write-Host " - $($item.Name): $($item.Profile)"
}
Write-Host "Log in manually with sandbox accounts, then rerun your publish flow."

if ($StopAfterOpen) {
  foreach ($item in $profiles) {
    & openclaw browser --browser-profile $item.Profile stop | Out-Null
  }
  Write-Host "Profiles stopped after opening."
}
