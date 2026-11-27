@echo off
cd /d "%~dp0"
title ComfyUI (demarre par Film Studio)
if not exist ".venv\Scripts\python.exe" (
    echo Lancez d'abord install.bat.
    pause
    exit /b 1
)
echo ComfyUI demarre... la page s'ouvrira dans le navigateur quand il sera pret.
echo Fermez cette fenetre (ou Ctrl+C) pour arreter ComfyUI.
".venv\Scripts\python.exe" -m filmstudio demarrer-comfy --ouvrir
pause
