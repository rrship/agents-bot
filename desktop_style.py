"""Shared native desktop palette, controls and small navigation icons."""
import tkinter as tk
from tkinter import font as tkfont, ttk
from math import sqrt


FONT = "Microsoft YaHei UI"
BG = "#f0f4f8"
SURFACE = "#ffffff"
FIELD = "#f8fafc"
INK = "#0f172a"
MUTED = "#64748b"
BORDER = "#cbd5e1"
SUBTLE = "#e2e8f0"
ACCENT = "#2563eb"
RAIL = "#0f172a"
RAIL_TEXT = "#94a3b8"
RAIL_SUB = "#64748b"
RAIL_ACTIVE = "#1e293b"

# Cycling palette for bot avatars
AVATAR_COLORS = [
    "#3b82f6",  # blue
    "#8b5cf6",  # violet
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ef4444",  # rose
    "#06b6d4",  # cyan
    "#ec4899",  # pink
    "#84cc16",  # lime
]


def configure(root):
    root.configure(bg=BG)
    root.option_add("*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", SURFACE)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=SURFACE, foreground=INK, font=(FONT, 10),
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE,
                    troughcolor=BG, selectbackground=ACCENT, selectforeground=SURFACE)
    style.configure("TFrame", background=SURFACE)
    style.configure("TLabel", background=SURFACE, foreground=INK)
    style.configure("Hint.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Title.TLabel", font=(FONT, 18, "bold"))
    style.configure("Section.TLabel", font=(FONT, 11, "bold"), foreground=MUTED)
    style.configure("TButton", width=0, background=SURFACE, foreground=INK, padding=(10, 5),
                    borderwidth=1, relief="raised", focusthickness=1, focuscolor=BORDER)
    style.map("TButton",
              background=[("disabled", BG), ("pressed", SUBTLE), ("active", SUBTLE)],
              foreground=[("disabled", "#94a3b8")],
              bordercolor=[("focus", MUTED), ("active", MUTED)])
    style.configure("Accent.TButton", background=ACCENT, foreground=SURFACE, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=SURFACE, padding=(13, 7),
                    font=(FONT, 10, "bold"))
    style.map("Accent.TButton",
              background=[("disabled", "#e2e8f0"), ("pressed", "#1e40af"), ("active", "#1d4ed8")],
              foreground=[("disabled", "#94a3b8")],
              bordercolor=[("disabled", BORDER), ("!disabled", ACCENT)])
    style.configure("Danger.TButton", background=SURFACE, foreground="#b91c1c", bordercolor="#fca5a5",
                    lightcolor=SURFACE, darkcolor=SURFACE, padding=(10, 5))
    style.map("Danger.TButton",
              background=[("disabled", BG), ("pressed", "#fef2f2"), ("active", "#fff5f5")],
              foreground=[("disabled", "#94a3b8"), ("active", "#991b1b")],
              bordercolor=[("active", "#f87171")])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=FIELD, padding=(7, 5), arrowsize=12)
        style.map(name, fieldbackground=[("disabled", BG), ("readonly", FIELD)],
                  foreground=[("disabled", "#94a3b8")], bordercolor=[("focus", ACCENT)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=SURFACE, padding=(2, 3),
                        indicatorbackground=FIELD, indicatorforeground=INK)
        style.map(name, background=[("active", SUBTLE)], foreground=[("disabled", "#94a3b8")])
    style.configure("TNotebook", borderwidth=0, background=BG, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(10, 6))
    style.map("TNotebook.Tab",
              background=[("selected", SURFACE), ("active", SUBTLE)],
              foreground=[("selected", INK), ("active", INK)])
    style.layout("Pages.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.layout("Pages.TNotebook.Tab", [])
    style.configure("Pages.TNotebook", borderwidth=0, background=BG, tabmargins=0, tabposition="n")
    style.configure("TSeparator", background=BORDER)


def style_text(widget, *, editor=False):
    widget.configure(bg=FIELD if editor else SURFACE, fg=INK, insertbackground=ACCENT,
                     selectbackground="#bfdbfe", selectforeground=INK, relief="flat", borderwidth=0,
                     highlightthickness=1 if editor else 0, highlightbackground=BORDER, highlightcolor=ACCENT,
                     font=(FONT, 10), padx=10, pady=8, spacing1=2, spacing3=3)
    if hasattr(widget, "vbar"):
        bar = widget.vbar
        bar.configure(width=8, borderwidth=0, relief="flat", highlightthickness=0,
                      troughcolor=BG, background="#94a3b8", activebackground=MUTED)

        def scroll(first, last):
            if float(first) > 0 or float(last) < 1:
                if not bar.winfo_manager():
                    bar.pack(side="right", fill="y", before=widget._w)
            else:
                bar.pack_forget()
            bar.set(first, last)

        widget.configure(yscrollcommand=scroll)
    return widget


def bot_avatar(master, index, size=18):
    """Colored filled-circle avatar keyed by bot list index."""
    color = AVATAR_COLORS[index % len(AVATAR_COLORS)]
    photo = tk.PhotoImage(master=master, width=size, height=size)
    cx = cy = size / 2
    r = size / 2 - 0.5
    for row in range(size):
        for col in range(size):
            if sqrt((col + 0.5 - cx) ** 2 + (row + 0.5 - cy) ** 2) <= r:
                photo.put(color, (col, row))
    return photo


def icon(master, name, color, size=20):
    """Render line icons into transparent Tk images at the current display scale."""
    from math import hypot
    segments = {
        "tasks": [(4,5,16,5), (4,10,13,10), (4,15,10,15)],
        "opc": [(2,7,18,7), (18,7,18,16), (18,16,2,16), (2,16,2,7), (6,7,6,4),
                (6,4,14,4), (14,4,14,7), (2,10,9,12), (9,12,11,12), (11,12,18,10)],
        "software": [(2,3,13,3), (13,3,13,10), (13,10,8,10), (8,10,5,13), (5,13,5,10),
                     (5,10,2,10), (2,10,2,3), (15,7,18,7), (18,7,18,15), (18,15,15,15),
                     (15,15,15,18), (15,18,12,15), (12,15,8,15)],
        "team": [(3,16,4,13), (4,13,7,11), (7,11,10,11), (10,11,13,13),
                 (13,13,14,16), (14,11,17,13), (17,13,18,16)],
        "lite": [(9,2,10.8,7.1), (10.8,7.1,16,9), (16,9,10.8,11),
                 (10.8,11,9,16), (9,16,7.2,11), (7.2,11,2,9), (2,9,7.2,7.1), (7.2,7.1,9,2)],
        "cloud": [(5,15,15,15), (15,15,17.5,13.5), (17.5,13.5,18,11),
                  (18,11,16.7,9), (16.7,9,14.5,8.5), (14.5,8.5,13.5,5.5),
                  (13.5,5.5,11,4), (11,4,8,4), (8,4,5.7,6), (5.7,6,5,8.5),
                  (5,8.5,3,9.5), (3,9.5,2,12), (2,12,3,14), (3,14,5,15)],
        "phone": [(6,2.5,14,2.5), (14,2.5,14,17.5), (14,17.5,6,17.5),
                  (6,17.5,6,2.5), (9,15,11,15)],
        "mark": [(2,15.5,9,3.5), (9,3.5,11,3.5), (11,3.5,18,15.5), (7.8,12.5,13.5,12.5)],
        "activity": [(1,11,5,11), (5,11,8,4), (8,4,12,16), (12,16,15,9), (15,9,19,9)],
    }[name]
    circles = [(8.5,6,2.6),(15,7.3,1.9)] if name == "team" else []
    width = round(size * tkfont.nametofont("TkDefaultFont", root=master).metrics("linespace") / 17)
    width = max(size, min(width, size * 2))
    photo = tk.PhotoImage(master=master, width=width, height=width)
    for row in range(width):
        for col in range(width):
            x, y = (col+.5)*20/width, (row+.5)*20/width
            for ax, ay, bx, by in segments:
                dx, dy = bx-ax, by-ay
                t = max(0, min(1, ((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy)))
                if hypot(x-ax-t*dx, y-ay-t*dy) < (1.45 if name == "mark" else .85):
                    photo.put(color, (col, row))
                    break
            else:
                if any(abs(hypot(x-cx, y-cy)-r) < .85 for cx, cy, r in circles):
                    photo.put(color, (col, row))
    return photo
