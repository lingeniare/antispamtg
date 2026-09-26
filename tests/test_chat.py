"""Тесты разговорной личности ВЕГА: триггеры, [SILENT], ЛС только для своих,
спам не получает ответа."""

import time
from types import SimpleNamespace

import pytest

from src.bot import handlers


class FakeBot:
    token = "TESTTOKEN"
    id = 9999

    def __init__(self):
        self.restricted = []

    async def get_chat_administrators(self, chat_id):
        return []

    async def get_me(self):
        return SimpleNamespace(id=9999, username="spamvega_bot")

    async def get_file(self, fid):
        return SimpleNamespace(file_path="photos/x.jpg")

    async def download(self, fid):
        import io

        return io.BytesIO(b"img")

    async def send_message(self, *a, **k):
        return SimpleNamespace(delete=lambda: None)

    async def restrict_chat_member(self, *a, **k):
        pass

    async def get_chat(self, uid):
        return SimpleNamespace(bio="")


def make_msg(bot, uid=None, chat_type="supergroup", **kw):
    base = {
        "chat": SimpleNamespace(id=-100999, type=chat_type, username=None),
        "from_user": SimpleNamespace(
            id=uid or 1000 + int(time.time() * 1000) % 1000000,
            full_name="User",
            is_bot=False,
            username="userx",
        ),
        "message_id": 1,
        "text": None,
        "caption": None,
        "photo": None,
        "sticker": None,
        "animation": None,
        "video": None,
        "video_note": None,
        "document": None,
        "entities": None,
        "caption_entities": None,
        "reply_markup": None,
        "reply_to_message": None,
        "forward_origin": None,
        "sender_chat": None,
        "bot": bot,
        "new_chat_members": None,
    }
    base.update(kw)
    m = SimpleNamespace(**base)
    m.replies = []

    async def _reply(t):
        m.replies.append(t)

    async def _delete():
        m.deleted = True

    m.reply = _reply
    m.answer = _reply
    m.delete = _delete
    return m


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ALLOWED_CHATS", "-100999")
    monkeypatch.setenv("WHITELIST_USERS", "")
    monkeypatch.setenv("ADMIN_USER_IDS", "")
    monkeypatch.setenv("BAN_ON_REPEAT_SPAM", "false")
    from src import config

    config.clear_settings_cache()
    from src.storage import db

    monkeypatch.setattr(db, "_db_initialized", False)
    return db


async def _old_member(db, chat_id, uid):
    await db.init_db()
    await db.record_join(chat_id, uid, ts=1)
    async with db.aiosqlite.connect(db._db()) as d:
        await d.execute(
            "UPDATE member_state SET joined_ts=0, msg_count=99 WHERE chat_id=? AND user_id=?",
            (chat_id, uid),
        )
        await d.commit()


def _llm(monkeypatch, spam=False, chat="Привет от ВЕГИ!", calls=None):
    async def fake_spam(text, **kw):
        return {"spam": spam, "reason": "t", "category": "sex", "via": "llm"}

    async def fake_chat(prompt, content, model, max_tokens, web_search=False):
        if calls is not None:
            calls.append(content)
        return chat

    monkeypatch.setattr(handlers, "ai_is_spam", fake_spam)
    monkeypatch.setattr(handlers, "ai_chat_reply", fake_chat)


async def test_chat_on_name(env, monkeypatch):
    """«привет вега» в группе → чистая модерация → ВЕГА отвечает."""
    _llm(monkeypatch)
    bot = FakeBot()
    m = make_msg(bot, text="привет вега, как дела")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert m.replies == ["Привет от ВЕГИ!"]


async def test_chat_on_mention(env, monkeypatch):
    _llm(monkeypatch)
    bot = FakeBot()
    m = make_msg(bot, text="@spamvega_bot расскажи анекдот")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert len(m.replies) == 1


async def test_chat_reply_to_bot(env, monkeypatch):
    """Ответ на сообщение бота → триггер."""
    _llm(monkeypatch)
    bot = FakeBot()
    rep = SimpleNamespace(from_user=SimpleNamespace(id=9999), text="привет", caption=None)
    m = make_msg(bot, text="и тебе привет", reply_to_message=rep)
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert len(m.replies) == 1


