import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mobile import Monitor, device_key, supported_address, remember_address, saved_address


class PhoneMonitorTests(unittest.TestCase):
    def test_monitor_preference(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp):
            self.assertEqual(saved_address(), "")
            remember_address("100.64.3.9")
            self.assertEqual(saved_address(), "100.64.3.9")
            remember_address("")
            self.assertEqual(saved_address(), "")

    def test_one_time_invitation(self):
        monitor = Monitor("127.0.0.1")
        self.addCleanup(monitor.close)
        def invite_request():
            url, token = monitor.invite_link().split("#")
            return Request(url, method="POST", headers={"Authorization": "Bearer " + token})
        def rejected(req):
            with self.assertRaises(HTTPError) as denied:
                urlopen(req, timeout=2)
            with denied.exception as res:
                self.assertEqual(res.code, 403)
        req = invite_request()
        with urlopen(req, timeout=2) as result:
            self.assertEqual(result.read().decode(), monitor.link.split("#")[1])
        rejected(req)
        req = invite_request()
        monitor.cancel_invite()
        rejected(req)
        req = invite_request()
        with patch("mobile.time.monotonic", return_value=monitor.deadline + 1):
            rejected(req)
        stale = invite_request()
        invite_request()
        rejected(stale)

    def test_private_interfaces(self):
        for ip in ("10.0.0.1", "172.31.0.2", "192.168.2.1", "100.127.255.254"):
            self.assertTrue(supported_address(ip))
        for ip in ("0.0.0.0", "100.128.0.1", "127.0.0.1", "169.254.0.1", "1.1.1.1", "localhost"):
            self.assertFalse(supported_address(ip))

    def test_binding_restart_and_revocation(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp):
            original = device_key()
            self.assertEqual(original, device_key())
            for key, revoked in [(original, False), (device_key(reset=True), True)]:
                monitor = Monitor("127.0.0.1", key)
                try:
                    origin = monitor.link.split("#")[0]
                    with urlopen(origin, timeout=2) as page:
                        self.assertIn("协作动态", page.read().decode())
                    def request(token):
                        return Request(origin + "progress", headers={"Authorization": "Bearer " + token})
                    monitor.publish("任务", "运行中", ["GPT", "Kimi"], "GPT", ["已分工"])
                    with urlopen(request(key), timeout=2) as response:
                        state = json.load(response)
                    self.assertEqual(state, dict(goal="任务", status="运行中", members=["GPT", "Kimi"],
                                                 leader="GPT", events=["已分工"]))
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(request(original if revoked else "invalid"), timeout=2)
                    self.assertEqual(denied.exception.code, 403)
                    denied.exception.close()
                finally:
                    monitor.close()
