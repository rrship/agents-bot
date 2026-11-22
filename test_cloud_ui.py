import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import app
from cloud_room import dispatch
from cloud_ui import export_project, import_project


class CloudPanelTests(unittest.TestCase):
    def test_import_keeps_supported_text_and_omits_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_bytes(b"x" * 70000)
            (root / "secret.json").write_text("private")
            (root / "image.png").write_bytes(b"binary\0data")
            self.assertEqual(import_project(root), {"large.txt": "x" * 70000})
            (root / "large.txt").write_bytes(b"x" * 96001)
            with self.assertRaisesRegex(ValueError, "large.txt"):
                import_project(root)

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.desk = app.Desk(self.root)
        self.panel = self.desk.cloud
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.panel.disconnect)
        self.session = Mock(host="server.example", user="team", port=22)
        self.worker = dict(status="idle", message="未运行", members=[])
        self.session.rpc.side_effect = self.rpc

    def rpc(self, op, **fields):
        if op == "worker_status":
            return self.worker
        if op == "start_worker":
            self.worker = dict(status="running", message="正在运行", members=list(fields["configs"]))
            return self.worker
        if op == "stop_worker":
            self.worker = dict(status="stopped", message="已停止", members=[])
            return self.worker
        return dispatch(dict(op=op, **fields), Path(self.temp.name))

    def wait_for(self, fn):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.panel.poll()
            if fn():
                return
            time.sleep(.01)
        self.fail("timed out waiting for cloud operation")

    def connect_and_create(self):
        panel = self.panel
        self.assertIn("云服务器", [self.desk.tabs.tab(t, "text") for t in self.desk.tabs.tabs()])
        panel.host.set("server.example")
        panel.user.set("team")
        with patch("cloud_ui.CloudSSH", return_value=self.session) as ssh:
            panel.buttons["connect"].invoke()
            self.wait_for(lambda: panel.ready)
        ssh.assert_called_once_with("server.example", "team", "22", identity_file=None)
        self.desk.goal.insert("end", "Calendar")
        self.desk.team_mode.set("leader")
        self.desk.leader.set("GPT")
        panel.buttons["create"].invoke()
        self.wait_for(lambda: panel.view and not panel.busy)
        self.assertEqual(panel.view["leader"], "GPT")
        self.assertEqual(panel.room.get(), panel.view["room"])
        return panel.room.get()

    def test_cloud_ui_uses_room_member_config_and_phone_snapshot(self):
        room = self.connect_and_create()
        panel = self.panel
        panel.checks["Gemini"].invoke()
        with patch("cloud_ui.run_team") as runner:
            panel.buttons["local"].invoke()
            self.wait_for(lambda: runner.called and panel.runner is None)
        rpc, actual_room, configs, _, _ = runner.call_args.args
        self.assertEqual(actual_room, room)
        self.assertEqual(list(configs), ["Gemini"])
        self.desk.monitor = Mock()
        panel.share.set(True)
        self.desk.publish_mobile()
        mobile_fields = self.desk.monitor.publish.call_args.args
        self.assertEqual(mobile_fields[:4], ("Calendar", "云协作 · 进行中", ["GPT", "Gemini"], "GPT"))
        self.assertNotIn(room, str(mobile_fields))

    def test_confirm_before_sending_only_selected_api_credentials(self):
        room = self.connect_and_create()
        panel = self.panel
        panel.checks["GPT"].invoke()
        self.desk.forms["GPT"]["mode"].set("API Key")
        self.desk.forms["GPT"]["model"].set("fixture")
        with patch.dict(os.environ, OPENAI_API_KEY="fixture-key"), patch("cloud_ui.messagebox.askyesno") as confirm:
            confirm.return_value = False
            panel.buttons["server"].invoke()
            self.assertNotIn("start_worker", [c.args[0] for c in self.session.rpc.call_args_list])
            confirm.return_value = True
            panel.buttons["server"].invoke()
            self.wait_for(lambda: panel.remote_running)
        self.session.rpc.assert_any_call("start_worker", room=room, configs={"GPT": dict(
            mode="api", model="fixture", key="fixture-key")})
        self.assertNotIn("fixture-key", panel.log.get("1.0", "end"))
        panel.buttons["stop_server"].invoke()
        self.wait_for(lambda: not panel.remote_running)
        self.session.rpc.assert_any_call("stop_worker", room=room)

    def test_join_exports_into_new_directory_and_blocks_traversal(self):
        room = self.connect_and_create()
        self.panel.room.set(room)
        self.panel.buttons["join"].invoke()
        self.wait_for(lambda: not self.panel.busy)
        self.session.rpc.assert_any_call("status", room=room)
        with patch("cloud_ui.filedialog.askdirectory", return_value=self.temp.name):
            self.panel.buttons["export"].invoke()
            self.wait_for(lambda: self.panel.notice.get().startswith("已导出"))
        first = export_project(self.temp.name, {"docs/1.md": "yes"})
        second = export_project(self.temp.name, {"docs/1.md": "updated"})
        self.assertNotEqual(first, second)
        self.assertEqual((first / "docs/1.md").read_text("utf-8"), "yes")
        with self.assertRaises(ValueError):
            export_project(self.temp.name, {"../escape": "x"})
