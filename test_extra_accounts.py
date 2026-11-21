import json
from pathlib import Path
import unittest
from unittest.mock import patch

from extra_accounts import extra_call


class AccountBridgeTests(unittest.TestCase):
    @patch("extra_accounts.command", return_value=["baton"])
    def test_harness_receipt_and_final_text(self, _):
        completed = {"state": "completed", "final_text": '{"message":"done"}'}
        with patch("extra_accounts.run_json", side_effect=[{"run_id": "run-42"}, completed]) as runner:
            self.assertEqual(json.loads(extra_call("DeepSeek", "", "任务"))["message"], "done")
            send, wait = runner.call_args_list
            self.assertEqual(send.args[0][-1], "-")
            self.assertIn("任务", send.args[2])
            self.assertEqual(wait.args[0][1:3], ["wait", "run-42"])

    @patch("extra_accounts.command", return_value=["baton"])
    def test_wait_timeout_cancels_only_its_run(self, _):
        with patch("extra_accounts.run_json", side_effect=[{"run_id": "x"}, {"state":"running","timed_out":True}, {}]) as runner:
            with self.assertRaisesRegex(RuntimeError, "x"):
                extra_call("DeepSeek", "", "hello")
            self.assertEqual(runner.call_args.args[0], ["baton", "cancel", "x"])

    @patch("extra_accounts.command", return_value=["zcode"])
    def test_zcode_large_unicode_prompt_and_response(self, _):
        prompt = "任务" * 50000
        def verify(argv, cwd):
            self.assertLess(len(str(argv)), 1000)
            self.assertEqual(Path(cwd, "request.txt").read_text(encoding="utf-8"), prompt)
            return {"response": "完成"}
        with patch("extra_accounts.run_json", side_effect=verify):
            self.assertEqual(extra_call("GLM", "", prompt), "完成")

    def test_desktop_model_control(self):
        for name in ("GLM", "DeepSeek"):
            with self.assertRaisesRegex(ValueError, "留空"):
                extra_call(name, "arbitrary", "test")
