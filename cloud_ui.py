"""Desktop controls for a shared SSH room. All Tk updates run in Desk._poll."""
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from cloud_room import snapshot, validated_files
from cloud_ssh import CloudSSH
from cloud_worker import run_team
from desktop_style import style_text
from engine import hidden
from lite_agent import bot_instructions
from providers import BUILTIN, ENV_KEYS, MEMBER_NAMES
from team_modes import OPC


STATUS = {"active": "进行中", "complete": "已完成", "blocked": "待整改",
          "pending": "待领取", "running": "进行中", "done": "已提交"}
STAGE = {"plan": "组长派单", "implement": "实现", "review": "组长复核"}


def import_project(root):
    files = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not hidden(Path(folder) / d))
        for name in sorted(names):
            path = Path(folder) / name
            if hidden(path) or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                validated_files({rel: ""})
            except ValueError:
                continue
            with path.open("rb") as stream:
                data = stream.read(96_001)
            if b"\0" in data:
                continue
            try:
                value = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            files[rel] = value
            if len(data) > 96_000:
                raise ValueError(f"{rel} 超过单文件 96,000 字节上限，请选较小的项目目录。")
            validated_files(files)
    return files


def export_project(parent, files):
    validated_files(files)
    directory = Path(parent).resolve(strict=True)
    return directory / snapshot(directory, files)


def server_configs(configs):
    result = {}
    for name, cfg in configs.items():
        if cfg["mode"] == "software":
            raise ValueError(f"{name} 在提供它的电脑上运行。请选择「本机参与」。")
        if cfg["mode"] != "api":
            raise ValueError(f"{name} 使用本机账号。请用「本机参与」，或先在「成员与连接」改为 API Key。")
        provider = cfg.get("provider") if name == BUILTIN else name
        if provider not in ENV_KEYS:
            raise ValueError("内置 Agent 需要绑定有效的 API 平台。")
        key = (cfg["key"] or os.environ.get(ENV_KEYS[provider], "")).strip()
        if not key:
            raise ValueError(f"请为 {name} 填写 API Key 或设置 {ENV_KEYS[provider]}。")
        result[name] = dict(mode="api", model=cfg["model"], key=key)
        if name == BUILTIN:
            result[name]["provider"] = provider
            instructions = bot_instructions(cfg)
            if instructions:
                result[name]["instructions"] = instructions
    return result


class CloudPanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=14)
        self.desk = desk
        self.conn = None
        self.ready = False
        self.busy = False
        self.view = {}
        self.remote_members = []
        self.remote_running = False
        self.remote_message = "尚未启动服务器成员"
        self.runner = None
        self.notices = queue.Queue()
        self.next_refresh = 0
        self.generation = 0
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="22")
        self.user = tk.StringVar()
        self.room = tk.StringVar()
        self.notice = tk.StringVar(value="登录后可创建房间，或使用队友提供的房间号加入。")
        self.share = tk.BooleanVar(value=False)
        self.selected = {}
        self.buttons = {}
        self.inputs = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text="云服务器", style="Title.TLabel").grid(row=0, sticky="w", pady=(0, 10))
        self.pages = ttk.Notebook(self)
        self.pages.grid(row=1, sticky="nsew")
        connection = ttk.Frame(self.pages, padding=16)
        connection.columnconfigure(0, weight=1)
        self.progress = ttk.Frame(self.pages, padding=16)
        self.progress.columnconfigure(0, weight=1)
        self.progress.rowconfigure(5, weight=1)
        self.pages.add(connection, text="连接与房间")
        self.pages.add(self.progress, text="成员与进度")

        ttk.Label(connection, text="SSH 连接", style="Section.TLabel").grid(row=0, sticky="w")
        login = ttk.Frame(connection)
        login.grid(row=1, sticky="ew", pady=(8, 6))
        for column, (label, var, width, weight) in enumerate((
                ("服务器", self.host, 18, 3), ("端口", self.port, 5, 0), ("SSH 用户", self.user, 12, 2))):
            login.columnconfigure(column, weight=weight)
            ttk.Label(login, text=label).grid(row=0, column=column, sticky="w")
            entry = ttk.Entry(login, textvariable=var, width=width)
            entry.grid(row=1, column=column, sticky="ew", padx=(0, 8) if column < 2 else 0)
            self.inputs.append(entry)
        identity = ttk.Frame(connection)
        identity.grid(row=2, sticky="ew", pady=(1, 4))
        self.identity = ""
        self._button(identity, "connect", "连接", self.connect, primary=True)
        self._button(identity, "identity", "选择私钥…", self.choose_identity)
        self._button(identity, "disconnect", "断开", self.disconnect)
        ttk.Label(connection, text="首次连接请核对服务器指纹，密码或密钥口令在 SSH 窗口输入。",
                  style="Hint.TLabel", wraplength=440).grid(row=3, sticky="w", pady=(2, 10))
        ttk.Separator(connection).grid(row=4, sticky="ew", pady=(3, 10))
        ttk.Label(connection, text="协作房间", style="Section.TLabel").grid(row=5, sticky="w")
        rooms = ttk.Frame(connection)
        rooms.grid(row=6, sticky="ew", pady=(7, 4))
        ttk.Label(rooms, text="房间号").pack(side="left", padx=(0, 8))
        self.room_entry = ttk.Entry(rooms, textvariable=self.room, width=15)
        self.room_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._button(rooms, "join", "加入", self.join)
        room_actions = ttk.Frame(connection)
        room_actions.grid(row=7, sticky="w", pady=(3, 6))
        self._button(room_actions, "create", "从工作台创建房间", self.create)

        members = ttk.Frame(self.progress)
        members.grid(row=0, sticky="ew", pady=(0, 5))
        ttk.Label(members, text="本次提供的成员", style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.checks = {}
        for index, name in enumerate(MEMBER_NAMES):
            self.selected[name] = tk.BooleanVar(value=False)
            box = ttk.Checkbutton(members, text=name, variable=self.selected[name], command=self._controls)
            box.grid(row=1+index//4, column=index%4, sticky="w", padx=(0, 12))
            self.checks[name] = box
        controls = ttk.Frame(self.progress)
        controls.grid(row=1, sticky="ew", pady=(2, 4))
        self._button(controls, "local", "本机参与", self.start_local)
        self._button(controls, "stop_local", "停止本机参与", self.stop_local)
        self._button(controls, "server", "启动服务器 API", lambda: self.server(True))
        self._button(controls, "stop_server", "停止服务器 API", lambda: self.server(False))
        ttk.Separator(self.progress).grid(row=2, sticky="ew", pady=(8, 8))
        actions = ttk.Frame(self.progress)
        actions.grid(row=3, sticky="ew")
        ttk.Label(actions, text="房间进度", style="Section.TLabel").pack(side="left")
        ttk.Checkbutton(actions, text="手机显示此房间", variable=self.share,
                        command=desk.publish_mobile).pack(side="right")
        export = self._button(actions, "export", "导出文件…", self.export)
        export.pack_configure(side="right", padx=(0, 8))
        copy = self._button(actions, "copy", "复制房间号", self.copy)
        copy.pack_configure(side="right", padx=(0, 5))
        self.heading = tk.StringVar(value="加入房间后可查看任务与交接记录。")
        heading = ttk.Label(self.progress, textvariable=self.heading, style="Hint.TLabel", wraplength=470)
        heading.grid(row=4, sticky="ew", pady=(4, 0))
        self.log = style_text(ScrolledText(self.progress, height=3, width=20, wrap="word", state="disabled"))
        self.log.grid(row=5, sticky="nsew", pady=(2, 0))
        notice = ttk.Label(self, textvariable=self.notice, wraplength=490, style="Hint.TLabel")
        notice.grid(row=2, sticky="ew", pady=(7, 0))
        for label in (heading, notice):
            label.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(100, event.width-4)))
        self._controls()

    def _button(self, frame, key, label, action, *, primary=False):
        widget = ttk.Button(frame, text=label, command=action, style="Accent.TButton" if primary else "TButton")
        widget.pack(side="left", padx=(0, 5))
        self.buttons[key] = widget
        return widget

    def _controls(self):
        active = self.view.get("status") == "active"
        room = bool(self.view)
        idle = self.ready and not self.busy
        chosen = any(v.get() for v in self.selected.values())
        states = dict(connect=self.conn is None, identity=self.conn is None, disconnect=self.conn is not None,
                      create=idle and not self.runner, join=idle and not self.runner,
                      copy=room, export=idle and room, local=idle and active and chosen and not self.runner,
                      stop_local=self.runner is not None and not self.runner.is_set(),
                      server=idle and active and chosen and not self.remote_running,
                      stop_server=idle and self.remote_running)
        for key, value in states.items():
            self.buttons[key].configure(state="normal" if value else "disabled")
        for box in self.inputs:
            box.configure(state="normal" if self.conn is None else "disabled")
        for name, box in self.checks.items():
            box.configure(state="normal" if name in self.view.get("members", []) else "disabled")

    def _launch(self, kind, fn):
        version = self.generation
        def work():
            try:
                self.notices.put((version, kind, fn(), None))
            except Exception as error:
                self.notices.put((version, kind, None, error))
        threading.Thread(target=work, daemon=True).start()

    def _action(self, kind, fn):
        self.busy = True
        self._controls()
        self._launch(kind, fn)

    def _error(self, message):
        messagebox.showerror("云服务器", str(message), parent=self)

    def connect(self):
        try:
            conn = CloudSSH(self.host.get(), self.user.get(), self.port.get(), identity_file=self.identity or None)
        except (ValueError, OSError) as error:
            self._error(error)
            return
        self.conn = conn
        self.notice.set("正在连接。请在 SSH 窗口核对指纹或输入凭据。")
        self._action("connect", conn.connect)

    def choose_identity(self):
        if self.identity:
            self.identity = ""
        else:
            self.identity = filedialog.askopenfilename(title="选择本机 SSH 私钥", parent=self)
        self.buttons["identity"].configure(text="清除所选私钥" if self.identity else "选择私钥…")
        self.notice.set("已选择本机私钥：" + Path(self.identity).name if self.identity else "使用 OpenSSH 默认登录方式。")

    def disconnect(self):
        self.generation += 1
        if self.runner:
            self.runner.set()
            self.runner = None
        if self.conn:
            threading.Thread(target=self.conn.close, daemon=True).start()
        self.conn = None
        self.ready = self.busy = self.remote_running = False
        self.remote_members = []
        self.remote_message = "尚未启动服务器成员"
        self.view = {}
        self.share.set(False)
        for var in self.selected.values():
            var.set(False)
        self.notice.set("SSH 已断开。已启动的服务器 API 成员可继续处理任务。")
        self.render()
        self._controls()

    def create(self):
        if self.desk.task_mode.get() == OPC:
            self._error("OPC 员工在这台电脑的任务工作台接单。要创建 SSH 房间，请在新任务中切换常规团队。")
            return
        goal = self.desk.goal.get("1.0", "end").strip()
        roster = self.desk._members()
        leader = self.desk.leader.get() if self.desk.team_mode.get() == "leader" else None
        project = self.desk.project.get().strip()
        if not goal or len(roster) < 2 or (project and not Path(project).is_dir()):
            self._error("请在任务工作台填写目标，在成员页选择至少两位成员。创建空项目时可留空目录。")
            return
        rpc = self.conn.rpc
        def create_room():
            files = import_project(Path(project)) if project else {}
            return rpc("create", goal=goal, members=roster, leader=leader, files=files)
        self._action("room", create_room)

    def join(self):
        code = self.room.get().strip()
        if not code:
            self._error("请输入队友提供的房间号。")
            return
        rpc = self.conn.rpc
        self._action("room", lambda: rpc("status", room=code))

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.view["room"])
        self.notice.set("已复制当前房间号。队友以同一项目 SSH 用户连接服务器后即可加入。")

    def export(self):
        parent = filedialog.askdirectory(title="选择导出位置，将创建新的项目子目录", parent=self, mustexist=True)
        if parent:
            rpc, room = self.conn.rpc, self.view["room"]
            self._action("export", lambda: export_project(parent, rpc("export", room=room)["files"]))

    def configs(self):
        configs = {name: self.desk._config(name) for name in self.view["members"]
                   if name in self.selected and self.selected[name].get()}
        if not configs:
            raise ValueError("请先勾选要提供的房间成员。")
        return configs

    def start_local(self):
        try:
            configs = self.configs()
        except ValueError as error:
            self._error(error)
            return
        self.runner = stop = threading.Event()
        rpc, room, version = self.conn.rpc, self.view["room"], self.generation
        def report(text):
            self.notices.put((version, "report", text, None))
        self.notice.set("本机参与中 · " + "、".join(configs))
        self._controls()
        self._launch("local", lambda: run_team(rpc, room, configs, stop, report))

    def stop_local(self):
        self.runner.set()
        self.notice.set("正在停止本机成员。")
        self._controls()

    def server(self, start):
        rpc, room = self.conn.rpc, self.view["room"]
        if start:
            try:
                configs = server_configs(self.configs())
            except ValueError as error:
                self._error(error)
                return
            target = f"{self.conn.user}@{self.conn.host}:{self.conn.port}"
            labels = [f"{name}（{cfg['provider']} / {cfg['model']}）" if name == BUILTIN else name
                      for name, cfg in configs.items()]
            if not messagebox.askyesno("授权服务器使用 API", f"将 {'、'.join(labels)} 的 API Key 经 SSH 发送到 {target}，"
                    "供该项目账号的后台进程在内存中调用模型。关闭电脑后仍会使用相应 API 额度。启动这些成员？", parent=self):
                return
            self._action("worker", lambda: rpc("start_worker", room=room, configs=configs))
        else:
            self._action("worker", lambda: rpc("stop_worker", room=room))

    def phone_snapshot(self):
        if self.share.get() and self.view:
            v = self.view
            return (v["goal"], "云协作 · " + STATUS.get(v["status"], v["status"]),
                    v["members"], v["leader"], v["events"][-100:])
        return None

    def render(self):
        v = self.view
        lines = []
        if v:
            complete = sum(t["status"] == "done" for t in v["tasks"])
            self.heading.set(f"{STATUS.get(v['status'], v['status'])} · {complete}/{len(v['tasks'])} 项分工 · {v['goal'][:100]}")
            lines.append("服务器 API：" + self.remote_message + (" · " + "、".join(self.remote_members) if self.remote_members else ""))
            for task in v["tasks"]:
                lines.append(f"{task['member']} · {STAGE.get(task['stage'], task['stage'])} · {STATUS.get(task['status'], task['status'])}　{task['assignment']}")
            lines.extend(["", f"服务器文件（{len(v['files'])}）：" + ("、".join(v["files"]) or "暂无"), "", *v["events"]])
        else:
            self.heading.set("加入房间后可查看任务与交接记录。")
        content = "\n".join(lines)
        if self.log.get("1.0", "end-1c") != content:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("1.0", content)
            self.log.see("end")
            self.log.configure(state="disabled")

    def poll(self):
        while True:
            try:
                version, kind, result, error = self.notices.get_nowait()
            except queue.Empty:
                break
            if version != self.generation:
                continue
            if kind not in ("report", "local", "refresh"):
                self.busy = False
            if kind == "local":
                self.runner = None
            if error:
                if kind == "connect" or isinstance(error, (ConnectionError, TimeoutError)):
                    self.disconnect()
                self.notice.set(str(error))
                if kind != "refresh":
                    self._error(error)
            elif kind == "report":
                self.notice.set(result)
            elif kind == "local":
                self.notice.set("本机成员已停止。")
            elif kind == "export":
                self.notice.set("已导出到 " + str(result))
            elif kind == "connect":
                self.ready = True
                self.notice.set("SSH 已连接。可从工作台创建房间，或输入房间号加入。")
            elif kind == "room":
                self.generation += 1
                self.view = result
                self.room.set(result["room"])
                self.share.set(False)
                self.remote_running = False
                self.remote_members = []
                self.remote_message = "正在读取状态"
                for var in self.selected.values():
                    var.set(False)
                self.notice.set("已进入房间。勾选本设备提供的成员，再选择运行位置。")
                self.pages.select(self.progress)
                self.next_refresh = 0
            elif kind == "refresh":
                self.view, worker = result
                self._worker_state(worker)
            elif kind == "worker":
                self._worker_state(result)
                self.next_refresh = 0
            if kind == "refresh":
                self.next_refresh = time.monotonic() + 3
            self.render()
            self._controls()
        if self.ready and self.view and not self.busy and time.monotonic() >= self.next_refresh:
            self.next_refresh = float("inf")
            rpc, room = self.conn.rpc, self.view["room"]
            self._launch("refresh", lambda: (rpc("status", room=room), rpc("worker_status", room=room)))

    def _worker_state(self, result):
        self.remote_running = result["status"] in {"starting", "running", "stopping"}
        self.remote_members = result.get("members", [])
        self.remote_message = result["message"]
