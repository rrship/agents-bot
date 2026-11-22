"""Native desktop client for the multi-member Agents Bot workbench."""
import json
import os
import queue
import sqlite3
import threading
from collections import deque
from mobile import Monitor, device_key, saved_address
from pairing_ui import PairingWindow
from cloud_ui import CloudPanel
from lite_ui import LitePanel
from account_store import AccountStore
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from engine import Job
from extra_accounts import EXTRA_ACCOUNTS
from providers import ACCOUNT_NAMES, BUILTIN, ENV_KEYS, MEMBER_NAMES, NAMES, call, launch_login


DEFAULT_MEMBERS = ("GPT", "Gemini")
MODES = {"账号登录": "account", "API Key": "api"}
BG = "#f1f4f7"
WHITE = "#ffffff"
INK = "#182b3c"
MUTED = "#4c6170"
ACCENT = "#126b66"
FONT = "Microsoft YaHei UI"


def parse_commands(text):
    commands = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            args = json.loads(line)
        except ValueError as error:
            raise ValueError(f"命令第 {number} 行需要使用 JSON 数组，字符串请用双引号。") from error
        if not isinstance(args, list) or not args or not all(isinstance(x, str) and x.strip() for x in args):
            raise ValueError(f"命令第 {number} 行必须包含程序名和可选参数。")
        commands.append(args)
    return commands


