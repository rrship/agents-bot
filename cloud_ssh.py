"""Carry room requests over one authenticated OpenSSH session."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import threading


WIRE_LIMIT = 2_000_000
RUNTIME_FILES = ("cloud_room.py", "cloud_worker.py", "cloud_daemon.py", "handoff.py",
                 "engine.py", "providers.py", "extra_accounts.py", "lite_agent.py", "software_bridge.py")


def runtime_bundle(source=None):
    source = Path(source or Path(__file__).parent)
    contents = {name: base64.b64encode((source / name).read_bytes()).decode("ascii")
                for name in RUNTIME_FILES}
    raw = json.dumps(contents, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(raw) + 1 > WIRE_LIMIT:
        raise ValueError("云端运行组件超出部署大小限制")
    return raw + b"\n", hashlib.sha256(raw).hexdigest()


# Only fixed modules from the installed app are deployed. Concurrent clients can
# prepare identical versions independently and atomically publish one directory.
BOOTSTRAP = "allowed = " + repr(RUNTIME_FILES) + "\n" + r"""
import base64, hashlib, json, os, shutil, sys, tempfile
from pathlib import Path
os.umask(0o077)
sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit("Agents Bot requires Python 3.11+")
line = sys.stdin.buffer.readline(2000001)
if len(line) > 2000000 or not line.endswith(b"\n"):
    raise ValueError("invalid runtime transfer")
raw = line[:-1]
digest = hashlib.sha256(raw).hexdigest()
encoded = json.loads(raw)
if set(encoded) != set(allowed):
    raise ValueError("unexpected runtime modules")
files = {name: base64.b64decode(encoded[name], validate=True) for name in allowed}
parent = Path.home() / ".agents-bot" / "runtime"
parent.mkdir(mode=0o700, parents=True, exist_ok=True)
target = parent / digest
if not target.exists():
    with tempfile.TemporaryDirectory(prefix="install-", dir=parent) as temp:
        stage = Path(temp) / "modules"
        stage.mkdir(mode=0o700)
        for name, value in files.items():
            (stage / name).write_bytes(value)
        try:
            stage.rename(target)
        except OSError:
            if not target.is_dir():
                raise
if target.is_symlink() or any((target / name).is_symlink() or
                              (target / name).read_bytes() != value
                              for name, value in files.items()):
    raise ValueError("damaged runtime cache")
sys.path.insert(0, str(target))
from cloud_room import serve
print(json.dumps({"agents_bot_ready": 2, "runtime": digest}), flush=True)
serve()
"""


def endpoint(host, user, port):
    host, user = host.strip(), user.strip()
    try:
        address = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        if "%" in address:
            raise ValueError()
        host = str(ipaddress.ip_address(address))
    except ValueError:
        labels = host.rstrip(".").split(".")
        if len(host) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                      for label in labels):
            raise ValueError("请填写主机名或 IP 地址，用户名和端口请分别填写") from None
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", user):
        raise ValueError("SSH 用户名格式不正确")
    if not re.fullmatch(r"[0-9]{1,5}", str(port)) or not 1 <= int(port) <= 65535:
        raise ValueError("SSH 端口应为 1 到 65535")
    return host, user, int(port)


class CloudSSH:
    def __init__(self, host, user, port=22, identity_file=None):
        self.host, self.user, self.port = endpoint(host, user, port)
        self.identity_file = Path(identity_file).expanduser().resolve(strict=True) if identity_file else None
        if self.identity_file and not self.identity_file.is_file():
            raise ValueError("请选择 SSH 私钥文件")
        self._process = None
        self._io = threading.Lock()
        self._lifecycle = threading.Lock()
        self._closed = False

    def _command(self):
        ssh = shutil.which("ssh.exe" if os.name == "nt" else "ssh")
        if not ssh or (os.name == "nt" and Path(ssh).suffix.lower() != ".exe"):
            raise OSError("请在 Windows 可选功能中安装 OpenSSH 客户端，并确认可以运行 ssh.exe")
        return [
            ssh, "-T", "-a", "-x", "-o", "ClearAllForwardings=yes",
            "-o", "StrictHostKeyChecking=ask", "-o", "BatchMode=no",
            "-o", "PermitLocalCommand=no", "-o", "ConnectionAttempts=1",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            *(["-i", str(self.identity_file), "-o", "IdentitiesOnly=yes"] if self.identity_file else []),
            "-p", str(self.port), "-l", self.user,
            "--", self.host, "python3 -I -u -c " + shlex.quote(BOOTSTRAP),
        ]

    def connect(self):
        source, digest = runtime_bundle()
        with self._io:
            with self._lifecycle:
                if self._closed:
                    raise ConnectionError("连接已经关闭，请重新连接")
                if self._process is not None:
                    raise RuntimeError("SSH 会话已启动")
                self._process = subprocess.Popen(
                    self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            try:
                ready = self._exchange(source, 180)
                if ready != {"agents_bot_ready": 2, "runtime": digest}:
                    raise ConnectionError("远端未能启动 Agents Bot 房间服务")
            except Exception:
                self.close()
                raise

    def _exchange(self, payload, timeout):
        process = self._process
        if process is None or process.poll() is not None:
            raise ConnectionError("SSH 已断开，请重新连接并检查房间进度")
        result = queue.Queue(maxsize=1)

        def transfer():
            try:
                process.stdin.write(payload)
                process.stdin.flush()
                line = process.stdout.readline(WIRE_LIMIT + 1)
                if not line:
                    raise EOFError()
                if len(line) > WIRE_LIMIT or not line.endswith(b"\n"):
                    raise ConnectionError("远端响应超出传输限制，请重新连接")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("invalid response")
                result.put((value, None))
            except (ValueError, OSError, EOFError) as exc:
                result.put((None, exc))

        threading.Thread(target=transfer, daemon=True).start()
        try:
            value, error = result.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError("SSH 响应超时，请重新连接后查看房间状态") from None
        if error:
            self.close()
            if isinstance(error, (EOFError, BrokenPipeError, ConnectionResetError)):
                raise ConnectionError("SSH 连接已结束，请核对登录信息、主机指纹及服务器的 Python 3.11+ 环境") from error
            if isinstance(error, ConnectionError):
                raise error
            raise ConnectionError("云会话中断或返回了无效响应，请重新连接") from error
        return value

    def rpc(self, op, **fields):
        payload = json.dumps(dict(fields, op=op), ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > WIRE_LIMIT:
            raise ValueError("请求超出云端传输限制")
        with self._io:
            response = self._exchange(payload, 60)
        if not response.get("ok"):
            raise ValueError(str(response.get("error", "远端房间操作失败"))[:1000])
        if not isinstance(response.get("result"), dict):
            raise ConnectionError("远端房间返回了无效结果")
        return response["result"]

    def close(self):
        with self._lifecycle:
            self._closed = True
            process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait()
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass
