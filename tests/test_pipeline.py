"""Интеграционные тесты пайплайна модерации — прогон use cases злоумышленника:
порно-картинка без подписи, «смотри профиль», скрытые ссылки, рейт-лимит,
кэш вердиктов, strict-fallback при падении LLM, скан профиля."""

import time
from types import SimpleNamespace

import pytest

from src.bot import handlers


class FakeBot:
    token = "TESTTOKEN"

    def __init__(self):
        self.restricted: list[int] = []

    async def get_chat_administrators(self, chat_id):
        return []

    async def get_file(self, fid):
        return SimpleNamespace(file_path="photos/file_1.jpg")

    async def get_chat(self, uid):
        return SimpleNamespace(bio="")

    async def send_message(self, *a, **k):
        return SimpleNamespace(delete=lambda: None)

    async def restrict_chat_member(self, chat_id, uid, permissions, until_date=None):
        self.restricted.append(uid)

    async def ban_chat_member(self, *a):
        pass

    async def unban_chat_member(self, *a):
        pass


def make_msg(bot, **kw):
    base = dict(  # noqa: C408 - kwargs-форма удобнее для заглушки сообщения
        chat=SimpleNamespace(id=-100999, type="supergroup", username=None),
        from_user=SimpleNamespace(
            id=1000 + int(time.time() * 1000) % 1000000,
            full_name="Spammer",
            is_bot=False,
            username="spammy",
        ),
        message_id=1,
        text=None,
        caption=None,
        photo=None,
        sticker=None,
        animation=None,
        video=None,
        video_note=None,
        document=None,
        entities=None,
        caption_entities=None,
        reply_markup=None,
        forward_origin=None,
        sender_chat=None,
        bot=bot,
        new_chat_members=None,
    )
    base.update(kw)
    m = SimpleNamespace(**base)

    async def _delete():
        m.deleted = True

    m.delete = _delete
    return m


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_DB_PATH", str(tmp_path / "t.db"))
    # изолируемся от реального config.yaml/.env — тестовый чат должен быть разрешён
    monkeypatch.setenv("ALLOWED_CHATS", "-100999")
    monkeypatch.setenv("WHITELIST_USERS", "")
    monkeypatch.setenv("ADMIN_USER_IDS", "")
    monkeypatch.setenv("BAN_ON_REPEAT_SPAM", "false")
    from src import config

    config.clear_settings_cache()
    from src.storage import db

    monkeypatch.setattr(db, "_db_initialized", False)
    return db


def spy_llm(monkeypatch, spam=True, calls=None):
    async def fake(text, image_url=None, is_forward=False, model=None, context=None):
        if calls is not None:
            calls.append({"text": text, "image_url": image_url, "model": model})
        if isinstance(spam, dict):
            return spam
        return {"spam": spam, "reason": "test verdict", "category": "sex", "via": "llm"}

    monkeypatch.setattr(handlers, "ai_is_spam", fake)


async def _old_member(db, chat_id, uid):
    """Юзер давно в чате и с кучей сообщений — probation не действует."""
    await db.init_db()
    await db.record_join(chat_id, uid, ts=1)
    async with db.aiosqlite.connect(db._db()) as d:
        await d.execute(
            "UPDATE member_state SET joined_ts=0, msg_count=99 WHERE chat_id=? AND user_id=?",
            (chat_id, uid),
        )
        await d.commit()


async def test_porn_photo_no_caption_new_user(env, monkeypatch):
    """Порно-картинка без подписи от нового юзера → vision-вызов → спам."""
    calls = []
    spy_llm(monkeypatch, spam=True, calls=calls)
    monkeypatch.setattr(handlers, "resolve_image", lambda bot, m: _async(("http://img", "uidX")))
    await env.init_db()
    bot = FakeBot()
    photo = [SimpleNamespace(file_id="f1", file_unique_id="uidX", file_size=1000)]
    m = make_msg(bot, photo=photo, caption=None)
    res = await handlers._moderate(m)
    assert res["spam"] is True
    assert calls[0]["image_url"] == "http://img"
    # повтор того же файла — из кэша, без LLM
    res2 = await handlers._moderate(m)
    assert res2["spam"] is True and res2["via"] == "media_cache"
    assert len(calls) == 1


