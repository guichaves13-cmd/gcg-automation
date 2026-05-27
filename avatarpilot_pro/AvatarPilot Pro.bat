@echo off
chcp 65001 >nul
cd /d "C:\Users\Guilherme\Music\automaçao video\avatarpilot_pro"
start "" "venv311\Scripts\pythonw.exe" server.py
timeout /t 4 /nobreak >nul
start "" "http://localhost:5052"
