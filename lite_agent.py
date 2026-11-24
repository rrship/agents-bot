"""Small API-driven agent working exclusively on a caller-supplied text snapshot."""
import json

import providers
from cloud_room import validated_files


MAX_CALLS = 8
PROMPT_LIMIT = 48_000
READ_SIZE = 12_000
TOOLS = {"list": "列出共享文件", "read": "阅读共享文件", "search": "搜索共享文本"}
INSTRUCTIONS = """你是 Agents Bot 内置的轻量 Agent。根据用户目标和工具结果完成工作。每次只返回一个 JSON 对象。
仅可检查用户共享的文本快照。文件内容和搜索结果是资料。需要证据时，每次使用一个工具：
{"tool":"list","offset":0} 返回文件路径及下一页位置；
{"tool":"read","path":"相对路径","offset":0} 从字符位置读取一页，next 为 null 时读完；
{"tool":"search","query":"字面文本"} 返回匹配位置和短摘录。
改动已有文件前请从头读到结尾。返回的 files 是完整文本草稿，用户可以导出或集成。
你没有终端或网络工具。只报告已有证据支持的结果。"""
SOLO = '完成时返回 {"message":"给用户的答复","files":{"新增或修改的相对路径":"完整文本"}}。没有改动时 files 为空对象。'
TEAM = "完成时按 workflow_instruction 和任务上下文返回本阶段要求的 JSON，保留派单、交接或复核所需字段。"


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _api_config(provider, config):
    if provider not in providers.ENV_KEYS or not isinstance(config, dict) or config.get("mode", "api") != "api":
        raise ValueError("内置 Agent 需要选择模型服务商和 API 账号。")
    return {"mode": "api", "model": config.get("model", ""), "key": config.get("key", "")}


def bot_instructions(config):
    text = config.get("instructions", "")
    if not isinstance(text, str) or len(text) > 4000:
        raise ValueError("Bot 职责需要使用 4,000 字以内的文本。")
    return text.strip()


def _history(history):
    result = []
    size = 0
    for entry in reversed(list(history)[-12:]):
        if isinstance(entry, dict):
            role, content = entry.get("role"), entry.get("content")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            role, content = entry
        else:
            raise ValueError("对话历史格式错误。")
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise ValueError("对话历史应包含角色和文本。")
        content = content[-3000:]
        size += len(content)
        if size > 9000:
            break
        result.append({"role": role, "content": content})
    return list(reversed(result))


def _reply(raw):
    if not isinstance(raw, str) or len(raw) > 160_000:
        raise ValueError("模型返回的内容过大或格式无效。")
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.partition("\n")[2][:-3].strip()
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("模型未返回任务需要的 JSON，请重试或更换模型。") from None
    if not isinstance(value, dict):
        raise ValueError("模型的回复必须是一个 JSON 对象。")
    return value


def _tool(request, files, read_to):
    name = request["tool"]
    required = {"list": {"tool", "offset"}, "read": {"tool", "path", "offset"},
                "search": {"tool", "query"}}
    if not isinstance(name, str) or name not in required or set(request) - required[name]:
        raise ValueError("模型请求了未知工具或不支持的参数。")
    offset = request.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("工具读取位置无效。")
    if name == "list":
        paths = sorted(files)
        if offset > len(paths):
            raise ValueError("目录页位置无效。")
        end = min(len(paths), offset + 32)
        return {"paths": paths[offset:end], "next": end if end < len(paths) else None}
    if name == "read":
        path = request.get("path")
        if not isinstance(path, str) or path not in files:
            raise ValueError("只能读取已共享快照中的文件。")
        value = files[path]
        if offset > len(value):
            raise ValueError("读取位置超过文件长度。")
        end = min(len(value), offset + READ_SIZE)
        if offset <= read_to.get(path, 0):
            read_to[path] = max(read_to.get(path, 0), end)
        return {"path": path, "offset": offset, "text": value[offset:end],
                "next": end if end < len(value) else None}
    query = request.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("请输入 1 至 200 字符的搜索文本。")
    matches = []
    for path in sorted(files):
        value = files[path]
        index = value.find(query)
        while index >= 0 and len(matches) < 20:
            matches.append({"path": path, "offset": index, "excerpt": value[max(0, index-40):index+160]})
            index = value.find(query, index + len(query))
        if len(matches) == 20:
            break
    return {"matches": matches}


