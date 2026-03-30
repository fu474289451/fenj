@echo off
chcp 65001 >nul
echo ============================================
echo   视频镜头分镜工具 - 一键打包
echo   使用国内镜像源加速下载
echo ============================================
echo.

set MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

echo [1/3] 安装打包工具 PyInstaller ...
pip install pyinstaller %MIRROR%
if errorlevel 1 (
    echo 清华源失败，尝试阿里源...
    pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
)
if errorlevel 1 (
    echo.
    echo 安装 PyInstaller 失败。请手动执行：
    echo   pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo [2/3] 安装项目依赖 ...
pip install -r requirements.txt %MIRROR%
if errorlevel 1 (
    pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
)
if errorlevel 1 (
    echo 安装依赖失败。
    pause
    exit /b 1
)

echo [3/3] 正在打包为 exe（需要2-5分钟，请耐心等待）...

REM Find haarcascade xml path from OpenCV
for /f "delims=" %%i in ('python -c "import cv2, os; print(os.path.join(os.path.dirname(cv2.__file__), 'data', 'haarcascade_frontalface_default.xml'))"') do set CASCADE_PATH=%%i

if exist "%CASCADE_PATH%" (
    echo 找到人脸检测模型: %CASCADE_PATH%
    python -m PyInstaller --onefile --windowed ^
        --name "视频镜头分镜工具" ^
        --add-data "%CASCADE_PATH%;." ^
        --hidden-import=numpy ^
        --hidden-import=numpy.core ^
        --hidden-import=cv2 ^
        --hidden-import=PIL ^
        --hidden-import=PIL.Image ^
        --hidden-import=scenedetect ^
        --hidden-import=scenedetect.detectors ^
        --hidden-import=scenedetect.detectors.content_detector ^
        --hidden-import=scenedetect.detectors.adaptive_detector ^
        --hidden-import=yt_dlp ^
        --hidden-import=yt_dlp.extractor ^
        --hidden-import=yt_dlp.downloader ^
        scene_storyboard.py
) else (
    echo 警告: 未找到人脸检测模型，景别检测功能可能受限。
    python -m PyInstaller --onefile --windowed ^
        --name "视频镜头分镜工具" ^
        --hidden-import=numpy ^
        --hidden-import=numpy.core ^
        --hidden-import=cv2 ^
        --hidden-import=PIL ^
        --hidden-import=PIL.Image ^
        --hidden-import=scenedetect ^
        --hidden-import=scenedetect.detectors ^
        --hidden-import=scenedetect.detectors.content_detector ^
        --hidden-import=scenedetect.detectors.adaptive_detector ^
        --hidden-import=yt_dlp ^
        --hidden-import=yt_dlp.extractor ^
        --hidden-import=yt_dlp.downloader ^
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
echo.
echo   exe 文件位于: dist\视频镜头分镜工具.exe
echo   双击即可运行，无需安装 Python。
echo ============================================
echo.

REM Open the dist folder
explorer dist

pause
