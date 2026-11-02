@echo off
cd /d "%~dp0..\.."
setlocal enabledelayedexpansion

set "PY="
if exist "D:\python\python.exe" set "PY=D:\python\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  echo.
  echo [错误] 没有找到 Python。请先安装 Python 3.8+ 并勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo.
echo ==========================================================
echo    寰宇图志 · 单个国家重新采集
echo ==========================================================
echo.
set "ISO="
set /p "ISO=请输入国家代码（如 US / FR / DE），直接回车默认 US："
if not defined ISO set "ISO=US"

echo.
echo    目标：!ISO!
echo    请确认代理已开启。
echo.
pause

"!PY!" "scripts\collect.py" "!ISO!"

echo.
echo ==========================================================
echo    采集结束。把输出发给助手，助手会接着压图、生成语音、推送。
echo ==========================================================
echo.
pause
