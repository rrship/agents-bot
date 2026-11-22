"""Run selected model connections against the shared room protocol."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
import time
import uuid

from cloud_room import validated_files
from engine import parse
from handoff import HANDOFF_INSTRUCTION, automatic_handoff, bounded_prompt, eligible_members, parse_handoff
from providers import BUILTIN, MEMBER_NAMES, call


POLL_SECONDS = 2.5
RENEW_SECONDS = 40
INSTRUCTIONS = {
    "plan": '为 workers 中每一位成员安排独立工作、接口和验收条件。返回 {"message":"计划","assignments":{"成员名":"分工"}}。',
    "implement": '按 assignment 实现分工。接手时从 summary 和项目文件继续。只提交改动过的完整文本文件。'
                 '返回 {"message":"完成的工作","files":{"相对路径":"完整内容"}}。' + HANDOFF_INSTRUCTION,
    "review": '对照目标审阅当前文件和最近的交付记录。返回 {"message":"验收结论或具体整改意见","approved":true}。'
              '需要修改时令 approved 为 false，可以通过 assignments 给每名执行成员分配整改项。'
              '请区分代码审阅与实际运行的结果。',
}


def prepare(task):
    member, stage = task["member"], task["stage"]
    targets = eligible_members(member, task["peers"], task["visited"]) if stage == "implement" else []
    ctx = {key: task[key] for key in ("stage", "goal", "assignment", "summary", "files", "workers", "members", "leader")}
    ctx.update(messages=task["events"], handoff_candidates=targets)
    packet = automatic_handoff(ctx, member, targets, task["visited"])
    if packet:
        return None, ("handoff", {**packet, "files": {}})
    prompt = bounded_prompt(f"你是{member}。{INSTRUCTIONS[stage]}只输出一个 JSON 对象。", ctx)
    return prompt, None


def submission(task, prompt, answer):
    reply = parse(answer)
    stage = task["stage"]
    if stage == "plan":
        return "finish", dict(message=reply["message"], assignments=reply.get("assignments"))
    if stage == "review":
        return "finish", dict(message=reply["message"], approved=reply.get("approved"),
                              assignments=reply.get("assignments"))
    packet = parse_handoff(reply, task["member"], task["peers"], task["visited"])
    files = validated_files(reply.get("files", {} if packet else None))
    shown = json.loads(prompt.split("\n", 1)[1])["files"]
    for name in files:
        if name in task["files"] and name not in shown:
            raise ValueError("未完整展示的文件不能覆盖：" + name)
    if packet:
        return "handoff", dict(**packet, files=files)
    return "finish", dict(message=reply["message"], files=files)


def run_claim(rpc, lease, task, config, stopping):
    """Only this thread may commit. A late provider reply cannot revive a released lease."""
    committed = False
    cancelled = threading.Event()
    try:
        prompt, result = prepare(task)
        if result is None:
            received = threading.Event()
            response = {}

            def invoke():
                try:
                    if lease["member"] == BUILTIN:
                        from lite_agent import team_call
                        response["answer"] = team_call(config, prompt, stop=cancelled)
                    else:
                        response["answer"] = call(lease["member"], config, prompt)
                except Exception as error:
                    response["error"] = error
                finally:
                    received.set()

            threading.Thread(target=invoke, daemon=True).start()
            next_renew = time.monotonic() + RENEW_SECONDS
            while not received.wait(.2):
                if stopping():
                    return
                if time.monotonic() >= next_renew:
                    rpc("renew", **lease)
                    next_renew = time.monotonic() + RENEW_SECONDS
            if stopping():
                return
            if "error" in response:
                raise response["error"]
            result = submission(task, prompt, response["answer"])
        if not stopping():
            op, fields = result
            rpc(op, **lease, **fields)
            committed = True
    finally:
        cancelled.set()
        if not committed:
            try:
                rpc("release", **lease)
            except (ValueError, OSError, RuntimeError):
                # If disconnected, the server makes the task available when its lease expires.
                pass


def run_team(rpc, room, configs, stop, report):
    state = rpc("status", room=room)
    if not configs or any(name not in MEMBER_NAMES or name not in state["members"] for name in configs):
        raise ValueError("请选择房间内由本设备提供的成员")
    client = uuid.uuid4().hex
    failed = threading.Event()

    def stopping():
        return stop.is_set() or failed.is_set()

    def run_member(member, config):
        conflict_task = None
        while not stopping():
            task = rpc("claim", room=room, member=member, client=client)["task"]
            if task is None:
                if rpc("status", room=room)["status"] != "active":
                    return
                stop.wait(POLL_SECONDS)
                continue
            report(f"{member} 已领取任务 · {task['stage']}")
            lease = dict(room=room, client=client, member=member, id=task["id"], lease=task["lease"])
            try:
                run_claim(rpc, lease, task, config, stopping)
            except ValueError as error:
                if str(error).startswith("其他成员已更新 ") and conflict_task != task["id"]:
                    conflict_task = task["id"]
                    report(f"{member} 正在读取其他成员的改动，重新合并文件")
                    continue
                raise

    with ThreadPoolExecutor(max_workers=len(configs)) as pool:
        futures = [pool.submit(run_member, name, cfg) for name, cfg in configs.items()]
        try:
            for completed in as_completed(futures):
                completed.result()
        finally:
            failed.set()
