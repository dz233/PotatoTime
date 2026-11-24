"""应用启动、单实例与 Windows 睡眠事件。"""

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QAbstractNativeEventFilter, QStandardPaths, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .audio import Audio
from .controller import Controller
from .storage import Store
from .window import MainWindow


def load_fonts():
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "msyh.ttc"):
        path = fonts / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    QApplication.instance().setFont(QFont("Microsoft YaHei UI", 10))


class PowerEvents(QAbstractNativeEventFilter):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x218 and msg.wParam == 4:  # WM_POWERBROADCAST / PBT_APMSUSPEND
                self.controller.suspend()
        return False, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="TomatoTime 本地番茄钟")
    parser.add_argument("--data-dir", type=Path, help="使用独立数据目录（开发／测试）")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--no-single-instance", action="store_true")
    parser.add_argument("--smoke-exit", type=int, help="指定毫秒后退出，用于启动验证")
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("TomatoTime")
    app.setOrganizationName("TomatoTime")
    app.setQuitOnLastWindowClosed(False)
    load_fonts()
    directory = (args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))).resolve()
    server = None
    if not args.no_single_instance:
        name = "TomatoTime-" + hashlib.sha256(str(directory).encode()).hexdigest()[:20]
        client = QLocalSocket()
        client.connectToServer(name)
        if client.waitForConnected(400):
            client.write(b"open")
            client.waitForBytesWritten(400)
            return 0
        server = QLocalServer()
        if not server.listen(name):
            QLocalServer.removeServer(name)
            if not server.listen(name):
                QMessageBox.critical(None, "无法启动", "无法建立应用单实例，请检查是否已有进程运行。")
                return 1
    store = Store(directory)
    controller = Controller(store)
    audio = Audio(store)
    window = MainWindow(controller, audio, args.no_tray)
    if server:
        def connected():
            socket = server.nextPendingConnection()
            if socket:
                socket.close()
                socket.deleteLater()
            window.reveal()
        server.newConnection.connect(connected)
    native = PowerEvents(controller)
    app.installNativeEventFilter(native)
    def error_hook(exc_type, value, tb):
        log = "".join(traceback.format_exception(exc_type, value, tb))
        with (directory / "errors.log").open("a", encoding="utf-8") as stream:
            stream.write(log + "\n")
        if sys.stderr:
            sys.stderr.write(log)
        QMessageBox.critical(window, "发生错误", f"{value}\n错误已写入数据目录的 errors.log。")
    sys.excepthook = error_hook
    window.show()
    if args.smoke_exit:
        QTimer.singleShot(args.smoke_exit, window.exit_app)
    code = app.exec()
    if server:
        server.close()
    store.close()
    return code
