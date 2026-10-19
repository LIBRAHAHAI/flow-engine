@echo off
setlocal enabledelayedexpansion
title Flow Engine

rem ============================================================
rem  Flow Engine one-click launcher (toolkit layout).
rem  - Relative paths only (%~dp0); works from any install drive.
rem  - Keep this file pure ASCII + CRLF (cmd requirement).
rem  - Python discovery order:
rem      1. portable venv  %~dp0panel\backend\.venv\Scripts\python.exe
rem      2. py -3 launcher
rem      3. python on PATH
rem      4. uv-managed CPython under %LocalAppData% / %AppData%
rem  - First run: missing deps trigger an automatic portable venv
rem    bootstrap (PEP 668 safe - never pip-installs into system/uv
rem    managed interpreters directly).
rem ============================================================

set "BACKEND_DIR=%~dp0panel\backend"
set "URL=http://localhost:8650"
set "LOGFILE=%BACKEND_DIR%\api.log"
set "PYEXE="

rem portable venv next to the app (bootstrap target, checked first)
set "VENV_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"

rem -- 1) portable venv --
if exist "%VENV_PY%" set "PYEXE=%VENV_PY%"

rem -- 2) py launcher --
if not defined PYEXE (
    py -3 -c "print(1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3"
)

rem -- 3) python on PATH --
if not defined PYEXE (
    python -c "print(1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)

rem -- 4) uv-managed CPython, prefer 3.11 then any 3.x (%LocalAppData% then %AppData%) --
if not defined PYEXE (
    for /d %%D in ("%LocalAppData%\uv\python\cpython-3.11*-windows-x86_64-none") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)
if not defined PYEXE (
    for /d %%D in ("%AppData%\uv\python\cpython-3.11*-windows-x86_64-none") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)
if not defined PYEXE (
    for /d %%D in ("%LocalAppData%\uv\python\cpython-3.*-windows-x86_64-none") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)
if not defined PYEXE (
    for /d %%D in ("%AppData%\uv\python\cpython-3.*-windows-x86_64-none") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)

if not defined PYEXE (
    echo [Flow Engine] ERROR: no Python found.
    echo   Install Python 3.11+ and re-run, or create a venv at:
    echo   %BACKEND_DIR%\.venv
    pause
    exit /b 1
)
echo [Flow Engine] Using python: %PYEXE%

rem -- deps: bootstrap portable venv once if missing (PEP 668 safe) --
%PYEXE% -c "import fastapi, uvicorn, yaml" >nul 2>&1
if errorlevel 1 (
    echo [Flow Engine] First run: bootstrapping portable venv ...
    %PYEXE% -m venv "%BACKEND_DIR%\.venv"
    if errorlevel 1 (
        echo [Flow Engine] ERROR: venv creation failed.
        pause
        exit /b 1
    )
    set "PYEXE=%VENV_PY%"
    "!PYEXE!" -m pip install --quiet --disable-pip-version-check fastapi uvicorn pyyaml
    if errorlevel 1 (
        echo [Flow Engine] ERROR: dependency install failed. Check network/proxy.
        pause
        exit /b 1
    )
    echo [Flow Engine] Portable venv ready.
)

echo [Flow Engine] Checking port 8650 ...
netstat -ano | findstr :8650 | findstr LISTENING >nul
if not errorlevel 1 (
    echo [Flow Engine] Port already in use - instance seems running.
    echo [Flow Engine] Opening panel: %URL%
    start "" %URL%
    exit /b 0
)

echo [Flow Engine] Starting backend API + panel on single port ...
cd /d "%BACKEND_DIR%"
start "flow-engine-api" /min cmd /c ""!PYEXE!" api.py > "%LOGFILE%" 2>&1"

echo [Flow Engine] Waiting for service ready ...
set /a TRIES=0
:waitloop
timeout /t 2 /nobreak >nul
curl -s -o nul %URL%/api/health
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% lss 15 goto waitloop
echo [Flow Engine] Start timeout after 30s. Check log: %LOGFILE%
pause
exit /b 1

:ready
echo [Flow Engine] Ready: %URL%
echo [Flow Engine] Log: %LOGFILE%
start "" %URL%
exit /b 0
