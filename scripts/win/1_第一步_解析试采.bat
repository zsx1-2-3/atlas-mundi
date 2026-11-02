@echo off
chcp 936 >nul
cd /d "%~dp0..\.."
setlocal
set "PY=D://python//python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   ============================================
echo    寰宇图志 . 第一步：解析 + 试采 5 国
echo   ============================================
echo     0 体检（含代理探测）  1 解析 QID
echo     2 试采正文/图片      2b 地标图（模型点名 + Commons 实搜）
echo     3 生成审核页并自动打开
echo.
echo     大约 15~30 分钟（取决于网络）
echo     卡住/失败时先看 logs 目录，再决定要不要重跑
echo   ============================================
echo.

"%PY%" scripts\step1_preview.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo   [未全部通过] 退出码 %RC% ，看上面的提示。
if "%RC%"=="0" echo   [完成] 审核页已经自动打开，看完再双击 2_第二步_全量采集.bat
echo.
pause
