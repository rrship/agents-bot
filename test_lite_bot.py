import json
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from cloud_daemon import validate_configs
from cloud_ui import server_configs
from lite_agent import run_task, team_call
from lite_ui import LitePanel
from providers import BUILTIN


class BotConversationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        desk = self.desk = Mock()
        profiles = [{"id": p, "name": p, "provider": "GPT", "model": p} for p in ("first", "shared")]
        desk.account_store.list_profiles.return_value = profiles
        desk.account_store.resolve.side_effect = lambda uid: dict(provider="GPT", model=uid, mode="api", key="fixture")
        self.panel = LitePanel(self.root, desk)
        self.addCleanup(self.panel.close)
        self.panel.refresh_profiles("first")
        self.bot = dict(id="a", name="校对", instructions="指出术语不一致", profile_id="shared")

    def test_two_bots_and_account_chat_remain_independent(self):
        p = self.panel
        self.assertTrue(p.open_bot(self.bot))
        self.assertEqual(p.selected_id, "shared")
        self.assertEqual(p.current_config()["instructions"], self.bot["instructions"])
        p._session()["drafts"]["report.txt"] = "private to bot a"
        p.prompt.insert("1.0", "unsent")
        p.refresh_profiles("first")
        self.assertEqual(p.selected_id, "shared")
        p.bind_bot.invoke()
        self.desk.apply_profile.assert_called_once_with("shared", builtin=True, bot_id="a")
        second = dict(self.bot, id="b", name="文案")
        self.assertTrue(p.open_bot(second))
        self.assertEqual(p._session()["drafts"], {})
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "")
        self.assertTrue(p.open_accounts())
        self.assertEqual(p.selected_id, "first")
        self.assertNotIn("instructions", p.current_config())
        p.refresh_profiles("shared")
        self.assertEqual(p._session()["drafts"], {})
        self.assertTrue(p.open_bot(self.bot))
        self.assertEqual(p.prompt.get("1.0", "end-1c"), "unsent")
        self.assertEqual(p._session()["drafts"]["report.txt"], "private to bot a")
        self.assertTrue(p.forget_bot("a"))
        self.assertIsNone(p.active_bot)
        self.assertNotIn(("bot", "a"), p.sessions)

    def test_running_turn_keeps_its_target(self):
        p = self.panel
        p.open_bot(self.bot)
        p.stop = threading.Event()
        p._session()["pending"] = "review"
        self.assertFalse(p.open_bot(dict(self.bot, id="b")))
        self.assertFalse(p.open_accounts())
        self.assertFalse(p.forget_bot("a"))
        p.events.put((p.turn, "result", dict(message="reviewed", files={})))
        p.poll()
        self.assertEqual(p._session()["history"][-1]["content"], "reviewed")
        self.assertTrue(p.open_accounts())

    def test_rebound_bot_does_not_share_old_material_with_new_account(self):
        p = self.panel
        p.open_bot(self.bot)
        p._session()["files"]["notes.txt"] = "confidential"
        self.assertTrue(p.open_bot(dict(self.bot, profile_id="first")))
        self.assertEqual(p.current_config()["model"], "first")
        self.assertFalse(p._session()["files"])
        self.desk.account_store.list_profiles.side_effect = OSError("busy")
        p.open_bot(self.bot)
        with self.assertRaises(ValueError):
            p.current_config()


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