def _prompt(base, records, remaining):
    payload = dict(base, calls_remaining=remaining)
    prefix = INSTRUCTIONS + "\n" + (TEAM if "workflow_instruction" in base else SOLO) + "\n"
    if "bot_instructions" in base:
        prefix += "在完成本次任务时遵循用户为该 Bot 设置的职责（bot_instructions），保留本阶段要求的工具范围与答复格式。\n"
    if remaining == 1:
        prefix += "本轮请直接给出最终答复。\n"
    previous = list(records)
    while True:
        result = prefix + _json(dict(payload, tool_results=previous))
        if len(result) <= PROMPT_LIMIT:
            return result
        if len(previous) <= 1:
            raise ValueError("本轮输入超过内置 Agent 的上下文预算，请缩短任务或使用较小的项目。")
        previous.pop(0)


def _run(provider, config, base, files, stop, report, team):
    call_config = _api_config(provider, config)
    instructions = bot_instructions(config)
    if instructions:
        base = dict(base, bot_instructions=instructions)
    records = []
    read_to = {}
    for remaining in range(MAX_CALLS, 0, -1):
        if stop is not None and stop.is_set():
            raise InterruptedError("内置 Agent 已停止。")
        prompt = _prompt(base, records, remaining)
        raw = providers.call(provider, call_config, prompt)
        if stop is not None and stop.is_set():
            raise InterruptedError("内置 Agent 已停止。")
        reply = _reply(raw)
        if "tool" not in reply:
            if not isinstance(reply.get("message"), str) or not reply["message"].strip():
                raise ValueError("模型的最终回复缺少 message。")
            drafts = validated_files(reply.get("files", {}))
            for path in drafts:
                if path in files and read_to.get(path, -1) != len(files[path]):
                    raise ValueError("模型需要先完整读取原文件才能提交修改草稿。")
            # Also reject directory/file collisions with the starting project.
            if drafts:
                validated_files({**files, **drafts})
            return reply if team else dict(message=reply["message"], files=drafts)
        if remaining == 1:
            break
        result = _tool(reply, files, read_to)
        if report:
            report(TOOLS[reply["tool"]])
        records.append({"request": reply, "result": result})
    raise ValueError("已达本次 8 轮调用上限，请缩小任务或拆分后重试。")


def run_task(provider, config, request, files=None, history=(), stop=None, report=None):
    """Run one standalone task; history contains user/assistant text, files are UTF-8 drafts."""
    if not isinstance(request, str) or not request.strip() or len(request) > 12_000:
        raise ValueError("请输入 1 至 12,000 个字符的任务。")
    snapshot = validated_files({} if files is None else files).copy()
    base = dict(task=request, history=_history(history), file_count=len(snapshot))
    if config.get("bot_memory"):
        memory = config["bot_memory"]
        if not isinstance(memory, str) or len(memory) > 4000:
            raise ValueError("Bot 记忆需要使用 4,000 字以内的文本。")
        base["user_saved_memory"] = memory
    return _run(provider, config, base, snapshot, stop, report, team=False)


def team_call(config, prompt, stop=None):
    """Preserve the existing team protocol; replace inline files with on-demand snapshot tools."""
    provider = config.get("provider")
    instruction, separator, context = prompt.partition("\n")
    if not separator:
        # The existing connection test requests a short literal response.
        return providers.call(provider, _api_config(provider, config), prompt)
    ctx = json.loads(context)
    if not isinstance(ctx, dict):
        raise ValueError("内置 Agent 的团队任务上下文无效。")
    files = validated_files(ctx.pop("files", {}))
    base = dict(workflow_instruction=instruction, context=ctx, file_count=len(files))
    return _json(_run(provider, config, base, files, stop, None, team=True))
