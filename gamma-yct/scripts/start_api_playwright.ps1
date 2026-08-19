# API: 强制门户扫码 + Playwright DOM（禁用 CDP / 桌面 RPA）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$env:AUTOMATION_MODE = "playwright"
$env:SKIP_QR_LOGIN = "false"
$env:PLAYWRIGHT_USE_CDP = "false"
$env:DESKTOP_RPA_FALLBACK = "false"
$env:ALLOW_FINAL_SUBMIT = "false"
$env:PLAYWRIGHT_HEADLESS = "false"
$env:YCT_CAPTURE_QR = "false"
$env:YCT_LOGIN_TIMEOUT_SEC = "300"
$env:YCT_PORTAL_URL = "https://yct.sh.gov.cn/portal_yct/"
if (-not $env:PLAYWRIGHT_CHANNEL) { $env:PLAYWRIGHT_CHANNEL = "chrome" }

Write-Host "AUTOMATION_MODE=$env:AUTOMATION_MODE USE_CDP=$env:PLAYWRIGHT_USE_CDP SKIP_QR=$env:SKIP_QR_LOGIN"
Write-Host "Flow: portal -> close popup -> 开办 -> 企业 -> QR -> 我已知晓 -> 设立登记 -> name DOM -> Next"
& .\.venv\Scripts\uvicorn.exe apps.api.main:app --host 127.0.0.1 --port 8000
