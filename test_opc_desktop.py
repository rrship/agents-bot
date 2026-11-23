import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from app import Desk
from engine import Job
from opc_skills import LEAD


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
        uid = desk.account_store.save_profile("样例账号", "GPT", "test-model", key_env="OPC_SAMPLE_KEY")
        team = {LEAD: {"kind": "api", "target": uid}, "后端开发员": {"kind": "mcp", "target": "Codex"}}
        desk.nav_buttons[str(desk.opc)].invoke()
        self.root.update()
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
        desk.regular_link.invoke()
        self.assertIsNone(desk.opc_routes)
        self.assertEqual(desk.tabs.select(), str(desk.team))
        self.assertEqual(desk.goal.get("1.0", "end-1c"), "生成导出文件")

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
