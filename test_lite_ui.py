import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from account_store import AccountStore
from lite_ui import LitePanel, ProfileEditor, read_attachments


class LitePanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.desk = Mock()
        self.desk.account_store = AccountStore(self.folder / "vault.db")
        self.panel = LitePanel(self.root, self.desk)
        self.addCleanup(self.panel.close)
        fake_env = patch.dict(os.environ, LITE_UI_TEST_KEY="secret-for-offline-ui-test")
        fake_env.start()
        self.addCleanup(fake_env.stop)

    def profile(self, name="Demo", provider="GPT"):
        uid = self.desk.account_store.save_profile(name, provider, "sample", key_env="LITE_UI_TEST_KEY")
        self.panel.refresh_profiles(uid)
        return uid

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.panel.poll()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for the local UI task")

    def test_edit_import_delete_and_apply_profile(self):
        panel = self.panel
        panel.refresh_profiles()
        self.assertTrue(panel.actions["send"].instate(["disabled"]))
        self.assertFalse(panel.account_menu.instate(["disabled"]))
        self.assertTrue(panel.chat_menu.instate(["disabled"]))
        self.assertEqual(panel.account_menu.menu.entrycget("编辑当前账号…", "state"), "disabled")
        uid = self.profile()
        editor = ProfileEditor(panel, panel.profiles[0])
        self.assertEqual(editor.fields["api_key"].get(), "")
        editor.fields["name"].set("Main profile")
        editor.save()
        self.assertEqual(panel.selected_id, uid)
        self.assertIn("Main profile", panel.choices.get())
        data = self.folder / "profiles.json"
        data.write_text(json.dumps(dict(version=1, accounts=[dict(name="second", provider="Kimi", model="test",
                                                                   key_env="LITE_UI_TEST_KEY")])), encoding="utf-8")
        with patch("lite_ui.filedialog.askopenfilename", return_value=str(data)):
            panel.account_menu.menu.invoke("导入账号 JSON…")
        second = panel.selected_id
        self.assertNotEqual(second, uid)
        self.assertEqual(panel.current_config()["provider"], "Kimi")
        panel.actions["team_agent"].invoke()
        self.desk.apply_profile.assert_called_with(second, builtin=True)
        panel.actions["team_provider"].invoke()
        self.desk.apply_profile.assert_called_with(second, builtin=False)
        with patch("lite_ui.messagebox.askyesno", return_value=True):
            panel.actions["delete"].invoke()
        self.assertEqual(panel.selected_id, uid)
        self.assertNotIn("secret-for-offline-ui-test", panel.choices.get())

    def test_run_follow_up_with_snapshots_and_export(self):
        uid = self.profile()
        panel = self.panel
        data = self.folder / "data.json"
        data.write_text('{"count":3}', encoding="utf-8")
        with patch("lite_ui.filedialog.askopenfilenames", return_value=[str(data)]):
            panel.actions["attach"].invoke()
        replies = [dict(message="first", files={"src/app.txt":"draft"}), dict(message="second", files={"src/app.txt":"final"})]
        with patch("lite_ui.run_task", side_effect=replies) as run:
            for prompt in ("start", "continue"):
                panel.prompt.insert("1.0", prompt)
                panel.actions["send"].invoke()
                self.wait_for(lambda: panel.stop is None)
        self.assertEqual(run.call_args.args[:1], ("GPT",))
        self.assertEqual(run.call_args.kwargs["files"], {"data.json": '{"count":3}', "src/app.txt": "draft"})
        self.assertEqual(run.call_args.kwargs["history"], [
            {"role":"user", "content":"start"}, {"role":"assistant", "content":"first"}])
        with patch("lite_ui.filedialog.askdirectory", return_value=str(self.folder)):
            panel.actions["export"].invoke()
        result, = self.folder.glob("project-*/src/app.txt")
        self.assertEqual(result.read_text("utf-8"), "final")
        self.profile("Other", "Claude")
        self.assertFalse(panel.transcript.get("1.0", "end").strip())
        panel.refresh_profiles(uid)
        self.assertIn("second", panel.transcript.get("1.0", "end"))

    def test_cancel_rejects_stale_completion(self):
        self.profile()
        entered = threading.Event()
        finish = threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            finish.wait(2)
            return dict(message="LATE", files={"late.txt":"late"})
        panel = self.panel
        with patch("lite_ui.run_task", side_effect=delayed):
            panel.prompt.insert("1.0", "hold")
            panel.actions["send"].invoke()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(panel.account_menu.instate(["disabled"]))
                self.assertTrue(panel.chat_menu.instate(["disabled"]))
                panel.actions["stop"].invoke()
                self.assertIsNone(panel.stop)
                self.assertFalse(panel.account_menu.instate(["disabled"]))
                self.assertFalse(panel.chat_menu.instate(["disabled"]))
            finally:
                finish.set()
            deadline = time.monotonic() + 2
            while panel.events.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(panel.events.empty())
            panel.poll()
        self.assertFalse(panel._session()["history"])
        self.assertFalse(panel._session()["drafts"])

    def test_attachments_are_atomic_and_distinct_from_key_import(self):
        first = self.folder / "good.txt"
        first.write_text("hello")
        second = self.folder / "personal.json"
        second.write_text('{"accounts":[],"version":1}')
        previous = {"readme.txt":"original"}
        with self.assertRaisesRegex(ValueError, "账号"):
            read_attachments([first, second], previous)
        self.assertEqual(previous, {"readme.txt":"original"})
