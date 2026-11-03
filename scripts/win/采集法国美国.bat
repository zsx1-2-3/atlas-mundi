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
echo    寰宇图志 · 构建期采集
echo    目标：法国 FR + 美国 US
echo    Python：!PY!
echo ==========================================================
echo.
echo    开始前请确认代理已开启，浏览器能打开 commons.wikimedia.org
echo.
echo    跑两步：先法国，再美国。任何一国失败不影响另一国。
echo.
pause

echo.
echo ----------------------------------------------------------
echo   [1/2] 法国 FR
echo ----------------------------------------------------------
"!PY!" "scripts\collect.py" FR

echo.
echo ----------------------------------------------------------
echo   [2/2] 美国 US
echo ----------------------------------------------------------
"!PY!" "scripts\collect.py" US

echo.
echo ==========================================================
echo    全部结束。请把上面两段输出（尤其「待复核」）发给助手。
echo ==========================================================
echo.
pause
