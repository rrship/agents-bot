import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from account_store import AccountStore
from app import Desk
from engine import Job
from workspace_search import Destination, matches, recent_jobs, remember_job


class WorkspaceLookup(unittest.TestCase):
    def test_recent_jobs_survive_restart_with_bounded_deduplicated_index(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profiles.db"
            store = AccountStore(path)
            self.assertEqual(recent_jobs(store), [])
            from types import SimpleNamespace
            for number in range(42):
                job = SimpleNamespace(path=Path(temp) / str(number),
                                      state={"goal": "目标 " + str(number), "root": temp})
                remember_job(store, job)
            remember_job(store, job)
            rows = recent_jobs(AccountStore(path))
            self.assertEqual(len(rows), 40)
            self.assertEqual((rows[0]["mode"], rows[0]["title"]), ("standard", "目标 41"))
            self.assertEqual(rows[-1]["title"], "目标 2")

    def test_terms_match_name_category_and_connection(self):
        item = Destination("Bot", "id", "设计助理", "Gemini")
        self.assertEqual(matches([item], "gem 设计"), [item])
        self.assertEqual(matches([item], "Claude"), [])

    def test_quick_switcher_opens_saved_job_and_handles_running_job(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, LOCALAPPDATA=tmp), \
                patch("app.saved_address", return_value=""):
            root = tk.Tk()
            try:
                desk = Desk(root)
                workspace = Path(tmp) / "project"
                workspace.mkdir()
                job = Job.create(workspace, "筛选导出", [])
                desk.open_job(job.path)
                desk.new_job()
                desk.search_button.invoke()
                popup = desk.switcher
                popup.query.set("筛选")
                self.assertEqual([item.kind for item in popup.visible], ["最近任务"])
                popup._open()
                self.assertEqual(desk.job.path, job.path)
                desk.nav_buttons[str(desk.routines)].invoke()
                desk.running = True
                with patch("app.messagebox.showinfo") as notice:
                    desk._open_destination(Destination("最近任务", str(job.path), "筛选导出"))
                    notice.assert_called_once()
                desk.running = False
                self.assertEqual(desk.tabs.select(), str(desk.routines))
                desk.search_button.invoke()
                desk.switcher.query.set("成员与连接")
                desk.switcher._open()
                self.assertEqual(desk.tabs.select(), str(desk.team))
            finally:
                root.destroy()
