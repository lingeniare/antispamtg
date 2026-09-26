from src.ai.vega_client import heuristic_spam, strict_fallback


def test_heuristic_link_plus_topic():
    assert heuristic_spam("заходи на casino.ru бонус") == (True, "heuristic: link + banned topic")
    assert heuristic_spam("просто текст") is None
    # ссылка без запрещённой темы — решает LLM
    assert heuristic_spam("мой сайт example.ru, посмотри") is None


def test_strict_fallback_link():
    r = strict_fallback("привет site.ru")
    assert r and r["spam"] is True and r["via"] == "strict"


def test_strict_fallback_forward_topic():
    assert strict_fallback("казино бонус", is_forward=True)["spam"] is True
    assert strict_fallback("казино бонус", is_forward=False) is None


def test_strict_fallback_clean():
    assert strict_fallback("обычный текст") is None
    assert strict_fallback("") is None
