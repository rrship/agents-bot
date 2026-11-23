import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import codex_native


SERVER = r'''
import json, sys
from pathlib import Path
mode, seen = sys.argv[1:3]
def emit(**packet):
    print(json.dumps(packet), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    with Path(seen).open('a', encoding='utf-8') as f:
        print(method, file=f)
    if method == 'initialize':
        emit(id=msg['id'], result={})
    elif method == 'thread/start':
        p = msg['params']
        assert p['sandbox'] == 'read-only' and p['ephemeral'] and p['approvalPolicy'] == 'never'
        assert list(Path(p['cwd']).iterdir()) == []
        emit(id=msg['id'], result={'thread': {'id': 'T'}})
    elif method == 'turn/start':
        assert msg['params']['input'][0] == {'type': 'text', 'text': '团队任务'}
        event = {'threadId': 'T', 'turnId': 'R'}
        emit(method='item/completed', params=dict(event, item={'type': 'agentMessage', 'phase': 'commentary', 'text': 'thinking'}))
        emit(id=msg['id'], result={'turn': {'id': 'R'}})
        if mode == 'complete':
            emit(method='item/completed', params=dict(event, item={'type':'agentMessage', 'phase':'final_answer','text':'{"message":"已完成"}'}))
            emit(method='item/completed', params=dict(event, turnId='other', item={'type':'agentMessage','text':'other turn'}))
            emit(method='turn/completed', params={'threadId':'T','turn':{'id':'R','status':'completed','items':[]}})
        if mode == 'fail':
            emit(method='turn/completed', params={'threadId':'T','turn':{'id':'R','status':'failed','error':{'message':'login required'}}})
    elif method == 'turn/interrupt':
        assert msg['params'] == {'threadId': 'T', 'turnId': 'R'}
'''


class CodexNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.script = Path(self.temp.name, "native_stub.py")
        self.script.write_text(SERVER, encoding="utf-8")
        self.log = Path(self.temp.name, "calls.log")

    def mock_command(self, mode):
        return patch.object(codex_native, "command", return_value=[sys.executable, "-X", "utf8", "-u",
                                                                  str(self.script), mode, str(self.log)])

    def test_actual_stdio_turn(self):
        with self.mock_command("complete"):
            self.assertEqual(json.loads(codex_native.run("团队任务", timeout=10)), {"message":"已完成"})
        self.assertEqual(self.log.read_text().splitlines(), ["initialize", "initialized", "thread/start", "turn/start"])

    def test_cancel_interrupts_only_started_turn(self):
        stop = threading.Event()
        reached = threading.Event()
        def pause():
            for _ in range(100):
                if self.log.exists() and "turn/start" in self.log.read_text():
                    reached.set()
                    # Give the app-server its turn/start response before cancellation.
                    time.sleep(.1)
                    stop.set()
                    return
                time.sleep(.02)
        monitor = threading.Thread(target=pause)
        with self.mock_command("waiting"):
            monitor.start()
            try:
                with self.assertRaises(InterruptedError):
                    codex_native.run("团队任务", stop=stop, timeout=6)
            finally:
                monitor.join()
        self.assertTrue(reached.is_set())
        self.assertEqual(self.log.read_text().splitlines()[-1], "turn/interrupt")

    def test_failed_turn_surfaces_error(self):
        with self.mock_command("fail"):
            with self.assertRaisesRegex(RuntimeError, "login required"):
                codex_native.run("团队任务", timeout=5)
