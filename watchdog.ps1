# autoliveblog watchdog: health-check every 5 min, auto-restart the server if down
# (with Telegram notification), and auto-update yt-dlp weekly.
$root = $PSScriptRoot
$python = $env:AUTOLIVEBLOG_PYTHON
if (-not $python) { $python = "python" }
$marker = Join-Path $root "logs\ytdlp_updated.marker"

function Get-EnvValue($key) {
    $line = Get-Content (Join-Path $root ".env") -ErrorAction SilentlyContinue |
        Select-String "^$key="
    if ($line) { return ($line.Line -replace "^$key=", "") }
}

function Send-Tg($text) {
    $t = Get-EnvValue "TELEGRAM_BOT_TOKEN"
    $chat = Get-EnvValue "TELEGRAM_CHAT_ID"
    if ($t -and $chat) {
        foreach ($c in ($chat -split ",")) {
            try {
                Invoke-RestMethod "https://api.telegram.org/bot$t/sendMessage" -Method Post `
                    -Body @{ chat_id = $c.Trim(); text = $text } -TimeoutSec 15 | Out-Null
            } catch {}
        }
    }
}

function Start-Server {
    wscript (Join-Path $root "start_hidden.vbs")
}

function Test-Server {
    try {
        Invoke-RestMethod "http://127.0.0.1:8766/api/usage" -TimeoutSec 8 | Out-Null
        return $true
    } catch { return $false }
}

New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null

if (-not (Test-Server)) { Start-Server; Start-Sleep -Seconds 15 }

while ($true) {
    if (-not (Test-Server)) {
        Start-Server
        Start-Sleep -Seconds 20
        if (Test-Server) {
            Send-Tg "🔧 autoliveblog server was down and has been auto-restarted (running jobs will resume)."
        } else {
            Send-Tg "⚠ autoliveblog server restart failed, check logs\server.log."
            Start-Sleep -Seconds 300
        }
    }
    $needUpdate = $true
    if (Test-Path $marker) {
        $age = (Get-Date) - (Get-Item $marker).LastWriteTime
        if ($age.TotalDays -lt 7) { $needUpdate = $false }
    }
    if ($needUpdate) {
        & "$python" -m pip install -q -U yt-dlp 2>$null
        New-Item -ItemType File -Force $marker | Out-Null
        $pid8766 = (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -First 1
        if ($pid8766) { Stop-Process -Id $pid8766 -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 3
        Start-Server
    }
    Start-Sleep -Seconds 300
}
