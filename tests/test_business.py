"""检查会影响时间记录、提醒防重和备份可靠性的业务规则。"""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tomatotime.core import Clock, Config, Timer, day_seconds, next_due, now, stamp
from tomatotime.controller import Controller
from tomatotime.storage import Store


class FakeClock(Clock):
    def __init__(self, value=None):
        self.wall = value or now().replace(hour=10, minute=0, second=0, microsecond=0)
        self.mono = 1000.0

    def now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds, wall_seconds=None):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds if wall_seconds is None else wall_seconds)


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.timer = Timer(self.clock)

    def test_long_replaces_short_and_no_final_rest(self):
        self.timer.start(Config(1, 1, 8, True, 4, 2))
        phases = []
        while self.timer.active:
            phases.append(self.timer.kind)
            self.clock.advance(self.timer.duration)
            self.assertEqual(self.timer.tick(), "boundary")
            self.timer.transition()
        self.assertEqual(phases, ["focus", "short", "focus", "short", "focus", "short", "focus", "long",
                                  "focus", "short", "focus", "short", "focus", "short", "focus"])
        self.assertEqual(self.timer.completed, 8)

    def test_four_cycles_finish_before_long(self):
        self.timer.start(Config(1, 1, 4, True, 4, 2))
        phases = []
        while self.timer.active:
            phases.append(self.timer.kind)
            self.clock.advance(1)
            self.timer.tick()
            self.timer.transition()
        self.assertNotIn("long", phases)

    def test_pause_excludes_elapsed_gap(self):
        self.timer.start(Config(20))
        self.clock.advance(3)
        self.timer.pause()
        self.clock.advance(500)
        self.timer.resume()
        self.clock.advance(4)
        self.timer.tick()
        self.assertEqual(self.timer.elapsed, 7)
        self.assertEqual(sum(f["seconds"] for f in self.timer.fragments()), 7)

    def test_sleep_and_clock_change_pause_without_inflation(self):
        self.timer.start(Config(100))
        self.clock.advance(2)
        self.timer.tick()
        self.clock.advance(1000)
        self.assertEqual(self.timer.tick(), "interrupted")
        self.assertEqual(self.timer.elapsed, 2)
        self.timer.resume()
        self.clock.advance(1, 3601)
        self.assertEqual(self.timer.tick(), "interrupted")
        self.assertEqual(self.timer.elapsed, 2)

    def test_late_callback_only_finishes_current_phase(self):
        self.timer.start(Config(1, 1, 4))
        self.clock.advance(10)
        self.assertEqual(self.timer.tick(), "boundary")
        self.timer.transition()
        self.assertEqual(self.timer.kind, "short")
        self.assertEqual(self.timer.round, 1)
        self.assertEqual(self.timer.elapsed, 0)

    def test_restore_excludes_offline_gap(self):
        self.timer.start(Config(20))
        self.clock.advance(3)
        self.timer.tick()
        state = self.timer.snapshot()
        self.clock.advance(500)
        other = Timer(self.clock)
        other.restore(state)
        self.assertTrue(other.paused)
        other.resume()
        self.clock.advance(2)
        other.tick()
        self.assertEqual(other.elapsed, 5)

    def test_free_timer_not_auto_finished(self):
        self.timer.start(Config(1, free=True))
        self.clock.advance(10)
        self.assertIsNone(self.timer.tick())
        self.assertEqual(self.timer.elapsed, 10)

    def test_estimate_excludes_final_rest(self):
        self.assertEqual(Config(1500, 300, 8, True, 4, 900).total(), 14700)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.day = (now() - timedelta(days=1)).date().isoformat()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_midnight_split_with_pauses(self):
        start = now().replace(hour=23, minute=50, second=0, microsecond=0) - timedelta(days=2)
        end = start + timedelta(minutes=20)
        result = day_seconds([{"start": stamp(start), "end": stamp(end), "seconds": 1200}])
        self.assertEqual(result[start.date().isoformat()], 600)
        self.assertEqual(result[end.date().isoformat()], 600)

    def test_unconfirmed_mixed_record_not_counted(self):
        end = now() - timedelta(days=1)
        self.store.manual(end - timedelta(hours=1), end, None, None, "阅读＋休息", "")
        self.assertEqual(self.store.stats(end.date().isoformat())["focus"], 0)

    def test_overlap_rejected_and_correction_retains_original(self):
        end = now() - timedelta(days=1)
        first = self.store.manual(end - timedelta(hours=1), end, 1800, None, "阅读", "")
        with self.assertRaisesRegex(ValueError, "重叠"):
            self.store.manual(end - timedelta(minutes=20), end + timedelta(minutes=10), 100, None, "", "")
        self.store.manual(end - timedelta(hours=1), end, 1200, None, "阅读＋休息", "", first)
        self.assertTrue(self.store.one("segments", first)["original"])
        self.assertEqual(self.store.stats(end.date().isoformat())["focus"], 1200)

    def test_carry_preserves_origin_and_does_not_copy_time(self):
        task = self.store.add_task(self.day, "背单词", "110 个", 3600)
        new = self.store.carry(task)
        self.assertEqual(self.store.one("tasks", new)["origin_id"], task)
        self.assertEqual(self.store.one("tasks", task)["status"], "carried")
        with self.assertRaises(ValueError):
            self.store.carry(task)

    def test_backup_round_trip_and_reject_malformed_before_changes(self):
        task = self.store.add_task(self.day, "背单词")
        (self.store.audio_dir / "test.wav").write_bytes(b"sound")
        path = Path(self.temp.name) / "backup.json"
        self.store.backup(path)
        self.store.update("tasks", task, title="修改后")
        self.store.restore(path)
        self.assertEqual(self.store.one("tasks", task)["title"], "背单词")
        self.assertEqual((self.store.audio_dir / "test.wav").read_bytes(), b"sound")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tables"]["tasks"][0]["unknown"] = "bad"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.restore(path)
        self.assertEqual(self.store.one("tasks", task)["title"], "背单词")

    def test_backup_rejects_audio_path_traversal(self):
        path = Path(self.temp.name) / "backup.json"
        self.store.backup(path)
        data = json.loads(path.read_text())
        data["audio"] = {"../escape.wav": "YQ=="}
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.store.restore(path)

    def test_markdown_contains_original_notes_and_task_state(self):
        self.store.add_task(self.day, "阅读", "需要批改", status="done")
        self.store.save_day(self.day, reflection="明天复习错题")
        text = self.store.markdown(self.day)
        self.assertIn("[x] 阅读", text)
        self.assertIn("明天复习错题", text)

    def test_time_correction_preserves_focus_completion_and_original(self):
        end = now() - timedelta(days=1)
        identity = self.store.add("segments", round=1, kind="focus", start=stamp(end - timedelta(minutes=25)), end=stamp(end),
                                  seconds=1500, intervals=json.dumps([{"start": stamp(end - timedelta(minutes=25)), "end": stamp(end), "seconds": 1500}]),
                                  status="complete", source="timer")
        self.store.manual(end - timedelta(minutes=25), end, 1200, None, "修正实际时间", "", identity)
        record = self.store.one("segments", identity)
        self.assertEqual(record["kind"], "focus")
        self.assertEqual(record["status"], "complete")
        self.assertEqual(self.store.stats(end.date().isoformat())["completed"], 1)
        self.assertEqual(json.loads(record["original"])["seconds"], 1500)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.clock = FakeClock()
        self.ctrl = Controller(self.store, self.clock)
        self.ctrl.poll_reminders()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_complete_cycle_keeps_task_manual_and_records_each_phase(self):
        task = self.store.add_task(self.clock.now().date().isoformat(), "阅读")
        self.ctrl.start(Config(2, 1, 2), task)
        for seconds in (2, 1, 2):
            self.clock.advance(seconds)
            self.ctrl.tick()
        self.assertFalse(self.ctrl.timer.active)
        records = self.store.rows("SELECT * FROM segments ORDER BY id")
        self.assertEqual([r["kind"] for r in records], ["focus", "short", "focus"])
        self.assertEqual(self.store.stats(self.clock.now().date().isoformat())["focus"], 4)
        self.assertNotEqual(self.store.one("tasks", task)["status"], "done")

    def test_note_stays_with_original_segment(self):
        self.ctrl.start(Config(2, 1, 2))
        original = self.ctrl.segment_id
        self.clock.advance(2)
        self.ctrl.tick()
        self.ctrl.save_note(original, "背了 30 个", "有些混淆", "继续复习")
        self.clock.advance(1)
        self.ctrl.tick()
        self.assertEqual(self.store.one("segments", original)["content"], "背了 30 个")
        self.assertEqual(self.store.one("segments", self.ctrl.segment_id)["content"], "")

    def test_repeat_reminder_fires_once_and_snoozes(self):
        notices = []
        self.ctrl.notices.connect(lambda rows: notices.extend(rows))
        identity = self.ctrl.add_reminder("阅读", self.clock.now() + timedelta(seconds=2), repeat="daily")
        self.clock.advance(2)
        self.ctrl.tick()
        self.ctrl.tick()
        self.assertEqual(len(notices), 1)
        occurrence = notices[0]["id"]
        self.ctrl.acknowledge([occurrence], "snoozed", 5)
        self.clock.advance(299)
        self.ctrl.tick()
        self.assertEqual(len(notices), 1)
        self.clock.advance(1)
        self.ctrl.tick()
        self.assertEqual(len(notices), 2)
        self.assertEqual(len(self.store.rows("SELECT * FROM occurrences")), 1)
        self.assertGreater(parse_dt(self.store.one("reminders", identity)["due"]), self.clock.now())

    def test_missed_repeat_is_one_summary_not_backlog(self):
        identity = self.ctrl.add_reminder("复习", self.clock.now() + timedelta(seconds=2), repeat="daily")
        self.clock.advance(86400 * 5)
        notices = []
        self.ctrl.notices.connect(lambda rows: notices.extend(rows))
        self.ctrl.tick()
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]["missed"])
        self.assertGreater(parse_dt(self.store.one("reminders", identity)["due"]), self.clock.now())

    def test_schedule_does_not_override_running_timer(self):
        identity = self.ctrl.add_reminder("预约", self.clock.now() + timedelta(seconds=1), kind="schedule", config={"focus": 2})
        self.ctrl.start(Config(10))
        original = self.ctrl.session_id
        self.clock.advance(1)
        self.ctrl.tick()
        self.assertEqual(self.ctrl.session_id, original)
        self.assertEqual(self.store.rows("SELECT * FROM occurrences")[0]["status"], "skipped")

    def test_schedule_starts_when_idle(self):
        self.ctrl.add_reminder("预约", self.clock.now() + timedelta(seconds=1), kind="schedule", config={"focus": 2})
        self.clock.advance(1)
        self.ctrl.tick()
        self.assertTrue(self.ctrl.timer.active)
        self.assertEqual(self.ctrl.timer.config.focus, 2)

    def test_restart_recovers_paused_with_notes(self):
        self.ctrl.start(Config(10))
        self.clock.advance(3)
        self.ctrl.tick()
        self.ctrl.save()
        self.clock.advance(3600)
        recovered = Controller(self.store, self.clock)
        self.assertTrue(recovered.timer.paused)
        self.assertEqual(recovered.timer.elapsed, 3)
        self.assertTrue(recovered.recovery)

    def test_snooze_replays_sound_once(self):
        sounds = []
        self.ctrl.sound.connect(lambda event, _: sounds.append(event))
        self.ctrl.add_reminder("复习", self.clock.now() + timedelta(seconds=1))
        self.clock.advance(1)
        self.ctrl.tick()
        identity = self.store.rows("SELECT * FROM occurrences")[0]["id"]
        self.ctrl.acknowledge([identity], "snoozed", 5)
        self.clock.advance(300)
        self.ctrl.tick()
        self.ctrl.tick()
        self.assertEqual(sounds, ["reminder", "reminder"])


def parse_dt(value):
    return datetime.fromisoformat(value)


class RepeatTests(unittest.TestCase):
    def test_weekdays_skip_weekend(self):
        friday = now().replace(year=2026, month=11, day=20, hour=19, minute=0)
        following = next_due(friday, "weekdays", [], friday)
        self.assertEqual(following.weekday(), 0)
        self.assertEqual(following.day, 23)

    def test_weekly_selected_day(self):
        monday = now().replace(year=2026, month=11, day=23, hour=10, minute=0)
        self.assertEqual(next_due(monday, "weekly", [2], monday).weekday(), 2)
        with self.assertRaises(ValueError):
            next_due(monday, "weekly", [], monday)


if __name__ == "__main__":
    unittest.main()
