@echo off
REM ===========================================================================
REM AvatarPilot Pro - Download de todos os modelos de IA (~15-20 GB)
REM Chamado automaticamente por first_run_setup.bat, ou pode ser rodado solo.
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."

if not exist "venv311\Scripts\python.exe" (
    echo  ERRO: venv311 nao encontrado. Rode first_run_setup.bat primeiro.
    pause & exit /b 1
)

echo.
echo  ============================================================
echo   Baixando modelos de IA (~15-20 GB)
echo   Demora 30 min a 2 h dependendo da conexao
echo  ============================================================
echo.

REM Cria pasta models se nao existe
if not exist "models" mkdir models

REM ── 1. SadTalker checkpoints (~1 GB) ───────────────────────────────────────
echo  [1/6] SadTalker checkpoints...
if not exist "models\SadTalker\checkpoints\mapping_00229-model.pth.tar" (
    if exist "venv311\Scripts\python.exe" (
        venv311\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('vinthony/SadTalker-V002rc', local_dir='models/SadTalker/checkpoints', local_dir_use_symlinks=False)" 2>nul
        if errorlevel 1 echo  AVISO: SadTalker download falhou. Tente manualmente.
    )
) else (
    echo  ja baixado.
)

REM ── 2. GFPGAN v1.4 (~330 MB) ─────────────────────────────────────────────
echo  [2/6] GFPGANv1.4.pth...
if not exist "models\SadTalker\checkpoints\GFPGANv1.4.pth" (
    venv311\Scripts\python.exe -c "import urllib.request; urllib.request.urlretrieve('https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth', 'models/SadTalker/checkpoints/GFPGANv1.4.pth')" 2>nul
    if errorlevel 1 echo  AVISO: GFPGAN download falhou.
) else (
    echo  ja baixado.
)

REM ── 3. MuseTalk (~5 GB) ───────────────────────────────────────────────────
echo  [3/6] MuseTalk checkpoints...
if not exist "models\MuseTalk\models\musetalk\musetalk.json" (
    venv311\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('TMElyralab/MuseTalk', local_dir='models/MuseTalk/models', local_dir_use_symlinks=False)" 2>nul
    if errorlevel 1 echo  AVISO: MuseTalk download falhou.
) else (
    echo  ja baixado.
)

REM ── 4. Wav2Lip (~500 MB) ──────────────────────────────────────────────────
echo  [4/6] Wav2Lip checkpoints...
if not exist "models\Wav2Lip\checkpoints\wav2lip_gan.pth" (
    if not exist "models\Wav2Lip\checkpoints" mkdir "models\Wav2Lip\checkpoints"
    venv311\Scripts\python.exe -c "from huggingface_hub import hf_hub_download; hf_hub_download('numz/wav2lip_studio', 'wav2lip_gan.pth', local_dir='models/Wav2Lip/checkpoints')" 2>nul
    if errorlevel 1 echo  AVISO: Wav2Lip download falhou.
) else (
    echo  ja baixado.
)

REM ── 5. Real-ESRGAN x2 (~70 MB) ────────────────────────────────────────────
echo  [5/6] Real-ESRGAN x2plus...
if not exist "models\RealESRGAN_x2plus.pth" (
    venv311\Scripts\python.exe -c "import urllib.request; urllib.request.urlretrieve('https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth', 'models/RealESRGAN_x2plus.pth')" 2>nul
    if errorlevel 1 echo  AVISO: Real-ESRGAN download falhou.
) else (
    echo  ja baixado.
)

REM ── 6. Gesture pack (Pexels CC0 - opcional, ~500 MB) ──────────────────────
echo  [6/6] Gesture pack (opcional, salta sem Pexels API key)...
if not exist "static\gesture_videos\pexels_32113838.mp4" (
    echo  Para baixar o gesture pack, rode: scripts\download_gesture_pack.py --key SUA_KEY
    echo  Cadastro free em https://www.pexels.com/api/
) else (
    echo  ja baixado.
)

echo.
echo  ============================================================
echo   Download de modelos concluido!
echo  ============================================================
echo.
echo  Modelos opcionais que voce pode baixar depois:
echo   - F5-TTS (voice cloning): pip install f5-tts
echo   - CodeFormer (HD enhancer): pip install codeformer-pip
echo   - InsightFace (gesture face swap): pip install insightface
echo.
endlocal
exit /b 0
