import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class TeamUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.desk = app.Desk(self.root)
        self.addCleanup(self.root.destroy)

    def test_roster_and_connection_capabilities(self):
        desk = self.desk
        self.assertEqual(desk._members(), list(app.DEFAULT_MEMBERS))
        self.assertEqual(len(desk.connections.tabs()), 6)
        for name, form in desk.forms.items():
            self.assertEqual(form["login"] is not None, name in app.ACCOUNT_NAMES)
            if name in app.EXTRA_ACCOUNTS:
                form["mode"].set("API Key")
                form["model"].set("api-fixture")
                form["mode"].set("账号登录")
                self.assertTrue(form["model_box"].instate(["disabled"]))
                self.assertEqual(desk._config(name)["model"], "")
                form["mode"].set("API Key")
                self.assertEqual(desk._config(name)["model"], "api-fixture")
        desk.team_mode.set("leader")
        desk.leader.set("Gemini")
        desk.member_vars["Gemini"].set(False)
        self.assertEqual(desk.leader.get(), "GPT")
        self.assertEqual(tuple(desk.leader_box.cget("values")), ("GPT",))

    def test_start_validates_count_and_configures_selected_only(self):
        desk = self.desk
        with tempfile.TemporaryDirectory() as folder:
            desk.project.set(folder)
            desk.goal.insert("1.0", "Create an app")
            desk.member_vars["Gemini"].set(False)
            with patch("app.messagebox.showerror") as error, patch.object(desk, "_launch") as launch:
                desk.start_button.invoke()
                error.assert_called_once()
                launch.assert_not_called()
                desk.member_vars["DeepSeek"].set(True)
                desk.forms["DeepSeek"]["mode"].set("API Key")
                desk.forms["DeepSeek"]["model"].set("fixture-model")
                desk.team_mode.set("leader")
                desk.leader.set("DeepSeek")
                desk.start_button.invoke()
                configs, creation = launch.call_args.args
                self.assertEqual(set(configs), {"GPT", "DeepSeek"})
                self.assertEqual(creation[-2:], (desk._members(), "DeepSeek"))
                self.assertEqual(configs["DeepSeek"]["model"], "fixture-model")

    def test_reopen_and_resume_saved_and_legacy_rosters(self):
        desk = self.desk
        with tempfile.TemporaryDirectory() as folder:
            job = app.Job.create(folder, "Saved task", [], ["Gemini", "DeepSeek"], "DeepSeek")
            with patch("app.filedialog.askdirectory", return_value=str(job.path)):
                desk.reopen.invoke()
                self.assertEqual(desk._members(), ["Gemini", "DeepSeek"])
                self.assertEqual(desk.leader.get(), "DeepSeek")
                self.assertTrue(desk.leader_box.instate(["disabled"]))
                desk.forms["DeepSeek"]["mode"].set("API Key")
                desk.forms["DeepSeek"]["model"].set("fixture-model")
                with patch.object(desk, "_launch") as launch:
                    desk.resume.invoke()
                    self.assertEqual(set(launch.call_args.args[0]), {"Gemini", "DeepSeek"})
                state = dict(job.state)
                del state["members"], state["leader"]
                Path(job.path, "state.json").write_text(json.dumps(state), encoding="utf-8")
                desk.reopen.invoke()
                self.assertEqual(desk.team_mode.get(), "peer")
                self.assertEqual(desk._members(), list(app.DEFAULT_MEMBERS))
