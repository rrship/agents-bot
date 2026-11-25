"""Shared native desktop palette, controls and small navigation icons."""
import tkinter as tk
import sys
from tkinter import font as tkfont, ttk


FONT = "Microsoft YaHei UI"
BG = "#111221"
SURFACE = "#1b1d31"
FIELD = "#131629"
INK = "#eff0ff"
MUTED = "#b1b5cf"
BORDER = "#383c58"
SUBTLE = "#303251"
ACCENT = "#ad9aff"
ACCENT_INK = "#21163f"
MINT = "#68dfc3"
RAIL = "#10111e"
RAIL_TEXT = "#e6e4f7"
RAIL_SUB = "#a5a7c2"
RAIL_ACTIVE = "#302944"
_SECONDARY = "#34315c"
_SECONDARY_BORDER = "#544e82"
_DISABLED = "#27283e"
_DISABLED_INK = "#989ab1"
_SELECTION = "#51487a"

# Cycling palette for bot avatars
AVATAR_COLORS = [
    "#ac9bff", "#72d8c6", "#8bb5ff", "#e9b26e",
    "#eb9ecb", "#87cdeb", "#bfabed", "#abbf75",
]


def _button_tile(root, fill, border):
    """Small rounded outline. Ttk stretches its center and preserves its corners."""
    photo = tk.PhotoImage(master=root, width=15, height=15)
    inset = (4, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 4)
    for y, left in enumerate(inset):
        photo.put(border, to=(left, y, 15-left, y+1))
        if 0 < y < 14:
            inner = max(left+1, inset[y-1], inset[y+1])
            photo.put(fill, to=(inner, y, 15-inner, y+1))
    return photo


