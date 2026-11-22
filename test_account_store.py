import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from account_store import AccountStore


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = AccountStore(self.root / "user" / "accounts.db")
        self.alpha = dict(name="Work", provider="GPT", model="test-model", key_env="BOT_TEST_KEY")

    def import_json(self, data):
        file = self.root / "profiles.json"
        file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
        return self.store.import_file(file)

    def test_environment_profile_survives_restart_and_can_be_renamed_or_deleted(self):
        profile = self.store.save_profile(**self.alpha)
        reopened = AccountStore(self.store.path)
        self.assertEqual(reopened.list_profiles(), [{**self.alpha, "id": profile}])
        with patch.dict(os.environ, {"BOT_TEST_KEY": "fixture-A"}):
            self.assertEqual(reopened.resolve(profile), dict(mode="api", provider="GPT",
                                                             model="test-model", key="fixture-A"))
        with patch.dict(os.environ, {"BOT_TEST_KEY": ""}), self.assertRaises(ValueError):
            reopened.resolve(profile)
        self.store.save_profile("工作", "GPT", "new-model", profile_id=profile)
        self.assertEqual(reopened.list_profiles()[0]["key_env"], "BOT_TEST_KEY")
        with self.assertRaises(ValueError):
            reopened.save_profile("工作", "Claude", "model", profile_id=profile)
        reopened.delete_profile(profile)
        self.assertEqual(self.store.list_profiles(), [])
        with self.assertRaises(ValueError):
            self.store.resolve(profile)

    def test_import_batch_is_atomic_when_existing_or_duplicate_names_collide(self):
        self.store.save_profile(**self.alpha)
        beta = dict(self.alpha, name="Home")
        with self.assertRaisesRegex(ValueError, "第 2 项"):
            self.import_json(dict(version=1, accounts=[beta, dict(self.alpha, name="work")]))
        self.assertEqual([p["name"] for p in self.store.list_profiles()], ["Work"])
        ids = self.import_json(dict(version=1, accounts=[beta, dict(self.alpha, provider="Claude")]))
        self.assertEqual(len(set(ids)), 2)
        solo = self.import_json(dict(self.alpha, provider="Gemini", model="models/demo"))
        self.assertEqual(len(solo), 1)
        self.assertEqual(next(p["model"] for p in self.store.list_profiles() if p["id"] == solo[0]), "demo")

    def test_invalid_imports_are_rejected_without_echoing_data(self):
        secret = "user-sensitive-fixture"
        for invalid in [dict(self.alpha, cookie=secret), dict(self.alpha, api_key=secret),
                        dict(self.alpha, model={"token": secret}), {"refresh_token": secret},
                        dict(self.alpha, key_env="A=" + secret), dict(version=True, accounts=[self.alpha])]:
            with self.assertRaises(ValueError) as caught:
                self.import_json(invalid)
            self.assertNotIn(secret, str(caught.exception))
        path = self.root / "invalid.json"
        path.write_text('{"name":"x","name":"duplicate"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_file(path)
        self.assertEqual(self.store.list_profiles(), [])

    @unittest.skipUnless(os.name == "nt", "Windows user DPAPI")
    def test_real_dpapi_ciphertext_is_reusable_but_never_exposed_as_metadata(self):
        key = "fixture-for-local-dpapi-check"
        profile = self.store.save_profile("personal", "Claude", "test-model", key)
        with closing(sqlite3.connect(self.store.path)) as conn:
            cipher, = conn.execute("SELECT protected_key FROM profiles").fetchone()
        self.assertNotIn(key.encode(), cipher)
        self.assertNotIn(key.encode(), self.store.path.read_bytes())
        self.assertNotIn(key, repr(self.store.list_profiles()))
        reopened = AccountStore(self.store.path)
        reopened.save_profile("renamed", "Claude", "next", profile_id=profile)
        self.assertEqual(reopened.resolve(profile)["key"], key)
        reopened.save_profile("renamed", "Claude", "next", key_env="BOT_TEST_KEY", profile_id=profile)
        with patch.dict(os.environ, {"BOT_TEST_KEY": "new-fixture"}):
            self.assertEqual(reopened.resolve(profile)["key"], "new-fixture")

    def test_protection_error_rolls_back_whole_import(self):
        with patch("account_store._dpapi", side_effect=ValueError("无法保护密钥")):
            with self.assertRaises(ValueError):
                self.import_json(dict(version=1, accounts=[
                    self.alpha, dict(name="B", provider="DeepSeek", model="sample", api_key="dummy")]))
        self.assertEqual(self.store.list_profiles(), [])


if __name__ == "__main__":
    unittest.main()
