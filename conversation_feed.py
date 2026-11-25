"""Selectable team conversation with explicit sender, recipients, and work stage."""
import re
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import desktop_style as ui


_HEADER = re.compile(
    r"^(?P<sender>[^→\[\]\n]{1,80}?)(?:\s+→\s+(?P<to>[^\[\]\n]+?))?\s+"
    r"\[(?P<stage>[^\]\n]{1,64})\]\s*(?P<body>.*)$"
)
_SYSTEM = re.compile(r"^(?:任务目录\s|任务目标 · |集成文件\s|验收\s|已载入任务\s|任务受阻[：:]|完成[：:]|"
                     r"待人工验收[：:]|已请求暂停|正在暂停|已取消$|已暂停$|受阻$|完成$|待人工验收$)")
_COLORS = (("#3b305a", "#ccbcff"), ("#203d40", "#83e2cc"), ("#283c5c", "#a7c8ff"),
           ("#443627", "#efc48a"), ("#223e54", "#8cdcf6"), ("#4a2e45", "#f6b9dc"))


def _events(text):
    """Read both individual events and concatenated legacy events.txt logs."""
    header = None
    body = []
    for line in text.splitlines():
        match = _HEADER.match(line)
        if match or _SYSTEM.match(line):
            if body or header:
                yield header, "\n".join(body)
            header = match
            body = [match["body"] if match else line]
        else:
            body.append(line)
    if body or header:
        yield header, "\n".join(body)


class ConversationFeed(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._colors = {}
        self._unread = 0
        self.log = ScrolledText(self, height=4, width=8, state="disabled", wrap="word",
                                cursor="xterm", takefocus=True, undo=False)
        ui.style_text(self.log)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.configure(padx=8, pady=6, spacing1=0, spacing3=0)
        for i, (_background, foreground) in enumerate(_COLORS):
            self.log.tag_configure(f"member{i}", foreground=foreground)
        self.log.tag_configure("speaker", font=(ui.FONT, 11, "bold"))
        self.log.tag_configure("avatar", spacing1=12, spacing3=2, lmargin1=1)
        self.log.tag_configure("to", font=(ui.FONT, 9), foreground=ui.MUTED, lmargin2=8)
        self.log.tag_configure("phase", foreground=ui.MUTED, font=(ui.FONT, 9))
        self.log.tag_configure("handoff", foreground=ui.MINT)
        self.log.tag_configure("message", foreground=ui.INK, font=(ui.FONT, 10),
                               lmargin1=38, lmargin2=38, rmargin=12, spacing1=2, spacing2=3, spacing3=8)
        self.log.tag_configure("subject", font=(ui.FONT, 13, "bold"), foreground=ui.INK,
                               lmargin1=6, lmargin2=6, rmargin=12, spacing1=5, spacing3=10)
        self.log.tag_configure("system", foreground=ui.MUTED, font=(ui.FONT, 9),
                               lmargin1=4, lmargin2=15, rmargin=4, spacing1=5, spacing2=1)
        self.empty_feed = ttk.Frame(self, padding=8)
        self._empty_art = tk.Canvas(self.empty_feed, width=158, height=64, background=ui.SURFACE,
                                    borderwidth=0, highlightthickness=0, takefocus=0)
        self._empty_art.pack()
        self._avatar_images = []
        for x, y, destination_x, destination_y in ((43, 29, 58, 37), (98, 37, 115, 24)):
            self._empty_art.create_line(x, y, destination_x, destination_y, fill=ui.MUTED, dash=(2, 3),
                                        width=1, arrow="last", arrowshape=(4, 5, 2))
        for x, y, idx, radius in ((26, 22, 0, 16), (78, 40, 1, 18), (133, 19, 2, 16)):
            self._empty_art.create_oval(x-radius, y-radius, x+radius, y+radius,
                                        outline=_COLORS[idx][0], fill=ui.FIELD, width=2)
            img = ui.bot_avatar(self._empty_art, idx, size=radius+3)
            self._avatar_images.append(img)
            self._empty_art.create_image(x, y, image=img)
        ttk.Label(self.empty_feed, text="尚无团队消息", style="Section.TLabel").pack(pady=(5, 3))
        ttk.Label(self.empty_feed, text="开始任务后，在这里查看成员的分工、交接和回复。",
                  wraplength=200, justify="center", style="Hint.TLabel").pack()
        self.latest = ttk.Button(self, command=self._show_latest)
        self.clear()

    def _show_latest(self):
        self.log.see("end")
        self._unread = 0
        self.latest.place_forget()

    def _message(self, header, body):
        sender = header["sender"].strip()
        idx = self._colors.setdefault(sender, len(self._colors) % len(_COLORS))
        fill, ink = _COLORS[idx]
        initials = "".join(w[0] for w in sender.split()[:2]).upper() if sender.isascii() else sender[0]
        avatar = tk.Canvas(self.log, width=29, height=29, bg=ui.SURFACE, borderwidth=0, highlightthickness=0,
                           takefocus=0)
        avatar.create_oval(1, 1, 28, 28, outline="", fill=fill)
        avatar.create_text(14.5, 14.5, text=initials, fill=ink, font=(ui.FONT, 9, "bold"))
        self.log.insert("end", " ", "avatar")
        self.log.window_create("end", window=avatar, align="center", padx=2, pady=2)
        self.log.insert("end", f" {sender} ", ("speaker", f"member{idx}"))
        if header["to"]:
            self.log.insert("end", "  →  " + header["to"].strip(), "to")
        stage = header["stage"]
        tags = ("phase", "handoff") if "交接" in stage else ("phase",)
        self.log.insert("end", "  ·  " + stage + "\n", tags)
        if body:
            self.log.insert("end", body + "\n", "message")

    def append(self, line):
        if not line:
            return
        was_empty = bool(self.empty_feed.winfo_manager())
        follow = was_empty or not self.log.winfo_ismapped() or self.log.yview()[1] >= .999
        count = 0
        self.empty_feed.place_forget()
        self.log.configure(state="normal")
        try:
            for header, body in _events(line):
                if header:
                    self._message(header, body)
                elif body.startswith("任务目标 · "):
                    self.log.insert("end", body + "\n", "subject")
                else:
                    self.log.insert("end", body + "\n", "system")
                count += 1
        finally:
            self.log.configure(state="disabled")
        if follow:
            self._show_latest()
        else:
            self._unread += count
            self.latest.configure(text=f"↓  {self._unread} 条新动态")
            self.latest.place(relx=1, rely=1, x=-17, y=-4, anchor="se")

    def clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._colors.clear()
        self._show_latest()
        self.empty_feed.place(relx=.5, rely=.48, anchor="center")