class Desk:
    def __init__(self, root):
        self.root = root
        root.title("Agents Bot · 多智能体开发工作台")
        root.geometry("1080x740")
        root.minsize(880, 630)
        root.configure(bg=BG)
        root.option_add("*Font", (FONT, 10))
        self.queue = queue.Queue()
        self.monitor = None
        self.activity = deque(maxlen=100)
        self.stop = threading.Event()
        self.running = False
        self.job = None
        self.active_checks = set()
        self.forms = {}
        self._account_store = None
        self.profile_bindings = {}
        self.cancel_pending = False
        self.project = tk.StringVar()
        self.status = tk.StringVar(value="准备就绪")
        self._style()
        header = tk.Frame(root, bg=INK, padx=22, pady=13)
        header.pack(fill="x")
        tk.Label(header, text="Agents Bot", font=("Segoe UI Semibold", 19), bg=INK, fg=WHITE).pack(side="left")
        tk.Label(header, text="多智能体开发工作台", fg="#c0d7df", bg=INK, padx=16).pack(side="left")
        tk.Label(header, textvariable=self.status, bg="#294858", fg=WHITE, padx=10, pady=4).pack(side="right")
        ttk.Button(header, text="手机监控", command=self.mobile_settings).pack(side="right", padx=8)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=15, pady=12)
        self.tasks = ttk.Frame(self.tabs, padding=12)
        self.team = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.tasks, text="任务工作台")
        self.tabs.add(self.team, text="成员与连接")
        self._task_view()
        self._team_view()
        self.cloud = CloudPanel(self.tabs, self)
        self.tabs.add(self.cloud, text="云服务器")
        self.lite = LitePanel(self.tabs, self)
        self.tabs.add(self.lite, text="内置 Agent 与账号")
        self._controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        self.pairing_window = None
        self.root.after_idle(self.restore_monitor)

    def restore_monitor(self):
        address = saved_address()
        if address and not self.monitor:
            try:
                self.enable_monitor(address)
            except OSError:
                self.root.after(10000, self.restore_monitor)
            except ValueError:
                pass

    def enable_monitor(self, address):
        if self.monitor and self.monitor.server.server_address[0] == address:
            return
        replacement = Monitor(address, device_key(), 48763)
        if self.monitor:
            self.monitor.close()
        self.monitor = replacement
        self.publish_mobile()

    def mobile_settings(self):
        if self.pairing_window and self.pairing_window.winfo_exists():
            self.pairing_window.lift()
            return
        self.pairing_window = PairingWindow(self)

    def publish_mobile(self):
        if self.monitor:
            cloud = self.cloud.phone_snapshot()
            if cloud:
                self.monitor.publish(*cloud)
                return
            state = self.job.state if self.job else {}
            self.monitor.publish(state.get("goal", ""), self.status.get(), state.get("members", []),
                                 state.get("leader"), self.activity)

    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", font=(FONT, 10), foreground=INK)
        s.configure("TFrame", background=WHITE)
        s.configure("TLabel", background=WHITE, foreground=INK)
        s.configure("Hint.TLabel", foreground=MUTED)
        s.configure("Title.TLabel", font=(FONT, 14, "bold"))
        s.configure("TButton", padding=(9, 5))
        s.configure("Accent.TButton", background=ACCENT, foreground=WHITE)
        s.map("Accent.TButton", background=[("disabled", "#879694"), ("active", "#095953")])
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(15, 7))

    def _task_view(self):
        self.tasks.columnconfigure(0, weight=3, uniform="main")
        self.tasks.columnconfigure(1, weight=4, uniform="main")
        self.tasks.rowconfigure(0, weight=1)
        left = ttk.Frame(self.tasks, padding=(0, 0, 14, 0))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(4, weight=1)
        ttk.Label(left, text="开发任务", style="Title.TLabel").grid(sticky="w", pady=(0, 8))
        ttk.Label(left, text="项目目录").grid(row=1, sticky="w")
        row = ttk.Frame(left)
        row.grid(row=2, sticky="ew", pady=(3, 9))
        row.columnconfigure(0, weight=1)
        self.folder_entry = ttk.Entry(row, textvariable=self.project)
        self.folder_entry.grid(sticky="ew")
        self.browse = ttk.Button(row, text="选择…", command=self._browse)
        self.browse.grid(row=0, column=1, padx=(4, 0))
        ttk.Label(left, text="需求与验收条件").grid(row=3, sticky="w")
        self.goal = self._text(left, 5)
        self.goal.grid(row=4, sticky="nsew", pady=(3, 9))
        ttk.Label(left, text="构建或测试命令").grid(row=5, sticky="w")
        self.tests = self._text(left, 2)
        self.tests.grid(row=6, sticky="ew", pady=(3, 6))
        ttk.Label(left, text="启动冒烟检查").grid(row=7, sticky="w")
        self.smoke = self._text(left, 2)
        self.smoke.grid(row=8, sticky="ew", pady=(3, 3))
        ttk.Label(left, style="Hint.TLabel", wraplength=330,
                  text='每行一个命令，例如 ["python", "smoke.py"]。冒烟脚本需启动软件、检查并退出。两栏均留空时交付物等待人工验收。').grid(row=9, sticky="w")
        self.team_link = ttk.Button(left, command=lambda: self.tabs.select(self.team))
        self.team_link.grid(row=10, sticky="ew", pady=(6, 0))
        self.start_button = ttk.Button(left, text="开始协作", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=11, sticky="ew", pady=(10, 0))

        right = ttk.Frame(self.tasks, padding=(12, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        ttk.Label(right, text="协作记录", style="Title.TLabel").grid(sticky="w", pady=(0, 8))
        self.log = ScrolledText(right, wrap="word", font=(FONT, 10), bg="#f8fafb", fg=INK,
                                relief="solid", borderwidth=1, padx=10, pady=8, state="disabled")
        self.log.grid(row=1, sticky="nsew")
        buttons = ttk.Frame(right)
        buttons.grid(row=2, sticky="ew", pady=(9, 0))
        self.pause = ttk.Button(buttons, text="暂停", command=self.request_pause)
        self.pause.pack(side="left")
        self.resume = ttk.Button(buttons, text="继续", command=self.continue_job)
        self.resume.pack(side="left", padx=3)
        self.reopen = ttk.Button(buttons, text="载入任务…", command=self.open_job)
        self.reopen.pack(side="left")
        self.output = ttk.Button(buttons, text="交付目录", command=self.open_folder)
        self.output.pack(side="right")
        more = ttk.Frame(right)
        more.grid(row=3, sticky="ew", pady=(5, 0))
        self.feedback = ttk.Button(more, text="补充要求", command=self.add_feedback)
        self.feedback.pack(side="left")
        self.diff = ttk.Button(more, text="改动差异", command=self.open_diff)
        self.diff.pack(side="left", padx=3)
        self.cancel_btn = ttk.Button(more, text="取消任务", command=self.cancel_job)
        self.cancel_btn.pack(side="left")
        self.new_button = ttk.Button(more, text="新建任务", command=self.new_job)
        self.new_button.pack(side="right")

    def _team_view(self):
        ttk.Label(self.team, text="任务成员", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.team, text="选择 2 至 7 位成员。内置 Agent 使用「内置 Agent 与账号」中绑定的 API 账号。", style="Hint.TLabel").pack(anchor="w")
        roster = ttk.Frame(self.team)
        roster.pack(anchor="w", pady=(4, 8))
        self.member_vars = {}
        self.team_controls = []
        for name in MEMBER_NAMES:
            member = tk.BooleanVar(value=name in DEFAULT_MEMBERS)
            self.member_vars[name] = member
            button = ttk.Checkbutton(roster, text=name, variable=member)
            button.pack(side="left", padx=(0, 12))
            self.team_controls.append(button)
        roles = ttk.Frame(self.team)
        roles.pack(anchor="w", pady=(0, 12))
        self.team_mode = tk.StringVar(value="peer")
        self.leader = tk.StringVar()
        for label, value in (("平等协作", "peer"), ("组长统筹", "leader")):
            radio = ttk.Radiobutton(roles, text=label, variable=self.team_mode, value=value)
            radio.pack(side="left", padx=(0, 10))
            self.team_controls.append(radio)
        self.leader_box = ttk.Combobox(roles, textvariable=self.leader, width=12)
        self.leader_box.pack(side="left")
        for variable in (*self.member_vars.values(), self.team_mode, self.leader):
            variable.trace_add("write", self._sync_team)

        ttk.Label(self.team, text="成员连接", style="Title.TLabel").pack(anchor="w")
        self.connections = ttk.Notebook(self.team)
        self.connections.pack(fill="both", expand=True, pady=(5, 0))
        for name in NAMES:
            card = ttk.Frame(self.connections, padding=12)
            self.connections.add(card, text=name)
            card.columnconfigure(1, weight=1)
            mode, model, key, info = (tk.StringVar() for _ in range(4))
            has_account = name in ACCOUNT_NAMES
            mode.set("账号登录" if has_account else "API Key")
            ttk.Label(card, text="接入方式").grid(row=0, column=0, sticky="w", padx=(0, 12))
            selector = ttk.Combobox(card, values=list(MODES) if has_account else ["API Key"],
                                    textvariable=mode, state="readonly")
            selector.grid(row=0, column=1, sticky="ew", pady=4)
            ttk.Label(card, text="模型 ID").grid(row=1, column=0, sticky="w")
            model_box = ttk.Entry(card, textvariable=model)
            model_box.grid(row=1, column=1, sticky="ew", pady=4)
            ttk.Label(card, text="API Key").grid(row=2, column=0, sticky="w")
            key_box = ttk.Entry(card, textvariable=key, show="•")
            key_box.grid(row=2, column=1, sticky="ew", pady=4)
            hint = f"密钥仅在当前窗口有效。留空时读取 {ENV_KEYS[name]}。"
            if name in EXTRA_ACCOUNTS:
                hint += f"账号模式请在 {EXTRA_ACCOUNTS[name]} 中选择模型。"
            elif has_account:
                hint += "账号模式可留空模型 ID。"
            ttk.Label(card, text=hint, style="Hint.TLabel", wraplength=600).grid(
                row=3, column=1, sticky="w")
            actions = ttk.Frame(card)
            actions.grid(row=4, column=1, sticky="w", pady=(10, 4))
            test = ttk.Button(actions, text="测试连接", command=lambda n=name: self.test(n))
            test.pack(side="left")
            login = None
            if has_account:
                login = ttk.Button(actions, text="登录账号…", command=lambda n=name: self.login(n))
                login.pack(side="left", padx=6)
            ttk.Label(card, textvariable=info, wraplength=580, style="Hint.TLabel").grid(
                row=5, column=1, sticky="w", pady=4)
            self.forms[name] = dict(mode=mode, model=model, key=key, info=info, test=test, login=login,
                                    entry=key_box, model_box=model_box)
            for var in (mode, model, key):
                var.trace_add("write", lambda *_args, n=name: self._changed(n))
            self._changed(name)

        self.builtin_binding = tk.StringVar(value="内置 Agent 尚未绑定团队账号")
        binding = ttk.Frame(self.team)
        binding.pack(fill="x", pady=(4, 0))
        ttk.Label(binding, textvariable=self.builtin_binding).pack(side="left")
        ttk.Button(binding, text="选择账号…", command=lambda: self.tabs.select(self.lite)).pack(side="right")

    @property
    def account_store(self):
        if self._account_store is None:
            self._account_store = AccountStore()
        return self._account_store

    def apply_profile(self, profile_id, builtin=True):
        profile = next((row for row in self.account_store.list_profiles() if row["id"] == profile_id), None)
        if profile is None:
            raise ValueError("账号已删除，请重新选择。")
        member = BUILTIN if builtin else profile["provider"]
        label = f"{profile['name']} · {profile['provider']}"
        if builtin:
            self.builtin_binding.set("内置 Agent 团队账号：" + label)
        else:
            form = self.forms[member]
            form["mode"].set("API Key")
            form["model"].set(profile["model"])
            form["key"].set("")
            form["info"].set("已绑定账号：" + label)
        self.profile_bindings[member] = profile_id
        if self.job is None and not self.running:
            self.member_vars[member].set(True)

    def refresh_bound_profiles(self, profiles):
        records = {row["id"]: row for row in profiles}
        for member, profile_id in list(self.profile_bindings.items()):
            row = records.get(profile_id)
            if row is None or member != BUILTIN and row["provider"] != member:
                # Keep the invalid binding until the user rebinds or edits this member.
                # A deleted account must never silently fall back to an environment key.
                label = "绑定已失效，请重新选择账号"
            else:
                label = f"{row['name']} · {row['provider']} / {row['model']}"
            if member == BUILTIN:
                self.builtin_binding.set("内置 Agent 团队账号：" + label)
            else:
                if row is not None and row["provider"] == member:
                    self.forms[member]["model"].set(row["model"])
                    self.profile_bindings[member] = profile_id
                self.forms[member]["info"].set(label)

    def _members(self):
        return [name for name, selected in self.member_vars.items() if selected.get()]

    def _sync_team(self, *_args):
        members = self._members()
        editable = self.job is None and not self.running
        leader_mode = self.team_mode.get() == "leader"
        self.leader_box.configure(values=members, state="readonly" if editable and leader_mode and members else "disabled")
        if self.leader.get() not in members:
            fallback = members[0] if members else ""
            if self.leader.get() != fallback:
                self.leader.set(fallback)
        summary = "、".join(members) if len(members) <= 3 else f"{len(members)} 位成员"
        summary = summary or "未选择成员"
        role = f"组长：{self.leader.get()}" if leader_mode else "平等协作"
        self.team_link.configure(text=f"{summary}  ·  {role}  ›")
        for widget in self.team_controls:
            widget.configure(state="normal" if editable else "disabled")

    def _changed(self, name):
        self.profile_bindings.pop(name, None)
        form = self.forms[name]
        account = form["mode"].get() == "账号登录"
        form["entry"].configure(state="disabled" if account else "normal")
        form["model_box"].configure(state="disabled" if account and name in EXTRA_ACCOUNTS else "normal")
        if form["login"] is not None:
            form["login"].configure(state="normal" if account else "disabled")
        form["info"].set("待测试")

    @staticmethod
    def _text(parent, height):
        return ScrolledText(parent, height=height, width=16, wrap="word", font=(FONT, 10), fg=INK,
                            bg=WHITE, relief="solid", borderwidth=1, padx=6, pady=4, undo=True)

    def _config(self, name):
        if name in self.profile_bindings:
            try:
                cfg = dict(self.account_store.resolve(self.profile_bindings[name]))
            except (ValueError, LookupError, OSError, RuntimeError, sqlite3.Error) as error:
                raise ValueError("绑定的账号不可用，请在「内置 Agent 与账号」检查设置或重新绑定。") from error
            if name != BUILTIN and cfg.pop("provider") != name:
                raise ValueError("账号的模型平台已改变，请重新绑定对应成员。")
            return cfg
        if name == BUILTIN:
            raise ValueError("请在「内置 Agent 与账号」中选择 API 账号，并将它用于团队内置 Agent。")
        fields = self.forms[name]
        mode = MODES[fields["mode"].get()]
        model = "" if mode == "account" and name in EXTRA_ACCOUNTS else fields["model"].get().strip()
        cfg = {"mode": mode, "model": model, "key": fields["key"].get().strip() if mode == "api" else ""}
        if cfg["mode"] == "api" and not cfg["model"]:
            raise ValueError(f"请在“成员与连接”填写 {name} 的 API 模型 ID。")
        return cfg

    def login(self, name):
        try:
            launch_login(name)
            self.forms[name]["info"].set("请在登录窗口完成授权，再点击测试连接。")
        except Exception as error:
            messagebox.showerror(f"{name} 账号登录", str(error), parent=self.root)

    def test(self, name):
        try:
            config = self._config(name)
        except ValueError as e:
            messagebox.showerror("连接设置", str(e), parent=self.root)
            return
        self.active_checks.add(name)
        self.forms[name]["test"].configure(state="disabled")
        self.forms[name]["info"].set("正在测试…")

        def work():
            try:
                reply = call(name, config, "Reply only OK.")
                msg = "连接成功 · " + (config["model"] or "客户端默认模型") if reply.strip() else "服务返回了空内容"
            except Exception as error:
                msg = f"连接失败 · {error}"
            self.queue.put(("tested", (name, config, msg)))
        threading.Thread(target=work, daemon=True).start()

    def _browse(self):
        path = filedialog.askdirectory(title="选择项目目录", parent=self.root, mustexist=True)
        if path:
            self.project.set(path)

    def _controls(self):
        self._sync_team()
        new = self.job is None and not self.running
        self.start_button.configure(state="normal" if new else "disabled")
        self.browse.configure(state="normal" if new else "disabled")
        self.folder_entry.configure(state="normal" if new else "disabled")
        for widget in (self.goal, self.tests, self.smoke):
            widget.configure(state="normal" if new else "disabled")
        self.pause.configure(state="normal" if self.running and not self.stop.is_set() else "disabled")
        status = self.job.state.get("status", "") if self.job and not self.running else ""
        terminal = status in ("完成", "已取消", "待人工验收")
        resumable = self.job and not self.running and not terminal
        self.resume.configure(state="normal" if resumable else "disabled")
        self.cancel_btn.configure(state="normal" if self.job and not terminal and not self.cancel_pending else "disabled")
        self.feedback.configure(state="normal" if resumable else "disabled")
        self.diff.configure(state="normal" if self.job and not self.running else "disabled")
        self.reopen.configure(state="disabled" if self.running else "normal")
        self.new_button.configure(state="normal" if self.job and not self.running else "disabled")
        delivered = self.job and (Path(self.job.path) / "project").is_dir()
        self.output.configure(state="normal" if delivered else "disabled")

    def _append(self, line):
        self.activity.append(str(line)[-4000:])
        self.log.configure(state="normal")
        self.log.insert("end", str(line) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.activity.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def start(self):
        try:
            root = self.project.get().strip()
            if not root or not Path(root).is_dir():
                raise ValueError("请选择一个存在的项目目录。新项目可以选择空目录。")
            goal = self.goal.get("1.0", "end").strip()
            if not goal:
                raise ValueError("请填写需求与验收条件。")
            checks = parse_commands(self.tests.get("1.0", "end"))
            smoke = parse_commands(self.smoke.get("1.0", "end"))
            if bool(checks) != bool(smoke):
                raise ValueError("自动验收需要同时填写测试和冒烟检查。两栏均留空可采用人工验收。")
            members = self._members()
            if len(members) < 2:
                raise ValueError("请在“成员与连接”至少选择两位成员。")
            leader = self.leader.get() if self.team_mode.get() == "leader" else None
            configs = {name: self._config(name) for name in members}
        except ValueError as e:
            messagebox.showerror("无法启动", str(e), parent=self.root)
            return
        self._clear_log()
        self._launch(configs, (root, goal, checks + smoke, members, leader))

    def continue_job(self):
        try:
            configs = {name: self._config(name) for name in self.job.state.get("members", DEFAULT_MEMBERS)}
        except ValueError as e:
            messagebox.showerror("连接设置", str(e), parent=self.root)
            return
        self._launch(configs)

    def _launch(self, configs, creation=None):
        self.stop.clear()
        self.running = True
        self.status.set("协作运行中")
        self._controls()
        job = self.job

        def work():
            try:
                active = Job.create(*creation) if creation else job
                self.queue.put(("job", active))
                result = active.run(configs, lambda s: self.queue.put(("log", str(s))), self.stop)
                self.queue.put(("done", str(result)))
            except Exception as e:
                self.queue.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def request_pause(self):
        self.stop.set()
        self.status.set("正在暂停")
        self._append("已请求暂停，正在等待当前步骤结束。")
        self._controls()

    def cancel_job(self):
        if not messagebox.askyesno("取消任务", "终止本次协作并保留已生成的文件和记录？取消后不能继续。", parent=self.root):
            return
        if self.running:
            self.cancel_pending = True
            self.request_pause()
            self.status.set("正在取消")
        else:
            self._finish_cancel()

    def _finish_cancel(self):
        self.cancel_pending = False
        try:
            self.job.cancel()
            self.status.set(self.job.state["status"])
            self._append(self.status.get())
        except Exception as error:
            self._append(f"取消失败 · {error}")
        self._controls()

    def add_feedback(self):
        text = simpledialog.askstring("补充要求", "输入新的要求，成员将在后续协作中收到。", parent=self.root)
        if text and text.strip():
            try:
                self.job.add_feedback(text.strip())
                self._append("用户补充 · " + text.strip())
                self._load_fields(self.job)
                self.status.set(self.job.state["status"])
                self._controls()
            except Exception as error:
                messagebox.showerror("补充要求", str(error), parent=self.root)

    def new_job(self):
        self.job = None
        self.status.set("准备就绪")
        self._controls()
        self._clear_log()
        self.goal.delete("1.0", "end")

    def _load_fields(self, job):
        state = job.state
        members = state.get("members", DEFAULT_MEMBERS)
        for name, member in self.member_vars.items():
            member.set(name in members)
        leader = state.get("leader")
        self.team_mode.set("leader" if leader else "peer")
        if leader:
            self.leader.set(leader)
        self.project.set(str(state.get("root", state.get("source", ""))))
        commands = [json.dumps(cmd, ensure_ascii=False) for cmd in state.get("checks", [])]
        for field, value in ((self.goal, state["goal"]), (self.tests, "\n".join(commands[:-1])),
                             (self.smoke, "\n".join(commands[-1:]))):
            field.configure(state="normal")
            field.delete("1.0", "end")
            field.insert("1.0", value)

    def open_job(self):
        path = filedialog.askdirectory(title="选择已保存的任务目录", parent=self.root, mustexist=True)
        if not path:
            return
        try:
            job = Job.load(path)
        except Exception as e:
            messagebox.showerror("无法载入", str(e), parent=self.root)
            return
        self.job = job
        self._load_fields(job)
        self._clear_log()
        log_path = Path(job.path) / "events.txt"
        if log_path.is_file():
            with log_path.open("rb") as stream:
                stream.seek(max(0, log_path.stat().st_size - 128000))
                self._append(stream.read().decode("utf-8", errors="replace"))
        self._append(f"已载入任务 {job.path}")
        self.status.set(job.state["status"])
        self._controls()

    def open_folder(self):
        if self.job:
            try:
                os.startfile(str(Path(self.job.path) / "project"))
            except OSError as error:
                messagebox.showerror("打开目录", str(error), parent=self.root)

    def open_diff(self):
        from changes import write_diff
        try:
            os.startfile(str(write_diff(self.job)))
        except Exception as error:
            messagebox.showerror("查看差异", str(error), parent=self.root)

    def _poll(self):
        self.cloud.poll()
        self.lite.poll()
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "job":
                self.job = value
                self._append(f"任务目录  {value.path}")
                self._controls()
            elif kind == "log":
                self._append(value)
            elif kind == "tested":
                name, config, msg = value
                self.active_checks.discard(name)
                form = self.forms[name]
                form["test"].configure(state="normal")
                try:
                    if self._config(name) == config:
                        form["info"].set(msg)
                except ValueError:
                    pass
            else:
                self.running = False
                self.status.set(value if kind == "done" else "任务阻塞")
                self._append(value)
                self._controls()
                if self.cancel_pending:
                    self._finish_cancel()
        if self.monitor:
            self.publish_mobile()
        self.root.after(100, self._poll)

    def close(self):
        if self.running:
            self.request_pause()
            messagebox.showinfo("等待暂停", "当前步骤结束并保存后即可关闭窗口。", parent=self.root)
            return
        if self.monitor:
            self.monitor.close()
        self.lite.close()
        self.cloud.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    Desk(root)
    root.mainloop()


if __name__ == "__main__":
    main()
