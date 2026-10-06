$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    py -3.13 -m venv .runtime
    if ($LASTEXITCODE -ne 0) { throw '创建Python环境失败。' }
}
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败。' }
if (-not (Test-Path -LiteralPath 'tools\ffmpeg.exe')) { throw '请先把ffmpeg.exe放入tools目录。' }
& $taskPython -m PyInstaller --noconfirm --clean --onedir --windowed --name 'B站视频下载器' --distpath '发行版' --icon 'assets\icon.ico' --add-data 'assets;assets' --add-binary 'tools\ffmpeg.exe;tools' main.py
if ($LASTEXITCODE -ne 0) { throw '打包失败，请查看上面的信息。' }
$taskRelease = Join-Path $PSScriptRoot '发行版\B站视频下载器'
Copy-Item -LiteralPath 'edge-extension' -Destination $taskRelease -Recurse -Force
Copy-Item -LiteralPath '使用说明.md' -Destination $taskRelease -Force
Copy-Item -LiteralPath 'tools\FFmpeg-LICENSE.txt' -Destination $taskRelease -Force
Write-Host "打包完成：$taskRelease"
