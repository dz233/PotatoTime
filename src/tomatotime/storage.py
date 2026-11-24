"""SQLite 存储。所有写入由应用主线程串行执行。"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from .core import day_seconds, now, parse, stamp, minutes, LABELS
from .allocation import OverlapError, assignments, effective_before, references, task_title


TABLES = ("tasks", "days", "sessions", "segments", "reminders", "occurrences", "settings")


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        existed = (self.directory / "tomatotime.sqlite3").exists()
        self.audio_dir = self.directory / "audio"
        self.audio_dir.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.directory / "tomatotime.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY, day TEXT, title TEXT, notes TEXT DEFAULT '', estimate REAL DEFAULT 0,
          status TEXT DEFAULT 'todo', position INTEGER DEFAULT 0, origin_id INTEGER, deleted INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, note TEXT DEFAULT '', reflection TEXT DEFAULT '', closed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
          id INTEGER PRIMARY KEY, task_id INTEGER, task_name TEXT, start TEXT, end TEXT,
          status TEXT, config TEXT, summary TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS segments (
          id INTEGER PRIMARY KEY, session_id INTEGER, task_id INTEGER, task_name TEXT, round INTEGER,
          kind TEXT, start TEXT, end TEXT, seconds REAL DEFAULT 0, intervals TEXT DEFAULT '[]',
          status TEXT, source TEXT DEFAULT 'timer', content TEXT DEFAULT '', reflection TEXT DEFAULT '',
          next_step TEXT DEFAULT '', original TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS reminders (
          id INTEGER PRIMARY KEY, title TEXT, due TEXT, note TEXT, task_id INTEGER,
          repeat TEXT, weekdays TEXT, enabled INTEGER DEFAULT 1, kind TEXT DEFAULT 'reminder',
          config TEXT DEFAULT '{}', sound TEXT DEFAULT 'default');
        CREATE TABLE IF NOT EXISTS occurrences (
          id INTEGER PRIMARY KEY, reminder_id INTEGER, planned TEXT, fired TEXT, status TEXT,
          snooze_due TEXT, missed INTEGER DEFAULT 0, title TEXT, note TEXT, task_id INTEGER,
          UNIQUE(reminder_id, planned));
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX IF NOT EXISTS segments_start ON segments(start);
        """)
        self.db.commit()
        additions = {"segments": {"tasks": "[]", "allocations": "[]", "allocation_mode": "auto", "revisions": "[]"},
                     "sessions": {"tasks": "[]", "activities": ""}}
        missing = [(table, field, default) for table, fields in additions.items()
                   for field, default in fields.items() if field not in {r["name"] for r in self.rows(f"PRAGMA table_info({table})")}]
        if missing:
            if existed and any(self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "sessions", "segments")):
                backup = sqlite3.connect(self.directory / f"before-v0.2-{now():%Y%m%d-%H%M%S-%f}.sqlite3")
                try:
                    self.db.backup(backup)
                finally:
                    backup.close()
            with self.db:
                for table, field, default in missing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT DEFAULT '{default}'")
                for table in ("segments", "sessions"):
                    for record in self.rows(f"SELECT * FROM {table} WHERE task_id IS NOT NULL"):
                        self.db.execute(f"UPDATE {table} SET tasks=? WHERE id=?", (json.dumps(references(record), ensure_ascii=False), record["id"]))
        self.db.execute("PRAGMA user_version=2")
        self.db.commit()

    def rows(self, sql: str, args=()) -> list[dict]:
        return [dict(row) for row in self.db.execute(sql, args)]

    def one(self, table: str, identity: int) -> dict | None:
        return next(iter(self.rows(f"SELECT * FROM {table} WHERE id=?", (identity,))), None)

    def add(self, table: str, **fields) -> int:
        keys = list(fields)
        with self.db:
            cursor = self.db.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", list(fields.values()))
        return cursor.lastrowid

    def update(self, table: str, identity: int, **fields) -> None:
        with self.db:
            self.db.execute(f"UPDATE {table} SET {','.join(key + '=?' for key in fields)} WHERE id=?", [*fields.values(), identity])

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self.db:
            self.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def day(self, day: str) -> dict:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO days(day) VALUES (?)", (day,))
        return dict(self.db.execute("SELECT * FROM days WHERE day=?", (day,)).fetchone())

    def save_day(self, day: str, **fields) -> None:
        self.day(day)
        with self.db:
            self.db.execute(f"UPDATE days SET {','.join(key + '=?' for key in fields)} WHERE day=?", [*fields.values(), day])

    def tasks(self, day: str) -> list[dict]:
        return self.rows("SELECT * FROM tasks WHERE day=? AND deleted=0 ORDER BY position,id", (day,))

    def add_task(self, day: str, title: str, notes="", estimate=0, status="todo") -> int:
        if not title.strip():
            raise ValueError("请输入任务名称")
        return self.add("tasks", day=day, title=title.strip(), notes=notes, estimate=estimate, status=status, position=len(self.tasks(day)))

    def carry(self, task_id: int) -> int:
        task = self.one("tasks", task_id)
        if not task or task["status"] in ("done", "carried"):
            raise ValueError("只可迁移未完成任务")
        target = (datetime.fromisoformat(task["day"]).date() + timedelta(days=1)).isoformat()
        with self.db:
            cursor = self.db.execute("INSERT INTO tasks(day,title,notes,estimate,status,position,origin_id) VALUES (?,?,?,?,?,?,?)",
                                     (target, task["title"], task["notes"], task["estimate"], "todo", len(self.tasks(target)), task["origin_id"] or task_id))
            self.db.execute("UPDATE tasks SET status='carried' WHERE id=?", (task_id,))
        return cursor.lastrowid

    def segments(self, day: str) -> list[dict]:
        records = self.rows("SELECT * FROM segments ORDER BY start,id")
        return [r for r in records if parse(r["start"]).astimezone().date().isoformat() == day
                or day in day_seconds(json.loads(r["intervals"]))]

    def stats(self, day: str) -> dict:
        records = self.segments(day)
        focus, rest, completed, partial = 0.0, 0.0, 0, 0
        by_task: dict[str, float] = {}
        task_seconds: dict[int, float] = {}
        unresolved: set[int] = set()
        for record in records:
            seconds = day_seconds(json.loads(record["intervals"])).get(day, 0)
            if record["kind"] in ("focus", "free"):
                focus += seconds
                allocated = 0.0
                for allocation in assignments(record):
                    amount = day_seconds(allocation["intervals"]).get(day, 0)
                    allocated += amount
                    title = allocation["task_name"] or "未关联任务"
                    by_task[title] = by_task.get(title, 0) + amount
                    if allocation["task_id"] is not None:
                        task_seconds[allocation["task_id"]] = task_seconds.get(allocation["task_id"], 0) + amount
                    elif amount > 0:
                        unresolved.update(task["id"] for task in references(record))
                if seconds - allocated > .001:
                    by_task["未细分任务"] = by_task.get("未细分任务", 0) + seconds - allocated
                    unresolved.update(task["id"] for task in references(record))
                end = parse(record["end"]).astimezone().date().isoformat() if record["end"] else None
                completed += record["kind"] == "focus" and record["status"] == "complete" and end == day
                partial += record["status"] in ("stopped", "interrupted") and end == day
            else:
                rest += seconds
        return {"focus": focus, "rest": rest, "completed": completed, "partial": partial,
                "by_task": by_task, "task_seconds": task_seconds, "unresolved": unresolved}

    def task_references(self, ids):
        result = []
        for identity in dict.fromkeys(ids):
            task = self.one("tasks", identity)
            if not task:
                raise ValueError("关联的任务不存在")
            result.append({"id": identity, "name": task["title"]})
        return result

    def revise_segment(self, identity, reason, **fields):
        previous = self.one("segments", identity)
        if not previous:
            raise ValueError("时间段不存在")
        history = json.loads(previous["revisions"])
        history.append({"time": stamp(now()), "reason": reason, "record": {k: v for k, v in previous.items() if k != "revisions"}})
        self.update("segments", identity, revisions=json.dumps(history, ensure_ascii=False),
                    original=previous["original"] or json.dumps(previous, ensure_ascii=False), **fields)

    def associate_segment(self, identity, ids):
        record = self.one("segments", identity)
        tasks = self.task_references(ids)
        fields = dict(tasks=json.dumps(tasks, ensure_ascii=False), task_id=ids[0] if len(ids) == 1 else None,
                      task_name="、".join(t["name"] for t in tasks))
        if record["allocation_mode"] != "auto":
            allocations = json.loads(record["allocations"])
            for allocation in allocations:
                if allocation["task_id"] not in ids:
                    allocation["task_id"], allocation["task_name"] = None, "未细分任务"
            fields["allocations"] = json.dumps(allocations, ensure_ascii=False)
        self.revise_segment(identity, "调整关联任务", **fields)

    def split_segment(self, identity, ranges, allow_estimate=False):
        record = self.one("segments", identity)
        if not record or not record["end"]:
            raise ValueError("请结束计时后再划分任务")
        intervals = json.loads(record["intervals"])
        if not intervals:
            raise ValueError("该记录没有确认的专注时间")
        if any(abs((parse(f["end"]) - parse(f["start"])).total_seconds() - f["seconds"]) > .1 for f in intervals) and not allow_estimate:
            raise ValueError("这条补记没有明确休息位置，请确认按比例估算后再划分")
        cursor = parse(record["start"])
        finish = parse(record["end"])
        allocations, ids = [], []
        for end, task_id in ranges:
            if end <= cursor or end > finish:
                raise ValueError("分界时间需依次递增，且位于原时段内")
            ref = self.task_references([task_id])[0] if task_id else {"id": None, "name": "未细分任务"}
            allocations.append({"task_id": ref["id"], "task_name": ref["name"],
                                "from": effective_before(intervals, cursor), "to": effective_before(intervals, end)})
            if task_id and task_id not in ids:
                ids.append(task_id)
            cursor = end
        if cursor != finish:
            raise ValueError("划分需覆盖原时段，最后一个结束时间不能变")
        tasks = self.task_references(ids)
        self.revise_segment(identity, "按时间划分任务", tasks=json.dumps(tasks, ensure_ascii=False),
                            allocations=json.dumps(allocations, ensure_ascii=False), allocation_mode="custom",
                            task_id=ids[0] if len(ids) == 1 else None, task_name="、".join(t["name"] for t in tasks))

    def undo_segment(self, identity):
        record = self.one("segments", identity)
        history = json.loads(record["revisions"])
        if not history:
            raise ValueError("没有可撤销的调整")
        previous = history.pop()["record"]
        previous.pop("id")
        self.update("segments", identity, **previous, revisions=json.dumps(history, ensure_ascii=False))

    def complete_task(self, identity, completed=True):
        task = self.one("tasks", identity)
        if not task:
            raise ValueError("任务不存在")
        state = "done" if completed else "todo"
        changed = task["status"] != state
        if changed:
            self.update("tasks", identity, status=state)
        return changed

    def manual(self, start: datetime, end: datetime, actual: float | None, task_id: int | None,
               content: str, reflection: str, segment_id: int | None = None, task_ids=None) -> int:
        previous = self.one("segments", segment_id) if segment_id else None
        if segment_id and not previous:
            raise ValueError("记录不存在")
        if end <= start or end > now() + timedelta(minutes=1):
            raise ValueError("结束时间需晚于开始时间，且不能在未来")
        if actual is not None and not 0 <= actual <= (end - start).total_seconds():
            raise ValueError("实际专注时间不能超过该时间段长度")
        if actual is not None and actual > 0 and (not previous or previous["kind"] in ("focus", "free")):
            for r in self.rows("SELECT * FROM segments WHERE kind IN ('focus','free')"):
                if r["id"] == segment_id:
                    continue
                for f in json.loads(r["intervals"]):
                    if float(f["seconds"]) > 0 and start < parse(f["end"]) and end > parse(f["start"]):
                        raise OverlapError(f"与记录 #{r['id']}（{task_title(r)}）重叠：\n"
                                         f"{parse(f['start']).astimezone():%Y-%m-%d %H:%M:%S} — {parse(f['end']).astimezone():%Y-%m-%d %H:%M:%S}\n"
                                         "可右键该记录调整任务或划分时间；新增记录需避开这段有效专注。", r["id"], parse(f["end"]))
        task = self.one("tasks", task_id) if task_id else None
        ids = list(dict.fromkeys(task_ids if task_ids is not None else [task_id] if task_id else []))
        tasks = self.task_references(ids)
        fields = dict(task_id=task_id, task_name=task["title"] if task else "", kind="free", start=stamp(start), end=stamp(end),
                      seconds=actual or 0, intervals=json.dumps([{"start": stamp(start), "end": stamp(end), "seconds": actual}] if actual is not None else []),
                      status="manual" if actual is not None else "unconfirmed", source="manual", content=content, reflection=reflection)
        fields["task_id"] = ids[0] if len(ids) == 1 else None
        fields["task_name"] = "、".join(t["name"] for t in tasks)
        fields["tasks"] = json.dumps(tasks, ensure_ascii=False)
        fields["allocations"], fields["allocation_mode"] = "[]", "auto"
        if segment_id:
            fields["kind"] = previous["kind"]
            if actual is not None and previous["source"] != "manual":
                fields["status"] = previous["status"]
                fields["source"] = "corrected"
            self.revise_segment(segment_id, "修正时间与实际投入", **fields)
            return segment_id
        return self.add("segments", round=0, **fields)

    def markdown(self, day: str) -> str:
        data, stats = self.day(day), self.stats(day)
        lines = [f"# {day}", "", "## 今日计划", ""]
        for task in self.tasks(day):
            lines += [f"- [{'x' if task['status'] == 'done' else ' '}] {task['title']}" + ("（已迁移）" if task["status"] == "carried" else "")]
            if task["notes"]:
                lines += [f"  备注：{task['notes']}"]
            if task["estimate"]:
                lines += [f"  预计：{minutes(task['estimate'])}；当日投入：{minutes(stats['task_seconds'].get(task['id'], 0))}"]
        lines += ["", "## 当天备注", "", data["note"], "", "## 时间记录", ""]
        for r in self.segments(day):
            start = parse(r["start"]).astimezone().strftime("%H:%M")
            end = parse(r["end"]).astimezone().strftime("%H:%M") if r["end"] else "进行中"
            lines += [f"### {start}–{end} · {LABELS[r['kind']]} · {task_title(r)}",
                      f"实际时长：{minutes(day_seconds(json.loads(r['intervals'])).get(day, 0))}" + ("（未确认，不计入统计）" if r["status"] == "unconfirmed" else ""), ""]
            for label, key in (("内容", "content"), ("感想／问题", "reflection"), ("下一步", "next_step")):
                if r[key]:
                    lines += [f"{label}：{r[key]}", ""]
            if len(references(r)) > 1 or r["allocation_mode"] != "auto":
                for allocation in assignments(r):
                    lines += [f"- {allocation['task_name']}：{minutes(day_seconds(allocation['intervals']).get(day, 0))}"]
                lines += [""]
        lines += ["## 专注计划总结", ""]
        for s in self.rows("SELECT * FROM sessions WHERE summary!='' OR activities!='' ORDER BY start"):
            if parse(s["start"]).astimezone().date().isoformat() == day:
                lines += ["做了什么：" + s["activities"], "总结与感想：" + s["summary"], ""]
        lines += ["## 当日总结", "", f"专注时长：{minutes(stats['focus'])}", f"休息时长：{minutes(stats['rest'])}",
                  f"完整番茄：{stats['completed']}；提前结束段：{stats['partial']}", ""]
        for title, seconds in stats["by_task"].items():
            lines += [f"- {title}：{minutes(seconds)}"]
        lines += ["", "未完成事项："]
        lines += [f"- {t['title']}" for t in self.tasks(day) if t["status"] not in ("done", "carried")]
        lines += ["", "## 当日回顾", "", data["reflection"], ""]
        return "\n".join(lines)

    def backup(self, path: Path) -> None:
        data = {"version": 2, "tables": {t: self.rows(f"SELECT * FROM {t}") for t in TABLES},
                "audio": {p.name: base64.b64encode(p.read_bytes()).decode() for p in self.audio_dir.iterdir() if p.is_file()}}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def restore(self, path: Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") not in (1, 2) or set(data.get("tables", {})) != set(TABLES):
            raise ValueError("不是支持的 TomatoTime 备份")
        for table in TABLES:
            columns = {r["name"] for r in self.rows(f"PRAGMA table_info({table})")}
            defaults = {"segments": {"tasks": "[]", "allocations": "[]", "allocation_mode": "auto", "revisions": "[]"}, "sessions": {"tasks": "[]", "activities": ""}}.get(table, {})
            if not isinstance(data["tables"][table], list):
                raise ValueError("备份结构损坏")
            for row in data["tables"][table]:
                if data["version"] == 1:
                    for field, default in defaults.items():
                        row.setdefault(field, default)
                if set(row) != columns:
                    raise ValueError("备份结构损坏")
                if table in ("segments", "sessions") and row["task_id"] and not json.loads(row["tasks"]):
                    row["tasks"] = json.dumps(references(row), ensure_ascii=False)
        audio = {}
        for name, encoded in data.get("audio", {}).items():
            if Path(name).name != name or Path(name).suffix.lower() not in (".wav", ".mp3", ".ogg"):
                raise ValueError("备份包含无效音效文件名")
            audio[name] = base64.b64decode(encoded, validate=True)
        self.backup(self.directory / f"before-restore-{now().strftime('%Y%m%d-%H%M%S-%f')}.json")
        with self.db:
            for table in TABLES:
                self.db.execute(f"DELETE FROM {table}")
                for row in data["tables"][table]:
                    keys = list(row)
                    self.db.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", list(row.values()))
        for name, content in audio.items():
            (self.audio_dir / name).write_bytes(content)

    def close(self) -> None:
        self.db.close()
