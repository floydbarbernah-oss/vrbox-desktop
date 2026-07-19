@echo off
chcp 65001 >nul
title VRBox Desktop server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   ERROR: Python environment was not found.
  echo   Run these commands in this project folder:
  echo     py -3.14 -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo   VRBox Desktop
echo   ==============
echo   Stopping the previous VRBox server, if it is running...
powershell -NoProfile -Command "$python = [IO.Path]::GetFullPath('%~dp0.venv\Scripts\python.exe'); Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and $_.CommandLine.Contains($python) -and $_.CommandLine -match 'pc[\\/]server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo   Starting the server. Keep this window open.
echo.
".venv\Scripts\python.exe" -u "pc\server.py"
set "SERVER_EXIT=%ERRORLEVEL%"

echo.
echo   Server stopped with exit code %SERVER_EXIT%.
echo   Press any key to close this window.
pause >nul
exit /b %SERVER_EXIT%
