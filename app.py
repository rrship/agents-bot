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
import desktop_style as ui
from bot_sidebar import BotRail
from conversation_feed import ConversationFeed
from software_bridge import Bridge, SOFTWARE_NAMES
from software_ui import SoftwarePanel
from opc_ui import OPCPanel
from opc_runtime import resolve_configs
from opc_skills import LEAD
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from engine import Job
from team_modes import STANDARD, OPC, job_mode
from extra_accounts import EXTRA_ACCOUNTS
from providers import ACCOUNT_NAMES, BUILTIN, ENV_KEYS, MEMBER_NAMES, NAMES, call, launch_login


DEFAULT_MEMBERS = ("GPT", "Gemini")
TASK_MODES = {STANDARD: "常规多智能体协作", OPC: "OPC 一人公司"}
MODES = {"账号登录": "account", "API Key": "api"}
FONT = ui.FONT


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
        root.geometry("1180x760")
        root.minsize(880, 630)
        ui.configure(root)
        self.queue = queue.Queue()
        self.monitor = None
        self.activity = deque(maxlen=100)
        self.stop = threading.Event()
        self.running = False
        self.task_mode = tk.StringVar(value=STANDARD)
        self.opc_routes = None
        self._opc_initialized = False
        self.job = None
        self.active_checks = set()
        self.forms = {}
        self._view_traces = []
        self._account_store = None
        self._bridge = None
        self.bridge_path = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AgentsBot" / "software-bridge.sqlite3"
        self.profile_bindings = {}
        self.team_bot_id = None
        self.cancel_pending = False
        self.project = tk.StringVar()
        self.status = tk.StringVar(value="准备就绪")
        self.sidebar = tk.Frame(root, bg=ui.RAIL, width=176)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.tabs = ttk.Notebook(root, style="Pages.TNotebook", takefocus=False)
        self.tabs.pack(side="left", fill="both", expand=True)
        self.tasks = tk.Frame(self.tabs, bg=ui.BG)
        self.team = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.tasks, text="任务工作台")
        self.tabs.add(self.team, text="成员与连接")
        self._task_view()
        self._team_view()
        self.software = SoftwarePanel(self.tabs, self)
        self.tabs.add(self.software, text="软件协作")
        self.cloud = CloudPanel(self.tabs, self)
        self.tabs.add(self.cloud, text="云服务器")
        self.lite = LitePanel(self.tabs, self)
        self.tabs.add(self.lite, text="内置 Agent 与账号")
        self.opc = OPCPanel(self.tabs, self)
        self.tabs.add(self.opc, text="OPC 一人公司")
        self._navigation()
        self._controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_timer = self.root.after(100, self._poll)
        self.pairing_window = None
        self.restore_timer = self.root.after_idle(self.restore_monitor)
        self.root.bind("<Destroy>", self._cancel_timers, add="+")

    def _cancel_timers(self, event):
        if event.widget is self.root:
            for timer in (self.poll_timer, self.restore_timer):
                if timer:
                    self.root.after_cancel(timer)
            for variable, callback in self._view_traces:
                variable.trace_remove("write", callback)
            self._view_traces.clear()

    def restore_monitor(self):
        self.restore_timer = None
        address = saved_address()
        if address and not self.monitor:
            try:
                self.enable_monitor(address)
            except OSError:
                self.restore_timer = self.root.after(10000, self.restore_monitor)
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

    def _navigation(self):
        self.icons = {name: ui.icon(self.root, name, ui.RAIL_TEXT) for name in ("tasks", "team", "software", "opc", "lite", "cloud", "phone")}
        self.icons["mark"] = ui.icon(self.root, "mark", ui.SURFACE, 24)
        brand = tk.Frame(self.sidebar, bg=ui.RAIL)
        brand.pack(fill="x", padx=15, pady=(24, 30))
        tk.Label(brand, image=self.icons["mark"], bg=ui.RAIL).pack(side="left")
        tk.Label(brand, text="Agents Bot", bg=ui.RAIL, fg=ui.SURFACE,
                 font=("Segoe UI Semibold", 15)).pack(side="left", padx=(7, 0))
        self.nav_buttons = {}
        for page, symbol in ((self.tasks, "tasks"), (self.opc, "opc"), (self.software, "software"), (self.team, "team"),
                             (self.lite, "lite"), (self.cloud, "cloud")):
            label = self.tabs.tab(page, "text")
            btn = self._rail_button(label, symbol, lambda p=page: self._open_page(p))
            btn.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[str(page)] = btn
        self._rail_button("手机监控", "phone", self.mobile_settings).pack(side="bottom", fill="x", padx=8, pady=(4, 16))
        tk.Frame(self.sidebar, bg=ui.RAIL_ACTIVE, height=1).pack(side="bottom", fill="x", padx=15)
        self.bots = BotRail(self.sidebar, self)
        self.tabs.bind("<<NotebookTabChanged>>", self._nav_state)
        self._nav_state()

    def _rail_button(self, text, symbol, command):
        return tk.Button(self.sidebar, text=text, command=command, image=self.icons[symbol], compound="left",
                         font=(FONT, 10), anchor="w", bg=ui.RAIL, fg=ui.RAIL_TEXT, relief="flat",
                         activebackground=ui.RAIL_ACTIVE, activeforeground=ui.SURFACE, cursor="hand2",
                         borderwidth=0, highlightthickness=1, highlightcolor=ui.RAIL_SUB,
                         highlightbackground=ui.RAIL, padx=8, pady=8)

    def _open_page(self, page):
        if page is self.lite:
            self.show_accounts()
            return
        if self.job is None and not self.running and page in (self.opc, self.team):
            self.task_mode.set(OPC if page is self.opc else STANDARD)
            self._controls()
        self.tabs.select(page)

    def _nav_state(self, _event=None):
        current = self.tabs.select()
        if current == str(self.opc):
            self.opc.refresh()
            if not self._opc_initialized:
                self._opc_initialized = True
                try:
                    saved = self.account_store.opc_team()
                    if saved:
                        self.opc.restore(saved)
                except (ValueError, OSError, sqlite3.Error) as exc:
                    messagebox.showerror("读取 OPC 岗位配置", str(exc), parent=self.root)
        in_bot = current == str(self.lite) and self.lite.active_bot is not None
        for page, btn in self.nav_buttons.items():
            selected = page == current and not in_bot
            bg = ui.RAIL_ACTIVE if selected else ui.RAIL
            btn.configure(bg=bg, highlightbackground=bg, fg=ui.SURFACE if selected else ui.RAIL_TEXT)
        self.bots.set_active(self.lite.active_bot["id"] if in_bot else None)

    def use_opc_team(self):
        if self.running or (self.job and job_mode(self.job.state) != OPC):
            return
        try:
            routes = self.opc.selection()
            if self.job and routes != self.job.state["opc_routes"]:
                self.job.rebind_opc(routes)
            self.account_store.save_opc_team(routes)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("配置 OPC 员工", str(exc), parent=self.root)
            return
        self.opc_routes = routes
        self.task_mode.set(OPC)
        self._opc_initialized = True
        self._controls()
        self.tabs.select(self.tasks)

    def _pick_task_mode(self):
        if self.running or self.job:
            return
        self._controls()
        if self.task_mode.get() == OPC and not self.opc_routes:
            self.tabs.select(self.opc)

    def show_bot(self, bot):
        if self.lite.open_bot(bot) is False:
            self.tabs.select(self.lite)
            return False
        self.tabs.select(self.lite)
        self._nav_state()
        return True

    def show_accounts(self):
        self.tabs.select(self.lite)
        self.lite.open_accounts()
        self._nav_state()

    def bot_removed(self, bot_id):
        self.lite.forget_bot(bot_id)
        if self.team_bot_id == bot_id:
            self.team_bot_id = None
            self.profile_bindings.pop(BUILTIN, None)
            self.builtin_binding.set("先选择一个 Bot 或账号，再绑定到团队。")
        self._nav_state()

    def _task_view(self):
        page = self.tasks
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        head = tk.Frame(page, bg=ui.BG)
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(17, 13))
        tk.Label(head, text="任务工作台", bg=ui.BG, fg=ui.INK, font=(FONT, 21, "bold")).pack(side="left")
        tk.Label(head, textvariable=self.status, bg=ui.SUBTLE, fg=ui.INK, padx=10, pady=4,
                 font=(FONT, 9)).pack(side="right")
        selector = ttk.Frame(page, padding=(8, 3))
        selector.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 7))
        ttk.Label(selector, text="任务模式").pack(side="left", padx=(0, 12))
        self.mode_buttons = {}
        for mode, label in TASK_MODES.items():
            button = ttk.Radiobutton(selector, text=label, variable=self.task_mode,
                                     value=mode, command=self._pick_task_mode)
            button.pack(side="left", padx=(0, 16))
            self.mode_buttons[mode] = button
        cards = self.task_cards = tk.Frame(page, bg=ui.BG)
        cards.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        cards.rowconfigure(0, weight=1)
        cards.columnconfigure(0, weight=10, uniform="columns")
        cards.columnconfigure(1, weight=11, uniform="columns")

        def card(column):
            outer = tk.Frame(cards, bg=ui.SURFACE, highlightbackground=ui.BORDER, highlightthickness=1)
            outer.grid(row=0, column=column, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0))
            body = ttk.Frame(outer, padding=(12, 12))
            body.pack(fill="both", expand=True)
            body.columnconfigure(0, weight=1)
            return body

        left = card(0)
        self.settings_card = left.master
        left.rowconfigure(4, weight=1)
        ttk.Label(left, text="任务设置", style="Section.TLabel").grid(row=0, sticky="w", pady=(0, 12))
        ttk.Label(left, text="项目目录").grid(row=1, sticky="w")
        row = ttk.Frame(left)
        row.grid(row=2, sticky="ew", pady=(3, 10))
        row.columnconfigure(0, weight=1)
        self.folder_entry = ttk.Entry(row, textvariable=self.project, width=8)
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.browse = ttk.Button(row, text="选择…", command=self._browse)
        self.browse.grid(row=0, column=1, padx=(5, 0))
        ttk.Label(left, text="需求与验收条件").grid(row=3, sticky="w")
        self.goal = self._text(left, 3)
        self.goal.grid(row=4, sticky="nsew", pady=(4, 8))
        self.goal_hint = tk.Label(self.goal, text="描述要完成的任务，以及怎样算完成。", anchor="nw",
                                  bg=ui.FIELD, fg=ui.MUTED, font=(FONT, 10))
        self.goal_hint.bind("<Button-1>", lambda _event: self.goal.focus_set())
        self.goal.bind("<FocusIn>", lambda _event: self.goal_hint.place_forget())
        self.goal.bind("<FocusOut>", self._goal_hint)
        self.goal.bind("<<Modified>>", self._goal_hint)
        self._goal_hint()
        self.checks_button = ttk.Button(left, text="▸  自动验收命令", command=self._toggle_checks)
        self.checks_button.grid(row=5, sticky="ew")
        self.check_fields = ttk.Frame(left)
        self.check_fields.columnconfigure(0, weight=1)
        self.check_fields.grid(row=6, sticky="ew")
        ttk.Label(self.check_fields, text="构建或测试命令").grid(row=0, sticky="w", pady=(4, 0))
        self.tests = self._text(self.check_fields, 1)
        self.tests.grid(row=1, sticky="ew")
        ttk.Label(self.check_fields, text="启动冒烟检查").grid(row=2, sticky="w", pady=(3, 0))
        self.smoke = self._text(self.check_fields, 1)
        self.smoke.grid(row=3, sticky="ew")
        self.check_fields.grid_remove()
        roster_title = ttk.Frame(left)
        roster_title.grid(row=7, sticky="ew", pady=(6, 3))
        ttk.Label(roster_title, text="参与成员").pack(side="left")
        self.team_link = ttk.Button(left, command=lambda: self.tabs.select(self.opc if self.task_mode.get() == OPC else self.team))
        self.team_link.grid(row=8, sticky="ew")
        self.start_button = ttk.Button(left, text="开始协作", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=9, sticky="ew", pady=(10, 0))

        right = card(1)
        self.chat_card = right.master
        self.chat_focus = False
        self.task_summary = tk.StringVar()
        right.rowconfigure(1, weight=1)
        heading = ttk.Frame(right)
        heading.grid(row=0, sticky="ew", pady=(0, 6))
        ttk.Label(heading, text="团队对话", style="Section.TLabel").pack(side="left")
        self.summary_label = ttk.Label(heading, textvariable=self.task_summary, style="Hint.TLabel")
        self.reopen = ttk.Button(heading, text="载入任务…", command=self.open_job)
        self.reopen.pack(side="right")
        self.feed = ConversationFeed(right)
        self.feed.grid(row=1, sticky="nsew")
        self.log, self.empty_feed = self.feed.log, self.feed.empty_feed
        ttk.Separator(right).grid(row=2, sticky="ew", pady=(6, 5))
        actions = self.task_actions = ttk.Frame(right)
        actions.grid(row=3, sticky="ew")
        for i in range(3):
            actions.columnconfigure(i, weight=1, uniform="actions")
        controls = (("pause", "暂停", self.request_pause), ("resume", "继续", self.continue_job),
                    ("feedback", "补充要求", self.add_feedback), ("cancel_btn", "取消任务", self.cancel_job),
                    ("diff", "改动差异", self.open_diff), ("output", "交付目录", self.open_folder))
        for index, (attr, label, action) in enumerate(controls):
            button = ttk.Button(actions, text=label, command=action)
            button.grid(row=index//3, column=index%3, sticky="ew", padx=1, pady=2)
            setattr(self, attr, button)
        self.new_button = ttk.Button(head, text="新建任务", command=self.new_job)
        self.new_button.pack(side="right", padx=(0, 8))
        self.settings_toggle = ttk.Button(head, text="展开任务设置", command=self._toggle_task_focus)

    def _toggle_task_focus(self):
        self.chat_focus = not self.chat_focus
        self._task_layout()

    def _task_layout(self):
        active = self.running or self.job is not None
        if not active:
            self.settings_toggle.pack_forget()
            self.chat_focus = False
        elif not self.settings_toggle.winfo_manager():
            self.settings_toggle.pack(side="right", padx=(0, 6))
        if self.chat_focus:
            self.settings_card.grid_remove()
            self.task_cards.columnconfigure(0, weight=0, uniform="")
            self.chat_card.grid_configure(column=0, columnspan=2, padx=0)
            self.settings_toggle.configure(text="展开任务设置")
            members = self._members()
            role = f"组长 {self.leader.get()}" if self.team_mode.get() == "leader" else "平等协作"
            self.task_summary.set(f"{len(self.opc_routes)} 位员工   ·   项目经理统筹" if self.task_mode.get() == OPC
                                  else f"{len(members)} 位成员   ·   {role}")
            self.summary_label.pack(side="left", padx=10)
        else:
            self.summary_label.pack_forget()
            self.settings_card.grid()
            self.task_cards.columnconfigure(0, weight=10, uniform="columns")
            self.chat_card.grid_configure(column=1, columnspan=1, padx=(5, 0))
            self.settings_toggle.configure(text="收起任务设置")

    def _goal_hint(self, _event=None):
        if self.goal.edit_modified():
            self.goal.edit_modified(False)
        if self.goal.get("1.0", "end-1c") or self.root.focus_get() == self.goal:
            self.goal_hint.place_forget()
        else:
            self.goal_hint.place(x=10, y=7, relwidth=.93)

    def _toggle_checks(self):
        if self.check_fields.winfo_manager():
            self.check_fields.grid_remove()
            self.checks_button.configure(text="▸  自动验收命令")
        else:
            self.check_fields.grid()
            self.checks_button.configure(text="▾  自动验收命令")

    def _team_view(self):
        heading = ttk.Frame(self.team)
        heading.pack(fill="x", pady=(0, 6))
        ttk.Label(heading, text="常规团队 · 成员与连接", style="Title.TLabel").pack(side="left")
        ttk.Label(self.team, text="至少选择两位成员。模型、内置 Agent 和桌面软件可以混合组队。", style="Hint.TLabel").pack(anchor="w")
        roster = ttk.Frame(self.team)
        roster.pack(fill="x", pady=(6, 8))
        self.member_vars = {name: tk.BooleanVar(value=name in DEFAULT_MEMBERS) for name in MEMBER_NAMES}
        self.team_controls = []
        for index, name in enumerate((*NAMES, BUILTIN)):
            button = ttk.Checkbutton(roster, text=name, variable=self.member_vars[name])
            button.grid(row=index//4, column=index%4, sticky="w", padx=(0, 15))
            self.team_controls.append(button)
        self.software_link = ttk.Button(roster, text="选择软件…", command=lambda: self.tabs.select(self.software))
        self.software_link.grid(row=1, column=3, sticky="w")
        roles = ttk.Frame(self.team)
        roles.pack(anchor="w", pady=(0, 12))
        self.team_mode = tk.StringVar(value="peer")
        self.leader = tk.StringVar()
        for label, value in (("平等协作", "peer"), ("组长统筹", "leader")):
            radio = ttk.Radiobutton(roles, text=label, variable=self.team_mode, value=value)
            radio.pack(side="left", padx=(0, 10))
            self.team_controls.append(radio)
        self.leader_box = ttk.Combobox(roles, textvariable=self.leader, width=18)
        self.leader_box.pack(side="left")
        for variable in (*self.member_vars.values(), self.team_mode, self.leader):
            self._view_traces.append((variable, variable.trace_add("write", self._sync_team)))

        ttk.Label(self.team, text="成员连接", style="Section.TLabel").pack(anchor="w")
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
            ttk.Label(card, text=hint, style="Hint.TLabel", wraplength=440).grid(
                row=3, column=1, sticky="w")
            actions = ttk.Frame(card)
            actions.grid(row=4, column=1, sticky="w", pady=(10, 4))
            test = ttk.Button(actions, text="测试连接", command=lambda n=name: self.test(n))
            test.pack(side="left")
            login = None
            if has_account:
                login = ttk.Button(actions, text="登录账号…", command=lambda n=name: self.login(n))
                login.pack(side="left", padx=6)
            ttk.Label(card, textvariable=info, wraplength=440, style="Hint.TLabel").grid(
                row=5, column=1, sticky="w", pady=4)
            self.forms[name] = dict(mode=mode, model=model, key=key, info=info, test=test, login=login,
                                    entry=key_box, model_box=model_box)
            for var in (mode, model, key):
                callback = var.trace_add("write", lambda *_args, n=name: self._changed(n))
                self._view_traces.append((var, callback))
            self._changed(name)

        self.builtin_binding = tk.StringVar(value="内置 Agent 尚未绑定团队账号")
        binding = ttk.Frame(self.team)
        binding.pack(fill="x", pady=(4, 0))
        ttk.Label(binding, textvariable=self.builtin_binding).pack(side="left")
        ttk.Button(binding, text="选择账号…", command=self.show_accounts).pack(side="right")

    @property
    def account_store(self):
        if self._account_store is None:
            self._account_store = AccountStore()
        return self._account_store

    def apply_profile(self, profile_id, builtin=True, bot_id=None):
        if self.task_mode.get() == OPC:
            raise ValueError("要给 OPC 员工分配账号，请在「OPC 一人公司」的岗位行选择该账号。")
        profile = next((row for row in self.account_store.list_profiles() if row["id"] == profile_id), None)
        if profile is None:
            raise ValueError("账号已删除，请重新选择。")
        member = BUILTIN if builtin else profile["provider"]
        label = f"{profile['name']} · {profile['provider']}"
        if builtin:
            if bot_id:
                bot = self.find_bot(bot_id)
                if bot["profile_id"] != profile_id:
                    raise ValueError("Bot 的账号已更新，请重新选择。")
                label = f"{bot['name']} · {label}"
            self.team_bot_id = bot_id
            self.builtin_binding.set("团队内置 Agent：" + label)
        else:
            form = self.forms[member]
            form["mode"].set("API Key")
            form["model"].set(profile["model"])
            form["key"].set("")
            form["info"].set("已绑定账号：" + label)
        self.profile_bindings[member] = profile_id
        if self.job is None and not self.running:
            self.member_vars[member].set(True)

    def find_bot(self, bot_id):
        bot = next((row for row in self.account_store.list_bots() if row["id"] == bot_id), None)
        if bot is None:
            raise ValueError("此 Bot 已删除，请重新选择。")
        return bot

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
                if self.team_bot_id:
                    try:
                        bot = self.find_bot(self.team_bot_id)
                        self.profile_bindings[member] = bot["profile_id"]
                        account = records.get(bot["profile_id"])
                        label = f"{bot['name']} · {account['name']} / {account['model']}" if account else "绑定已失效"
                    except ValueError:
                        label = "绑定已失效，请重新选择 Bot"
                self.builtin_binding.set("团队内置 Agent：" + label)
            else:
                if row is not None and row["provider"] == member:
                    self.forms[member]["model"].set(row["model"])
                    self.profile_bindings[member] = profile_id
                self.forms[member]["info"].set(label)

    def _members(self):
        return [name for name, selected in self.member_vars.items() if selected.get()]

    def _sync_team(self, *_args):
        members = self._members()
        count = sum(name in SOFTWARE_NAMES for name in members)
        self.software_link.configure(text=f"软件成员 ({count})…")
        editable = self.job is None and not self.running and self.task_mode.get() == STANDARD
        leader_mode = self.team_mode.get() == "leader"
        self.leader_box.configure(values=members, state="readonly" if editable and leader_mode and members else "disabled")
        if self.leader.get() not in members:
            fallback = members[0] if members else ""
            if self.leader.get() != fallback:
                self.leader.set(fallback)
        summary = "、".join(members) if len(members) <= 3 else f"{len(members)} 位成员"
        summary = summary or "未选择成员"
        role = f"组长：{self.leader.get()}" if leader_mode else "平等协作"
        if self.task_mode.get() == OPC:
            label = f"OPC 一人公司  ·  {len(self.opc_routes)} 位员工  ›" if self.opc_routes else "配置 OPC 岗位与连接  ›"
        else:
            label = f"{summary}  ·  {role}  ›"
        self.team_link.configure(text=label)
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
        return ui.style_text(ScrolledText(parent, height=height, width=12, wrap="word", undo=True), editor=True)

    def _config(self, name):
        if name in SOFTWARE_NAMES:
            return self.software.config(name)
        if name in self.profile_bindings:
            try:
                bot = self.find_bot(self.team_bot_id) if name == BUILTIN and self.team_bot_id else None
                cfg = dict(self.account_store.resolve(bot["profile_id"] if bot else self.profile_bindings[name]))
                if bot and bot["instructions"]:
                    cfg["instructions"] = bot["instructions"]
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
        self._task_layout()
        new = self.job is None and not self.running
        for button in self.mode_buttons.values():
            button.configure(state="normal" if new else "disabled")
        opc = self.task_mode.get() == OPC
        self.software.set_opc_context(opc)
        self.opc.connect_button.configure(state="normal" if opc else "disabled")
        self.opc.set_locked(self.running or bool(self.job and (not opc or
                            self.job.state["status"] in {"完成", "已取消", "待人工验收"})), self.job is not None)
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
        if self.job or self.running:
            self.task_actions.grid()
            if not self.new_button.winfo_manager():
                self.new_button.pack(side="right", padx=(0, 8))
        else:
            self.task_actions.grid_remove()
            self.new_button.pack_forget()
        delivered = self.job and (Path(self.job.path) / "project").is_dir()
        self.output.configure(state="normal" if delivered else "disabled")

    def _append(self, line):
        self.activity.append(str(line)[-4000:])
        self.feed.append(str(line))

    def _clear_log(self):
        self.activity.clear()
        self.feed.clear()

    def bridge(self):
        if self._bridge is None:
            try:
                self._bridge = Bridge(self.bridge_path)
            except (OSError, sqlite3.Error) as exc:
                raise ValueError(f"无法打开本机软件协作队列：{exc}") from exc
        return self._bridge

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
            if self.task_mode.get() == OPC:
                if not self.opc_routes:
                    self.tabs.select(self.opc)
                    raise ValueError("请在「OPC 一人公司」配置出勤员工和连接，再点「进入任务工作台」。")
                configs = resolve_configs(self.opc_routes, self.account_store, self.bridge)
                creation = (root, goal, checks + smoke, list(self.opc_routes), LEAD, self.opc_routes)
            else:
                members = self._members()
                if len(members) < 2:
                    raise ValueError("请在“成员与连接”至少选择两位成员。")
                leader = self.leader.get() if self.team_mode.get() == "leader" else None
                configs = {name: self._config(name) for name in members}
                creation = (root, goal, checks + smoke, members, leader)
        except (ValueError, OSError, sqlite3.Error) as e:
            messagebox.showerror("无法启动", str(e), parent=self.root)
            return
        self._clear_log()
        self._launch(configs, creation)

    def continue_job(self):
        try:
            if job_mode(self.job.state) == OPC:
                configs = resolve_configs(self.job.state["opc_routes"], self.account_store, self.bridge)
            else:
                configs = {name: self._config(name) for name in self.job.state.get("members", DEFAULT_MEMBERS)}
        except (ValueError, OSError, sqlite3.Error) as e:
            messagebox.showerror("连接设置", str(e), parent=self.root)
            return
        self._launch(configs)

    def _launch(self, configs, creation=None):
        mode = self.task_mode.get()
        self.stop.clear()
        self.running = True
        if creation:
            self.chat_focus = True
        self.status.set("协作运行中")
        self._controls()
        job = self.job

        def work():
            try:
                active = Job.create(*creation, mode=mode) if creation else job
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
        self.task_mode.set(job_mode(state))
        if self.task_mode.get() == OPC:
            self.opc_routes = state["opc_routes"]
            self.opc.refresh()
            self.opc.restore(self.opc_routes)
            self._opc_initialized = True
        else:
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
        self.chat_focus = True
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
        self.software.poll()
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
        self.poll_timer = self.root.after(100, self._poll)

    def close(self):
        if self.running:
            self.request_pause()
            messagebox.showinfo("等待暂停", "当前步骤结束并保存后即可关闭窗口。", parent=self.root)
            return
        if self.monitor:
            self.monitor.close()
        self.lite.close()
        self.cloud.disconnect()
        if self._bridge is not None:
            self._bridge.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    Desk(root)
    root.mainloop()


if __name__ == "__main__":
    main()
