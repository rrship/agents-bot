import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cloud_ssh import BOOTSTRAP, RUNTIME_FILES, CloudSSH, endpoint, runtime_bundle


class CloudTransportTests(unittest.TestCase):
    def test_endpoint_and_ssh_command(self):
        self.assertEqual(endpoint("[2001:db8::3]", "alice", "2222"), ("2001:db8::3", "alice", 2222))
        for host in ("-oProxyCommand=test", "valid.host/path", "host name", "u@host", "host;echo"):
            with self.assertRaises(ValueError):
                endpoint(host, "alice", 22)
        for user, port in (("-V", 22), ("a@b", 22), ("x", 0), ("x", 65536)):
            with self.assertRaises(ValueError):
                endpoint("example.net", user, port)
        with patch("cloud_ssh.shutil.which", return_value="ssh.exe"):
            cmd = CloudSSH("example.net", "alice", 2222)._command()
        self.assertIn("StrictHostKeyChecking=ask", cmd)
        self.assertEqual(cmd[-7:-1], ["-p", "2222", "-l", "alice", "--", "example.net"])
        self.assertNotIn("alice", cmd[-1])
        with tempfile.TemporaryDirectory() as folder:
            identity = Path(folder) / "key with spaces"
            identity.write_bytes(b"fixture")
            with patch("cloud_ssh.shutil.which", return_value="ssh.exe"):
                cmd = CloudSSH("example.net", "alice", identity_file=identity)._command()
            self.assertEqual(cmd[cmd.index("-i")+1], str(identity.resolve()))
            self.assertIn("IdentitiesOnly=yes", cmd)
            self.assertNotIn("fixture", str(cmd))

    def test_versioned_install_is_atomic_and_rejects_unexpected_modules(self):
        payload, digest = runtime_bundle()
        self.assertEqual(set(json.loads(payload)), set(RUNTIME_FILES))
        self.assertEqual(digest, hashlib.sha256(payload.rstrip(b"\n")).hexdigest())
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, HOME=folder, USERPROFILE=folder)
            def launch(data):
                return subprocess.run([sys.executable, "-I", "-u", "-c", BOOTSTRAP], env=env,
                                      input=data, capture_output=True, timeout=20)
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                sessions = list(pool.map(launch, [payload, payload]))
            for session in sessions:
                self.assertEqual(session.returncode, 0, session.stderr.decode(errors="replace"))
                self.assertEqual(json.loads(session.stdout)["runtime"], digest)
            versions = Path(folder) / ".agents-bot" / "runtime"
            self.assertEqual([p.name for p in versions.iterdir()], [digest])
            cache = versions / digest
            for name in RUNTIME_FILES:
                self.assertEqual((cache / name).read_bytes(), Path(__file__).with_name(name).read_bytes())
            bad = json.loads(payload)
            bad["other.py"] = "YQ=="
            self.assertNotEqual(launch(json.dumps(bad).encode()+b"\n").returncode, 0)
            (cache / "cloud_room.py").write_bytes(b"# corrupted")
            self.assertNotEqual(launch(payload).returncode, 0)

    def local_transport(self):
        session = CloudSSH("localhost", "tester")
        # Exercise the actual bootstrap, request/reply pipes and persistent server
        # subprocess, substituting only the SSH network hop.
        session._command = lambda: [sys.executable, "-I", "-u", "-c", BOOTSTRAP]
        with patch("cloud_ssh.subprocess.CREATE_NEW_CONSOLE", 0, create=True):
            session.connect()
        self.addCleanup(session.close)
        return session

    def test_service_pipes_concurrent_rpc_close_and_reconnect(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, AGENTSBOT_ROOMS=folder,
                                                                HOME=folder, USERPROFILE=folder):
            host = self.local_transport()
            guest = self.local_transport()
            room = host.rpc("create", goal="保持中文", members=["A", "B"], files={"说明.md": "初稿"})["room"]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(host.rpc, "status", room=room)
                second = pool.submit(host.rpc, "export", room=room)
                self.assertEqual(first.result()["goal"], "保持中文")
                self.assertEqual(second.result()["files"], {"说明.md": "初稿"})
            with self.assertRaises(ValueError):
                guest.rpc("status", room="invalid")
            self.assertEqual(guest.rpc("status", room=room)["members"], ["A","B"])
            host.close()
            with self.assertRaises(ConnectionError):
                host.rpc("status", room=room)

    def test_close_interrupts_waiting_authentication(self):
        session = CloudSSH("example.net", "tester")
        session._command = lambda: [sys.executable,"-c","import time; time.sleep(120)"]
        started = threading.Event()
        exchange = session._exchange
        def waiting(*args):
            started.set()
            return exchange(*args)
        session._exchange = waiting
        with patch("cloud_ssh.subprocess.CREATE_NEW_CONSOLE", 0, create=True):
            with concurrent.futures.ThreadPoolExecutor() as pool:
                ticket = pool.submit(session.connect)
                try:
                    self.assertTrue(started.wait(5))
                finally:
                    session.close()
                with self.assertRaises(ConnectionError):
                    ticket.result(timeout=5)
