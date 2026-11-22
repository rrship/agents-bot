"""Bounded task briefs shared by local and SSH team members."""
import json


# A portable character budget for the workbench. Model token limits vary.
DEFAULT_CONTEXT_CHARS = 48000
HANDOFF_INSTRUCTION = (
    '需要另一位成员接手时，可以返回 {"message":"进度","handoff":{"to":"候选成员名",'
    '"summary":"已完成、相关文件、接口决定和待办步骤","reason":"交接原因"},'
    '"files":{"已完成的相对路径":"完整内容"}}。files 可为空。'
    '仅向 handoff_candidates 中的成员交接。'
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clip(text, size):
    if len(text) <= size:
        return text
    half = (size - 1) // 2
    return text[:half] + "…" + text[-(size - half - 1):]


def eligible_members(agent, candidates, visited=()):
    excluded = set(visited) | {agent}
    return [name for name in candidates if name not in excluded]


def automatic_handoff(context, agent, candidates, visited=()):
    targets = eligible_members(agent, candidates, visited)
    if visited or not targets or len(_json(context)) <= DEFAULT_CONTEXT_CHARS:
        return None
    pieces = ["原分工：" + _clip(str(context.get("assignment") or context.get("goal", "")), 1100)]
    if context.get("summary"):
        pieces.append("进度：" + _clip(str(context["summary"]), 700))
    if context.get("feedback"):
        pieces.append("待处理反馈：" + _clip(str(context["feedback"]), 350))
    messages = context.get("messages") or []
    if messages:
        pieces.append("最近约定：" + _clip(str(messages[-1]), 450))
    paths = list(context.get("files", {}))
    if paths:
        assignment = str(context.get("assignment") or context.get("goal", ""))
        paths.sort(key=lambda path: (path not in assignment and path.rsplit("/", 1)[-1] not in assignment, path))
        pieces.append("共享文件（共 " + str(len(paths)) + " 个）：" + _clip("、".join(paths[:12]), 600))
    return dict(to=targets[0], summary="\n".join(pieces), reason="本轮上下文超过工作台字符预算")


def parse_handoff(reply, agent, candidates, visited=()):
    if "handoff" not in reply:
        return None
    packet = reply["handoff"]
    if not isinstance(packet, dict):
        raise ValueError("handoff 必须包含 to 和 summary")
    target = packet.get("to")
    if target not in eligible_members(agent, candidates, visited):
        raise ValueError("接手成员必须来自本次任务的可用成员，且尚未处理过这项分工")
    summary = packet.get("summary")
    reason = packet.get("reason", "成员主动交接")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(reason, str):
        raise ValueError("交接需要非空 summary 和文本 reason")
    return dict(to=target, summary=_clip(summary.strip(), 3200), reason=_clip(reason.strip(), 350))


def _shorten(value, text_limit, count):
    if isinstance(value, str):
        return _clip(value, text_limit)
    if isinstance(value, list):
        return [_shorten(item, text_limit, count) for item in value[-count:]]
    if isinstance(value, dict):
        return {k: _shorten(v, text_limit, count) for k, v in list(value.items())[:count]}
    return value


def bounded_prompt(header, context, limit=None):
    """Keep complete file contents, favor paths named in the assignment and signal omitted evidence."""
    limit = DEFAULT_CONTEXT_CHARS if limit is None else limit
    prefix = header.rstrip() + "\n"
    payload = _json(context)
    if len(prefix) + len(payload) <= limit:
        return prefix + payload
    files = context.get("files", {})
    brief = {k: v for k, v in context.items() if k != "files"}
    brief["context_compacted"] = True
    brief["source_file_count"] = len(files)
    brief["files"] = {}
    # Reduce conversational text only when it crowds out the project evidence.
    for chars, count in ((2400, 12), (800, 8), (256, 6), (96, 3)):
        if len(_json(brief)) <= (limit - len(prefix)) * (0.65 if files else 0.95):
            break
        brief = {
            k: _shorten(v, chars, count) for k, v in brief.items()
        }
    size = len(prefix) + len(_json(brief))
    if size > limit:
        raise ValueError("任务说明超过可用上下文，请缩短需求或减少本轮验收资料")
    focus = "\n".join(str(context.get(k, "")) for k in ("assignment", "summary", "feedback", "goal"))
    # A file omitted here is never presented as a complete, editable file.
    def priority(path):
        return (path not in focus and path.rsplit("/", 1)[-1] not in focus, path)
    for name in sorted(files, key=priority):
        value = files[name]
        addition = len(_json(name)) + len(_json(value)) + 1 + bool(brief["files"])
        if size + addition <= limit:
            brief["files"][name] = value
            size += addition
    return prefix + _json(brief)
