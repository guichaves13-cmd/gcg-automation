@echo off
REM Inicia o watchdog do AvatarPilot Pro em background (auto-restart se servidor cair)
REM O watchdog detecta crash via healthz, mata orfaos GPU, reinicia, e detecta crash-loop.
setlocal
cd /d "%~dp0"

REM Verifica se ja esta rodando
tasklist /FI "WINDOWTITLE eq AvatarPilot Watchdog" 2>nul | findstr /I "powershell" >nul
if not errorlevel 1 (
    echo Watchdog ja esta rodando.
    pause
    exit /b 0
)

REM Inicia hidden, mas com titulo identificavel
start "AvatarPilot Watchdog" /min powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0server_watchdog.ps1"
echo AvatarPilot Pro watchdog iniciado — servidor reinicia automaticamente em crash.
echo Log: %~dp0watchdog.log
echo.
echo Para parar o watchdog: feche a janela 'AvatarPilot Watchdog' no Task Manager
echo OU rode: powershell "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%%server_watchdog%%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
endlocal
