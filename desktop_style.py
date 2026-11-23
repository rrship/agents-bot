"""Shared native desktop palette, controls and small navigation icons."""
import tkinter as tk
from tkinter import font as tkfont, ttk


FONT = "Microsoft YaHei UI"
BG = "#f5f5f3"
SURFACE = "#ffffff"
FIELD = "#fbfbfa"
INK = "#202123"
MUTED = "#585b61"
BORDER = "#dededb"
SUBTLE = "#efefed"
ACCENT = "#252627"
RAIL = "#1c1d1f"
RAIL_TEXT = "#d2d3d5"
RAIL_SUB = "#a0a2a7"
RAIL_ACTIVE = "#38393c"


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
    style.configure("Title.TLabel", font=(FONT, 19, "bold"))
    style.configure("Section.TLabel", font=(FONT, 12, "bold"))
    style.configure("TButton", width=0, background=SURFACE, foreground=INK, padding=(9, 5),
                    borderwidth=1, relief="raised", focusthickness=1, focuscolor=MUTED)
    style.map("TButton", background=[("disabled", BG), ("pressed", BORDER), ("active", SUBTLE)],
              foreground=[("disabled", "#75767a")], bordercolor=[("focus", MUTED), ("active", "#a5a6a7")])
    style.configure("Accent.TButton", background=ACCENT, foreground=SURFACE, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=SURFACE, padding=(12, 7))
    style.map("Accent.TButton",
              background=[("disabled", "#e7e7e5"), ("pressed", "#111214"), ("active", "#404145")],
              foreground=[("disabled", "#686a6e")],
              bordercolor=[("disabled", BORDER), ("!disabled", ACCENT)])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=FIELD, padding=(6, 4), arrowsize=12)
        style.map(name, fieldbackground=[("disabled", BG), ("readonly", FIELD)],
                  foreground=[("disabled", "#77797c")], bordercolor=[("focus", MUTED)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=SURFACE, padding=(1, 3), indicatorbackground=FIELD, indicatorforeground=INK)
        style.map(name, background=[("active", SUBTLE)], foreground=[("disabled", "#77797c")])
    style.configure("TNotebook", borderwidth=0, background=SURFACE, tabmargins=(0, 0, 0, 3))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(8, 5))
    style.map("TNotebook.Tab", background=[("selected", ACCENT), ("active", SUBTLE)],
              foreground=[("selected", SURFACE)])
    style.layout("Pages.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.layout("Pages.TNotebook.Tab", [])
    style.configure("Pages.TNotebook", borderwidth=0, background=BG, tabmargins=0, tabposition="n")


def style_text(widget, *, editor=False):
    widget.configure(bg=FIELD if editor else SURFACE, fg=INK, insertbackground=INK,
                     selectbackground="#dce3ed", selectforeground=INK, relief="flat", borderwidth=0,
                     highlightthickness=1 if editor else 0, highlightbackground=BORDER, highlightcolor=MUTED,
                     font=(FONT, 10), padx=10, pady=7, spacing1=1, spacing3=2)
    if hasattr(widget, "vbar"):
        bar = widget.vbar
        bar.configure(width=9, borderwidth=0, relief="flat", highlightthickness=0,
                      troughcolor=BG, background=SUBTLE, activebackground=BORDER)

        def scroll(first, last):
            if float(first) > 0 or float(last) < 1:
                if not bar.winfo_manager():
                    # ScrolledText.__str__ identifies the wrapper, not its inner Text.
                    bar.pack(side="right", fill="y", before=widget._w)
            else:
                bar.pack_forget()
            bar.set(first, last)

        widget.configure(yscrollcommand=scroll)
    return widget


def icon(master, name, color, size=20):
    """Render line icons into transparent Tk images at the current display scale."""
    from math import hypot
    segments = {
        "tasks": [(4,5,16,5), (4,10,13,10), (4,15,10,15)],
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
