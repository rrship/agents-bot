"""Bot shortcuts and the editor for a named assistant."""
import tkinter as tk
import sqlite3
from tkinter import messagebox, ttk

import desktop_style as ui
from lite_ui import ProfileEditor


TEMPLATES = {
    "研究助手": "先澄清研究问题，从提供的资料提取证据。区分已知事实、推断和待验证的问题，并标注证据来源。",
    "代码审查": "审阅提供的代码和改动。按严重程度说明具体文件、可复现的问题和修复建议。",
    "测试助手": "根据需求整理验收条件，覆盖正常流程、边界和失败情况。给出可执行的测试用例。",
}


class BotRail:
    def __init__(self, parent, desk):
        self.desk = desk
        self.records = []
        self.rows = []
        self.editor = None
        tk.Frame(parent, bg=ui.RAIL_ACTIVE, height=1).pack(fill="x", padx=15, pady=(18, 9))
        bar = tk.Frame(parent, bg=ui.RAIL)
        bar.pack(fill="x", padx=13)
        tk.Label(bar, text="我的 Bot", bg=ui.RAIL, fg=ui.RAIL_SUB, font=(ui.FONT, 9, "bold")).pack(side="left")
        self.new = self._button(bar, "＋ 新建", lambda: self.edit())
        self.new.pack(side="right")
        self.viewport = tk.Canvas(parent, bg=ui.RAIL, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.viewport.pack(fill="both", expand=True, padx=8, pady=(5, 4))
        self.items = tk.Frame(self.viewport, bg=ui.RAIL)
        self.content = self.viewport.create_window(0, 0, window=self.items, anchor="nw")
        self.viewport.bind("<Configure>", lambda event: self.viewport.itemconfigure(self.content, width=event.width))
        self.items.bind("<Configure>", lambda _: self.viewport.configure(scrollregion=self.viewport.bbox("all")))
        self.entry_widgets = {}
        self.refresh()

    @staticmethod
    def _button(parent, label, callback):
        return tk.Button(parent, text=label, command=callback, fg=ui.RAIL_TEXT, bg=ui.RAIL,
                         activeforeground=ui.SURFACE, activebackground=ui.RAIL_ACTIVE,
                         font=(ui.FONT, 9), padx=5, pady=3, relief="flat", borderwidth=0,
                         highlightthickness=1, highlightbackground=ui.RAIL, highlightcolor=ui.RAIL_SUB,
                         anchor="w", cursor="hand2", disabledforeground=ui.RAIL_SUB)

    def refresh(self, selected=None):
        try:
            self.records = self.desk.account_store.list_bots()
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            self.desk.status.set("无法读取 Bot：" + str(exc))
            return
        active = self.desk.lite.active_bot
        selected = selected or (active and active["id"])
        self.rows = self.records or [dict(name=name, instructions=prompt) for name, prompt in TEMPLATES.items()]
        for row in self.items.winfo_children():
            row.destroy()
        self.entry_widgets.clear()
        for row in self.rows:
            saved = bool(row.get("id"))
            tile = tk.Frame(self.items, bg=ui.RAIL)
            tile.pack(fill="x", pady=1)
            label = self._button(tile, row["name"] if saved else "＋ " + row["name"],
                                 (lambda b=row: self.desk.show_bot(b)) if saved
                                 else lambda b=row: self.edit(template=b["name"]))
            if saved:
                settings = self._button(tile, "⋯", lambda b=row: self.edit(b))
                settings.configure(font=("Segoe UI", 12), padx=4)
                settings.pack(side="right", fill="y")
                self.entry_widgets[row["id"]] = (tile, label, settings)
            label.configure(font=(ui.FONT, 10), pady=5, width=1)
            label.pack(side="left", fill="both", expand=True)
            for control in tile.winfo_children():
                control.bind("<MouseWheel>", self._scroll)
                control.bind("<FocusIn>", self._reveal)
        self.viewport.bind("<MouseWheel>", self._scroll)
        self.set_active(selected)

    def _scroll(self, event):
        if self.items.winfo_height() > self.viewport.winfo_height():
            self.viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _reveal(self, event):
        self.viewport.update_idletasks()
        height = self.items.winfo_height()
        if height > self.viewport.winfo_height():
            row = event.widget.master
            top = row.winfo_y()
            bottom = top + row.winfo_height()
            low, high = self.viewport.yview()
            if top < height * low:
                self.viewport.yview_moveto(top / height)
            elif bottom > height * high:
                self.viewport.yview_moveto((bottom-self.viewport.winfo_height()) / height)

    def set_active(self, bot_id):
        for uid, (tile, label, settings) in self.entry_widgets.items():
            bg = ui.RAIL_ACTIVE if uid == bot_id else ui.RAIL
            tile.configure(bg=bg)
            for control in (label, settings):
                control.configure(bg=bg, highlightbackground=bg)

    def edit(self, bot=None, template=None):
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.lift()
            return
        active = self.desk.lite.active_bot
        if self.desk.lite.stop is not None and active and bot and active["id"] == bot.get("id"):
            messagebox.showinfo("Bot 设置", "请等待此 Bot 回复，或先点「停止」。", parent=self.desk.root)
            return
        self.editor = BotEditor(self, bot, template)


class BotEditor(tk.Toplevel):
    def __init__(self, rail, bot=None, template=None):
        super().__init__(rail.desk.root)
        self.rail = rail
        self.desk = rail.desk
        self.bot = bot or {}
        self.title("Bot 设置" if bot else "新建 Bot")
        self.transient(self.desk.root)
        self.resizable(True, True)
        self.minsize(425, 365)
        body = ttk.Frame(self, padding=(20, 16))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=self.title(), style="Title.TLabel").grid(row=0, columnspan=2, sticky="w", pady=(0, 10))
        if not bot:
            self.template = ttk.Combobox(body, values=("自定义", *TEMPLATES), state="readonly", width=12)
            self.template.set(template or "自定义")
            self.template.grid(row=0, column=1, sticky="e")
            self.template.bind("<<ComboboxSelected>>", lambda _: self._use_template())
        ttk.Label(body, text="Bot 名称").grid(row=1, columnspan=2, sticky="w")
        self.name = tk.StringVar(value=self.bot.get("name", template or ""))
        entry = ttk.Entry(body, textvariable=self.name)
        entry.grid(row=2, columnspan=2, sticky="ew", pady=(3, 9))
        ttk.Label(body, text="模型账号").grid(row=3, columnspan=2, sticky="w")
        self.accounts = ttk.Combobox(body, state="readonly", width=22)
        self.accounts.grid(row=4, column=0, sticky="ew", pady=(3, 9))
        ttk.Button(body, text="新建账号…", command=self._new_account).grid(row=4, column=1, padx=(6,0), sticky="e")
        ttk.Label(body, text="职责与指令").grid(row=5, columnspan=2, sticky="w")
        self.instructions = ui.style_text(tk.Text(body, width=44, height=5, wrap="word", undo=True), editor=True)
        self.instructions.grid(row=6, columnspan=2, sticky="nsew", pady=(3, 12))
        body.rowconfigure(6, weight=1)
        self.instructions.insert("1.0", self.bot.get("instructions", TEMPLATES.get(template, "")))
        footer = ttk.Frame(body)
        footer.grid(row=7, columnspan=2, sticky="ew")
        if bot:
            ttk.Button(footer, text="删除 Bot", command=self._delete).pack(side="left")
        ttk.Button(footer, text="保存", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=6)
        self._profiles(self.bot.get("profile_id"))
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        entry.focus_set()

    def _use_template(self):
        name = self.template.get()
        self.name.set(name if name in TEMPLATES else "")
        self.instructions.delete("1.0", "end")
        self.instructions.insert("1.0", TEMPLATES.get(name, ""))

    def _profiles(self, preferred):
        self.profiles = self.desk.account_store.list_profiles()
        self.accounts.configure(values=[f"{p['name']} · {p['provider']} / {p['model']}" for p in self.profiles])
        if self.profiles:
            self.accounts.current(next((i for i,p in enumerate(self.profiles) if p["id"] == preferred), 0))
        else:
            self.accounts.set("请先新建模型账号")

    def _new_account(self):
        old_ids = {p["id"] for p in self.profiles}
        old = self.accounts.current()
        editor = ProfileEditor(self.desk.lite)
        editor.transient(self)
        self.wait_window(editor)
        if self.winfo_exists():
            self.grab_set()
            new = next((p["id"] for p in self.desk.account_store.list_profiles() if p["id"] not in old_ids), None)
            self._profiles(new or (self.profiles[old]["id"] if old >= 0 else None))

    def _save(self):
        try:
            index = self.accounts.current()
            if index < 0:
                raise ValueError("请新建或选择一个模型账号。")
            uid = self.desk.account_store.save_bot(self.name.get(), self.instructions.get("1.0", "end-1c"),
                                                   self.profiles[index]["id"], bot_id=self.bot.get("id"))
        except Exception as exc:
            messagebox.showerror("保存 Bot", str(exc), parent=self)
            return
        self.rail.refresh(uid)
        self.desk.refresh_bound_profiles(self.desk.account_store.list_profiles())
        self.destroy()
        self.desk.show_bot(next(row for row in self.rail.records if row["id"] == uid))

    def _delete(self):
        if not messagebox.askyesno("删除 Bot", f"删除「{self.bot['name']}」和它在本窗口的对话？", parent=self):
            return
        try:
            self.desk.account_store.delete_bot(self.bot["id"])
        except Exception as exc:
            messagebox.showerror("删除 Bot", str(exc), parent=self)
            return
        self.desk.bot_removed(self.bot["id"])
        self.rail.refresh()
        self.destroy()
