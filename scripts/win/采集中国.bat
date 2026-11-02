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
  echo [错误] 没有找到 Python。
  echo        请先安装 Python 3.8 或更高版本，安装时勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo.
echo ==========================================================
echo    寰宇图志 · Atlas Mundi   构建期采集
echo    目标：中国（ISO 3166-1 alpha-2 = CN）
echo    Python：!PY!
echo ==========================================================
echo.
echo    开始前请确认：
echo      1. 代理 / 科学上网已开启（否则维基媒体全部不可达）
echo      2. 浏览器能打开 https://commons.wikimedia.org
echo.
echo    本步会做的事：
echo      Wikimedia 系 -^> 取 ISO / 坐标 / 首都 / 语言 / 图片 / 真实授权
echo      Wikipedia   -^> 取中文摘要并压到 300~500 字
echo      Commons     -^> 把图片和发音下载到 assets\cn\
echo.
pause

"!PY!" "scripts\collect.py" CN

echo.
echo ==========================================================
echo    采集结束。上面输出的「待复核」里逐条列了缺口。
echo    把结果告诉助手，由助手核对 JSON 并接前端。
echo ==========================================================
echo.
pause