def _button_style(root, style, name, fill, edge, hover, pressed, ink):
    element = name + ".rounded"
    # Tcl stores the image names. Python owns their lifetime for this Tk root.
    if element not in style.element_names():
        colors = ((fill, edge), (_DISABLED, BORDER), (pressed, ACCENT),
                  (hover, ACCENT), (fill, MINT), (hover, MINT))
        normal, disabled, down, hot, focus, focused_hot = [_button_tile(root, *pair) for pair in colors]
        root._agents_button_images.extend((normal, disabled, down, hot, focus, focused_hot))
        style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", down),
                             ("active", "focus", focused_hot), ("focus", focus), ("active", hot),
                             border=5, padding=(3, 2), sticky="nsew")
    style.layout(name, [(element, {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"})]})]})])
    style.configure(name, width=0, background=fill, foreground=ink, padding=(9, 5),
                    borderwidth=0, relief="flat", anchor="center")
    style.map(name, foreground=[("disabled", _DISABLED_INK), ("!disabled", ink)],
              background=[("disabled", _DISABLED), ("pressed", pressed), ("active", hover)])


def _install_dark_title_bars(root):
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    try:
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    except (AttributeError, OSError):
        return
    set_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD)
    enabled = wintypes.BOOL(True)

    def set_dark(event):
        window = event.widget
        if not isinstance(window, (tk.Tk, tk.Toplevel)):
            return
        hwnd = int(window.wm_frame(), 0)
        if hwnd:
            result = set_attribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
            if result:
                set_attribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))

    root.bind_class("Toplevel", "<Map>", set_dark, add="+")
    root.bind("<Map>", set_dark, add="+")


def configure(root):
    root.configure(bg=BG)
    if not hasattr(root, "_agents_button_images"):
        root._agents_button_images = []
        _install_dark_title_bars(root)
    root.option_add("*Listbox.font", (FONT, 10))
    for widget in ("Listbox", "Menu"):
        for prop, value in (("background", FIELD), ("foreground", INK),
                            ("activeBackground", _SELECTION), ("activeForeground", INK),
                            ("selectBackground", _SELECTION), ("selectForeground", INK)):
            root.option_add(f"*{widget}.{prop}", value)
    root.option_add("*Toplevel.background", SURFACE)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=SURFACE, foreground=INK, font=(FONT, 10),
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE,
                    troughcolor=BG, focuscolor=ACCENT, selectbackground=_SELECTION, selectforeground=INK)
    style.map(".", background=[("disabled", SURFACE), ("active", SUBTLE)],
              foreground=[("disabled", _DISABLED_INK)],
              selectbackground=[("!focus", SUBTLE)], selectforeground=[("!focus", INK)])
    style.configure("TFrame", background=SURFACE)
    style.configure("TLabel", background=SURFACE, foreground=INK)
    style.configure("Hint.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Title.TLabel", font=(FONT, 18, "bold"))
    style.configure("Section.TLabel", font=(FONT, 11, "bold"), foreground=INK)
    _button_style(root, style, "TButton", _SECONDARY, _SECONDARY_BORDER, "#494171", "#292544", INK)
    _button_style(root, style, "Accent.TButton", ACCENT, ACCENT, "#c5b6ff", "#9583ed", ACCENT_INK)
    style.configure("Accent.TButton", padding=(12, 6), font=(FONT, 10, "bold"))
    _button_style(root, style, "Danger.TButton", "#492a40", "#78405d", "#60334c", "#3a2031", "#ffbdd0")
    style.configure("TMenubutton", width=0, padding=(10, 5), relief="flat", background=_SECONDARY,
                    bordercolor=_SECONDARY_BORDER, lightcolor=_SECONDARY, darkcolor=_SECONDARY,
                    arrowcolor=ACCENT)
    style.map("TMenubutton", background=[("disabled", _DISABLED), ("active", "#494171")],
              bordercolor=[("focus", MINT), ("active", ACCENT)])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, background=SUBTLE, fieldbackground=FIELD, foreground=INK, padding=(7, 5),
                        insertcolor=INK, arrowsize=12, arrowcolor=ACCENT, lightcolor=FIELD, darkcolor=FIELD)
        style.map(name, fieldbackground=[("disabled", BG), ("readonly", FIELD)],
                  background=[("disabled", _DISABLED), ("active", _SECONDARY), ("readonly", SUBTLE)],
                  foreground=[("disabled", _DISABLED_INK), ("readonly", INK)],
                  bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)],
                  arrowcolor=[("disabled", _DISABLED_INK)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=SURFACE, padding=(2, 3), indicatorbackground=FIELD,
                        indicatorforeground=ACCENT_INK, indicatormargin=(1, 1, 5, 1))
        style.map(name, background=[("active", SUBTLE)], bordercolor=[("focus", MINT), ("selected", ACCENT)],
                  indicatorbackground=[("disabled", _DISABLED), ("selected", ACCENT), ("pressed", SUBTLE)])
    style.configure("TNotebook", borderwidth=0, background=BG, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(10, 6))
    style.map("TNotebook.Tab",
              background=[("selected", _SECONDARY), ("active", SUBTLE), ("!selected", BG)],
              lightcolor=[("selected", _SECONDARY), ("!selected", BG)],
              foreground=[("selected", INK), ("active", INK)])
    style.layout("Pages.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.layout("Pages.TNotebook.Tab", [])
    style.configure("Pages.TNotebook", borderwidth=0, background=BG, tabmargins=0, tabposition="n")
    style.configure("TSeparator", background=BORDER)
    style.configure("Mode.Toolbutton", padding=(10, 4), background=FIELD, foreground=MUTED,
                    borderwidth=1, relief="flat")
    style.map("Mode.Toolbutton", background=[("selected", _SECONDARY), ("disabled", _DISABLED), ("active", SUBTLE)],
              foreground=[("selected", INK), ("disabled", _DISABLED_INK), ("active", INK)],
              bordercolor=[("focus", MINT), ("selected", _SECONDARY_BORDER)], relief=[("!disabled", "flat")],
              lightcolor=[("selected", _SECONDARY), ("active", SUBTLE)],
              darkcolor=[("selected", _SECONDARY), ("active", SUBTLE)])
    style.configure("Treeview", fieldbackground=FIELD, background=FIELD, foreground=INK, borderwidth=0)
    style.map("Treeview", background=[("selected", _SELECTION)], foreground=[("selected", INK)])
    style.configure("Switch.Treeview", rowheight=28)
    style.configure("Heading", background=SUBTLE, foreground=INK, relief="flat", font=(FONT, 9, "bold"))
    style.configure("TScrollbar", background=_SECONDARY, troughcolor=FIELD, arrowcolor=MUTED, arrowsize=11,
                    bordercolor=FIELD, darkcolor=_SECONDARY, lightcolor=_SECONDARY, relief="flat")
    style.map("TScrollbar", background=[("pressed", _SELECTION), ("active", _SECONDARY_BORDER)])


def style_text(widget, *, editor=False):
    widget.configure(bg=FIELD if editor else SURFACE, fg=INK, insertbackground=ACCENT,
                     selectbackground=_SELECTION, selectforeground=INK, relief="flat", borderwidth=0,
                     highlightthickness=1 if editor else 0, highlightbackground=BORDER, highlightcolor=ACCENT,
                     font=(FONT, 10), padx=10, pady=8, spacing1=2, spacing3=3)
    if hasattr(widget, "vbar"):
        widget.frame.configure(bg=widget.cget("background"))
        widget.vbar.destroy()
        bar = widget.vbar = ttk.Scrollbar(widget.frame, orient="vertical", command=widget.yview)

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
    """Pixel-art bot face on a transparent background."""
    color = AVATAR_COLORS[index % len(AVATAR_COLORS)]
    photo = tk.PhotoImage(master=master, width=size, height=size)
    def block(x1, y1, x2, y2, shade):
        photo.put(shade, to=tuple(round(n * size / 20) for n in (x1, y1, x2, y2)))
    block(9, 1, 11, 5, color)
    block(7, 1, 10, 3, color)
    block(4, 5, 16, 17, color)
    block(2, 7, 18, 15, color)
    block(1, 9, 3, 13, color)
    block(17, 9, 19, 13, color)
    block(4, 8, 16, 14, RAIL)
    block(6, 10, 8, 12, color)
    block(12, 10, 14, 12, color)
    return photo


def icon(master, name, color, size=20):
    """Render line icons into transparent Tk images at the current display scale."""
    from math import hypot
    segments = {
        "tasks": [(4,5,16,5), (4,10,13,10), (4,15,10,15)],
        "search": [(12,12,17,17)],
        "routines": [(10,5,10,10), (10,10,14,12)],
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
    circles = {"team": [(8.5,6,2.6),(15,7.3,1.9)], "search": [(8,8,5.5)],
               "routines": [(10,10,7.4)]}.get(name, ())
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
