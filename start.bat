@echo off
chcp 65001 >nul
echo Starting FunClip...
.venv\Scripts\python.exe funclip/launch.py %*
