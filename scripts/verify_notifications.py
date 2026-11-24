"""阶段通知与内置音效回归，使用独立数据和零音量播放。"""

import argparse
from contextlib import ExitStack
import faulthandler
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

parser = argparse.ArgumentParser()
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
if not args.native:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.stdout.reconfigure(encoding="utf-8")
faulthandler.dump_traceback_later(30, exit=True)
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import delete
from tomatotime.audio import Audio, EVENTS
from tomatotime.controller import Controller
from tomatotime.core import Config
from tomatotime.storage import Store
from tomatotime.window import MainWindow
from test_business import FakeClock

app = QApplication([])
app.setQuitOnLastWindowClosed(False)

def wait(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()

with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
    store = Store(Path(directory))
    cleanup.callback(store.close)
    for event in EVENTS:
        store.set("sound:" + event, "silent")
    store.set("muted", True)
    store.set("volume", 0)
    ctrl = Controller(store, FakeClock())
    audio = Audio(store)
    cleanup.callback(delete, audio)
    cleanup.callback(audio.close)
    errors = []
    audio.error.connect(errors.append)
    window = MainWindow(ctrl, audio, no_tray=True, auto_tick=False)
    window.show()
    app.processEvents()
    # 启动与浏览界面不应改写原有音效偏好。
    assert all(store.get("sound:" + event) == "silent" for event in EVENTS)
    assert store.get("muted") is True and store.get("volume") == 0
    restore = window.findChild(QPushButton, "restoreBuiltinSounds")
    QTest.mouseClick(restore, Qt.MouseButton.LeftButton)
    assert all(store.get("sound:" + event) == "default" for event in EVENTS)
    assert all(combo.currentData() == "default" for combo in window.sound_combos.values())
    assert store.get("muted") is False and store.get("volume") == 60
    store.set("volume", 0)

    # 替代系统托盘，检查实际发送请求但不给用户发送测试通知。
    tray = Mock()
    window.tray = tray
    sounds = []
    ctrl.sound.connect(lambda event, _: sounds.append(event))
    ctrl.start(Config(focus=2, short=2, cycles=4, long_enabled=True, long_every=2, long=2))
    assert tray.showMessage.call_count == 1
    tray.showMessage.reset_mock()
    expected = ["break_start", "break_end", "long_start", "break_end", "break_start", "break_end", "complete"]
    titles = ["短休息开始", "专注开始", "长休息开始", "专注开始", "短休息开始", "专注开始", "本次计划结束"]
    for index, (event, title) in enumerate(zip(expected, titles)):
        print("Checking", event, flush=True)
        # 覆盖主窗口打开、隐藏及最小化三种状态。
        if index % 3 == 0:
            window.showNormal()
        elif index % 3 == 1:
            window.hide()
        else:
            window.showMinimized()
        app.processEvents()
        before = tray.showMessage.call_count
        ctrl.clock.advance(ctrl.timer.remaining)
        ctrl.tick()
        assert sounds[-1] == event, (event, sounds)
        assert tray.showMessage.call_count == before + 1, (event, tray.showMessage.call_args_list)
        assert tray.showMessage.call_args.args[0] == title
        wait(1200)
        assert Path(audio.player.source().toLocalFile()).resolve() == (store.audio_dir / f"builtin-{event}.wav").resolve()
        assert not errors, errors
        if args.native:
            assert audio.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia, (event, audio.player.mediaStatus())
        if ctrl.timer.active:
            ctrl.pause()
            ctrl.resume()
            ctrl.switch_task(None)
            window.phase_changed()
            assert tray.showMessage.call_count == before + 1, "非阶段切换操作不得重复提醒"
    assert sounds == expected

    store.set("notifications", False)
    ctrl.start(Config(focus=2, short=2, cycles=2))
    before = tray.showMessage.call_count
    ctrl.clock.advance(2)
    ctrl.tick()
    assert tray.showMessage.call_count == before, "关闭系统通知必须生效"
    assert sounds[-1] == "break_start", "通知开关不应关闭应用音效"
    wait(900)

    store.set("sound:break_end", "silent")
    previous_source = audio.player.source()
    ctrl.clock.advance(2)
    ctrl.tick()
    wait(300)
    assert audio.player.source() == previous_source, "单项静音必须生效"
    assert tray.showMessage.call_count == before
    audio.close()
    window.tray = None
    window.quitting = True
    window.tick_timer.stop()
    window.refresh_timer.stop()
    for dialog in window.dialogs:
        dialog.close()
    window.close()
    delete(window)

print("Notifications OK: visible/hidden/minimized, short/long/end, no duplicates, notification switch")
print("Audio OK: existing silence retained, one-click defaults, all transition WAVs, per-event silence")
print("Platform:", "Windows native with zero-volume decoding" if args.native else "Qt offscreen")
faulthandler.cancel_dump_traceback_later()
