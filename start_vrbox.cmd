@echo off
chcp 65001 >nul
title VRBox Desktop server
cd /d "%~dp0"

echo(
echo   VRBox Desktop
echo   ==============
echo   Останавливаю старый сервер, если он уже запущен...
REM Гасим только СВОЙ сервер: сверяем по полному пути к pc\server.py этой папки,
REM иначе под фильтр 'server.py' попадёт любой чужой python-процесс.
powershell -NoProfile -Command "$self = Join-Path '%~dp0' 'pc\server.py'; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($self) } | ForEach-Object { Stop-Process $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo   Запускаю сервер (ссылку на телефон смотри ниже в баннере)...
echo(
".venv\Scripts\python.exe" "pc\server.py"

echo(
echo   Сервер остановлен. Нажми любую клавишу, чтобы закрыть окно.
pause >nul
