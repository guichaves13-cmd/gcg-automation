@echo off
REM ===========================================================================
REM AvatarPilot Pro — Launcher (release / instalacao do cliente)
REM Inicia o servidor com licenciamento ATIVO + abre o navegador na UI.
REM ===========================================================================
setlocal
cd /d "%~dp0"

REM ── Liga o enforcement de licenca (producao) ────────────────────────────────
set AVP_LICENSE_ENFORCE=1
if not defined AVP_MAX_SCRIPT_CHARS set AVP_MAX_SCRIPT_CHARS=15000
if not defined AVP_STUCK_TIMEOUT_MIN set AVP_STUCK_TIMEOUT_MIN=240

REM ── Primeira execucao: rodar setup automatico ──────────────────────────────
if not exist "venv311\Scripts\python.exe" (
    echo.
    echo  ============================================================
    echo   AvatarPilot Pro — Primeira execucao
    echo  ============================================================
    echo   Vou instalar o Python venv + dependencias agora.
    echo   Isso roda apenas na primeira vez ^(~5-15 min^).
    echo  ============================================================
    if exist "first_run_setup.bat" (
        call first_run_setup.bat
        if errorlevel 1 (
            echo  Configuracao inicial FALHOU. Veja as mensagens acima.
            pause & exit /b 1
        )
    ) else (
        echo  ERRO: first_run_setup.bat ausente. Reinstale o aplicativo.
        pause & exit /b 1
    )
)

REM ── Inicia o servidor em background ────────────────────────────────────────
echo.
echo  Iniciando AvatarPilot Pro...
start "" /B "venv311\Scripts\python.exe" server.py

REM ── Aguarda servidor responder na porta 5052 ───────────────────────────────
set TRIES=0
:waitloop
set /a TRIES+=1
ping -n 2 127.0.0.1 >nul
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://localhost:5052/api/healthz' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { 0 }" 2>nul | findstr /C:"200" >nul
if errorlevel 1 (
    if %TRIES% LSS 30 goto waitloop
    echo  Servidor demorou demais. Abra http://localhost:5052 manualmente.
    goto openbrowser
)

:openbrowser
start "" "http://localhost:5052"
echo.
echo  ============================================================
echo   AvatarPilot Pro rodando em http://localhost:5052
echo   Para ativar sua licenca: Configuracoes ^> Licenca
echo   Feche esta janela para encerrar o servidor.
echo  ============================================================
echo.
pause >nul
endlocal