async def test_chat_silent(env, monkeypatch):
    """Модель решила промолчать ([SILENT] → None) → ответа нет."""
    _llm(monkeypatch, chat=None)
    bot = FakeBot()
    m = make_msg(bot, text="вега")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert m.replies == []


async def test_no_trigger_no_chat(env, monkeypatch):
    """Обычное сообщение без обращения — ВЕГА молчит, LLM-чат не вызывается."""
    calls = []
    _llm(monkeypatch, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, text="обычный разговор людей")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert calls == [] and m.replies == []


async def test_spam_no_chat(env, monkeypatch):
    """Спам не получает ответа — сначала модерация."""
    calls = []
    _llm(monkeypatch, spam=True, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, text="вега заходи на casino.ru")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert calls == [] and m.replies == []
    assert getattr(m, "deleted", False) is True


async def test_private_admin_gets_reply(env, monkeypatch):
    _llm(monkeypatch)
    bot = FakeBot()
    m = make_msg(bot, uid=42, chat_type="private", text="привет")
    monkeypatch.setenv("ADMIN_USER_IDS", "42")
    from src import config

    config.clear_settings_cache()
    await handlers._process(m)
    assert len(m.replies) == 1


async def test_private_stranger_refused(env, monkeypatch):
    """Чужой в ЛС — отказ «общаюсь только с создателями», LLM не зовётся."""
    calls = []
    _llm(monkeypatch, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, chat_type="private", text="привет")
    await handlers._process(m)
    assert calls == []
    assert len(m.replies) == 1 and "создателями" in m.replies[0]


async def test_private_whitelist_not_admin_refused(env, monkeypatch):
    """Whitelist ≠ создатель: в ЛС тоже получает отказ."""
    calls = []
    _llm(monkeypatch, calls=calls)
    monkeypatch.setenv("WHITELIST_USERS", "555")
    from src import config

    config.clear_settings_cache()
    bot = FakeBot()
    m = make_msg(bot, uid=555, chat_type="private", text="привет")
    await handlers._process(m)
    assert calls == []
    assert len(m.replies) == 1 and "создателями" in m.replies[0]


async def test_ambient_free_will(env, monkeypatch):
    """CHAT_AMBIENT_PCT=100: без триггера модель ВСЁ РАВНО видит сообщение
    и сама решает ответить — свобода воли."""
    calls = []
    _llm(monkeypatch, calls=calls)
    monkeypatch.setenv("CHAT_AMBIENT_PCT", "100")
    from src import config

    config.clear_settings_cache()
    bot = FakeBot()
    m = make_msg(bot, text="обычный разговор без обращения")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert len(calls) == 1  # увидела сообщение
    assert m.replies == ["Привет от ВЕГИ!"]  # и решила ответить


async def test_whitelist_user_gets_reply(env, monkeypatch):
    """Whitelist-юзер в группе: модерацию пропускает, но ВЕГА ему отвечает."""
    calls = []
    _llm(monkeypatch, calls=calls)
    monkeypatch.setenv("WHITELIST_USERS", "777")
    from src import config

    config.clear_settings_cache()
    bot = FakeBot()
    m = make_msg(bot, uid=777, text="вега, привет")
    await handlers._process(m)
    assert len(calls) == 1 and m.replies == ["Привет от ВЕГИ!"]


async def test_chat_admin_gets_reply(env, monkeypatch):
    """Админ чата: без модерации, но с разговором."""
    calls = []
    _llm(monkeypatch, calls=calls)
    bot = FakeBot()

    uid = 4242
    handlers._admins_cache.clear()  # модульный кэш между тестами

    async def admins(chat_id):
        return [SimpleNamespace(user=SimpleNamespace(id=uid))]

    bot.get_chat_administrators = admins
    m = make_msg(bot, uid=uid, text="вега, ты тут?")
    await handlers._process(m)
    assert len(calls) == 1 and len(m.replies) == 1


async def test_ambient_zero_no_call(env, monkeypatch):
    """При pct=0 (дефолт) ambient не работает — только триггеры."""
    calls = []
    _llm(monkeypatch, calls=calls)
    monkeypatch.setenv("CHAT_AMBIENT_PCT", "0")
    from src import config

    config.clear_settings_cache()
    bot = FakeBot()
    m = make_msg(bot, text="обычный разговор")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert calls == []
