"""Load the offline employee handbooks and their pinned source references."""
from pathlib import Path


_BUNDLE = Path(__file__).resolve().with_name("opc_skills")
_MATT = "https://github.com/mattpocock/skills/blob/abeeaf95660ba9e8b7c753eb38f441ca707a8823/skills/engineering/"
_GITHUB = "https://github.com/github/awesome-copilot/blob/f10fdb8b2660ce85c98f220b2f1ca24653768a5c/skills/"

_ROLES = {
    "项目经理": ("project-manager", "拆分老板的目标，协调接口并复核交付", (
        ("可验收的任务拆分", _MATT + "to-tickets/SKILL.md"),
    )),
    "前端设计师": ("frontend-designer", "设计界面并交付可用的交互", (
        ("界面设计与实现", _GITHUB + "premium-frontend-ui/SKILL.md"),
        ("响应式与可访问性审查", _GITHUB + "web-design-reviewer/SKILL.md"),
    )),
    "后端开发员": ("backend-developer", "设计接口、实现业务规则与数据保存", (
        ("模块与接口设计", _MATT + "codebase-design/SKILL.md"),
    )),
    "测试工程师": ("qa-engineer", "设计行为测试，核对验收证据", (
        ("公共接口的测试驱动开发", _MATT + "tdd/SKILL.md"),
    )),
}

ROLE_NAMES = tuple(_ROLES)
LEAD = "项目经理"


def role_info(name):
    """Return the exact instructions to send with this employee's assignment."""
    if name not in _ROLES:
        raise ValueError(f"未知的 OPC 员工：{name}")
    folder, title, skills = _ROLES[name]
    path = _BUNDLE / folder / "SKILL.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取{name}的内置技能文件，请重新完整解压安装包。") from exc
    metadata, divider, instructions = raw.partition("\n---\n")
    if not metadata.startswith("---\n") or not divider or not instructions.strip():
        raise ValueError(f"{name}的内置技能文件不完整，请重新完整解压安装包。")
    return {
        "title": title,
        "skills": [label for label, _ in skills],
        "instructions": instructions.strip(),
        "sources": [url for _, url in skills],
    }
