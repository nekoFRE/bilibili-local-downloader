# B站视频下载器

一个简单的Windows桌面下载器，配合原生JavaScript Edge扩展使用。
项目仓库：https://github.com/nekoFRE/bilibili-local-downloader （私有）。
支持自选目录、画质和分P选择、收藏夹队列、FFmpeg合并及本机任务连接。
Edge扩展支持视频页悬浮下载面板和操作栏按钮，桌面程序支持托盘后台下载；1.4.0可在下载时自动后台唤醒桌面端，无需每次手动打开。保留工具栏Off快捷开关、收藏夹分P识别、Edge登录同步和收藏夹高级筛选。

日常使用请打开本地发行版中的程序；完整操作见[使用说明](使用说明.md)。

源码主要为 `main.py`、`ui.py`、`downloader.py`、`tasks.py`，扩展在 `edge-extension`。
`browser_bridge.py` 是仅负责启动桌面程序的Edge原生通信助手；首次保存连接码时自动注册到当前Windows用户，无需管理员权限。
重新打包需要Python 3.13，并将FFmpeg的Windows可执行文件放到 `tools/ffmpeg.exe`，再运行 `打包.bat`。
FFmpeg下载地址：https://ffmpeg.org/download.html 。

Cookie、连接码、下载记录、视频、发行版、运行环境和测试产物均不进入Git。
桌面保存的Cookie使用Windows DPAPI加密，扩展随任务传入的Cookie只用于内存中的任务。
版本变化见[更新记录](更新记录.md)。
