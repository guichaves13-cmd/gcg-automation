@echo off
title Radar Pro - Mapa de Canais do YouTube
cd /d "%~dp0"
echo ================================================
echo   RADAR PRO  -  iniciando servidor (porta 5051)
echo ================================================
set PY=C:\Python314\python.exe
if not exist "%PY%" set PY=python
start "" http://127.0.0.1:5051
"%PY%" app.py
pause
