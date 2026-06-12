@echo off
chcp 65001 >nul
title TitlePilot Pro v2.0

:: Detectar Python automaticamente
set PYTHON_CMD=
for %%p in (
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
) do (
    if exist %%~p (
        set PYTHON_CMD=%%~p
        goto :start
    )
)
where python >nul 2>&1 && set PYTHON_CMD=python && goto :start
echo [ERRO] Python não encontrado! Execute INSTALL.bat primeiro.
pause
exit /b 1

:start
echo.
echo  ╔══════════════════════════════════════╗
echo  ║        TitlePilot Pro v2.0           ║
echo  ║   YouTube Title Intelligence Tool    ║
echo  ╚══════════════════════════════════════╝
echo.
echo  Iniciando servidor em http://localhost:5050
echo  Pressione Ctrl+C para parar
echo.

cd /d "%~dp0"
%PYTHON_CMD% server.py
pause
