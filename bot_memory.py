"""Private bot conversations in the desktop account database."""
from contextlib import contextmanager
from uuid import uuid4


RECENT_TURNS = 50
MEMORY_LIMIT = 4000


def previews(store):
    """Return a single-line excerpt from each bot's latest completed reply."""
    with store._database() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_chat_turns'").fetchone():
            return {}
        rows = db.execute("""SELECT c.bot_id,t.reply FROM bot_chats c JOIN bot_chat_turns t
                             ON t.id=(SELECT last.id FROM bot_chat_turns last WHERE last.chat_id=c.chat_id
                                      ORDER BY last.id DESC LIMIT 1)""")
        return {bot_id: " ".join(reply.split())[:70] for bot_id, reply in rows}


@contextmanager
def _conversation(store, bot_id, profile_id, provider):
    with store._database(write=True) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS bot_chats (
            bot_id TEXT PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
            chat_id TEXT NOT NULL UNIQUE, memory TEXT NOT NULL DEFAULT ''
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS bot_chat_turns (
            id INTEGER PRIMARY KEY,
            chat_id TEXT NOT NULL REFERENCES bot_chats(chat_id) ON DELETE CASCADE,
            question TEXT NOT NULL, reply TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS bot_chat_turn_order ON bot_chat_turns(chat_id, id)")
        db.execute("""CREATE TRIGGER IF NOT EXISTS bot_chats_rebind
            AFTER UPDATE OF profile_id ON bots WHEN OLD.profile_id <> NEW.profile_id
            BEGIN DELETE FROM bot_chats WHERE bot_id = OLD.id; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS bot_chats_provider
            AFTER UPDATE OF provider ON profiles WHEN OLD.provider <> NEW.provider
            BEGIN DELETE FROM bot_chats WHERE bot_id IN
                (SELECT id FROM bots WHERE profile_id = OLD.id); END""")
        if not db.execute("""SELECT 1 FROM bots b JOIN profiles p ON b.profile_id=p.id
                             WHERE b.id=? AND p.id=? AND p.provider=?""",
                          (bot_id, profile_id, provider)).fetchone():
            raise ValueError("Bot 的绑定已改变，请从侧栏重新打开。")
        chat = db.execute("SELECT chat_id, memory FROM bot_chats WHERE bot_id=?", (bot_id,)).fetchone()
        if chat is None:
            chat = (uuid4().hex, "")
            db.execute("INSERT INTO bot_chats(bot_id,chat_id) VALUES(?,?)", (bot_id, chat[0]))
        yield db, chat[0], chat[1]


def load(store, bot_id, profile_id, provider):
    with _conversation(store, bot_id, profile_id, provider) as (db, chat_id, memory):
        rows = db.execute("""SELECT question, reply FROM bot_chat_turns
                             WHERE chat_id=? ORDER BY id""", (chat_id,))
        history = []
        for question, reply in rows:
            history.extend(({"role": "user", "content": question}, {"role": "assistant", "content": reply}))
        return chat_id, memory, history


def _check(current, expected):
    if current != expected:
        raise ValueError("这段对话已在别处清空或重新绑定，请重新打开 Bot。")


def append_turn(store, bot_id, profile_id, provider, expected, question, reply):
    with _conversation(store, bot_id, profile_id, provider) as (db, chat_id, _):
        _check(chat_id, expected)
        db.execute("INSERT INTO bot_chat_turns(chat_id,question,reply) VALUES(?,?,?)",
                   (chat_id, question, reply))
        db.execute("""DELETE FROM bot_chat_turns WHERE chat_id=? AND id <=
                      (SELECT id FROM bot_chat_turns WHERE chat_id=? ORDER BY id DESC LIMIT 1 OFFSET ?)""",
                   (chat_id, chat_id, RECENT_TURNS))


def set_memory(store, bot_id, profile_id, provider, expected, previous, text):
    text = text.strip()
    if len(text) > MEMORY_LIMIT or "\x00" in text:
        raise ValueError("记忆最多 4,000 个字符，不能包含空字符。")
    with _conversation(store, bot_id, profile_id, provider) as (db, chat_id, memory):
        _check(chat_id, expected)
        if previous != memory:
            raise ValueError("记忆已在另一个窗口更改。请重新打开记忆后编辑。")
        db.execute("UPDATE bot_chats SET memory=? WHERE chat_id=?", (text, chat_id))
    return text


def clear_history(store, bot_id, profile_id, provider, expected):
    with _conversation(store, bot_id, profile_id, provider) as (db, chat_id, _):
        _check(chat_id, expected)
        new_id = uuid4().hex
        db.execute("DELETE FROM bot_chat_turns WHERE chat_id=?", (chat_id,))
        db.execute("UPDATE bot_chats SET chat_id=? WHERE bot_id=?", (new_id, bot_id))
    return new_id
