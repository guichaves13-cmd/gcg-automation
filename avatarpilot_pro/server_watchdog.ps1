# server_watchdog.ps1 - Watchdog robusto p/ AvatarPilot Pro
# - Detecta servidor caido (healthz nao responde em 5s)
# - Mata pelo PORT 5052 (mais confiavel que filtro de nome)
# - Limpa orfaos GPU (ffmpeg + workers) antes de reiniciar
# - Loga em watchdog.log com rotacao a 1MB
# - Detecta crash-loop (>5 restarts em 10min) e para de tentar
$ErrorActionPreference = "SilentlyContinue"

$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPy  = Join-Path $baseDir "venv311\Scripts\python.exe"
$server  = Join-Path $baseDir "server.py"
$logFile = Join-Path $baseDir "watchdog.log"
$port    = 5052

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Output $line
    # Rotacao simples: se >1MB, renomeia p/ .old e zera
    if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt 1048576) {
        $old = "$logFile.old"
        if (Test-Path $old) { Remove-Item $old -Force }
        Rename-Item $logFile $old
    }
}

function Test-ServerAlive {
    try {
        $r = Invoke-WebRequest "http://localhost:$port/api/healthz" -TimeoutSec 5 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Stop-ServerOnPort {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Write-Log "  killing server PID $($conn.OwningProcess) on port $port"
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
    }
}

function Clear-Orphans {
    # ffmpeg orfaos
    $ff = @(Get-Process ffmpeg -ErrorAction SilentlyContinue)
    foreach ($f in $ff) { Stop-Process -Id $f.Id -Force -ErrorAction SilentlyContinue }
    if ($ff.Count -gt 0) { Write-Log "  cleared $($ff.Count) ffmpeg orphans" }
    # workers GPU (SadTalker, MuseTalk, Wav2Lip, GFPGAN, face_swap)
    $workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'inference|face_swap_worker|gfpgan_worker|musetalk|wav2lip|sadtalker' }
    foreach ($w in $workers) {
        Stop-Process -Id $w.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($workers.Count -gt 0) { Write-Log "  cleared $($workers.Count) worker orphans" }
}

function Start-Server {
    if (-not (Test-Path $venvPy)) { Write-Log "FATAL: venv311 nao encontrado em $venvPy"; return $false }
    if (-not (Test-Path $server)) { Write-Log "FATAL: server.py nao encontrado em $server"; return $false }
    Write-Log "  starting server: $venvPy $server"
    Start-Process -FilePath $venvPy -ArgumentList "`"$server`"" -WorkingDirectory $baseDir -WindowStyle Hidden
    # Aguarda boot ate 60s
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 2
        if (Test-ServerAlive) { Write-Log "  server UP after $($i*2)s"; return $true }
    }
    Write-Log "  WARNING: server nao respondeu apos 60s"
    return $false
}

Write-Log "============================================================"
Write-Log "Watchdog STARTED (port $port, baseDir $baseDir)"
Write-Log "============================================================"

# Detector de crash-loop: 5 restarts em 10min => espera 10min antes de tentar de novo
$restartTimes = @()
$crashLoopBackoff = 600

while ($true) {
    if (Test-ServerAlive) {
        Start-Sleep 30
        continue
    }
    Write-Log "Server DOWN — iniciando recovery..."
    # Limpa restartTimes antigos (>10min)
    $cutoff = (Get-Date).AddMinutes(-10)
    $restartTimes = @($restartTimes | Where-Object { $_ -gt $cutoff })
    if ($restartTimes.Count -ge 5) {
        Write-Log "  CRASH-LOOP detectado (5+ restarts em 10min). Pausando $crashLoopBackoff s."
        Start-Sleep $crashLoopBackoff
        $restartTimes = @()
        continue
    }
    Stop-ServerOnPort
    Clear-Orphans
    Start-Sleep 3
    $ok = Start-Server
    if ($ok) {
        $restartTimes += (Get-Date)
        Write-Log "  recovery SUCESSO (restart #$($restartTimes.Count) nos ultimos 10min)"
        Start-Sleep 30
    } else {
        Write-Log "  recovery FALHOU. Tentando novamente em 60s."
        Start-Sleep 60
    }
}
