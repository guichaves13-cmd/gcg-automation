@echo off
REM ===========================================================================
REM AvatarPilot Pro — Setup de primeira execucao
REM Cria o venv311, instala dependencias Python, e baixa os modelos pesados.
REM Roda APENAS na primeira execucao (chamado pelo Start AvatarPilot Pro.bat).
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  ===== ETAPA 1/3: Verificando Python 3.11+ =====
where python >nul 2>&1
if errorlevel 1 (
    echo  ERRO: Python nao encontrado no PATH.
    echo  Instale Python 3.11 ou superior de https://python.org/downloads/
    echo  durante a instalacao MARQUE "Add Python to PATH".
    pause & exit /b 1
)
python --version
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo  ERRO: Python detectado e menor que 3.11. Atualize.
    pause & exit /b 1
)

echo.
echo  ===== ETAPA 2/3: Criando ambiente virtual e instalando dependencias =====
echo  ^(isso pode levar 5-15 minutos dependendo da sua conexao^)
if not exist "venv311" (
    python -m venv venv311
    if errorlevel 1 (
        echo  ERRO: falha ao criar venv. Verifique permissoes desta pasta.
        pause & exit /b 1
    )
)
call venv311\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
if exist "requirements.txt" (
    pip install -r requirements.txt
    if errorlevel 1 (
        echo  ERRO: falha ao instalar dependencias. Verifique sua conexao.
        pause & exit /b 1
    )
) else (
    echo  AVISO: requirements.txt nao encontrado. Instalando minimo essencial...
    pip install flask waitress requests cryptography edge-tts opencv-python pillow numpy psutil
)

echo.
echo  ===== ETAPA 3/3: Baixando modelos de IA (~15GB) =====
echo  Isso e o mais demorado ^(30min-2h dependendo da conexao^).
echo  Voce pode pular agora e baixar depois rodando os scripts em scripts\.
echo.
choice /C SN /M "Baixar modelos agora?"
if errorlevel 2 (
    echo  Pulando download de modelos. Voce podera baixar depois.
    echo  Os caminhos dos scripts estao em scripts\ ^(ex: download_gesture_pack.py^)
    goto done
)

if exist "scripts\download_all_models.bat" (
    call scripts\download_all_models.bat
) else (
    echo  AVISO: scripts\download_all_models.bat nao encontrado.
    echo  Baixe manualmente: MuseTalk, SadTalker, Wav2Lip, GFPGAN, InsightFace.
    echo  Detalhes em RESUMO_AVATARPILOT.md.
)

:done
echo.
echo  ============================================================
echo   Setup concluido! Pode usar o AvatarPilot Pro normalmente.
echo  ============================================================
endlocal
exit /b 0
