import tkinter as tk
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
from unittest.mock import patch

from app import Desk
from team_modes import STANDARD, OPC


class DesktopLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = patch.dict(os.environ, LOCALAPPDATA=self.temp.name, BOT_TEST_KEY="sample-only")
        folder.start()
        self.addCleanup(folder.stop)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        saved_address = patch("app.saved_address", return_value=None)
        saved_address.start()
        self.addCleanup(saved_address.stop)
        self.desk = Desk(self.root)
        self.addCleanup(self.cancel_timers)
        self.root.geometry("880x630")
        self.root.update()

    def cancel_timers(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)

    def test_sidebar_and_team_shortcut_select_the_same_page(self):
        desk = self.desk
        desk.nav_buttons[str(desk.cloud)].invoke()
        self.root.update_idletasks()
        self.assertEqual(desk.tabs.select(), str(desk.cloud))
        desk.tabs.select(desk.tasks)
        desk.team_link.invoke()
        self.root.update_idletasks()
        self.assertEqual(desk.tabs.select(), str(desk.team))

    def test_command_fields_keep_text_when_folded_and_fit_at_minimum_size(self):
        desk = self.desk
        self.assertFalse(desk.check_fields.winfo_ismapped())
        desk.checks_button.invoke()
        self.root.update_idletasks()
        self.assertTrue(desk.check_fields.winfo_ismapped())
        for mode in (STANDARD, OPC):
            with self.subTest(mode=mode):
                desk.mode_buttons[mode].invoke()
                if mode == OPC:
                    self.root.update()
                    desk.opc.restore({role: {"kind": "mcp", "target": "Codex"}
                                      for role in ("项目经理", "测试工程师")})
                    desk.opc.start_button.invoke()
                self.root.update()
                self.assertEqual(desk.tabs.select(), str(desk.tasks))
                self.assertEqual(desk.task_mode.get(), mode)
                for widget in (desk.goal, desk.tests, desk.smoke, desk.start_button):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreater(widget.winfo_height(), 14)
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                         self.root.winfo_rooty() + self.root.winfo_height())
                for widget in (desk.goal, desk.tests, desk.smoke):
                    line = widget.dlineinfo("1.0")
                    self.assertIsNotNone(line)
                    self.assertLessEqual(line[1] + line[3], widget.winfo_height())
        desk.tests.insert("1.0", '["python", "test.py"]')
        desk.checks_button.invoke()
        self.assertEqual(desk.tests.get("1.0", "end-1c"), '["python", "test.py"]')

    def test_empty_feed_and_tagged_activity_keep_original_mobile_event(self):
        desk = self.desk
        self.assertFalse(desk.chat_card.winfo_ismapped())
        line = "GPT → Gemini [分工说明] 请检查接口。\n保留第二行。"
        desk._append(line)
        self.root.update_idletasks()
        self.assertTrue(desk.chat_card.winfo_ismapped())
        self.assertFalse(desk.empty_feed.winfo_manager())
        self.assertEqual(desk.activity[-1], line)
        self.assertTrue(desk.log.tag_ranges("speaker"))
        self.assertIn("保留第二行。", desk.log.get("1.0", "end"))
        desk._clear_log()
        self.root.update_idletasks()
        self.assertFalse(desk.chat_card.winfo_ismapped())
        self.assertTrue(desk.empty_feed.winfo_manager())

    def test_task_menu_and_primary_controls_follow_job_state(self):
        desk = self.desk
        self.assertTrue(desk.task_menu.winfo_ismapped())
        self.assertTrue(desk.reopen.instate(["!disabled"]))
        self.assertTrue(desk.cancel_btn.instate(["disabled"]))
        desk.job = SimpleNamespace(state={"status": "已暂停"}, path=self.temp.name)
        desk.running = desk.chat_focus = True
        desk._controls()
        self.root.update_idletasks()
        self.assertTrue(desk.pause.winfo_ismapped())
        self.assertFalse(desk.settings_card.winfo_ismapped())
        self.assertTrue(desk.reopen.instate(["disabled"]))
        desk.checks_button.invoke()
        self.root.update_idletasks()
        self.assertTrue(desk.tests.winfo_ismapped())
        self.assertTrue(desk.tests.cget("state") == "disabled")
        desk.running = False
        desk._controls()
        self.root.update_idletasks()
        self.assertTrue(desk.resume.winfo_ismapped())
        self.assertFalse(desk.pause.winfo_ismapped())
        self.assertTrue(desk.new_button.instate(["!disabled"]))
        desk.job.state["status"] = "完成"
        desk._controls()
        self.assertFalse(desk.resume.winfo_manager())
        self.assertTrue(desk.cancel_btn.instate(["disabled"]))
        desk.new_button.invoke()
        self.root.update_idletasks()
        self.assertTrue(desk.start_button.winfo_ismapped())
        self.assertFalse(desk.chat_card.winfo_ismapped())

    def test_long_task_can_scroll_after_starting_with_empty_editor(self):
        goal = self.desk.goal
        goal.insert("1.0", "项目需求\n" * 95)
        self.root.update()
        self.assertTrue(goal.vbar.winfo_ismapped())
        goal.yview_moveto(1)
        self.assertEqual(goal.yview()[1], 1.0)
        goal.delete("1.0", "end")
        self.root.update()
        self.assertFalse(goal.vbar.winfo_ismapped())

    def test_create_from_sidebar_edit_and_use_the_bot_in_a_team(self):
        desk = self.desk
        desk.account_store.save_profile("本地档案", "Kimi", "demo-model", key_env="BOT_TEST_KEY")
        desk.bots.new.invoke()
        editor = desk.bots.editor
        editor.template.set("研究助手")
        editor._use_template()
        editor._save()
        bot = desk.lite.active_bot
        self.assertEqual(bot["name"], "研究助手")
        desk.lite.bind_bot.invoke()
        self.assertEqual(desk._config("内置 Agent")["instructions"], bot["instructions"])
        tile, select, settings = desk.bots.entry_widgets[bot["id"]]
        settings.invoke()
        editor = desk.bots.editor
        editor.name.set("文献整理")
        editor.instructions.delete("1.0", "end")
        editor.instructions.insert("1.0", "记录证据来源")
        editor._save()
        self.assertEqual(desk._config("内置 Agent")["instructions"], "记录证据来源")
        desk.show_accounts()
        desk.bots.entry_widgets[bot["id"]][1].invoke()
        self.assertEqual(desk.lite.active_bot["name"], "文献整理")
        desk.bots.entry_widgets[bot["id"]][2].invoke()
        with patch("bot_sidebar.messagebox.askyesno", return_value=True):
            desk.bots.editor._delete()
        self.assertIsNone(desk.lite.active_bot)
        self.assertNotIn("内置 Agent", desk.profile_bindings)
