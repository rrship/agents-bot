import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from account_store import AccountStore
from engine import Job
from handoff import DEFAULT_CONTEXT_CHARS
from opc_runtime import checked_routes, employee_prompt, resolve_configs, skill_snapshot
from opc_skills import LEAD, ROLE_NAMES
from software_bridge import Bridge
from test_software_workflow import Host


def mcp(software):
    return {"kind": "mcp", "target": software}


class OPCTests(unittest.TestCase):
    def test_boss_api_and_two_employees_share_one_real_stdio_queue(self):
        with ExitStack() as stack:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            project = folder / "project"
            project.mkdir()
            store = AccountStore(folder / "accounts.sqlite3")
            secret = "test-opc-key-only-in-account-store"
            stack.enter_context(patch.dict(os.environ, OPC_TEST_KEY=secret))
            profile = store.save_profile("老板的账号", "GPT", "test-model", key_env="OPC_TEST_KEY")
            roles = [LEAD, "前端设计师", "后端开发员"]
            routes = {LEAD: {"kind": "api", "target": profile},
                      roles[1]: mcp("Codex"), roles[2]: mcp("Codex")}
            db = folder / "queue.db"
            bridge = Bridge(db)
            job = Job.create(project, "给阅读清单增加导出功能", [], roles, LEAD, routes)
            routes[roles[1]]["target"] = "ZCode"
            job = Job.load(job.path)
            self.assertEqual(job.state["opc_routes"][roles[1]], mcp("Codex"))
            roster = job.state["opc_roles"]
            configs = resolve_configs(job.state["opc_routes"], store, lambda: bridge)
            stages = []
            def api(name, model, key, prompt):
                self.assertEqual((name, model, key), ("GPT", "test-model", secret))
                self.assertNotIn(secret, prompt)
                ctx = json.loads(prompt.split("\n", 1)[1])
                self.assertEqual(ctx["opc_role"], LEAD)
                self.assertEqual(ctx["role_instructions"], roster[LEAD]["instructions"])
                self.assertEqual(set(ctx["opc_team"]), set(roles))
                if "阶段=派单" in prompt:
                    stages.append("plan")
                    return json.dumps({"message": "安排两位员工", "assignments": {
                        roles[1]: "写界面说明", roles[2]: "写接口说明"}})
                stages.append("review")
                self.assertEqual(set(ctx["worker_results"]), set(roles[1:]))
                return '{"message":"通过","approved":true,"checklist":["已检查两个文件"]}'
            stack.enter_context(patch("providers._api", api))
            host = Host(stack, db, "Codex")
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
            stack.callback(bridge.close)
            future = pool.submit(job.run, configs, lambda _: None, threading.Event())
            seen = set()
            for _ in range(2):
                task = host.claim()
                prompt = task["prompt"]
                ctx = json.loads(prompt.split("\n", 1)[1])
                actor = ctx["opc_role"]
                self.assertIn(actor, roles[1:])
                self.assertEqual(ctx["role_instructions"], roster[actor]["instructions"])
                self.assertEqual(ctx["role_skills"], roster[actor]["skills"])
                self.assertNotIn(secret, prompt)
                self.assertNotIn(profile, prompt)
                seen.add(actor)
                host.submit(task, message=f"{actor} 已交付", files={f"{actor}.md": "交付"})
            self.assertEqual(future.result(timeout=8), "待人工验收")
            self.assertEqual(seen, set(roles[1:]))
            self.assertEqual(stages, ["plan", "review"])
            for role in seen:
                self.assertTrue((job.path / "project" / (role + ".md")).is_file())
            self.assertNotIn(secret, (job.path / "state.json").read_text(encoding="utf-8"))

    def test_rebind_preserves_work_and_rejects_stale_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            engineer = "前端设计师"
            members = [LEAD, engineer]
            routes = {role: mcp("Codex") for role in members}
            job = Job.create(tmp, "验收", [], members, LEAD, routes)
            original = resolve_configs(routes, None, lambda: object())
            job.state["answers"]["0-派单"] = {LEAD: {"message": "plan", "assignments": {engineer: "do"}}}
            job.state["status"] = "已暂停"
            job.save()
            frozen = job.state["opc_roles"]
            job.rebind_opc({**routes, engineer: mcp("ZCode")})
            saved = Job.load(job.path)
            self.assertEqual(saved.state["opc_roles"], frozen)
            self.assertEqual(saved.state["answers"], job.state["answers"])
            with self.assertRaises(ValueError):
                saved.rebind_opc({**routes, "测试工程师": mcp("Codex")})
            with patch("engine.call") as call:
                self.assertEqual(saved.run(original, lambda _: None, threading.Event()), "受阻")
                call.assert_not_called()

    def test_invalid_routes_rejected_before_project_created(self):
        roles = [LEAD, "测试工程师"]
        for invalid in ({"kind":"api", "target":"sk-raw-api-secret"}, {**mcp("Codex"), "key": "secret"},
                        mcp("imaginary-host")):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    Job.create(tmp, "验收", [], roles, LEAD, {LEAD:mcp("Codex"), roles[1]:invalid})
                self.assertFalse((Path(tmp) / ".agentdesk").exists())
        with self.assertRaises(ValueError):
            checked_routes({LEAD:mcp("Codex")})

    def test_one_api_account_covers_plan_worker_handoff_and_review(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, OPC_SOLO_KEY="example-only"):
            folder = Path(tmp)
            (folder / "project").mkdir()
            store = AccountStore(folder / "accounts.db")
            uid = store.save_profile("共用", "Gemini", "model", key_env="OPC_SOLO_KEY")
            tech, qa = "后端开发员", "测试工程师"
            routes = {name: {"kind": "api", "target": uid} for name in (LEAD, tech, qa)}
            job = Job.create(folder / "project", "交接草稿给测试员工", [], list(routes), LEAD, routes)
            calls = []
            def model(name, model_id, key, prompt):
                self.assertEqual((name, model_id, key), ("Gemini", "model", "example-only"))
                ctx = json.loads(prompt.split("\n", 1)[1])
                actor = ctx["opc_role"]
                self.assertEqual(ctx["role_instructions"], job.state["opc_roles"][actor]["instructions"])
                calls.append(actor)
                if "阶段=派单" in prompt:
                    return json.dumps(dict(message="分工", assignments={tech: "写接口", qa: "写验收说明"}))
                if "阶段=验收复核" in prompt:
                    self.assertEqual(ctx["worker_results"][tech]["completed_by"], qa)
                    return '{"message":"验收通过","approved":true,"checklist":["已审阅接口和说明"]}'
                if actor == tech:
                    return json.dumps(dict(message="初稿已就绪", files={"api.txt": "contract"},
                        handoff={"to": qa, "summary": "检查接口草稿并补充边界用例"}))
                if ctx["assignment_owner"] == tech:
                    self.assertEqual(ctx["received_from"], tech)
                    self.assertEqual(ctx["files"]["api.txt"], "contract")
                    return '{"message":"交付边界用例","files":{"cases.txt":"boundary"}}'
                return '{"message":"交付验收说明","files":{"qa.txt":"acceptance"}}'
            configs = resolve_configs(routes, store, lambda: self.fail("API 模式不应访问 MCP 队列"))
            with patch("providers._api", model):
                result = job.run(configs, lambda _: None, threading.Event())
            self.assertEqual(result, "待人工验收")
            self.assertEqual(sorted(calls), sorted([LEAD, LEAD, tech, qa, qa]))
            self.assertEqual(sorted(p.name for p in (job.path / "project").iterdir()), ["api.txt", "cases.txt", "qa.txt"])

    def test_full_skill_survives_context_compaction(self):
        snapshot = skill_snapshot(ROLE_NAMES)
        role = "后端开发员"
        prompt = employee_prompt(role, snapshot, "阶段=实现，只输出 JSON。",
                                {"goal":"交付报表", "files":{"small.csv":"hello", "huge.csv":"x"*55000},
                                 "feedback":"f"*32000, "messages":["y"*20000]*3})
        self.assertLessEqual(len(prompt), DEFAULT_CONTEXT_CHARS)
        ctx = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(ctx["role_instructions"], snapshot[role]["instructions"])
        self.assertTrue(ctx["context_compacted"])
        self.assertNotIn("huge.csv", ctx["files"])
        self.assertEqual(ctx["files"]["small.csv"], "hello")
