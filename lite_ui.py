"""Tk interface for the bundled agent and named API accounts."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from cloud_room import validated_files
from cloud_ui import export_project
from providers import ENV_KEYS
from lite_agent import run_task


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
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        self.fields = {}
        for row, (field, title) in enumerate((("name", "账号名称"), ("provider", "模型服务"),
                ("model", "模型 ID"), ("api_key", "API Key"), ("key_env", "或使用环境变量"))):
            ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            var = self.fields[field] = tk.StringVar(self, value=self.profile.get(field) or ("GPT" if field == "provider" else ""))
            if field == "provider":
                box = ttk.Combobox(body, values=tuple(ENV_KEYS), state="readonly", textvariable=var, width=29)
            else:
                box = ttk.Entry(body, width=32, textvariable=var, show="•" if field == "api_key" else "")
            box.grid(row=row, column=1, sticky="ew")
            if field == "name":
                box.focus_set()
        hint = "填入密钥或保存环境变量名称。"
        if profile:
            hint += "保留原密钥时让密钥框留空。"
        ttk.Label(body, text=hint, wraplength=360).grid(row=5, columnspan=2, sticky="w", pady=6)
        row = ttk.Frame(body)
        row.grid(row=6, columnspan=2, sticky="e")
        ttk.Button(row, text="取消", command=self.destroy).pack(side="left", padx=3)
        ttk.Button(row, text="保存", command=self.save).pack(side="left", padx=3)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def save(self):
        fields = {key: var.get().strip() for key, var in self.fields.items()}
        try:
            profile_id = self.panel.desk.account_store.save_profile(**fields, profile_id=self.profile.get("id"))
        except Exception as exc:
            messagebox.showerror("保存账号", str(exc), parent=self)
            return
        self.fields["api_key"].set("")
        self.panel.refresh_profiles(profile_id)
        self.destroy()


class LitePanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=12)
        self.desk = desk
        self.profiles = []
        self.selected_id = None
        self.sessions = {}
        self.events = queue.Queue()
        self.stop = None
        self.turn = 0
        self.closed = False
        self.actions = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        ttk.Label(self, text="内置 Agent", style="Title.TLabel").grid(row=0, sticky="w")
        profiles = ttk.Frame(self)
        profiles.grid(row=1, sticky="ew", pady=(5, 0))
        ttk.Label(profiles, text="API 账号").pack(side="left", padx=(0, 5))
        self.choices = ttk.Combobox(profiles, state="readonly", width=30)
        self.choices.pack(side="left", padx=(0, 6))
        self.choices.bind("<<ComboboxSelected>>", self._select)
        self._button(profiles, "add", "新建", lambda: ProfileEditor(self))
        self._button(profiles, "edit", "编辑", lambda: ProfileEditor(self, self.profiles[self.choices.current()]))
        self._button(profiles, "delete", "删除", self.remove_profile)
        self._button(profiles, "import", "导入账号 JSON", self.import_profiles)
        team = ttk.Frame(self)
        team.grid(row=2, sticky="ew", pady=4)
        self._button(team, "team_agent", "用于团队内置 Agent", lambda: self.apply_profile(True))
        self._button(team, "team_provider", "用于同名模型成员", lambda: self.apply_profile(False))
        self.hint = tk.StringVar(self, value="新建或导入一个 API 账号即可开始。")
        ttk.Label(self, textvariable=self.hint, wraplength=710).grid(row=3, sticky="w", pady=(2, 4))
        self.transcript = ScrolledText(self, height=6, width=45, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.transcript.grid(row=4, sticky="nsew")
        tools = ttk.Frame(self)
        tools.grid(row=5, sticky="ew", pady=4)
        self._button(tools, "attach", "添加参考文件…", self.attach)
        self._button(tools, "detach", "清除参考文件", self.detach)
        self._button(tools, "export", "导出草稿…", self.export)
        self._button(tools, "new", "清空对话", self.clear)
        ttk.Label(self, text="任务或问题").grid(row=6, sticky="w")
        self.prompt = ScrolledText(self, height=3, width=45, wrap="word", relief="solid", borderwidth=1)
        self.prompt.grid(row=7, sticky="ew")
        row = ttk.Frame(self)
        row.grid(row=8, sticky="ew", pady=(4, 0))
        self._button(row, "send", "发送", self.submit)
        self._button(row, "stop", "停止", self.cancel)
        self.summary = tk.StringVar(self)
        ttk.Label(row, textvariable=self.summary).pack(side="left", padx=6)
        self._render()
        self.bind("<Map>", self._on_map)

    def _on_map(self, event):
        if event.widget is self:
            self.refresh_profiles()

    def _button(self, parent, key, title, command):
        btn = self.actions[key] = ttk.Button(parent, text=title, command=command)
        btn.pack(side="left", padx=(0, 4))

    def refresh_profiles(self, preferred=None):
        if self.stop is not None:
            return
        try:
            profiles = self.desk.account_store.list_profiles()
        except Exception as exc:
            self.hint.set("读取账号失败：" + str(exc))
            return
        old = self.selected_id
        ids = [p["id"] for p in profiles]
        for previous in self.profiles:
            updated = next((p for p in profiles if p["id"] == previous["id"]), None)
            if updated is None or previous["provider"] != updated["provider"]:
                self.sessions.pop(previous["id"], None)
        self.profiles = profiles
        self.desk.refresh_bound_profiles(profiles)
        self.choices.configure(values=[f"{p['name']} · {p['provider']} / {p['model']}" for p in profiles])
        wanted = preferred if preferred in ids else old
        if profiles:
            self.choices.current(ids.index(wanted) if wanted in ids else 0)
        else:
            self.choices.set("")
        self._select()

    def _select(self, _event=None):
        index = self.choices.current()
        chosen = self.profiles[index]["id"] if 0 <= index < len(self.profiles) else None
        if chosen != self.selected_id:
            self.prompt.delete("1.0", "end")
        self.selected_id = chosen
        self.hint.set("可直接提问，也可添加要给当前模型查看的项目文本或 JSON。账号文件请用「导入账号 JSON」。"
                      if chosen else "新建或导入一个 API 账号即可开始。")
        self._render()

    def _session(self):
        return self.sessions.setdefault(self.selected_id, {"history": [], "files": {}, "drafts": {}})

    def current_config(self):
        if not self.selected_id:
            self.refresh_profiles()
        if not self.selected_id:
            raise ValueError("请在「内置 Agent」新建或导入一个 API 账号。")
        return self.desk.account_store.resolve(self.selected_id)

    def _render(self):
        session = self.sessions.get(self.selected_id) or {}
        active = self.stop is not None
        has = self.selected_id is not None
        history = session.get("history", [])
        drafts = session.get("drafts", {})
        files = session.get("files", {})
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        for item in history:
            self.transcript.insert("end", ("你" if item["role"] == "user" else "Agent") + "\n" + item["content"] + "\n\n")
        if session.get("pending"):
            self.transcript.insert("end", "你\n" + session["pending"] + "\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")
        self.summary.set(f"参考文件 {len(files)} 个　草稿 {len(drafts)} 个")
        self.choices.configure(state="disabled" if active else "readonly")
        self.prompt.configure(state="disabled" if active else "normal")
        for name, btn in self.actions.items():
            available = (active if name == "stop" else not active and
                         (has or name in {"add", "import"}))
            if name == "export":
                available = available and bool(drafts)
            if name == "detach":
                available = available and bool(files)
            btn.configure(state="normal" if available else "disabled")

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
        if not messagebox.askyesno("清空对话", "清空当前账号的对话、参考文件和未导出的草稿？", parent=self):
            return
        self.sessions.pop(self.selected_id, None)
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
                else:
                    session["history"].extend([{"role": "user", "content": request},
                                               {"role": "assistant", "content": value["message"]}])
                    session["drafts"] = drafts
                    self.prompt.configure(state="normal")
                    self.prompt.delete("1.0", "end")
                    self.hint.set("已完成。" + (" 可导出：" + "、".join(drafts)[:100] if drafts else ""))
            else:
                self.hint.set("请求失败：" + value)
            self._render()

    def close(self):
        self.closed = True
        if self.stop is not None:
            self.stop.set()
