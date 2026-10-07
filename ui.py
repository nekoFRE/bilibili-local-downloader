"""一个主窗口，三个页面；下载任务由tasks.py独立执行。"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

import requests
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSystemTrayIcon,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from downloader import (
    APP_ROOT, RESOURCE_ROOT, QUALITY_NAMES, account_info, favorite_folders,
    favorite_videos, ffmpeg_path, inspect_video, parse_cookies, merge_local_files,
)
from tasks import FINISHED


class WorkerSignals(QObject):
    success = Signal(int, object)
    error = Signal(int, str)


class Worker(QRunnable):
    def __init__(self, job_id, function):
        super().__init__()
        self.job_id, self.function = job_id, function
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.success.emit(self.job_id, self.function())
        except Exception as exc:
            self.signals.error.emit(self.job_id, str(exc)[:600])


def label(text, object_name=""):
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(True)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return widget, layout


class MainWindow(QMainWindow):
    def __init__(self, settings, manager, bridge):
        super().__init__()
        self.settings, self.manager, self.bridge = settings, manager, bridge
        self.meta = None
        self.favorites = []
        self.jobs, self.job_id = {}, 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.exiting = False
        self.tray_notice_shown = False
        self.setWindowTitle("B站视频下载器")
        self.resize(1120, 840)
        self.setMinimumSize(900, 650)
        icon = RESOURCE_ROOT / "assets" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(26, 22, 26, 18)
        outer.setSpacing(16)
        head = QHBoxLayout()
        title = QVBoxLayout()
        title.addWidget(label("B站视频下载器", "appTitle"))
        title.addWidget(label("把喜欢的视频，安稳地留在本地。", "muted"))
        head.addLayout(title)
        head.addStretch()
        self.account_label = label("游客模式", "badge")
        head.addWidget(self.account_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.background_button = button("后台运行", self.hide_to_tray)
        head.addWidget(self.background_button)
        outer.addLayout(head)
        directory_card, directory_layout = card()
        directory_layout.setContentsMargins(16, 12, 16, 12)
        directory_row = QHBoxLayout()
        directory_row.addWidget(label("保存到", "muted"))
        self.directory = QLineEdit(settings.values["directory"])
        self.directory.setReadOnly(True)
        self.directory.setToolTip("新任务使用这个目录，已经排队的任务保留原来的目录。")
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(button("选择目录", self.choose_directory))
        directory_row.addWidget(button("打开目录", lambda: self.open_path(self.directory.text(), folder=True)))
        directory_layout.addLayout(directory_row)
        outer.addWidget(directory_card)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_download_page(), "视频下载")
        self.tabs.addTab(self.build_favorites_page(), "收藏夹")
        self.tabs.addTab(self.build_settings_page(), "设置与连接")
        outer.addWidget(self.tabs, 1)
        self.notice = label("准备好了，粘贴一个视频链接开始。", "muted")
        outer.addWidget(self.notice)
        self.setCentralWidget(central)
        self.setup_tray()
        style_path = RESOURCE_ROOT / "assets" / "style.qss"
        if style_path.exists():
            style = style_path.read_text(encoding="utf-8").replace("url(assets/check.svg)", f'url("{(RESOURCE_ROOT / "assets" / "check.svg").as_posix()}")')
            self.setStyleSheet(style)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(450)
        if manager.cookies:
            self.check_account()
        if settings.error:
            self.notice.setText(settings.error)

    def build_download_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(14)
        input_card, content = card()
        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("粘贴视频链接、BV号或分享文字")
        self.url.returnPressed.connect(self.analyze)
        row.addWidget(self.url, 1)
        row.addWidget(button("粘贴", lambda: self.url.setText(QApplication.clipboard().text().strip())))
        self.analyze_button = button("解析视频", self.analyze, True)
        row.addWidget(self.analyze_button)
        content.addLayout(row)
        preview = QHBoxLayout()
        self.cover = QLabel("视频预览")
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(180, 102)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.addWidget(self.cover)
        info = QVBoxLayout()
        self.video_title = label("选择你想保存的视频", "sectionTitle")
        self.video_info = label("解析后可以选择画质和分P，视频将自动合并为MP4。", "muted")
        info.addWidget(self.video_title)
        info.addWidget(self.video_info)
        info.addStretch()
        preview.addLayout(info, 1)
        content.addLayout(preview)
        options = QHBoxLayout()
        options.addWidget(label("画质"))
        self.quality = QComboBox()
        self.quality.addItem("自动 · 最高可用", 0)
        self.quality.setMinimumWidth(170)
        options.addWidget(self.quality)
        options.addStretch()
        self.all_pages = QCheckBox("全选分P")
        self.all_pages.setVisible(False)
        self.all_pages.toggled.connect(lambda checked: self.select_items(self.pages, checked))
        options.addWidget(self.all_pages)
        self.add_button = button("加入下载队列", self.add_video, True)
        self.add_button.setEnabled(False)
        options.addWidget(self.add_button)
        content.addLayout(options)
        self.pages = QListWidget()
        self.pages.setMaximumHeight(112)
        self.pages.setVisible(False)
        content.addWidget(self.pages)
        layout.addWidget(input_card)
        queue_card, queue_layout = card()
        row = QHBoxLayout()
        row.addWidget(label("下载队列", "sectionTitle"))
        self.queue_count = label("0 个任务", "muted")
        row.addWidget(self.queue_count)
        row.addStretch()
        self.filter = QComboBox()
        self.filter.addItems(["全部", "进行中", "已完成", "失败 / 中断"])
        self.filter.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.filter)
        self.pause_button = button("暂停队列", self.toggle_pause)
        row.addWidget(self.pause_button)
        row.addWidget(button("清除已结束", self.clear_finished))
        queue_layout.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["视频 / 分P", "状态", "进度", "操作", "保存目录"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in [(1, 82), (2, 200), (3, 82), (4, 125)]:
            self.table.setColumnWidth(index, width)
        self.table.cellDoubleClicked.connect(self.show_task)
        queue_layout.addWidget(self.table, 1)
        queue_layout.addWidget(label("双击任务查看详情。暂停队列会等待当前任务完成；取消可停止当前下载。", "muted"))
        layout.addWidget(queue_card, 1)
        return widget

    def build_favorites_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 16, 0, 0)
        content_card, content = card()
        content.addWidget(label("从收藏夹批量保存", "sectionTitle"))
        content.addWidget(label("导入登录Cookie，或使用Edge扩展登录浏览器下载。", "muted"))
        row = QHBoxLayout()
        self.folder_combo = QComboBox()
        self.folder_combo.setMinimumWidth(220)
        row.addWidget(self.folder_combo, 1)
        self.load_folders_button = button("加载收藏夹", self.load_folders, True)
        row.addWidget(self.load_folders_button)
        self.load_videos_button = button("读取视频", self.load_favorites)
        row.addWidget(self.load_videos_button)
        content.addLayout(row)
        self.favorite_search = QLineEdit()
        self.favorite_search.setPlaceholderText("按标题或UP主筛选")
        self.favorite_search.textChanged.connect(self.filter_favorites)
        content.addWidget(self.favorite_search)
        content.addWidget(label("高级选项：按标题或UP主筛选后，可逐条勾选要下载的视频。", "muted"))
        self.favorite_list = QListWidget()
        content.addWidget(self.favorite_list, 1)
        row = QHBoxLayout()
        row.addWidget(button("全选当前列表", lambda: self.select_items(self.favorite_list, True, visible_only=True)))
        row.addWidget(button("取消全部选择", lambda: self.select_items(self.favorite_list, False)))
        self.favorite_all_pages = QCheckBox("下载所有分P")
        self.favorite_all_pages.setChecked(True)
        row.addWidget(self.favorite_all_pages)
        row.addStretch()
        row.addWidget(button("加入下载队列", self.add_favorites, True))
        content.addLayout(row)
        content.addWidget(label("收藏夹任务使用设置中的默认画质。失效视频会标记失败，其他任务继续下载。", "muted"))
        layout.addWidget(content_card, 1)
        return widget

    def build_settings_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(14)
        login_card, login = card()
        login.addWidget(label("登录与画质", "sectionTitle"))
        login.addWidget(label("可从Edge扩展同步登录，也可导入或粘贴Cookie。Edge同步默认只在本次运行中使用，需要保留时点击“保存当前登录”。", "muted"))
        self.cookie_input = QPlainTextEdit()
        self.cookie_input.setPlaceholderText("在这里粘贴Cookie（不会显示在日志中）")
        self.cookie_input.setMaximumHeight(85)
        login.addWidget(self.cookie_input)
        row = QHBoxLayout()
        row.addWidget(button("导入Cookie文件", self.import_cookie))
        row.addWidget(button("保存并检查登录", self.save_cookie_text, True))
        row.addWidget(button("保存当前登录", self.save_current_login))
        row.addWidget(button("退出登录", self.clear_cookie))
        row.addStretch()
        login.addLayout(row)
        self.login_status = label("未设置登录，可下载允许游客访问的视频。", "muted")
        login.addWidget(self.login_status)
        row = QHBoxLayout()
        row.addWidget(label("默认画质"))
        self.default_quality = QComboBox()
        self.default_quality.addItem("自动 · 最高可用", 0)
        for quality, name in QUALITY_NAMES.items():
            self.default_quality.addItem(name, quality)
        index = self.default_quality.findData(self.settings.values.get("quality", 0))
        self.default_quality.setCurrentIndex(max(0, index))
        self.default_quality.currentIndexChanged.connect(lambda _: self.settings.set("quality", self.default_quality.currentData()))
        row.addWidget(self.default_quality)
        row.addWidget(label("不可用时选择较低的可用画质。", "muted"), 1)
        login.addLayout(row)
        layout.addWidget(login_card)
        bridge_card, bridge_layout = card()
        bridge_layout.addWidget(label("连接Edge浏览器扩展", "sectionTitle"))
        bridge_layout.addWidget(label("在Edge扩展设置中粘贴连接码并保存，即可配置自动唤醒。以后下载时会在后台启动本程序，B站登录状态随任务传入。", "muted"))
        row = QHBoxLayout()
        self.token = QLineEdit(self.settings.values["token"])
        self.token.setReadOnly(True)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(self.token, 1)
        row.addWidget(button("复制连接码", self.copy_token, True))
        row.addWidget(button("打开扩展目录", lambda: self.open_path(str(APP_ROOT / "edge-extension"), folder=True)))
        bridge_layout.addLayout(row)
        bridge_layout.addWidget(label(self.bridge.error or "本机连接已就绪 · 127.0.0.1:17890", "muted"))
        bridge_layout.addWidget(label("在Edge的扩展管理页面开启开发人员模式，点击“加载解压缩的扩展”，选择edge-extension文件夹。", "muted"))
        layout.addWidget(bridge_card)
        tools_card, tools = card()
        tools.addWidget(label("运行状态", "sectionTitle"))
        self.minimize_tray = QCheckBox("最小化时收起到系统托盘")
        self.minimize_tray.setChecked(self.settings.values.get("minimize_to_tray", True))
        self.minimize_tray.toggled.connect(lambda value: self.settings.set("minimize_to_tray", value))
        tools.addWidget(self.minimize_tray)
        self.close_tray = QCheckBox("关闭窗口时继续在后台下载")
        self.close_tray.setChecked(self.settings.values.get("close_to_tray", True))
        self.close_tray.toggled.connect(lambda value: self.settings.set("close_to_tray", value))
        tools.addWidget(self.close_tray)
        tools.addWidget(label("点击任务栏右下角的托盘图标恢复窗口。完全退出请使用托盘菜单或下方按钮。", "muted"))
        tools.addWidget(button("退出程序", self.request_exit))
        tools.addWidget(label("FFmpeg 已就绪，可以自动合并MP4。" if ffmpeg_path() else "未找到FFmpeg，请把ffmpeg.exe放入tools目录。", "muted"))
        self.merge_button = button("合并已有音视频文件", self.merge_existing)
        tools.addWidget(self.merge_button)
        tools.addWidget(label("第一版支持取消和重试，重试会重新下载当前分P。扩展的浏览器直下模式会分别保存音视频轨道。", "muted"))
        layout.addWidget(tools_card)
        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

    def async_job(self, function, success, error=None):
        self.job_id += 1
        job_id = self.job_id
        worker = Worker(job_id, function)
        self.jobs[job_id] = (worker, success, error)
        worker.signals.success.connect(self.job_success, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self.job_error, Qt.ConnectionType.QueuedConnection)
        self.pool.start(worker)

    @Slot(int, object)
    def job_success(self, job_id, value):
        job = self.jobs.pop(job_id, None)
        if job:
            job[1](value)

    @Slot(int, str)
    def job_error(self, job_id, message):
        job = self.jobs.pop(job_id, None)
        if job:
            if job[2]:
                job[2](message)
            else:
                self.notice.setText(message)
                QMessageBox.warning(self, "操作未完成", message)

    def analyze(self):
        text = self.url.text().strip()
        if not text:
            self.notice.setText("先粘贴一个视频链接或BV号。")
            return
        self.meta = None
        self.add_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.analyze_button.setText("解析中…")
        self.notice.setText("正在获取视频信息和当前可用画质…")
        cookies = dict(self.manager.cookies)
        def success(meta):
            self.analyze_button.setEnabled(True)
            self.analyze_button.setText("解析视频")
            self.meta = meta
            self.video_title.setText(meta["title"])
            self.video_info.setText(f'{meta["author"]}  ·  {duration(meta["duration"])}  ·  {meta["bvid"]}  ·  {len(meta["pages"])} 个分P')
            self.quality.clear()
            self.quality.addItem("自动 · 最高可用", 0)
            for item in meta["qualities"]:
                self.quality.addItem(item["name"], item["id"])
            index = self.quality.findData(self.settings.values.get("quality", 0))
            self.quality.setCurrentIndex(max(0, index))
            self.pages.clear()
            self.all_pages.blockSignals(True)
            self.all_pages.setChecked(False)
            self.all_pages.blockSignals(False)
            for p in meta["pages"]:
                item = QListWidgetItem(f'P{p["page"]:02d}  {p["part"]}  ·  {duration(p["duration"])}')
                item.setData(Qt.ItemDataRole.UserRole, p["page"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if p["page"] == meta["page"] else Qt.CheckState.Unchecked)
                self.pages.addItem(item)
            self.pages.setVisible(len(meta["pages"]) > 1)
            self.all_pages.setVisible(len(meta["pages"]) > 1)
            self.add_button.setEnabled(True)
            self.notice.setText("解析完成。选择画质和分P，然后加入队列。")
            self.cover.clear()
            self.cover.setText("正在加载封面")
            if meta.get("cover"):
                cover_url = meta["cover"]
                bvid = meta["bvid"]
                self.async_job(lambda: self.fetch_cover(cover_url), lambda data: self.show_cover(data) if self.meta and self.meta["bvid"] == bvid else None, lambda _: self.cover.setText("暂无封面"))
        def error(message):
            self.analyze_button.setEnabled(True)
            self.analyze_button.setText("解析视频")
            self.notice.setText(message)
            QMessageBox.warning(self, "解析失败", message)
        self.async_job(lambda: inspect_video(text, cookies), success, error)

    @staticmethod
    def fetch_cover(url):
        response = requests.get(url.replace("http://", "https://"), timeout=(5, 15))
        response.raise_for_status()
        return response.content

    def show_cover(self, data):
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if not pixmap.isNull():
            self.cover.setPixmap(pixmap.scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover.setText("暂无封面")

    def add_video(self):
        if not self.meta:
            return
        selected = [self.pages.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.pages.count()) if self.pages.item(i).checkState() == Qt.CheckState.Checked]
        if not selected:
            self.notice.setText("请至少选择一个分P。")
            return
        specs = [{"url": self.meta["url"], "page": p, "title": self.meta["title"], "quality": self.quality.currentData(), "directory": self.directory.text()} for p in selected]
        ids = self.manager.add(specs)
        self.notice.setText(f"已加入 {len(ids)} 个任务；重复的任务或已完成文件会跳过。")
        self.refresh()

    @staticmethod
    def select_items(widget, checked, visible_only=False):
        for i in range(widget.count()):
            item = widget.item(i)
            if not visible_only or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def choose_directory(self, *_):
        selected = QFileDialog.getExistingDirectory(self, "选择视频保存目录", self.directory.text())
        if selected:
            self.directory.setText(str(Path(selected)))
            self.settings.set("directory", str(Path(selected)))
            self.notice.setText("保存目录已更新，新任务将使用这个目录。")
        return str(Path(selected)) if selected else None

    def toggle_pause(self):
        self.manager.paused = not self.manager.paused
        self.pause_button.setText("继续队列" if self.manager.paused else "暂停队列")
        self.manager.wakeup.set()
        if self.tray:
            self.tray_pause.setText("继续下载队列" if self.manager.paused else "暂停下载队列")

    def clear_finished(self):
        self.manager.clear_finished()
        self.notice.setText("已清除结束的任务记录，视频文件保留。")
        self.refresh()

    def refresh(self, *_):
        try:
            login = self.manager.login_updates.get_nowait()
            if login["revision"] == self.manager.login_revision:
                self.show_account(login["account"])
        except queue.Empty:
            pass
        if not self.manager.window_requests.empty():
            while not self.manager.window_requests.empty():
                try:
                    self.manager.window_requests.get_nowait()
                except queue.Empty:
                    break
            self.restore_window()
        snapshots = self.manager.snapshot()
        counts = sum(t["status"] not in FINISHED for t in snapshots)
        self.queue_count.setText(f'{counts} 个进行中 · {len(snapshots)} 个任务')
        if self.tray:
            self.tray.setToolTip(f"B站视频下载器 · {counts} 个任务" + (" · 队列已暂停" if self.manager.paused else ""))
        mode = self.filter.currentText()
        rows = [t for t in snapshots if mode == "全部" or (mode == "进行中" and t["status"] not in FINISHED) or (mode == "已完成" and t["status"] in {"已完成", "已跳过"}) or (mode == "失败 / 中断" and t["status"] in {"失败", "已中断", "已取消"})]
        self.visible_tasks = rows
        structure = tuple((t["id"], t["status"] in FINISHED) for t in rows)
        if getattr(self, "table_structure", None) != structure:
            self.table_structure = structure
            self.table.setRowCount(len(rows))
            for row, task in enumerate(rows):
                for column in [0, 1, 4]:
                    self.table.setItem(row, column, QTableWidgetItem())
                progress = QProgressBar()
                progress.setRange(0, 100)
                progress.setFixedHeight(22)
                self.table.setCellWidget(row, 2, progress)
                self.table.setRowHeight(row, 42)
        for row, task in enumerate(rows):
            self.table.item(row, 0).setText(f'{task["title"]} · P{task["page"]}')
            self.table.item(row, 0).setToolTip(task["detail"])
            self.table.item(row, 1).setText(task["status"])
            self.table.item(row, 4).setText(Path(task["directory"]).name or task["directory"])
            self.table.item(row, 4).setToolTip(task["directory"])
            progress = self.table.cellWidget(row, 2)
            progress.setValue(int(task["progress"]))
            progress.setFormat("%p% · " + task["detail"].split(" · ")[0] if task["status"] in {"下载视频", "下载音频"} else "%p%")
            progress.setToolTip(task["detail"])
            current = self.table.cellWidget(row, 3)
            action = "打开" if task["status"] in {"已完成", "已跳过"} else "重试" if task["status"] in FINISHED else "取消"
            if not current or current.text() != action:
                task_id = task["id"]
                callback = (lambda _, path=task.get("path", ""): self.open_path(path)) if action == "打开" else (lambda _, task_id=task_id: self.manager.retry(task_id)) if action == "重试" else (lambda _, task_id=task_id: self.manager.cancel_task(task_id))
                self.table.setCellWidget(row, 3, button(action, callback))
        try:
            request = self.manager.directory_requests.get_nowait()
        except queue.Empty:
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        request["directory"] = self.choose_directory()
        request["event"].set()

    def show_task(self, row, _column):
        if 0 <= row < len(self.visible_tasks):
            task = self.visible_tasks[row]
            QMessageBox.information(self, "任务详情", f'{task["title"]}\n\n{task["bvid"]} · P{task["page"]}\n状态：{task["status"]}\n{task["detail"]}\n\n保存目录：{task["directory"]}\n文件：{task.get("path") or "尚未生成"}')

    def open_path(self, path, folder=False):
        target = Path(path)
        if folder:
            target.mkdir(parents=True, exist_ok=True)
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))
        else:
            self.notice.setText("文件已移动或删除。")

    def import_cookie(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入B站Cookie", str(APP_ROOT), "Cookie文件 (*.txt *.json);;所有文件 (*)")
        if path:
            try:
                self.apply_cookies(parse_cookies(Path(path).read_text(encoding="utf-8-sig")))
            except Exception as exc:
                QMessageBox.warning(self, "导入失败", str(exc))

    def save_cookie_text(self):
        try:
            self.apply_cookies(parse_cookies(self.cookie_input.toPlainText()))
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def apply_cookies(self, cookies):
        if not cookies:
            raise ValueError("没有解析到Cookie，请检查文件或粘贴内容。")
        self.manager.set_cookies(cookies)
        self.cookie_input.clear()
        self.check_account()

    def check_account(self):
        self.login_status.setText("正在检查登录状态…")
        cookies = dict(self.manager.cookies)
        revision = self.manager.login_revision
        def success(account):
            if revision == self.manager.login_revision:
                self.show_account(account)
        def error(message):
            if revision == self.manager.login_revision:
                self.account_label.setText("登录待更新")
                self.login_status.setText(message)
        self.async_job(lambda: account_info(cookies), success, error)

    def show_account(self, account):
        self.account_label.setText(account["name"])
        source = " · 来自Edge（本次运行有效，未保存本次登录）" if self.manager.cookie_source == "Edge临时同步" else " · 已加密保存"
        self.login_status.setText(f'已登录：{account["name"]}' + (" · 大会员" if account["vip"] else "") + source)
        self.notice.setText("B站登录状态已验证，可在桌面解析视频和加载收藏夹。")

    def save_current_login(self):
        if not self.manager.cookies:
            self.notice.setText("先从Edge扩展同步登录，或导入Cookie，再保存当前登录。")
            return
        try:
            self.manager.set_cookies(dict(self.manager.cookies))
            self.check_account()
        except Exception:
            QMessageBox.warning(self, "保存失败", "无法保存当前登录信息，请检查程序目录是否可写。")

    def clear_cookie(self):
        self.manager.set_cookies({})
        self.cookie_input.clear()
        self.account_label.setText("游客模式")
        self.login_status.setText("已清除保存的登录信息。")

    def copy_token(self):
        QApplication.clipboard().setText(self.settings.values["token"])
        self.notice.setText("连接码已复制，粘贴到Edge扩展设置中即可。")

    def merge_existing(self):
        video, _ = QFileDialog.getOpenFileName(self, "选择视频轨道（通常以_video.m4s结尾）", self.directory.text(), "媒体文件 (*.m4s *.mp4 *.flv);;所有文件 (*)")
        if not video:
            return
        suggested = str(Path(video).with_name(Path(video).name.replace("_video", "_audio")))
        audio, _ = QFileDialog.getOpenFileName(self, "选择音频轨道（通常以_audio.m4s结尾）", suggested, "媒体文件 (*.m4s *.m4a *.aac *.mp4);;所有文件 (*)")
        if not audio:
            return
        default_output = str(Path(video).with_name(Path(video).stem.replace("_video", "") + ".mp4"))
        output, _ = QFileDialog.getSaveFileName(self, "保存合并后的MP4", default_output, "MP4视频 (*.mp4)")
        if not output:
            return
        self.merge_button.setEnabled(False)
        self.notice.setText("正在合并本地音视频文件…")
        def success(path):
            self.merge_button.setEnabled(True)
            self.notice.setText("本地合并完成：" + path)
        def error(message):
            self.merge_button.setEnabled(True)
            self.notice.setText(message)
        self.async_job(lambda: merge_local_files(video, audio, output, threading.Event()), success, error)

    def load_folders(self):
        if not self.manager.cookies:
            self.tabs.setCurrentIndex(2)
            self.notice.setText("先导入B站Cookie，再加载收藏夹。")
            return
        self.load_folders_button.setEnabled(False)
        cookies = dict(self.manager.cookies)
        def success(folders):
            self.load_folders_button.setEnabled(True)
            self.folder_combo.clear()
            for folder in folders:
                self.folder_combo.addItem(f'{folder["title"]} · {folder.get("media_count", 0)} 个', folder["id"])
            self.notice.setText(f"已加载 {len(folders)} 个收藏夹。")
        def error(message):
            self.load_folders_button.setEnabled(True)
            self.notice.setText(message)
        self.async_job(lambda: favorite_folders(cookies), success, error)

    def load_favorites(self):
        folder_id = self.folder_combo.currentData()
        if not folder_id:
            self.notice.setText("请先加载并选择收藏夹。")
            return
        self.load_videos_button.setEnabled(False)
        self.notice.setText("正在读取收藏夹中的视频…")
        cookies = dict(self.manager.cookies)
        def success(videos):
            self.load_videos_button.setEnabled(True)
            self.favorites = videos
            self.favorite_list.clear()
            for video in videos:
                item = QListWidgetItem(f'{video["title"]}  ·  {video["author"]}  ·  {duration(video["duration"])}')
                item.setData(Qt.ItemDataRole.UserRole, video)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.favorite_list.addItem(item)
            self.filter_favorites()
            self.notice.setText(f"已读取 {len(videos)} 个视频，勾选要保存的内容。")
        def error(message):
            self.load_videos_button.setEnabled(True)
            self.notice.setText(message)
        self.async_job(lambda: favorite_videos(folder_id, cookies), success, error)

    def filter_favorites(self, *_):
        term = self.favorite_search.text().strip().lower()
        for i in range(self.favorite_list.count()):
            item = self.favorite_list.item(i)
            item.setHidden(term not in item.text().lower())

    def add_favorites(self):
        specs = []
        for i in range(self.favorite_list.count()):
            item = self.favorite_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                video = item.data(Qt.ItemDataRole.UserRole)
                specs.append({"url": video["bvid"], "page": 1, "title": video["title"], "directory": self.directory.text(), "quality": self.default_quality.currentData(), "all_pages": self.favorite_all_pages.isChecked()})
        if not specs:
            self.notice.setText("请先勾选要下载的视频。")
            return
        try:
            ids = self.manager.add(specs)
            self.notice.setText(f"已加入 {len(ids)} 个收藏夹任务。")
            self.tabs.setCurrentIndex(0)
        except ValueError as exc:
            self.notice.setText(str(exc))

    def setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.background_button.setEnabled(False)
            self.background_button.setToolTip("当前系统没有可用的托盘，窗口仍可正常最小化。")
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu(self)
        menu.addAction("显示主窗口", self.restore_window)
        self.tray_pause = menu.addAction("暂停下载队列", self.toggle_pause)
        menu.addAction("打开下载目录", lambda: self.open_path(self.directory.text(), folder=True))
        menu.addSeparator()
        menu.addAction("退出程序", self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("B站视频下载器")
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_window()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        if self.exiting or not self.tray:
            return
        self.hide()
        if not self.tray_notice_shown:
            self.tray_notice_shown = True
            self.tray.showMessage("下载器正在后台运行", "下载会继续。点击托盘图标恢复窗口，右键菜单可完全退出。", QSystemTrayIcon.MessageIcon.Information, 3000)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() and self.settings.values.get("minimize_to_tray", True):
            QTimer.singleShot(0, self.hide_to_tray)

    def request_exit(self):
        self.exiting = True
        if not self.close():
            self.exiting = False

    def closeEvent(self, event):
        if not self.exiting and self.tray and self.settings.values.get("close_to_tray", True):
            event.ignore()
            self.hide_to_tray()
            return
        if any(t["status"] not in FINISHED for t in self.manager.snapshot()):
            result = QMessageBox.question(self, "退出下载器", "还有未完成的任务。退出会停止当前下载，重新打开后可以重试。\n\n确定退出吗？")
            if result != QMessageBox.StandardButton.Yes:
                event.ignore()
                self.exiting = False
                return
        self.timer.stop()
        self.manager.shutdown()
        self.bridge.shutdown()
        if self.tray:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()
