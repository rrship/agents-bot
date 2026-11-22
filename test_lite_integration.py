import json
import os
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

from account_store import AccountStore
from app import Desk
from cloud_daemon import validate_configs
from cloud_ui import import_project, server_configs
from engine import Job
from providers import BUILTIN


class BoundAgentIntegration(unittest.TestCase):
    def test_json_profile_reaches_task_and_key_never_enters_project(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LITE_WORK_KEY="example-only-token"):
            folder = Path(temp)
            project = folder / "inputs"
            project.mkdir()
            (project / "rates.json").write_text('{"rate":2}', encoding="utf-8")
            settings = project / "my-export.json"
            settings.write_text(json.dumps(dict(version=1, accounts=[
                dict(name="Personal", provider="Gemini", model="m1", key_env="LITE_WORK_KEY"),
                dict(name="Service", provider="Gemini", model="m2", api_key="do-not-share-raw-key"),
            ])) if os.name == "nt" else json.dumps(dict(name="Personal", provider="Gemini", model="m1",
                                                        key_env="LITE_WORK_KEY")), encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            self.addCleanup(root.destroy)
            desk = Desk(root)
            desk._account_store = AccountStore(folder / "vault.db")
            prof = desk.account_store.import_file(settings)[0]
            desk.apply_profile(prof, builtin=True)
            desk.apply_profile(prof, builtin=False)
            self.assertIn(BUILTIN, desk._members())
            self.assertEqual(desk._config(BUILTIN)["provider"], "Gemini")
            self.assertEqual(desk._config("Gemini")["key"], "example-only-token")
            self.assertEqual(import_project(project), {"rates.json": '{"rate":2}'})
            config = desk._config(BUILTIN)
            clean = validate_configs(server_configs({BUILTIN: config}), [BUILTIN, "GPT"])
            self.assertEqual(clean[BUILTIN], config)
            job = Job.create(project, "Write a guide", [], ["GPT", BUILTIN], "GPT")
            observed = []

            def mock_api(name, model, key, prompt):
                observed.append((name, model))
                self.assertNotIn("do-not-share-raw-key", prompt)
                self.assertNotIn("example-only-token", prompt)
                if name == "Gemini":
                    self.assertEqual((model, key), ("m1", "example-only-token"))
                    return json.dumps(dict(message="draft", files={"guide.md":"Read me"}))
                if "阶段=派单" in prompt:
                    return json.dumps(dict(message="delegate", assignments={BUILTIN:"Write guide.md"}))
                return json.dumps(dict(message="reviewed", approved=True, checklist=["guide.md"]))

            with patch("providers._api", mock_api):
                state = job.run({"GPT":dict(mode="api", model="lead", key="other"), BUILTIN:config},
                                lambda msg: None, threading.Event())
            self.assertEqual(state, "待人工验收")
            self.assertIn(("Gemini", "m1"), observed)
            self.assertEqual((job.path / "project/guide.md").read_text(), "Read me")
            self.assertFalse((job.path / "project/my-export.json").exists())
            for path in job.path.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"example-only-token", path.read_bytes())
                    self.assertNotIn(b"do-not-share-raw-key", path.read_bytes())
            desk.account_store.delete_profile(prof)
            desk.lite.refresh_profiles()
            self.assertIn("失效", desk.builtin_binding.get())
            with self.assertRaises(ValueError):
                desk._config(BUILTIN)
            with self.assertRaises(ValueError):
                desk._config("Gemini")
