@echo off
chcp 65001 >nul
echo ========================================================
echo   CT Dose 輻射劑量自動化評估系統 - Windows 一鍵打包工具
echo ========================================================
echo.

:: 檢查是否有 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 Python！請先在 Windows 上安裝 Python 3.9+ 並勾選 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

echo [1/3] 正在安裝必要套件 (pydicom, pandas, matplotlib, pyinstaller)...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo [2/3] 開始使用 PyInstaller 打包為獨立執行檔 (CTDose.exe)...
pyinstaller --clean -y reader.spec

if %errorlevel% neq 0 (
    echo.
    echo [錯誤] 打包過程發生錯誤，請檢查上方訊息。
    pause
    exit /b 1
)

echo.
echo [3/3] 打包完成！
echo.
echo ========================================================
echo   產生的執行檔位置: dist\CTDose.exe
echo   您可以將 dist\CTDose.exe 複製到任何 Windows 電腦直接執行！
echo ========================================================
echo.
pause
