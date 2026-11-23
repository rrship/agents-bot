"""Connect native desktop assistants to an Agents Bot team."""
import json
from pathlib import Path
import sqlite3
import sys
import time
import tkinter as tk
from tkinter import ttk
from uuid import uuid4

from engine import clean_path, parse
from handoff import parse_handoff
from software_bridge import BridgeError, SOFTWARE_NAMES
import software_setup


GUIDES = {
    "Codex": "写入配置后，重新打开 Codex 会话。开始协作，再把领取指令发给 Codex。",
    "Claude Desktop": "写入配置后完全退出并重启 Claude Desktop。开始协作，再把领取指令发到新对话。",
    "Antigravity": "在 Antigravity 的 MCP 设置中打开原始配置，合并复制的服务条目并刷新，然后在会话中发送领取指令。",
    "ZCode": "在 ZCode 的 MCP 设置中添加本地 stdio 服务，导入复制的配置或填写 command 和 args，然后在会话中发送领取指令。",
    "DeepSeek Harness": "在 Harness 的 MCP 设置中添加 stdio 服务，按复制的字段填写名称、命令和参数，然后在对话中发送领取指令。",
    "其他 Harness": "在支持本地 stdio MCP 的软件里添加此服务。宿主如使用独立表单，请从配置中分别填写命令和参数。",
}
NATIVE_HELP = "在已登录的 DeepSeek Harness 中启用 dsh-baton-host，并安装 dsh-baton 命令。开始协作后，任务会直接发到桌面插件。"
CODEX_HELP = "使用已登录的 Codex 运行时主动派单。开始协作后，App Server 为每次分工建立独立回合，回复会回到团队对话。"


def server_command(app, database):
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    args = ["-B", "-X", "utf8", str(Path(__file__).resolve().with_name("software_bridge.py")),
            "--app", app, "--database", str(Path(database).resolve())]
    return str(executable), args


def config_text(app, database):
    command, args = server_command(app, database)
    entry = {"command": command, "args": args}
    if app == "Codex":
        return ("[mcp_servers.agents_bot]\ncommand = " + json.dumps(command, ensure_ascii=False)
                + "\nargs = " + json.dumps(args, ensure_ascii=False) + "\n")
    if app == "DeepSeek Harness":
        value = {"serverName": "agents-bot", "transport": "stdio", **entry}
    else:
        value = {"mcpServers": {"agents-bot": entry}}
    return json.dumps(value, ensure_ascii=False, indent=2)


def validated_reply(app, prompt, raw):
    """Catch common copy/paste errors while the manual lease is still usable."""
    try:
        reply = parse(raw)
    except (ValueError, IndexError):
        raise ValueError("请从目标对话复制完整的 JSON 回复，其中需要有 message 字段。") from None
    header, _, context = prompt.partition("\n")
    try:
        ctx = json.loads(context)
    except ValueError:
        ctx = {}
    stage = ctx.get("stage", "")
    if "阶段=派单" in header or stage == "plan":
        workers = ctx.get("workers", [])
        items = reply.get("assignments")
        if (not isinstance(items, dict) or set(items) != set(workers) or
                any(not isinstance(v, str) or not v.strip() for v in items.values())):
            raise ValueError("请在 assignments 中为每位执行成员填写分工。")
    if "阶段=实现" in header or stage == "implement":
        transfer = parse_handoff(reply, ctx.get("opc_role", app), ctx.get("handoff_candidates", []))
        files = reply.get("files", {} if transfer else None)
        if not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()):
            raise ValueError("请在 files 中按相对路径提供完整的文件内容。")
        for path in files:
            clean_path(path)
    if "阶段=互审" in header or "阶段=验收复核" in header or stage == "review":
        if type(reply.get("approved")) is not bool:
            raise ValueError("请在 approved 中填写 true 或 false。")
    if "阶段=验收复核" in header:
        checks = reply.get("checklist")
        if not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c.strip() for c in checks):
            raise ValueError("组长复核需要填写非空 checklist 验收清单。")
    return json.dumps(reply, ensure_ascii=False)


