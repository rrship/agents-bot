import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from engine import Job
from handoff import bounded_prompt, parse_handoff


class HandoffTests(unittest.TestCase):
    def test_manual_transfer_persists_drafts_and_recipient_across_retry(self):
        calls = []
        team = ["GPT", "Gemini"]
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "创建应用", [], team)

            def provider(actor, config, prompt):
                ctx = json.loads(prompt.split("\n", 1)[1])
                if "阶段=实现" not in prompt:
                    return '{"message":"ok","approved":true}'
                owner = ctx["assignment_owner"]
                calls.append((owner, actor))
                if owner == "Gemini":
                    return '{"message":"parallel","files":{"notes.txt":"review"}}'
                if actor == "GPT":
                    return json.dumps(dict(message="draft ready", files={"draft.py": "a = 1"},
                        handoff=dict(to="Gemini", summary="draft.py 已完成。请实现 UI。", reason="上下文即将耗尽")))
                self.assertEqual(ctx["files"]["draft.py"], "a = 1")
                self.assertIn("UI", ctx["summary"])
                self.assertEqual(ctx["handoff_candidates"], [])
                if calls.count((owner, actor)) == 1:
                    raise ConnectionError("offline")
                return '{"message":"UI done","files":{"ui.py":"value = 3"}}'

            with patch("engine.call", provider):
                args = dict.fromkeys(team), lambda line: None, threading.Event()
                self.assertEqual(job.run(*args), "受阻")
                saved = Job.load(job.path)
                self.assertEqual(saved.run(*args), "待人工验收")
            self.assertEqual(calls.count(("GPT", "GPT")), 1)
            self.assertEqual(calls.count(("Gemini", "Gemini")), 1)
            self.assertEqual((saved.path / "project/draft.py").read_text(), "a = 1")
            self.assertEqual((saved.path / "project/ui.py").read_text(), "value = 3")
            self.assertEqual(saved.state["answers"]["0-实现"]["GPT"]["completed_by"], "Gemini")

    def test_automatic_handoff_uses_compact_context_and_preserves_leader_review(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "large.txt").write_text("x" * 47950)
            job = Job.create(folder, "build", [], ["GPT", "Kimi"], "GPT")
            recipients = []
            def provider(actor, config, prompt):
                self.assertLessEqual(len(prompt), 48000)
                ctx = json.loads(prompt.split("\n", 1)[1])
                if "阶段=派单" in prompt:
                    return '{"message":"plan","assignments":{"Kimi":"Add app.py"}}'
                if "阶段=实现" in prompt:
                    recipients.append(actor)
                    self.assertEqual(ctx["received_from"], "Kimi")
                    self.assertIn("Add app.py", ctx["summary"])
                    self.assertTrue(ctx["context_compacted"])
                    return '{"message":"done","files":{"app.py":"pass"}}'
                self.assertEqual(ctx["worker_results"]["Kimi"]["completed_by"], "GPT")
                return '{"message":"ok","approved":true,"checklist":["done"]}'
            with patch("engine.call", provider):
                result = job.run({"GPT": {}, "Kimi": {}}, lambda line: None, threading.Event())
            self.assertEqual(result, "待人工验收")
            self.assertEqual(recipients, ["GPT"])

    def test_budget_prioritizes_named_files_and_rejects_cycles(self):
        ctx = dict(goal="create file", assignment="change z.txt", messages=["line" * 4000],
                   files={"a.txt": "A" * 1000, "z.txt": "Z" * 1000})
        prompt = bounded_prompt("header", ctx, limit=2400)
        self.assertLessEqual(len(prompt), 2400)
        files = json.loads(prompt.split("\n", 1)[1])["files"]
        self.assertEqual(files, {"z.txt": ctx["files"]["z.txt"]})
        for target in ("outside", "worker", "previous"):
            reply = {"handoff": {"to": target, "summary": "pending work"}}
            with self.assertRaises(ValueError):
                parse_handoff(reply, "worker", ["worker", "previous", "unused"], ["previous"])


if __name__ == "__main__":
    unittest.main()
