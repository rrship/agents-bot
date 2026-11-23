import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from engine import Job
from team_modes import OPC, STANDARD, job_mode


class SavedModes(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.team = ["项目经理", "前端设计师"]
        self.routes = {name: {"kind": "mcp", "target": "Codex"} for name in self.team}

    def test_new_and_legacy_modes_round_trip_without_eager_disk_migration(self):
        for mode, args in ((STANDARD, []), (OPC, [self.team, self.team[0], self.routes])):
            with self.subTest(mode=mode):
                task = Job.create(self.root, "任务", [], *args, mode=mode)
                file = task.path / "state.json"
                data = json.loads(file.read_text("utf-8"))
                self.assertEqual(data.pop("mode"), mode)
                file.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
                original = file.read_bytes()
                loaded = Job.load(task.path)
                self.assertEqual(loaded.state["mode"], mode)
                self.assertEqual(file.read_bytes(), original)
                loaded.add_feedback("补充验收细节")
                self.assertEqual(json.loads(file.read_text("utf-8"))["mode"], mode)

    def test_unknown_and_inconsistent_mode_rejected_before_provider_or_filesystem_changes(self):
        for mode, args in ((STANDARD, [self.team, self.team[0], self.routes]), (OPC, []), ("future", [])):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                Job.create(self.root, "测试", [], *args, mode=mode)
        self.assertFalse((self.root / ".agentdesk").exists())
        task = Job.create(self.root, "任务", [], self.team, self.team[0], self.routes)
        file = task.path / "state.json"
        data = json.loads(file.read_text("utf-8"))
        del data["mode"], data["opc_roles"]
        file.write_text(json.dumps(data), "utf-8")
        with self.assertRaisesRegex(ValueError, "缺少"), patch("engine.call") as call:
            task.run({}, lambda _: None, threading.Event())
        call.assert_not_called()

    def test_standard_team_accepts_both_coordination_styles_without_opc_runtime(self):
        with patch.dict(sys.modules, {"opc_runtime": None, "opc_skills": None}):
            for lead in ("GPT", None):
                with self.subTest(lead=lead):
                    task = Job.create(self.root, "普通团队", [], ["GPT", "Gemini"], lead)
                    self.assertEqual(task.state["mode"], STANDARD)
                    with self.assertRaisesRegex(ValueError, "OPC"):
                        task.rebind_opc(self.routes)
                    with patch("engine.call") as call:
                        result = task.run({"GPT": {"opc_binding": self.routes[self.team[0]]}},
                                          lambda _: None, threading.Event())
                        self.assertEqual(result, "受阻")
                        call.assert_not_called()

    def test_incomplete_or_mixed_legacy_snapshots_raise(self):
        for state in ({"opc_roles": {}}, {"opc_routes": None, "opc_roles": {}},
                      {"mode": None}, {"mode": OPC}, {"mode": STANDARD, "opc_roles": {}, "opc_routes": {}},
                      {"opc_roles": {"员工": {}}, "opc_routes": {"其他": {}}}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                job_mode(state)


if __name__ == "__main__":
    unittest.main()
