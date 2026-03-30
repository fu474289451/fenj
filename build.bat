@echo off
chcp 65001 >nul
echo ============================================
echo   视频镜头分镜工具 - 一键打包
echo ============================================
echo.

echo [1/3] 安装打包工具 PyInstaller ...
pip install pyinstaller -q
if errorlevel 1 (
    echo 安装 PyInstaller 失败，请检查网络连接。
    pause
    exit /b 1
)

echo [2/3] 安装项目依赖 ...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo 安装依赖失败，请检查网络连接。
    pause
    exit /b 1
)

echo [3/3] 正在打包为 exe（可能需要1-2分钟）...

REM Find haarcascade xml path from OpenCV
for /f "delims=" %%i in ('python -c "import cv2, os; print(os.path.join(os.path.dirname(cv2.__file__), 'data', 'haarcascade_frontalface_default.xml'))"') do set CASCADE_PATH=%%i

if exist "%CASCADE_PATH%" (
    echo 找到人脸检测模型: %CASCADE_PATH%
    pyinstaller --onefile --windowed ^
        --name "视频镜头分镜工具" ^
        --add-data "%CASCADE_PATH%;." ^
        --hidden-import=numpy ^
        --hidden-import=cv2 ^
        --hidden-import=PIL ^
        --hidden-import=scenedetect ^
        --hidden-import=scenedetect.detectors ^
        --hidden-import=yt_dlp ^
        scene_storyboard.py
) else (
    echo 警告: 未找到人脸检测模型，景别检测功能可能受限。
    pyinstaller --onefile --windowed ^
        --name "视频镜头分镜工具" ^
        --hidden-import=numpy ^
        --hidden-import=cv2 ^
        --hidden-import=PIL ^
        --hidden-import=scenedetect ^
        --hidden-import=scenedetect.detectors ^
        --hidden-import=yt_dlp ^
        scene_storyboard.py
)

if errorlevel 1 (
    echo.
    echo 打包失败，请查看上方错误信息。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成！
echo   exe 文件位于: dist\视频镜头分镜工具.exe
echo   双击即可运行，无需安装 Python。
echo ============================================
echo.

REM Open the dist folder
explorer dist

pause
