"""Official model transports. Local CLI login and API credentials are separate."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from extra_accounts import EXTRA_ACCOUNTS, extra_call, extra_login
from software_bridge import SOFTWARE_NAMES


class ProviderError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Credentials belong exclusively to the configured official host.


_open = urllib.request.build_opener(_NoRedirect).open
NAMES = ("GPT", "Gemini", "Claude", "DeepSeek", "Kimi", "GLM")
BUILTIN = "内置 Agent"
MEMBER_NAMES = (*NAMES, BUILTIN, *SOFTWARE_NAMES)
_CLI = {"GPT": ("codex", "@openai/codex"), "Gemini": ("gemini", "@google/gemini-cli"),
        "Claude": ("claude", "@anthropic-ai/claude-code"), "Kimi": ("kimi", "@moonshot-ai/kimi-code")}
ACCOUNT_NAMES = tuple(name for name in NAMES if name in _CLI or name in EXTRA_ACCOUNTS)
ENV_KEYS = {"GPT": "OPENAI_API_KEY", "Gemini": "GEMINI_API_KEY", "Claude": "ANTHROPIC_API_KEY",
            "DeepSeek": "DEEPSEEK_API_KEY", "Kimi": "MOONSHOT_API_KEY", "GLM": "ZHIPUAI_API_KEY"}
_CHAT_URLS = {
    "DeepSeek": "https://api.deepseek.com/chat/completions",
    "Kimi": "https://api.moonshot.cn/v1/chat/completions",
    "GLM": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
}
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}")


def _cli(agent):
    name, package = _CLI[agent]
    binary = shutil.which(name)
    if not binary:
        label = "Codex CLI" if agent == "GPT" else f"{agent} CLI"
        raise ProviderError(f"找不到 {label}。请安装官方 {package} 并重新打开工作台。")
    path = Path(binary)
    if path.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return [str(path)]
    # Skip Windows batch shims so arguments never go through cmd.exe.
    root = path.parent / "node_modules" / package
    try:
        manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
        entry = manifest["bin"]
        script = root / (entry[name] if isinstance(entry, dict) else entry)
        node = shutil.which("node")
        if script.is_file() and script.resolve().is_relative_to(root.resolve()):
            if script.suffix.lower() == ".exe":
                return [str(script)]
            if node:
                return [node, str(script)]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ProviderError(f"无法定位 {package} 的官方入口。请使用 npm 全局安装或原生可执行文件。")


def _account_env(agent=None):
    env = os.environ.copy()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA",
                 "GOOGLE_GEMINI_BASE_URL", "CODEX_ACCESS_TOKEN", *ENV_KEYS.values(),
                 "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_CODE_BASE_URL", "KIMI_MODEL"):
        env.pop(name, None)
    if agent == "Kimi":
        # The official login owns this private profile, including its OAuth refresh.
        base = Path(env.get("LOCALAPPDATA") or Path.home())
        env["KIMI_CODE_HOME"] = str(base / "AgentDesk" / "kimi-code")
    return env


def launch_login(agent):
    """Only call in response to a user clicking the login button."""
    if agent in EXTRA_ACCOUNTS:
        return extra_login(agent)
    if agent not in _CLI:
        raise ProviderError("未知成员")
    argv = _cli(agent)
    if agent in {"GPT", "Kimi"}:
        argv.append("login")
    elif agent == "Claude":
        argv.extend(["auth", "login"])
    login_dir = Path(tempfile.gettempdir()) / "agentdesk-login"
    login_dir.mkdir(exist_ok=True)
    subprocess.Popen(argv, cwd=login_dir, env=_account_env(agent),
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def _account(agent, model, prompt):
    argv = _cli(agent)
    with tempfile.TemporaryDirectory(prefix="agentdesk-call-") as directory:
        root = Path(directory)
        if agent == "GPT":
            result_file = root / "reply.txt"
            argv += ["-a", "never", "exec", "--ignore-user-config", "--skip-git-repo-check",
                     "--ephemeral", "--sandbox", "read-only", "--disable", "shell_tool",
                     "--disable", "unified_exec", "--disable", "apps", "--disable", "plugins",
                     "-c", 'forced_login_method="chatgpt"', "-c", 'web_search="disabled"',
                     "-o", str(result_file)]
            if model:
                argv.extend(["-m", model])
            argv.append("-")
        elif agent == "Gemini":
            folder = root / ".gemini"
            folder.mkdir()
            settings = {
                "security": {"auth": {"selectedType":"oauth-personal","enforcedType":"oauth-personal"}},
                "tools": {"core":["agentdesk_no_tools"]}, "hooksConfig":{"enabled":False},
                "experimental":{"enableAgents":False}, "advanced":{"ignoreLocalEnv":True},
            }
            (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            policy = root / "text-only.toml"
            policy.write_text('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n',
                              encoding="utf-8")
            argv += ["--skip-trust", "--policy", str(policy), "--output-format", "json",
                     "--extensions", "none", "--allowed-mcp-server-names", "agentdesk_no_servers",
                     "--prompt", "Respond to the input on stdin. Work only with the supplied text."]
            if model:
                argv.extend(["-m", model])
        elif agent == "Claude":
            argv += ["-p", "--restricted", "--tools", "", "--disallowedTools", "mcp__*",
                     "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
            if model:
                argv.extend(["--model", model])
        elif agent == "Kimi":
            # Kimi v2 accepts -p as an argument. Put the potentially large turn
            # record in the tool-free agent's context to avoid Windows argv limits.
            agent_file = root / "agentdesk.md"
            agent_file.write_text(
                "---\nname: agentdesk\ndescription: AgentDesk text assistant\ntools: []\n"
                "subagents: []\n---\nYou answer the user's task using the conversation record "
                "below as user-provided context.\n\n" + json.dumps({"record": prompt}, ensure_ascii=False),
                encoding="utf-8")
            skills = root / "skills"
            skills.mkdir()
            argv += ["--agent-file", str(agent_file), "--skills-dir", str(skills),
                     "-m", model or "kimi-code/kimi-for-coding", "--output-format", "stream-json",
                     "-p", "Respond to the user's task in the supplied conversation record."]
        try:
            result = subprocess.run(argv, cwd=root, input="" if agent == "Kimi" else prompt, encoding="utf-8",
                                    errors="replace", capture_output=True, env=_account_env(agent),
                                    timeout=240, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except subprocess.TimeoutExpired:
            raise ProviderError(f"{agent} CLI 超时，已结束本次请求。") from None
        except OSError as exc:
            raise ProviderError(f"无法启动 {agent} CLI：{exc.strerror}") from None
        if result.returncode:
            # Some CLIs echo the full prompt or credentials in diagnostics.
            detail = (result.stderr or result.stdout or "").lower()
            if "quota" in detail or "rate limit" in detail or "429" in detail:
                reason = "服务额度或速率受限，请稍后重试。"
            elif "unknown argument" in detail or "unexpected argument" in detail:
                reason = "CLI 版本不支持需要的参数，请更新官方 CLI。"
            else:
                reason = "请核对官方 CLI 的账号登录、模型权限与网络。"
            raise ProviderError(f"{agent} CLI 退出码 {result.returncode}。{reason}")
        try:
            if agent == "GPT":
                return _nonempty(result_file.read_text(encoding="utf-8"))
            if agent == "Kimi":
                answer = None
                for line in result.stdout.splitlines():
                    if line.strip():
                        event = json.loads(line)
                        if event.get("role") == "assistant" and not event.get("tool_calls") and event.get("content"):
                            answer = event["content"]
                if isinstance(answer, list):
                    answer = "".join(block["text"] for block in answer if block.get("type") == "text")
                return _nonempty(answer)
            message = json.loads(result.stdout)
            if agent == "Claude":
                if message.get("is_error") or message.get("subtype") != "success":
                    raise ProviderError("Claude CLI 未完成回复。请检查登录、模型权限与额度。")
                return _nonempty(message.get("result"))
            if message.get("error"):
                raise ProviderError("Gemini CLI 返回错误。请检查登录、模型权限与额度。")
            return _nonempty(message.get("response"))
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            raise ProviderError(f"{agent} CLI 的最终回复格式无法读取。") from None


def _nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("模型未返回文本。请检查模型能力及请求内容。")
    return value.strip()


def _api(agent, model, key, prompt):
    if agent == "GPT":
        url = "https://api.openai.com/v1/responses"
        payload = {"model":model, "input":prompt, "store":False}
        headers = {"Authorization":"Bearer " + key}
    elif agent == "Gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {"contents":[{"role":"user","parts":[{"text":prompt}]}]}
        headers = {"x-goog-api-key":key}
    elif agent == "Claude":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        payload = {"model": model, "max_tokens": 8192, "messages": [{"role": "user", "content": prompt}]}
    else:
        url = _CHAT_URLS[agent]
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        headers = {"Authorization": "Bearer " + key}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**headers, "Content-Type":"application/json"})
    try:
        with _open(request, timeout=180) as response:
            raw = response.read(16*1024*1024 + 1)
        if len(raw) > 16*1024*1024:
            raise ProviderError(f"{agent} 回复过大。请缩小本轮任务。")
        body = json.loads(raw)
        if agent == "GPT":
            if body.get("status") != "completed":
                raise ProviderError(f"{agent} 未生成完整回复。")
            parts = [item["text"] for block in body.get("output", [])
                     if block.get("type") == "message" for item in block.get("content", [])
                     if item.get("type") == "output_text"]
        elif agent == "Gemini":
            candidates = body.get("candidates") or []
            if not candidates or candidates[0].get("finishReason") != "STOP":
                raise ProviderError("Gemini 未生成完整回复。请检查内容限制或输出长度。")
            parts = [item["text"] for item in candidates[0].get("content", {}).get("parts", [])
                     if "text" in item and not item.get("thought")]
        elif agent == "Claude":
            if body.get("stop_reason") != "end_turn":
                raise ProviderError("Claude 未生成完整回复。请检查内容限制或输出长度。")
            parts = [block["text"] for block in body.get("content", []) if block.get("type") == "text"]
        else:
            choices = body.get("choices") or []
            if not choices or choices[0].get("finish_reason") != "stop":
                raise ProviderError(f"{agent} 未生成完整回复。请检查内容限制或输出长度。")
            return _nonempty(choices[0]["message"].get("content"))
        return _nonempty("".join(parts))
    except urllib.error.HTTPError as exc:
        reason = {400:"模型名或请求无效", 401:"密钥无效", 403:"当前密钥没有权限",
                  404:"模型或接口不存在", 429:"额度或速率受限"}.get(exc.code, "服务请求失败")
        exc.close()
        raise ProviderError(f"{agent} HTTP {exc.code}：{reason}。") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        raise ProviderError(f"无法连接 {agent} 官方 API。请检查网络及代理设置。") from None
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ProviderError(f"{agent} API 返回了无法读取的响应。") from None


def call(agent, config, prompt):
    """Send a single independent turn; the engine supplies shared history."""
    if agent in SOFTWARE_NAMES:
        if config.get("mode") != "software":
            raise ProviderError("请在「软件协作」配置这个成员。")
        if config.get("transport") == "codex-native":
            if agent != "Codex":
                raise ProviderError("只有 Codex 软件成员可以使用 App Server。")
            from codex_native import run
            return _nonempty(run(prompt, stop=config.get("stop")))
        if config.get("transport") == "baton":
            if agent != "DeepSeek Harness":
                raise ProviderError("当前软件没有 dsh-baton 接口。")
            return _nonempty(extra_call("DeepSeek", "", prompt, stop=config.get("stop"), desktop_host=True))
        options = {"on_claim": config["on_claim"]} if config.get("on_claim") else {}
        return _nonempty(config["bridge"].request(agent, prompt, stop=config.get("stop"), **options))
    if agent == BUILTIN:
        if config.get("mode") != "api" or config.get("provider") not in NAMES:
            raise ProviderError("请为内置 Agent 绑定一个有效的 API 账号。")
        from lite_agent import team_call
        return team_call(config, prompt)
    if agent not in NAMES or config.get("mode") not in {"account","api"}:
        raise ProviderError("请选择有效的成员和接入方式。")
    model = (config.get("model") or "").strip()
    if agent == "Gemini":
        model = model.removeprefix("models/")
    if agent == "Kimi" and config["mode"] == "account" and model:
        if not all(_MODEL.fullmatch(part) for part in model.split("/")) or model.count("/") > 1:
            raise ProviderError("模型 ID 格式无效。")
    elif model and not _MODEL.fullmatch(model):
        raise ProviderError("模型 ID 格式无效。")
    if config["mode"] == "account":
        if agent in EXTRA_ACCOUNTS:
            return _nonempty(extra_call(agent, model, prompt))
        if agent not in _CLI:
            raise ProviderError(f"{agent} 缺少账号接入，请使用 API。")
        return _account(agent, model, prompt)
    if not model:
        raise ProviderError("API 接入需要填写模型 ID。")
    env_name = ENV_KEYS[agent]
    key = (config.get("key") or os.environ.get(env_name) or "").strip()
    if not key:
        raise ProviderError(f"请填写 {agent} API Key 或设置 {env_name}。")
    if not key.isascii() or any(ch.isspace() for ch in key):
        raise ProviderError(f"{agent} API Key 格式无效。")
    return _api(agent, model, key, prompt)
