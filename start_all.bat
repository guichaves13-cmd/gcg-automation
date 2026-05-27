@echo off
title GCG Video Suite — All Services
echo ============================================
echo   GCG Video Suite — Starting All Services
echo ============================================
echo.

echo [1/3] Starting TitlePilot Pro (port 5050)...
start /min python "%~dp0titlepilot_pro\server.py"
timeout /t 2 /nobreak >nul

echo [2/3] Starting StudioPilot Web (port 5051)...
start /min python "%~dp0studiopilot_web\server.py"
timeout /t 2 /nobreak >nul

echo [3/3] Starting AvatarPilot Pro (port 5052)...
cd /d "%~dp0avatarpilot_pro"
if exist "venv311\Scripts\activate.bat" (
    call venv311\Scripts\activate.bat
)
start /min python server.py
cd /d "%~dp0"
timeout /t 3 /nobreak >nul

echo.
echo ============================================
echo   All services started!
echo.
echo   TitlePilot Pro:  http://localhost:5050
echo   StudioPilot Web: http://localhost:5051
echo   AvatarPilot Pro: http://localhost:5052
echo ============================================
echo.
echo Opening StudioPilot in browser...
start http://localhost:5051
pause
