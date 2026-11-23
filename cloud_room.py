"""Durable cloud room service. One SSH session runs one JSON-lines process."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time


LEASE_SECONDS = 180
MAX_WIRE = 2_000_000
MAX_TEXT = 240_000
PRIVATE_PART = re.compile(r"^(?:node_modules|venv|__pycache__|dist|build|id_(?:rsa|ed25519|ecdsa|dsa)|credentials|secrets?)(?:\.|$)", re.I)
RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(label + "为空或过长")
    return value


def validated_files(files):
    if not isinstance(files, dict) or len(files) > 256:
        raise ValueError("项目最多支持 256 个文本文件")
    total = 0
    seen = {}
    for name, value in files.items():
        text(name, 240, "文件路径")
        parts = name.split("/")
        if (any(not p or p.startswith(".") or p.endswith((".", " ")) or PRIVATE_PART.match(p)
                or RESERVED.match(p) or re.search(r'[\\<>:"|?*\x00-\x1f]', p) for p in parts)
                or name.lower().endswith((".pem", ".key", ".pfx", ".p12", ".ppk", ".kdbx", ".jks"))):
            raise ValueError("云项目包含不支持的文件路径：" + name)
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("只接受 UTF-8 文本文件")
        size = len(value.encode("utf-8"))
        total += size
        if size > 96_000 or total > MAX_TEXT:
            raise ValueError("单文件上限 96 KB，云项目文本总上限 240 KB")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            kind = (prefix, i == len(parts))
            if seen.setdefault(prefix.casefold(), kind) != kind:
                raise ValueError("文件路径与已有目录或文件冲突：" + prefix)
    return files


def snapshot(folder, files):
    # Only generated paths are used as snapshot roots; no inbound links are copied.
    path = Path(tempfile.mkdtemp(prefix="project-", dir=folder))
    try:
        for name, value in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value.encode("utf-8"))
    except Exception:
        shutil.rmtree(path)
        raise
    return path.name


def history(state, message):
    state["events"] = (state["events"] + [message])[-100:]


def new_task(member, stage, assignment):
    return dict(id=secrets.token_hex(8), member=member, stage=stage, assignment=assignment,
                summary="", status="pending", handoffs=0, visited=[])


def create(root, req):
    goal = text(req.get("goal"), 12_000, "任务目标")
    members = req.get("members")
    if not isinstance(members, list) or not 2 <= len(members) <= 13:
        raise ValueError("云团队需要 2 至 13 位成员")
    for member in members:
        text(member, 80, "成员名")
    if len(set(members)) != len(members):
        raise ValueError("成员名必须唯一")
    leader = req.get("leader") or None
    if leader is not None and leader not in members:
        raise ValueError("组长必须是团队成员")
    files = validated_files(req.get("files", {}))
    if leader:
        tasks = [new_task(leader, "plan", "为全部执行成员制定并行分工和验收条件。")]
    else:
        tasks = [new_task(m, "implement", f"负责第 {i+1} 份分工：" +
                          ("实现主要功能及入口。" if i == 0 else
                           "编写测试和运行说明。" if i == 1 else "完善独立模块及边界场景。"))
                 for i, m in enumerate(members)]
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    room = secrets.token_hex(16)
    folder = root / room
    folder.mkdir(mode=0o700)
    try:
        state = dict(room=room, goal=goal, members=members, leader=leader, status="active",
                     tasks=tasks, events=["云端任务已创建"], receipts={}, reviews=0,
                     files=files, snapshot=snapshot(folder, files))
        with contextlib.closing(sqlite3.connect(folder / "room.db")) as db, db:
            db.execute("CREATE TABLE room (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
            db.execute("INSERT INTO room VALUES (1, ?)", (json.dumps(state, ensure_ascii=False),))
        return view(state, folder)
    except Exception:
        shutil.rmtree(folder)
        raise


def view(state, folder):
    now = time.time()
    tasks = []
    for row in state["tasks"]:
        fields = {key: row[key] for key in ("id", "member", "stage", "assignment", "summary", "status", "handoffs")}
        if row["status"] == "running" and row["expires"] <= now:
            fields["status"] = "pending"
        tasks.append(fields)
    return dict(room=state["room"], goal=state["goal"], status=state["status"], leader=state["leader"],
                members=state["members"], tasks=tasks, files=sorted(state["files"]), events=state["events"],
                project=str(folder / state["snapshot"]))


def workers(state):
    return [m for m in state["members"] if m != state["leader"]]


def add_assignments(state, assignments):
    if not isinstance(assignments, dict) or set(assignments) != set(workers(state)):
        raise ValueError("组长须为每个执行成员提供完整分工")
    for member, spec in assignments.items():
        state["tasks"].append(new_task(member, "implement", text(spec, 6000, "分工")))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def apply_files(state, folder, task, changes):
    validated_files(changes)
    if not changes:
        return
    files = state["files"]
    for name, value in changes.items():
        current = files.get(name)
        fingerprint = None if current is None else digest(current)
        if task["base"].get(name) != fingerprint and current != value:
            raise ValueError("其他成员已更新 " + name + "；请重新领取任务后合并")
    merged = validated_files({**files, **changes})
    if files != merged:
        state["snapshot"] = snapshot(folder, merged)
        state["files"] = merged


def close_lease(task):
    for key in ("client", "lease", "expires", "base"):
        task.pop(key, None)


def claim(state, member, client):
    if state["status"] != "active":
        return {"task": None}
    eligible = [t for t in state["tasks"] if t["member"] == member and t["status"] != "done"]
    now = time.time()
    existing = next((t for t in eligible if t.get("client") == client and t["expires"] > now), None)
    task = existing or next((t for t in eligible if t.get("expires", 0) <= now), None)
    if task is None:
        return {"task": None}
    if not existing:
        task.update(client=client, status="running", lease=secrets.token_urlsafe(24),
                    base={k: digest(v) for k, v in state["files"].items()})
        history(state, member + " 领取任务 · " + task["stage"])
    task["expires"] = now + LEASE_SECONDS
    return {"task": dict(id=task["id"], lease=task["lease"], stage=task["stage"], member=member,
                         goal=state["goal"], assignment=task["assignment"], summary=task["summary"],
                         peers=[m for m in state["members"] if m != member], members=state["members"],
                         leader=state["leader"], files=state["files"], events=state["events"][-16:],
                         workers=workers(state), handoffs=task["handoffs"], visited=task.get("visited", []))}


def complete(state, task, req, folder):
    message = text(req.get("message"), 6000, "交付说明")
    stage = task["stage"]
    if stage == "plan":
        add_assignments(state, req.get("assignments"))
    elif stage == "review":
        approved = req.get("approved")
        if type(approved) is not bool:
            raise ValueError("组长复核需要 approved 布尔值")
        if approved:
            state["status"] = "complete"
        elif state["reviews"] >= 3:
            state["status"] = "blocked"
        elif req.get("assignments") is not None:
            add_assignments(state, req["assignments"])
        else:
            state["tasks"].append(new_task(state["leader"], "plan", "针对复核意见重新分工：" + message))
    else:
        apply_files(state, folder, task, req.get("files", {}))
    task.update(status="done", summary=message)
    history(state, f"{task['member']} [{stage}] {message}")
    if state["status"] == "active" and all(t["status"] == "done" for t in state["tasks"]):
        if state["leader"]:
            state["reviews"] += 1
            state["tasks"].append(new_task(state["leader"], "review", "审阅服务器项目与成员交付记录。"))
        else:
            state["status"] = "complete"


def act(state, op, req, folder):
    if op == "status":
        return view(state, folder)
    if op == "export":
        return {"files": state["files"]}
    member = req.get("member")
    client = text(req.get("client"), 120, "客户端标识")
    if member not in state["members"]:
        raise ValueError("请选择房间中的成员")
    if op == "claim":
        return claim(state, member, client)
    if op not in {"renew", "release", "finish", "handoff"}:
        raise ValueError("未知房间操作")
    lease = text(req.get("lease"), 120, "租约")
    record = [op, member, client, req.get("id"), digest(json.dumps(req, ensure_ascii=False, sort_keys=True))]
    if state["receipts"].get(lease) == record:
        return {}
    task = next((t for t in state["tasks"] if t["id"] == req.get("id")), None)
    if (not task or task["member"] != member or task.get("client") != client or
            task.get("lease") != lease or task["expires"] <= time.time()):
        raise ValueError("任务租约已失效，请重新领取")
    if op == "renew":
        task["expires"] = time.time() + LEASE_SECONDS
        return {}
    if op == "finish":
        complete(state, task, req, folder)
    elif op == "handoff":
        target = req.get("to")
        if (task["stage"] != "implement" or target not in state["members"]
                or target == member or target in task.get("visited", [])):
            raise ValueError("实现任务可以交给同房间尚未处理该分工的另一名成员")
        if task["handoffs"] >= 6:
            raise ValueError("该任务已交接 6 次，请完成当前分工")
        summary = text(req.get("summary"), 6000, "交接摘要")
        reason = text(req.get("reason", "成员主动交接"), 500, "交接原因")
        apply_files(state, folder, task, req.get("files", {}))
        task.setdefault("visited", []).append(member)
        task.update(member=target, status="pending", summary=summary, handoffs=task["handoffs"] + 1)
        history(state, f"{member} → {target} [交接 · {reason}] {summary}")
    else:
        task["status"] = "pending"
        history(state, f"{member} 释放任务，可重新领取")
    state["receipts"][lease] = record
    while len(state["receipts"]) > 256:
        del state["receipts"][next(iter(state["receipts"]))]
    close_lease(task)
    return {}


def dispatch(req, root=None):
    if not isinstance(req, dict):
        raise ValueError("请求必须为 JSON 对象")
    root = Path(root or os.environ.get("AGENTSBOT_ROOMS") or Path.home() / ".agents-bot/rooms").absolute()
    op = req.get("op", req.get("action"))
    if op == "create":
        return create(root, req)
    room = req.get("room")
    if not isinstance(room, str) or not re.fullmatch(r"[a-f0-9]{32}", room):
        raise ValueError("房间号无效")
    folder = root / room
    path = folder / "room.db"
    if not path.is_file():
        raise ValueError("未找到房间，请核对 SSH 登录账号和房间号")
    if op in {"start_worker", "stop_worker", "worker_status"}:
        from cloud_daemon import handle
        return handle(op, root, room, req.get("configs"))
    with contextlib.closing(sqlite3.connect(path, timeout=15)) as db, db:
        readonly = op in ("status", "export")
        db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
        state = json.loads(db.execute("SELECT state FROM room WHERE id=1").fetchone()[0])
        result = act(state, op, req, folder)
        if not readonly:
            db.execute("UPDATE room SET state=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))
    return result


def serve():
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    while True:
        raw = incoming.readline(MAX_WIRE + 1)
        if not raw:
            return
        if len(raw) > MAX_WIRE:
            outgoing.write(b'{"ok":false,"error":"request too large"}\n')
            outgoing.flush()
            return
        try:
            response = dict(ok=True, result=dispatch(json.loads(raw)))
        except (ValueError, OSError, sqlite3.Error) as error:
            response = dict(ok=False, error=str(error))
        outgoing.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        outgoing.flush()


if __name__ == "__main__":
    serve()
