"""Local Bot routines scheduled against the user's wall clock."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import errno
import json
import re
import uuid

from engine import exclusive
from providers import call


FREQUENCIES = {"daily": "每天", "weekdays": "周一至周五"}
ACTIVE = "运行中"
INTERRUPTED = "已中断"
_DUE = """enabled=1 AND eligible_on<=? AND clock<=?
          AND (frequency='daily' OR ?<5)"""


def _eligible(clock, now):
    day = now.date()
    if now.strftime("%H:%M") > clock:
        day += timedelta(days=1)
    return day.isoformat()


def _when(now):
    return now.date().isoformat(), now.strftime("%H:%M"), now.weekday()


class RoutineStore:
    def __init__(self, accounts):
        self.accounts = accounts

    def _schema(self, db):
        db.execute("""CREATE TABLE IF NOT EXISTS routines (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            prompt TEXT NOT NULL, frequency TEXT NOT NULL, clock TEXT NOT NULL, enabled INTEGER NOT NULL,
            eligible_on TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS routine_runs (
            id INTEGER PRIMARY KEY, routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
            date_slot TEXT, started TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '', UNIQUE(routine_id, date_slot))""")

    def save(self, name, bot_id, prompt, frequency, clock, enabled=True, routine_id=None, now=None):
        now = now or datetime.now()
        name, prompt, clock = name.strip(), prompt.strip(), clock.strip()
        if not name.isprintable() or not 1 <= len(name) <= 80:
            raise ValueError("任务名称需要 1 至 80 个可显示字符。")
        if not 1 <= len(prompt) <= 12000 or "\0" in prompt:
            raise ValueError("指令需要 1 至 12,000 个字符。")
        if frequency not in FREQUENCIES or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
            raise ValueError("请选择执行周期并以 HH:MM 填写 24 小时时间，例如 09:30。")
        with self.accounts._database(write=True) as db:
            self._schema(db)
            if not db.execute("SELECT 1 FROM bots WHERE id=?", (bot_id,)).fetchone():
                raise ValueError("请选择一个已保存的 Bot。")
            if routine_id is None:
                routine_id = uuid.uuid4().hex
                db.execute("INSERT INTO routines VALUES (?,?,?,?,?,?,?,?)",
                           (routine_id, name, bot_id, prompt, frequency, clock, bool(enabled), _eligible(clock, now)))
            else:
                if not db.execute("""UPDATE routines SET name=?,bot_id=?,prompt=?,frequency=?,clock=?,enabled=?,
                        eligible_on=CASE WHEN frequency=? AND clock=? AND enabled=? THEN eligible_on ELSE ? END
                        WHERE id=?""", (name, bot_id, prompt, frequency, clock, bool(enabled), frequency, clock,
                                       bool(enabled), _eligible(clock, now), routine_id)).rowcount:
                    raise ValueError("此例行任务已删除。")
        return routine_id

    def toggle(self, routine_id, now=None):
        with self.accounts._database(write=True) as db:
            row = db.execute("SELECT clock FROM routines WHERE id=?", (routine_id,)).fetchone()
            if not row:
                raise ValueError("此例行任务已删除。")
            db.execute("UPDATE routines SET enabled=1-enabled,eligible_on=? WHERE id=?",
                       (_eligible(row["clock"], now or datetime.now()), routine_id))

    def delete(self, routine_id):
        with self.accounts._database(write=True) as db:
            db.execute("DELETE FROM routines WHERE id=?", (routine_id,))

    def all(self):
        with self.accounts._database(timeout=0.05) as db:
            self._schema(db)
            return [dict(r) for r in db.execute("""SELECT r.*,b.name AS bot_name FROM routines r
                    JOIN bots b ON b.id=r.bot_id ORDER BY r.clock,r.name,r.id""")]

    def history(self, routine_id):
        with self.accounts._database(timeout=0.05) as db:
            return [dict(r) for r in db.execute("""SELECT id,started,trigger,status FROM routine_runs
                    WHERE routine_id=? ORDER BY id DESC""", (routine_id,))]

    def result(self, run_id):
        with self.accounts._database(timeout=0.05) as db:
            row = db.execute("SELECT result FROM routine_runs WHERE id=?", (run_id,)).fetchone()
            return row[0] if row else ""

    def needs_tick(self, now=None):
        if not self.accounts.path.is_file():
            return False
        with self.accounts._database() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='routines'").fetchone():
                return False
            if db.execute("SELECT 1 FROM routine_runs WHERE status=? LIMIT 1", (ACTIVE,)).fetchone():
                return True
            return bool(db.execute("SELECT 1 FROM routines WHERE " + _DUE + " LIMIT 1",
                                   _when(now or datetime.now())).fetchone())

    def _claim(self, routine_id, now):
        # The OS lock is held through the model call. Its release on process exit
        # lets the next owner distinguish an abandoned run from a slow provider.
        with self.accounts._database(write=True) as db:
            self._schema(db)
            db.execute("UPDATE routine_runs SET status=?,result=? WHERE status=?",
                       (INTERRUPTED, "上次运行未返回结果。可点「立即运行」重试。", ACTIVE))
            if routine_id is None:
                row = db.execute("SELECT * FROM routines WHERE " + _DUE + " ORDER BY clock,id LIMIT 1",
                                 _when(now)).fetchone()
            else:
                row = db.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
                if not row:
                    raise ValueError("此例行任务已删除。")
            if not row:
                return None
            day = now.date()
            due = (row["enabled"] and row["eligible_on"] <= day.isoformat() and row["clock"] <= now.strftime("%H:%M")
                   and (row["frequency"] == "daily" or now.weekday() < 5))
            date_slot = day.isoformat() if due else None
            if due:
                db.execute("UPDATE routines SET eligible_on=? WHERE id=?",
                           ((day + timedelta(days=1)).isoformat(), row["id"]))
                if db.execute("SELECT 1 FROM routine_runs WHERE routine_id=? AND date_slot=?",
                              (row["id"], date_slot)).fetchone():
                    if routine_id is None:
                        return None
                    date_slot = None
            run_id = db.execute("""INSERT INTO routine_runs(routine_id,date_slot,started,trigger,status)
                                 VALUES(?,?,?,?,?)""",
                                (row["id"], date_slot, now.isoformat(timespec="seconds"),
                                 "手动" if routine_id else "定时", ACTIVE)).lastrowid
            bot = db.execute("SELECT name,instructions,profile_id FROM bots WHERE id=?", (row["bot_id"],)).fetchone()
            return run_id, dict(bot), row["prompt"]

    def execute(self, routine_id=None, now=None, stop=None):
        now = now or datetime.now()
        # One run per desktop account at once, including across application windows.
        lock_path = self.accounts.path.with_suffix(".routines.lock")
        with ExitStack() as stack:
            try:
                stack.enter_context(exclusive(lock_path))
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN) or not lock_path.is_file():
                    raise
                if routine_id is not None:
                    raise ValueError("另一项例行任务正在运行，请在结束后重试。") from None
                return None
            claimed = self._claim(routine_id, now)
            if not claimed:
                return None
            run_id, bot, instruction = claimed
            key = ""
            try:
                if stop and stop.is_set():
                    raise InterruptedError()
                cfg = self.accounts.resolve(bot["profile_id"])
                key = cfg["key"]
                prompt = ("你正在执行一个本地 Bot 的例行任务。请依据职责和本次指令直接回复文本。"
                          "此任务只提供下列资料。\n" +
                          json.dumps({"bot": bot["name"], "responsibilities": bot["instructions"],
                                      "local_time": now.isoformat(timespec="minutes"), "request": instruction},
                                     ensure_ascii=False))
                answer = call(cfg["provider"], cfg, prompt)
                status = "完成"
            except InterruptedError:
                status, answer = INTERRUPTED, "工作台已关闭，可点「立即运行」重试。"
            except Exception as exc:
                status, answer = "失败", str(exc)[:1200]
            if key:
                answer = answer.replace(key, "〔密钥已隐藏〕")
            with self.accounts._database(write=True) as db:
                db.execute("UPDATE routine_runs SET status=?,result=? WHERE id=? AND status=?",
                           (status, answer, run_id, ACTIVE))
            return run_id
