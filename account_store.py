"""User-owned API profiles for the desktop agent. Secrets stay outside project directories."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

from providers import ENV_KEYS


PROVIDERS = tuple(ENV_KEYS)
IMPORT_BYTES = 1024 * 1024
IMPORT_LIMIT = 100
_FIELDS = {"name", "provider", "model", "api_key", "key_env"}


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("在此系统上请使用 key_env 引用本机环境变量。保存密钥需要 Windows 当前用户的数据保护服务。")
    crypt = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    free = ctypes.WinDLL("kernel32.dll", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p if decrypt else ctypes.c_wchar_p,
                   ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_uint32, ctypes.POINTER(_Blob)]
    fn.restype = ctypes.c_int
    memory = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(memory, ctypes.c_void_p))
    target = _Blob()
    try:
        if not fn(ctypes.byref(source), None if decrypt else "Agents Bot API key", None,
                  None, None, 1, ctypes.byref(target)):
            raise ValueError("无法使用 Windows 当前用户保护或读取密钥，请重新保存该档案，或选择环境变量。")
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(memory, 0, len(memory))
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            free(target.data)


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{1,4096}", value):
        raise ValueError("API Key 必须由可见 ASCII 字符组成，不能含空格或换行。")
    return value


def _metadata(name, provider, model, key_env):
    if not all(isinstance(v, str) for v in (name, provider, model, key_env)):
        raise ValueError("档案名称、提供商、模型 ID 和环境变量名需要使用字符串。")
    name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(name) <= 80 or not name.isprintable():
        raise ValueError("请输入 1 至 80 个可显示字符作为档案名称。")
    if provider not in PROVIDERS:
        raise ValueError("请选择支持的模型提供商。")
    model = model.strip()
    if provider == "Gemini":
        model = model.removeprefix("models/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}", model):
        raise ValueError("请输入有效的 API 模型 ID。")
    key_env = key_env.strip()
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key_env):
        raise ValueError("环境变量名只能包含字母、数字和下划线，且不能以数字开头。")
    return name, provider, model, key_env


def _unique_fields(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("导入文件含重复的 JSON 字段。")
        obj[key] = value
    return obj


class AccountStore:
    def __init__(self, path=None):
        if path is None:
            folder = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
            path = folder / "AgentsBot" / "accounts.db"
        self.path = Path(path)

    @contextmanager
    def _database(self, write=False):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_fold TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, key_env TEXT NOT NULL,
                    protected_key BLOB, UNIQUE(provider, name_fold),
                    CHECK((protected_key IS NULL) = (key_env <> ''))
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_fold TEXT NOT NULL UNIQUE,
                    instructions TEXT NOT NULL,
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT
                )""")
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def list_profiles(self):
        with self._database() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,name,provider,model,key_env FROM profiles ORDER BY provider,name_fold,id")]

    @staticmethod
    def _row(db, profile_id):
        record = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if record is None:
            raise ValueError("档案不存在，请刷新账号列表。")
        return record

    def _save(self, db, name, provider, model, api_key="", key_env="", profile_id=None):
        name, provider, model, key_env = _metadata(name, provider, model, key_env)
        if not isinstance(api_key, str):
            raise ValueError("API Key 需要使用字符串。")
        if api_key and key_env:
            raise ValueError("API Key 与 key_env 请选择其中一种。")
        existing = self._row(db, profile_id) if profile_id is not None else None
        protected = None
        if api_key:
            protected = _dpapi(_key(api_key).encode("ascii"))
        elif not key_env:
            if existing is None or provider != existing["provider"]:
                raise ValueError("为新档案或不同的提供商指定 API Key 或环境变量。")
            protected, key_env = existing["protected_key"], existing["key_env"]
        profile_id = profile_id if existing is not None else uuid.uuid4().hex
        try:
            db.execute("""INSERT INTO profiles (id,name,name_fold,provider,model,key_env,protected_key)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, name_fold=excluded.name_fold,
                    provider=excluded.provider, model=excluded.model, key_env=excluded.key_env,
                    protected_key=excluded.protected_key""",
                (profile_id, name, name.casefold(), provider, model, key_env, protected))
        except sqlite3.IntegrityError:
            raise ValueError("该提供商下已有同名档案。请改名，或编辑已有档案。") from None
        return profile_id

    def save_profile(self, name, provider, model, api_key="", key_env="", profile_id=None):
        with self._database(write=True) as db:
            return self._save(db, name, provider, model, api_key, key_env, profile_id)

    def delete_profile(self, profile_id):
        with self._database(write=True) as db:
            if db.execute("SELECT 1 FROM bots WHERE profile_id=? LIMIT 1", (profile_id,)).fetchone():
                raise ValueError("有 Bot 正在使用此账号，请先为它们更换账号或删除对应 Bot。")
            if not db.execute("DELETE FROM profiles WHERE id=?", (profile_id,)).rowcount:
                raise ValueError("档案不存在，请刷新账号列表。")

    def list_bots(self):
        with self._database() as db:
            return [dict(row) for row in db.execute("""
                SELECT b.id, b.name, b.instructions, b.profile_id,
                       p.name AS profile_name, p.provider, p.model
                FROM bots AS b JOIN profiles AS p ON p.id = b.profile_id
                ORDER BY b.name_fold, b.id""")]

    def save_bot(self, name, instructions, profile_id, bot_id=None):
        if not isinstance(name, str):
            raise ValueError("请输入 Bot 名称。")
        name = unicodedata.normalize("NFC", name.strip())
        if not 1 <= len(name) <= 80 or not name.isprintable():
            raise ValueError("Bot 名称须为 1 至 80 个可显示字符。")
        if not isinstance(instructions, str) or len(instructions) > 4000 or "\0" in instructions:
            raise ValueError("Bot 职责须为最多 4000 字符的文本。")
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("请选择一个 API 账号。")
        values = (name, name.casefold(), instructions.strip(), profile_id)
        with self._database(write=True) as db:
            if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise ValueError("所选 API 账号不存在，请刷新账号列表。")
            try:
                if bot_id is None:
                    bot_id = uuid.uuid4().hex
                    db.execute("INSERT INTO bots(name,name_fold,instructions,profile_id,id) VALUES (?,?,?,?,?)",
                               (*values, bot_id))
                elif not db.execute("UPDATE bots SET name=?, name_fold=?, instructions=?, profile_id=? WHERE id=?",
                                    (*values, bot_id)).rowcount:
                    raise ValueError("Bot 不存在，请刷新列表。")
            except sqlite3.IntegrityError:
                raise ValueError("已有同名 Bot，请使用不同的名称。") from None
        return bot_id

    def delete_bot(self, bot_id):
        with self._database(write=True) as db:
            if not db.execute("DELETE FROM bots WHERE id=?", (bot_id,)).rowcount:
                raise ValueError("Bot 不存在，请刷新列表。")

    def resolve(self, profile_id):
        with self._database() as db:
            row = self._row(db, profile_id)
        if row["key_env"]:
            key = os.environ.get(row["key_env"])
            if not key:
                raise ValueError("此档案引用的环境变量为空，请设置后重新打开工作台。")
        else:
            key = _dpapi(row["protected_key"], decrypt=True).decode("ascii")
        return dict(provider=row["provider"], model=row["model"], key=_key(key), mode="api")

    def import_file(self, path):
        with Path(path).open("rb") as stream:
            raw = stream.read(IMPORT_BYTES + 1)
        if len(raw) > IMPORT_BYTES:
            raise ValueError("导入文件超过 1 MiB。")
        try:
            obj = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise ValueError("请选择有效的 UTF-8 JSON 文件。") from None
        if isinstance(obj, dict) and ("version" in obj or "accounts" in obj):
            if set(obj) != {"version", "accounts"} or type(obj["version"]) is not int or obj["version"] != 1:
                raise ValueError("导入格式需要 version 为 1，并包含 accounts 数组。")
            records = obj["accounts"]
        else:
            records = [obj]
        if not isinstance(records, list) or not 1 <= len(records) <= IMPORT_LIMIT:
            raise ValueError("每次可导入 1 至 100 个 API 账号。")
        ids = []
        with self._database(write=True) as db:
            for i, record in enumerate(records, 1):
                if not isinstance(record, dict) or not {"name", "provider", "model"} <= record.keys() <= _FIELDS:
                    raise ValueError(f"第 {i} 项字段无效。需要 name、provider、model 以及 api_key 或 key_env。")
                try:
                    ids.append(self._save(db, **record))
                except ValueError as exc:
                    raise ValueError(f"第 {i} 项：{exc}") from None
        return ids
