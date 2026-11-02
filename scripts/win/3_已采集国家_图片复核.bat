@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0..\.."

echo.
echo ============================================================
echo   寰宇图志 · 已采集国家图片复核
echo ============================================================
echo.
echo   让大模型逐张看图，把"尺寸比例都合规、但画面上根本不是
echo   这处地标"的图挑出来，从候选池换一张合格的顶上；
echo   图片数量不够的，顺便补齐。
echo.
echo   本批：巴西 不丹 中国 埃及 法国 冰岛 日本 肯尼亚 基里巴斯 挪威 美国
echo   花费：约 0.05 元（预算上限 2 元，超了自动停）
echo   耗时：1~2 分钟
echo.
echo   验过的图有缓存，反复跑不会重复花钱。
echo ------------------------------------------------------------
echo.

set "PY=D:\python\python.exe"
if not exist "%PY%" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [失败] 找不到 Python。
    echo     装的路径不是 D:\python\python.exe，PATH 里也没有 python。
    echo     请把 D:\python\python.exe 改成你自己的路径，或者装个 Python。
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
)

if not exist ".env" (
  echo [注意] 没找到 .env 文件，需要里面的 DEEPSEEK_API_KEY。
  echo     在项目根目录建一个 .env，内容一行：
  echo     DEEPSEEK_API_KEY=sk-你 的 密 钥
  echo.
  pause
  exit /b 1
)

echo [1/3] 视觉复核中……（这一步要花几毛钱，慢慢看图）
echo.
"%PY%" scripts\verify_images.py --only BR BT CN EG FR IS JP KE KI NO US --budget 2 --max-rounds 2
set "RC=%ERRORLEVEL%"
echo.

if not "%RC%"=="0" (
  echo [注意] 复核脚本返回了错误码 %RC%，先别继续压图。
  echo     常见原因：.env 里的 key 过期、网络断了、没有余额。
  echo.
  pause
  exit /b %RC%
)

echo [2/3] 压图（原图转 WebP + 缩略图）
echo.
"%PY%" scripts\optimize_images.py --all --prune
echo.

echo [3/3] 同步索引
echo.
"%PY%" scripts\sync_index.py
echo.

echo ============================================================
echo   完成。图片有改动的国家：
echo   - 详情面板里的缩略图会立刻是新图（本地文件已换）
echo   - 想上线的话，需要 git 提交并推送
echo ============================================================
echo.
pause
