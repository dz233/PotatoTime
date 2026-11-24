"""七项反馈的界面回归，所有数据写入临时目录。"""

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
if not args.native:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))

from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QDateTimeEdit, QListWidget, QPlainTextEdit, QPushButton, QTableWidget, QMessageBox
from shiboken6 import delete
from tomatotime.app import load_fonts
from tomatotime.audio import Audio
from tomatotime.controller import Controller
from tomatotime.core import Config, now, stamp
from tomatotime.storage import Store
from tomatotime.window import MainWindow
from test_business import FakeClock


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
load_fonts()


def in_modal(open_dialog, action):
    errors = []
    def run():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog, "模态表单未出现"
            action(dialog)
        except BaseException as error:
            errors.append(error)
            if dialog:
                dialog.reject()
    QTimer.singleShot(40, run)
    open_dialog()
    if errors:
        raise errors[0]


with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory))
    clock = FakeClock(now() - timedelta(days=1))
    ctrl = Controller(store, clock)
    audio = Audio(store)
    store.set("muted", True)
    window = MainWindow(ctrl, audio, no_tray=not args.native, auto_tick=False)
    window.show()
    window.day_picker.setDate(QDate.fromString(clock.now().date().isoformat(), "yyyy-MM-dd"))
    day = clock.now().date().isoformat()
    a = store.add_task(day, "复习全部单词")
    b = store.add_task(day, "背新单词")
    for i in range(8):
        store.add_task(day, f"第 {i+3} 项学习计划", "备注可以独立填写")
    window.refresh()
    app.processEvents()
    assert window.tasks_table.rowCount() == 10
    assert window.tasks_table.verticalScrollBar().maximum() == 0, "默认待办区域需同时显示十项"
    assert window.tasks_table.viewport().height() >= sum(window.tasks_table.rowHeight(i) for i in range(10)), "第十行不得被表头或下方按钮遮挡"

    def choose(dialog):
        tasks = dialog.findChild(QListWidget)
        for i in range(tasks.count()):
            tasks.item(i).setCheckState(Qt.CheckState.Checked if tasks.item(i).data(Qt.ItemDataRole.UserRole) in (a, b) else Qt.CheckState.Unchecked)
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    in_modal(window.choose_tasks, choose)
    assert window.selected_tasks == [a, b]
    window.free_mode.setChecked(True)
    window.start_focus()
    parent = ctrl.segment_id
    assert ctrl.timer.active and ctrl.next_task is None
    clock.advance(3)
    ctrl.tick()
    window.task_combo.setCurrentIndex(window.task_combo.findData(a))
    clock.advance(4)
    ctrl.tick()
    window.task_combo.setCurrentIndex(window.task_combo.findData(b))
    clock.advance(5)
    ctrl.tick()
    ctrl.save()
    stats = store.stats(day)
    assert stats["focus"] == 12 and stats["task_seconds"] == {a: 4, b: 5}
    assert ctrl.segment_id == parent

    window.show_mini()
    window.hide()
    window.quick_record_popup()
    quick = window.dialogs[-1]
    assert quick.isVisible() and not window.isVisible(), "迷你记录不应强制切回主窗口"
    editors = quick.findChildren(QPlainTextEdit)
    editors[0].setPlainText("迷你窗口记录背新单词")
    window.flush_summaries()
    assert store.one("segments", parent)["content"] == "迷你窗口记录背新单词"
    assert ctrl.timer.active
    quick.accept()
    assert window.mini_stop.isEnabled()
    def confirm_end():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            QTest.mouseClick(dialog.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)
    QTimer.singleShot(40, confirm_end)
    QTest.mouseClick(window.mini_stop, Qt.MouseButton.LeftButton)
    assert not ctrl.timer.active, "迷你结束按钮需实际结束本次计时"
    summary = window.dialogs[-1]
    assert len(summary.findChildren(QPlainTextEdit)) == 2
    summary.findChild(QPlainTextEdit, "sessionActivities").setPlainText("复习＋背单词，涵盖整个周期")
    summary.findChild(QPlainTextEdit, "sessionSummary").setPlainText("明天继续复习易混词")
    mark = next(button for button in summary.findChildren(QPushButton) if button.text().startswith("标记完成") and "复习" in button.text())
    QTest.mouseClick(mark, Qt.MouseButton.LeftButton)
    assert not mark.isEnabled() and "已完成" in mark.text()
    assert "已标记完成" in summary.findChild(type(window.daily_summary), "completionFeedback").text()
    window.flush_summaries()
    assert store.one("sessions", ctrl.session_id)["activities"].startswith("复习＋")
    assert store.one("segments", parent)["content"] == "迷你窗口记录背新单词", "周期总结不能写进最后一段"
    summary.accept()
    window.show()

    start = clock.now().replace(hour=11, minute=6, second=0, microsecond=123456)
    finish = start + timedelta(minutes=69)
    interval_id = store.add("segments", task_id=a, task_name="复习全部单词", round=1, kind="focus", start=stamp(start), end=stamp(finish), seconds=3540,
                            intervals=json.dumps([{"start": stamp(start), "end": stamp(start + timedelta(minutes=29)), "seconds": 1740},
                                                  {"start": stamp(start + timedelta(minutes=39)), "end": stamp(finish), "seconds": 1800}]), status="complete")
    store.associate_segment(interval_id, [a, b])
    window.refresh()
    def split(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 2
        table.cellWidget(0, 1).setDateTime(start + timedelta(minutes=34))
        table.cellWidget(0, 2).setCurrentIndex(table.cellWidget(0, 2).findData(a))
        table.cellWidget(1, 2).setCurrentIndex(table.cellWidget(1, 2).findData(b))
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert not dialog.isVisible(), dialog.error_label.text()
    in_modal(lambda: window.split_form(interval_id), split)
    assert store.one("segments", interval_id)["seconds"] == 3540
    assert store.stats(day)["completed"] == 1
    assert "第 1 段" not in window.note_title.text()

    def manual(dialog):
        first = dialog.findChild(QDateTimeEdit, "manualStart")
        last = dialog.findChild(QDateTimeEdit, "manualEnd")
        text = dialog.findChild(QPlainTextEdit, "manualContent")
        first.setDateTime(finish - timedelta(minutes=20))
        last.setDateTime(finish + timedelta(minutes=10))
        text.setPlainText("校验失败仍应保留输入")
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert dialog.isVisible() and "重叠" in dialog.error_label.text()
        assert "复习" in dialog.error_label.text()
        assert text.toPlainText() == "校验失败仍应保留输入"
        align = next(button for button in dialog.findChildren(QPushButton) if "紧接冲突" in button.text())
        assert align.isEnabled()
        QTest.mouseClick(align, Qt.MouseButton.LeftButton)
        last.setDateTime(finish + timedelta(minutes=10))
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert not dialog.isVisible(), dialog.error_label.text()
    in_modal(window.manual_form, manual)
    added = store.rows("SELECT * FROM segments ORDER BY id DESC LIMIT 1")[0]
    assert added["content"] == "校验失败仍应保留输入"
    assert abs(added["seconds"] - 600) < .001

    window.bind_note(interval_id)
    window.refresh()
    assert "自由专注" not in window.note_title.text() and "复习" in window.note_title.text()
    output = root / "artifacts" / "v0.2"
    output.mkdir(parents=True, exist_ok=True)
    window.history_date.setDate(QDate.fromString(day, "yyyy-MM-dd"))
    for page, name in ((0, "today-ten-tasks"), (2, "history-allocation")):
        window.navigate(page)
        window.pages.currentWidget().verticalScrollBar().setValue(0)
        app.processEvents()
        assert window.grab().save(str(output / (name + ".png")))
    window.quitting = True
    window.tick_timer.stop()
    for dialog in window.dialogs:
        dialog.close()
    window.flush_note()
    window.flush_day()
    window.flush_summaries()
    if window.tray:
        window.tray.hide()
    window.mini.close()
    window.close()
    audio.close()
    delete(audio)
    store.close()
print("Feedback UI checks passed: multi-selection, instant switching, precise split, retained validation form, summary scope/feedback, mini record/end, 10 tasks")
print("Platform:", "native" if args.native else "offscreen")
