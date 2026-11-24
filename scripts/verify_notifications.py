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
from tomatotime.synthesis import PRESETS, synthesize
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
    (store.audio_dir / "builtin-break_start.wav").write_bytes(b"old generated sound")
    custom = store.audio_dir / "custom.wav"
    custom.write_bytes(b"imported audio must stay unchanged")
    ctrl = Controller(store, FakeClock())
    audio = Audio(store)
    cleanup.callback(delete, audio)
    cleanup.callback(audio.close)
    errors = []
    audio.error.connect(errors.append)
    assert (store.audio_dir / "builtin-break_start.wav").read_bytes() == synthesize("chime")
    assert custom.read_bytes() == b"imported audio must stay unchanged"
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

    # 实际显示应用弹窗；系统托盘不得再发送会发声的通知。
    tray = Mock()
    window.tray = tray
    notifications = Mock(wraps=window.show_notification)
    window.show_notification = notifications
    sounds = []
    ctrl.sound.connect(lambda event, _: sounds.append(event))
    ctrl.start(Config(focus=2, short=2, cycles=4, long_enabled=True, long_every=2, long=2))
    assert notifications.call_count == 1
    assert window.notification_popup.isVisible()
    notifications.reset_mock()
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
        before = notifications.call_count
        ctrl.clock.advance(ctrl.timer.remaining)
        ctrl.tick()
        assert sounds[-1] == event, (event, sounds)
        assert notifications.call_count == before + 1, (event, notifications.call_args_list)
        assert notifications.call_args.args[0] == title
        assert window.notification_popup.isVisible() and not window.notification_popup.isMinimized()
        assert window.notification_popup.title.text() == title
        assert not window.notification_popup.isModal()
        wait(1700)
        assert Path(audio.player.source().toLocalFile()).resolve() == (store.audio_dir / f"builtin-{event}.wav").resolve()
        assert not errors, errors
        if args.native:
            assert audio.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia, (event, audio.player.mediaStatus())
        if ctrl.timer.active:
            ctrl.pause()
            ctrl.resume()
            ctrl.switch_task(None)
            window.phase_changed()
            assert notifications.call_count == before + 1, "非阶段切换操作不得重复提醒"
    assert sounds == expected

    store.set("notifications", False)
    ctrl.start(Config(focus=2, short=2, cycles=2))
    before = notifications.call_count
    ctrl.clock.advance(2)
    ctrl.tick()
    assert notifications.call_count == before, "关闭桌面小弹窗必须生效"
    assert sounds[-1] == "break_start", "通知开关不应关闭应用音效"
    wait(1700)

    store.set("sound:break_end", "silent")
    previous_source = audio.player.source()
    ctrl.clock.advance(2)
    ctrl.tick()
    wait(300)
    assert audio.player.source() == previous_source, "单项静音必须生效"
    assert notifications.call_count == before
    tray.showMessage.assert_not_called()
    # 可选内置预设可直接试听，自动开始和事项提醒也使用合成音效。
    for event, preset in (("auto_start", None), ("reminder", None), ("break_start", "wood")):
        if preset:
            combo = window.sound_combos[event]
            combo.setCurrentIndex(combo.findData("preset:" + preset))
            assert store.get("sound:" + event) == "preset:" + preset
        audio.play(event)
        wait(1700)
        assert not errors, errors
        if args.native:
            assert audio.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
    popup = window.notification_popup
    popup.present("合成音效", "不播放系统声音；测试已通过。")
    wait(100)
    if args.native:
        screenshot = root / "artifacts" / "audio-popup.png"
        assert popup.grab().save(str(screenshot))
    QTest.mouseClick(popup.open_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not popup.isVisible() and window.isVisible(), "打开按钮应回到应用并关闭小弹窗"
    audio.close()
    window.tray = None
    window.quitting = True
    window.tick_timer.stop()
    window.refresh_timer.stop()
    for dialog in window.dialogs:
        dialog.close()
    window.close()
    delete(window)

print("Notifications OK: visible/hidden/minimized, nonmodal popup, no system sounds, no duplicates, open action")
print("Audio OK: old cache refreshed, custom audio preserved, synth defaults/presets, mute and zero-volume decoding")
print("Platform:", "Windows native with zero-volume decoding" if args.native else "Qt offscreen")
faulthandler.cancel_dump_traceback_later()
