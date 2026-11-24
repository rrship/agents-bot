"""Local quick switcher for named bots, workbench pages and recent team jobs."""
from dataclasses import dataclass
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

import desktop_style as ui


@dataclass(frozen=True)
class Destination:
    kind: str
    key: str
    label: str
    detail: str = ""


def matches(entries, query):
    terms = query.casefold().split()
    return [item for item in entries if all(word in
            f"{item.kind} {item.label} {item.detail}".casefold() for word in terms)]


def remember_job(store, job):
    from team_modes import job_mode
    path = str(Path(job.path).resolve())
    title = " ".join(job.state["goal"].split())[:160]
    project = Path(job.state.get("root") or job.path).name
    with store._database(write=True) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS recent_jobs (
            path TEXT PRIMARY KEY, title TEXT NOT NULL, project TEXT NOT NULL,
            mode TEXT NOT NULL, opened INTEGER NOT NULL)""")
        db.execute("""INSERT INTO recent_jobs VALUES (?,?,?,?,?) ON CONFLICT(path)
            DO UPDATE SET title=excluded.title, project=excluded.project, mode=excluded.mode,
            opened=excluded.opened""", (path, title, project, job_mode(job.state), time.time_ns()))
        db.execute("""DELETE FROM recent_jobs WHERE path NOT IN
            (SELECT path FROM recent_jobs ORDER BY opened DESC, path LIMIT 40)""")


def recent_jobs(store):
    with store._database() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recent_jobs'").fetchone():
            return []
        return [dict(row) for row in db.execute(
            "SELECT path,title,project,mode FROM recent_jobs ORDER BY opened DESC, path")]


class WorkspaceSearch(tk.Toplevel):
    def __init__(self, desk, entries, activate):
        super().__init__(desk.root)
        self.title("搜索工作区")
        self.transient(desk.root)
        self.resizable(True, True)
        self.minsize(420, 290)
        self.configure(bg=ui.SURFACE)
        self.entries = entries
        self.activate = activate
        self.query = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        head = ttk.Frame(self, padding=(16, 14, 16, 8))
        head.grid(row=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="查找", style="Section.TLabel").grid(row=0, column=0, padx=(0, 10))
        self.input = ttk.Entry(head, textvariable=self.query)
        self.input.grid(row=0, column=1, sticky="ew")
        body = ttk.Frame(self, padding=(12, 0, 12, 0))
        body.grid(row=1, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.results = ttk.Treeview(body, columns=("kind", "detail"), show="tree headings",
                                    selectmode="browse", height=10, style="Switch.Treeview")
        for col, label, width, stretch in (("#0", "名称", 220, True), ("kind", "类别", 72, False),
                                           ("detail", "位置 / 连接", 140, True)):
            self.results.heading(col, text=label, anchor="w")
            self.results.column(col, width=width, minwidth=60, stretch=stretch, anchor="w")
        self.results.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, command=self.results.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.results.configure(yscrollcommand=scroll.set)
        self.hint = ttk.Label(self, style="Hint.TLabel")
        self.hint.grid(row=2, sticky="w", padx=18, pady=8)
        self.input.bind("<Down>", lambda _: self._move(1))
        self.input.bind("<Up>", lambda _: self._move(-1))
        self.results.bind("<Double-1>", self._click)
        self.bind("<Return>", self._open)
        self.bind("<Escape>", lambda _: self.destroy())
        self._trace = self.query.trace_add("write", self._filter)
        self._filter()
        x = desk.root.winfo_rootx() + max(0, (desk.root.winfo_width() - 570) // 2)
        y = desk.root.winfo_rooty() + max(0, (desk.root.winfo_height() - 360) // 3)
        self.geometry(f"570x360{x:+d}{y:+d}")
        self.grab_set()
        self.input.focus_set()

    def destroy(self):
        if hasattr(self, "_trace"):
            self.query.trace_remove("write", self._trace)
            del self._trace
        super().destroy()

    def _filter(self, *_):
        self.results.delete(*self.results.get_children())
        self.visible = matches(self.entries, self.query.get())
        for index, item in enumerate(self.visible):
            self.results.insert("", "end", iid=str(index), text=item.label, values=(item.kind, item.detail))
        if self.visible:
            self.results.selection_set("0")
            self.results.focus("0")
        self.hint.configure(text="↑ ↓ 选择     Enter 打开     Esc 关闭" if self.visible else "没有匹配的结果")

    def _move(self, offset):
        current = self.results.selection()
        if current:
            target = str(max(0, min(len(self.visible) - 1, int(current[0]) + offset)))
            self.results.selection_set(target)
            self.results.focus(target)
            self.results.see(target)
        return "break"

    def _click(self, event):
        row = self.results.identify_row(event.y)
        if row:
            self.results.selection_set(row)
            return self._open()

    def _open(self, _event=None):
        row = self.results.selection()
        if row:
            item = self.visible[int(row[0])]
            self.destroy()
            self.activate(item)
        return "break"
