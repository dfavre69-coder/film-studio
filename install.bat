@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Installation de Film Studio

echo.
echo  ==============================================
echo     Film Studio - installation
echo  ==============================================
echo.

rem ---------------------------------------------------------------
rem 1. Trouver Python 3.10 ou plus recent (sinon l'installer)
rem ---------------------------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [1/6] Python introuvable : installation de Python 3.12 avec winget...
    where winget >nul 2>nul
    if errorlevel 1 goto :sans_winget
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
    )
)
if not defined PY goto :sans_winget
echo [1/6] Python trouve :
%PY% --version

rem ---------------------------------------------------------------
rem 2. Environnement isole (.venv) pour ne rien toucher au systeme
rem ---------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/6] Creation de l'environnement .venv...
    %PY% -m venv .venv
    if errorlevel 1 goto :erreur
) else (
    echo [2/6] Environnement .venv deja present.
)

rem ---------------------------------------------------------------
rem 3. Dependances (Pillow, ffmpeg)
rem ---------------------------------------------------------------
echo [3/6] Installation des dependances...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 goto :erreur
".venv\Scripts\python.exe" -c "import tkinter, PIL, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()"
if errorlevel 1 goto :erreur

rem ---------------------------------------------------------------
rem 4. Raccourcis sur le Bureau et dans le menu Demarrer
rem ---------------------------------------------------------------
echo [4/6] Creation des raccourcis...
set "DOSSIER=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d = $env:DOSSIER; $w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($cible in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $cible 'Film Studio.lnk'));" ^
  "  $s.TargetPath = (Join-Path $d '.venv\Scripts\pythonw.exe');" ^
  "  $s.Arguments = '-m filmstudio';" ^
  "  $s.WorkingDirectory = $d;" ^
  "  $s.IconLocation = (Join-Path $d 'filmstudio\assets\icone.ico');" ^
  "  $s.Description = 'Courts-metrages d''animation par IA';" ^
  "  $s.Save() }"

rem ---------------------------------------------------------------
rem 5. Claude et ChatGPT avec l'abonnement (facultatif, sans cle API)
rem ---------------------------------------------------------------
echo.
echo [5/6] Claude et ChatGPT avec votre abonnement (facultatif, sans cle API)
echo       - Claude Code ecrit le decoupage du film (abonnement Claude Pro ou Max)
echo       - Codex dessine les personnages, les lieux et les premieres images (abonnement ChatGPT)
call :claude_code
call :codex

rem ---------------------------------------------------------------
rem 6. ComfyUI (rendu des videos sur la carte graphique)
rem ---------------------------------------------------------------
echo.
echo [6/6] ComfyUI (rendu des videos sur votre carte graphique)
echo       Film Studio le demarre et l'arrete tout seul quand il en a besoin.
".venv\Scripts\python.exe" -m filmstudio installer-comfy --verifier >nul 2>nul
if not errorlevel 1 (
    echo   ComfyUI est deja installe.
    goto :comfy_modeles
)
choice /c ON /n /m "  Installer ComfyUI automatiquement (environ 10 Go, 10 a 20 minutes) ? [O/N] "
if errorlevel 2 goto :comfy_fin
".venv\Scripts\python.exe" -m filmstudio installer-comfy
if errorlevel 1 goto :comfy_fin

:comfy_modeles
".venv\Scripts\python.exe" -m filmstudio telecharger-modeles --verifier >nul 2>nul
if not errorlevel 1 (
    echo   Les modeles LTX-2.5 sont deja la.
    goto :comfy_workflow
)
choice /c ON /n /m "  Telecharger les modeles video LTX-2.5 (plusieurs dizaines de Go) ? [O/N] "
if errorlevel 2 goto :comfy_fin
".venv\Scripts\python.exe" -m filmstudio telecharger-modeles
if errorlevel 1 goto :comfy_fin

