@echo off
setlocal
rem Flow Engine stopper: kills the process listening on port 8650.
rem Keep this file pure ASCII + CRLF (cmd requirement).

set "PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8650 ^| findstr LISTENING') do set "PID=%%p"

if not defined PID (
    echo [Flow Engine] No listener on 8650 - nothing to stop.
    exit /b 0
)

echo [Flow Engine] Stopping PID %PID% (port 8650) ...
taskkill /f /pid %PID% >nul 2>&1
if not errorlevel 1 (
    echo [Flow Engine] Stopped.
) else (
    echo [Flow Engine] Stop failed - insufficient permission?
)
pause
