import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cloud_daemon
from cloud_room import dispatch


class DetachedWorkers(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "rooms"
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        for name in ("cloud_daemon", "cloud_room", "cloud_worker", "providers", "extra_accounts", "engine", "handoff"):
            shutil.copyfile(Path(__file__).with_name(name + ".py"), self.runtime / (name + ".py"))
        # The production entry point still launches a detached interpreter. Replace only the model in its isolated copy.
        with (self.runtime / "providers.py").open("a", encoding="utf-8") as stream:
            stream.write("""
def call(agent, config, prompt):
    import json, time
    from pathlib import Path
    if config['model'] == 'error':
        raise RuntimeError(config['key'])
    for unused in range(160):
        if Path(__file__).with_name('continue').exists():
            break
        time.sleep(.05)
    return json.dumps({'message':'done', 'files':{agent+'.txt': 'project file'}})
""")
        self.team = ["GPT", "Gemini"]
        self.key = "test-secret-never-save"
        self.cfg = {m: dict(mode="api", model="fixture", key=self.key) for m in self.team}
        self.room = self.rpc("create", goal="test", members=self.team)["room"]

    def rpc(self, op, **fields):
        return dispatch(dict(op=op, room=getattr(self, "room", None), **fields), self.root)

    def wait_for(self, values, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.rpc("worker_status")
            if result["status"] in values:
                return result
            time.sleep(.075)
        self.fail("后台状态等待超时：" + str(result))

    def test_detached_worker_continues_after_starting_process_exits(self):
        self.assertEqual(self.rpc("worker_status")["status"], "idle")
        command = ["import json,sys", "from pathlib import Path", "from cloud_room import dispatch",
                   "print(json.dumps(dispatch(json.load(sys.stdin),Path(sys.argv[1]))))"]
        result = subprocess.run([sys.executable, "-c", ";".join(command), str(self.root)],
                                input=json.dumps(dict(op="start_worker",room=self.room,configs=self.cfg)),
                                text=True, capture_output=True, timeout=8, cwd=self.runtime, check=True)
        self.assertEqual(json.loads(result.stdout)["status"], "starting")
        try:
            self.assertEqual(self.wait_for({"running"})["members"], self.team)
            with self.assertRaisesRegex(ValueError, "已有"):
                self.rpc("start_worker", configs=self.cfg)
            (self.runtime / "continue").touch()
            self.wait_for({"complete"})
            self.assertEqual(set(self.rpc("export")["files"]), {"GPT.txt","Gemini.txt"})
            for p in self.root.rglob("*"):
                if p.is_file():
                    self.assertNotIn(self.key.encode(), p.read_bytes(), p.name)
        finally:
            self.rpc("stop_worker")
            self.wait_for({"stopped", "complete", "failed"})

    def test_failure_stop_and_restart(self):
        with patch.object(cloud_daemon, "RUNTIME", self.runtime):
            invalid = {"GPT": dict(mode="account", key=self.key, model="fixture")}
            with self.assertRaises(ValueError):
                self.rpc("start_worker", configs=invalid)
            self.rpc("start_worker", configs={"GPT": dict(self.cfg["GPT"], model="error")})
            failed = self.wait_for({"failed"})
            self.assertNotIn(self.key, str(failed))
            self.rpc("start_worker", configs=self.cfg)
            try:
                self.wait_for({"running"})
                self.assertEqual(self.rpc("stop_worker")["status"], "stopping")
                self.wait_for({"stopped"})
                self.assertEqual(self.rpc("export")["files"], {})
                (self.runtime / "continue").touch()
                self.rpc("start_worker", configs=self.cfg)
                self.wait_for({"complete"})
            finally:
                self.rpc("stop_worker")
                self.wait_for({"stopped", "complete", "failed"})
