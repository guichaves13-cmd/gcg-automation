@echo off
chcp 65001 >nul
title TitlePilot Pro — Instalador

echo.
echo ████████╗██╗████████╗██╗     ███████╗██████╗ ██╗██╗      ██████╗ ████████╗
echo ╚══██╔══╝██║╚══██╔══╝██║     ██╔════╝██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝
echo    ██║   ██║   ██║   ██║     █████╗  ██████╔╝██║██║     ██║   ██║   ██║   
echo    ██║   ██║   ██║   ██║     ██╔══╝  ██╔═══╝ ██║██║     ██║   ██║   ██║   
echo    ██║   ██║   ██║   ███████╗███████╗██║     ██║███████╗╚██████╔╝   ██║   
echo    ╚═╝   ╚═╝   ╚═╝   ╚══════╝╚══════╝╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝   
echo                              PRO v2.0 — Instalador
echo.

:: Detectar Python
set PYTHON_CMD=
for %%p in (
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    python
    python3
) do (
    if exist %%~p (
        set PYTHON_CMD=%%~p
        goto :found_python
    )
)
where python >nul 2>&1 && set PYTHON_CMD=python && goto :found_python
echo [ERRO] Python não encontrado!
echo Baixe em: https://python.org/downloads
echo.
pause
exit /b 1

:found_python
echo [OK] Python encontrado: %PYTHON_CMD%
%PYTHON_CMD% --version

echo.
echo [1/3] Instalando dependencias Python...
%PYTHON_CMD% -m pip install flask flask-cors requests google-generativeai google-api-python-client --quiet --upgrade
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias!
    pause
    exit /b 1
)
echo [OK] Dependencias instaladas!

echo.
echo [2/3] Configurando API Keys...
echo.
set /p GEMINI_KEY="Sua chave Gemini AI (obtenha em aistudio.google.com): "
set /p YT_KEY="Sua chave YouTube Data API (opcional, pressione Enter para pular): "

%PYTHON_CMD% -c "
import json, base64, os

keys_file = r'%~dp0..\.api_keys.json'
try:
    with open(keys_file) as f:
        keys = json.load(f)
except:
    keys = {}

gemini = '%GEMINI_KEY%'.strip()
yt = '%YT_KEY%'.strip()

if gemini:
    keys['gemini'] = base64.b64encode(gemini.encode()).decode()
    print('[OK] Chave Gemini salva!')
if yt:
    keys['youtube'] = base64.b64encode(yt.encode()).decode()
    print('[OK] Chave YouTube salva!')

with open(keys_file, 'w') as f:
    json.dump(keys, f, indent=2)
"

echo.
echo [3/3] Criando atalho na area de trabalho...
%PYTHON_CMD% -c "
import os, sys
desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
bat_src = os.path.abspath('%~dp0START.bat')
shortcut_path = os.path.join(desktop, 'TitlePilot Pro.lnk')
try:
    import winshell
    from win32com.client import Dispatch
    shell = Dispatch('WScript.Shell')
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.Targetpath = bat_src
    shortcut.WorkingDirectory = os.path.dirname(bat_src)
    shortcut.IconLocation = bat_src
    shortcut.save()
    print('[OK] Atalho criado na area de trabalho!')
except:
    print('[INFO] Atalho nao criado (instale pywin32 para isso)')
"

echo.
echo ════════════════════════════════════════
echo   INSTALACAO CONCLUIDA!
echo ════════════════════════════════════════
echo.
echo Para iniciar: execute START.bat
echo Ou clique em "TitlePilot Pro" na area de trabalho
echo.
pause
