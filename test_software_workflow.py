import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch

from cloud_ui import server_configs
from cloud_worker import run_claim
from engine import Job
from providers import call
from software_bridge import Bridge, BridgeError
from software_setup import install


class Host:
    """Drive the actual stdio endpoint with the same tool calls as an MCP host."""
    def __init__(self, stack, database, app):
        child = stack.enter_context(subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("software_bridge.py")),
             "--app", app, "--database", str(database)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8"))
        self.input, self.output = child.stdin, child.stdout
        stack.callback(self.input.close)
        self.seq = 0
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "integration-test", "version": "1"}})
        self.input.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.input.flush()

    def send(self, method, params):
        self.seq += 1
        self.input.write(json.dumps(dict(jsonrpc="2.0", id=self.seq, method=method, params=params)) + "\n")
        self.input.flush()
        return json.loads(self.output.readline())["result"]

    def tool(self, name, **arguments):
        result = self.send("tools/call", dict(name=name, arguments=arguments))
        if result.get("isError"):
            raise AssertionError(result)
        return json.loads(result["content"][0]["text"])

    def claim(self):
        task = self.tool("claim_task", wait_seconds=5)
        if "task_id" not in task:
            raise AssertionError(f"Missing task: {task}")
        return task

    def submit(self, task, **reply):
        return self.tool("submit_result", task_id=task["task_id"], lease=task["lease"],
                         reply=json.dumps(reply, ensure_ascii=False))


class SoftwareWorkflows(unittest.TestCase):
    def test_two_hosts_deliver_project_through_real_leader_workflow(self):
        with ExitStack() as stack:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            project = folder / "project"
            project.mkdir()
            db = folder / "mailbox.db"
            bridge = Bridge(db)
            members = ["Codex", "Claude Desktop"]
            job = Job.create(project, "交付说明文件", [["python", "-c",
                             "from pathlib import Path; assert Path('summary.txt').read_text(encoding='utf-8') == '完成'"]],
                             members, leader="Codex")
            hosts = [Host(stack, db, member) for member in members]
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(bridge.close)
            logs = []
            future = pool.submit(job.run, {n: dict(mode="software", bridge=bridge) for n in members},
                                 logs.append, threading.Event())
            plan = hosts[0].claim()
            self.assertIn("阶段=派单", plan["prompt"])
            hosts[0].submit(plan, message="分工完毕", assignments={"Claude Desktop": "生成 summary.txt"})
            work = hosts[1].claim()
            self.assertIn("生成 summary.txt", work["prompt"])
            hosts[1].submit(work, message="已生成说明", files={"summary.txt": "完成"})
            review = hosts[0].claim()
            self.assertIn("已生成说明", review["prompt"])
            self.assertTrue(json.loads(review["prompt"].split("\n", 1)[1])["test_results"][0]["passed"])
            hosts[0].submit(review, message="验收通过", checklist=["内容正确"], approved=True)
            self.assertEqual(future.result(8), "完成")
            self.assertEqual((job.path / "project/summary.txt").read_text("utf-8"), "完成")
            self.assertTrue(any("Claude Desktop → Codex [实现]" in msg for msg in logs))

    def test_pause_then_resume_reissues_only_outstanding_turn(self):
        with ExitStack() as stack:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            (folder / "project").mkdir()
            db = folder / "queue.db"
            bridge = Bridge(db)
            actors = ["Codex", "ZCode"]
            job = Job.create(folder / "project", "测试", [], actors, "Codex")
            cfg = {n: dict(mode="software", bridge=bridge) for n in actors}
            host = Host(stack, db, actors[0])
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(bridge.close)
            stop = threading.Event()
            first = pool.submit(job.run, cfg, lambda s: None, stop)
            stale = host.claim()
            stop.set()
            self.assertEqual(first.result(5), "已暂停")
            stop.clear()
            second = pool.submit(job.run, cfg, lambda s: None, stop)
            current = host.claim()
            self.assertNotEqual(stale["task_id"], current["task_id"])
            with self.assertRaises(AssertionError):
                host.submit(stale, message="旧结果")
            stop.set()
            self.assertEqual(second.result(5), "已暂停")

    def test_local_cloud_software_stop_and_detached_rejection(self):
        event = threading.Event()
        withdrew = threading.Event()

        class WaitingBridge:
            def request(self, app, prompt, *, stop):
                event.set()
                if stop.wait(2):
                    withdrew.set()
                raise BridgeError("已撤回")

        cfg = dict(bridge=WaitingBridge(), mode="software")
        lease = dict(member="Antigravity", room="room", id="task", lease="lease", client="client")
        calls = []
        with patch("cloud_worker.prepare", return_value=("领取工作", None)):
            run_claim(lambda action, **args: calls.append(action), lease, {}, cfg, event.is_set)
        self.assertTrue(withdrew.wait(1))
        self.assertEqual(calls, ["release"])
        with self.assertRaisesRegex(ValueError, "本机参与"):
            server_configs({"Antigravity": cfg})

    def test_native_harness_requires_desktop_plugin_and_cancels_own_run(self):
        stop = threading.Event()
        def invoke(argv, folder, *args, **options):
            self.assertTrue(options["desktop_host"])
            if argv[1] == "send":
                return {"run_id": "task-1"}
            if argv[1] == "wait":
                stop.set()
                return {"state": "running", "timed_out": True}
            return {}
        cfg = dict(mode="software", transport="baton", stop=stop)
        with patch("extra_accounts.command", return_value=["baton"]), patch("extra_accounts.run_json", side_effect=invoke) as runner:
            with self.assertRaises(InterruptedError):
                call("DeepSeek Harness", cfg, "测试")
            self.assertEqual(runner.call_args.args[0], ["baton", "cancel", "task-1"])


class SoftwareSetupTests(unittest.TestCase):
    def test_reinstall_updates_only_our_entry(self):
        for app in ("Codex", "Claude Desktop"):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "config"
                seed = ('# 我的设置\n[mcp_servers.other]\ncommand = "keep"\n' if app == "Codex" else
                        '{"preferences":{"locale":"zh"},"mcpServers":{"other":{"command":"keep"}}}')
                target.write_text(seed, "utf-8")
                args = ["D:/旧目录/software_bridge.py", "--app", app]
                install(app, "python", args, path=target)
                first = target.read_bytes()
                install(app, "python", args, path=target)
                self.assertEqual(first, target.read_bytes())
                args[0] = "D:/Program Files/新版/software_bridge.py"
                install(app, "D:/Apps/python.exe", args, path=target)
                data = target.read_text("utf-8")
                if app == "Codex":
                    self.assertIn("# 我的设置", data)
                    servers = tomllib.loads(data)["mcp_servers"]
                    key = "agents_bot"
                else:
                    doc = json.loads(data)
                    self.assertEqual(doc["preferences"]["locale"], "zh")
                    servers, key = doc["mcpServers"], "agents-bot"
                self.assertEqual(servers["other"]["command"], "keep")
                self.assertEqual(servers[key]["args"], args)

    def test_invalid_or_foreign_configuration_is_preserved(self):
        for app, seed in (("Codex", '[mcp_servers.agents_bot]\ncommand="other"\n'),
                          ("Claude Desktop", '{"mcpServers":{"agents-bot":{"command":"other"}}}'),
                          ("Codex", "[parse error"), ("Claude Desktop", "{parse error")):
            with self.subTest(app=app, seed=seed), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config"
                path.write_text(seed, "utf-8")
                with self.assertRaises(ValueError):
                    install(app, "python", [], path=path)
                self.assertEqual(seed, path.read_text("utf-8"))
