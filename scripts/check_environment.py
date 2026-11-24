"""验证项目解释器与 Qt 基础能力，不打开可见窗口。"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

# QApplication 创建前选择离屏平台，仅作用于这个检查进程。
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget


class EnvironmentPreview(QWidget):
    """绘制参考界面的最小视觉样例，仅用于验证 QPainter。"""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(460, 520)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#fafbfe"))

        painter.setPen(QColor("#252830"))
        painter.setFont(QFont("Microsoft YaHei UI", 16))
        painter.drawText(28, 44, "专注时段")
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(QColor("#6a7080"))
        painter.drawText(28, 70, "Qt 环境与离屏绘制验证")

        pen = QPen(QColor("#e9edf4"), 10)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(75, 112, 310, 310)

        pen.setColor(QColor("#0078d4"))
        painter.setPen(pen)
        painter.drawArc(75, 112, 310, 310, 90 * 16, -80 * 16)

        painter.setPen(QColor("#20232b"))
        painter.setFont(QFont("Segoe UI", 46))
        painter.drawText(75, 112, 310, 310, Qt.AlignmentFlag.AlignCenter, "25:00")
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(QColor("#6a7080"))
        painter.drawText(0, 447, 460, 30, Qt.AlignmentFlag.AlignCenter, "环境检查图 · 非功能界面")
        painter.end()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("本项目应通过 uv 使用 Python 3.13")

    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE checks (name TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO checks VALUES (?)", ("sqlite",))
        if connection.execute("SELECT name FROM checks").fetchone() != ("sqlite",):
            raise RuntimeError("SQLite 读写检查失败")

    app = QApplication([sys.argv[0]])
    app.setApplicationName("TomatoTime Environment Check")

    # 离屏平台不一定自动发现 Windows 字体，显式加载用于检查的字体。
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    loaded_fonts = []
    for filename in ("seguiemj.ttf", "segoeui.ttf", "msyh.ttc"):
        font_path = windows_fonts / filename
        if font_path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id >= 0:
                loaded_fonts.extend(QFontDatabase.applicationFontFamilies(font_id))
    if not loaded_fonts:
        raise RuntimeError("未能为离屏检查加载 Windows 字体")

    widget = EnvironmentPreview()
    widget.show()
    app.processEvents()

    output = Path(__file__).resolve().parents[1] / "artifacts" / "environment-check.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(output), "PNG"):
        raise RuntimeError("Qt 离屏图像保存失败")
    widget.close()

    print(f"Python: {sys.version.split()[0]}")
    print(f"Interpreter: {sys.executable}")
    print(f"SQLite: {sqlite3.sqlite_version} (read/write OK)")
    print(f"PySide6: {pyside_version}; Qt: {qVersion()}")
    print(f"Fonts: {', '.join(dict.fromkeys(loaded_fonts))}")
    print("Qt Widgets / QPainter / Multimedia imports: OK")
    print(f"Tray available on offscreen platform: {QSystemTrayIcon.isSystemTrayAvailable()}")
    print(f"Preview: {output}")
    print("Environment check passed; desktop audio/tray/reminders require later live checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
