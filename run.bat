@echo off
REM ---------------------------------------------------------------------
REM  stockforge — double-click this.
REM
REM  Deliberately thin. Everything it does beyond finding Python lives in
REM  tools\launch.py, which updates itself with the rest of the code every
REM  time you pull. A batch file that rewrites itself while cmd.exe is
REM  part-way through reading it misbehaves, so this one stays still.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title stockforge

set "PYEXE="
if exist "%~dp0.venv\Scripts\python.exe" "%~dp0.venv\Scripts\python.exe" -c "pass" >nul 2>&1 && set "PYEXE="%~dp0.venv\Scripts\python.exe""
if not defined PYEXE where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"

if not defined PYEXE (
  echo.
  echo   Python was not found on this machine.
  echo.
  echo   Install it, tick "Add python.exe to PATH" during setup, then run
  echo   this file again:
  echo.
  echo       winget install Python.Python.3.12
  echo.
  pause
  exit /b 1
)

%PYEXE% "%~dp0tools\launch.py" %*
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" (
  echo.
  echo   Stopped with an error ^(code %CODE%^).
  pause
)
exit /b %CODE%
