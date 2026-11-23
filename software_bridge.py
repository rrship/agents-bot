"""Local mailbox shared by the Agents Bot workbench and native MCP clients."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time


SOFTWARE_NAMES = ("Codex", "Claude Desktop", "Antigravity", "ZCode", "DeepSeek Harness", "其他 Harness")
_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class BridgeError(RuntimeError):
    """An expired, incomplete, or mismatched software turn."""


def _duration(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise BridgeError(f"{label}必须是正数。")
    return value


def _identity(app, client=None):
    if app not in SOFTWARE_NAMES:
        raise BridgeError("未识别的软件成员。")
    if client is not None and (not isinstance(client, str) or not client.strip() or len(client) > 256):
        raise BridgeError("无效的软件会话。")


class Bridge:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._closed = threading.Event()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS software_turns (
                task_id TEXT PRIMARY KEY, app TEXT NOT NULL, prompt TEXT NOT NULL,
                deadline REAL NOT NULL, owner TEXT, lease TEXT, lease_until REAL,
                reply TEXT, receipt TEXT)""")
            db.execute("DELETE FROM software_turns WHERE deadline <= ?", (time.time(),))

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def request(self, app, prompt, *, stop=None, timeout=900, on_claim=None):
        """Publish one turn and wait for that exact response in the dispatch thread."""
        _identity(app)
        if not isinstance(prompt, str) or not prompt.strip():
            raise BridgeError("软件任务内容为空。")
        deadline = time.time() + _duration(timeout, "等待时间")
        task_id = secrets.token_hex(16)
        reported_lease = None
        try:
            if self._closed.is_set() or (stop is not None and stop.is_set()):
                raise BridgeError("软件任务已停止。")
            with self._db() as db:
                db.execute("DELETE FROM software_turns WHERE deadline <= ?", (time.time(),))
                db.execute("INSERT INTO software_turns(task_id,app,prompt,deadline) VALUES (?,?,?,?)",
                           (task_id, app, prompt, deadline))
            while not self._closed.is_set() and not (stop is not None and stop.is_set()):
                with self._db() as db:
                    row = db.execute("SELECT reply, lease FROM software_turns WHERE task_id=?", (task_id,)).fetchone()
                    if row and row["lease"] and row["lease"] != reported_lease and on_claim is not None:
                        reported_lease = row["lease"]
                        on_claim()
                    if row and row["reply"] is not None:
                        db.execute("UPDATE software_turns SET reply=NULL WHERE task_id=?", (task_id,))
                        return row["reply"]
                remaining = deadline - time.time()
                if remaining <= 0 or row is None:
                    raise BridgeError(f"等待 {app} 交回结果超时。请在软件会话中领取任务，然后重试。")
                self._closed.wait(min(.15, remaining))
            raise BridgeError("软件任务已停止。")
        finally:
            with self._db() as db:
                db.execute("DELETE FROM software_turns WHERE task_id=? AND receipt IS NULL", (task_id,))

    def close(self):
        """Wake requests issued by this Bridge instance only."""
        self._closed.set()

    def pending(self, app):
        """Report live counts for the panel without claiming or exposing another turn."""
        _identity(app)
        now = time.time()
        with self._db() as db:
            row = db.execute("""SELECT count(*) AS total,
                count(CASE WHEN lease_until>? THEN 1 END) AS claimed FROM software_turns
                WHERE app=? AND deadline>? AND receipt IS NULL""", (now, app, now)).fetchone()
        return {"waiting": row["total"] - row["claimed"], "claimed": row["claimed"]}

    def claim(self, app, client_id, *, lease_seconds=None):
        """Receive one turn. Reclaiming in the same session renews its lease."""
        _identity(app, client_id)
        if not client_id:
            raise BridgeError("无效的软件会话。")
        duration = 300 if lease_seconds is None else _duration(lease_seconds, "领取有效期")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("DELETE FROM software_turns WHERE deadline <= ?", (now,))
            row = db.execute("""SELECT * FROM software_turns WHERE app=? AND receipt IS NULL
                AND (owner IS NULL OR owner=? OR lease_until<=?)
                ORDER BY CASE WHEN owner=? AND lease_until>? THEN 0 ELSE 1 END, rowid LIMIT 1""",
                             (app, client_id, now, client_id, now)).fetchone()
            if row is None:
                return None
            same_lease = row["owner"] == client_id and row["lease_until"] > now
            lease = row["lease"] if same_lease else secrets.token_hex(16)
            until = min(now + duration, row["deadline"])
            db.execute("UPDATE software_turns SET owner=?, lease=?, lease_until=? WHERE task_id=?",
                       (client_id, lease, until, row["task_id"]))
            return dict(task_id=row["task_id"], prompt=row["prompt"], lease=lease, lease_until=until)

    def complete(self, app, client_id, task_id, lease, reply):
        """Only the client holding the active lease may commit this turn."""
        _identity(app, client_id)
        if not all(isinstance(x, str) and x.strip() for x in (client_id, task_id, lease, reply)):
            raise BridgeError("任务编号、领取凭据和完整回复均为必填项。")
        digest = hashlib.sha256(reply.encode("utf-8")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            row = db.execute("""SELECT lease_until, receipt FROM software_turns
                WHERE task_id=? AND app=? AND owner=? AND lease=? AND deadline>?""",
                             (task_id, app, client_id, lease, now)).fetchone()
            if row is None:
                raise BridgeError("任务已撤回、超时，或由其他会话领取。")
            if row["receipt"] is not None:
                if row["receipt"] == digest:
                    return True
                raise BridgeError("该任务已交回其他结果。")
            if row["lease_until"] <= now:
                raise BridgeError("领取凭据已过期，请重新领取任务。")
            # Keep an accepted reply retrievable across the deadline boundary when
            # the dispatcher is between polls. The request erases its contents
            # after delivery, leaving only a short-lived idempotency receipt.
            db.execute("""UPDATE software_turns SET prompt='', reply=?, receipt=?,
                deadline=MAX(deadline, ?) WHERE task_id=?""", (reply, digest, now + 60, task_id))
            return True

    def release(self, app, client_id):
        """Hand unfinished work back when a software session exits normally."""
        with self._db() as db:
            db.execute("""UPDATE software_turns SET owner=NULL, lease=NULL, lease_until=NULL
                WHERE app=? AND owner=? AND receipt IS NULL""", (app, client_id))


_TOOLS = [
    {
        "name": "claim_task",
        "description": "领取这个软件的一个工作台任务。若已领取，重复调用可续期五分钟。按 prompt 要求生成完整回复，然后调用 submit_result。",
        "inputSchema": {"type": "object", "properties": {"wait_seconds": {
            "type": "integer", "minimum": 0, "maximum": 20,
            "description": "没有任务时等待的秒数，默认 0。"}}, "additionalProperties": False},
    },
    {
        "name": "submit_result",
        "description": "交回所领任务的完整最终回复。若任务要求 JSON，reply 传序列化后的完整 JSON 字符串。",
        "inputSchema": {"type": "object", "properties": {
            "task_id": {"type": "string"}, "lease": {"type": "string"}, "reply": {"type": "string"}},
            "required": ["task_id", "lease", "reply"], "additionalProperties": False},
    },
]


def _tool_call(bridge, app, client_id, name, args):
    if not isinstance(args, dict):
        raise BridgeError("工具参数须为对象。")
    if name == "claim_task":
        wait = args.get("wait_seconds", 0)
        if set(args) - {"wait_seconds"} or type(wait) is not int or not 0 <= wait <= 20:
            raise BridgeError("wait_seconds 的取值范围是 0 到 20 秒。")
        end = time.monotonic() + wait
        while True:
            task = bridge.claim(app, client_id)
            remaining = end - time.monotonic()
            if task is not None or remaining <= 0:
                return task or {"status": "idle"}
            time.sleep(min(.25, remaining))
    if name == "submit_result":
        if set(args) != {"task_id", "lease", "reply"}:
            raise BridgeError("请提供 task_id、lease 和 reply。")
        bridge.complete(app, client_id, **args)
        return {"status": "accepted"}
    raise BridgeError("未知工具。")


def serve(bridge, app, source, output):
    client_id = secrets.token_hex(16)
    try:
        for line in source:
            request_id = None
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
                    raise ValueError
                if "id" not in msg:
                    continue
                request_id = msg["id"]
                method, params = msg["method"], msg.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError
                if method == "initialize":
                    version = params.get("protocolVersion")
                    result = {"protocolVersion": version if version in _PROTOCOLS else _PROTOCOLS[0],
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "agents-bot", "version": "1"}}
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": _TOOLS}
                elif method == "tools/call":
                    try:
                        data = _tool_call(bridge, app, client_id, params.get("name"), params.get("arguments", {}))
                        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
                    except (BridgeError, sqlite3.Error) as err:
                        result = {"isError": True, "content": [{"type": "text", "text": str(err)}]}
                else:
                    raise LookupError
                payload = {"result": result}
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"error": {"code": -32700, "message": "Parse error"}}
            except (ValueError, TypeError):
                payload = {"error": {"code": -32600, "message": "Invalid request"}}
            except LookupError:
                payload = {"error": {"code": -32601, "message": "Method not found"}}
            response = {"jsonrpc": "2.0", "id": request_id, **payload}
            output.write((json.dumps(response, ensure_ascii=True) + "\n").encode("utf-8"))
            output.flush()
    finally:
        bridge.release(app, client_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=SOFTWARE_NAMES)
    parser.add_argument("--database", required=True, type=Path)
    options = parser.parse_args()
    serve(Bridge(options.database), options.app, sys.stdin.buffer, sys.stdout.buffer)
