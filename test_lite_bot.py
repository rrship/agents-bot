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
import bot_memory
from cloud_daemon import validate_configs
from cloud_ui import server_configs
from lite_agent import run_task, team_call
from lite_ui import LitePanel, ProfileEditor
from providers import BUILTIN


class BotConversationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        desk = self.desk = Mock()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, MEMORY_TEST_KEY="fixture-token")
        env.start()
        self.addCleanup(env.stop)
        self.store = desk.account_store = AccountStore(Path(temp.name) / "accounts.db")
        self.first, self.shared = (self.store.save_profile(p, "GPT", p, key_env="MEMORY_TEST_KEY")
                                   for p in ("first", "shared"))
        self.panel = LitePanel(self.root, desk)
        self.addCleanup(self.panel.close)
        self.panel.refresh_profiles(self.first)
        self.store.save_bot("校对", "指出术语不一致", self.shared)
        self.bot = self.store.list_bots()[0]

    def test_two_bots_and_account_chat_remain_independent(self):
        p = self.panel
        self.assertTrue(p.open_bot(self.bot))
        self.assertEqual(p.selected_id, self.shared)
        self.assertEqual(p.current_config()["instructions"], self.bot["instructions"])
        p._session()["drafts"]["report.txt"] = "private to bot a"
        p.prompt.insert("1.0", "unsent")
        p.refresh_profiles(self.first)
        self.assertEqual(p.selected_id, self.shared)
        p.bind_bot.invoke()
        self.desk.apply_profile.assert_called_once_with(self.shared, builtin=True, bot_id=self.bot["id"])
        uid = self.store.save_bot("文案", "", self.shared)
        second = dict(self.bot, id=uid, name="文案")
        self.assertTrue(p.open_bot(second))
        self.assertEqual(p._session()["drafts"], {})
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "")
        self.assertTrue(p.open_accounts())
        self.assertEqual(p.selected_id, self.first)
        self.assertNotIn("instructions", p.current_config())
        p.refresh_profiles(self.shared)
        self.assertEqual(p._session()["drafts"], {})
        self.assertTrue(p.open_bot(self.bot))
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "unsent")
        self.assertEqual(p._session()["drafts"]["report.txt"], "private to bot a")
        self.assertTrue(p.forget_bot(self.bot["id"]))
        self.assertIsNone(p.active_bot)
        self.assertNotIn(("bot", self.bot["id"]), p.sessions)

    def test_running_turn_keeps_its_target(self):
        p = self.panel
        p.open_bot(self.bot)
        p.stop = threading.Event()
        p._session()["pending"] = "review"
        self.assertFalse(p.open_bot(dict(self.bot, id="b")))
        self.assertFalse(p.open_accounts())
        self.assertFalse(p.forget_bot(self.bot["id"]))
        p.events.put((p.turn, "result", dict(message="reviewed", files={})))
        p.poll()
        self.assertEqual(p._session()["history"][-1]["content"], "reviewed")
        self.assertTrue(p.open_accounts())

    def test_rebound_bot_does_not_share_old_material_with_new_account(self):
        p = self.panel
        p.open_bot(self.bot)
        p._session()["files"]["notes.txt"] = "confidential"
        self.store.save_bot(self.bot["name"], "", self.first, self.bot["id"])
        self.assertTrue(p.open_bot(dict(self.bot, profile_id=self.first)))
        self.assertEqual(p.current_config()["model"], "first")
        self.assertFalse(p._session()["files"])
        with patch.object(self.store, "list_profiles", side_effect=OSError("busy")):
            p.open_bot(self.bot)
            with self.assertRaises(ValueError):
                p.current_config()

    def test_reopen_persists_text_and_memory_but_only_sends_them_to_its_bot(self):
        p = self.panel
        p.open_bot(self.bot)
        args = p._chat_args()
        bot_memory.set_memory(*args, "", "用中文写总结")
        p._session()["drafts"]["note.txt"] = "private file contents"
        p.stop = threading.Event()
        p._session()["pending"] = "上一次的问题"
        p.events.put((p.turn, "result", dict(message="上一次的答复", files={})))
        p.poll()
        reopened = LitePanel(self.root, self.desk)
        self.addCleanup(reopened.close)
        reopened.open_bot(self.bot)
        self.assertIn("上一次的问题", reopened.transcript.get("1.0", "end"))
        self.assertEqual(reopened._session()["memory"], "用中文写总结")
        self.assertFalse(reopened._session()["drafts"])
        observed = threading.Event()
        def provider(_name, config, prompt):
            data = json.loads(prompt.splitlines()[-1])
            self.assertEqual(data["user_saved_memory"], "用中文写总结")
            self.assertIn("上一次的答复", str(data["history"]))
            self.assertNotIn("private file contents", prompt)
            self.assertNotIn("fixture-token", prompt)
            self.assertNotIn("bot_memory", config)
            observed.set()
            return '{"message":"收到"}'
        with patch("providers.call", side_effect=provider):
            reopened.prompt.insert("1.0", "请总结")
            reopened.actions["send"].invoke()
            self.assertTrue(observed.wait(2))
        reopened.cancel()
        with patch("lite_ui.messagebox.askyesno", return_value=True):
            reopened.clear()
        _, memory, history = bot_memory.load(*args[:4])
        self.assertEqual(history, [])
        self.assertEqual(memory, "用中文写总结")
        with self.assertRaisesRegex(ValueError, "清空"):
            bot_memory.append_turn(*args, "late", "stale")
        self.store.save_bot(self.bot["name"], "", self.first, self.bot["id"])
        self.store.save_bot(self.bot["name"], "", self.shared, self.bot["id"])
        self.assertEqual(bot_memory.load(*args[:4])[1:], ("", []))

    def test_clear_in_other_window_drops_unsent_context_and_late_turn(self):
        p = self.panel
        p.open_bot(self.bot)
        p._session()["files"]["draft.txt"] = "旧项目"
        p.prompt.insert("1.0", "仅供之前的会话")
        args = p._chat_args()
        bot_memory.set_memory(*args, "", "只处理中文稿件")
        bot_memory.clear_history(*args)
        with patch("lite_ui.messagebox.showerror") as error, patch("lite_ui.run_task") as run:
            p.submit()
        error.assert_called_once()
        run.assert_not_called()
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "")
        self.assertEqual(p._session()["files"], {})
        self.assertEqual(p._session()["memory"], "只处理中文稿件")

    def test_other_window_clears_chat_before_reply_and_drafts_arrive(self):
        first = self.panel
        first.open_bot(self.bot)
        window = tk.Toplevel(self.root)
        second_desk = Mock(account_store=AccountStore(self.store.path))
        second = LitePanel(window, second_desk)
        self.addCleanup(second.close)
        second.open_bot(self.bot)
        first._session()["files"]["old.txt"] = "old attachment"
        first._session()["drafts"]["prior.txt"] = "old draft"
        first.prompt.insert("1.0", "old question")
        entered, finish = threading.Event(), threading.Event()
        def reply(*args, **kwargs):
            entered.set()
            finish.wait(3)
            return {"message": "late answer", "files": {"late.txt": "private draft"}}
        with patch("lite_ui.run_task", side_effect=reply):
            first.submit()
            try:
                self.assertTrue(entered.wait(2))
                with patch("lite_ui.messagebox.askyesno", return_value=True):
                    second.clear()
                bot_memory.append_turn(*second._chat_args(), "new question", "new answer")
            finally:
                finish.set()
            deadline = time.monotonic() + 3
            while first.stop is not None and time.monotonic() < deadline:
                first.poll()
                time.sleep(.01)
        self.assertIsNone(first.stop)
        self.assertEqual(first._session()["history"], bot_memory.load(*second._chat_args()[:4])[2])
        self.assertIn("new answer", first.transcript.get("1.0", "end"))
        self.assertNotIn("late answer", first.transcript.get("1.0", "end"))
        self.assertEqual(first._session()["files"], {})
        self.assertEqual(first._session()["drafts"], {})
        self.assertEqual(first.prompt.get("1.0", "end-1c"), "")
        self.assertTrue(first.actions["export"].instate(["disabled"]))

    def test_rebound_bot_discards_completed_turn_but_io_failure_keeps_request(self):
        p = self.panel
        p.open_bot(self.bot)
        p.prompt.insert("1.0", "原账号的问题")
        p._session()["files"]["source.md"] = "私有资料"
        def completed_turn():
            p.stop = threading.Event()
            p._session()["pending"] = "原账号的问题"
            p.events.put((p.turn, "result", dict(message="原账号的答复", files={"result.md": "文件"})))
            p.poll()
        with patch.object(bot_memory, "append_turn", side_effect=OSError("测试磁盘错误")):
            completed_turn()
        self.assertIn("未保存", p.hint.get())
        self.assertEqual(p._session()["files"], {"source.md": "私有资料"})
        self.assertEqual(p._session()["history"], [])
        self.assertFalse(p._session()["drafts"])
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "原账号的问题")
        self.store.save_bot(self.bot["name"], "", self.first, self.bot["id"])
        completed_turn()
        self.assertFalse(p._session()["files"])
        self.assertFalse(p._session()["drafts"])
        self.assertFalse(p._session()["history"])
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "")
        self.assertTrue(p.actions["send"].instate(["disabled"]))

    def test_sidebar_previews_and_provider_change_confirmation(self):
        self.assertEqual(bot_memory.previews(self.store), {})
        self.panel.open_bot(self.bot)
        args = self.panel._chat_args()
        bot_memory.set_memory(*args, "", "按职责处理")
        bot_memory.append_turn(*args, "private question", "old reply")
        bot_memory.append_turn(*args, "new question", "第一行\n    第二行   " + "答" * 120)
        summary = bot_memory.previews(self.store)
        self.assertEqual(summary, {self.bot["id"]: ("第一行 第二行 " + "答" * 120)[:70]})
        self.panel.open_accounts()
        profile = next(row for row in self.panel.profiles if row["id"] == self.shared)
        editor = ProfileEditor(self.panel, profile)
        editor.fields["provider"].set("Gemini")
        with patch("lite_ui.messagebox.askyesno", return_value=False):
            editor.save()
        self.assertEqual(bot_memory.previews(self.store), summary)
        with patch("lite_ui.messagebox.askyesno", return_value=True):
            editor.save()
        self.desk.refresh_bot_previews.assert_called_once()
        self.assertEqual(bot_memory.previews(self.store), {})
        self.assertEqual(bot_memory.load(self.store, self.bot["id"], self.shared, "Gemini")[1:], ("", []))


class BotPromptTests(unittest.TestCase):
    def test_instructions_reach_individual_and_team_models_including_ssh(self):
        text = "把接口约束列为验收项"
        config = dict(provider="GPT", model="test", key="local-test", mode="api", instructions=text)
        cloud = validate_configs(server_configs({BUILTIN: config}), [BUILTIN])[BUILTIN]
        self.assertEqual(cloud, config)
        with patch("providers.call", return_value='{"message":"ok"}') as api:
            self.assertEqual(run_task("GPT", config, "review")["message"], "ok")
            self.assertEqual(json.loads(team_call(cloud, "团队复核\n{}"))["message"], "ok")
        for call in api.call_args_list:
            self.assertIn(text, call.args[2])
            self.assertNotIn("local-test", call.args[2])
            self.assertNotIn("instructions", call.args[1])

    def test_cloud_bounds_and_non_builtin_members(self):
        cfg = dict(provider="GPT", model="test", mode="api", key="fixture", instructions="x"*4001)
        with self.assertRaisesRegex(ValueError, "Bot"):
            validate_configs({BUILTIN:cfg}, [BUILTIN])
        self.assertNotIn("instructions", server_configs({"GPT":cfg})["GPT"])
