@echo off
setlocal
cd /d "%~dp0"
title Invoice Extractor

echo.
echo  ==============================================
echo    Invoice Extractor - starting up
echo  ==============================================
echo.

REM ---- 1. Find a Python that works (3.11 to 3.14) ----
set "PY="
for %%V in (3.14 3.13 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo  [PROBLEM] I couldn't find Python 3.11 - 3.14 on this computer.
  echo.
  echo  Fix: install Python 3.13 from https://www.python.org/downloads/windows/
  echo       Choose "Windows installer (64-bit)" under Python 3.13, and on the
  echo       first screen of the installer TICK "Add python.exe to PATH".
  echo       Then double-click this file again.
  echo.
  pause
  exit /b 1
)
echo  Using Python: %PY%

REM ---- 2. One-time setup: a private Python environment for this project ----
if not exist ".venv\Scripts\python.exe" (
  echo  First-time setup: creating a private environment for this project...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

REM ---- 3. Install / check the required packages ----
echo  Checking required packages. The first time takes a few minutes...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

REM ---- 4. Skip Streamlit's one-time "enter your email" question ----
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" >nul 2>&1
  >  "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

REM ---- 5. Start the app ----
echo.
echo  Starting the app. Your web browser will open in a few seconds.
echo  KEEP THIS WINDOW OPEN while you use the app. Close it to stop the app.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo  [PROBLEM] Something went wrong - see the messages above.
echo  Tip: press Ctrl+A then Ctrl+C to copy this window, and paste it to Claude.
echo.
pause
exit /b 1
