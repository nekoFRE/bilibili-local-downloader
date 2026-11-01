"""一个主窗口，三个页面；下载任务由tasks.py独立执行。"""
from __future__ import annotations

import queue
import re
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QUrl, QRect, QSize, QPoint
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QColor, QPen, QFont, QPainterPath, QLinearGradient
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSystemTrayIcon,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QDialog, QDialogButtonBox, QGridLayout, QSlider, QSpinBox, QStyledItemDelegate,
)

from downloader import (
    APP_ROOT, RESOURCE_ROOT, QUALITY_NAMES, account_info, favorite_folders,
    favorite_page, ffmpeg_path, inspect_video, video_metadata, parse_cookies, merge_local_files,
)
from danmaku import DEFAULTS as DM_DEFAULTS, WEB_FIELDS, normalize_profile, resolve_profile
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


def favorite_part_text(video, selected):
    if not video["available"]:
        return "已失效"
    if video.get("page_count") == 1:
        return "单视频"
    if selected is not None:
        return f"已选 {len(selected)}P" if selected else "未选分P"
    return "选择分P" if video.get("page_count", 0) > 1 else "查看分P"


def normalize_part_selections(value):
    if not isinstance(value, dict):
        return {}
    return {bvid: sorted({page for page in pages if type(page) is int and 1 <= page <= 10000})
            for bvid, pages in list(value.items())[-1000:]
            if isinstance(bvid, str) and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) and isinstance(pages, list)}


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return widget, layout


class DanmakuPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = dict(DM_DEFAULTS)
        self.setMinimumHeight(150)
        self.setMaximumHeight(170)
        self.setAccessibleName("弹幕样式示意，非最终视频预览")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#d5e3ec"))
        gradient.setColorAt(1, QColor("#aabfcd"))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 9, 9)
        p = self.profile
        painter.setFont(QFont("Microsoft YaHei UI", max(9, round(p["font"] * .43)), QFont.Weight.DemiBold))
        painter.setOpacity(p["opacity"] / 100)
        for enabled, text, x, y in [(p["top"], "精彩的瞬间", .5, .18), (p["scroll"], "这段值得再看一遍", .07, .39), (p["bottom"], "下次一定", .5, .53 if p["area"] <= 50 else .77)]:
            if enabled:
                left = int(self.width() * x) - (painter.fontMetrics().horizontalAdvance(text) // 2 if x == .5 else 0)
                painter.setPen(QColor("#667e90"))
                painter.drawText(left + 1, int(self.height() * y) + 1, text)
                painter.setPen(QColor("white"))
                painter.drawText(left, int(self.height() * y), text)
        painter.setOpacity(1)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#415c6f"))
        if p["reserve"]:
            y = int(self.height() * .85)
            painter.setPen(QPen(QColor("#8399ac"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(0, y, self.width(), y)
            painter.drawText(self.rect().adjusted(8, y, -8, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "字幕留白区")
        painter.setPen(QColor("#415c6f"))
        painter.drawText(10, self.height() - 9, "样式示意 · 示例弹幕")


class DanmakuDialog(QDialog):
    def __init__(self, profile, manager, parent):
        super().__init__(parent)
        self.manager = manager
        self.reference = manager.danmaku_reference
        self.profile = resolve_profile(profile, self.reference)
        self.controls, self.outputs = {}, {}
        self.setWindowTitle("弹幕设置与预览")
        self.resize(560, 720)
        self.setStyleSheet("QDialog, QDialog QWidget {background:#fff;} QLabel, QCheckBox {background:transparent;} QLabel#badge {background:#e5f6fc;color:#1685ad;border-radius:12px;padding:6px 12px;} QLabel#dmIsolation {background:#eaf7fc;color:#0086b0;border-radius:7px;padding:9px;} QPushButton#primary {background:#00a2d6;color:white;border-color:#00a2d6;}")
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(12)
        row = QHBoxLayout()
        self.follow = QCheckBox("跟随网页设置")
        self.follow.toggled.connect(self.set_follow)
        row.addWidget(self.follow)
        row.addStretch()
        self.source = label("", "badge")
        self.source.setWordWrap(False)
        row.addWidget(self.source)
        layout.addLayout(row)
        layout.addWidget(label("这里的修改仅影响下载，不会修改B站网页。", "dmIsolation"))
        self.web_reference = label("", "muted")
        layout.addWidget(self.web_reference)
        reference_button = button("采用扩展最新参考", self.update_reference)
        reference_button.setToolTip("先在Edge扩展中读取当前视频页设置；桌面端使用扩展最近传来的只读参考。")
        layout.addWidget(reference_button)
        self.preview = DanmakuPreview()
        layout.addWidget(self.preview)
        grid = QGridLayout()
        for index, (key, title, low, high, unit) in enumerate([("font", "字号", 16, 48, ""), ("opacity", "不透明度", 10, 100, "%"), ("duration", "单条滚动时长", 4, 20, " 秒")]):
            group = QVBoxLayout()
            head = QHBoxLayout()
            head.addWidget(label(title))
            output = label("")
            head.addStretch()
            head.addWidget(output)
            group.addLayout(head)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.valueChanged.connect(lambda value, key=key: self.change(key, value))
            group.addWidget(slider)
            self.controls[key], self.outputs[key] = slider, (output, unit)
            grid.addLayout(group, index // 2, index % 2)
        group = QVBoxLayout()
        group.addWidget(label("显示区域"))
        area = QComboBox()
        for value, text in [(25, "上1/4屏"), (50, "上半屏"), (75, "上3/4屏"), (100, "全屏")]:
            area.addItem(text, value)
        area.currentIndexChanged.connect(lambda _: self.change("area", area.currentData()))
        self.controls["area"] = area
        group.addWidget(area)
        grid.addLayout(group, 1, 1)
        layout.addLayout(grid)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for key, title in [("scroll", "滚动"), ("top", "顶部"), ("bottom", "底部"), ("reserve", "字幕留白")]:
            control = QCheckBox(title)
            control.toggled.connect(lambda value, key=key: self.change(key, value))
            self.controls[key] = control
            row.addWidget(control)
        advanced_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label("每10秒最多弹幕条数"))
        density = QSpinBox()
        density.setRange(10, 200)
        density.valueChanged.connect(lambda value: self.change("density", value))
        self.controls["density"] = density
        row.addWidget(density)
        advanced_layout.addLayout(row)
        advanced_layout.addWidget(label("屏蔽关键词（逗号或换行分隔）", "muted"))
        keywords = QPlainTextEdit()
        keywords.setMaximumHeight(65)
        keywords.textChanged.connect(lambda: self.change("keywords", keywords.toPlainText()[:2000]))
        self.controls["keywords"] = keywords
        advanced_layout.addWidget(keywords)
        for key, title in [("save_ass", "额外保存ASS弹幕文件"), ("keep_original", "同时保留普通原视频")]:
            control = QCheckBox(title)
            control.toggled.connect(lambda value, key=key: self.change(key, value))
            self.controls[key] = control
            advanced_layout.addWidget(control)
        advanced.hide()
        layout.addWidget(button("更多设置", lambda: advanced.setVisible(not advanced.isVisible())))
        layout.addWidget(advanced)
        layout.addWidget(label("未读取的项目采用下载器默认值。智能防遮挡、BAS等特殊效果暂不支持。滚动时长越大，速度越慢。", "muted"))
        layout.addWidget(label("弹幕版需要重新编码，耗时更长；写入画面后无法单独关闭。ASS文件可由兼容播放器单独加载。", "muted"))
        scroll.setWidget(body)
        outer.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("应用到下载")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setObjectName("primary")
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        outer.addWidget(buttons)
        self.render()

    def update_reference(self):
        self.reference = self.manager.danmaku_reference
        self.profile = resolve_profile(self.profile, self.reference)
        self.render()

    def set_follow(self, checked):
        self.profile["source"] = "web" if checked else "custom"
        if checked:
            base = dict(self.profile)
            for key in WEB_FIELDS:
                base[key] = DM_DEFAULTS[key]
            self.profile = resolve_profile(base, self.reference)
        self.render()

    def change(self, key, value):
        self.profile.update({key: value, "source": "custom"})
        self.profile = normalize_profile(self.profile)
        self.render()

    def render(self):
        self.follow.blockSignals(True)
        self.follow.setChecked(self.profile["source"] == "web")
        self.follow.blockSignals(False)
        self.source.setText("已自定义 · 仅影响下载" if self.profile["source"] == "custom" else "跟随网页" if self.reference else "采用下载器默认值")
        names = {"font": "字号", "opacity": "不透明度", "area": "显示区域", "duration": "滚动秒数", "scroll": "滚动", "top": "顶部", "bottom": "底部", "reserve": "字幕留白"}
        if self.reference:
            values = self.reference["values"]
            text = " · ".join(names[key] + " " + (("开" if values[key] else "关") if isinstance(values[key], bool) else str(values[key]) + ("%" if key in {"opacity", "area"} else "")) for key in self.reference["fields"])
            text += "；其余项目未读取，使用默认值。" if len(self.reference["fields"]) < len(WEB_FIELDS) else ""
        else:
            text = "尚未收到网页设置。请打开Edge扩展读取当前视频页；这里使用下载器默认值，不会修改网页。"
        self.web_reference.setText("网页参考 · 扩展最近读取 · 只读\n" + text)
        for key, control in self.controls.items():
            control.blockSignals(True)
            value = self.profile[key]
            if isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QComboBox):
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control, QPlainTextEdit):
                if control.toPlainText() != value:
                    control.setPlainText(value)
            else:
                control.setValue(value)
            control.blockSignals(False)
        for key, (output, unit) in self.outputs.items():
            output.setText(str(self.profile[key]) + unit)
        self.preview.profile = self.profile
        self.preview.update()


class FavoriteGrid(QListWidget):
    viewportChanged = Signal()

    def __init__(self):
        super().__init__()
        self.display_mode = "card"
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setDragEnabled(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListWidget {background:#f7f9fc;border:0;} QListWidget::item {padding:0;border:0;}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def set_display_mode(self, mode):
        self.display_mode = "list" if mode == "list" else "card"
        self.reflow()
        self.doItemsLayout()

    def reflow(self):
        width = max(1, self.viewport().width())
        if self.display_mode == "list":
            self.setGridSize(QSize(width, 52))
            self.viewportChanged.emit()
            return
        columns = max(2, min(5, width // 220))
        cell = width // columns
        self.setGridSize(QSize(cell, int((cell - 10) * 9 / 16) + 90))
        self.viewportChanged.emit()


class FavoriteDelegate(QStyledItemDelegate):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.check_icon = QIcon(str(RESOURCE_ROOT / "assets" / "check.svg"))

    def sizeHint(self, option, index):
        return self.owner.favorite_list.gridSize()

    def paint(self, painter, option, index):
        video = index.data(Qt.ItemDataRole.UserRole)
        if not video:
            return
        if self.owner.favorite_list.display_mode == "list":
            self.paint_list(painter, option, index, video)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 4, -4, -4)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        painter.setPen(QPen(QColor("#009bcc" if checked else "#e0e7ef"), 2 if checked else 1))
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(r, 9, 9)
        cover = QRect(r.x() + 1, r.y() + 1, r.width() - 2, int((r.width() - 2) * 9 / 16))
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(r, 9, 9)
        painter.setClipPath(clip)
        painter.fillRect(cover, QColor("#e6edf4"))
        url = video.get("thumbnail", "")
        pixmap = self.owner.favorite_covers.get(url)
        if pixmap is not None:
            self.owner.favorite_covers.move_to_end(url)
            scaled = pixmap.scaled(cover.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(cover, scaled, QRect((scaled.width() - cover.width()) // 2, (scaled.height() - cover.height()) // 2, cover.width(), cover.height()))
        else:
            painter.setPen(QColor("#8998ac"))
            painter.drawText(cover, Qt.AlignmentFlag.AlignCenter, "已失效" if not video["available"] else "暂无封面 · 右键重试" if url in self.owner.favorite_cover_errors else "封面加载中" if url else "暂无封面")
        painter.restore()

        if not video["available"]:
            painter.setOpacity(.5)
        indicator = QRect(r.x() + 9, r.y() + 9, 17, 17)
        painter.setBrush(QColor("#00a2d6" if checked else "#ffffff"))
        painter.setPen(QPen(QColor("#00a2d6" if checked else "#c7d3e2"), 1))
        painter.drawRoundedRect(indicator, 4, 4)
        if checked:
            self.check_icon.paint(painter, indicator)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        time = duration(video.get("duration"))
        text_width = painter.fontMetrics().horizontalAdvance(time) + 10
        time_rect = QRect(cover.right() - text_width - 5, cover.bottom() - 22, text_width, 18)
        painter.fillRect(time_rect, QColor(37, 48, 71, 190))
        painter.setPen(QColor("white"))
        painter.drawText(time_rect, Qt.AlignmentFlag.AlignCenter, time)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#253047"))
        text = str(video["title"]).replace("\n", " ")
        available_width = r.width() - 18
        metrics = painter.fontMetrics()
        split = 0
        while split < len(text) and metrics.horizontalAdvance(text[:split + 1]) <= available_width:
            split += 1
        title = text[:split] + ("\n" + metrics.elidedText(text[split:], Qt.TextElideMode.ElideRight, available_width) if split < len(text) else "")
        painter.drawText(QRect(r.x() + 9, cover.bottom() + 7, available_width, 42), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(QColor("#7b879b"))
        painter.drawText(QRect(r.x() + 9, r.bottom() - 25, r.width() - 90, 19), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(video.get("author") or video.get("bvid", ""), Qt.TextElideMode.ElideRight, max(0, r.width() - 90)))
        pages = self.owner.favorite_parts.get(video.get("bvid"))
        text = favorite_part_text(video, pages)
        painter.setPen(QColor("#0086b0" if video["available"] and video.get("page_count") != 1 else "#8998ac"))
        painter.drawText(QRect(r.right() - 78, r.bottom() - 25, 70, 19), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def paint_list(self, painter, option, index, video):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 3, -4, -3)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        painter.setPen(QPen(QColor("#009bcc" if checked else "#e0e7ef"), 1))
        painter.setBrush(QColor("#eff9fd" if checked else "#ffffff"))
        painter.drawRoundedRect(r, 7, 7)
        if not video["available"]:
            painter.setOpacity(.5)
        indicator = QRect(r.x() + 9, r.center().y() - 8, 17, 17)
        painter.setBrush(QColor("#00a2d6" if checked else "#ffffff"))
        painter.setPen(QColor("#00a2d6" if checked else "#c7d3e2"))
        painter.drawRoundedRect(indicator, 4, 4)
        if checked:
            self.check_icon.paint(painter, indicator)
        cover = QRect(r.x() + 35, r.y() + 5, 64, 36)
        painter.fillRect(cover, QColor("#e6edf4"))
        pixmap = self.owner.favorite_covers.get(video.get("thumbnail", ""))
        if pixmap is not None:
            painter.drawPixmap(cover, pixmap)
            self.owner.favorite_covers.move_to_end(video["thumbnail"])
        left, room = r.x() + 110, max(20, r.width() - 265)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#253047"))
        painter.drawText(QRect(left, r.y() + 4, room, 20), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(video["title"], Qt.TextElideMode.ElideRight, room))
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(QColor("#7b879b"))
        meta = (video.get("author") or "") + " · " + video.get("bvid", "")
        painter.drawText(QRect(left, r.y() + 24, room, 17), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, room))
        painter.drawText(QRect(r.right() - 145, r.y(), 58, r.height()), Qt.AlignmentFlag.AlignCenter, duration(video.get("duration")))
        painter.setPen(QColor("#0086b0" if video["available"] and video.get("page_count") != 1 else "#8998ac"))
        pages = self.owner.favorite_parts.get(video.get("bvid"))
        text = favorite_part_text(video, pages)
        painter.drawText(QRect(r.right() - 80, r.y(), 70, r.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        video = index.data(Qt.ItemDataRole.UserRole)
        if not video or not video["available"]:
            return False
        click = event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton
        key = event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
        if click and video.get("page_count") != 1 and event.position().x() > option.rect.right() - 88 and event.position().y() > option.rect.bottom() - 35:
            self.owner.open_favorite_parts(video)
            return True
        if click or key:
            current = index.data(Qt.ItemDataRole.CheckStateRole)
            model.setData(index, Qt.CheckState.Unchecked.value if current in (Qt.CheckState.Checked, Qt.CheckState.Checked.value) else Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
            return True
        return False


class MainWindow(QMainWindow):
    def __init__(self, settings, manager, bridge):
        super().__init__()
        self.settings, self.manager, self.bridge = settings, manager, bridge
        self.meta = None
        self.favorites = []
        self.favorite_folder_id = None
        self.favorite_states, self.favorite_nodes = {}, {}
        self.favorite_selected = set()
        self.favorite_parts = normalize_part_selections(settings.values.get("favorite_parts"))
        self.favorite_next_page, self.favorite_total = 1, 0
        self.favorite_has_more, self.favorite_loading = False, False
        self.favorite_scan, self.favorite_generation = None, 0
        self.favorite_covers = OrderedDict()
        self.favorite_cover_errors, self.favorite_cover_pending = set(), set()
        try:
            self.danmaku_profile = normalize_profile(settings.values.get("danmaku"))
        except ValueError:
            self.danmaku_profile = dict(DM_DEFAULTS)
            settings.error += " 弹幕设置无效，已使用默认值，原设置文件已保留。"
        self.danmaku_rows, self.danmaku_revision = [], -1
        self.jobs, self.job_id = {}, 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.thumbnail_pool = QThreadPool(self)
        self.thumbnail_pool.setMaxThreadCount(3)
        self.favorite_image_timer = QTimer(self)
        self.favorite_image_timer.setSingleShot(True)
        self.favorite_image_timer.timeout.connect(self.load_visible_covers)
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
        self.tabs.currentChanged.connect(lambda _: self.schedule_favorite_images())
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
        content.addWidget(self.build_danmaku_entry())
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
        self.cancel_all_button = button("全部取消", self.cancel_all_downloads)
        self.cancel_all_button.setToolTip("停止当前下载并取消全部排队任务，已完成的视频保留。")
        row.addWidget(self.cancel_all_button)
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
        queue_layout.addWidget(label("双击任务查看详情。暂停队列会等待当前任务完成；全部取消会停止当前下载及排队任务，已完成文件保留。", "muted"))
        layout.addWidget(queue_card, 1)
        return widget

    def build_favorites_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 16, 0, 0)
        content_card, content = card()
        content.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(label("收藏夹 · 浏览并选择", "sectionTitle"))
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("选择收藏夹", None)
        self.folder_combo.currentIndexChanged.connect(self.change_favorite_folder)
        self.folder_combo.setMinimumWidth(220)
        row.addWidget(self.folder_combo, 1)
        self.favorite_view_buttons = {}
        view_mode = self.settings.values.get("favorite_view", "card")
        for mode, title in [("card", "卡片"), ("list", "列表")]:
            control = button(title, lambda _, mode=mode: self.set_favorite_view(mode))
            control.setObjectName("viewMode")
            control.setCheckable(True)
            control.setChecked(mode == view_mode)
            self.favorite_view_buttons[mode] = control
            row.addWidget(control)
        self.load_folders_button = button("刷新收藏夹", self.load_folders)
        row.addWidget(self.load_folders_button)
        content.addLayout(row)
        row = QHBoxLayout()
        self.favorite_search = QLineEdit()
        self.favorite_search.setPlaceholderText("筛选已加载视频：标题 / UP主 / BV号")
        self.favorite_search.textChanged.connect(self.filter_favorites)
        row.addWidget(self.favorite_search, 1)
        self.favorite_search_all = button("搜索整个收藏夹", lambda: self.scan_favorites("search"))
        row.addWidget(self.favorite_search_all)
        self.favorite_stop = button("停止读取", self.stop_favorite_scan)
        self.favorite_stop.hide()
        row.addWidget(self.favorite_stop)
        content.addLayout(row)
        row = QHBoxLayout()
        self.favorite_count = label("选择收藏夹后，视频会分批显示。", "muted")
        row.addWidget(self.favorite_count, 1)
        self.favorite_select_visible = button("全选已加载", self.select_visible_favorites)
        row.addWidget(self.favorite_select_visible)
        row.addWidget(button("取消全部选择", self.clear_favorite_selection))
        self.favorite_select_whole = button("选择整个收藏夹", lambda: self.scan_favorites("select"))
        row.addWidget(self.favorite_select_whole)
        content.addLayout(row)
        self.favorite_list = FavoriteGrid()
        self.favorite_list.set_display_mode(view_mode)
        self.favorite_list.setItemDelegate(FavoriteDelegate(self))
        self.favorite_list.itemChanged.connect(self.favorite_selection_changed)
        self.favorite_list.viewportChanged.connect(self.schedule_favorite_images)
        self.favorite_list.verticalScrollBar().valueChanged.connect(self.favorite_scrolled)
        self.favorite_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.favorite_list.customContextMenuRequested.connect(self.favorite_context_menu)
        content.addWidget(self.favorite_list, 1)
        self.favorite_more = button("继续加载", self.load_favorites)
        self.favorite_more.setEnabled(False)
        self.favorite_settings = QWidget()
        settings_layout = QVBoxLayout(self.favorite_settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("画质"))
        self.favorite_quality = QComboBox()
        self.favorite_quality.addItem("跟随默认画质", -1)
        for value, title in QUALITY_NAMES.items():
            self.favorite_quality.addItem(title, value)
        row.addWidget(self.favorite_quality)
        self.favorite_all_pages = QCheckBox("下载全部分P（单条选择优先）")
        self.favorite_all_pages.setChecked(True)
        self.favorite_all_pages.toggled.connect(self.update_favorite_count)
        row.addWidget(self.favorite_all_pages)
        row.addStretch()
        settings_layout.addLayout(row)
        settings_layout.addWidget(self.build_danmaku_entry())
        self.favorite_settings.hide()
        content.addWidget(self.favorite_settings)
        row = QHBoxLayout()
        row.addWidget(button("本次下载设置", lambda: self.favorite_settings.setVisible(not self.favorite_settings.isVisible())))
        row.addWidget(self.favorite_more)
        self.favorite_summary = label("全部分P · 普通MP4", "muted")
        row.addWidget(self.favorite_summary, 1)
        self.favorite_add_button = button("加入下载队列（0）", self.add_favorites, True)
        self.favorite_add_button.setEnabled(False)
        row.addWidget(self.favorite_add_button)
        content.addLayout(row)
        content.addWidget(label("保存目录使用上方当前目录，入队时固定。默认不勾选；已知失效视频会置灰。右键卡片可重试封面或选择分P。", "muted"))
        layout.addWidget(content_card, 1)
        return widget

    def build_danmaku_entry(self):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        enabled = QCheckBox("带弹幕")
        enabled.setChecked(self.danmaku_profile["enabled"])
        enabled.toggled.connect(self.set_danmaku_enabled)
        hint = label("未读取网页 · 采用默认", "muted")
        row.addWidget(enabled)
        row.addWidget(hint, 1)
        row.addWidget(button("设置与预览", self.edit_danmaku))
        self.danmaku_rows.append((enabled, hint))
        return widget

    def set_danmaku_enabled(self, enabled):
        self.danmaku_profile["enabled"] = enabled
        self.settings.set("danmaku", self.danmaku_profile)
        self.update_danmaku_rows()

    def update_danmaku_rows(self):
        for checkbox, hint in self.danmaku_rows:
            checkbox.blockSignals(True)
            checkbox.setChecked(self.danmaku_profile["enabled"])
            checkbox.blockSignals(False)
            hint.setText("已自定义 · 仅影响下载" if self.danmaku_profile["source"] == "custom" else "跟随网页 · 仅影响下载" if self.manager.danmaku_reference else "未读取网页 · 采用默认")
        if hasattr(self, "favorite_summary"):
            self.update_favorite_count()

    def edit_danmaku(self):
        dialog = DanmakuDialog(self.danmaku_profile, self.manager, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.danmaku_profile = normalize_profile(dialog.profile)
            self.settings.set("danmaku", self.danmaku_profile)
            self.update_danmaku_rows()
        dialog.deleteLater()

    def danmaku_snapshot(self):
        profile = dict(self.danmaku_profile)
        if profile["source"] == "web":
            for key in WEB_FIELDS:
                profile[key] = DM_DEFAULTS[key]
        return resolve_profile(profile, self.manager.danmaku_reference)

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

    def async_job(self, function, success, error=None, pool=None):
        self.job_id += 1
        job_id = self.job_id
        worker = Worker(job_id, function)
        self.jobs[job_id] = (worker, success, error)
        worker.signals.success.connect(self.job_success, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self.job_error, Qt.ConnectionType.QueuedConnection)
        (pool or self.pool).start(worker)

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
        profile = self.danmaku_snapshot()
        if profile["enabled"] and self.quality.currentData() in {125, 126}:
            self.notice.setText("弹幕版暂不支持HDR和杜比视界，请选择普通SDR画质。")
            return
        specs = [{"url": self.meta["url"], "page": p, "title": self.meta["title"], "quality": self.quality.currentData(), "directory": self.directory.text(), "danmaku": profile} for p in selected]
        ids = self.enqueue_specs(specs)
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
        if self.manager.danmaku_revision != self.danmaku_revision:
            self.danmaku_revision = self.manager.danmaku_revision
            self.update_danmaku_rows()
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
        self.cancel_all_button.setEnabled(bool(counts) or getattr(self, "favorite_submitting", False))
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
            previous = self.favorite_folder_id
            self.folder_combo.blockSignals(True)
            self.folder_combo.clear()
            self.folder_combo.addItem("选择收藏夹", None)
            for folder in folders:
                self.folder_combo.addItem(f'{folder["title"]} · {folder.get("media_count", 0)} 个', folder["id"])
            index = self.folder_combo.findData(previous)
            self.folder_combo.setCurrentIndex(max(0, index))
            self.folder_combo.blockSignals(False)
            if self.folder_combo.currentData() != previous:
                self.change_favorite_folder()
            self.notice.setText(f"已加载 {len(folders)} 个收藏夹。")
        def error(message):
            self.load_folders_button.setEnabled(True)
            self.notice.setText(message)
        self.async_job(lambda: favorite_folders(cookies), success, error)

    def change_favorite_folder(self, *_):
        if self.favorite_folder_id:
            self.favorite_states[self.favorite_folder_id] = {"items": self.favorites, "selected": set(self.favorite_selected), "next": self.favorite_next_page, "more": self.favorite_has_more, "total": self.favorite_total, "query": self.favorite_search.text(), "scroll": self.favorite_list.verticalScrollBar().value()}
        self.favorite_generation += 1
        self.favorite_scan, self.favorite_loading = None, False
        self.favorite_page_error = False
        self.favorite_folder_id = self.folder_combo.currentData()
        state = self.favorite_states.get(self.favorite_folder_id, {})
        self.favorites = state.get("items", [])
        self.favorite_selected = set(state.get("selected", []))
        self.favorite_next_page, self.favorite_total = state.get("next", 1), state.get("total", 0)
        self.favorite_has_more = state.get("more", bool(self.favorite_folder_id))
        self.favorite_nodes.clear()
        self.favorite_list.clear()
        self.favorite_search.blockSignals(True)
        self.favorite_search.setText(state.get("query", ""))
        self.favorite_search.blockSignals(False)
        self.render_favorites()
        QTimer.singleShot(0, lambda value=state.get("scroll", 0): self.favorite_list.verticalScrollBar().setValue(value))
        if self.favorite_folder_id and not self.favorites:
            self.load_favorites()

    def set_favorite_view(self, mode):
        mode = "list" if mode == "list" else "card"
        anchor = self.favorite_list.itemAt(QPoint(12, 12))
        if anchor is None:
            anchor = next((self.favorite_list.item(i) for i in range(self.favorite_list.count()) if not self.favorite_list.item(i).isHidden()), None)
        self.favorite_list.set_display_mode(mode)
        for key, control in self.favorite_view_buttons.items():
            control.setChecked(key == mode)
        self.settings.set("favorite_view", mode)
        if anchor:
            generation = self.favorite_generation
            QTimer.singleShot(0, lambda: self.favorite_list.scrollToItem(anchor, QAbstractItemView.ScrollHint.PositionAtTop) if generation == self.favorite_generation else None)
        self.schedule_favorite_images()

    @staticmethod
    def thumbnail_url(url):
        parsed = urlparse(url or "")
        if parsed.scheme not in {"http", "https"} or not (parsed.hostname or "").endswith(".hdslb.com"):
            return ""
        return url.replace("http://", "https://", 1) + ("@320w_180h_1c.webp" if not parsed.query and "@" not in parsed.path else "")

    def load_favorites(self):
        if not self.favorite_folder_id or self.favorite_loading or not self.favorite_has_more:
            return
        folder_id, generation, page = self.favorite_folder_id, self.favorite_generation, self.favorite_next_page
        self.favorite_loading, self.favorite_page_error = True, False
        self.update_favorite_count()
        cookies = dict(self.manager.cookies)
        def success(data):
            if generation != self.favorite_generation:
                return
            self.favorite_loading = False
            known = {video["key"] for video in self.favorites}
            for video in data["items"]:
                if video["key"] not in known:
                    video["thumbnail"] = self.thumbnail_url(video.get("cover"))
                    self.favorites.append(video)
                    known.add(video["key"])
            self.favorite_next_page = data["page"] + 1
            self.favorite_has_more = data["has_more"]
            self.favorite_total = data["total"] or max(self.favorite_total, len(self.favorites))
            self.render_favorites()
            if self.favorite_scan:
                QTimer.singleShot(160, lambda: self.continue_favorite_scan(generation))
        def error(message):
            if generation == self.favorite_generation:
                self.favorite_loading, self.favorite_page_error, self.favorite_scan = False, True, None
                self.update_favorite_count()
                self.notice.setText(message + " 可点击“重试读取”。")
        self.async_job(lambda: favorite_page(folder_id, cookies, page), success, error)

    def render_favorites(self):
        self.favorite_list.blockSignals(True)
        for video in self.favorites:
            item = self.favorite_nodes.get(video["key"])
            if item is None:
                item = QListWidgetItem(video["title"])
                item.setData(Qt.ItemDataRole.UserRole, video)
                item.setToolTip(video["title"] + "\n" + (video.get("author") or "") + "\n" + video.get("bvid", ""))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if not video["available"]:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.favorite_list.addItem(item)
                self.favorite_nodes[video["key"]] = item
            item.setCheckState(Qt.CheckState.Checked if video["bvid"] in self.favorite_selected else Qt.CheckState.Unchecked)
        self.favorite_list.blockSignals(False)
        self.filter_favorites()

    def filter_favorites(self, *_):
        term = self.favorite_search.text().strip().lower()
        for i in range(self.favorite_list.count()):
            item = self.favorite_list.item(i)
            video = item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(bool(term and term not in (video["title"] + " " + video.get("author", "") + " " + video.get("bvid", "")).lower()))
        self.update_favorite_count()
        self.schedule_favorite_images()

    def favorite_selection_changed(self, item):
        video = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked and video["available"]:
            self.favorite_selected.add(video["bvid"])
        else:
            self.favorite_selected.discard(video["bvid"])
        self.update_favorite_count()

    def update_favorite_count(self, *_):
        query = self.favorite_search.text().strip()
        matched = sum(not self.favorite_list.item(i).isHidden() for i in range(self.favorite_list.count()))
        text = f"已加载 {len(self.favorites)} / {self.favorite_total or '—'} 个 · 已选 {len(self.favorite_selected)} 个"
        if query:
            text += f" · 匹配 {matched} 个"
        text += " · 正在读取完整清单…" if self.favorite_scan else " · 加载中…" if self.favorite_loading else ""
        self.favorite_count.setText(text)
        self.favorite_select_visible.setText("全选匹配结果" if query else "全选已加载")
        self.favorite_add_button.setText(f"加入下载队列（{len(self.favorite_selected)}）")
        self.favorite_add_button.setEnabled(bool(self.favorite_selected) and not getattr(self, "favorite_submitting", False))
        self.favorite_more.setEnabled(bool(self.favorite_folder_id) and self.favorite_has_more and not self.favorite_loading and not self.favorite_scan)
        self.favorite_more.setText("重试读取" if getattr(self, "favorite_page_error", False) else "加载中…" if self.favorite_loading else "继续加载" if self.favorite_has_more else "已加载全部")
        for control in (self.favorite_select_whole, self.favorite_search_all):
            control.setEnabled(bool(self.favorite_folder_id) and not self.favorite_scan)
        self.favorite_stop.setVisible(bool(self.favorite_scan))
        self.favorite_summary.setText(("全部分P" if self.favorite_all_pages.isChecked() else "默认P1") + (" · 弹幕版（仅影响下载）" if self.danmaku_profile["enabled"] else " · 普通MP4"))

    def select_visible_favorites(self):
        for i in range(self.favorite_list.count()):
            item = self.favorite_list.item(i)
            video = item.data(Qt.ItemDataRole.UserRole)
            if not item.isHidden() and video["available"]:
                self.favorite_selected.add(video["bvid"])
        self.render_favorites()

    def clear_favorite_selection(self):
        self.favorite_selected.clear()
        self.render_favorites()

    def scan_favorites(self, goal):
        if not self.favorite_folder_id or self.favorite_scan:
            return
        self.favorite_scan = goal
        self.update_favorite_count()
        self.continue_favorite_scan(self.favorite_generation)

    def continue_favorite_scan(self, generation):
        if generation != self.favorite_generation or not self.favorite_scan:
            return
        if self.favorite_loading:
            QTimer.singleShot(100, lambda: self.continue_favorite_scan(generation))
        elif self.favorite_has_more:
            self.load_favorites()
        else:
            if self.favorite_scan == "select":
                self.favorite_selected = {video["bvid"] for video in self.favorites if video["available"]}
                self.notice.setText("已选择整个收藏夹中的可用视频；已知失效视频已跳过。")
            else:
                self.notice.setText("已读取完整清单，搜索范围覆盖整个收藏夹。")
            self.favorite_scan = None
            self.render_favorites()

    def stop_favorite_scan(self):
        self.favorite_scan = None
        self.update_favorite_count()
        self.notice.setText("已停止继续读取，保留已加载内容和原有勾选。")

    def favorite_scrolled(self, *_):
        self.schedule_favorite_images()
        bar = self.favorite_list.verticalScrollBar()
        if self.tabs.currentIndex() == 1 and bar.maximum() - bar.value() < 100 and not self.favorite_search.text().strip() and not self.favorite_scan and not getattr(self, "favorite_page_error", False):
            self.load_favorites()

    def schedule_favorite_images(self):
        self.favorite_image_timer.start(70)

    def load_visible_covers(self):
        if self.tabs.currentIndex() != 1 or not self.favorite_list.isVisible():
            return
        region = self.favorite_list.viewport().rect().adjusted(0, -160, 0, 200)
        for i in range(self.favorite_list.count()):
            if len(self.favorite_cover_pending) >= 3:
                break
            item = self.favorite_list.item(i)
            if item.isHidden() or not region.intersects(self.favorite_list.visualItemRect(item)):
                continue
            video = item.data(Qt.ItemDataRole.UserRole)
            url = video.get("thumbnail", "")
            if not video["available"] or not url or url in self.favorite_covers or url in self.favorite_cover_errors or url in self.favorite_cover_pending:
                continue
            self.favorite_cover_pending.add(url)
            def finish(data, url=url):
                self.favorite_cover_pending.discard(url)
                pixmap = QPixmap()
                if pixmap.loadFromData(data) and not pixmap.isNull():
                    self.favorite_covers[url] = pixmap.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    while len(self.favorite_covers) > 180:
                        self.favorite_covers.popitem(last=False)
                else:
                    self.favorite_cover_errors.add(url)
                self.favorite_list.viewport().update()
                self.schedule_favorite_images()
            def failed(_, url=url):
                self.favorite_cover_pending.discard(url)
                self.favorite_cover_errors.add(url)
                self.favorite_list.viewport().update()
                self.schedule_favorite_images()
            self.async_job(lambda url=url: self.fetch_cover(url), finish, failed, pool=self.thumbnail_pool)

    def favorite_context_menu(self, point):
        item = self.favorite_list.itemAt(point)
        if not item:
            return
        video = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if video["available"]:
            if video.get("page_count") != 1:
                menu.addAction(favorite_part_text(video, None), lambda: self.open_favorite_parts(video))
            menu.addAction("在B站打开", lambda: QDesktopServices.openUrl(QUrl(f'https://www.bilibili.com/video/{video["bvid"]}/')))
        if video.get("thumbnail"):
            def retry():
                self.favorite_cover_errors.discard(video["thumbnail"])
                self.favorite_covers.pop(video["thumbnail"], None)
                self.schedule_favorite_images()
            menu.addAction("重试封面", retry)
        menu.exec(self.favorite_list.mapToGlobal(point))

    def open_favorite_parts(self, video, meta=None):
        if not video["available"] or video.get("page_count") == 1:
            return
        if meta is None:
            generation = self.favorite_generation
            pending = getattr(self, "favorite_part_pending", set())
            self.favorite_part_pending = pending
            request = (generation, video["bvid"])
            if request in pending:
                return
            pending.add(request)
            self.notice.setText("正在读取视频分P…")
            cookies = dict(self.manager.cookies)
            def success(result):
                pending.discard(request)
                if generation != self.favorite_generation:
                    return
                video["page_count"] = len(result["pages"])
                node = self.favorite_nodes.get(video["key"])
                if node is not None:
                    node.setData(Qt.ItemDataRole.UserRole, video)
                if video["page_count"] == 1:
                    if video["bvid"] in self.favorite_parts:
                        self.remember_favorite_parts(video["bvid"], None)
                    self.render_favorites()
                    self.notice.setText("这是单视频，无需选择分P，勾选视频即可下载。")
                    return
                if not video["page_count"]:
                    self.notice.setText("未读取到可用的视频内容，请稍后重试。")
                    return
                self.open_favorite_parts(video, result)
            def error(message):
                pending.discard(request)
                if generation == self.favorite_generation:
                    self.notice.setText(message)
            self.async_job(lambda: video_metadata(video["bvid"], cookies), success, error)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("选择视频分P")
        dialog.resize(550, 440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(video["title"], "sectionTitle"))
        all_parts = QCheckBox("全选分P")
        layout.addWidget(all_parts)
        listing = QListWidget()
        layout.addWidget(listing, 1)
        hint = label("首次默认不选，应用后记住本条视频的选择（仅本机）。单条选择优先于批量“下载全部分P”设置。", "muted")
        layout.addWidget(hint)
        all_parts.toggled.connect(lambda checked: self.select_items(listing, checked))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用选择")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        generation = self.favorite_generation
        selected = self.favorite_parts.get(video["bvid"], [])
        for part in meta["pages"]:
            item = QListWidgetItem(f'P{part["page"]} {part["part"]} · {duration(part["duration"])}')
            item.setData(Qt.ItemDataRole.UserRole, part["page"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if part["page"] in selected else Qt.CheckState.Unchecked)
            listing.addItem(item)
        all_parts.setChecked(all(listing.item(i).checkState() == Qt.CheckState.Checked for i in range(listing.count())))
        if dialog.exec() == QDialog.DialogCode.Accepted and generation == self.favorite_generation:
            selected = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count()) if listing.item(i).checkState() == Qt.CheckState.Checked]
            self.remember_favorite_parts(video["bvid"], selected)
            if selected:
                self.favorite_selected.add(video["bvid"])
            else:
                self.favorite_selected.discard(video["bvid"])
            self.render_favorites()
        dialog.deleteLater()

    def remember_favorite_parts(self, bvid, pages):
        self.favorite_parts.pop(bvid, None)
        if pages is not None:
            self.favorite_parts[bvid] = pages
        while len(self.favorite_parts) > 1000:
            self.favorite_parts.pop(next(iter(self.favorite_parts)))
        self.settings.set("favorite_parts", dict(self.favorite_parts))

    def cancel_all_downloads(self):
        count = self.manager.cancel_all()
        self.notice.setText(f"已请求取消全部 {count} 个未结束任务，正在清理当前临时文件；已完成的视频保留。" if count else "当前没有未结束的下载，已停止继续提交本批任务。")
        self.refresh()

    def enqueue_specs(self, specs, cancel_generation=None):
        if cancel_generation is None:
            cancel_generation = self.manager.cancel_generation
        ids = []
        for start in range(0, len(specs), 100):
            ids.extend(self.manager.add(specs[start:start + 100], cancel_generation=cancel_generation))
        return ids

    def add_favorites(self):
        if not self.favorite_selected or getattr(self, "favorite_submitting", False):
            return
        profile, specs = self.danmaku_snapshot(), []
        quality = self.favorite_quality.currentData()
        quality = self.default_quality.currentData() if quality == -1 else quality
        if profile["enabled"] and quality in {125, 126}:
            self.notice.setText("弹幕版暂不支持HDR和杜比视界，请选择普通SDR画质。")
            return
        for video in self.favorites:
            if video["bvid"] not in self.favorite_selected or not video["available"]:
                continue
            pages = self.favorite_parts.get(video["bvid"]) if video.get("page_count") != 1 else None
            if pages == []:
                self.notice.setText(f'“{video["title"][:50]}”尚未选择分P，请先选择需要的内容。')
                return
            for page in pages or [1]:
                specs.append({"url": video["bvid"], "page": page, "title": video["title"], "directory": self.directory.text(), "quality": quality, "all_pages": not pages and self.favorite_all_pages.isChecked(), "danmaku": profile})
        if not specs:
            return
        self.favorite_submitting = True
        cancel_generation = self.manager.cancel_generation
        self.update_favorite_count()
        def success(ids):
            self.favorite_submitting = False
            self.update_favorite_count()
            self.notice.setText(f"已加入 {len(ids)} 个收藏夹任务；保存目录和弹幕配置已固定。")
            self.tabs.setCurrentIndex(0)
        def error(message):
            self.favorite_submitting = False
            self.update_favorite_count()
            self.notice.setText("部分任务可能已入队，请先查看下载队列。" + message)
        self.async_job(lambda: self.enqueue_specs(specs, cancel_generation), success, error)

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
