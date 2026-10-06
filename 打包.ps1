param([string]$BuildDirectory = '', [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = if ($PythonPath) { $PythonPath } else { Join-Path $PSScriptRoot '.runtime\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) {
    py -3.13 -m venv .runtime
    if ($LASTEXITCODE -ne 0) { throw '创建Python环境失败。' }
}
& $taskPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败。' }
if (-not (Test-Path -LiteralPath 'tools\ffmpeg.exe')) { throw '请先把ffmpeg.exe放入tools目录。' }
$taskRelease = Join-Path $PSScriptRoot '发行版\B站视频下载器'
$taskReleaseExe = Join-Path $taskRelease 'B站视频下载器.exe'
if (Get-Process -Name 'B站视频下载器' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskReleaseExe }) {
    throw '请先从下载器托盘菜单中选择“退出程序”，再重新打包。'
}
$taskBuild = if ($BuildDirectory) { [IO.Path]::GetFullPath($BuildDirectory) } else { Join-Path $PSScriptRoot 'build' }
$taskAssets = Join-Path $PSScriptRoot 'assets'
$taskFFmpeg = Join-Path $PSScriptRoot 'tools\ffmpeg.exe'
& $taskPython -m PyInstaller --noconfirm --clean --onedir --windowed --name 'B站视频下载器' --distpath (Join-Path $taskBuild '发行版') --workpath (Join-Path $taskBuild '临时') --specpath $taskBuild --icon (Join-Path $taskAssets 'icon.ico') --add-data "$taskAssets;assets" --add-binary "$taskFFmpeg;tools" (Join-Path $PSScriptRoot 'main.py')
if ($LASTEXITCODE -ne 0) { throw '打包失败，请查看上面的信息。' }
New-Item -ItemType Directory -Path $taskRelease -Force | Out-Null
$taskStagedRelease = Join-Path $taskBuild '发行版\B站视频下载器'
Get-ChildItem -LiteralPath $taskStagedRelease | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $taskRelease -Recurse -Force }
Copy-Item -LiteralPath 'edge-extension' -Destination $taskRelease -Recurse -Force
Copy-Item -LiteralPath '使用说明.md' -Destination $taskRelease -Force
Copy-Item -LiteralPath 'tools\FFmpeg-LICENSE.txt' -Destination $taskRelease -Force
Write-Host "打包完成：$taskRelease"
