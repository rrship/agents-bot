import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import desktop_style as ui
from opc_skills import LEAD, ROLE_NAMES, role_info
from opc_ui import OPCPanel


class OPCPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        ui.configure(self.root)
        self.root.geometry("880x630")
        sidebar = ttk.Frame(self.root, width=176)
        sidebar.pack(side="left", fill="y")
        pages = ttk.Notebook(self.root, style="Pages.TNotebook")
        pages.pack(side="left", fill="both", expand=True)
        software = ttk.Frame(pages)
        pages.add(software)
        software.app_name = tk.StringVar(self.root)
        software.codex_mode = tk.StringVar(self.root, "codex-native")
        software.harness_mode = tk.StringVar(self.root, "baton")
        profiles = Mock()
        profiles.list_profiles.return_value = [
            dict(id="project-key", name="工作账号", provider="GPT", model="project-model",
                 key_env="ENV_KEY_FOR_TEST")]
        self.desk = SimpleNamespace(account_store=profiles, tabs=pages, software=software,
                                    show_accounts=Mock(), use_opc_team=Mock())
        self.panel = OPCPanel(pages, self.desk)
        pages.add(self.panel)
        pages.select(self.panel)
        self.panel.refresh()
        self.root.update()

    def select(self, box, kind, target):
        box.set(self.panel.options[kind, target])
        box.event_generate("<<ComboboxSelected>>")
        self.root.update()

    def test_bulk_account_individual_mcp_and_no_secrets(self):
        panel = self.panel
        with self.assertRaisesRegex(ValueError, LEAD):
            panel.selection()
        self.select(panel.bulk_box, "api", "project-key")
        panel.bulk_button.invoke()
        self.select(panel.boxes["前端设计师"], "mcp", "Claude Desktop")
        panel.checks["测试工程师"].invoke()
        self.assertEqual(panel.selection(), {
            LEAD: {"kind": "api", "target": "project-key"},
            "前端设计师": {"kind": "mcp", "target": "Claude Desktop"},
            "后端开发员": {"kind": "api", "target": "project-key"},
        })
        panel.start_button.invoke()
        self.desk.use_opc_team.assert_called_once_with()
        for role in ("前端设计师", "后端开发员"):
            panel.checks[role].invoke()
        self.assertTrue(panel.checks[LEAD].instate(["disabled"]))
        with self.assertRaisesRegex(ValueError, "执行员工"):
            panel.selection()

    def test_reopen_and_rebind_deleted_account_without_changing_staff(self):
        panel = self.panel
        routes = {LEAD: {"kind": "api", "target": "removed-id"},
                  "后端开发员": {"kind": "mcp", "target": "ZCode"}}
        panel.restore(routes)
        panel.set_locked(False, True)
        self.assertTrue(all(box.instate(["disabled"]) for box in panel.checks.values()))
        self.assertTrue(panel.boxes["前端设计师"].instate(["disabled"]))
        self.assertIn("失效", panel.boxes[LEAD].get())
        with self.assertRaisesRegex(ValueError, LEAD):
            panel.selection()
        self.select(panel.boxes[LEAD], "api", "project-key")
        before = panel.selection()
        self.assertEqual(before[LEAD]["target"], "project-key")
        self.desk.account_store.list_profiles.return_value[0]["name"] = "改名后的账号"
        panel.refresh()
        self.assertIn("改名后的账号", panel.boxes[LEAD].get())
        self.assertEqual(panel.selection(), before)
        panel.set_locked(True, True)
        self.assertTrue(panel.start_button.instate(["disabled"]))
        self.assertTrue(panel.bulk_button.instate(["disabled"]))
        self.assertTrue(all(box.instate(["disabled"]) for box in panel.boxes.values()))

    def test_navigate_to_matching_mcp_connection_and_account_settings(self):
        panel = self.panel
        self.select(panel.boxes[LEAD], "mcp", "Codex")
        panel.connect_button.invoke()
        self.assertEqual(self.desk.tabs.select(), str(self.desk.software))
        self.assertEqual(self.desk.software.app_name.get(), "Codex")
        self.assertEqual(self.desk.software.codex_mode.get(), "codex-native")
        panel.accounts_button.invoke()
        self.desk.show_accounts.assert_called_once_with()

    def test_handbooks_and_controls_fit_smallest_desktop(self):
        panel = self.panel
        left, top = panel.winfo_rootx(), panel.winfo_rooty()
        right = left + panel.winfo_width()
        bottom = top + panel.winfo_height()
        for role in ROLE_NAMES:
            panel.details_buttons[role].invoke()
            self.root.update()
            body = panel.handbook.get("1.0", "end")
            info = role_info(role)
            self.assertIn(info["instructions"], body)
            self.assertIn(info["skills"][0], body)
        self.assertEqual(panel.accounts_button.cget("text"), "配置 API 账号")
        self.assertEqual(panel.connect_button.cget("text"), "连接本地软件")
        for widget in (panel.bulk_button, panel.start_button, panel.connection_menu,
                       panel.handbook, *panel.checks.values(), *panel.boxes.values(),
                       *panel.details_buttons.values()):
            self.assertTrue(widget.winfo_ismapped(), str(widget))
            self.assertGreaterEqual(widget.winfo_rootx(), left)
            self.assertGreaterEqual(widget.winfo_rooty(), top)
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
