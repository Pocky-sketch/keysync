@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ======================================
echo  按键同步 - 构建脚本
echo ======================================

echo.
echo [1/2] 安装依赖...
python -m pip install -r requirements.txt --quiet

echo [2/2] 打包中...
python -m PyInstaller KeySync.spec --clean --noconfirm

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ======================================
    echo  构建成功！
    echo  输出: dist\按键同步\按键同步.exe
    echo ======================================
) else (
    echo.
    echo 构建失败，请检查错误。
)
pause
