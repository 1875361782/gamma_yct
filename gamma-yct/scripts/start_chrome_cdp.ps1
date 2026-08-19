# Start Chrome with remote debugging. Opens YCT portal first (login before name apply).
# Quit other Chrome instances if the debug port fails to bind.

$ErrorActionPreference = "Stop"

$chrome = @(
  "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) {
  Write-Error "chrome.exe not found"
  exit 1
}

$userData = Join-Path $env:LOCALAPPDATA "chrome-yct-debug"
New-Item -ItemType Directory -Force -Path $userData | Out-Null
$port = 9222
$url = "https://yct.sh.gov.cn/portal_yct/"

Write-Host "Starting Chrome remote debugging on port $port"
Write-Host "user-data-dir: $userData"
Write-Host "URL (portal): $url"
Start-Process -FilePath $chrome -ArgumentList @(
  "--remote-debugging-port=$port",
  "--user-data-dir=$userData",
  "--no-first-run",
  "--no-default-browser-check",
  $url
)

Start-Sleep -Seconds 2
try {
  $v = Invoke-RestMethod "http://127.0.0.1:$port/json/version"
  Write-Host "CDP_OK $($v.Browser)"
} catch {
  Write-Host "CDP not ready yet; check http://127.0.0.1:$port/json/version"
}

Write-Host "Login on the portal page first, then start the case."
Write-Host 'Env example: AUTOMATION_MODE=playwright PLAYWRIGHT_USE_CDP=true SKIP_QR_LOGIN=true'
