import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cloud_room


class CloudRooms(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.members = ["GPT", "Gemini"]

    def rpc(self, op, **args):
        return cloud_room.dispatch(dict(op=op, room=getattr(self, "room", None), **args), root=self.root)

    def start(self, **extra):
        result = self.rpc("create", goal="编写一套测试", members=self.members, **extra)
        self.room = result["room"]
        return result

    def claim(self, member, client="client-a"):
        task = self.rpc("claim", member=member, client=client)["task"]
        return task, dict(member=member, client=client, id=task["id"], lease=task["lease"])

    def test_concurrent_claim_finish_and_export(self):
        initial = self.start(files={"README.md": "initial"})
        with ThreadPoolExecutor(2) as pool:
            tasks = list(pool.map(lambda m: self.claim(m, client=m), self.members))
            futures = [pool.submit(self.rpc, "finish", **lease, message="贡献", files={m+".txt":m})
                       for m, (_, lease) in zip(self.members, tasks)]
            for f in futures:
                f.result()
        info = self.rpc("status")
        self.assertEqual(info["status"], "complete")
        self.assertEqual(set(info["files"]), {"GPT.txt","Gemini.txt","README.md"})
        self.assertFalse((Path(initial["project"]) / "GPT.txt").exists())
        for name, value in self.rpc("export")["files"].items():
            self.assertEqual((Path(info["project"])/name).read_text("utf-8"), value)
        self.assertNotIn("lease", json.dumps(info))

    def test_retry_and_timeout(self):
        self.start()
        with ThreadPoolExecutor(2) as pool:
            res = list(pool.map(lambda c: self.rpc("claim", member="GPT", client=c)["task"], ("A", "B")))
        self.assertEqual(sum(bool(t) for t in res), 1)
        client = "A" if res[0] else "B"
        task, lease = self.claim("GPT", client)
        self.assertIn(task["lease"], [t["lease"] for t in res if t])
        self.rpc("renew", **lease)
        with patch("cloud_room.time.time", return_value=10**11):
            newer, _ = self.claim("GPT", "new client")
            self.assertNotEqual(task["lease"], newer["lease"])
            with self.assertRaises(ValueError):
                self.rpc("finish", **lease, message="stale")

    def test_common_file_conflict_requires_reclaim(self):
        self.start(files={"shared.md":"A"})
        _, one = self.claim("GPT")
        _, two = self.claim("Gemini", "other")
        self.rpc("finish", **one, files={"shared.md":"B"}, message="one")
        self.rpc("finish", **one, files={"shared.md":"B"}, message="one")
        with self.assertRaisesRegex(ValueError, "其他成员"):
            self.rpc("finish", **two, files={"shared.md":"C"}, message="two")
        self.rpc("release", **two)
        res, fresh = self.claim("Gemini", "other")
        self.assertEqual(res["files"]["shared.md"], "B")
        self.rpc("finish", **fresh, files={"shared.md":"BC"}, message="merged")
        self.assertEqual(self.rpc("export")["files"], {"shared.md":"BC"})

    def test_handoff_preserves_checkpoint_role_and_review(self):
        self.members.append("Claude")
        self.start(leader="GPT")
        task, lease = self.claim("GPT")
        self.assertEqual(task["stage"], "plan")
        self.assertIsNone(self.rpc("claim", member="Gemini", client="phone")["task"])
        self.rpc("finish", **lease, assignments={"Gemini":"impl", "Claude":"tests"}, message="分工")
        own, original = self.claim("Gemini")
        self.rpc("handoff", **original, to="Claude", summary="继续实现", files={"main.txt":"半成品"})
        res, handover = self.claim("Claude", "other-user")
        self.assertEqual((res["summary"], res["id"], res["files"]["main.txt"]), ("继续实现",own["id"],"半成品"))
        with self.assertRaisesRegex(ValueError, "租约"):
            self.rpc("finish", **original, message="late")
        self.rpc("finish", **handover, message="实现完毕", files={"main.txt":"完成"})
        _, testing = self.claim("Claude", "other-user")
        self.rpc("finish", **testing, message="已提交测试", files={"test.txt":"测试"})
        review, leased = self.claim("GPT")
        self.assertEqual(review["stage"], "review")
        self.assertTrue(any("实现完毕" in e for e in review["events"]))
        self.rpc("finish", **leased, message="验收通过", approved=True)
        self.assertEqual(self.rpc("status")["status"], "complete")

    def test_validates_private_and_ambiguous_paths(self):
        for files in [{"/etc/test":"x"}, {"a":"x", "a/b":"y"}, {"CON":"x"},
                      {"back\\door":"x"}, {"a/b":"1","A/F":"2"}, {"cert.key":"x"},
                      {"credentials.json":"x"}, {".env.production":"x"}]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.start(files=files)
        with self.assertRaises(ValueError):
            cloud_room.dispatch({"op":"export", "room":"../../outside"}, root=self.root)

    def test_json_lines_two_independent_clients(self):
        env = dict(os.environ, AGENTSBOT_ROOMS=str(self.root))
        def run(*req):
            result = subprocess.run([sys.executable, cloud_room.__file__], input=b"".join(
                json.dumps(r).encode()+b"\n" for r in req), capture_output=True, env=env, check=True)
            return [json.loads(row) for row in result.stdout.splitlines()]
        bad, ok = run({"foo":1}, {"op":"create","goal":"云端项目","members":self.members})
        self.assertFalse(bad["ok"])
        room = ok["result"]["room"]
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda m: run({"op":"claim","room":room,"member":m,"client":m}), self.members))
        self.assertEqual([v[0]["result"]["task"]["member"] for v in responses], self.members)
