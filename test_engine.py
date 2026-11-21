import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from engine import AGENTS, Job, clean_path


class WorkflowTests(unittest.TestCase):
    def test_resume_and_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = Job.create(root, "构建应用", [[sys.executable, "app.py"]])
            failed = []
            plans = []
            def provider(agent, config, prompt):
                if "阶段=分工" in prompt:
                    plans.append(agent)
                    if agent == "Gemini" and not failed:
                        failed.append(True)
                        raise RuntimeError("offline")
                    return '{"message":"Use start()"}'
                if "阶段=实现" in prompt:
                    self.assertIn("Use start()", prompt)
                    files = {"app.py": "from worker import start\nassert start() == 1"} if agent == "GPT" else {"worker.py": "def start(): return 1"}
                    return json.dumps(dict(message="完成实现", files=files))
                self.assertIn("def start()", prompt)
                return '{"approved":true,"message":"ok"}'
            with patch("engine.call", provider):
                args = (dict.fromkeys(AGENTS), lambda msg: None, threading.Event())
                self.assertEqual(job.run(*args), "受阻")
                self.assertEqual(Job.load(job.path).run(*args), "完成")
                self.assertEqual(plans.count("GPT"), 1)
                self.assertTrue((job.path / "project" / "app.py").exists())
                self.assertFalse((root / "app.py").exists())

    def test_conflicting_writes_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "协作", [])
            def provider(agent, config, prompt):
                if "阶段=实现" in prompt:
                    files = {"main.txt": agent} if "双方修改同一文件" not in prompt or agent == "GPT" else {}
                    return json.dumps(dict(message="修改", files=files))
                return '{"approved":true,"message":"通过"}'
            with patch("engine.call", provider):
                status = job.run(dict.fromkeys(AGENTS), lambda msg: None, threading.Event())
            self.assertEqual(status, "待人工验收")
            self.assertEqual((job.path / "project/main.txt").read_text(), "GPT")

    def test_unsafe_paths(self):
        for name in ("../oops", "C:/oops", "foo/CON", ".git/config", "/absolute"):
            with self.assertRaises(ValueError):
                clean_path(name)

    def test_empty_suite_repaired_and_test_artifacts_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "交付功能", [["python", "-m", "unittest", "discover"]])
            def provider(agent, config, prompt):
                if "阶段=实现" in prompt:
                    files = {"app.py": "x = 10"} if agent == "GPT" else {}
                    if agent == "Gemini" and "Ran 0 tests" in prompt:
                        files["test_app.py"] = ("import unittest\nfrom pathlib import Path\nfrom app import x\n"
                                                "class Test(unittest.TestCase):\n def test_x(self):\n"
                                                "  Path('transient.txt').touch()\n  self.assertEqual(x, 10)")
                    return json.dumps(dict(message="ok", files=files))
                return '{"approved":true,"message":"检查"}'
            with patch("engine.call", provider):
                self.assertEqual(job.run(dict.fromkeys(AGENTS), lambda msg: None, threading.Event()), "完成")
            self.assertEqual(job.state["round"], 1)
            self.assertFalse((job.path / "project/transient.txt").exists())

    def test_pause_feedback_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "示例", [])
            stop = threading.Event()
            stop.set()
            self.assertEqual(job.run({}, lambda msg: None, stop), "已暂停")
            job.add_feedback("使用中文界面")
            self.assertIn("使用中文界面", Job.load(job.path).state["goal"])
            job.cancel()
            self.assertEqual(job.run({}, lambda msg: None, stop), "已取消")
