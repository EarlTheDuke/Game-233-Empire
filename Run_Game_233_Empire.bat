@echo off
setlocal EnableExtensions
title Game 233 Empire

rem Move to the directory of this script
cd /d "%~dp0"

set "LOG=%~dp0last_run.log"
echo [%DATE% %TIME%] Launching Game 233 Empire... > "%LOG%"
echo Launching Game 233 Empire...

rem Detect Python launcher or python.exe
set "PYEXE="
py -V >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"

if not defined PYEXE (
  echo Error: Python was not found on PATH. >> "%LOG%"
  echo Error: Python was not found on PATH.
  echo Please install Python from https://www.python.org/ and try again.
  echo See log at: "%LOG%"
  goto :END
)

echo Using Python: %PYEXE% >> "%LOG%"

rem Ensure curses is available (windows-curses on Windows)
"%PYEXE%" -c "import curses" >nul 2>&1 || (
  echo Installing windows-curses... >> "%LOG%"
  "%PYEXE%" -m pip install --user windows-curses >> "%LOG%" 2>&1
)

rem Run the game and log output
echo Running main.py ... >> "%LOG%"
"%PYEXE%" "%~dp0main.py" >> "%LOG%" 2>&1
set "ERR=%ERRORLEVEL%"
echo Game exited with code %ERR% >> "%LOG%"

type "%LOG%"

:END
echo.
echo Press any key to close this window...
pause >nul


