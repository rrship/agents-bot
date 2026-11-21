import io
import json
import os
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import providers as p


class ProviderExtensions(unittest.TestCase):
    def test_api_envelopes(self):
        cases = {
            "Claude": ("https://api.anthropic.com/v1/messages", {
                "stop_reason": "end_turn", "content": [
                    {"type": "thinking", "thinking": "private"}, {"type": "text", "text": "answer"}]}),
            "DeepSeek": ("https://api.deepseek.com/chat/completions", None),
            "Kimi": ("https://api.moonshot.cn/v1/chat/completions", None),
            "GLM": ("https://open.bigmodel.cn/api/paas/v4/chat/completions", None),
        }
        for agent, (url, body) in cases.items():
            with self.subTest(agent=agent):
                body = body or {"choices": [{"finish_reason": "stop", "message": {
                    "reasoning_content": "private", "content": "answer"}}]}
                with patch.dict(os.environ, {p.ENV_KEYS[agent]: "test-key"}, clear=True), patch.object(
                        p, "_open", return_value=io.BytesIO(json.dumps(body).encode())) as opened:
                    self.assertEqual("answer", p.call(agent, {"mode": "api", "model": "m"}, "task"))
                request = opened.call_args.args[0]
                self.assertEqual(request.full_url, url)
                self.assertEqual(json.loads(request.data)["messages"], [{"role": "user", "content": "task"}])
                if agent == "Claude":
                    self.assertEqual("test-key", request.get_header("X-api-key"))
                    self.assertTrue(request.get_header("Anthropic-version"))
                else:
                    self.assertEqual("Bearer test-key", request.get_header("Authorization"))

    def test_new_account_clients(self):
        prompt = "中文\n" * 20000

        def run(argv, **kw):
            self.assertNotIn(prompt, argv)
            self.assertNotIn("MOONSHOT_API_KEY", kw["env"])
            self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])
            if argv[0] == "Kimi":
                self.assertEqual(kw["input"], "")
                self.assertIn("AgentDesk", kw["env"]["KIMI_CODE_HOME"])
                descriptor = Path(argv[argv.index("--agent-file")+1]).read_text(encoding="utf-8")
                self.assertIn("tools: []", descriptor)
                self.assertIn(json.dumps(prompt, ensure_ascii=False), descriptor)
                self.assertEqual("kimi-code/kimi-for-coding", argv[argv.index("-m")+1])
                reply = '{"role":"assistant","content":"answer"}\n{"type":"session.resume_hint"}\n'
            else:
                self.assertEqual(kw["input"], prompt)
                self.assertEqual(argv[argv.index("--tools")+1], "")
                self.assertIn("--restricted", argv)
                reply = '{"subtype":"success","is_error":false,"result":"answer"}'
            return CompletedProcess(argv, 0, reply, "")

        for name in ("Claude", "Kimi"):
            with self.subTest(name=name), patch.object(p, "_cli", return_value=[name]), patch.object(
                    p.subprocess, "run", side_effect=run):
                self.assertEqual("answer", p.call(name, {"mode": "account"}, prompt))

    def test_login_and_extra_routing(self):
        for agent, expected in (("Claude", ["Claude", "auth", "login"]), ("Kimi", ["Kimi", "login"])):
            with patch.object(p, "_cli", return_value=[agent]), patch.object(p.subprocess, "Popen") as popen:
                p.launch_login(agent)
                self.assertEqual(expected, popen.call_args.args[0])
        for agent in p.EXTRA_ACCOUNTS:
            with patch.object(p, "extra_login") as login, patch.object(p, "extra_call", return_value="ok") as call:
                p.launch_login(agent)
                login.assert_called_once_with(agent)
                self.assertEqual("ok", p.call(agent, {"mode": "account"}, "test"))
                call.assert_called_once_with(agent, "", "test")

    def test_truncated_api_outputs(self):
        for agent, body in (("Claude", {"stop_reason": "max_tokens", "content": []}),
                            ("DeepSeek", {"choices": [{"finish_reason": "length"}]})):
            with patch.object(p, "_open", return_value=io.BytesIO(json.dumps(body).encode())):
                with self.assertRaisesRegex(p.ProviderError, "未生成完整"):
                    p.call(agent, {"mode": "api", "model": "test", "key": "x"}, "task")
