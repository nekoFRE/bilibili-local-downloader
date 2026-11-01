<div align="center">

<img src="assets/icon.png" width="88" height="88" alt="B站视频下载器图标">

# B站视频下载器

**在视频页选择画质和分P，把视频保存到自己的文件夹。**

Windows桌面下载器 + Edge悬浮助手，支持收藏夹批量下载、自动合并MP4与后台下载。

![Windows 11 已验证](https://img.shields.io/badge/Windows_11-已验证-0078D4) ![Edge 扩展](https://img.shields.io/badge/Edge-悬浮下载-00A2D6) ![MP4 自动合并](https://img.shields.io/badge/MP4-自动合并-19A580)

[获取源码与扩展](https://github.com/nekoFRE/bilibili-local-downloader/archive/refs/heads/main.zip) · [快速开始](#quick-start) · [完整使用说明](使用说明.md) · [反馈问题](https://github.com/nekoFRE/bilibili-local-downloader/issues)

</div>

![桌面下载器：视频解析、画质选择与下载队列](assets/readme/desktop.png)

<sub>真实程序界面，使用演示任务展示；不含用户账号或下载记录。</sub>

## 你可以用它做什么

- **在视频页直接下载。** 点击页面上的「下载视频」打开悬浮面板，拖动、收起后可以继续浏览。
- **选好再下。** 支持画质选择、当前分P或多P下载，也能识别收藏夹列表中的视频地址。
- **收藏夹按需批量下载。** 用「高级选项」搜索标题或BV号，逐条勾选需要的视频。
- **保存成一个MP4。** 桌面端通过FFmpeg合并音视频，无需自己处理两个文件。
- **目录自己决定。** 桌面和插件共用保存目录，修改后自动同步；已排队任务保留原目录。
- **下载时再启动桌面。** 首次配置后，插件可以按需在后台唤醒下载器，通过系统托盘管理任务。

## 先选一种使用方式

| 你想做什么 | 需要准备 | 从这里开始 |
| --- | --- | --- |
| 只用Edge保存音视频，接受分开的文件 | Edge浏览器与本仓库的扩展 | [浏览器直接下载](#browser-only) |
| 自动合并MP4、管理队列、批量下载收藏夹 | Windows桌面端 + Edge扩展（可选） | [桌面端 + Edge](#desktop-and-edge) |
| 从GitHub获取并运行完整桌面端 | Python 3.13、FFmpeg，首次打包 | [从源码生成发行版](使用说明.md#build-source) |

> **当前仓库提供源码和Edge扩展，尚未发布预编译的Windows下载包。** 下载源码ZIP后，可以直接加载其中的扩展；桌面端请先按指南打包。已有完整发行版的用户，可直接运行其中的EXE。

<a id="quick-start"></a>
## 快速开始

<a id="browser-only"></a>
### 方式一：只用Edge，直接下载

1. [下载源码ZIP](https://github.com/nekoFRE/bilibili-local-downloader/archive/refs/heads/main.zip)并解压到固定位置。
2. 在Edge地址栏输入 `edge://extensions`，开启「开发人员模式」。
3. 点击「加载解压缩的扩展」，选择解压后的 **`edge-extension`** 文件夹。
4. 打开B站视频页，点击「下载视频」，选择画质、分P和「浏览器直接下载」。

这种方式无需启动桌面程序，也无需配置连接码。默认会弹出Edge保存对话框。

**DASH视频会分别保存视频轨和音频轨两个 `.m4s` 文件。** 想得到合并后的MP4，请使用下面的桌面模式；也可以在桌面设置中选择「合并已有音视频文件」。

<a id="desktop-and-edge"></a>
### 方式二：桌面端 + Edge，自动合并MP4

先[从源码生成发行版](使用说明.md#build-source)，或使用你已有的完整发行版。

1. 打开 `发行版\B站视频下载器\B站视频下载器.exe`，在窗口顶部选择保存目录。
2. 安装上面的Edge扩展；推荐加载发行版内附带的 `edge-extension` 文件夹。
3. 桌面「设置与连接」→「复制连接码」；扩展「设置」→粘贴→「保存并检查连接」。看到「自动唤醒已配置」后，首次连接就完成了。
4. 打开B站视频页，选择「桌面合并MP4」、画质和分P，点击「开始下载」。

之后无需每次提前打开桌面端：提交任务时会自动后台启动，点击扩展中的「打开桌面端」可查看队列。移动发行版或扩展文件夹后，重新保存一次连接码即可。

**也可以只用桌面端。** 粘贴视频链接、BV号或分享文字 →「解析视频」→选择画质和分P→「加入下载队列」。需要登录时，可在设置中导入Cookie，或通过扩展同步Edge登录。

<details>
<summary><strong>两种模式有什么区别？</strong></summary>

| 功能 | 桌面合并MP4 | 浏览器直接下载 |
| --- | --- | --- |
| 保存文件 | 自动合并成MP4 | DASH音视频分别保存；FLV按接口结果保存 |
| 保存位置 | 自选目录，与桌面设置同步 | Edge保存对话框或Edge默认下载目录 |
| 收藏夹批量队列 | 支持，可筛选视频 | 收藏夹批量任务交给桌面端处理 |
| 任务管理 | 查看进度、取消、重试、暂停队列 | 使用Edge下载管理器 |
| 桌面程序 | 按需唤醒，在托盘后台运行 | 不需要 |

</details>

## Edge面板预览

<p align="center">
  <img src="assets/readme/extension.png" width="470" alt="Edge扩展：选择画质、分P和下载方式">
</p>

<sub>真实扩展界面，使用演示视频信息。视频页悬浮窗复用同一面板，支持拖动、收起和关闭。</sub>

## 收藏夹与后台下载

**收藏夹不必整夹下载。** 在Edge中登录B站，打开面板里的「收藏夹批量下载」→「加载」→选择收藏夹→「高级选项」。按标题或BV号筛选后勾选视频，再加入本地队列。桌面版也提供收藏夹勾选功能。

**关掉面板后，已提交任务继续下载。** 桌面窗口默认在最小化或关闭时收起到托盘；点击托盘图标恢复窗口，右键可暂停队列、打开目录或退出程序。完全退出会停止未完成任务；目前支持手动重试，尚无断点续传。

> 「暂停队列」会等待当前任务完成，再停止启动后续任务；要停止当前下载，请使用该任务的「取消」。

## 登录与本机数据

- **画质取决于账号权限。** 只提供当前账号和B站接口允许访问的内容与画质。
- **连接使用本机地址。** 扩展通过 `127.0.0.1:17890` 与桌面端通信，并用连接码验证请求。B站接口和媒体下载仍需要联网。
- **登录默认临时使用。** 扩展随任务传入的Cookie用于内存中的任务；连接时同步的登录默认也只在桌面端本次运行有效。
- **长期保存需要主动操作。** 在桌面设置中点击「保存当前登录」或导入Cookie后，使用Windows DPAPI加密保存到程序旁的 `data/login.dat`。
- **反馈时请隐藏隐私。** 不要在Issue、截图或公开仓库中附上Cookie、连接码、账号资料或个人下载历史。

## 常见问题

<details>
<summary><strong>下载了源码，为什么双击「启动下载器.bat」还不能用？</strong></summary>

启动脚本优先打开本地发行版，其次使用已准备好的 `.runtime` 环境。首次从GitHub下载源码时，请先[安装Python 3.13并打包](使用说明.md#build-source)。只想用浏览器下载，可以直接安装扩展。

</details>

<details>
<summary><strong>为什么下载后是两个.m4s文件？</strong></summary>

你选择了「浏览器直接下载」，而当前视频使用DASH格式。改用「桌面合并MP4」可自动合并；已有文件可在桌面设置中使用「合并已有音视频文件」。

</details>

<details>
<summary><strong>视频页没有按钮，或者悬浮窗打不开？</strong></summary>

检查插件总开关、悬浮窗开关及Edge对B站的站点访问权限。更新扩展后，在 `edge://extensions` 点击重新加载，再刷新视频页。页面中的操作栏按钮会等待B站初始化完成后出现，也可从工具栏弹窗点击「在视频页悬浮」。

</details>

<details>
<summary><strong>自动唤醒失败，需要重新安装吗？</strong></summary>

先手动打开完整发行版，确认同目录有 `browser-bridge.exe`；再到扩展设置点击「保存并检查连接」。更换程序位置、扩展安装位置或Edge配置后需要重新配置。完整排查见[使用说明](使用说明.md#troubleshooting)。

</details>

<details>
<summary><strong>修改保存目录后，为什么旧任务仍下载到原目录？</strong></summary>

任务在加入队列时记住目录。新的目录只影响之后加入的任务，避免正在下载的文件被突然移动。插件面板在目录可见且桌面端运行时会自动同步显示，通常约2秒；也可在扩展设置点击「同步桌面目录」。

</details>

<details>
<summary><strong>支持哪些系统？是否需要一直打开桌面窗口？</strong></summary>

当前面向Windows，已有Windows 11验证记录；macOS和Linux尚未适配。下载时桌面端可以收起到托盘，窗口不必一直显示；从托盘菜单完全退出程序会停止未完成任务。

</details>

## 源码与反馈

项目保留少量Python模块和原生JavaScript扩展，便于阅读、修改和本地打包。

| 位置 | 内容 |
| --- | --- |
| `main.py`、`ui.py` | 桌面入口、窗口与托盘 |
| `downloader.py`、`tasks.py` | 视频解析、下载合并、任务队列与本机接口 |
| `browser_bridge.py` | Edge按需唤醒助手 |
| `edge-extension/` | Manifest V3浏览器扩展 |
| `打包.bat`、`打包.ps1` | 生成完整Windows发行版 |

[完整使用说明](使用说明.md) · [版本更新记录](更新记录.md) · [提交问题或建议](https://github.com/nekoFRE/bilibili-local-downloader/issues/new)

反馈时提供：Windows与Edge版本、插件版本、操作步骤、预期结果和实际结果。可以附去除隐私的截图；如问题与特定视频有关，提供公开的视频链接即可。

界面使用[PySide6](https://doc.qt.io/qtforpython-6/)，音视频合并使用[FFmpeg](https://ffmpeg.org/)。FFmpeg随附的许可证见[tools/FFmpeg-LICENSE.txt](tools/FFmpeg-LICENSE.txt)。
