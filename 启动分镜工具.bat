@echo off
chcp 65001 >nul
cd /d "%~dp0"
pythonw scene_storyboard.py
if errorlevel 1 (
    python scene_storyboard.py
    pause
)
