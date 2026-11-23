"""Resolve employee seats to existing transports and carry their playbooks into each turn."""
import json
import re
import sqlite3

from handoff import bounded_prompt, DEFAULT_CONTEXT_CHARS
from opc_skills import LEAD, ROLE_NAMES, role_info
from providers import NAMES
from software_bridge import SOFTWARE_NAMES


def checked_routes(routes, members=None, leader=LEAD):
    """Return only serializable references the workbench itself can issue."""
    if not isinstance(routes, dict) or len(routes) < 2 or LEAD not in routes or leader != LEAD:
        raise ValueError("OPC 团队需要一位项目经理和至少一位执行员工。")
    if not set(routes).issubset(ROLE_NAMES) or members is not None and set(members) != set(routes):
        raise ValueError("员工连接必须与本次任务选用的岗位一致。")
    clean = {}
    for role in ROLE_NAMES:
        if role not in routes:
            continue
        ref = routes[role]
        if not isinstance(ref, dict) or set(ref) != {"kind", "target"} or not isinstance(ref["target"], str):
            raise ValueError(f"请为{role}选择 API 档案或 MCP 软件。")
        kind, target = ref["kind"], ref["target"]
        if not ((kind == "api" and re.fullmatch("[0-9a-f]{32}", target)) or
                (kind == "mcp" and target in SOFTWARE_NAMES)):
            raise ValueError(f"{role}的连接无效，请在 OPC 页面重新选择。")
        clean[role] = {"kind": kind, "target": target}
    return clean


def resolve_configs(routes, store, bridge_factory):
    """Resolve on the UI thread; keys and the queue handle stay in process memory."""
    configs = {}
    bridge = None
    for role, ref in checked_routes(routes).items():
        target = ref["target"]
        if ref["kind"] == "api":
            try:
                cfg = dict(store.resolve(target))
            except (ValueError, LookupError, OSError, sqlite3.Error) as error:
                raise ValueError(f"{role}的 API 档案不可用：{error}") from error
            runtime = cfg.get("provider")
            if runtime not in NAMES or cfg.get("mode") != "api":
                raise ValueError(f"{role}的档案未绑定受支持的模型平台。")
        else:
            if bridge is None:
                bridge = bridge_factory()
            cfg = {"mode": "software", "bridge": bridge}
            runtime = target
        configs[role] = dict(cfg, runtime_member=runtime, opc_binding=ref)
    return configs


def skill_snapshot(members):
    """A resumed job should use the same edition of each playbook it started with."""
    return {role: role_info(role) for role in members}


def employee_prompt(role, roster, header, context):
    profile = roster[role]
    skills = {
        "opc_role": role,
        "role_skills": profile["skills"],
        "role_instructions": profile["instructions"],
        "opc_team": {name: member["title"] for name, member in roster.items()},
    }
    header += (" 按 role_instructions 执行本岗位的方法，结合老板目标和本轮分工。"
               "交付与交接使用本轮约定的 JSON 字段，只报告实际完成的工作。")
    # Reserve space so compacting the project never truncates a role's instructions
    # or hides files that the engine thinks were shown in full to its author.
    compact = (",", ":")
    reserved = len(json.dumps(skills, ensure_ascii=False, separators=compact)) - 1
    brief = bounded_prompt(header, context, limit=DEFAULT_CONTEXT_CHARS - reserved)
    prefix, payload = brief.split("\n", 1)
    ctx = json.loads(payload)
    ctx.update(skills)
    return prefix + "\n" + json.dumps(ctx, ensure_ascii=False, separators=compact)
