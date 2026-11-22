"""Local team workflow. Model outputs are data, never shell commands."""
import contextlib
import difflib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import uuid
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from handoff import HANDOFF_INSTRUCTION, automatic_handoff, bounded_prompt, eligible_members, parse_handoff

def call(agent, config, prompt):
    from providers import call as invoke
    return invoke(agent, config, prompt)

AGENTS = ("GPT", "Gemini")
EXCLUDED = {".git", ".agentdesk", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".aws", ".ssh", ".codex", ".gemini"}


def account_document(data):
    """Recognize this application's import format, whatever the user named the JSON."""
    def is_profile(row):
        return (isinstance(row, dict) and {"name", "provider", "model"} <= row.keys()
                and ("api_key" in row or "key_env" in row))
    return (is_profile(data) or isinstance(data, dict) and isinstance(data.get("accounts"), list)
            and any(is_profile(row) for row in data["accounts"]))


def hidden(path):
    if (path.name.startswith(".") or path.name.lower() in EXCLUDED
            or path.suffix.lower() in {".pem", ".key", ".pfx"}
            or path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 1024)):
        return True
    if path.suffix.lower() != ".json" or not path.is_file() or path.stat().st_size > 1024 * 1024:
        return False
    try:
        with path.open(encoding="utf-8-sig") as stream:
            return account_document(json.load(stream))
    except (ValueError, RecursionError):
        return False


def ignored(folder, names):
    return [name for name in names if hidden(Path(folder) / name)]


def files_at(root):
    result = {}
    remaining = 120000
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not hidden(Path(folder) / d))
        for name in sorted(names):
            path = Path(folder) / name
            if hidden(path) or path.stat().st_size > min(48000, remaining):
                continue
            try:
                value = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            if "\0" not in value:
                result[path.relative_to(root).as_posix()] = value
                remaining -= path.stat().st_size
    return result


def clean_path(name):
    if not isinstance(name, str):
        raise ValueError("文件路径必须为字符串")
    parts = name.replace("\\", "/").split("/")
    if any(not p or p.startswith(".") or p.endswith((" ", ".")) or
           re.search(r'[<>:"|?*\x00-\x1f]', p) or (getattr(os.path, "isreserved", lambda x: PureWindowsPath(x).is_reserved())(p))
           or p.lower() in EXCLUDED for p in parts):
        raise ValueError(f"禁止的项目路径 {name}")
    return "/".join(parts)


def parse(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("message"), str):
        raise ValueError("成员返回的 JSON 缺少 message")
    return value


