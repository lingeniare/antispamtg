from src.filters.content import has_link

def test_has_link():
    assert has_link("https://example.com")
    assert has_link("www.example.com")
    assert has_link("@username")
    assert not has_link("привет как дела")

def test_has_link_tme():
    assert has_link("t.me/joinchat/abc")
