import io
import json
import os
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import providers as p


class Providers(unittest.TestCase):
    def test_openai_response_and_headers(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "完成"}]}]}
        with patch.object(p, "_open", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            self.assertEqual("完成", p.call("GPT", {"mode":"api","model":"test","key":"secret"}, "你好"))
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data)["input"], "你好")

    def test_gemini_excludes_thoughts(self):
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text":"internal","thought":True},{"text":"输出"}]}}]}
        with patch.object(p, "_open", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            self.assertEqual("输出", p.call("Gemini", {"mode":"api","model":"models/test","key":"secret"}, "你好"))
        request = send.call_args.args[0]
        self.assertNotIn("secret", request.full_url)
        self.assertTrue(request.full_url.endswith("/models/test:generateContent"))

    def test_incomplete_response_is_error(self):
        with patch.object(p, "_open", return_value=io.BytesIO(b'{"status":"incomplete","output":[]}')):
            with self.assertRaises(p.ProviderError):
                p.call("GPT", {"mode":"api","model":"test","key":"secret"}, "hi")

    def test_api_key_only_from_matching_environment(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY":"only"}, clear=True):
            with self.assertRaisesRegex(p.ProviderError, "OPENAI_API_KEY"):
                p.call("GPT", {"mode":"api","model":"test"}, "hi")

    def test_cli_missing_and_invalid_model(self):
        with patch.object(p.shutil, "which", return_value=None):
            with self.assertRaisesRegex(p.ProviderError, "Gemini CLI"):
                p.call("Gemini", {"mode":"account"}, "hi")
        with self.assertRaises(p.ProviderError):
            p.call("GPT", {"mode":"account","model":"--help"}, "hi")

    def test_both_account_adapters(self):
        def fake_run(argv, **kw):
            self.assertEqual(kw["input"], "你好")
            self.assertNotIn("OPENAI_API_KEY", kw["env"])
            self.assertNotIn("GEMINI_API_KEY", kw["env"])
            self.assertNotIn("你好", argv)
            if argv[0] == "codex":
                Path(argv[argv.index("-o")+1]).write_text("GPT结果", encoding="utf-8")
                return CompletedProcess(argv, 0, "", "")
            settings = json.loads((Path(kw["cwd"])/".gemini/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["security"]["auth"]["selectedType"], "oauth-personal")
            return CompletedProcess(argv, 0, '{"response":"Gemini结果"}', "")
        with patch.object(p, "_cli", side_effect=lambda agent: ["codex" if agent=="GPT" else "gemini"]), patch.object(p.subprocess,"run",side_effect=fake_run):
            self.assertEqual(p.call("GPT", {"mode":"account"}, "你好"), "GPT结果")
            self.assertEqual(p.call("Gemini", {"mode":"account"}, "你好"), "Gemini结果")


if __name__ == "__main__":
    unittest.main()
