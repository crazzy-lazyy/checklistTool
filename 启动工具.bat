@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先启动打包版（免安装目录），其次单文件版，否则退回开发模式
if exist "dist\ChecklistTool\ChecklistTool.exe" (
    start "" "dist\ChecklistTool\ChecklistTool.exe"
    exit /b 0
)
if exist "dist\ChecklistTool.exe" (
    start "" "dist\ChecklistTool.exe"
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe main.py
) else (
    python main.py
)
pause
