import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_DB_PATH", str(tmp_path / "test.db"))
    from src.storage import db

    monkeypatch.setattr(db, "_db_initialized", False)
    return db


async def test_member_state(tmp_db):
    await tmp_db.init_db()
    assert await tmp_db.record_join(1, 100) is True
    # повторный join — идемпотентно (защита от двойных триггеров)
    assert await tmp_db.record_join(1, 100) is False
    m = await tmp_db.get_member(1, 100)
    assert m["msg_count"] == 0 and m["flagged"] == 0
    await tmp_db.bump_member_msgs(1, 100)
    await tmp_db.flag_member(1, 100)
    m = await tmp_db.get_member(1, 100)
    assert m["msg_count"] == 1 and m["flagged"] == 1


async def test_verdict_cache(tmp_db):
    await tmp_db.init_db()
    assert await tmp_db.verdict_get("h1") is None
    await tmp_db.verdict_set("h1", True, "sex", "porn link")
    v = await tmp_db.verdict_get("h1")
    assert v["spam"] == 1 and v["category"] == "sex"
    await tmp_db.verdict_set("h1", False, "ok", "clean")
    v = await tmp_db.verdict_get("h1")
    assert v["spam"] == 0


async def test_media_verdict_cache(tmp_db):
    await tmp_db.init_db()
    assert await tmp_db.media_verdict_get("uid1") is None
    await tmp_db.media_verdict_set("uid1", True, "sex", "porn image")
    v = await tmp_db.media_verdict_get("uid1")
    assert v["spam"] == 1 and v["reason"] == "porn image"


async def test_violations_count(tmp_db):
    await tmp_db.init_db()
    c24, c3d, level = await tmp_db.add_violation(1, 5, "sex", "x")
    assert c24 == 1 and c3d == 1 and level == 0
    c24, c3d, level = await tmp_db.add_violation(1, 5, "sex", "y")
    assert c24 == 2 and c3d == 2
    await tmp_db.set_mute_level(1, 5, 3, None)
    assert await tmp_db.get_mute_level(1, 5) == 3
