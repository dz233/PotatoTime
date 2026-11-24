"""不依赖 Qt 的计时、日统计和重复提醒规则。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import time


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Clock:
    def now(self) -> datetime:
        return now()

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass
class Config:
    focus: float = 1500
    short: float = 300
    cycles: int = 4
    long_enabled: bool = False
    long_every: int = 4
    long: float = 900
    free: bool = False

    def validate(self) -> None:
        if min(self.focus, self.short, self.long) <= 0:
            raise ValueError("时长必须大于零")
        if not 1 <= self.cycles <= 99 or not 1 <= self.long_every <= 99:
            raise ValueError("番茄数量和长休息间隔需在 1～99 之间")

    def total(self) -> float:
        if self.free:
            return 0
        rests = sum(self.long if self.long_enabled and i % self.long_every == 0 else self.short
                    for i in range(1, self.cycles))
        return self.focus * self.cycles + rests


class Timer:
    """使用实际经过时间；一次检查最多切换一个阶段，不补跑虚构番茄。"""

    def __init__(self, clock: Clock | None = None):
        self.clock = clock or Clock()
        self.config = Config()
        self.active = False
        self.paused = False
        self.kind = "focus"
        self.round = 1
        self.completed = 0
        self.elapsed = 0.0
        self.duration = self.config.focus
        self.intervals: list[dict] = []
        self.fragment: dict | None = None
        self.last_mono = self.clock.monotonic()
        self.last_wall = self.clock.now()

    def start(self, config: Config) -> None:
        config.validate()
        self.config = config
        self.active, self.paused = True, False
        self.round, self.completed = 1, 0
        self.begin("free" if config.free else "focus", config.focus)

    def begin(self, kind: str, duration: float) -> None:
        self.kind, self.duration, self.elapsed = kind, duration, 0.0
        self.intervals = []
        self.fragment = None
        self.anchor()

    def anchor(self) -> None:
        self.last_mono, self.last_wall = self.clock.monotonic(), self.clock.now()

    @property
    def remaining(self) -> float:
        return max(0, self.duration - self.elapsed)

    def tick(self) -> str | None:
        if not self.active or self.paused:
            return None
        wall, mono = self.clock.now(), self.clock.monotonic()
        delta = max(0, mono - self.last_mono)
        wall_delta = (wall - self.last_wall).total_seconds()
        # 睡眠／长时间停止响应／系统时钟变更不计入学习时间。
        if delta > 30 or abs(wall_delta - delta) > 5:
            self.pause(update=False)
            return "interrupted"
        step = delta if self.kind == "free" else min(delta, self.remaining)
        if step:
            if self.fragment is None:
                self.fragment = {"start": stamp(self.last_wall), "end": stamp(self.last_wall), "seconds": 0.0}
            self.fragment["seconds"] += step
            self.fragment["end"] = stamp(parse(self.fragment["start"]) + timedelta(seconds=self.fragment["seconds"]))
            self.elapsed += step
        self.last_mono, self.last_wall = mono, wall
        return "boundary" if self.kind != "free" and self.remaining <= 0.000001 else None

    def fragments(self) -> list[dict]:
        return self.intervals + ([dict(self.fragment)] if self.fragment else [])

    def pause(self, update: bool = True) -> str | None:
        event = self.tick() if update and not self.paused else None
        if self.fragment:
            self.intervals.append(dict(self.fragment))
            self.fragment = None
        self.paused = True
        return event

    def resume(self) -> None:
        self.paused = False
        self.anchor()

    def transition(self) -> str:
        if self.kind in ("focus", "free"):
            self.completed += self.kind == "focus"
            if self.kind == "free" or self.round >= self.config.cycles:
                self.active = False
                return "complete"
            long_break = self.config.long_enabled and self.completed % self.config.long_every == 0
            self.begin("long" if long_break else "short", self.config.long if long_break else self.config.short)
            return "long_start" if long_break else "break_start"
        self.round += 1
        self.begin("focus", self.config.focus)
        return "break_end"

    def snapshot(self) -> dict:
        return {"config": asdict(self.config), "active": self.active, "paused": self.paused,
                "kind": self.kind, "round": self.round, "completed": self.completed,
                "elapsed": self.elapsed, "duration": self.duration, "intervals": self.fragments()}

    def restore(self, data: dict) -> None:
        self.config = Config(**data["config"])
        self.config.validate()
        for field in ("active", "kind", "round", "completed", "elapsed", "duration"):
            setattr(self, field, data[field])
        self.intervals, self.fragment = data["intervals"], None
        self.paused = True
        self.anchor()


def day_seconds(intervals: list[dict]) -> dict[str, float]:
    """按当地午夜拆分，暂停不在 intervals 内。"""
    result: dict[str, float] = {}
    for fragment in intervals:
        start, end = parse(fragment["start"]).astimezone(), parse(fragment["end"]).astimezone()
        length = (end - start).total_seconds()
        if length <= 0:
            continue
        seconds = float(fragment["seconds"])
        cursor = start
        while cursor < end:
            midnight = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time()).astimezone()
            edge = min(midnight, end)
            key = cursor.date().isoformat()
            result[key] = result.get(key, 0) + seconds * (edge - cursor).total_seconds() / length
            cursor = edge
    return result


def next_due(due: datetime, repeat: str, weekdays: list[int], after: datetime) -> datetime | None:
    if repeat == "once":
        return None
    if repeat not in ("daily", "weekdays", "weekly"):
        raise ValueError("未知的重复规则")
    allowed = list(range(7)) if repeat == "daily" else list(range(5)) if repeat == "weekdays" else weekdays
    if not allowed:
        raise ValueError("请选择每周提醒的星期")
    day = max(due.date(), after.astimezone().date())
    for _ in range(8):
        candidate = datetime.combine(day, due.timetz().replace(tzinfo=None)).astimezone()
        if candidate > after and day.weekday() in allowed:
            return candidate
        day += timedelta(days=1)
    raise ValueError("无法计算下一次提醒")


LABELS = {"focus": "专注", "free": "自由专注", "short": "短休息", "long": "长休息"}


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} 分钟"
