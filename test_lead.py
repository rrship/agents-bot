import json
import sys
import tempfile
import threading
import unittest
from collections import Counter
from unittest.mock import patch

from engine import AGENTS, Job


def response(**fields):
    return json.dumps(dict(message="交付说明", **fields), ensure_ascii=False)


def context(prompt):
    return json.loads(prompt.split("\n", 1)[1])


class TeamTests(unittest.TestCase):
    def test_leader_repairs_with_verified_evidence(self):
        team = ["GPT", "Gemini", "DeepSeek"]
        workers = ["GPT", "Gemini"]
        barrier = threading.Barrier(2, timeout=5)
        seen = Counter()
        reviews = []
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "修复功能", [[sys.executable, "app.py"]], team, "DeepSeek")

            def provider(agent, config, prompt):
                ctx = context(prompt)
                if "阶段=派单" in prompt:
                    self.assertEqual(agent, "DeepSeek")
                    seen["dispatch"] += 1
                    if seen["dispatch"] == 2:
                        self.assertIn("REPAIR", ctx["feedback"])
                        self.assertIn("AssertionError", ctx["feedback"])
                    return response(assignments={name: name + " spec" for name in workers})
                if "阶段=实现" in prompt:
                    self.assertEqual(ctx["assignment"], agent + " spec")
                    barrier.wait()
                    if agent == "GPT":
                        return response(files={"app.py": "from worker import value\nassert value == 2"})
                    return response(files={"worker.py": "value = " + str(seen["dispatch"])})
                self.assertIn("阶段=验收复核", prompt)
                self.assertEqual(agent, "DeepSeek")
                reviews.append(ctx)
                # A leader cannot override a failed local test.
                return json.dumps(dict(message="REPAIR", approved=True, checklist=["检查 value"]))

            with patch("engine.call", provider):
                status = job.run(dict.fromkeys(team), lambda _: None, threading.Event())
            self.assertEqual(status, "完成", job.state["feedback"])
            self.assertEqual([r["test_results"][0]["exit_code"] for r in reviews], [1, 0])
            self.assertEqual(reviews[1]["files"]["worker.py"], "value = 2")
            self.assertEqual(reviews[1]["changed_paths"], ["worker.py"])
            self.assertEqual(set(reviews[0]["worker_results"]), set(workers))

    def test_peer_pair_and_old_states(self):
        with tempfile.TemporaryDirectory() as folder:
            pair = ["GPT", "DeepSeek"]
            job = Job.create(folder, "编写应用", [], pair)
            seen = set()
            def provider(agent, config, prompt):
                seen.add(agent)
                if "阶段=实现" in prompt:
                    return response(files={agent + ".txt": agent})
                return response(approved=True)
            with patch("engine.call", provider):
                self.assertEqual(job.run(dict.fromkeys(pair), lambda _: None, threading.Event()), "待人工验收")
            self.assertEqual(seen, set(pair))
            self.assertTrue(any("GPT → DeepSeek" in m for m in job.state["messages"]))
            del job.state["members"], job.state["leader"]
            job.save()
            old = Job.load(job.path)
            self.assertEqual(old.state["members"], list(AGENTS))
            self.assertIsNone(old.state["leader"])

    def test_resume_workers_and_review(self):
        team = ["Gemini", "GPT", "DeepSeek"]
        counts = Counter()
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "交付", [[sys.executable, "-c", "print('tested')"]], team, "Gemini")
            def provider(agent, config, prompt):
                if "阶段=派单" in prompt:
                    counts["plan"] += 1
                    return response(assignments={w: w for w in team[1:]})
                if "阶段=实现" in prompt:
                    counts[agent] += 1
                    if agent == "DeepSeek" and counts[agent] == 1:
                        raise ConnectionError("offline")
                    return response(files={agent + ".txt": "hello"})
                counts["review"] += 1
                if counts["review"] == 1:
                    return response(approved=True)  # missing checklist, retry only review
                return response(approved=True, checklist=["已验证"])
            logs = []
            with patch("engine.call", provider):
                statuses = [Job.load(job.path).run(dict.fromkeys(team), logs.append, threading.Event()) for _ in range(3)]
            self.assertEqual(statuses, ["受阻", "受阻", "完成"])
            self.assertEqual(counts, dict(plan=1, GPT=1, DeepSeek=2, review=2))
            self.assertEqual(sum(m.startswith("验收 ") for m in logs), 1)

    def test_leader_rejects_and_reassigns_despite_passing_tests(self):
        pair = ["GPT", "DeepSeek"]
        attempts = []
        with tempfile.TemporaryDirectory() as folder:
            job = Job.create(folder, "写使用说明", [[sys.executable, "-c", "pass"]], pair, "GPT")

            def provider(agent, config, prompt):
                ctx = context(prompt)
                if "阶段=派单" in prompt:
                    spec = "撰写说明"
                    if attempts:
                        self.assertIn("缺少启动示例", ctx["feedback"])
                        spec = "添加启动示例"
                    attempts.append(spec)
                    return response(assignments={"DeepSeek": spec})
                if "阶段=实现" in prompt:
                    self.assertEqual(agent, "DeepSeek")
                    return response(files={"README.md": ctx["assignment"]})
                return response(approved=len(attempts) == 2, checklist=["缺少启动示例" if len(attempts) == 1 else "已提供启动示例"])

            with patch("engine.call", provider):
                self.assertEqual(job.run(dict.fromkeys(pair), lambda _: None, threading.Event()), "完成")
            self.assertEqual(attempts, ["撰写说明", "添加启动示例"])
            self.assertEqual((job.path / "project/README.md").read_text("utf-8"), attempts[-1])


if __name__ == "__main__":
    unittest.main()
