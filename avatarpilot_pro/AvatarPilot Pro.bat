@echo off
cd /d "C:\Users\Guilherme\Music\automa?ao video\avatarpilot_pro"
start "" "venv311\Scripts\pythonw.exe" server.py
timeout /t 3 /nobreak >nul
start "" "http://localhost:5052"
