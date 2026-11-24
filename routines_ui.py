"""Desktop scheduler and run history for user-created Bots."""
import queue
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import desktop_style as ui
from routines import ACTIVE, FREQUENCIES, RoutineStore


_ERRORS = (OSError, sqlite3.Error, ValueError)


class RoutineEditor(tk.Toplevel):
    def __init__(self, panel, bots, routine=None):
        super().__init__(panel)
        self.panel, self.bots, self.routine = panel, bots, routine
        self.title("编辑例行任务" if routine else "新建例行任务")
        self.transient(panel.winfo_toplevel())
        self.resizable(True, True)
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(4, weight=1)
        row = routine or {}
        self.name = tk.StringVar(self, row.get("name", ""))
        self.clock = tk.StringVar(self, row.get("clock", "09:00"))
        self.enabled = tk.BooleanVar(self, row.get("enabled", True))
        self.bot = ttk.Combobox(form, state="readonly", values=[b["name"] for b in bots])
        self.bot.current(next((i for i, bot in enumerate(bots) if bot["id"] == row.get("bot_id")), 0))
        self.repeat = ttk.Combobox(form, state="readonly", values=list(FREQUENCIES.values()))
        self.repeat.set(FREQUENCIES[row.get("frequency", "daily")])
        name_box = ttk.Entry(form, textvariable=self.name, width=32)
        for i, (label, control) in enumerate((("名称", name_box), ("执行 Bot", self.bot),
                                               ("周期", self.repeat),
                                               ("本机时间", ttk.Entry(form, textvariable=self.clock)))):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=4)
            control.grid(row=i, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="任务指令").grid(row=4, column=0, sticky="nw", pady=8)
        self.prompt = ScrolledText(form, width=36, height=5, wrap="char")
        ui.style_text(self.prompt, editor=True)
        self.prompt.grid(row=4, column=1, sticky="nsew", pady=4)
        self.prompt.insert("1.0", row.get("prompt", ""))
        ttk.Checkbutton(form, text="启用定时运行", variable=self.enabled).grid(row=5, column=1, sticky="w")
        footer = ttk.Frame(form)
        footer.grid(row=6, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(footer, text="保存", style="Accent.TButton", command=self.save).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        name_box.focus_set()

    def save(self):
        try:
            rid = self.panel.store.save(
                self.name.get(), self.bots[self.bot.current()]["id"], self.prompt.get("1.0", "end-1c"),
                list(FREQUENCIES)[self.repeat.current()], self.clock.get(), self.enabled.get(),
                self.routine["id"] if self.routine else None)
        except _ERRORS as exc:
            messagebox.showerror("保存例行任务", str(exc), parent=self)
            return
        self.destroy()
        self.panel.refresh(rid)


class RoutinesPanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=16)
        self.desk = desk
        self.store = RoutineStore(desk.account_store)
        self.results = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.next_tick = 0
        self.rows = {}
        self.run_rows = []
        self.selected = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        head = ttk.Frame(self)
        head.grid(row=0, sticky="ew")
        ttk.Label(head, text="例行任务", style="Title.TLabel").pack(side="left")
        self.new = ttk.Button(head, text="新建任务", style="Accent.TButton", command=self.edit_new)
        self.new.pack(side="right")
        self.hint = tk.StringVar(self, "按本机时间，在工作台打开期间使用 Bot 的 API 账号运行。")
        ttk.Label(self, textvariable=self.hint, style="Hint.TLabel", wraplength=600).grid(
            row=1, sticky="ew", pady=(3, 9))
        style = ttk.Style(self)
        style.configure("Plan.Treeview", rowheight=49, borderwidth=0, font=(ui.FONT, 10))
        style.configure("Runs.Treeview", rowheight=27, borderwidth=0)
        for name in ("Plan.Treeview", "Runs.Treeview"):
            style.map(name, background=[("selected", ui.SUBTLE)], foreground=[("selected", ui.INK)])
        self.body = ttk.Frame(self)
        self.body.grid(row=2, sticky="nsew")
        self.body.columnconfigure(0, weight=2, uniform="body")
        self.body.columnconfigure(1, weight=4, uniform="body")
        self.body.rowconfigure(0, weight=1)
        left = ttk.Frame(self.body, padding=(0, 5, 12, 0))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="我的计划", style="Section.TLabel").grid(row=0, sticky="w", pady=(0, 6))
        self.listing = self._tree(left, 1, (("name", "任务", 124), ("state", "状态", 54)), "Plan.Treeview")
        self.listing.configure(show="")
        self.listing.column("state", width=52, stretch=False, anchor="center")
        self.listing.bind("<<TreeviewSelect>>", self._select)
        self.listing.bind("<Double-1>", lambda _: self.edit_current())
        actions = ttk.Frame(left)
        actions.grid(row=2, sticky="ew", pady=(8, 0))
        for i in range(2):
            actions.columnconfigure(i, weight=1, uniform="actions")
        self.buttons = {}
        for i, (label, fn) in enumerate((("立即运行", self.run_now), ("编辑", self.edit_current),
                                         ("暂停", self.toggle), ("删除", self.delete))):
            button = ttk.Button(actions, text=label, command=fn, state="disabled",
                                style="Accent.TButton" if i == 0 else "TButton")
            button.grid(row=i//2, column=i%2, sticky="ew", padx=(0, 3), pady=2)
            self.buttons[label] = button
        detail = ttk.Frame(self.body, padding=(16, 8), borderwidth=1, relief="solid")
        detail.grid(row=0, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(7, weight=1)
        self.detail_name = ttk.Label(detail, font=(ui.FONT, 14, "bold"), width=1)
        self.detail_name.grid(row=0, sticky="ew", pady=(3, 2))
        self.meta = ttk.Label(detail, style="Hint.TLabel", width=1)
        self.meta.grid(row=1, sticky="ew", pady=(0, 9))
        ttk.Label(detail, text="任务指令", style="Section.TLabel").grid(row=2, sticky="w", pady=(0, 5))
        self.instruction = ScrolledText(detail, wrap="char", height=2, width=14, state="disabled")
        ui.style_text(self.instruction, editor=True)
        self.instruction.grid(row=3, sticky="ew")
        ttk.Label(detail, text="运行记录", style="Section.TLabel").grid(row=4, sticky="w", pady=(14, 6))
        self.history = self._tree(detail, 5, (("time", "开始时间", 145), ("trigger", "触发", 54),
                                              ("status", "结果", 65)), "Runs.Treeview")
        self.history.bind("<<TreeviewSelect>>", self._show_result)
        ttk.Label(detail, text="运行答复", style="Section.TLabel").grid(row=6, sticky="w", pady=(14, 4))
        self.answer = ScrolledText(detail, wrap="char", width=16, height=3)
        ui.style_text(self.answer)
        self.answer.grid(row=7, sticky="nsew")
        self.answer.configure(state="disabled")
        self.empty = ttk.Frame(self, padding=16)
        self.empty.grid(row=2, sticky="nsew")
        middle = ttk.Frame(self.empty)
        middle.place(relx=.5, rely=.42, anchor="center")
        self.empty_icon = ui.icon(self.empty, "routines", ui.MUTED, 36)
        ttk.Label(middle, image=self.empty_icon).pack(pady=(0, 8))
        ttk.Label(middle, text="还没有例行任务", style="Section.TLabel").pack()
        ttk.Label(middle, text="创建计划，让一位 Bot 按时执行你的指令。", style="Hint.TLabel").pack(pady=5)
        ttk.Button(middle, text="新建任务", command=self.edit_new).pack(pady=4)
        self.body.grid_remove()
        self.bind("<Destroy>", self._destroyed, add="+")

    def _tree(self, parent, row, columns, style):
        box = ttk.Frame(parent)
        box.grid(row=row, sticky="nsew")
        widget = ttk.Treeview(box, columns=[name for name, _, _ in columns], show="headings",
                              height=4, selectmode="browse", style=style)
        for name, label, width in columns:
            widget.heading(name, text=label, anchor="w")
            widget.column(name, width=width, minwidth=45)
        bar = ttk.Scrollbar(box, command=widget.yview)
        widget.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        widget.pack(fill="both", expand=True)
        return widget

    def refresh(self, selected=None):
        try:
            rows = {row["id"]: row for row in self.store.all()}
            if rows != self.rows:
                self.listing.delete(*self.listing.get_children())
                for rid, row in rows.items():
                    schedule = FREQUENCIES[row["frequency"]] + " " + row["clock"]
                    self.listing.insert("", "end", iid=rid, values=(
                        row["name"] + "\n" + schedule, "启用" if row["enabled"] else "暂停"))
                self.rows = rows
            if rows:
                self.empty.grid_remove()
                self.body.grid()
            else:
                self.body.grid_remove()
                self.empty.grid()
            rid = selected or self.selected
            rid = rid if rid in rows else next(iter(rows), None)
            if rid and self.listing.selection() != (rid,):
                self.listing.selection_set(rid)
            self._select()
            self.hint.set("按本机时间，在工作台打开期间使用 Bot 的 API 账号运行。")
        except _ERRORS as exc:
            self.hint.set(f"读取例行任务失败。{exc}")

    def _select(self, _event=None):
        selected = self.listing.selection()
        self.selected = selected[0] if selected else None
        row = self.rows.get(self.selected)
        for button in self.buttons.values():
            button.configure(state="normal" if row else "disabled")
        if row:
            self.buttons["暂停"].configure(text="暂停" if row["enabled"] else "启用")
            self.detail_name.configure(text=row["name"])
            self.meta.configure(text=f"{row['bot_name']}    ·    {FREQUENCIES[row['frequency']]} {row['clock']}")
            if self.instruction.get("1.0", "end-1c") != row["prompt"]:
                self.instruction.configure(state="normal")
                self.instruction.delete("1.0", "end")
                self.instruction.insert("1.0", row["prompt"])
                self.instruction.configure(state="disabled")
        try:
            runs = self.store.history(self.selected) if row else []
            if runs != self.run_rows:
                previous = self.history.selection()
                self.history.delete(*self.history.get_children())
                for run in runs:
                    self.history.insert("", "end", iid=str(run["id"]), values=(
                        run["started"].replace("T", " "), run["trigger"], run["status"]))
                self.run_rows = runs
                if runs:
                    selected_run = previous[0] if previous and self.history.exists(previous[0]) else str(runs[0]["id"])
                    self.history.selection_set(selected_run)
            if any(run["status"] == ACTIVE for run in runs):
                self.buttons["立即运行"].configure(state="disabled")
            self._show_result()
        except _ERRORS as exc:
            self.hint.set(str(exc))

    def _show_result(self, _event=None):
        selection = self.history.selection()
        try:
            text = self.store.result(selection[0]) if selection else ""
        except _ERRORS as exc:
            text = str(exc)
        if not text and self.selected in self.rows:
            text = "正在等待 Bot 的答复。" if selection else "尚无运行记录，可立即运行一次。"
        if self.answer.get("1.0", "end-1c") != text:
            self.answer.configure(state="normal")
            self.answer.delete("1.0", "end")
            self.answer.insert("end", text)
            self.answer.configure(state="disabled")

    def edit_new(self):
        self._editor(None)

    def edit_current(self):
        if self.selected in self.rows:
            self._editor(self.rows[self.selected])

    def _editor(self, row):
        try:
            bots = self.desk.account_store.list_bots()
            if not bots:
                self.hint.set("先保存一个 Bot，再回到「例行任务」为它安排时间。")
                self.desk.bots.edit()
                return
            RoutineEditor(self, bots, row)
        except _ERRORS as exc:
            messagebox.showinfo("配置例行任务", str(exc), parent=self)

    def toggle(self):
        try:
            self.store.toggle(self.selected)
            self.refresh()
        except _ERRORS as exc:
            self.hint.set(str(exc))

    def delete(self):
        if self.selected and messagebox.askyesno("删除例行任务", "一起删除该任务和它的全部运行记录？", parent=self):
            try:
                self.store.delete(self.selected)
                self.refresh()
            except _ERRORS as exc:
                self.hint.set(str(exc))

    def _start(self, routine_id):
        if self.worker is not None or self.stop.is_set():
            return
        def work():
            try:
                run_id = None
                if routine_id is not None or self.store.needs_tick():
                    run_id = self.store.execute(routine_id, stop=self.stop)
                self.results.put((run_id, ""))
            except Exception as exc:
                self.results.put((None, str(exc)))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def run_now(self):
        if self.worker is not None:
            self.hint.set("另一项例行任务正在运行，请在结束后重试。")
        elif self.selected:
            self._start(self.selected)
            self.hint.set("正在执行例行任务…")

    def poll(self):
        if self.stop.is_set():
            return
        try:
            run_id, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.worker = None
            if run_id and self.winfo_ismapped():
                self.refresh()
            if error:
                self.hint.set(error)
        now = time.monotonic()
        if now < self.next_tick:
            return
        self.next_tick = now + 5
        try:
            if self.winfo_ismapped():
                self.refresh()
            if self.worker is None:
                self._start(None)
        except _ERRORS as exc:
            self.hint.set(str(exc))

    def _destroyed(self, event):
        if event.widget is self:
            self.close()

    def close(self):
        self.stop.set()
