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
echo    寰宇图志 · 下载地标候选图（每处最多 4 张）
echo ==========================================================
echo.
echo    这次不直接定图，只把候选都抓到 review\candidates\ 留档：
echo      候选来源优先级：Wikidata 代表图 ^> 中文维基词条首图 ^> Commons 关键词
echo    抓完之后由助手看图挑一张，再本地组装，不用你再跑。
echo.
echo    请确认代理已开启。
echo.
pause

"!PY!" "scripts\pick_candidates.py" --per 4

echo.
echo ==========================================================
echo    抓取结束。请把上面的输出发给助手。
echo ==========================================================
echo.
pause
