"""Tk interface for the bundled agent and named API accounts."""
import json
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from action_menu import ActionMenu
from cloud_room import validated_files
from cloud_ui import export_project
from desktop_style import ACCENT, FONT, INK, MUTED, SUBTLE, icon, style_text
from providers import ENV_KEYS
from lite_agent import run_task
import bot_memory


def read_attachments(paths, existing):
    """Read only explicitly selected text files into a bounded, flat snapshot."""
    combined = dict(existing)
    for path in map(Path, paths):
        name = path.name
        validated_files({name: ""})
        with path.open("rb") as stream:
            raw = stream.read(96_001)
        if len(raw) > 96_000:
            raise ValueError("单个参考文件最多 96,000 字节")
        value = raw.decode("utf-8-sig")
        if name.lower().endswith(".json"):
            try:
                data = json.loads(value)
            except RecursionError:
                raise ValueError("JSON 嵌套层数过多。") from None
            pending = [data]
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    keys = {k.lower().replace("_", "").replace("-", "") for k in node}
                    if keys.intersection({"apikey", "keyenv", "accesstoken", "refreshtoken", "idtoken"}) or (
                            "type" in node and node["type"] == "api_key" and "key" in node):
                        raise ValueError("该文件包含账号凭据字段。API 账号请用「导入账号 JSON」。")
                    if node.get("version") == 1 and "accounts" in node:
                        raise ValueError("该文件是账号配置，请用「导入账号 JSON」。")
                    pending.extend(node.values())
                elif isinstance(node, list):
                    pending.extend(node)
        if name in combined and combined[name] != value:
            raise ValueError(f"已附加同名文件 {name}，请先清除参考文件或更改文件名。")
        combined[name] = value
        validated_files(combined)
    return combined