class SoftwarePanel(ttk.Frame):
    def __init__(self, parent, desk):
        super().__init__(parent, padding=16)
        self.desk = desk
        self.opc_context = False
        self.client = "manual-" + uuid4().hex
        self.tickets = {}
        self.notes = {}
        self.next_refresh = 0
        self.app_name = tk.StringVar(self, "Codex")
        self.harness_mode = tk.StringVar(self, "baton")
        self.codex_mode = tk.StringVar(self, "mcp")
        self.guidance = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self.queue_state = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        heading = ttk.Frame(self)
        heading.grid(row=0, sticky="ew", pady=(0, 8))
        ttk.Label(heading, text="软件协作", style="Title.TLabel").pack(side="left")
        self.preset = ttk.Button(heading, text="用 Codex 和 Claude 组队", command=self.quick_team,
                                 style="Accent.TButton")
        self.preset.pack(side="right")
        desk.team_controls.append(self.preset)
        self.members_title = ttk.Label(self, text="参与常规团队的软件", style="Section.TLabel")
        self.members_title.grid(row=1, sticky="w")
        members = self.members_frame = ttk.Frame(self)
        members.grid(row=2, sticky="ew", pady=(3, 7))
        self.checks = {}
        for i, name in enumerate(SOFTWARE_NAMES):
            box = ttk.Checkbutton(members, text=name, variable=desk.member_vars[name],
                                  command=lambda n=name: self.app_name.set(n))
            box.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 16))
            desk.team_controls.append(box)
            self.checks[name] = box
        ttk.Separator(self).grid(row=3, sticky="ew", pady=(2, 8))
        settings = ttk.Frame(self)
        settings.grid(row=4, sticky="ew")
        ttk.Label(settings, text="配置软件", style="Section.TLabel").pack(side="left")
        self.selector = ttk.Combobox(settings, values=SOFTWARE_NAMES, state="readonly", width=20,
                                     textvariable=self.app_name)
        self.selector.pack(side="left", padx=10)
        ttk.Label(settings, textvariable=self.queue_state, style="Hint.TLabel").pack(side="left")
        self.routes = ttk.Frame(self)
        self.routes.grid(row=5, sticky="w", pady=(3, 0))
        self.codex_routes = ttk.Frame(self)
        self.codex_routes.grid(row=5, sticky="w", pady=(3, 0))
        for label, value in (("当前会话 / MCP", "mcp"), ("App Server 独立会话", "codex-native")):
            ttk.Radiobutton(self.codex_routes, text=label, variable=self.codex_mode, value=value).pack(
                side="left", padx=(0, 8))
        for label, value in (("原生插件", "baton"), ("MCP / 手动", "mcp")):
            ttk.Radiobutton(self.routes, text=label, variable=self.harness_mode, value=value).pack(
                side="left", padx=(0, 8))
        self.help_label = ttk.Label(self, textvariable=self.guidance, wraplength=500,
                                    justify="left", style="Hint.TLabel")
        self.help_label.grid(row=6, sticky="ew", pady=(5, 6))
        actions = ttk.Frame(self)
        actions.grid(row=7, sticky="w")
        self.install_button = ttk.Button(actions, text="写入 MCP 配置", command=self.install_config)
        self.install_button.grid(row=0, column=0, padx=(0, 5))
        self.copy_config = ttk.Button(actions, text="复制配置", command=self.copy_settings)
        self.copy_config.grid(row=0, column=1, padx=(0, 5))
        self.copy_instruction = ttk.Button(actions, text="复制领取指令", command=self.copy_prompt)
        self.copy_instruction.grid(row=0, column=2)
        self.manual = ttk.Frame(self)
        self.manual.grid(row=8, sticky="ew", pady=(12, 0))
        ttk.Separator(self.manual).pack(fill="x", pady=(0, 7))
        ttk.Label(self.manual, text="手动交接", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.manual, text="把任务粘贴到对应软件，复制它的完整 JSON 回复后交回。",
                  style="Hint.TLabel", wraplength=450).pack(anchor="w", pady=(2, 5))
        row = ttk.Frame(self.manual)
        row.pack(anchor="w")
        self.claim_button = ttk.Button(row, text="领取并复制任务", command=self.claim)
        self.claim_button.pack(side="left", padx=(0, 5))
        self.submit_button = ttk.Button(row, text="粘贴并提交结果", command=self.submit)
        self.submit_button.pack(side="left")
        self.status_label = ttk.Label(self, textvariable=self.status, wraplength=500, justify="left")
        self.status_label.grid(row=9, sticky="ew", pady=(8, 0))
        self.bind("<Configure>", self.resize)
        self._traces = [(variable, variable.trace_add("write", self.render)) for variable in
                        (self.app_name, self.harness_mode, self.codex_mode)]
        self.render()

    def destroy(self):
        for variable, callback in self._traces:
            variable.trace_remove("write", callback)
        super().destroy()

    def resize(self, event):
        if event.widget is self and event.width > 100:
            width = max(230, event.width - 42)
            self.help_label.configure(wraplength=width)
            self.status_label.configure(wraplength=width)

    def quick_team(self):
        if self.desk.job is not None or self.desk.running or self.opc_context:
            return
        for name, var in self.desk.member_vars.items():
            var.set(name in ("Codex", "Claude Desktop"))
        self.desk.team_mode.set("leader")
        self.desk.leader.set("Codex")
        self.desk.tabs.select(self.desk.tasks)

    def set_opc_context(self, enabled):
        if self.opc_context == enabled:
            return
        self.opc_context = enabled
        if enabled:
            self.members_title.grid_remove()
            self.members_frame.grid_remove()
            self.preset.pack_forget()
        else:
            self.members_title.grid()
            self.members_frame.grid()
            self.preset.pack(side="right")
        self.render()

    def config(self, name):
        if name not in SOFTWARE_NAMES:
            raise ValueError("未知的软件成员。")
        if name == "DeepSeek Harness" and self.harness_mode.get() == "baton":
            return {"mode": "software", "transport": "baton"}
        if name == "Codex" and self.codex_mode.get() == "codex-native":
            return {"mode": "software", "transport": "codex-native"}
        return {"mode": "software", "bridge": self.desk.bridge()}

    def native_selected(self, app):
        return not self.opc_context and ((app == "DeepSeek Harness" and self.harness_mode.get() == "baton")
                or (app == "Codex" and self.codex_mode.get() == "codex-native"))

    def render(self, *_):
        app = self.app_name.get()
        deepseek = app == "DeepSeek Harness"
        native = self.native_selected(app)
        if app == "Codex" and not self.opc_context:
            self.codex_routes.grid()
        else:
            self.codex_routes.grid_remove()
        if deepseek and not self.opc_context:
            self.routes.grid()
        else:
            self.routes.grid_remove()
        if native:
            self.manual.grid_remove()
        else:
            self.manual.grid()
        if native:
            self.guidance.set(NATIVE_HELP if deepseek else CODEX_HELP)
        else:
            self.guidance.set(GUIDES[app])
        if app in ("Codex", "Claude Desktop") and not native:
            self.install_button.grid()
        else:
            self.install_button.grid_remove()
        for button in (self.copy_config, self.copy_instruction):
            if native:
                button.grid_remove()
            else:
                button.grid()
        self.submit_button.configure(state="normal" if app in self.tickets else "disabled")
        self.status.set(self.notes.get(app, "") if not native else "")
        self.queue_state.set("")
        self.next_refresh = 0

    def poll(self):
        if not self.winfo_ismapped() or time.monotonic() < self.next_refresh:
            return
        self.next_refresh = time.monotonic() + 1
        app = self.app_name.get()
        if self.native_selected(app):
            return
        # Fresh installs and users who only use API members need no queue file.
        if not self.desk.bridge_path.is_file():
            self.queue_state.set("暂无任务")
            return
        try:
            counts = self.desk.bridge().pending(app)
            self.queue_state.set(f"待领取 {counts['waiting']}  ·  已领取 {counts['claimed']}")
        except (OSError, sqlite3.Error, ValueError, BridgeError):
            self.queue_state.set("队列暂时不可用")

    def note(self, text):
        self.notes[self.app_name.get()] = text
        self.status.set(text)

    def copy(self, value):
        self.clipboard_clear()
        self.clipboard_append(value)

    def install_config(self):
        app = self.app_name.get()
        command, args = server_command(app, self.desk.bridge_path)
        try:
            path = software_setup.install(app, command, args)
            self.note(f"配置已写入 {path}。请重开目标会话，再发送领取指令。")
        except (OSError, ValueError) as error:
            self.note(f"无法写入配置。{error}")

    def copy_settings(self):
        app = self.app_name.get()
        try:
            self.copy(config_text(app, self.desk.bridge_path))
            self.note(f"已复制 {app} 的 MCP 配置，请合并到这个软件的设置。")
        except tk.TclError:
            self.note("无法访问剪贴板，请再试一次。")

    def copy_prompt(self):
        app = self.app_name.get()
        instruction = (f"请作为 {app} 参与 Agents Bot 团队。调用 Agents Bot 的 claim_task（wait_seconds=20）"
                       "领取任务，根据 prompt 处理。使用收到的 task_id 和 lease 调用 submit_result，"
                       "将符合阶段要求的完整 JSON 序列化为 reply。随后继续领取下一阶段；"
                       "若任务处理超过五分钟，请在到期前再次调用 claim_task 续期。"
                       "连续两次等待都没有任务时，结束本次领取并告诉我；需要时我会重新发送本指令。")
        try:
            self.copy(instruction)
            self.note(f"领取指令已复制。开始协作后，在 {app} 的会话中粘贴并发送。")
        except tk.TclError:
            self.note("无法访问剪贴板，请再试一次。")

    def claim(self):
        app = self.app_name.get()
        try:
            task = self.desk.bridge().claim(app, self.client)
            if task is None:
                self.tickets.pop(app, None)
                self.note(f"{app} 暂无可手动领取的任务。")
            else:
                self.tickets[app] = task
                self.copy(task["prompt"])
                self.note(f"已复制 {app} 的任务 {task['task_id'][:8]}。请粘贴到目标对话。")
        except tk.TclError:
            self.note("任务已领取，复制失败。请再次点击领取并复制任务。")
        except (BridgeError, ValueError, OSError, sqlite3.Error) as error:
            self.note(str(error))
        self.render()

    def submit(self):
        app = self.app_name.get()
        task = self.tickets.get(app)
        if not task:
            return
        try:
            reply = validated_reply(app, task["prompt"], self.clipboard_get())
            self.desk.bridge().complete(app, self.client, task["task_id"], task["lease"], reply)
        except tk.TclError:
            self.note("剪贴板中没有可读取的文本，请先复制软件回复。")
        except BridgeError as error:
            self.tickets.pop(app, None)
            self.note(f"{error} 请重新领取当前任务。")
        except (ValueError, OSError, sqlite3.Error) as error:
            self.note(str(error))
        else:
            del self.tickets[app]
            self.note(f"{app} 已交回任务 {task['task_id'][:8]}。下一阶段可继续领取。")
        self.render()
