@echo off
setlocal
title Game 233 Empire

rem Move to the directory of this script
cd /d "%~dp0"

echo Launching Game 233 Empire...

rem Detect Python launcher or python.exe
set "PYEXE="
py -V >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
  where python >nul 2>&1 && set "PYEXE=python"
)

if not defined PYEXE (
  echo Error: Python was not found on PATH.
  echo Please install Python from https://www.python.org/ and try again.
  pause
  exit /b 1
)

rem Ensure curses is available (windows-curses on Windows)
"%PYEXE%" -c "import curses" >nul 2>&1
if errorlevel 1 (
  echo Installing windows-curses (first run only)...
  "%PYEXE%" -m pip install --user windows-curses
)

rem Run the game
"%PYEXE%" "%~dp0main.py"

echo.
echo (This window will close when you press a key.)
pause >nul


