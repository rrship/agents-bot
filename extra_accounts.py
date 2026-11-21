"""Use the locally signed-in Harness and ZCode clients."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

EXTRA_ACCOUNTS = {"DeepSeek": "DeepSeek Harness / dsh-baton", "GLM": "ZCode CLI"}


def command(agent):
    name = "dsh-baton" if agent == "DeepSeek" else "zcode"
    found = shutil.which(name)
    if found and Path(found).suffix.lower() not in {".bat", ".cmd", ".ps1"}:
        return [found]
    node = shutil.which("node")
    candidates = []
    if agent == "DeepSeek":
        if found:
            candidates.append(Path(found).parent / "node_modules/dsh-baton-mcp/src/cli.mjs")
        candidates.append(Path.home() / ".dsh-baton/app/src/cli.mjs")
    elif found:
        candidates.append(Path(found).parent / "node_modules/@zcode/cli/dist/zcode.cjs")
    for script in candidates:
        if node and script.is_file():
            return [node, str(script)]
    raise RuntimeError(f"找不到 {name}。请参阅使用说明，安装客户端并将命令加入 PATH。")


def extra_login(agent):
    if agent == "DeepSeek":
        try:
            os.startfile("dsh://open")
        except OSError as exc:
            raise RuntimeError("请打开 DeepSeek Harness 桌面客户端，登录并启用 dsh-baton-host。") from exc
    elif agent == "GLM":
        subprocess.Popen(command(agent) + ["login", "bigmodel"], cwd=tempfile.gettempdir(),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        raise ValueError("未知账号渠道")


def run_json(argv, workdir, prompt=None, timeout=240):
    from providers import _account_env
    result = subprocess.run(argv, cwd=workdir, env=_account_env(), input=prompt or "",
                            capture_output=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"客户端退出码 {result.returncode}；请检查登录、插件和当前模型。")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("客户端未返回预期 JSON，请核对客户端版本。") from None
    if not isinstance(data, dict):
        raise RuntimeError("客户端结果格式有误。")
    return data


def extra_call(agent, model, prompt):
    if model:
        raise ValueError(f"请在 {EXTRA_ACCOUNTS[agent]} 内选择模型，并留空工作台的账号模型栏。")
    argv = command(agent)
    with tempfile.TemporaryDirectory(prefix="agentdesk-account-") as temp:
        if agent == "DeepSeek":
            request = "仅根据以下文字返回最终回复。请勿运行命令或编辑文件。\n" + prompt
            sent = run_json(argv + ["send", "--cwd", temp, "-"], temp, request, 45)
            run_id = sent.get("run_id")
            if not isinstance(run_id, str) or not run_id or run_id.startswith("-"):
                raise RuntimeError("dsh-baton 未返回 run_id，请检查 Harness 的插件状态。")
            try:
                result = run_json(argv + ["wait", run_id, "--timeout", "240"], temp, timeout=255)
                if result.get("state") != "completed" or result.get("timed_out"):
                    raise RuntimeError(f"Harness 未完成本轮，请在客户端查看任务 {run_id}。")
                text = result.get("final_text")
            except Exception:
                # Each program turn owns exactly one run; stop it before a later retry.
                try:
                    run_json(argv + ["cancel", run_id], temp, timeout=15)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    pass
                raise
        elif agent == "GLM":
            # A small fixed argv also supports project snapshots exceeding the Windows argv limit.
            Path(temp, "request.txt").write_text(prompt, encoding="utf-8")
            result = run_json(argv + ["--prompt", "Read request.txt from the current directory. "
                              "Respond to its contents as text. Do not edit files or run commands.",
                              "--output-format", "json"], temp)
            text = result.get("response")
        else:
            raise ValueError("未知账号渠道")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"{agent} 未返回最终回复。")
        return text.strip()