:comfy_workflow
".venv\Scripts\python.exe" -m filmstudio installer-workflow --verifier >nul 2>nul
if not errorlevel 1 (
    echo   Le workflow video est deja pret.
    goto :comfy_fin
)
echo.
echo   Preparation du workflow video officiel "LTX-2.5 Image to Video"
echo   (ComfyUI demarre une minute puis s'arrete tout seul)...
".venv\Scripts\python.exe" -m filmstudio installer-workflow
if not errorlevel 1 goto :comfy_fin
echo   Le workflow n'a pas pu etre prepare automatiquement : voir docs\GUIDE.md
echo   (import a la main depuis ComfyUI).

:comfy_fin

echo.
echo  Installation terminee !
echo  Un raccourci "Film Studio" est sur le Bureau.
echo.
choice /c ON /n /m "Lancer Film Studio maintenant ? [O/N] "
if errorlevel 2 goto :fin
start "" ".venv\Scripts\pythonw.exe" -m filmstudio
goto :fin

:claude_code
set "CLAUDE="
where claude >nul 2>nul && set "CLAUDE=claude"
if not defined CLAUDE if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if defined CLAUDE goto :claude_verifier
echo.
echo   Claude Code n'est pas installe.
choice /c ON /n /m "  L'installer maintenant ? [O/N] "
if errorlevel 2 goto :claude_ignore
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if not defined CLAUDE where claude >nul 2>nul && set "CLAUDE=claude"
if defined CLAUDE goto :claude_verifier
echo   L'installation de Claude Code a echoue. Voir https://code.claude.com/docs
goto :claude_ignore

:claude_verifier
echo   Verification de Claude Code (une petite question de test)...
".venv\Scripts\python.exe" -m filmstudio verifier-ia --outil claude
if not errorlevel 1 exit /b 0
echo.
echo   Claude Code doit etre connecte a votre abonnement Claude.
choice /c ON /n /m "  Le connecter maintenant ? [O/N] "
if errorlevel 2 goto :claude_ignore
echo.
echo   Claude Code va s'ouvrir dans cette fenetre :
echo     1. choisissez la connexion avec votre compte Claude (abonnement)
echo     2. validez dans le navigateur qui s'ouvre
echo     3. de retour ici, tapez /exit puis Entree
echo.
pause
call "%CLAUDE%"
".venv\Scripts\python.exe" -m filmstudio verifier-ia --outil claude
if not errorlevel 1 exit /b 0
echo   Claude Code n'est toujours pas connecte : relancez install.bat plus tard.
exit /b 0

:claude_ignore
echo   Sans Claude Code, Ollama et le copier-coller du prompt restent disponibles.
exit /b 0

:codex
set "PATH=%PATH%;%ProgramFiles%\nodejs;%APPDATA%\npm"
set "CODEX="
where codex >nul 2>nul && set "CODEX=codex"
if not defined CODEX if exist "%APPDATA%\npm\codex.cmd" set "CODEX=%APPDATA%\npm\codex.cmd"
if defined CODEX goto :codex_verifier
echo.
echo   Codex (ChatGPT) n'est pas installe.
choice /c ON /n /m "  L'installer maintenant ? [O/N] "
if errorlevel 2 goto :codex_ignore
where npm >nul 2>nul
if not errorlevel 1 goto :codex_npm
echo   Node.js est necessaire : installation avec winget...
where winget >nul 2>nul
if errorlevel 1 goto :codex_sans_node
winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
where npm >nul 2>nul
if errorlevel 1 goto :codex_sans_node

:codex_npm
call npm install -g @openai/codex
where codex >nul 2>nul && set "CODEX=codex"
if not defined CODEX if exist "%APPDATA%\npm\codex.cmd" set "CODEX=%APPDATA%\npm\codex.cmd"
if defined CODEX goto :codex_verifier
echo   L'installation de Codex a echoue (voir les messages ci-dessus).
goto :codex_ignore

:codex_sans_node
echo   Node.js n'a pas pu etre installe automatiquement : https://nodejs.org
goto :codex_ignore

:codex_verifier
echo   Verification de Codex...
".venv\Scripts\python.exe" -m filmstudio verifier-ia --outil codex
if not errorlevel 1 exit /b 0
echo.
echo   Codex doit etre connecte a votre compte ChatGPT.
choice /c ON /n /m "  Le connecter maintenant ? [O/N] "
if errorlevel 2 goto :codex_ignore
echo   Une page de connexion ChatGPT va s'ouvrir dans le navigateur.
call "%CODEX%" login
".venv\Scripts\python.exe" -m filmstudio verifier-ia --outil codex
if not errorlevel 1 exit /b 0
echo   Codex n'est toujours pas connecte : relancez install.bat plus tard.
exit /b 0

:codex_ignore
echo   Sans Codex, vous pouvez dessiner les images avec ComfyUI ou importer les votres.
exit /b 0

:sans_winget
echo.
echo  Python 3.10+ est necessaire et n'a pas pu etre installe automatiquement.
echo  La page de telechargement va s'ouvrir : installez Python en cochant
echo  "Add python.exe to PATH", puis relancez install.bat.
start "" https://www.python.org/downloads/windows/
pause
exit /b 1

:erreur
echo.
echo  L'installation a echoue (voir les messages ci-dessus).
echo  Verifiez la connexion Internet puis relancez install.bat.
pause
exit /b 1

:fin
endlocal
exit /b 0
