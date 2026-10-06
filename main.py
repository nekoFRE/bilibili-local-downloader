"""桌面程序入口。"""
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from downloader import APP_ROOT
from tasks import Bridge, Settings, TaskManager
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
        QMessageBox.information(None, "程序已在运行", "B站视频下载器已经打开，请使用现有窗口。")
        return 0
    manager = TaskManager(settings)
    bridge = Bridge(manager)
    window = MainWindow(settings, manager, bridge)
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
