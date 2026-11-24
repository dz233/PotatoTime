"""将纯 Python 规则与数据库连接起来，通过 Qt 信号通知界面。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import json

from PySide6.QtCore import QObject, Signal

from .core import Clock, Config, Timer, next_due, parse, stamp
from .storage import Store


class Controller(QObject):
    changed = Signal()
    phase_changed = Signal()
    note_ready = Signal(int)
    session_finished = Signal(int)
    notices = Signal(list)
    message = Signal(str)
    sound = Signal(str, str)

    def __init__(self, store: Store, clock: Clock | None = None):
        super().__init__()
        self.store, self.clock = store, clock or Clock()
        self.timer = Timer(self.clock)
        self.session_id = self.segment_id = self.next_task = self.last_note = None
        self.last_saved = self.clock.monotonic()
        self.presented: set[int] = set()
        self.startup = True
        self.recovery = False
        state = store.get("active_state")
        if state and state["timer"]["active"]:
            if store.one("sessions", state["session"]) and store.one("segments", state["segment"]):
                self.timer.restore(state["timer"])
                self.session_id, self.segment_id = state["session"], state["segment"]
                self.next_task, self.last_note = state.get("next_task"), state.get("last_note")
                self.recovery = True

    def start(self, config: Config, task_id: int | None = None, automatic=False):
        if self.timer.active:
            raise ValueError("已有计划正在进行，请先结束")
        config.validate()
        task = self.store.one("tasks", task_id) if task_id else None
        self.next_task = task_id if task and not task["deleted"] else None
        self.timer.start(config)
        self.session_id = self.store.add("sessions", task_id=self.next_task, task_name=task["title"] if task else "",
                                         start=stamp(self.clock.now()), status="running", config=json.dumps(asdict(config)))
        self.store.set("timer_defaults", asdict(config))
        self.new_segment()
        self.save()
        self.changed.emit()
        self.phase_changed.emit()
        if automatic:
            self.sound.emit("auto_start", "default")

    def new_segment(self):
        task = self.store.one("tasks", self.next_task) if self.next_task else None
        self.segment_id = self.store.add("segments", session_id=self.session_id, task_id=self.next_task,
                                         task_name=task["title"] if task else "", round=self.timer.round,
                                         kind=self.timer.kind, start=stamp(self.clock.now()), status="running")
        if self.timer.kind in ("focus", "free"):
            self.store.save_day(self.clock.now().date().isoformat(), closed=0)
            if task and task["status"] == "todo":
                self.store.update("tasks", task["id"], status="doing")

    def save(self):
        if self.segment_id and self.timer.active:
            self.store.update("segments", self.segment_id, seconds=self.timer.elapsed,
                              intervals=json.dumps(self.timer.fragments()), status="paused" if self.timer.paused else "running")
        self.store.set("active_state", {"timer": self.timer.snapshot(), "session": self.session_id,
                                        "segment": self.segment_id, "next_task": self.next_task, "last_note": self.last_note})
        self.last_saved = self.clock.monotonic()

    def finish_segment(self, status="complete"):
        fragments = self.timer.fragments()
        end = fragments[-1]["end"] if fragments else stamp(self.clock.now())
        self.store.update("segments", self.segment_id, seconds=self.timer.elapsed, intervals=json.dumps(fragments), end=end, status=status)
        if self.timer.kind in ("focus", "free"):
            self.last_note = self.segment_id
            self.note_ready.emit(self.segment_id)

    def advance(self, skipped=False):
        self.finish_segment("skipped" if skipped else "complete")
        event = self.timer.transition()
        if event == "complete":
            self.store.update("sessions", self.session_id, end=stamp(self.clock.now()), status="complete")
            self.save()
            self.sound.emit("complete", "default")
            self.session_finished.emit(self.session_id)
        else:
            self.new_segment()
            self.save()
            self.sound.emit(event, "default")
        self.phase_changed.emit()
        self.changed.emit()

    def tick(self):
        event = self.timer.tick()
        if event == "boundary":
            self.advance()
        elif event == "interrupted":
            self.recovery = True
            self.save()
            self.phase_changed.emit()
            self.message.emit("检测到睡眠、时钟变化或较长中断，已暂停；中断时间未计入专注。确认后可继续。")
        if self.timer.active and self.clock.monotonic() - self.last_saved >= 5:
            self.save()
        self.poll_reminders()

    def pause(self):
        self.tick()
        if self.timer.active:
            self.timer.pause(update=False)
            self.save()
            self.phase_changed.emit()

    def resume(self):
        self.recovery = False
        self.timer.resume()
        self.save()
        self.phase_changed.emit()

    def stop(self):
        self.tick()
        if not self.timer.active:
            return
        self.finish_segment("complete" if self.timer.kind == "free" else "stopped")
        self.timer.active = False
        self.store.update("sessions", self.session_id, end=stamp(self.clock.now()), status="stopped")
        self.save()
        self.changed.emit()
        self.phase_changed.emit()
        self.session_finished.emit(self.session_id)

    def skip_rest(self):
        self.tick()
        if self.timer.active and self.timer.kind in ("short", "long"):
            self.advance(skipped=True)

    def extend_rest(self, seconds=300):
        self.tick()
        if self.timer.active and self.timer.kind in ("short", "long"):
            self.timer.duration += seconds
            self.save()
            self.phase_changed.emit()

    def suspend(self):
        if self.timer.active and not self.timer.paused:
            self.pause()
            self.recovery = True
            self.message.emit("电脑进入睡眠，专注已暂停；唤醒后可继续。")

    def save_note(self, segment_id: int, content: str, reflection: str, next_step: str):
        record = self.store.one("segments", segment_id)
        if not record:
            return
        self.store.update("segments", segment_id, content=content, reflection=reflection, next_step=next_step)
        self.store.save_day(parse(record["start"]).astimezone().date().isoformat(), closed=0)
        self.changed.emit()

    def add_reminder(self, title, due, note="", task_id=None, repeat="once", weekdays=None,
                     kind="reminder", config=None, sound="default", reminder_id=None):
        if not title.strip() or due <= self.clock.now():
            raise ValueError("请输入标题，并选择未来的提醒时间")
        weekdays = weekdays or []
        if repeat == "weekly" and not weekdays:
            raise ValueError("请选择至少一个星期")
        fields = dict(title=title.strip(), due=stamp(due), note=note, task_id=task_id, repeat=repeat,
                      weekdays=json.dumps(weekdays), enabled=1, kind=kind, config=json.dumps(config or {}), sound=sound)
        if reminder_id:
            self.store.update("reminders", reminder_id, **fields)
            identity = reminder_id
        else:
            identity = self.store.add("reminders", **fields)
        self.changed.emit()
        return identity

    def poll_reminders(self):
        current = self.clock.now()
        for reminder in self.store.rows("SELECT * FROM reminders WHERE enabled=1"):
            due = parse(reminder["due"])
            if due > current:
                continue
            missed = self.startup or (current - due).total_seconds() > 60
            existing = self.store.rows("SELECT id FROM occurrences WHERE reminder_id=? AND planned=?", (reminder["id"], reminder["due"]))
            if not existing:
                identity = self.store.add("occurrences", reminder_id=reminder["id"], planned=reminder["due"], fired=stamp(current),
                                          status="pending", missed=int(missed), title=reminder["title"], note=reminder["note"], task_id=reminder["task_id"])
                if reminder["kind"] == "schedule":
                    state = "skipped"
                    if not missed and not self.timer.active:
                        try:
                            self.start(Config(**json.loads(reminder["config"])), reminder["task_id"], automatic=True)
                            state = "started"
                        except ValueError as error:
                            self.message.emit(str(error))
                    else:
                        self.message.emit("预约已跳过：错过启动时间或已有计划正在运行。")
                    self.store.update("occurrences", identity, status=state)
                elif not missed:
                    self.sound.emit("reminder", reminder["sound"])
            following = next_due(due, reminder["repeat"], json.loads(reminder["weekdays"]), current)
            self.store.update("reminders", reminder["id"], due=stamp(following) if following else reminder["due"], enabled=int(following is not None))
            self.changed.emit()
        pending = []
        for occurrence in self.store.rows("SELECT * FROM occurrences WHERE status IN ('pending','snoozed') ORDER BY planned"):
            if occurrence["id"] in self.presented:
                continue
            if occurrence["status"] == "snoozed" and parse(occurrence["snooze_due"]) > current:
                continue
            if self.startup:
                self.store.update("occurrences", occurrence["id"], missed=1)
                occurrence["missed"] = 1
            elif occurrence["status"] == "snoozed":
                reminder = self.store.one("reminders", occurrence["reminder_id"])
                self.store.update("occurrences", occurrence["id"], status="pending", fired=stamp(current))
                if reminder:
                    self.sound.emit("reminder", reminder["sound"])
            self.presented.add(occurrence["id"])
            pending.append(occurrence)
        if pending:
            self.notices.emit(pending)
        self.startup = False

    def acknowledge(self, ids: list[int], status="seen", snooze_minutes=10):
        for identity in ids:
            self.store.update("occurrences", identity, status=status,
                              snooze_due=stamp(self.clock.now() + timedelta(minutes=snooze_minutes)) if status == "snoozed" else None)
            self.presented.discard(identity)
        self.changed.emit()