async def _async(v):
    return v


async def test_hidden_link_new_user_no_llm(env, monkeypatch):
    """Скрытая ссылка (text_link) от нового юзера → спам БЕЗ вызова LLM."""
    calls = []
    spy_llm(monkeypatch, spam=True, calls=calls)
    await env.init_db()
    bot = FakeBot()
    ents = [SimpleNamespace(type="text_link", url="https://casino.fake")]
    m = make_msg(bot, text="нажми сюда", entities=ents)
    res = await handlers._moderate(m)
    assert res["spam"] is True and res["via"] == "probation"
    assert calls == []  # LLM не вызывался


async def test_hidden_link_old_user_goes_to_llm(env, monkeypatch):
    """Та же скрытая ссылка от старого юзера → LLM видит [LINK]."""
    calls = []
    spy_llm(monkeypatch, spam=True, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, text="нажми сюда", entities=[SimpleNamespace(type="text_link", url="https://x.ru")])
    await _old_member(env, m.chat.id, m.from_user.id)
    res = await handlers._moderate(m)
    assert res["spam"] is True
    assert "[LINK] https://x.ru" in calls[0]["text"]


async def test_clean_text_cached(env, monkeypatch):
    """Одинаковый текст не жжёт второй LLM-вызов."""
    calls = []
    spy_llm(monkeypatch, spam=False, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, text="всем привет из чата")
    await _old_member(env, m.chat.id, m.from_user.id)
    r1 = await handlers._moderate(m)
    assert r1["spam"] is False
    r2 = await handlers._moderate(m)
    assert r2["via"] == "cache"
    assert len(calls) == 1


async def test_rate_limit_flood(env, monkeypatch):
    """7 сообщений за окно → flood без LLM."""
    calls = []
    spy_llm(monkeypatch, spam=False, calls=calls)
    bot = FakeBot()
    m = make_msg(bot, text="ok")
    await _old_member(env, m.chat.id, m.from_user.id)
    for _ in range(7):
        res = await handlers._moderate(m)
    assert res["spam"] is True and res["category"] == "flood"


async def test_llm_error_strict_fallback(env, monkeypatch):
    """Падение LLM → strict-fallback: ссылка всё равно удаляется (нет чистого fail-open)."""
    spy_llm(monkeypatch, spam={"spam": False, "error": True, "reason": "down", "category": "error"})
    bot = FakeBot()
    m = make_msg(bot, text="заходи на evil-site.ru")
    await _old_member(env, m.chat.id, m.from_user.id)
    res = await handlers._moderate(m)
    assert res["spam"] is True and res["via"] == "strict"


async def test_profile_bait_muted(env, monkeypatch):
    """«Смотри профиль»-наживка: ссылка+запретка в bio → перманентный мьют при входе."""
    await env.init_db()
    bot = FakeBot()

    async def bio_chat(uid):
        return SimpleNamespace(bio="onlyfans 🔞 t.me/xxx")

    bot.get_chat = bio_chat
    user = SimpleNamespace(id=555777, full_name="Sveta", username="sveta_x", is_bot=False)
    await handlers._scan_profile(bot, -100999, user)
    assert 555777 in bot.restricted


async def test_process_deletes_and_mutes(env, monkeypatch):
    """Полный прогон _process: спам → delete + перманентный мьют."""
    spy_llm(monkeypatch, spam=True)
    await env.init_db()
    bot = FakeBot()
    m = make_msg(bot, text="free money casino.ru now")
    await _old_member(env, m.chat.id, m.from_user.id)
    await handlers._process(m)
    assert getattr(m, "deleted", False) is True
    assert m.from_user.id in bot.restricted
