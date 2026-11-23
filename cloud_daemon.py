"""Supervise one detached group of API members per room, without PID signalling."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time

from providers import BUILTIN, MEMBER_NAMES, NAMES
from lite_agent import bot_instructions


ACTIVE = {"starting", "running", "stopping"}
RUNTIME = Path(__file__).resolve().parent
ENTER = "import sys;sys.path.insert(0,sys.argv[1]);from cloud_daemon import main;main()"
IDLE = {"status": "idle", "members": [], "message": "尚未启动服务器成员"}


@contextmanager
def control(folder):
    with closing(sqlite3.connect(folder / "worker.db", timeout=10)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS worker (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        yield db


def read(db):
    row = db.execute("SELECT data FROM worker WHERE id=1").fetchone()
    return json.loads(row[0]) if row else IDLE.copy()


def write(db, state):
    db.execute("INSERT OR REPLACE INTO worker VALUES (1, ?)", (json.dumps(state, ensure_ascii=False),))


def public(state):
    return {k: state[k] for k in ("status", "members", "message")}


def running(folder):
    # OS file locks backing an SQLite write transaction disappear even after a process crash.
    with closing(sqlite3.connect(folder / "worker-live.db", timeout=0)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return True
            raise
        return False


def checked(db, folder):
    state = read(db)
    if state["status"] in ACTIVE:
        # Give a just-launched process time to acquire its lifetime lock, including a quick stop.
        grace = state.get("launched") and time.time() - state["launched"] < 20 and not state.get("ready")
        if not grace and not running(folder):
            state.update(status="failed", message="服务器进程已退出，可以重新启动")
            write(db, state)
    return state


def validate_configs(configs, members):
    if not isinstance(configs, dict) or not 1 <= len(configs) <= len(MEMBER_NAMES):
        raise ValueError("请选择要在服务器运行的 API 成员")
    clean = {}
    for name, cfg in configs.items():
        if name not in members or name not in MEMBER_NAMES or not isinstance(cfg, dict) or cfg.get("mode") != "api":
            raise ValueError("服务器只接受当前房间中的 API 成员")
        provider = cfg.get("provider") if name == BUILTIN else name
        if provider not in NAMES:
            raise ValueError("内置 Agent 的 API 平台无效")
        model, key = cfg.get("model"), cfg.get("key")
        if not isinstance(model, str):
            raise ValueError("请填写 API 模型 ID")
        model = model.strip().removeprefix("models/") if provider == "Gemini" else model.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}", model):
            raise ValueError("API 模型 ID 格式无效")
        if not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,4096}", key):
            raise ValueError("请为服务器成员提供有效 API Key")
        clean[name] = dict(mode="api", model=model, key=key)
        if name == BUILTIN:
            clean[name]["provider"] = provider
            instructions = bot_instructions(cfg)
            if instructions:
                clean[name]["instructions"] = instructions
    return clean


def handle(op, root, room, configs=None):
    from cloud_room import dispatch
    folder = root / room
    with control(folder) as db:
        state = checked(db, folder)
        if op == "worker_status":
            return public(state)
        if op == "stop_worker":
            if state["status"] in ACTIVE:
                state.update(status="stopping", message="服务器成员正在释放任务")
                write(db, state)
            return public(state)
        if op != "start_worker":
            raise ValueError("未知后台操作")
        if state["status"] in ACTIVE:
            raise ValueError("该房间已有服务器成员在运行")
        room_state = dispatch(dict(op="status", room=room), root)
        if room_state["status"] != "active":
            raise ValueError("任务已结束")
        configs = validate_configs(configs, room_state["members"])
        run = secrets.token_hex(16)
        packet = json.dumps(dict(root=str(root), room=room, run=run, configs=configs)).encode()
        options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True})
        child = subprocess.Popen([sys.executable, "-I", "-u", "-c", ENTER, str(RUNTIME)],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, cwd=RUNTIME, **options)
        threading.Thread(target=child.wait, daemon=True).start()
        try:
            with child.stdin:
                child.stdin.write(packet)
        except OSError:
            raise ValueError("无法启动服务器成员，请检查远端 Python 运行环境") from None
        state = dict(status="starting", message="正在启动服务器成员", members=list(configs),
                     run=run, launched=time.time(), ready=False)
        write(db, state)
        return public(state)


def run_process(root, room, run, configs):
    from cloud_room import dispatch
    from cloud_worker import run_team
    folder = root / room

    def rpc(op, **fields):
        return dispatch(dict(op=op, **fields), root)

    def set_state(status, message, ready=False):
        with control(folder) as db:
            state = read(db)
            if state.get("run") != run:
                return False
            if status == "running" and state["status"] == "stopping":
                return False
            state.update(status=status, message=message, ready=ready)
            write(db, state)
            return True

    stop = threading.Event()

    def watch_stop():
        try:
            while not stop.wait(.4):
                with control(folder) as db:
                    state = read(db)
                if state.get("run") != run or state["status"] == "stopping":
                    break
        finally:
            stop.set()

    # The live lock is held through the final update; a replacement never signals an old PID.
    with closing(sqlite3.connect(folder / "worker-live.db", timeout=15)) as life:
        life.execute("BEGIN IMMEDIATE")
        try:
            if not set_state("running", "服务器 API 成员已就绪", ready=True):
                set_state("stopped", "服务器成员已停止")
                return
            monitor = threading.Thread(target=watch_stop, daemon=True)
            monitor.start()
            run_team(rpc, room, configs, stop, lambda _message: None)
            final = rpc("status", room=room)["status"]
            if final == "complete":
                set_state("complete", "本房间的任务已完成")
            elif final == "blocked":
                set_state("blocked", "任务等待人工处理")
            else:
                set_state("stopped", "服务器成员已停止")
        except Exception:
            set_state("failed", "服务器成员执行失败，请检查模型权限、额度、网络或回复格式，再重新启动")
        finally:
            stop.set()


def main():
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)
    with sys.stdin.buffer as source:
        packet = json.loads(source.read(160_000))
    run_process(Path(packet["root"]), packet["room"], packet["run"], packet["configs"])
