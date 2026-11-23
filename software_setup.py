"""Install one local MCP entry when the user chooses Connect in the workbench."""
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import tomllib


def config_path(app):
    if app == "Codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if app == "Claude Desktop":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ValueError("请打开 Claude Desktop 的 Developer 设置，在 Edit Config 中找到配置文件。")
        standard = Path(appdata) / "Claude" / "claude_desktop_config.json"
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates = list((Path(local) / "Packages").glob(
                "Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
            existing = ([standard] if standard.is_file() else []) + candidates
            if len(existing) == 1:
                return existing[0]
            if len(existing) > 1:
                raise ValueError("找到多个 Claude 配置。请在 Claude Desktop 的 Developer → Edit Config 中合并复制的配置。")
        return standard
    raise ValueError("此软件可通过「复制配置」连接。")


def _owned(entry, app):
    if not isinstance(entry, dict):
        return False
    args = entry.get("args")
    return (isinstance(args, list) and any(isinstance(v, str) and
            PureWindowsPath(v).name == "software_bridge.py" for v in args)
            and any(a == "--app" and b == app for a, b in zip(args, args[1:])))


def _merged(app, original, server_id, entry):
    codex = app == "Codex"
    data = tomllib.loads(original) if codex else json.loads(original or "{}")
    key = "mcp_servers" if codex else "mcpServers"
    if not isinstance(data, dict) or not isinstance(data.get(key, {}), dict):
        raise ValueError("现有 MCP 配置格式无法合并，请在目标软件中查看配置。")
    servers = data.setdefault(key, {})
    present = server_id in servers
    before = servers.get(server_id)
    if present and not _owned(before, app):
        raise ValueError("同名 MCP 服务已存在。请先在软件中核对该条目。")
    if present and all(before.get(k) == v for k, v in entry.items()):
        return original
    if not codex:
        servers[server_id] = {**(before or {}), **entry}
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    # Edit only our two generated values; any other TOML and comments remain verbatim.
    header = f"[mcp_servers.{server_id}]"
    fields = {k: json.dumps(v, ensure_ascii=False) for k, v in entry.items()}
    if present:
        lines = original.splitlines(keepends=True)
        index = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if index is None:
            raise ValueError("这个 MCP 条目使用了自定义 TOML 写法。请在 Codex 中合并复制的配置。")
        changed = set()
        for i in range(index + 1, len(lines)):
            if lines[i].lstrip().startswith("["):
                break
            match = re.match(r"^\s*(command|args)\s*=", lines[i])
            if match:
                name = match[1]
                # Accept a complete, generated single-line key only.
                if tomllib.loads(lines[i]).get(name) != before.get(name):
                    raise ValueError("请在 Codex 中手动更新该条目。")
                lines[i] = f"{name} = {fields[name]}\n"
                changed.add(name)
        if changed != set(fields):
            raise ValueError("请在 Codex 中手动更新该条目。")
        merged = "".join(lines)
    else:
        merged = original + ("\n\n" if original else "") + header + "\n" + "".join(
            f"{name} = {value}\n" for name, value in fields.items())
    expected = {**(before or {}), **entry}
    servers[server_id] = expected
    if tomllib.loads(merged) != data:
        raise ValueError("现有 TOML 配置需要手动合并。")
    return merged


def install(app, command, args, *, path=None, server_id=None):
    if app not in {"Codex", "Claude Desktop"}:
        raise ValueError("此软件可通过「复制配置」连接。")
    target = Path(path) if path is not None else config_path(app)
    name = server_id or ("agents_bot" if app == "Codex" else "agents-bot")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("MCP 服务名称无效。")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        raw = None
    original = (raw or b"").decode("utf-8-sig")
    merged = _merged(app, original, name, {"command": str(command), "args": list(args)})
    if merged == original:
        return target
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".agents-bot-", delete=False) as staged:
        temp = Path(staged.name)
    try:
        temp.write_bytes(merged.encode("utf-8-sig" if raw and raw.startswith(b"\xef\xbb\xbf") else "utf-8"))
        if raw is None:
            # Fail if another program created its config while we were preparing it.
            os.link(temp, target)
        else:
            if target.read_bytes() != raw:
                raise ValueError("配置刚被其他程序修改，请再试一次。")
            temp.chmod(target.stat().st_mode & 0o777)
            os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target