class ProfileEditor(tk.Toplevel):
    def __init__(self, panel, profile=None):
        super().__init__(panel)
        self.panel = panel
        self.profile = profile or {}
        self.title("编辑 API 账号" if profile else "新建 API 账号")
        self.transient(panel.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=(22, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text=self.title(), style="Title.TLabel").grid(row=0, columnspan=2, sticky="w", pady=(0, 14))
        self.fields = {}
        for row, (field, title) in enumerate((("name", "账号名称"), ("provider", "模型服务"),
                ("model", "模型 ID"), ("api_key", "API Key"), ("key_env", "环境变量名")), start=1):
            ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            var = self.fields[field] = tk.StringVar(self, value=self.profile.get(field) or ("GPT" if field == "provider" else ""))
            if field == "provider":
                box = ttk.Combobox(body, values=tuple(ENV_KEYS), state="readonly", textvariable=var, width=29)
            else:
                box = ttk.Entry(body, width=32, textvariable=var, show="•" if field == "api_key" else "")
            box.grid(row=row, column=1, sticky="ew")
            if field == "name":
                box.focus_set()
        hint = "API Key 和环境变量名任选一项。"
        if profile:
            hint += "保留原密钥时让密钥框留空。"
        ttk.Label(body, text=hint, style="Hint.TLabel", wraplength=360).grid(row=6, columnspan=2, sticky="w", pady=(8, 16))
        row = ttk.Frame(body)
        row.grid(row=7, columnspan=2, sticky="e")
        ttk.Button(row, text="取消", command=self.destroy).pack(side="left", padx=3)
        ttk.Button(row, text="保存", style="Accent.TButton", command=self.save).pack(side="left", padx=3)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def save(self):
        fields = {key: var.get().strip() for key, var in self.fields.items()}
        changed_provider = bool(self.profile and self.profile["provider"] != fields["provider"])
        try:
            if changed_provider and any(bot["profile_id"] == self.profile["id"]
                                        for bot in self.panel.desk.account_store.list_bots()):
                if not messagebox.askyesno("更换模型服务", "更换服务商将清除使用此账号的 Bot 已保存的私聊和长期记忆。继续更换？",
                                          parent=self):
                    return
            profile_id = self.panel.desk.account_store.save_profile(**fields, profile_id=self.profile.get("id"))
        except Exception as exc:
            messagebox.showerror("保存账号", str(exc), parent=self)
            return
        self.fields["api_key"].set("")
        self.panel.refresh_profiles(profile_id)
        if changed_provider:
            self.panel.desk.refresh_bot_previews()
        self.destroy()


class LitePanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=(16, 12))
        self.desk = desk
        self.profiles = []
        self.selected_id = None
        self.active_bot = None
        self.account_id = None
        self.sessions = {}
        self.events = queue.Queue()
        self.stop = None
        self.turn = 0
        self.closed = False
        self.actions = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.title_text = tk.StringVar(self, "内置 Agent")
        head = ttk.Frame(self)
        head.grid(row=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, textvariable=self.title_text, style="Title.TLabel", width=1).grid(
            row=0, column=0, sticky="ew")
        self.chat_menu = ActionMenu(head, text="对话菜单")
        self.chat_menu.grid(row=0, column=1)
        self.actions["export"] = self.chat_menu.add_action("导出草稿…", self.export)
        self.actions["detach"] = self.chat_menu.add_action("清除参考文件", self.detach)
        self.chat_menu.add_separator()
        self.actions["new"] = self.chat_menu.add_action("清空对话…", self.clear)
        profiles = self.profile_row = ttk.Frame(self)
        profiles.grid(row=1, sticky="ew")
        ttk.Label(profiles, text="API 账号").pack(side="left", padx=(0, 8))
        self.choices = ttk.Combobox(profiles, state="readonly", width=12)
        self.choices.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.choices.bind("<<ComboboxSelected>>", self._select)
        self.account_menu = ActionMenu(profiles, text="账号管理")
        self.account_menu.pack(side="right")
        self.actions["add"] = self.account_menu.add_action("新建 API 账号…", lambda: ProfileEditor(self))
        self.actions["import"] = self.account_menu.add_action("导入账号 JSON…", self.import_profiles)
        self.actions["edit"] = self.account_menu.add_action("编辑当前账号…", lambda: ProfileEditor(
            self, self.profiles[self.choices.current()]))
        self.account_menu.add_separator()
        self.actions["team_agent"] = self.account_menu.add_action("用于团队内置 Agent", lambda: self.apply_profile(True))
        self.actions["team_provider"] = self.account_menu.add_action("用于同名模型成员", lambda: self.apply_profile(False))
        self.account_menu.add_separator()
        self.actions["delete"] = self.account_menu.add_action("删除当前账号…", self.remove_profile)
        self.bot_row = ttk.Frame(self)
        self.bot_row.columnconfigure(0, weight=1)
        self.bot_info = tk.StringVar(self)
        info = ttk.Label(self.bot_row, textvariable=self.bot_info, style="Hint.TLabel", width=1)
        info.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.bot_menu = ActionMenu(self.bot_row, text="Bot 管理")
        self.bot_menu.grid(row=0, column=1, sticky="e")
        self.edit_bot = self.bot_menu.add_action("Bot 设置…", lambda: self.desk.bots.edit(self.active_bot))
        self.memory_button = self.bot_menu.add_action("长期记忆…", self.edit_memory)
        self.bind_bot = self.bot_menu.add_action("用于团队内置 Agent", lambda: self.apply_profile(True))
        chat = ttk.Frame(self)
        chat.grid(row=2, sticky="nsew", pady=(9, 0))
        self.transcript = ScrolledText(chat, height=3, width=20, wrap="word", state="disabled")
        style_text(self.transcript)
        self.transcript.pack(fill="both", expand=True)
        self.transcript.tag_configure("sender", foreground=ACCENT, font=(FONT, 11, "bold"), spacing1=12, spacing3=3)
        self.transcript.tag_configure("reply", foreground=INK, spacing3=10, lmargin1=6, lmargin2=6)
        self.transcript.tag_configure("user_name", foreground=MUTED, font=(FONT, 9, "bold"),
                                      justify="right", spacing1=9, spacing3=3, rmargin=12)
        self.transcript.tag_configure("user_message", background=SUBTLE, foreground=INK, justify="right",
                                      lmargin1=38, lmargin2=38, rmargin=12, spacing1=6, spacing3=6)
        self.empty_icon = icon(chat, "lite", ACCENT, 32)
        self.empty = tk.Label(chat, image=self.empty_icon, compound="top", pady=8, wraplength=320,
                              bg=self.transcript.cget("background"), fg=MUTED, justify="center", font=(FONT, 10))
        self.hint = tk.StringVar(self)
        note = ttk.Label(self, textvariable=self.hint, style="Hint.TLabel")
        note.grid(row=3, sticky="w", pady=(4, 2))
        self.bind("<Configure>", lambda event: note.configure(wraplength=max(160, event.width-40)))
        compose = ttk.Frame(self)
        compose.grid(row=4, sticky="ew", pady=(2, 5))
        ttk.Label(compose, text="任务或问题", style="Section.TLabel").pack(side="left")
        self.summary = tk.StringVar(self)
        ttk.Label(compose, textvariable=self.summary, style="Hint.TLabel").pack(side="right")
        self.prompt = ScrolledText(self, height=2, width=20, wrap="word")
        style_text(self.prompt, editor=True)
        self.prompt.grid(row=5, sticky="ew")
        row = ttk.Frame(self)
        row.grid(row=6, sticky="ew", pady=(6, 0))
        self._button(row, "attach", "添加参考文件…", self.attach)
        self._button(row, "send", "发送", self.submit, side="right", style="Accent.TButton")
        self._button(row, "stop", "停止", self.cancel, side="right", style="Danger.TButton")
        self._render()
        self.bind("<Map>", self._on_map)

    def _on_map(self, event):
        if event.widget is self:
            self.refresh_profiles()

    def open_bot(self, bot):
        """Select an individual bot, keeping its profile locked to the saved binding."""
        if self.stop is not None:
            if self.active_bot == bot:
                return True
            self.hint.set("请等待当前回复或点「停止」后切换 Bot。")
            return False
        if not self.active_bot:
            self.account_id = self.selected_id
        self._save_input()
        self.active_bot = dict(bot)
        self.selected_id = None
        self.title_text.set(bot["name"])
        self.profile_row.grid_remove()
        self.bot_row.grid(row=1, sticky="ew")
        if not self.refresh_profiles():
            self.prompt.delete("1.0", "end")
            return True
        self._restore_input()
        return True

    def open_accounts(self):
        if self.active_bot is None:
            return True
        if self.stop is not None:
            self.hint.set("请等待当前回复或点「停止」后切换账号。")
            return False
        self._save_input()
        self.active_bot = None
        self.title_text.set("内置 Agent")
        self.bot_row.grid_remove()
        self.profile_row.grid()
        self.refresh_profiles(self.account_id)
        self._restore_input()
        return True

    def forget_bot(self, bot_id):
        if self.active_bot and self.active_bot["id"] == bot_id:
            if not self.open_accounts():
                return False
        self.sessions.pop(("bot", bot_id), None)
        return True

    def _save_input(self):
        if self.selected_id:
            self._session()["input"] = self.prompt.get("1.0", "end-1c")

    def _restore_input(self):
        self.prompt.delete("1.0", "end")
        if self.selected_id:
            self.prompt.insert("1.0", self._session().get("input", ""))
        self.hint.set("")

    def _button(self, parent, key, title, command, *, side="left", style="TButton"):
        btn = self.actions[key] = ttk.Button(parent, text=title, command=command, style=style, width=0)
        btn.pack(side=side, padx=(0, 4))

    def refresh_profiles(self, preferred=None):
        if self.stop is not None:
            return False
        try:
            profiles = self.desk.account_store.list_profiles()
        except Exception as exc:
            self.hint.set("读取账号失败：" + str(exc))
            if self.active_bot:
                self.selected_id = None
                self.sessions.pop(self._session_key(), None)
                self.bot_info.set("绑定的 API 账号暂时无法读取。")
                self._render()
            return False
        old = self.selected_id
        ids = [p["id"] for p in profiles]
        for previous in self.profiles:
            updated = next((p for p in profiles if p["id"] == previous["id"]), None)
            if updated is None or previous["provider"] != updated["provider"]:
                self.sessions.pop(previous["id"], None)
        self.profiles = profiles
        self.desk.refresh_bound_profiles(profiles)
        self.choices.configure(values=[f"{p['name']} · {p['provider']} / {p['model']}" for p in profiles])
        if self.active_bot:
            wanted = self.active_bot["profile_id"]
            record = next((p for p in profiles if p["id"] == wanted), None)
            self.selected_id = wanted if record else None
            if record:
                try:
                    self._load_chat(record["provider"])
                except (OSError, ValueError, sqlite3.Error) as exc:
                    self.sessions.pop(self._session_key(), None)
                    self.selected_id = None
                    self.hint.set("读取 Bot 对话失败：" + str(exc))
                    self.bot_info.set("请重新打开 Bot。")
                    self._render()
                    return False
            else:
                self.sessions.pop(self._session_key(), None)
                self.prompt.delete("1.0", "end")
            self.bot_info.set(f"{record['provider']} / {record['model']}   ·   {record['name']}" if record
                              else "绑定的 API 账号已不存在，请编辑 Bot 选择账号。")
            self._render()
            return True
        wanted = preferred if preferred in ids else old
        if profiles:
            self.choices.current(ids.index(wanted) if wanted in ids else 0)
        else:
            self.choices.set("")
        self._select()
        return True

    def _select(self, _event=None):
        if self.active_bot:
            return
        index = self.choices.current()
        chosen = self.profiles[index]["id"] if 0 <= index < len(self.profiles) else None
        if chosen != self.selected_id:
            self.prompt.delete("1.0", "end")
            self.hint.set("")
        self.selected_id = chosen
        self._render()

    def _session(self):
        return self.sessions.setdefault(self._session_key(), {"history": [], "files": {}, "drafts": {}})

    def _session_key(self):
        return ("bot", self.active_bot["id"]) if self.active_bot else self.selected_id

    def _load_chat(self, provider):
        binding = (self.selected_id, provider)
        chat_id, memory, history = bot_memory.load(self.desk.account_store, self.active_bot["id"], *binding)
        session = self._session()
        changed = session.get("chat_id", chat_id) != chat_id or session.get("binding", binding) != binding
        if changed:
            self.sessions.pop(self._session_key())
            session = self._session()
            self.prompt.delete("1.0", "end")
        session.update(binding=binding, chat_id=chat_id, memory=memory, history=history)
        return changed

    def _chat_args(self):
        session = self._session()
        return self.desk.account_store, self.active_bot["id"], *session["binding"], session["chat_id"]

    def edit_memory(self):
        if not self.active_bot or self.stop is not None or not self.refresh_profiles() or not self.selected_id:
            return
        editor = tk.Toplevel(self)
        editor.title(self.active_bot["name"] + " · 长期记忆")
        editor.transient(self.winfo_toplevel())
        body = ttk.Frame(editor, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="长期记忆", style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="写下希望这个 Bot 在私聊时记住的偏好或项目约定。保存空白即可删除。",
                  style="Hint.TLabel", wraplength=390).pack(anchor="w", pady=(4, 10))
        note = ScrolledText(body, width=42, height=8, wrap="word")
        style_text(note, editor=True)
        note.pack(fill="both", expand=True)
        previous = self._session()["memory"]
        args = self._chat_args()
        note.insert("1.0", previous)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        def save():
            try:
                value = bot_memory.set_memory(*args, previous, note.get("1.0", "end-1c"))
            except (ValueError, OSError, sqlite3.Error) as exc:
                messagebox.showerror("保存记忆", str(exc), parent=editor)
                return
            if self.active_bot and self.active_bot["id"] == args[1]:
                self._session()["memory"] = value
            self.hint.set("已保存长期记忆。" if value else "已删除长期记忆。")
            editor.destroy()
        ttk.Button(actions, text="保存", command=save, style="Accent.TButton").pack(side="right")
        ttk.Button(actions, text="取消", command=editor.destroy).pack(side="right", padx=6)
        editor.bind("<Escape>", lambda _event: editor.destroy())
        editor.grab_set()
        note.focus_set()

    def current_config(self):
        if not self.selected_id:
            self.refresh_profiles()
        if not self.selected_id:
            raise ValueError("请在「内置 Agent」的「账号管理」中新建或导入一个 API 账号。")
        if self.active_bot and self.selected_id != self.active_bot["profile_id"]:
            raise ValueError("Bot 的账号绑定已改变，请重新打开这个 Bot。")
        config = self.desk.account_store.resolve(self.selected_id)
        if self.active_bot:
            config = dict(config, instructions=self.active_bot["instructions"])
        return config

    def _render(self):
        session = self.sessions.get(self._session_key()) or {}
        active = self.stop is not None
        has = self.selected_id is not None
        history = session.get("history", [])
        drafts = session.get("drafts", {})
        files = session.get("files", {})
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        messages = list(history)
        if session.get("pending"):
            messages.append({"role": "user", "content": session["pending"]})
        for item in messages:
            sender = "你" if item["role"] == "user" else self.title_text.get()
            author_tag, content_tag = ("user_name", "user_message") if item["role"] == "user" else ("sender", "reply")
            self.transcript.insert("end", sender + "\n", author_tag)
            self.transcript.insert("end", item["content"] + "\n", content_tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")
        if history or session.get("pending"):
            self.empty.place_forget()
        else:
            if has:
                text = "输入任务，或添加一份参考文件开始对话。"
            elif self.active_bot:
                text = "打开「Bot 管理」里的「Bot 设置」，检查绑定的 API 账号。"
            else:
                text = "打开「账号管理」，新建或导入第一个 API 账号。"
            self.empty.configure(text=text)
            self.empty.place(relx=.5, rely=.5, anchor="center")
        self.summary.set("　".join(f"{len(entries)} 个{label}" for entries, label in
                                  ((files, "参考文件"), (drafts, "草稿")) if entries))
        self.choices.configure(state="disabled" if active or self.active_bot else "readonly")
        self.account_menu.configure(state="disabled" if active else "normal")
        self.bot_menu.configure(state="disabled" if active else "normal")
        self.chat_menu.configure(state="normal" if has and not active else "disabled")
        self.bind_bot.configure(state="normal" if has and not active else "disabled")
        self.edit_bot.configure(state="disabled" if active else "normal")
        self.memory_button.configure(state="normal" if has and not active else "disabled")
        self.prompt.configure(state="disabled" if active else "normal")
        for name, btn in self.actions.items():
            available = (active if name == "stop" else not active and
                         (has or name in {"add", "import"}))
            if name == "export":
                available = available and bool(drafts)
            if name == "detach":
                available = available and bool(files)
            btn.configure(state="normal" if available else "disabled")
        self.actions["send" if active else "stop"].pack_forget()
        self.actions["stop" if active else "send"].pack(side="right")

    def import_profiles(self):
        file = filedialog.askopenfilename(parent=self, title="导入 API 账号", filetypes=[("账号 JSON", "*.json")])
        if file:
            try:
                ids = self.desk.account_store.import_file(file)
                self.refresh_profiles(ids[0] if ids else None)
                self.hint.set(f"已导入 {len(ids)} 个 API 账号。")
            except Exception as exc:
                messagebox.showerror("导入账号", str(exc), parent=self)

    def remove_profile(self):
        profile = self.profiles[self.choices.current()]
        if messagebox.askyesno("删除账号", f"从此工作台删除「{profile['name']}」及其本地对话草稿？", parent=self):
            try:
                self.desk.account_store.delete_profile(profile["id"])
                self.sessions.pop(profile["id"], None)
                self.refresh_profiles()
            except Exception as exc:
                messagebox.showerror("删除账号", str(exc), parent=self)

    def apply_profile(self, builtin):
        try:
            if builtin and self.active_bot:
                self.desk.apply_profile(self.selected_id, builtin=True, bot_id=self.active_bot["id"])
            else:
                self.desk.apply_profile(self.selected_id, builtin=builtin)
            self.hint.set("已将此账号应用到团队的" + ("内置 Agent。" if builtin else "对应模型成员。"))
        except Exception as exc:
            messagebox.showerror("应用账号", str(exc), parent=self)

    def attach(self):
        files = filedialog.askopenfilenames(parent=self, title="选择要提交给当前模型的项目文本",
                                           filetypes=[("文本与 JSON", "*.txt *.md *.json *.py *.csv"), ("所有文件", "*.*")])
        if files:
            try:
                session = self._session()
                session["files"] = read_attachments(files, session["files"])
                self.hint.set("已附加：" + "、".join(Path(p).name for p in files)[:110])
                self._render()
            except (ValueError, OSError) as exc:
                messagebox.showerror("添加参考文件", str(exc), parent=self)

    def detach(self):
        self._session()["files"].clear()
        self.hint.set("已移除本轮参考文件。")
        self._render()

    def clear(self):
        if not messagebox.askyesno("清空对话", "清空当前对话、参考文件和未导出的草稿？", parent=self):
            return
        if self.active_bot:
            try:
                session = self._session()
                chat_id = bot_memory.clear_history(*self._chat_args())
                session.update(history=[], files={}, drafts={}, input="", chat_id=chat_id)
                self.desk.refresh_bot_previews()
            except (OSError, sqlite3.Error, ValueError) as exc:
                messagebox.showerror("清空对话", str(exc), parent=self)
                return
        else:
            self.sessions.pop(self._session_key(), None)
        self.prompt.delete("1.0", "end")
        self._render()

    def export(self):
        parent = filedialog.askdirectory(parent=self, title="选择草稿导出位置，将创建新的子目录", mustexist=True)
        if parent:
            try:
                output = export_project(parent, self._session()["drafts"])
                self.hint.set("草稿已保存至 " + str(output))
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出草稿", str(exc), parent=self)

    def submit(self):
        request = self.prompt.get("1.0", "end-1c").strip()
        if not request or self.stop is not None:
            return
        try:
            config = self.current_config()
            if self.active_bot:
                if self._load_chat(config["provider"]):
                    self._render()
                    raise ValueError("Bot 的对话已在别处清空或重新绑定。请在当前对话重新填写任务。")
                config["bot_memory"] = self._session()["memory"]
            session = self._session()
            files = validated_files({**session["files"], **session["drafts"]})
        except Exception as exc:
            messagebox.showerror("发送", str(exc), parent=self)
            return
        stop = self.stop = threading.Event()
        self.turn += 1
        turn = self.turn
        history = list(session["history"])
        session["pending"] = request
        self.hint.set(f"{config['provider']} · {config['model']} 正在处理…")
        self._render()

        def report(message):
            self.events.put((turn, "progress", message))

        def work():
            try:
                result = run_task(config["provider"], config, request, files=files, history=history, stop=stop, report=report)
                self.events.put((turn, "result", result))
            except Exception as exc:
                self.events.put((turn, "error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.stop is None:
            return
        self.stop.set()
        self.turn += 1
        self.stop = None
        self._session().pop("pending", None)
        self.hint.set("已停止。可编辑任务后重试。")
        self._render()

    def poll(self):
        while not self.closed:
            try:
                turn, kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if turn != self.turn or self.stop is None:
                continue
            if kind == "progress":
                self.hint.set(value)
                continue
            self.stop = None
            session = self._session()
            request = session.pop("pending")
            if kind == "result":
                try:
                    drafts = validated_files({**session["drafts"], **value["files"]})
                except ValueError as exc:
                    self.hint.set("保存草稿失败：" + str(exc))
                    self._render()
                    continue
                if self.active_bot:
                    try:
                        bot_memory.append_turn(*self._chat_args(), request, value["message"])
                    except ValueError as exc:
                        # Another window cleared this private chat or changed its account.
                        self.sessions.pop(self._session_key(), None)
                        self.prompt.configure(state="normal")
                        self.prompt.delete("1.0", "end")
                        self.refresh_profiles()
                        self.desk.refresh_bot_previews()
                        self.hint.set(str(exc))
                        continue
                    except (OSError, sqlite3.Error) as exc:
                        self.hint.set("本次回复未保存到历史记录：" + str(exc))
                        self._render()
                        continue
                session["history"].extend([{"role": "user", "content": request},
                                           {"role": "assistant", "content": value["message"]}])
                session["drafts"] = drafts
                self.prompt.configure(state="normal")
                self.prompt.delete("1.0", "end")
                self.hint.set("已完成。" + (" 可导出：" + "、".join(drafts)[:100] if drafts else ""))
                if self.active_bot:
                    session["history"] = session["history"][-bot_memory.RECENT_TURNS * 2:]
                    self.desk.refresh_bot_previews()
            else:
                self.hint.set("请求失败：" + value)
            self._render()

    def close(self):
        self.closed = True
        if self.stop is not None:
            self.stop.set()
