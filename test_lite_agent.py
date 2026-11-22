import json
import threading
import unittest
from unittest.mock import patch

import lite_agent as lite


class LiteAgentTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(provider="Gemini", model="test-model", mode="api",
                           key="secret-api-marker", profile_label="private-profile-marker")

    def test_snapshot_tools_history_and_draft_only(self):
        snapshot = {"todo.py": "def add(): pass", "other.txt": "unread-file-marker"}
        prompts = []
        replies = iter([
            {"tool": "list"},
            {"tool": "search", "query": "def add"},
            {"tool": "read", "path": "todo.py", "offset": 0},
            {"message": "完成", "files": {"todo.py": "def add(): return 3"}},
        ])

        def fake_api(provider, config, prompt):
            self.assertEqual(provider, "Gemini")
            self.assertEqual(config, {k: self.config[k] for k in ("mode", "model", "key")})
            self.assertNotIn(self.config["key"], prompt)
            self.assertNotIn(self.config["profile_label"], prompt)
            self.assertNotIn("unread-file-marker", prompt)
            self.assertLessEqual(len(prompt), lite.PROMPT_LIMIT)
            prompts.append(prompt)
            return json.dumps(next(replies), ensure_ascii=False)

        events = []
        with patch("providers.call", side_effect=fake_api):
            answer = lite.run_task("Gemini", self.config, "继续上次修改",
                                   snapshot, [{"role": "user", "content": "旧需求"}], report=events.append)
        self.assertIn("旧需求", prompts[0])
        self.assertNotIn("def add(): pass", prompts[0])
        self.assertIn("def add(): pass", prompts[-1])
        self.assertEqual(events, ["列出共享文件", "搜索共享文本", "阅读共享文件"])
        self.assertEqual(answer["files"]["todo.py"], "def add(): return 3")
        self.assertEqual(snapshot["todo.py"], "def add(): pass")

    def test_tool_scope_budget_and_rejected_drafts(self):
        cases = [
            {"tool":"read", "path":"../credentials"},
            {"tool":"list", "offset":True},
            {"tool":"shell", "command":"test"},
            {"tool":"search", "query":""},
            {"message":"replace", "files":{"a.txt":"unread overwrite"}},
            {"message":"write", "files":{"../outside":"value"}},
        ]
        for action in cases:
            with self.subTest(action=action), patch("providers.call", return_value=json.dumps(action)):
                with self.assertRaises(ValueError):
                    lite.run_task("Gemini", self.config, "test", {"a.txt":"old"})
        with patch("providers.call", return_value='{"tool":"list"}') as invoke:
            with self.assertRaisesRegex(ValueError, "上限"):
                lite.run_task("Gemini", self.config, "test")
            self.assertEqual(invoke.call_count, lite.MAX_CALLS)

    def test_team_transport_handles_connection_check_reviews_and_handoffs(self):
        team_cfg = self.config
        with patch("providers.call", return_value="OK") as invoke:
            self.assertEqual(lite.team_call(team_cfg, "Reply only OK."), "OK")
            self.assertEqual(invoke.call_args.args[2], "Reply only OK.")
        ctx = {"goal":"build", "members":["内置 Agent", "Gemini"], "files":{"a.txt":"original text"}}
        for final in [{"message":"计划","assignments":{"Gemini":"实现"}},
                      {"message":"通过","approved":True,"checklist":["ok"]},
                      {"message":"交接","handoff":{"to":"Gemini","summary":"补测试"},"files":{"new.md":"new"}}]:
            with self.subTest(final=final), patch("providers.call", return_value=json.dumps(final)) as invoke:
                result = lite.team_call(team_cfg, "阶段说明\n" + json.dumps(ctx))
                self.assertEqual(json.loads(result), final)
                self.assertIn("阶段说明", invoke.call_args.args[2])
                self.assertNotIn("original text", invoke.call_args.args[2])

    def test_cancel_drops_late_reply_and_never_restarts(self):
        stop = threading.Event()
        def late(*args):
            stop.set()
            return '{"tool":"list"}'
        with patch("providers.call", side_effect=late) as invoke:
            with self.assertRaises(InterruptedError):
                lite.run_task("Gemini", self.config, "test", stop=stop)
            with self.assertRaises(InterruptedError):
                lite.run_task("Gemini", self.config, "test", stop=stop)
            self.assertEqual(invoke.call_count, 1)

    def test_original_provider_entrypoint_runs_cloud_assignment_with_bound_api(self):
        import cloud_worker
        import providers
        actor = providers.BUILTIN
        task = dict(member=actor, stage="implement", peers=["GPT"], visited=[], goal="calendar",
                    assignment="write notes", summary="", files={"base.txt": "draft"},
                    workers=[actor], members=["GPT", actor], leader="GPT", events=[])
        prompt, shortcut = cloud_worker.prepare(task)
        self.assertIsNone(shortcut)
        replies = ['{"tool":"read","path":"base.txt"}',
                   '{"message":"notes ready","files":{"base.txt":"complete"}}']
        with patch("providers._api", side_effect=replies) as transport:
            raw = providers.call(actor, self.config, prompt)
        op, payload = cloud_worker.submission(task, prompt, raw)
        self.assertEqual((op, payload), ("finish", {"message":"notes ready","files":{"base.txt":"complete"}}))
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(all(c.args[:3] == ("Gemini", "test-model", self.config["key"])
                            for c in transport.call_args_list))

    def test_partial_file_requires_all_pages(self):
        text = "x" * (lite.READ_SIZE + 1)
        read = '{"tool":"read","path":"a.txt","offset":0}'
        final = '{"message":"done","files":{"a.txt":"replacement"}}'
        with patch("providers.call", side_effect=[read, final]):
            with self.assertRaisesRegex(ValueError, "完整读取"):
                lite.run_task("Gemini", self.config, "modify", {"a.txt":text})
        with patch("providers.call", side_effect=[
            read, json.dumps(dict(tool="read", path="a.txt", offset=lite.READ_SIZE)), final
        ]):
            self.assertEqual(lite.run_task("Gemini", self.config, "modify", {"a.txt":text})["files"],
                             {"a.txt":"replacement"})
