@echo off
chcp 65001 >nul
title mookquant launcher
cd /d "%~dp0"

echo ===============================================
echo   mookquant · mookquant quick launcher
echo ===============================================
echo.

REM 1) 依赖检查
if not exist "node_modules\electron\dist\electron.exe" (
  echo [INFO] electron not installed, running npm install ...
  call npm install
)

REM 2) 启动参数透传
set "EXTRA=%*"

REM 3) 如果传入 --mock 就临时改 config
if /i "%1"=="--mock" (
  echo [INFO] switching to mock data source ...
  powershell -Command "(Get-Content config\default.json) -replace '\"dataSource\":\s*\"[^\"]*\"', '\"dataSource\": \"mock\"' | Set-Content config\default.json"
  set "EXTRA="
)

echo.
echo [INFO] starting electron ...
echo ===============================================

npx electron . %EXTRA%