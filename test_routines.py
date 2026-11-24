import json
import os
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from account_store import AccountStore
from routines import ACTIVE, INTERRUPTED, RoutineStore
from routines_ui import RoutineEditor, RoutinesPanel


class RoutinesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.accounts = AccountStore(Path(temp.name) / "accounts.db")
        self.store = RoutineStore(self.accounts)
        self.started = datetime(2026, 11, 23, 8)
        self.key = "unit-test-placeholder-key"
        self.env = patch.dict(os.environ, ROUTINE_TEST_KEY=self.key)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.profile = self.accounts.save_profile("办公", "GPT", "sample-model", key_env="ROUTINE_TEST_KEY")
        self.bot = self.accounts.save_bot("校对", "检查术语", self.profile)

    def create(self, clock="09:00", frequency="weekdays"):
        return self.store.save("周报", self.bot, "列出发布检查清单", frequency, clock, now=self.started)

    def test_schedule_reopens_and_runs_once_per_eligible_day(self):
        rid = self.create()
        due = self.started.replace(hour=9)
        with patch("routines.call", return_value="检查完成") as model:
            self.assertIsNone(self.store.execute(now=self.started))
            self.assertTrue(self.store.needs_tick(due))
            run = self.store.execute(now=due)
            provider, cfg, prompt = model.call_args.args
            self.assertEqual((provider, cfg["model"], cfg["key"]), ("GPT", "sample-model", self.key))
            self.assertEqual(json.loads(prompt.split("\n", 1)[1])["responsibilities"], "检查术语")
            self.assertNotIn(self.key, prompt)
            again = RoutineStore(AccountStore(self.accounts.path))
            self.assertFalse(again.needs_tick(due))
            self.assertIsNone(again.execute(now=due))
            self.assertEqual(again.result(run), "检查完成")
            self.assertEqual(again.history(rid)[0]["status"], "完成")
            self.assertIsNone(again.execute(now=due + timedelta(days=5)))  # Saturday
            monday = due + timedelta(days=7, hours=4)
            again.execute(now=monday)
            self.assertIsNone(again.execute(now=monday))
            self.assertEqual(model.call_count, 2)

    def test_pause_edit_manual_and_bot_deletion(self):
        rid = self.create()
        self.store.toggle(rid, self.started)
        with patch("routines.call", return_value="好") as model:
            self.assertIsNone(self.store.execute(now=self.started.replace(hour=10)))
            run = self.store.execute(rid, now=self.started.replace(hour=10))
            self.assertEqual(self.store.history(rid)[0]["trigger"], "手动")
            self.store.save("每日复盘", self.bot, "复盘", "daily", "11:45", True, rid, now=self.started)
            self.assertFalse(self.store.needs_tick(self.started.replace(hour=11)))
            self.assertTrue(self.store.needs_tick(self.started.replace(hour=12)))
            self.assertEqual(self.store.result(run), "好")
            self.assertEqual(model.call_count, 1)
        self.accounts.delete_bot(self.bot)
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.store.history(rid), [])

    def test_two_instances_and_manual_claim_do_not_duplicate_todays_slot(self):
        rid = self.create()
        now = self.started.replace(hour=10)
        entered, release = threading.Event(), threading.Event()
        def ask(*args):
            entered.set()
            release.wait(5)
            return "完成"
        other = RoutineStore(AccountStore(self.accounts.path))
        with patch("routines.call", side_effect=ask) as model:
            thread = threading.Thread(target=self.store.execute, args=(rid, now))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertIsNone(other.execute(now=now))
                with self.assertRaisesRegex(ValueError, "正在运行"):
                    other.execute(rid, now)
                self.assertEqual(other.history(rid)[0]["status"], ACTIVE)
            finally:
                release.set()
                thread.join(5)
            self.assertIsNone(other.execute(now=now))
            self.assertEqual(model.call_count, 1)
        self.assertEqual(other.history(rid)[0]["status"], "完成")

    def test_interrupted_run_stays_visible_and_can_be_retried(self):
        rid = self.create()
        due = self.started.replace(hour=9)
        abandoned, _, _ = self.store._claim(rid, due)
        with patch("routines.call", side_effect=ValueError("配置失败 " + self.key)) as model:
            self.assertTrue(self.store.needs_tick(due))
            self.assertIsNone(self.store.execute(now=due))
            self.assertEqual(self.store.history(rid)[0]["status"], INTERRUPTED)
            self.assertIn("重试", self.store.result(abandoned))
            retry = self.store.execute(rid, now=due)
            self.assertEqual(model.call_count, 1)
            self.assertNotIn(self.key, self.store.result(retry))
            self.assertEqual(self.store.history(rid)[0]["status"], "失败")

    def test_desktop_create_run_view_pause_delete(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.geometry("704x630")
        panel = RoutinesPanel(root, SimpleNamespace(account_store=self.accounts))
        panel.pack(fill="both", expand=True)
        panel.refresh()
        panel.new.invoke()
        editor = next(c for c in panel.winfo_children() if isinstance(c, RoutineEditor))
        editor.name.set("整理议程")
        editor.enabled.set(False)
        editor.prompt.insert("1.0", "准备例会议程模板")
        editor.save()
        root.update()
        rid = panel.selected
        self.assertEqual(panel.rows[rid]["enabled"], 0)
        self.assertEqual(panel.buttons["暂停"].cget("text"), "启用")
        with patch("routines.call", return_value="周会议程草案"):
            panel.run_now()
            deadline = time.monotonic() + 5
            while panel.worker is not None and time.monotonic() < deadline:
                panel.poll()
                root.update()
                time.sleep(.01)
        self.assertIn("周会议程草案", panel.answer.get("1.0", "end-1c"))
        panel.buttons["暂停"].invoke()
        self.assertEqual(panel.rows[rid]["enabled"], 1)
        with patch("routines_ui.messagebox.askyesno", return_value=True):
            panel.buttons["删除"].invoke()
        self.assertFalse(panel.rows)
        self.assertFalse(panel.history.get_children())
        self.assertEqual(panel.answer.get("1.0", "end-1c"), "")

    def test_due_routine_runs_while_panel_is_hidden(self):
        # A due persisted routine must run even when the app starts on another page.
        rid = self.create(frequency="daily", clock="00:00")
        now = self.started + timedelta(days=1)
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        panel = RoutinesPanel(root, SimpleNamespace(account_store=self.accounts))
        with patch("routines.datetime", wraps=datetime) as clock, patch("routines.call", return_value="已检查") as model:
            clock.now.return_value = now
            panel.poll()
            deadline = time.monotonic() + 4
            while panel.worker is not None and time.monotonic() < deadline:
                time.sleep(.01)
                panel.poll()
            model.assert_called_once()
            self.assertEqual(self.store.history(rid)[0]["trigger"], "定时")

    def test_locked_database_does_not_block_workbench_poll(self):
        self.create()
        root = tk.Tk()
        self.addCleanup(root.destroy)
        panel = RoutinesPanel(root, SimpleNamespace(account_store=self.accounts))
        panel.pack()
        root.update()
        external = sqlite3.connect(self.accounts.path)
        try:
            external.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            panel.poll()
            self.assertLess(time.monotonic() - start, 1)
        finally:
            panel.close()
            external.close()
            if panel.worker:
                panel.worker.join(3)


if __name__ == "__main__":
    unittest.main()
