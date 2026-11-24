"""可复用的圆形计时器、对话框与配色。"""

from datetime import timedelta
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from .core import now


def theme(dark=False):
    bg, card, text, muted, line = ("#171b24", "#222835", "#edf0f7", "#a5afc1", "#394254") if dark else ("#f3f5fa", "#ffffff", "#252c39", "#657187", "#e2e7f0")
    return f"""
    QWidget {{font-family: 'Microsoft YaHei UI'; font-size: 13px; color:{text};}}
    QMainWindow, QDialog {{background:{bg};}}
    QWidget#sidebar {{background:{card}; border-right:1px solid {line};}}
    QFrame#card, QWidget#card {{background:{card}; border:1px solid {line}; border-radius:14px;}}
    QLabel#heading {{font-size:26px; font-weight:600;}}
    QLabel#subheading {{font-size:17px; font-weight:600;}}
    QLabel#muted {{color:{muted};}}
    QLabel#stat {{font-size:24px; font-weight:600; color:#1681df;}}
    QPushButton {{background:{card}; border:1px solid {line}; border-radius:7px; padding:8px 14px;}}
    QPushButton:hover {{border-color:#1681df; background:{'#29374b' if dark else '#edf5ff'};}}
    QPushButton:disabled {{color:{muted};}}
    QPushButton#primary {{background:#0078d4; color:white; border:none;}}
    QPushButton#primary:hover {{background:#1686de;}}
    QPushButton#primary:disabled {{background:#91abc8; color:#edf3fa;}}
    QPushButton#nav {{text-align:left; padding:13px 20px; border:none; background:transparent;}}
    QPushButton#nav:checked {{background:{'#29374b' if dark else '#e8f2ff'}; color:#1681df; font-weight:600;}}
    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QDateTimeEdit {{background:{card}; border:1px solid {line}; border-radius:6px; padding:6px; selection-background-color:#0078d4;}}
    QTableWidget, QTreeWidget, QListWidget {{background:{card}; border:1px solid {line}; border-radius:8px; alternate-background-color:{bg};}}
    QHeaderView::section {{background:{bg}; border:none; padding:9px; color:{muted};}}
    QTableWidget::item, QTreeWidget::item, QListWidget::item {{padding:7px;}}
    QScrollArea {{border:none; background:transparent;}}
    QScrollArea > QWidget > QWidget {{background:transparent;}}
    QTabWidget::pane {{border:none;}}
    QStatusBar {{background:{card}; color:{muted};}}
    QToolTip {{background:{card}; color:{text}; border:1px solid {line};}}
    """


def icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#0078d4"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    pen = QPen(QColor("white"), 5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawArc(13, 13, 38, 38, 90 * 16, 280 * 16)
    painter.drawLine(32, 21, 32, 33)
    painter.drawLine(32, 33, 40, 37)
    painter.end()
    return QIcon(pixmap)


class Ring(QWidget):
    def __init__(self, mini=False):
        super().__init__()
        self.mini = mini
        self.seconds, self.ratio, self.free, self.dark = 1500, 0, False, False
        self.setMinimumSize(190 if mini else 310, 190 if mini else 310)

    def update_time(self, seconds, ratio, free=False, dark=False):
        self.seconds, self.ratio, self.free, self.dark = seconds, ratio, free, dark
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 28
        x, y = (self.width() - side) / 2, (self.height() - side) / 2
        painter.setPen(QPen(QColor("#394254" if self.dark else "#edf0f6"), 8))
        painter.drawEllipse(int(x), int(y), int(side), int(side))
        pen = QPen(QColor("#1681df"), 8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(int(x), int(y), int(side), int(side), 90 * 16, -int(min(1, self.ratio) * 360 * 16))
        painter.setPen(QColor("#edf0f7" if self.dark else "#252c39"))
        painter.setFont(QFont("Segoe UI", 27 if self.mini else 45))
        seconds = int(self.seconds if self.free else math.ceil(self.seconds))
        display = f"{seconds // 60:02d}:{seconds % 60:02d}" if seconds < 360000 else f"{seconds // 3600}h"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, display)
        painter.end()


class Form(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 420)
        self.layout_box = QVBoxLayout(self)
        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.layout_box.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout_box.addWidget(self.buttons)

    def text(self, label, value="", multiline=False):
        widget = QPlainTextEdit(value) if multiline else QLineEdit(value)
        if multiline:
            widget.setMaximumHeight(100)
        self.form.addRow(label, widget)
        return widget

    def number(self, label, value=0, maximum=99999, decimal=False):
        widget = QDoubleSpinBox() if decimal else QSpinBox()
        widget.setRange(0, maximum)
        if decimal:
            widget.setDecimals(1)
        widget.setValue(value)
        self.form.addRow(label, widget)
        return widget

    def choice(self, label, entries, value=None):
        widget = QComboBox()
        for text, data in entries:
            widget.addItem(text, data)
        if value is not None:
            widget.setCurrentIndex(max(0, widget.findData(value)))
        self.form.addRow(label, widget)
        return widget

    def date_time(self, label, value=None):
        widget = QDateTimeEdit((value or now() + timedelta(minutes=10)).replace(second=0, microsecond=0))
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.form.addRow(label, widget)
        return widget

    def check(self, label, value=False):
        widget = QCheckBox(label)
        widget.setChecked(value)
        self.form.addRow(widget)
        return widget

    def weekdays(self, selected=()):
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        checks = []
        for i, day in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            check = QCheckBox(day)
            check.setChecked(i in selected)
            layout.addWidget(check)
            checks.append(check)
        self.form.addRow("每周星期", box)
        return checks
