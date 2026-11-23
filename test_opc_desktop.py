import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import Desk
from engine import Job
from opc_skills import LEAD
from team_modes import OPC, STANDARD


class OPCDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, LOCALAPPDATA=self.temp.name, OPC_SAMPLE_KEY="example-on-test-only").start()
        patch("app.saved_address", return_value=None).start()
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.desk = Desk(self.root)
        self.root.geometry("880x630")
        self.root.update()
        self.rootdir = Path(self.temp.name, "project")
        self.rootdir.mkdir()

    def test_office_config_reaches_task_and_can_switch_back(self):
        desk = self.desk
        self.assertEqual(desk.task_mode.get(), STANDARD)
        desk.team_mode.set("leader")
        desk.leader.set("Gemini")
        uid = desk.account_store.save_profile("样例账号", "GPT", "test-model", key_env="OPC_SAMPLE_KEY")
        team = {LEAD: {"kind": "api", "target": uid}, "后端开发员": {"kind": "mcp", "target": "Codex"}}
        desk.mode_buttons[OPC].invoke()
        self.root.update()
        self.assertEqual(desk.tabs.select(), str(desk.opc))
        self.assertEqual(desk.task_mode.get(), OPC)
        desk.opc.restore(team)
        desk.opc.start_button.invoke()
        self.assertEqual(desk.tabs.select(), str(desk.tasks))
        self.assertEqual(desk.account_store.opc_team(), team)
        self.assertIn("OPC", desk.team_link.cget("text"))
        desk.project.set(str(self.rootdir))
        desk.goal.insert("1.0", "生成导出文件")
        with patch.object(desk, "_launch") as launch:
            desk.start_button.invoke()
            configs, creation = launch.call_args.args
            self.assertEqual(creation[-2:], (LEAD, team))
            self.assertEqual(set(configs), set(team))
            self.assertEqual(configs[LEAD]["runtime_member"], "GPT")
        desk.mode_buttons[STANDARD].invoke()
        self.assertEqual(desk.task_mode.get(), STANDARD)
        self.assertEqual(desk.opc_routes, team)
        desk.team_link.invoke()
        self.assertEqual(desk.tabs.select(), str(desk.team))
        self.assertEqual((desk._members(), desk.team_mode.get(), desk.leader.get()),
                         (["GPT", "Gemini"], "leader", "Gemini"))
        desk.nav_buttons[str(desk.tasks)].invoke()
        desk.mode_buttons[OPC].invoke()
        self.assertEqual(desk.tabs.select(), str(desk.tasks))
        self.assertEqual(desk.opc_routes, team)
        self.assertEqual(desk.goal.get("1.0", "end-1c"), "生成导出文件")

    def test_opc_draft_requires_employees_before_dispatch(self):
        desk = self.desk
        desk.project.set(str(self.rootdir))
        desk.goal.insert("1.0", "构建阅读清单")
        desk.mode_buttons[OPC].invoke()
        desk.nav_buttons[str(desk.tasks)].invoke()
        with patch("app.messagebox.showerror") as error, patch.object(desk, "_launch") as launch:
            desk.start_button.invoke()
        error.assert_called_once()
        launch.assert_not_called()
        self.assertEqual(desk.tabs.select(), str(desk.opc))
        self.assertEqual(desk.task_mode.get(), OPC)

    def test_employee_mcp_guides_preserve_both_regular_transports(self):
        desk = self.desk
        software = desk.software
        software.codex_mode.set("codex-native")
        software.harness_mode.set("baton")
        routes = {LEAD: {"kind": "mcp", "target": "Codex"},
                  "前端设计师": {"kind": "mcp", "target": "DeepSeek Harness"}}
        desk.mode_buttons[OPC].invoke()
        self.root.update()
        desk.opc.restore(routes)
        for role, route in routes.items():
            desk.nav_buttons[str(desk.opc)].invoke()
            desk.opc.details_buttons[role].invoke()
            desk.opc.connect_button.invoke()
            self.root.update()
            self.assertEqual(desk.tabs.select(), str(software))
            self.assertEqual(software.app_name.get(), route["target"])
            self.assertFalse(software.native_selected(route["target"]))
            self.assertTrue(software.copy_instruction.winfo_ismapped())
            self.assertFalse(software.preset.winfo_ismapped())
        desk.nav_buttons[str(desk.team)].invoke()
        desk.software_link.invoke()
        self.root.update()
        self.assertEqual(desk.task_mode.get(), STANDARD)
        self.assertTrue(software.preset.winfo_ismapped())
        self.assertTrue(software.native_selected("Codex"))
        self.assertTrue(software.native_selected("DeepSeek Harness"))

    def test_created_mode_is_locked_and_reopened_even_from_the_other_mode(self):
        desk = self.desk
        routes = {role: {"kind": "mcp", "target": "Codex"} for role in (LEAD, "后端开发员")}
        for mode, other in ((STANDARD, OPC), (OPC, STANDARD)):
            with self.subTest(mode=mode):
                desk.nav_buttons[str(desk.tasks)].invoke()
                desk.mode_buttons[mode].invoke()
                if mode == OPC:
                    self.root.update()
                    desk.opc.restore(routes)
                    desk.opc.start_button.invoke()
                desk.project.set(str(self.rootdir))
                desk.goal.insert("1.0", "生成报告")
                with patch.object(Job, "run", return_value="就绪"):
                    desk.start_button.invoke()
                    self.assertTrue(desk.mode_buttons[other].instate(["disabled"]))
                    deadline = time.monotonic() + 5
                    while desk.running and time.monotonic() < deadline:
                        self.root.update()
                        time.sleep(.01)
                self.assertFalse(desk.running)
                self.assertIsNotNone(desk.job)
                path = desk.job.path
                self.assertEqual(Job.load(path).state["mode"], mode)
                desk.mode_buttons[other].invoke()
                self.assertEqual(desk.task_mode.get(), mode)
                desk.new_button.invoke()
                self.assertTrue(desk.mode_buttons[other].instate(["!disabled"]))
                desk.mode_buttons[other].invoke()
                self.assertEqual(desk.task_mode.get(), other)
                desk.nav_buttons[str(desk.tasks)].invoke()
                with patch("app.filedialog.askdirectory", return_value=str(path)):
                    desk.reopen.invoke()
                self.assertEqual(desk.task_mode.get(), mode)
                self.assertTrue(desk.mode_buttons[other].instate(["disabled"]))
                desk.new_button.invoke()

    def test_reopen_resume_and_change_work_connection(self):
        routes = {r: {"kind": "mcp", "target": "Codex"} for r in (LEAD, "测试工程师")}
        job = Job.create(self.rootdir, "编写测试", [], list(routes), LEAD, routes)
        job.state["status"] = "已暂停"
        job.save()
        desk = self.desk
        with patch("app.filedialog.askdirectory", return_value=str(job.path)):
            desk.open_job()
        self.assertEqual(desk.opc_routes, routes)
        desk.team_link.invoke()
        self.root.update()
        self.assertTrue(desk.opc.checks["测试工程师"].instate(["disabled"]))
        routes["测试工程师"] = {"kind": "mcp", "target": "Claude Desktop"}
        desk.opc.restore(routes)
        desk.opc.start_button.invoke()
        self.assertEqual(Job.load(job.path).state["opc_routes"], routes)
        with patch.object(desk, "_launch") as launch:
            desk.continue_job()
            configs, = launch.call_args.args
        self.assertEqual(configs["测试工程师"]["runtime_member"], "Claude Desktop")
        self.assertNotIn("bridge", json.dumps(Job.load(job.path).state["opc_routes"]))
