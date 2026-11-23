import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from software_bridge import BridgeError, SOFTWARE_NAMES
from software_ui import SoftwarePanel, config_text, validated_reply


class SoftwareUI(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        tabs = ttk.Notebook(self.root)
        tasks = ttk.Frame(tabs)
        tabs.add(tasks, text="任务")
        self.desk = SimpleNamespace(tabs=tabs, tasks=tasks, team_controls=[], running=False, job=None,
                                    bridge_path=Path("fixture-not-created.db"), bridge=Mock(),
                                    team_mode=tk.StringVar(self.root, "peer"), leader=tk.StringVar(self.root),
                                    member_vars={name: tk.BooleanVar(self.root, name == "GPT")
                                                 for name in ("GPT", *SOFTWARE_NAMES)})
        self.panel = SoftwarePanel(tabs, self.desk)
        tabs.add(self.panel)
        tabs.select(self.panel)

    def test_quick_team_sets_only_pair_and_leader(self):
        self.panel.preset.invoke()
        self.assertEqual([k for k, v in self.desk.member_vars.items() if v.get()], ["Codex", "Claude Desktop"])
        self.assertEqual(self.desk.team_mode.get(), "leader")
        self.assertEqual(self.desk.leader.get(), "Codex")
        self.assertEqual(self.desk.tabs.select(), str(self.desk.tasks))
        self.desk.bridge.assert_not_called()

    def test_selection_routes_native_harness_or_shared_queue(self):
        self.panel.checks["DeepSeek Harness"].invoke()
        self.assertEqual(self.panel.app_name.get(), "DeepSeek Harness")
        self.assertTrue(self.desk.member_vars["DeepSeek Harness"].get())
        self.assertEqual(self.panel.config("DeepSeek Harness")["transport"], "baton")
        self.desk.bridge.assert_not_called()
        self.assertFalse(self.panel.manual.winfo_manager())
        self.panel.harness_mode.set("mcp")
        self.assertEqual(self.panel.config("DeepSeek Harness")["bridge"], self.desk.bridge.return_value)
        self.assertTrue(self.panel.manual.winfo_manager())

    def test_codex_independent_session_does_not_claim_mcp_tasks(self):
        self.panel.codex_mode.set("codex-native")
        self.assertEqual(self.panel.config("Codex"), {"mode": "software", "transport": "codex-native"})
        self.desk.bridge.assert_not_called()
        self.assertFalse(self.panel.manual.winfo_manager())
        self.assertFalse(self.panel.install_button.winfo_manager())
        self.panel.codex_mode.set("mcp")
        self.assertTrue(self.panel.install_button.winfo_manager())
        self.assertIs(self.panel.config("Codex")["bridge"], self.desk.bridge.return_value)

    def test_config_formats_and_explicit_install(self):
        for name in SOFTWARE_NAMES:
            text = config_text(name, self.desk.bridge_path)
            if name == "Codex":
                entry = tomllib.loads(text)["mcp_servers"]["agents_bot"]
            elif name == "DeepSeek Harness":
                entry = json.loads(text)
                self.assertEqual(entry["transport"], "stdio")
            else:
                entry = json.loads(text)["mcpServers"]["agents-bot"]
            self.assertIn(str(self.desk.bridge_path.resolve()), entry["args"])
            self.assertIn(name, entry["args"])
            self.assertTrue(Path(entry["command"]).is_absolute())
        with patch("software_setup.install", return_value=Path("fixture.toml")) as install:
            self.panel.install_button.invoke()
            self.assertEqual(install.call_args.args[0], "Codex")
            self.assertIn("fixture.toml", self.panel.status.get())

    def test_copy_and_submit_stays_with_its_member_and_allows_retry(self):
        bridge = self.desk.bridge.return_value
        bridge.claim.return_value = {"task_id": "fixture", "lease": "lease", "prompt": "fixture"}
        with patch.object(self.panel, "copy") as copy:
            self.panel.claim_button.invoke()
            copy.assert_called_once_with("fixture")
        self.panel.app_name.set("ZCode")
        self.assertTrue(self.panel.submit_button.instate(["disabled"]))
        self.panel.app_name.set("Codex")
        with patch.object(self.panel, "clipboard_get", return_value="no JSON"):
            self.panel.submit_button.invoke()
        self.assertIn("JSON", self.panel.status.get())
        bridge.complete.assert_not_called()
        text = '{"message":"完成"}'
        with patch.object(self.panel, "clipboard_get", return_value=text):
            self.panel.submit_button.invoke()
        app, client, task_id, lease, reply = bridge.complete.call_args.args
        self.assertEqual((app, client, task_id, lease), ("Codex", self.panel.client, "fixture", "lease"))
        self.assertEqual(json.loads(reply), json.loads(text))
        self.assertNotIn("Codex", self.panel.tickets)

    def test_stale_completion_clears_only_current_ticket(self):
        bridge = self.desk.bridge.return_value
        bridge.claim.return_value = {"task_id": "old", "lease": "lease", "prompt": "fixture"}
        with patch.object(self.panel, "copy"):
            self.panel.claim_button.invoke()
        bridge.complete.side_effect = BridgeError("任务已撤回")
        with patch.object(self.panel, "clipboard_get", return_value='{"message":"旧回包"}'):
            self.panel.submit_button.invoke()
        self.assertTrue(self.panel.submit_button.instate(["disabled"]))
        self.assertIn("重新领取", self.panel.status.get())

    def test_manual_reply_checks_worker_assignments_and_review(self):
        ctx = json.dumps({"workers": ["ZCode"]})
        with self.assertRaisesRegex(ValueError, "assignments"):
            validated_reply("Codex", "你是Codex。阶段=派单。\n" + ctx, '{"message":"做完"}')
        text = validated_reply("Codex", "阶段=派单。\n" + ctx,
                               '```json\n{"message":"拆分任务","assignments":{"ZCode":"编写说明"}}\n```')
        self.assertIn("ZCode", json.loads(text)["assignments"])
        with self.assertRaisesRegex(ValueError, "checklist"):
            validated_reply("Codex", "阶段=验收复核。", '{"message":"通过","approved":true}')
