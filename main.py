"""桌面程序入口。"""
import sys
import traceback
from pathlib import Path

import requests

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from downloader import APP_ROOT
from tasks import Bridge, PORT, Settings, TaskManager
from ui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("B站视频下载器")
    app.setOrganizationName("BiliLocal")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    settings = Settings()
    lock = QLockFile(str(settings.data_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        if "--background" in sys.argv:
            return 0
        try:
            session = requests.Session()
            session.trust_env = False
            with session:
                response = session.post(f"http://127.0.0.1:{PORT}/show-window", json={}, headers={"X-Bili-Token": settings.values["token"]}, timeout=2)
                response.raise_for_status()
        except requests.RequestException:
            QMessageBox.information(None, "程序已在运行", "B站视频下载器已经打开，请点击任务栏右下角的托盘图标恢复窗口。")
        return 0
    manager = TaskManager(settings)
    bridge = Bridge(manager)
    window = MainWindow(settings, manager, bridge)
    if "--background" in sys.argv and window.tray is not None:
        window.tray_notice_shown = True
    else:
        window.show()
    # Automated visual smoke checks use an ordinary render of this same window.
    if "--screenshot" in sys.argv:
        index = sys.argv.index("--screenshot")
        path = sys.argv[index + 1]
        QTimer.singleShot(800, lambda: window.grab().save(path))
        QTimer.singleShot(1200, app.quit)
    result = app.exec()
    manager.shutdown()
    bridge.shutdown()
    lock.unlock()
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        directory = APP_ROOT / "data"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if QApplication.instance():
            QMessageBox.critical(None, "启动失败", "程序启动失败，详情已保存到data/startup-error.log。")
        raise
