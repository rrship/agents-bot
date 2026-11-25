"""Keyboard-accessible action menus with a dark popup on Windows."""
import tkinter as tk
import sys
from tkinter import ttk

import desktop_style as ui


class ActionItem:
    def __init__(self, menu, index):
        self.menu, self.index = menu, index
        self.row = None

    def invoke(self):
        return self.menu.invoke(self.index)

    def configure(self, **options):
        if "text" in options:
            options["label"] = options.pop("text")
        self.menu.entryconfigure(self.index, **options)
        if self.row is not None:
            self.row.configure(text=self.cget("text"), state=self.cget("state"))

    def cget(self, option):
        return self.menu.entrycget(self.index, "label" if option == "text" else option)

    def instate(self, states):
        disabled = self.cget("state") == "disabled"
        return all((state == "disabled" and disabled) or (state == "!disabled" and not disabled)
                   for state in states)

    def state(self, states=None):
        if states:
            self.configure(state="disabled" if "disabled" in states else "normal")
        return ("disabled",) if self.cget("state") == "disabled" else ()


class ActionMenu(ttk.Menubutton):
    def __init__(self, parent, text="更多操作", **options):
        super().__init__(parent, text=text, takefocus=True, **options)
        self.menu = tk.Menu(self, tearoff=False)
        self.popup = None
        self._items = []
        self._rows = []
        self._previous_grab = None
        self.bind("<ButtonPress-1>", self.open)
        # Stop the platform menubutton class from posting its native light menu.
        self.bind("<ButtonRelease-1>", lambda _: "break")
        for key in ("space", "Return", "Down", "Up"):
            self.bind(f"<Key-{key}>", self.open)

    def add_action(self, label, callback, enabled=True):
        self.menu.add_command(label=label, command=callback, state="normal" if enabled else "disabled")
        item = ActionItem(self.menu, self.menu.index("end"))
        self._items.append(item)
        return item

    def add_separator(self):
        self.menu.add_separator()
        self._items.append(None)

    def _work_area(self):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("bounds", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            nearest = user32.MonitorFromWindow
            nearest.argtypes = (wintypes.HWND, wintypes.DWORD)
            nearest.restype = wintypes.HANDLE
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = nearest(self.winfo_id(), 2)  # MONITOR_DEFAULTTONEAREST
            if monitor and user32.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info)):
                work = info.work
                return work.left, work.top, work.right, work.bottom
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def open(self, event=None):
        if self.instate(["disabled"]):
            return "break"
        if self.popup is not None:
            self.dismiss()
            return "break"
        self._previous_grab = self.grab_current()
        self._previous_focus = self.focus_get()
        popup = self.popup = tk.Toplevel(self, bg=ui.BORDER, borderwidth=0)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        box = tk.Frame(popup, bg=ui.SURFACE, padx=4, pady=4)
        box.pack(fill="both", expand=True, padx=1, pady=1)
        self._rows = []
        for item in self._items:
            if item is None:
                tk.Frame(box, bg=ui.BORDER, height=1).pack(fill="x", padx=8, pady=3)
                continue
            row = tk.Button(box, text=item.cget("text"), anchor="w", font=(ui.FONT, 10),
                            background=ui.SURFACE, foreground=ui.INK, activebackground=ui.RAIL_ACTIVE,
                            activeforeground=ui.INK, disabledforeground=ui.MUTED, relief="flat",
                            highlightthickness=0, borderwidth=0, padx=12, pady=5, takefocus=False,
                            state=item.cget("state"), command=lambda i=item: self._select(i))
            row.pack(fill="x")
            item.row = row
            self._rows.append((row, item))
            row.bind("<Enter>", lambda _, r=row: self._highlight(r))
        popup.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        width = max(160, popup.winfo_reqwidth(), self.winfo_width())
        height = popup.winfo_reqheight()
        left, top, right, bottom = self._work_area()
        if y + height > bottom:
            y = max(top, self.winfo_rooty() - height - 4)
        owner = self.winfo_toplevel()
        if x + width > owner.winfo_rootx() + owner.winfo_width():
            x += self.winfo_width() - width
        x = max(left, min(x, right - width))
        # A leading minus in Tk geometry anchors to the opposite screen edge.
        popup.geometry(f"{width}x{height}+{x}+{y}")
        self._active = None
        popup.bind("<Down>", lambda _: self._step(1))
        popup.bind("<Up>", lambda _: self._step(-1))
        popup.bind("<Return>", self._activate)
        popup.bind("<space>", self._activate)
        for key in ("Escape", "Tab", "ISO_Left_Tab"):
            popup.bind("<" + key + ">", self.dismiss)
        popup.bind("<ButtonPress-1>", self._outside)
        self._owner_binding = self.winfo_toplevel().bind("<Unmap>", self._owner_hidden, add="+")
        popup.deiconify()
        popup.lift()
        popup.grab_set()
        popup.focus_set()
        if event is None or event.type == tk.EventType.KeyPress:
            self._step(-1 if event is not None and event.keysym == "Up" else 1)
        return "break"

    def _owner_hidden(self, event):
        if event.widget == self.winfo_toplevel():
            self.dismiss()

    def _highlight(self, row):
        self._active = None
        for button, item in self._rows:
            active = button is row and item.instate(["!disabled"])
            button.configure(bg=ui.RAIL_ACTIVE if active else ui.SURFACE)
            if active:
                self._active = item

    def _step(self, delta):
        enabled = [(row, item) for row, item in self._rows if item.instate(["!disabled"])]
        if enabled:
            current = next((i for i, (_, item) in enumerate(enabled) if item is self._active),
                           -1 if delta == 1 else 0)
            self._highlight(enabled[(current + delta) % len(enabled)][0])
        return "break"

    def _activate(self, _event):
        if self._active:
            self._select(self._active)
        return "break"

    def _select(self, item):
        self.dismiss()
        if self.instate(["!disabled"]) and item.instate(["!disabled"]):
            item.invoke()

    def _outside(self, event):
        p = self.popup
        if p and not (0 <= event.x_root - p.winfo_rootx() < p.winfo_width() and
                      0 <= event.y_root - p.winfo_rooty() < p.winfo_height()):
            return self.dismiss()

    def dismiss(self, _event=None):
        popup, self.popup = self.popup, None
        if popup is not None:
            self.winfo_toplevel().unbind("<Unmap>", self._owner_binding)
            restore_active = popup.focus_displayof() is not None
            popup.grab_release()
            popup.destroy()
            for _, item in self._rows:
                item.row = None
            self._rows.clear()
            if self._previous_grab is not None and self._previous_grab.winfo_exists():
                self._previous_grab.grab_set()
            focus = self._previous_focus
            self._previous_focus = self._previous_grab = self._active = None
            target = focus if focus is not None and focus.winfo_exists() else self
            if restore_active and target.winfo_viewable():
                target.focus_force()
            else:
                target.focus_set()
        return "break"

    def destroy(self):
        self.dismiss()
        super().destroy()
        self._items.clear()
        self.menu = None
