"""SQLite 存储。所有写入由应用主线程串行执行。"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from .core import day_seconds, now, parse, stamp, minutes, LABELS


TABLES = ("tasks", "days", "sessions", "segments", "reminders", "occurrences", "settings")


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
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
        PRAGMA user_version=1;
        """)
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
        for record in records:
            seconds = day_seconds(json.loads(record["intervals"])).get(day, 0)
            if record["kind"] in ("focus", "free"):
                focus += seconds
                by_task[record["task_name"] or "未关联任务"] = by_task.get(record["task_name"] or "未关联任务", 0) + seconds
                task_seconds[record["task_id"]] = task_seconds.get(record["task_id"], 0) + seconds
                end = parse(record["end"]).astimezone().date().isoformat() if record["end"] else None
                completed += record["kind"] == "focus" and record["status"] == "complete" and end == day
                partial += record["status"] in ("stopped", "interrupted") and end == day
            else:
                rest += seconds
        return {"focus": focus, "rest": rest, "completed": completed, "partial": partial,
                "by_task": by_task, "task_seconds": task_seconds}

    def manual(self, start: datetime, end: datetime, actual: float | None, task_id: int | None,
               content: str, reflection: str, segment_id: int | None = None) -> int:
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
                        raise ValueError("与已有专注记录重叠，请修正时间或只补充原记录文字")
        task = self.one("tasks", task_id) if task_id else None
        fields = dict(task_id=task_id, task_name=task["title"] if task else "", kind="free", start=stamp(start), end=stamp(end),
                      seconds=actual or 0, intervals=json.dumps([{"start": stamp(start), "end": stamp(end), "seconds": actual}] if actual is not None else []),
                      status="manual" if actual is not None else "unconfirmed", source="manual", content=content, reflection=reflection)
        if segment_id:
            fields["kind"] = previous["kind"]
            if actual is not None and previous["source"] != "manual":
                fields["status"] = previous["status"]
                fields["source"] = "corrected"
            fields["original"] = previous["original"] or json.dumps(previous, ensure_ascii=False)
            self.update("segments", segment_id, **fields)
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
            lines += [f"### {start}–{end} · {LABELS[r['kind']]} · {r['task_name'] or '未关联任务'}",
                      f"实际时长：{minutes(day_seconds(json.loads(r['intervals'])).get(day, 0))}" + ("（未确认，不计入统计）" if r["status"] == "unconfirmed" else ""), ""]
            for label, key in (("内容", "content"), ("感想／问题", "reflection"), ("下一步", "next_step")):
                if r[key]:
                    lines += [f"{label}：{r[key]}", ""]
        lines += ["## 专注计划总结", ""]
        for s in self.rows("SELECT * FROM sessions WHERE summary!='' ORDER BY start"):
            if parse(s["start"]).astimezone().date().isoformat() == day:
                lines += [s["summary"], ""]
        lines += ["## 当日总结", "", f"专注时长：{minutes(stats['focus'])}", f"休息时长：{minutes(stats['rest'])}",
                  f"完整番茄：{stats['completed']}；提前结束段：{stats['partial']}", ""]
        for title, seconds in stats["by_task"].items():
            lines += [f"- {title}：{minutes(seconds)}"]
        lines += ["", "未完成事项："]
        lines += [f"- {t['title']}" for t in self.tasks(day) if t["status"] not in ("done", "carried")]
        lines += ["", "## 当日回顾", "", data["reflection"], ""]
        return "\n".join(lines)

    def backup(self, path: Path) -> None:
        data = {"version": 1, "tables": {t: self.rows(f"SELECT * FROM {t}") for t in TABLES},
                "audio": {p.name: base64.b64encode(p.read_bytes()).decode() for p in self.audio_dir.iterdir() if p.is_file()}}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def restore(self, path: Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != 1 or set(data.get("tables", {})) != set(TABLES):
            raise ValueError("不是支持的 TomatoTime 备份")
        for table in TABLES:
            columns = {r["name"] for r in self.rows(f"PRAGMA table_info({table})")}
            if not isinstance(data["tables"][table], list) or any(set(row) != columns for row in data["tables"][table]):
                raise ValueError("备份结构损坏")
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
