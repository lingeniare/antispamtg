from types import SimpleNamespace

from src.filters.content import (
    extract_hidden_urls,
    has_banned_topic,
    has_link,
    has_url,
    normalize_text,
    verdict_hash,
)


def test_has_link():
    assert has_link("https://example.com")
    assert has_link("www.example.com")
    assert has_link("@username")
    assert not has_link("привет как дела")


def test_has_url_ignores_mentions():
    # probation: @mention друга — не ссылка
    assert has_url("https://casino.ru")
    assert has_url("t.me/joinchat/abc")
    assert not has_url("привет @username")
    assert not has_url("привет как дела")


def test_banned_topic():
    assert has_banned_topic("казино бонус")
    assert has_banned_topic("мефедрон закладки")
    assert has_banned_topic("onlyfans жду")
    assert not has_banned_topic("обычное сообщение")


def test_hidden_urls_text_link():
    # слово-гиперссылка: url живёт в entity, не в тексте
    ents = [SimpleNamespace(type="text_link", url="https://casino.ru/x")]
    assert extract_hidden_urls(ents, None) == ["https://casino.ru/x"]
    # обычная url-entity (видимая в тексте) не дублируется
    ents2 = [SimpleNamespace(type="url", url=None)]
    assert extract_hidden_urls(ents2, None) == []


def test_hidden_urls_buttons():
    btn = SimpleNamespace(url="https://1xbet.fake/go")
    markup = SimpleNamespace(inline_keyboard=[[btn]])
    assert extract_hidden_urls(None, markup) == ["https://1xbet.fake/go"]


def test_verdict_hash_stable():
    assert verdict_hash("Привет,  Мир!") == verdict_hash("привет, мир!")
    assert verdict_hash("a") != verdict_hash("b")


def test_normalize():
    assert normalize_text("  A  B\nC ") == "a b c"
