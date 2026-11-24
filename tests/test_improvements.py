"""v0.2：多任务归属、迁移、划分和重复完成的回归检查。"""

from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tomatotime.allocation import assignments, references
from tomatotime.controller import Controller
from tomatotime.core import Config, now, stamp
from tomatotime.storage import Store
from test_business import FakeClock


class MultiTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.clock = FakeClock(now() - timedelta(days=1))
        self.day = self.clock.now().date().isoformat()
        self.a = self.store.add_task(self.day, "复习单词")
        self.b = self.store.add_task(self.day, "背新单词")
        self.ctrl = Controller(self.store, self.clock)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_multi_selection_not_duplicated_or_automatically_split(self):
        self.ctrl.start(Config(free=True), task_ids=[self.a, self.b])
        self.clock.advance(10)
        self.ctrl.tick()
        self.ctrl.save()
        stats = self.store.stats(self.day)
        self.assertEqual(stats["focus"], 10)
        self.assertEqual(stats["by_task"]["未细分任务"], 10)
        self.assertEqual(stats["unresolved"], {self.a, self.b})
        self.assertEqual(sum(stats["task_seconds"].values()), 0)

    def test_switch_in_free_focus_is_immediate_and_keeps_parent(self):
        self.ctrl.start(Config(free=True), self.a, task_ids=[self.a, self.b])
        parent = self.ctrl.segment_id
        self.clock.advance(7)
        self.ctrl.switch_task(self.b)
        self.clock.advance(8)
        self.ctrl.stop()
        stats = self.store.stats(self.day)
        self.assertEqual(self.ctrl.segment_id, parent)
        self.assertEqual(stats["focus"], 15)
        self.assertEqual(stats["task_seconds"], {self.a: 7, self.b: 8})
        self.assertEqual(len(self.store.rows("SELECT * FROM segments")), 1)

    def test_pause_and_restart_keep_allocations(self):
        self.ctrl.start(Config(free=True), self.a, task_ids=[self.a, self.b])
        self.clock.advance(5)
        self.ctrl.pause()
        self.clock.advance(120)
        self.ctrl.switch_task(self.b)
        recovered = Controller(self.store, self.clock)
        recovered.resume()
        self.clock.advance(7)
        recovered.stop()
        self.assertEqual(self.store.stats(self.day)["task_seconds"], {self.a: 5, self.b: 7})

    def original_record(self, start=None):
        start = start or self.clock.now().replace(hour=11, minute=6, second=0, microsecond=123000)
        finish = start + timedelta(minutes=69)
        intervals = [{"start": stamp(start), "end": stamp(start + timedelta(minutes=29)), "seconds": 1740},
                     {"start": stamp(start + timedelta(minutes=39)), "end": stamp(finish), "seconds": 1800}]
        identity = self.store.add("segments", task_id=self.a, task_name="复习单词", round=1, kind="focus", start=stamp(start), end=stamp(finish),
                                  seconds=3540, intervals=json.dumps(intervals), status="complete")
        return identity, start, finish

    def test_69_minute_span_59_effective_split_at_pause(self):
        identity, start, finish = self.original_record()
        before = self.store.stats(self.day)
        self.store.associate_segment(identity, [self.a, self.b])
        self.store.split_segment(identity, [(start + timedelta(minutes=34), self.a), (finish, self.b)])
        after = self.store.stats(self.day)
        self.assertEqual(after["focus"], before["focus"])
        self.assertEqual(after["completed"], before["completed"])
        self.assertEqual(after["task_seconds"], {self.a: 1740, self.b: 1800})
        self.assertEqual(len(self.store.rows("SELECT * FROM segments")), 1)
        self.assertIn("背新单词", self.store.markdown(self.day))

    def test_revisions_undo_preserve_original_time_and_notes(self):
        identity, start, finish = self.original_record()
        self.store.update("segments", identity, content="原笔记")
        self.store.split_segment(identity, [(start + timedelta(minutes=34), self.a), (finish, self.b)])
        self.store.undo_segment(identity)
        record = self.store.one("segments", identity)
        self.assertEqual(record["content"], "原笔记")
        self.assertEqual(record["allocation_mode"], "auto")
        self.assertEqual(self.store.stats(self.day)["task_seconds"], {self.a: 3540})

    def test_association_removal_moves_consumed_time_to_unassigned(self):
        identity, start, finish = self.original_record()
        self.store.split_segment(identity, [(start + timedelta(minutes=34), self.a), (finish, self.b)])
        self.store.associate_segment(identity, [self.b])
        stats = self.store.stats(self.day)
        self.assertEqual(stats["focus"], 3540)
        self.assertEqual(stats["by_task"]["未细分任务"], 1740)
        self.assertEqual(stats["task_seconds"][self.b], 1800)

    def test_split_requires_confirmed_estimate_for_mixed_manual(self):
        end = self.clock.now()
        identity = self.store.manual(end - timedelta(hours=1), end, 1800, self.a, "学习＋休息", "")
        ranges = [(end - timedelta(minutes=30), self.a), (end, self.b)]
        with self.assertRaisesRegex(ValueError, "比例估算"):
            self.store.split_segment(identity, ranges)
        self.store.split_segment(identity, ranges, True)
        self.assertEqual(self.store.stats(self.day)["task_seconds"], {self.a: 900, self.b: 900})

    def test_split_invalid_boundaries_leave_database_unchanged(self):
        identity, start, finish = self.original_record()
        original = self.store.one("segments", identity)
        with self.assertRaises(ValueError):
            self.store.split_segment(identity, [(finish + timedelta(seconds=1), self.a)])
        self.assertEqual(self.store.one("segments", identity), original)

    def test_cross_midnight_allocation_count_once(self):
        start = self.clock.now().replace(hour=23, minute=50, second=0, microsecond=0)
        end = start + timedelta(minutes=20)
        identity = self.store.add("segments", task_id=self.a, task_name="复习", round=1, kind="focus", start=stamp(start), end=stamp(end),
                                  seconds=1200, intervals=json.dumps([{"start": stamp(start), "end": stamp(end), "seconds": 1200}]), status="complete")
        self.store.split_segment(identity, [(start + timedelta(minutes=10), self.a), (end, self.b)])
        first, second = self.store.stats(start.date().isoformat()), self.store.stats(end.date().isoformat())
        self.assertEqual(first["focus"], 600)
        self.assertEqual(second["focus"], 600)
        self.assertEqual(first["completed"] + second["completed"], 1)

    def test_two_completions_are_idempotent(self):
        self.assertTrue(self.store.complete_task(self.a))
        self.assertFalse(self.store.complete_task(self.a))
        self.assertEqual(len([t for t in self.store.tasks(self.day) if t["status"] == "done"]), 1)
        self.assertTrue(self.store.complete_task(self.a, False))

    def test_adjacent_manual_and_precise_conflict_information(self):
        end = self.clock.now().replace(second=27, microsecond=500000)
        self.store.manual(end - timedelta(minutes=20), end, 1200, self.a, "", "")
        with self.assertRaises(ValueError) as captured:
            self.store.manual(end - timedelta(seconds=27), end + timedelta(minutes=10), 300, self.b, "", "")
        self.assertIn("复习单词", str(captured.exception))
        self.assertEqual(captured.exception.conflict_end, end)
        self.store.manual(end, end + timedelta(minutes=10), 600, self.b, "", "")
        self.assertEqual(self.store.stats(self.day)["focus"], 1800)

    def test_backup_round_trip_preserves_allocations_and_two_summary_fields(self):
        identity, start, finish = self.original_record()
        self.store.split_segment(identity, [(start + timedelta(minutes=34), self.a), (finish, self.b)])
        session = self.store.add("sessions", start=stamp(start), end=stamp(finish), status="complete", activities="复习＋背单词", summary="明天继续")
        path = Path(self.temp.name) / "v2.json"
        self.store.backup(path)
        self.store.restore(path)
        self.assertEqual(self.store.one("sessions", session)["activities"], "复习＋背单词")
        self.assertEqual(self.store.stats(self.day)["task_seconds"], {self.a: 1740, self.b: 1800})

    def test_restore_v1_export(self):
        identity, start, finish = self.original_record()
        path = Path(self.temp.name) / "v1.json"
        self.store.backup(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = 1
        for record in data["tables"]["segments"]:
            for key in ("tasks", "allocations", "allocation_mode", "revisions"):
                record.pop(key)
        for record in data["tables"]["sessions"]:
            for key in ("tasks", "activities"):
                record.pop(key)
        path.write_text(json.dumps(data), encoding="utf-8")
        self.store.restore(path)
        self.assertEqual(references(self.store.one("segments", identity))[0]["id"], self.a)
        self.assertEqual(self.store.stats(self.day)["focus"], 3540)


class MigrationTests(unittest.TestCase):
    def test_v1_database_backup_and_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            db = sqlite3.connect(path / "tomatotime.sqlite3")
            db.executescript("""
                CREATE TABLE tasks (id INTEGER PRIMARY KEY,day TEXT,title TEXT,notes TEXT DEFAULT '',estimate REAL DEFAULT 0,status TEXT DEFAULT 'todo',position INTEGER DEFAULT 0,origin_id INTEGER,deleted INTEGER DEFAULT 0);
                CREATE TABLE sessions (id INTEGER PRIMARY KEY,task_id INTEGER,task_name TEXT,start TEXT,end TEXT,status TEXT,config TEXT,summary TEXT DEFAULT '');
                CREATE TABLE segments (id INTEGER PRIMARY KEY,session_id INTEGER,task_id INTEGER,task_name TEXT,round INTEGER,kind TEXT,start TEXT,end TEXT,seconds REAL DEFAULT 0,intervals TEXT DEFAULT '[]',status TEXT,source TEXT DEFAULT 'timer',content TEXT DEFAULT '',reflection TEXT DEFAULT '',next_step TEXT DEFAULT '',original TEXT DEFAULT '');
                PRAGMA user_version=1;
            """)
            end = now() - timedelta(days=1)
            db.execute("INSERT INTO tasks(id,day,title) VALUES(1,?,?)", (end.date().isoformat(), "旧任务"))
            db.execute("INSERT INTO segments(task_id,task_name,kind,start,end,seconds,intervals,status,content) VALUES(1,'旧任务','focus',?,?,60,?,'complete','旧笔记')",
                       (stamp(end - timedelta(seconds=60)), stamp(end), json.dumps([{"start": stamp(end - timedelta(seconds=60)), "end": stamp(end), "seconds": 60}])))
            db.commit()
            db.close()
            store = Store(path)
            try:
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertTrue(list(path.glob("before-v0.2-*.sqlite3")))
                self.assertEqual(store.stats(end.date().isoformat())["task_seconds"], {1: 60})
                self.assertEqual(store.one("segments", 1)["content"], "旧笔记")
            finally:
                store.close()
