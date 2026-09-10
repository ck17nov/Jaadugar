@echo off
setlocal

rem  Jaadugar backend launcher.
rem
rem  Exists because the Android app talking to nothing looks exactly like the
rem  app being broken: the topic list silently falls back to the copy built
rem  into the APK, and the reviewed-script count never answers. Keeping the
rem  backend up removes that whole class of confusion.
rem
rem  The repo root is derived from this file's own location, so moving or
rem  renaming the project folder does not break the scheduled task.

set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1
set "PY=%CD%\.venv\Scripts\python.exe"
set "LOGDIR=%CD%\workspace\logs"
set "LOG=%LOGDIR%\backend.log"

if not exist "%PY%" (
  echo Cannot find the virtualenv python at "%PY%".
  echo Create it with:  python -m venv .venv
  popd
  exit /b 1
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem  RESTART LOOP. A local server that dies at 3am and stays dead is the
rem  failure this is here to prevent. Fifteen seconds between attempts, so a
rem  genuine configuration error does not spin the CPU.
:run
echo. >> "%LOG%"
echo ==== starting %DATE% %TIME% ==== >> "%LOG%"
"%PY%" -m backend.cli serve --host 0.0.0.0 --port 8099 >> "%LOG%" 2>&1
echo ==== exited with code %ERRORLEVEL% at %DATE% %TIME%; retrying in 15s ==== >> "%LOG%"
timeout /t 15 /nobreak > nul
goto run
