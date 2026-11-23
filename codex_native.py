"""One isolated Codex App Server turn over the desktop runtime's stdio protocol."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time


def command():
    binary = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if binary:
        return [binary]
    # Windows does not always add the desktop app's bundled app-server to PATH.
    if os.name == "nt":
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        try:
            found = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                    "(Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1).InstallLocation"],
                                   capture_output=True, text=True, timeout=8,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            if found.returncode == 0 and found.stdout.strip():
                bundled = Path(found.stdout.strip()) / "app/resources/codex.exe"
                if bundled.is_file():
                    return [str(bundled)]
        except (OSError, subprocess.SubprocessError):
            pass
        shim = shutil.which("codex.cmd")
        if shim:
            root = Path(shim).parent / "node_modules/@openai/codex"
            for pattern in ("node_modules/@openai/codex-win32-*/vendor/*/codex/codex.exe",
                            "vendor/*-pc-windows-msvc/codex/codex.exe"):
                exe = next(root.glob(pattern), None)
                if exe is not None:
                    return [str(exe)]
    raise RuntimeError("找不到 Codex App Server。请安装 Codex，并在 Codex 中登录；也可以切换为 MCP 会话。")


class _Connection:
    def __init__(self, child, stop, deadline):
        self.child = child
        self.stop = stop
        self.deadline = deadline
        self.messages = queue.Queue()
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.child.stdout:
                self.messages.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.messages.put(None)

    def send(self, obj):
        try:
            self.child.stdin.write((json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii"))
            self.child.stdin.flush()
        except (OSError, ValueError) as error:
            raise RuntimeError("Codex App Server 已断开。") from error

    def receive(self):
        while True:
            if self.stop is not None and self.stop.is_set():
                raise InterruptedError("已暂停 Codex 派单。")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待 Codex App Server 回复超时。")
            try:
                packet = self.messages.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            if not isinstance(packet, dict):
                raise RuntimeError("Codex App Server 已退出或返回了无效消息。请检查 Codex 安装与登录状态。")
            if "method" in packet and "id" in packet:
                self.send({"id": packet["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
                raise RuntimeError("Codex 请求了当前文本派单不支持的交互。可在软件协作中改用已有 MCP 会话。")
            return packet

    def request(self, method, params, early=None):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        while True:
            packet = self.receive()
            if packet.get("id") != self.sequence:
                if early is not None and packet.get("method") in {"item/completed", "turn/completed"}:
                    early.append(packet)
                continue
            if "error" in packet:
                reason = packet["error"].get("message", "请求失败")
                raise RuntimeError(f"Codex App Server {method} 失败：{reason}")
            return packet["result"]


def _one_turn(connection, cwd, prompt):
    connection.request("initialize", {"clientInfo": {"name": "agents_bot", "title": "Agents Bot", "version": "1.6.1"}})
    connection.send({"method": "initialized"})
    thread = connection.request("thread/start", {
        "cwd": cwd, "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only",
        "developerInstructions": "你是 Agents Bot 团队成员。只处理派单中提供的文本快照；"
                                 "不要调用工具、执行命令或直接改动文件。按本轮要求通过最终答复交回 JSON。"})
    thread_id = thread["thread"]["id"]
    early = []
    started = connection.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}, early)
    turn_id = started["turn"]["id"]
    final, unclassified = "", ""
    try:
        while True:
            packet = early.pop(0) if early else connection.receive()
            params = packet.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if packet.get("method") == "item/completed" and params.get("turnId") == turn_id:
                items = [params["item"]]
                finished = False
            elif packet.get("method") == "turn/completed" and params["turn"]["id"] == turn_id:
                turn = params["turn"]
                if turn["status"] != "completed":
                    reason = (turn.get("error") or {}).get("message") or turn["status"]
                    raise RuntimeError(f"Codex 回合未完成：{reason}")
                items = turn.get("items", [])
                finished = True
            else:
                continue
            for item in items:
                if item.get("type") != "agentMessage" or not item.get("text"):
                    continue
                if item.get("phase") == "final_answer":
                    final = item["text"]
                elif item.get("phase") is None:
                    unclassified = item["text"]
            if finished:
                answer = (final or unclassified).strip()
                if not answer:
                    raise RuntimeError("Codex 回合已结束，未交回最终答复。")
                return answer
    except (InterruptedError, TimeoutError):
        try:
            connection.send({"id": connection.sequence + 1, "method": "turn/interrupt",
                             "params": {"threadId": thread_id, "turnId": turn_id}})
        except RuntimeError:
            pass
        raise


def run(prompt, *, stop=None, timeout=900):
    """Return the final assistant message using the user's signed-in Codex runtime."""
    if stop is not None and stop.is_set():
        raise InterruptedError("已暂停 Codex 派单。")
    from providers import _account_env
    argv = command() + ["app-server", "--listen", "stdio://"]
    for option in ("mcp_servers={}", "features.shell_tool=false", "features.unified_exec=false",
                   "features.plugins=false", "features.apps=false", "features.hooks=false",
                   "features.multi_agent=false", "web_search=\"disabled\""):
        argv.extend(["-c", option])
    with tempfile.TemporaryDirectory(prefix="agents-bot-codex-") as folder:
        with subprocess.Popen(argv, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=_account_env(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            connection = _Connection(process, stop, time.monotonic() + timeout)
            try:
                return _one_turn(connection, folder, prompt)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                connection.reader.join(timeout=1)
