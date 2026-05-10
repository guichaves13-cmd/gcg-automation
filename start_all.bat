@echo off
echo ============================================
echo   GCG Automation Suite - Starting All
echo ============================================

echo Starting TitlePilot Pro (port 5050)...
start /MIN python "%~dp0titlepilot_pro\server.py"

echo Starting VideosMAX (port 5051)...
start /MIN python "%~dp0studiopilot_web\server.py"

echo Starting AvatarPilot Pro (port 5052)...
start /MIN python "%~dp0avatarpilot_pro\server.py"

echo.
echo All servers starting...
timeout /t 5 /nobreak >nul
echo.
echo TitlePilot:  http://localhost:5050
echo VideosMAX:   http://localhost:5051
echo AvatarPilot: http://localhost:5052
echo.
pause
