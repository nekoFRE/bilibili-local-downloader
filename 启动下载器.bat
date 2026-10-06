@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "BILI_EXE=%~dp0发行版\B站视频下载器\B站视频下载器.exe"
if not exist "%BILI_EXE%" goto source
start "" "%BILI_EXE%"
if errorlevel 1 goto failed
exit /b 0

:source
if not exist "%~dp0.runtime\Scripts\pythonw.exe" goto missing
start "" "%~dp0.runtime\Scripts\pythonw.exe" "%~dp0main.py"
if errorlevel 1 goto failed
exit /b 0

:missing
echo 找不到发行版程序，请先运行“打包.bat”或恢复完整的发行版文件夹。
pause
exit /b 1

:failed
echo 启动失败。请尝试直接打开“发行版\B站视频下载器\B站视频下载器.exe”。
pause
exit /b 1
