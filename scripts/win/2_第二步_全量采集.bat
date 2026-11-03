@echo off
chcp 936 >nul
cd /d "%~dp0..\.."
setlocal
set "PY=D:\python\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   ============================================
echo    寰宇图志 . 第二步：全量采集（3 4 5 6）
echo   ============================================
echo     3 全量采集  4 大模型  5 语音  6 压图+索引
echo     223 个国家，预计 4~7 小时
echo.
echo   * 随时可以关窗口 / Ctrl+C ，重跑本脚本就是接着做
echo   * 想先只跑 10 个验证：在 cmd 里执行
echo       2_全量采集.bat --limit 10
echo.
echo   ============================================
echo.

"%PY%" scripts\step2_full.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo   [有失败项] 退出码 %RC% ，看 logs\采集报告.md
if "%RC%"=="0" echo   [完成] 报告：logs\采集报告.md
echo.
pause