@contextlib.contextmanager
def exclusive(path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class Job:
    def __init__(self, path, state):
        self.path, self.state = Path(path), state
        self.state.setdefault("members", list(AGENTS))
        self.state.setdefault("leader", None)

    @classmethod
    def create(cls, root, goal, checks, members=None, leader=None):
        root = Path(root).resolve(strict=True)
        if not root.is_dir() or not goal.strip():
            raise ValueError("请选择项目目录并填写任务")
        if members is None:
            members = AGENTS
        if (not isinstance(members, (list, tuple)) or len(members) < 2
                or any(not isinstance(name, str) or not name.strip() for name in members)
                or len(set(members)) != len(members)):
            raise ValueError("至少选择两个不同的团队成员")
        if leader is not None and leader not in members:
            raise ValueError("组长必须是已选择的成员")
        for cmd in checks:
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
                raise ValueError("每条验收命令应为非空参数列表")
        parent = root / ".agentdesk" / "jobs"
        if parent.resolve() != parent:
            raise ValueError("任务目录不能指向外部位置")
        path = parent / uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        shutil.copytree(root, path / "baseline", ignore=ignored)
        job = cls(path, dict(root=str(root), goal=goal, checks=checks, status="就绪", round=0,
                             members=list(members), leader=leader, answers={}, messages=[], feedback=""))
        job.save()
        return job

    def save(self):
        temp = self.path / "state.tmp"
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path / "state.json")

    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls(path, json.loads((path / "state.json").read_text(encoding="utf-8")))

    def cancel(self):
        with exclusive(self.path / "run.lock"):
            self.state = self.load(self.path).state
            if self.state["status"] not in {"完成", "待人工验收"}:
                self.state["status"] = "已取消"
                self.save()

    def add_feedback(self, text):
        if not text.strip():
            raise ValueError("请输入补充要求")
        with exclusive(self.path / "run.lock"):
            self.state = self.load(self.path).state
            s = self.state
            if s["status"] in {"完成", "已取消", "待人工验收"}:
                raise ValueError("任务已结束，请创建新任务")
            n = s["round"]
            previous = self.path / f"round-{n}"
            if not previous.exists():
                base = self.path / s.get("base", "baseline")
                if n and (self.path / f"round-{n-1}").exists():
                    base = self.path / f"round-{n-1}"
                shutil.copytree(base, previous, ignore=ignored)
            s["round"] = n + 1
            s["limit"] = n + 4
            s["goal"] += "\n用户补充：" + text.strip()
            s["messages"].append("用户 → 团队：" + text.strip())
            s["status"] = "已暂停"
            self.save()

    def run(self, configs, emit, stop):
        with exclusive(self.path / "run.lock"):
            self.state = self.load(self.path).state
            if self.state["status"] in {"完成", "待人工验收", "已取消"}:
                return self.state["status"]

            def log(line):
                with (self.path / "events.txt").open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                emit(line)

            try:
                self.workflow(configs, log, stop)
            except InterruptedError:
                self.state["status"] = "已暂停"
            except Exception as error:
                self.state["status"] = "受阻"
                log(f"任务受阻：{error}")
            self.save()
            return self.state["status"]

    def workflow(self, configs, log, stop):
        s = self.state
        s["status"] = "运行中"
        self.save()

        def checkpoint():
            if stop.is_set():
                raise InterruptedError

        members = s["members"]
        leader = s["leader"]
        workers = [name for name in members if name != leader]

        def stage_context(root):
            return dict(goal=s["goal"], files=files_at(root), messages=s["messages"][-16:],
                        feedback=s["feedback"], checks=s["checks"], members=members, leader=leader)

        def exchange(stage, root, participants, instruction, personal=None, evidence=None):
            checkpoint()
            batch = s["answers"].setdefault(f"{s['round']}-{stage}", {})
            context = stage_context(root)
            context.update(evidence or {})
            with ThreadPoolExecutor(max_workers=len(participants)) as pool:
                futures = {}
                for agent in participants:
                    if agent in batch:
                        continue
                    ctx = dict(context, assignment=(personal or {}).get(agent))
                    prompt = bounded_prompt(f"你是{agent}。阶段={stage}。{instruction}。只输出 JSON。", ctx)
                    futures[pool.submit(call, agent, configs[agent], prompt)] = agent
                errors = []
                for future in as_completed(futures):
                    agent = futures[future]
                    try:
                        value = parse(future.result())
                        if stage == "派单":
                            assignments = value.get("assignments")
                            if (not isinstance(assignments, dict) or set(assignments) != set(workers)
                                    or any(not isinstance(spec, str) or not spec.strip() for spec in assignments.values())):
                                raise ValueError("assignments 必须为每个执行成员提供一份非空分工说明")
                        if stage in {"互审", "验收复核"} and type(value.get("approved")) is not bool:
                            raise ValueError("审阅结果缺少 approved")
                        if stage == "验收复核":
                            items = value.get("checklist")
                            if (not isinstance(items, list) or not items or
                                    any(not isinstance(item, str) or not item.strip() for item in items)):
                                raise ValueError("复核结果缺少非空 checklist 验收清单")
                        batch[agent] = value
                        recipients = [leader] if leader and agent != leader else [m for m in members if m != agent]
                        message = f"{agent} → {', '.join(recipients)} [{stage}] {value['message']}"
                        s["messages"].append(message)
                        self.save()
                        log(message)
                        if stage == "派单":
                            for worker, spec in value["assignments"].items():
                                log(f"{agent} → {worker} [分工说明] {spec}")
                        if stage == "验收复核":
                            for item in items:
                                log(f"{agent} [验收清单] {item}")
                    except Exception as exc:
                        errors.append(f"{agent}: {exc}")
                if errors:
                    raise RuntimeError(" / ".join(errors))
            checkpoint()
            return batch

        def implement(root, assignments):
            key = f"{s['round']}-实现"
            batch = s["answers"].setdefault(key, {})
            transfers = s.setdefault("handoffs", {}).setdefault(key, {})
            context = stage_context(root)
            errors = []

            def transfer(owner, actor, packet, drafts):
                trail = transfers.setdefault(owner, [])
                trail.append(dict(sender=actor, **packet, files=drafts))
                line = f"{actor} → {packet['to']} [任务交接] {packet['reason']}\n{packet['summary']}"
                s["messages"].append(line)
                self.save()
                log(line)

            # One future per original assignment. Saving transfers in this thread
            # lets a resumed job start with the last recipient and its exact drafts.
            with ThreadPoolExecutor(max_workers=len(workers)) as pool:
                pending = [owner for owner in workers if owner not in batch]
                while pending:
                    checkpoint()
                    futures = {}
                    for owner in pending:
                        trail = transfers.get(owner, [])
                        actor = trail[-1]["to"] if trail else owner
                        visited = [step["sender"] for step in trail]
                        targets = eligible_members(actor, workers + ([leader] if leader else []), visited)
                        drafts = {p: v for step in trail for p, v in step["files"].items()}
                        ctx = dict(context, assignment=assignments[owner], assignment_owner=owner,
                                   handoff_candidates=targets)
                        packet = automatic_handoff(ctx, actor, targets, visited)
                        if packet:
                            transfer(owner, actor, packet, {})
                            visited = visited + [actor]
                            actor = packet["to"]
                            trail = transfers[owner]
                            targets = eligible_members(actor, targets, visited)
                            ctx["handoff_candidates"] = targets
                        if trail:
                            ctx.update(summary=trail[-1]["summary"], received_from=trail[-1]["sender"],
                                       checkpoint_paths=list(drafts), files={**context["files"], **drafts})
                        prompt = bounded_prompt(
                            f'你是{actor}。阶段=实现。按 assignment 完成分工，返回 '
                            '{"message":"交付说明","files":{"相对路径":"完整文件内容"}}。'
                            '只提交改动的文本文件。已交接的草稿会自动保留。' + HANDOFF_INSTRUCTION + '只输出 JSON。',
                            ctx)
                        shown = json.loads(prompt.split("\n", 1)[1])["files"].keys()
                        future = pool.submit(call, actor, configs[actor], prompt)
                        futures[future] = owner, actor, targets, visited, drafts, shown
                    pending = []
                    for future in as_completed(futures):
                        owner, actor, targets, visited, drafts, shown = futures[future]
                        try:
                            reply = parse(future.result())
                            packet = parse_handoff(reply, actor, targets, visited)
                            writes = reply.get("files", {} if packet else None)
                            if not isinstance(writes, dict):
                                raise ValueError("实现结果缺少 files")
                            normalized = {}
                            for name, content in writes.items():
                                path = clean_path(name)
                                if not isinstance(content, str):
                                    raise ValueError(f"文件内容无效 {path}")
                                if (path in context["files"] or path in drafts) and path not in shown:
                                    raise ValueError(f"未完整展示的文件不能覆盖 {path}")
                                normalized[path] = content
                            if packet:
                                transfer(owner, actor, packet, normalized)
                                pending.append(owner)
                            else:
                                batch[owner] = dict(message=reply["message"], files={**drafts, **normalized},
                                                    completed_by=actor)
                                recipients = [leader] if leader and actor != leader else [m for m in members if m != actor]
                                line = f"{actor} → {', '.join(recipients)} [实现] {reply['message']}"
                                s["messages"].append(line)
                                self.save()
                                log(line)
                        except Exception as exc:
                            errors.append(f"{actor}: {exc}")
            if errors:
                raise RuntimeError(" / ".join(errors))
            checkpoint()
            return batch

        def integrate(base, out, replies):
            existing = files_at(base)
            writes = {}
            conflicts = []
            for reply in replies.values():
                for name, content in reply["files"].items():
                    name = clean_path(name)
                    if (base / name).exists() and name not in existing:
                        raise ValueError(f"不能覆盖未提供给成员的文件 {name}")
                    key = name.casefold()
                    if key in writes and writes[key] != (name, content):
                        conflicts.append(name)
                    writes[key] = name, content
            if not writes and not s["round"] and not leader:
                raise ValueError("没有任何成员提交文件")
            if not out.exists():
                stage = self.path / ("stage-" + uuid.uuid4().hex[:8])
                try:
                    shutil.copytree(base, stage, ignore=ignored)
                    if not conflicts:
                        for name, content in writes.values():
                            target = stage / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(content, encoding="utf-8")
                            log(f"集成文件 {name}")
                    stage.rename(out)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
            if conflicts:
                return [], ["双方修改同一文件 " + ", ".join(sorted(set(conflicts))) + "，请协调为仅一人提交。"]
            changed = sorted(name for name, content in writes.values() if existing.get(name) != content)
            if not writes and not s["round"]:
                return [], ["没有任何成员提交文件"]
            return changed, []

        def test(out):
            key = str(s["round"])
            results = s.setdefault("test_results", {})
            if key not in results:
                records = []
                if s["checks"]:
                    check_dir = self.path / ("check-" + uuid.uuid4().hex[:8])
                    try:
                        shutil.copytree(out, check_dir, ignore=ignored)
                        for cmd in s["checks"]:
                            checkpoint()
                            argv = list(cmd)
                            if argv[0].lower() in {"python", "python3", "python.exe"}:
                                argv[0] = str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable
                            try:
                                run = subprocess.run(argv, cwd=check_dir, capture_output=True, timeout=180)
                                output = (run.stdout + run.stderr).decode("utf-8", "replace")
                                passed = run.returncode == 0 and not re.search(r"(?m)^Ran 0 tests? in ", output)
                                record = dict(command=cmd, exit_code=run.returncode, passed=passed, output=output[-5000:])
                            except (OSError, subprocess.TimeoutExpired) as exc:
                                record = dict(command=cmd, exit_code=None, passed=False, output=str(exc))
                            records.append(record)
                            log(f"验收 {cmd[0]}，退出码 {record['exit_code']}\n{record['output']}")
                    finally:
                        if check_dir.exists():
                            shutil.rmtree(check_dir)
                results[key] = records
                self.save()
            return results[key]

        while s["round"] < s.get("limit", 3):
            n = s["round"]
            base = self.path / (f"round-{n-1}" if n and (self.path / f"round-{n-1}").is_dir() else "baseline")
            if leader:
                dispatch = exchange("派单", base, [leader],
                    '你是组长。按用户目标和上一轮反馈分解可并行的工作，为每位执行成员写明交付文件、接口及验收标准。'
                    '返回 {"message":"整体计划","assignments":{"执行成员名":"详细分工"}}', evidence={"workers": workers})
                assignments = dispatch[leader]["assignments"]
            else:
                assignments = {name: ("应用实现与启动入口" if i == 0 else f"测试及独立模块，与其他成员商定文件边界（成员 {i+1}）")
                               for i, name in enumerate(workers)}
                exchange("分工", base, workers, '与同伴商定接口和文件归属，回复 {"message":"..."}', assignments)
            replies = implement(base, assignments)
            out = self.path / f"round-{n}"
            changed, issues = integrate(base, out, replies)
            if leader:
                records = [] if issues else test(out)
                evidence = dict(assignments=assignments, changed_paths=changed, integration_issues=list(issues),
                                worker_results={a: dict(message=r["message"], paths=list(r["files"]),
                                                        completed_by=r.get("completed_by", a)) for a,r in replies.items()},
                                test_results=records)
                review = exchange("验收复核", out, [leader],
                    '你是组长。检查集成代码、每份交付、实际命令结果和原始目标，逐项给出验收结论。'
                    '不符时给出具体修复分工。返回 {"approved":true或false,"message":"结论或修复指令","checklist":["验收项及结果"]}', evidence=evidence)
                decision = review[leader]
                if not decision["approved"] or issues or any(not r["passed"] for r in records):
                    issues.extend([decision["message"], *decision["checklist"]])
            elif not issues:
                review = exchange("互审", out, members, '检查已合并代码，重点审阅同伴交付；返回 {"approved":true或false,"message":"问题或通过原因"}')
                issues.extend(v["message"] for v in review.values() if not v["approved"])
                records = [] if issues else test(out)
            else:
                records = []
            issues.extend(r["output"] or f"退出码 {r['exit_code']}" for r in records if not r["passed"])
            if not issues:
                checkpoint()
                delivery = self.path / "project"
                if not delivery.exists():
                    temp = self.path / ("deliver-" + uuid.uuid4().hex[:8])
                    shutil.copytree(out, temp, ignore=ignored)
                    temp.rename(delivery)
                touched = {clean_path(name) for key, batch in s["answers"].items() if key.endswith("-实现")
                           for reply in batch.values() for name in reply["files"]}
                with (self.path / "changes.diff").open("w", encoding="utf-8") as diff:
                    for name in sorted(touched):
                        original, final = self.path / "baseline" / name, delivery / name
                        if final.is_file():
                            before = original.read_text(encoding="utf-8") if original.is_file() else ""
                            after = final.read_text(encoding="utf-8")
                            diff.writelines(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile="a/" + name, tofile="b/" + name))
                s["status"] = "完成" if s["checks"] else "待人工验收"
                log(f"{s['status']}：{delivery}")
                return
            s["feedback"] = "\n".join(issues)
            log(s["feedback"])
            s["round"] += 1
            self.save()
        raise RuntimeError("三轮后仍有待解决的问题：" + s["feedback"])
