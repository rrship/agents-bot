"""Configure the employees and model connections for a one-person company."""
import sqlite3
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import desktop_style as ui
from opc_skills import LEAD, ROLE_NAMES, role_info
from software_bridge import SOFTWARE_NAMES


class OPCPanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=(16, 12))
        self.desk = desk
        self.enabled = {}
        self.checks = {}
        self.boxes = {}
        self.details_buttons = {}
        self.bindings = {}
        self.options = {}
        self.bulk_route = None
        self.running = False
        self.saved_job = False
        self.active_role = LEAD
        self.note = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="OPC 一人公司", style="Title.TLabel").pack(side="left")
        self.start_button = ttk.Button(header, text="进入任务工作台", style="Accent.TButton",
                                      command=desk.use_opc_team)
        self.start_button.pack(side="right")
        ttk.Label(self, text="你给出项目目标，项目经理负责派单和验收。为每位出勤员工指定一个 API 账号或本地软件。",
                  style="Hint.TLabel", wraplength=590).grid(row=1, sticky="w", pady=(3, 9))

        bulk = ttk.Frame(self)
        bulk.grid(row=2, sticky="ew")
        bulk.columnconfigure(1, weight=1)
        ttk.Label(bulk, text="统一连接", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.bulk_box = ttk.Combobox(bulk, state="readonly", width=20)
        self.bulk_box.grid(row=0, column=1, sticky="ew")
        self.bulk_box.bind("<<ComboboxSelected>>", self._choose_bulk)
        self.bulk_button = ttk.Button(bulk, text="应用到出勤员工", command=self._apply_bulk)
        self.bulk_button.grid(row=0, column=2, padx=(6, 0))

        roster = ttk.Frame(self)
        roster.grid(row=3, sticky="ew", pady=(10, 7))
        roster.columnconfigure(1, weight=1)
        for col, label in enumerate(("出勤岗位", "工作连接", "岗位手册")):
            ttk.Label(roster, text=label, style="Hint.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 6))
        for row, role in enumerate(ROLE_NAMES, 1):
            enabled = tk.BooleanVar(self, True)
            self.enabled[role] = enabled
            check = ttk.Checkbutton(roster, text=role, variable=enabled,
                                    command=lambda name=role: self._toggle(name))
            check.grid(row=row, column=0, sticky="w", padx=(0, 10))
            self.checks[role] = check
            box = ttk.Combobox(roster, state="readonly", width=17)
            box.grid(row=row, column=1, sticky="ew", pady=2, padx=(0, 7))
            box.bind("<<ComboboxSelected>>", lambda event, name=role: self._choose(name))
            self.boxes[role] = box
            details = ttk.Button(roster, text="看技能", command=lambda name=role: self.show_skill(name))
            details.grid(row=row, column=2, sticky="e")
            self.details_buttons[role] = details

        self.skill_label = ttk.Label(self, style="Section.TLabel")
        self.skill_label.grid(row=4, sticky="w", pady=(2, 4))
        self.handbook = ScrolledText(self, wrap="word", width=16, height=4, state="disabled", takefocus=True)
        ui.style_text(self.handbook)
        self.handbook.configure(wrap="char")
        self.handbook.tag_configure("heading", font=(ui.FONT, 10, "bold"), spacing1=7)
        self.handbook.tag_configure("markup", elide=True)
        self.handbook.tag_configure("skills", font=(ui.FONT, 10, "bold"), foreground=ui.ACCENT, spacing3=4)
        self.handbook.tag_configure("sources", foreground=ui.MUTED, font=(ui.FONT, 9), spacing1=4)
        self.handbook.grid(row=5, column=0, sticky="nsew")
        footer = ttk.Frame(self)
        footer.grid(row=6, sticky="ew", pady=(7, 0))
        self.accounts_button = ttk.Button(footer, text="配置 API 账号", command=desk.show_accounts)
        self.accounts_button.pack(side="left", padx=(0, 6))
        self.connect_button = ttk.Button(footer, text="连接本地软件", command=self._open_software)
        self.connect_button.pack(side="left")
        self.notice_label = ttk.Label(self, textvariable=self.note, style="Hint.TLabel", wraplength=590)
        self.notice_label.grid(row=7, sticky="ew", pady=(3, 0))
        self._populate([])
        self.show_skill(LEAD)
        self.set_locked(False, False)

    def _populate(self, profiles):
        options = {}
        for profile in profiles:
            key = ("api", profile["id"])
            label = f"API · {profile['name']}（{profile['provider']} / {profile['model']}）"
            options[key] = label
        options.update({("mcp", app): f"MCP · {app}" for app in SOFTWARE_NAMES})
        self.options = options
        labels = tuple(options.values())
        self.bulk_box.configure(values=labels)
        self.bulk_box.set(options.get(self.bulk_route, "选择 API 账号或 MCP 软件"))
        for name, box in self.boxes.items():
            box.configure(values=labels)
            route = self.bindings.get(name)
            box.set(options.get(route, "连接已失效，请重选" if route else "请选择连接"))

    def refresh(self):
        try:
            profiles = self.desk.account_store.list_profiles()
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            self._populate([])
            self.note.set("暂时无法读取 API 账号。可重开此页刷新，或选择 MCP 软件。")
        else:
            self._populate(profiles)
            self.note.set("")

    def _current_route(self, box):
        index = box.current()
        return tuple(self.options)[index] if index >= 0 else None

    def _choose_bulk(self, _event):
        self.bulk_route = self._current_route(self.bulk_box)
        self.note.set("")

    def _apply_bulk(self):
        if self.running:
            return
        route = self._current_route(self.bulk_box)
        if route is None:
            self.note.set("请先在「统一连接」选择一个 API 账号或 MCP 软件。")
            return
        self.bulk_route = route
        count = 0
        for name in ROLE_NAMES:
            if self.enabled[name].get():
                self.bindings[name] = route
                self.boxes[name].set(self.options[route])
                count += 1
        self.note.set(f"已为 {count} 位出勤员工指定工作连接。")

    def _choose(self, name):
        route = self._current_route(self.boxes[name])
        if route is not None and not self.running and self.enabled[name].get():
            self.bindings[name] = route
            self.show_skill(name)
            self.note.set("")

    def _toggle(self, name):
        self.show_skill(name)
        self.set_locked(self.running, self.saved_job)

    def _open_software(self):
        app = self.desk.software
        route = self.bindings.get(self.active_role)
        if route and route[0] == "mcp":
            app.app_name.set(route[1])
            if route[1] == "DeepSeek Harness":
                app.harness_mode.set("mcp")
            elif route[1] == "Codex":
                app.codex_mode.set("mcp")
        self.desk.tabs.select(app)

    def show_skill(self, role):
        self.active_role = role
        info = role_info(role)
        self.skill_label.configure(text=f"{role} · 岗位手册")
        self.handbook.configure(state="normal")
        self.handbook.delete("1.0", "end")
        self.handbook.insert("end", "、".join(info["skills"]) + "\n", "skills")
        self.handbook.insert("end", info["title"] + "\n\n" + info["instructions"])
        for idx, line in enumerate(self.handbook.get("1.0", "end-1c").splitlines(), 1):
            if line.startswith("# "):
                self.handbook.tag_add("markup", f"{idx}.0", f"{idx + 1}.0")
            elif line.startswith("## "):
                self.handbook.tag_add("markup", f"{idx}.0", f"{idx}.3")
                self.handbook.tag_add("heading", f"{idx}.3", f"{idx}.end")
        if info["sources"]:
            self.handbook.insert("end", "\n\n技能来源\n" + "\n".join(info["sources"]), "sources")
        self.handbook.configure(state="disabled")
        self.handbook.yview_moveto(0)

    def selection(self):
        active = [name for name in ROLE_NAMES if self.enabled[name].get()]
        if LEAD not in active or len(active) < 2:
            raise ValueError("请保留项目经理，再勾选至少一位执行员工。")
        result = {}
        for role in active:
            route = self.bindings.get(role)
            if route not in self.options:
                raise ValueError(f"请为{role}选择有效的 API 账号或 MCP 软件。")
            result[role] = {"kind": route[0], "target": route[1]}
        return result

    def restore(self, routes):
        self.bindings.clear()
        self.bulk_route = None
        for role in ROLE_NAMES:
            self.enabled[role].set(role == LEAD or role in routes)
            route = routes.get(role)
            if isinstance(route, dict) and route.get("kind") in {"api", "mcp"} and isinstance(route.get("target"), str):
                self.bindings[role] = (route["kind"], route["target"])
        self.refresh()
        self.set_locked(self.running, self.saved_job)

    def set_locked(self, running, saved_job):
        self.running, self.saved_job = bool(running), bool(saved_job)
        for role in ROLE_NAMES:
            locked = self.running or self.saved_job or role == LEAD
            self.checks[role].configure(state="disabled" if locked else "normal")
            editable = not self.running and self.enabled[role].get()
            self.boxes[role].configure(state="readonly" if editable else "disabled")
        self.bulk_box.configure(state="disabled" if self.running else "readonly")
        self.bulk_button.configure(state="disabled" if self.running else "normal")
        self.start_button.configure(state="disabled" if self.running else "normal")
