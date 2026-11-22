import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import cloud_room
import cloud_worker


class CloudWorkers(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.members = ["GPT", "Gemini", "Kimi"]

    def rpc(self, op, **fields):
        return cloud_room.dispatch(dict(op=op, **fields), self.root)

    def team(self, room, names, stop=None):
        cloud_worker.run_team(self.rpc, room, {n: {"mode": "account"} for n in names},
                              stop or threading.Event(), lambda msg: None)

    def test_two_clients_plan_transfer_review_and_export(self):
        room = self.rpc("create", members=self.members, leader="GPT", goal="Develop a calendar")["room"]

        def model(name, cfg, prompt):
            ctx = json.loads(prompt.split("\n", 1)[1])
            stage = ctx["stage"]
            if stage == "plan":
                return json.dumps(dict(message="plan", assignments={"Gemini": "calendar", "Kimi": "docs"}))
            if stage == "review":
                self.assertEqual(set(ctx["files"]), {"calendar.py", "README.md", "leap.py"})
                return '{"message":"reviewed","approved":true}'
            if name == "Gemini":
                return json.dumps(dict(message="initial draft", files={"calendar.py": "# draft"},
                                       handoff={"to": "Kimi", "summary": "Add leap.py to finish."}))
            if ctx["summary"]:
                self.assertIn("Add leap.py", ctx["summary"])
                self.assertIn("calendar.py", ctx["files"])
                return json.dumps(dict(message="leap years added", files={"leap.py": "year=2024"}))
            return json.dumps(dict(message="instructions", files={"README.md": "Calendar"}))

        with patch("cloud_worker.call", model), patch("cloud_worker.POLL_SECONDS", .015):
            with ThreadPoolExecutor(2) as clients:
                one = clients.submit(self.team, room, ["GPT", "Gemini"])
                two = clients.submit(self.team, room, ["Kimi"])
                one.result(timeout=8)
                two.result(timeout=8)
        self.assertEqual(self.rpc("status", room=room)["status"], "complete")
        self.assertEqual(self.rpc("export", room=room)["files"]["leap.py"], "year=2024")

    def test_large_context_transfers_once_and_preserves_unseen_files(self):
        files = {"large.txt": "z" * 58000}
        room = self.rpc("create", members=self.members[:2], files=files, goal="new modules")["room"]
        prompts = []

        def model(name, cfg, prompt):
            prompts.append(prompt)
            ctx = json.loads(prompt.split("\n", 1)[1])
            self.assertNotIn("large.txt", ctx["files"])
            self.assertTrue(ctx["summary"])
            return json.dumps(dict(message="done", files={name + ".md": "added"}))

        with patch("cloud_worker.call", model), patch("cloud_worker.POLL_SECONDS", .015):
            self.team(room, self.members[:2])
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all(len(p) <= 48000 for p in prompts))
        self.assertEqual(self.rpc("export", room=room)["files"]["large.txt"], files["large.txt"])
        self.assertEqual([t["handoffs"] for t in self.rpc("status", room=room)["tasks"]], [1, 1])

    def test_stop_releases_lease_and_discards_late_model_reply(self):
        room = self.rpc("create", members=self.members[:2], goal="stop")["room"]
        entered, finish, stop = (threading.Event() for _ in range(3))

        def delayed(*args):
            entered.set()
            finish.wait(5)
            return '{"message":"late","files":{"late.txt":"late"}}'

        with patch("cloud_worker.call", delayed), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.team, room, ["GPT"], stop)
            try:
                self.assertTrue(entered.wait(3))
                stop.set()
                future.result(timeout=2)
                task = self.rpc("claim", room=room, member="GPT", client="other")["task"]
                self.assertIsNotNone(task)
            finally:
                finish.set()
        self.assertNotIn("late.txt", self.rpc("export", room=room)["files"])

    def test_provider_failure_can_be_retried_without_automatic_charges(self):
        room = self.rpc("create", members=self.members[:2], goal="retry")["room"]
        with patch("cloud_worker.call", side_effect=RuntimeError("offline")) as invoke:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                self.team(room, ["GPT"])
        self.assertEqual(invoke.call_count, 1)
        self.assertIsNotNone(self.rpc("claim", room=room, member="GPT", client="again")["task"])
