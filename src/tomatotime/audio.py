"""内置柔和音效、自定义音频和同时事件的声音合并。"""

import hashlib
from pathlib import Path
import shutil

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .synthesis import DEFAULT_PRESETS, PRESETS, synthesize


EVENTS = {"auto_start": "自动开始", "break_start": "短休息开始", "long_start": "长休息开始",
          "break_end": "休息结束", "complete": "全部完成", "reminder": "事项提醒"}
PRIORITY = {"auto_start": 1, "break_end": 2, "break_start": 3, "long_start": 3, "complete": 4, "reminder": 5}


class Audio(QObject):
    error = Signal(str)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(lambda *_: self.error.emit("音效播放失败：" + self.player.errorString()))
        self.pending = []
        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.timeout.connect(self.flush)
        bank = {f"builtin-{event}.wav": synthesize(preset) for event, preset in DEFAULT_PRESETS.items()}
        bank.update({f"builtin-preset-{preset}.wav": synthesize(preset) for preset in PRESETS})
        for name, content in bank.items():
            path = self.store.audio_dir / name
            # 内置文件是派生资源；更新旧缓存，导入的自定义音频不受影响。
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)

    def import_file(self, source: str) -> str:
        path = Path(source)
        if path.suffix.lower() not in (".mp3", ".wav", ".ogg"):
            raise ValueError("请选择 MP3、WAV 或 OGG 文件")
        name = hashlib.sha256(path.read_bytes()).hexdigest()[:20] + path.suffix.lower()
        target = self.store.audio_dir / name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        return name

    def queue(self, event, override="default"):
        self.pending.append((event, override))
        if not self.flush_timer.isActive():
            self.flush_timer.start(120)

    def flush(self):
        if not self.pending:
            return
        event, override = max(self.pending, key=lambda entry: PRIORITY.get(entry[0], 0))
        self.pending.clear()
        self.play(event, override)

    def play(self, event, override="default"):
        if self.store.get("muted", False):
            return
        name = self.store.get("sound:" + event, "default") if override == "default" else override
        if name == "silent":
            return
        if name == "default":
            name = f"builtin-{event}.wav"
        elif name.startswith("preset:"):
            name = f"builtin-preset-{name.removeprefix('preset:')}.wav"
        path = self.store.audio_dir / Path(name).name
        if not path.exists():
            self.error.emit("自定义音效不存在，请重新选择")
            return
        self.output.setVolume(self.store.get("volume", 60) / 100)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def close(self):
        self.flush_timer.stop()
        self.pending.clear()
        self.player.stop()
        self.player.setSource(QUrl())
