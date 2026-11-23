from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

from account_store import AccountStore


class BotProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = AccountStore(Path(folder.name) / "accounts.db")
        self.account = self.store.save_profile("项目", "GPT", "model-a", key_env="PROJECT_KEY")

    def test_opens_existing_database_that_contains_only_account_profiles(self):
        with closing(sqlite3.connect(self.store.path)) as conn:
            with conn:
                conn.execute("DROP TABLE bots")
        upgraded = AccountStore(self.store.path)
        self.assertEqual(upgraded.list_bots(), [])
        uid = upgraded.save_bot("助手", "概括原文", self.account)
        self.assertEqual(self.store.list_bots()[0]["id"], uid)
        self.assertEqual(self.store.list_profiles()[0]["id"], self.account)

    def test_bots_keep_individual_roles_after_restart_and_follow_account_edits(self):
        one = self.store.save_bot("  文档  ", "梳理接口。\n保留示例。", self.account)
        two = self.store.save_bot("测试", "检查接口。", self.account)
        self.assertNotEqual(one, two)
        self.store.save_profile("工作", "GPT", "model-b", profile_id=self.account)
        again = AccountStore(self.store.path)
        listing = {row["id"]: row for row in again.list_bots()}
        self.assertEqual(listing[one], dict(id=one, name="文档", instructions="梳理接口。\n保留示例。",
                                           profile_id=self.account, profile_name="工作", provider="GPT", model="model-b"))
        self.assertEqual(listing[two]["instructions"], "检查接口。")
        self.assertNotIn("PROJECT_KEY", repr(listing))

    def test_rename_rebind_and_delete_preserve_other_bots_and_accounts(self):
        uid = self.store.save_bot("选题", "", self.account)
        other = self.store.save_profile("第二个账号", "Kimi", "model-x", key_env="OTHER_KEY")
        with self.assertRaisesRegex(ValueError, "Bot"):
            self.store.delete_profile(self.account)
        self.assertEqual(self.store.save_bot("审阅", "检查来源", other, bot_id=uid), uid)
        self.store.delete_profile(self.account)
        self.assertEqual(self.store.list_bots()[0]["provider"], "Kimi")
        self.store.delete_bot(uid)
        self.assertEqual(self.store.list_bots(), [])
        self.assertEqual(self.store.list_profiles()[0]["id"], other)
        with self.assertRaises(ValueError):
            self.store.save_bot("恢复", "", other, bot_id=uid)
        with self.assertRaises(ValueError):
            self.store.delete_bot(uid)

    def test_invalid_updates_leave_the_bot_intact(self):
        uid = self.store.save_bot("主笔", "写作", self.account)
        snapshot = self.store.list_bots()
        for name, prompt, profile_id in [("", "", self.account), ("多\n行", "", self.account),
                                         ("新名称", "文"*4001, self.account), ("新名称", None, self.account),
                                         ("新名称", "", "不存在")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.save_bot(name, prompt, profile_id, bot_id=uid)
        self.assertEqual(self.store.list_bots(), snapshot)

    def test_simultaneous_casefold_equivalent_names_create_one_record(self):
        gate = Barrier(2)

        def create(name):
            gate.wait(timeout=5)
            try:
                return self.store.save_bot(name, "", self.account)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(create, ["Café", "CAFE\N{COMBINING ACUTE ACCENT}"]))
        self.assertEqual(sum(key is not None for key in ids), 1)
        self.assertEqual(len(self.store.list_bots()), 1)
