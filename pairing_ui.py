"""Desktop pairing and control of previously authorized monitoring."""
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

from mobile import device_key, local_addresses, remember_address, supported_address, saved_address
from desktop_style import SURFACE, BORDER
from qrcodegen import QrCode


class PairingWindow(tk.Toplevel):
    def __init__(self, desk):
        super().__init__(desk.root)
        self.desk = desk
        self.title("手机监控")
        self.configure(bg=SURFACE, padx=22, pady=16)
        self.transient(desk.root)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        ips = local_addresses()
        current = desk.monitor.server.server_address[0] if desk.monitor else saved_address()
        preferred = current if current in ips else next(iter(ips), current)
        self.host = tk.StringVar(self, preferred)
        self.hint = tk.StringVar(self)
        self.link = ""
        self.tick = None
        ttk.Label(self, text="绑定手机", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="打开手机上的 Agents Bot，选择「扫码绑定」。", style="Hint.TLabel").pack(anchor="w", pady=(2, 12))
        tile = tk.Frame(self, bg="white", highlightthickness=1, highlightbackground=BORDER, padx=8, pady=8)
        tile.pack(fill="x")
        self.canvas = tk.Canvas(tile, width=235, height=235, bg="white", highlightthickness=0)
        self.canvas.pack()
        ttk.Label(self, textvariable=self.hint, wraplength=330, style="Hint.TLabel").pack(anchor="w", pady=(8, 10))
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="重新生成配对码", style="Accent.TButton", command=self.show_code).pack(side="left")
        self.options = ttk.Frame(self)
        self.options_button = ttk.Button(actions, text="连接选项 ▸", command=self.toggle_options)
        self.options_button.pack(side="right")
        ttk.Separator(self.options).pack(fill="x", pady=(2, 6))
        ttk.Label(self.options, text="电脑地址").pack(anchor="w")
        box = ttk.Combobox(self.options, textvariable=self.host, values=ips)
        box.pack(fill="x", pady=4)
        box.bind("<<ComboboxSelected>>", lambda event: self.show_code())
        tools = ttk.Frame(self.options)
        tools.pack(fill="x", pady=(3, 0))
        tools.columnconfigure((0, 1), weight=1)
        for index, (label, action) in enumerate((
                ("复制配对邀请", self.copy),
                ("异地连接 · Tailscale", lambda: webbrowser.open("https://tailscale.com/download/windows")),
                ("停止监控", self.stop), ("解除全部绑定", self.revoke))):
            ttk.Button(tools, text=label, command=action).grid(row=index//2, column=index%2, sticky="ew", padx=2, pady=2)
        self.show_code()

    def toggle_options(self):
        if self.options.winfo_manager():
            self.options.pack_forget()
            self.options_button.configure(text="连接选项 ▸")
        else:
            self.options.pack(fill="x", pady=5)
            self.options_button.configure(text="连接选项 ▾")

    def clear(self):
        if self.tick:
            self.after_cancel(self.tick)
            self.tick = None
        if self.desk.monitor:
            self.desk.monitor.cancel_invite()
        self.link = ""
        self.canvas.delete("all")

    def show_code(self):
        self.clear()
        addr = self.host.get().strip()
        if not supported_address(addr):
            addresses = local_addresses()
            if not addresses:
                self.hint.set("未找到可连接的地址。请连接 Wi-Fi 或 Tailscale 后重试。")
                return
            addr = addresses[0]
            self.host.set(addr)
        try:
            self.desk.enable_monitor(addr)
            remember_address(addr)
        except (OSError, ValueError) as exc:
            self.hint.set(f"未能开启监控。请检查连接选项后重试。{exc}")
            return
        self.link = self.desk.monitor.invite_link()
        qr = QrCode.encode_text(self.link, QrCode.Ecc.MEDIUM)
        size = qr.get_size()
        scale = 235 // (size + 8)
        edge = (235 - size * scale) // 2
        for y in range(size):
            for x in range(size):
                if qr.get_module(x, y):
                    left, top = edge + scale*x, edge + scale*y
                    self.canvas.create_rectangle(left, top, left+scale, top+scale, fill="black", width=0)
        self.refresh()

    def refresh(self):
        self.tick = None
        monitor = self.desk.monitor
        if not monitor or monitor.paired or time.monotonic() >= monitor.deadline:
            self.clear()
            self.hint.set("已完成配对，手机可以查看进度。" if monitor and monitor.paired else "配对码已到期，可重新生成。")
            return
        mode = "可跨网 · 两端需连接 Tailscale" if self.host.get().startswith("100.") else "局域网 · 请连接相同 Wi-Fi"
        self.hint.set(f"{mode}   剩余 {max(1, int(monitor.deadline - time.monotonic()))} 秒")
        self.tick = self.after(500, self.refresh)

    def copy(self):
        if not self.link:
            self.show_code()
        if self.link:
            self.clipboard_clear()
            self.clipboard_append(self.link)

    def dismiss(self):
        self.clear()
        self.destroy()

    def stop(self):
        self.clear()
        remember_address("")
        if self.desk.monitor:
            self.desk.monitor.close()
            self.desk.monitor = None
        self.hint.set("监控已停止。生成配对码可重新开启。")

    def revoke(self):
        if messagebox.askyesno("解除绑定", "让所有已绑定手机退出监控？", parent=self):
            self.stop()
            device_key(reset=True)
            self.hint.set("已解除全部绑定。")
