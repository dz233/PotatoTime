"""在独立临时数据库里验证界面流程，并生成四页截图。"""

import argparse
from dataclasses import asdict
from datetime import timedelta
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

from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QPlainTextEdit
from shiboken6 import delete
from tomatotime.app import load_fonts
from tomatotime.audio import Audio
from tomatotime.controller import Controller
from tomatotime.core import Config
from tomatotime.storage import Store
from tomatotime.window import MainWindow
from test_business import FakeClock


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
load_fonts()
output = root / "artifacts" / "app"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory))
    clock = FakeClock()
    ctrl = Controller(store, clock)
    audio = Audio(store)
    store.set("muted", True)
    window = MainWindow(ctrl, audio, no_tray=not args.native, auto_tick=False)
    window.show()
    app.processEvents()
    day = clock.now().date().isoformat()
    window.day_picker.setDate(QDate.fromString(day, "yyyy-MM-dd"))
    task = store.add_task(day, "复习单词，背新单词", "新单词 110 个，短语另算", 5400)
    store.add_task(day, "完成一篇阅读并批改", "关注错题原因", 7200)
    store.save_day(day, note="今天优先完成英语学习，晚上继续项目。")
    window.loaded_day = None
    window.refresh()
    window.task_combo.setCurrentIndex(window.task_combo.findData(task))
    window.focus_minutes.setValue(.1)
    window.short_minutes.setValue(.1)
    window.cycles.setValue(2)
    window.navigate(1)
    QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert ctrl.timer.active
    clock.advance(2)
    ctrl.tick()
    QTest.mouseClick(window.pause_button, Qt.MouseButton.LeftButton)
    assert ctrl.timer.paused
    clock.advance(100)
    QTest.mouseClick(window.pause_button, Qt.MouseButton.LeftButton)
    clock.advance(4)
    ctrl.tick()
    assert ctrl.timer.kind == "short"
    window.note_content.setPlainText("复习了昨天的单词，背了 30 个新词。")
    window.note_reflection.setPlainText("容易混淆的词需要单独复习。")
    original = window.note_id
    window.flush_note()
    ctrl.skip_rest()
    assert ctrl.timer.kind == "focus"
    assert store.one("segments", original)["content"].startswith("复习")
    reminder = ctrl.add_reminder("做一套听力", clock.now() + timedelta(seconds=1), "准备耳机", task)
    ctrl.startup = False
    window.hide()
    clock.advance(1)
    ctrl.tick()
    app.processEvents()
    assert window.notice_dialog and window.notice_dialog.isVisible(), "主窗口隐藏时提醒必须弹出"
    window.finish_notices("snoozed")
    assert store.rows("SELECT * FROM occurrences")[0]["status"] == "snoozed"
    window.show()
    window.show_mini()
    app.processEvents()
    assert window.mini.isVisible()
    assert window.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_pinned(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_pinned(False)
    if args.native:
        assert QSystemTrayIcon.isSystemTrayAvailable(), "本机托盘不可用"
        assert window.tray and window.tray.isVisible()
        window.close()
        app.processEvents()
        assert not window.isVisible() and ctrl.timer.active
        window.reveal()
        # 静音音量测试真实音频解码与播放管线，不向用户播放声音。
        source = Path(directory) / "custom.wav"
        source.write_bytes((store.audio_dir / "builtin-reminder.wav").read_bytes())
        imported = audio.import_file(str(source))
        source.unlink()
        assert (store.audio_dir / imported).exists()
        store.set("muted", False)
        store.set("volume", 0)
        audio.play("reminder", imported)
        QTest.qWait(1000)
        assert not audio.player.errorString(), audio.player.errorString()
        store.set("muted", True)
    window.refresh()
    for index, name in enumerate(("today", "focus", "history", "settings")):
        window.navigate(index)
        window.pages.currentWidget().verticalScrollBar().setValue(0)
        app.processEvents()
        assert window.grab().save(str(output / f"{name}.png"))
    store.set("dark", True)
    window.apply_theme()
    window.navigate(1)
    window.pages.currentWidget().verticalScrollBar().setValue(0)
    app.processEvents()
    assert window.grab().save(str(output / "focus-dark.png"))
    ctrl.stop()
    app.processEvents()
    assert not ctrl.timer.active
    summary_editor = window.dialogs[-1].findChild(QPlainTextEdit, "sessionSummary")
    summary_editor.setPlainText("本次阅读与单词学习已记录，明天复习易错词。")
    window.flush_summaries()
    assert "明天复习" in store.one("sessions", ctrl.session_id)["summary"], "退出或备份时需提交尚未到自动保存时间的周期总结"
    for dialog in window.dialogs:
        dialog.close()
    window.flush_note()
    window.flush_day()
    window.tick_timer.stop()
    window.quitting = True
    if window.tray:
        window.tray.hide()
    window.mini.close()
    window.close()
    audio.close()
    delete(audio)
    store.close()
print("UI workflow OK: todo, focus/pause, rest, note binding, hidden-window reminder, snooze, mini, pin, summary")
print("Platform:", "Windows native" if args.native else "Qt offscreen")
print("Screenshots:", output)
