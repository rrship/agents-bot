import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from software_bridge import Bridge, BridgeError


class SoftwareBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "nested" / "software.db"
        self.bridge = Bridge(self.path)
        self.peer = Bridge(self.path)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.pool.shutdown)
        self.addCleanup(self.bridge.close)

    def request(self, app="Codex", prompt="检查界面", **kwargs):
        return self.pool.submit(self.bridge.request, app, prompt, **kwargs)

    def claim(self, app="Codex", client="first", **kwargs):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.peer.claim(app, client, **kwargs)
            if task is not None:
                return task
            time.sleep(.01)
        self.fail("No task was available")

    def test_separate_clients_receive_their_tasks_and_submit_idempotently(self):
        codex = self.request()
        claude = self.request("Claude Desktop", "评审设计")
        first = self.claim()
        self.assertEqual(first["prompt"], "检查界面")
        self.assertEqual(self.claim()["lease"], first["lease"])
        self.assertIsNone(self.peer.claim("Codex", "other"))
        second = self.claim("Claude Desktop", "other")
        self.assertEqual(second["prompt"], "评审设计")
        for wrong_app, wrong_client, wrong_lease in [
            ("Claude Desktop", "first", first["lease"]), ("Codex", "other", first["lease"]),
            ("Codex", "first", "incorrect"),
        ]:
            with self.assertRaises(BridgeError):
                self.peer.complete(wrong_app, wrong_client, first["task_id"], wrong_lease, "reply")
        args = ("Codex", "first", first["task_id"], first["lease"])
        self.peer.complete(*args, "完整回复")
        self.assertEqual(codex.result(2), "完整回复")
        self.assertTrue(self.peer.complete(*args, "完整回复"))
        with self.assertRaises(BridgeError):
            self.peer.complete(*args, "覆盖回复")
        self.peer.complete("Claude Desktop", "other", second["task_id"], second["lease"], "通过")
        self.assertEqual(claude.result(2), "通过")

    def test_expired_lease_is_reassigned_once_and_rejects_stale_result(self):
        request = self.request()
        old = self.claim(lease_seconds=.06)
        time.sleep(.09)
        clients = ["second", "third"]
        with ThreadPoolExecutor(2) as clients_pool:
            claims = list(clients_pool.map(lambda client: self.peer.claim("Codex", client), clients))
        winners = [(client, task) for client, task in zip(clients, claims) if task]
        self.assertEqual(len(winners), 1)
        client, task = winners[0]
        self.assertEqual(old["task_id"], task["task_id"])
        self.assertNotEqual(old["lease"], task["lease"])
        with self.assertRaises(BridgeError):
            self.peer.complete("Codex", "first", old["task_id"], old["lease"], "stale")
        self.peer.complete("Codex", client, task["task_id"], task["lease"], "current")
        self.assertEqual(request.result(2), "current")

    def test_stop_close_and_timeout_withdraw_queued_work(self):
        stop = threading.Event()
        request = self.request(stop=stop)
        task = self.claim()
        stop.set()
        with self.assertRaises(BridgeError):
            request.result(2)
        with self.assertRaises(BridgeError):
            self.peer.complete("Codex", "first", task["task_id"], task["lease"], "late")
        with self.assertRaises(BridgeError):
            self.request(timeout=.05).result(2)
        self.assertIsNone(self.peer.claim("Codex", "first"))
        last = self.request()
        self.claim()
        self.bridge.close()
        with self.assertRaises(BridgeError):
            last.result(2)
        self.assertIsNone(self.peer.claim("Codex", "first"))

    def test_real_stdio_process_exchanges_a_unicode_reply_with_desktop(self):
        request = self.request("Antigravity", "分工\n审阅文件")
        argv = [sys.executable, str(Path(__file__).with_name("software_bridge.py")),
                "--database", str(self.path), "--app", "Antigravity"]
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              encoding="utf-8") as child:
            try:
                def send(method, params=None, request_id=1):
                    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
                    if request_id is not None:
                        msg["id"] = request_id
                    child.stdin.write(json.dumps(msg) + "\n")
                    child.stdin.flush()
                    if request_id is not None:
                        return json.loads(child.stdout.readline())

                init = send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                            "clientInfo": {"name": "test", "version": "1"}})
                self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
                send("notifications/initialized", request_id=None)
                tools = send("tools/list")["result"]["tools"]
                self.assertEqual({tool["name"] for tool in tools}, {"claim_task", "submit_result"})
                claim = send("tools/call", {"name": "claim_task", "arguments": {"wait_seconds": 2}})
                task = json.loads(claim["result"]["content"][0]["text"])
                self.assertEqual(task["prompt"], "分工\n审阅文件")
                args = {"task_id": task["task_id"], "lease": task["lease"], "reply": '{"message":"评审完成"}'}
                wrong = send("tools/call", {"name": "submit_result", "arguments": dict(args, lease="bogus")})
                self.assertTrue(wrong["result"]["isError"])
                right = send("tools/call", {"name": "submit_result", "arguments": args})
                self.assertFalse(right["result"].get("isError", False))
                self.assertEqual(request.result(2), '{"message":"评审完成"}')
            finally:
                child.stdin.close()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
