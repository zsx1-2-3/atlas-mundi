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
echo    寰宇图志 · 按清单补齐地标图（中国 / 法国 / 美国）
echo ==========================================================
echo.
echo    每国 4 处地标，按 data\landmarks.json 配置的顺序取图：
echo      中国：长城 / 故宫太和殿 / 兵马俑 / 布达拉宫
echo      法国：埃菲尔铁塔 / 卢浮宫金字塔 / 圣米歇尔山 / 凡尔赛镜厅
echo      美国：自由女神像 / 大峡谷 / 金门大桥 / 白宫
echo.
echo    请确认代理已开启。
echo.
pause

"!PY!" "scripts\fill_images.py" CN FR US

echo.
echo ==========================================================
echo    结束。把输出发给助手，助手接着压图、重算索引、推送。
echo ==========================================================
echo.
pause
