import io
from types import SimpleNamespace

from src.ai.media import _pick_file, resolve_image


def _msg(**kw):
    base = {
        "photo": None, "sticker": None, "animation": None, "video": None,
        "video_note": None, "document": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_pick_photo_largest():
    m = _msg(
        photo=[
            SimpleNamespace(file_id="small", file_unique_id="u1", file_size=100),
            SimpleNamespace(file_id="big", file_unique_id="u2", file_size=5000),
        ]
    )
    fid, uid, size = _pick_file(m)
    assert fid == "big" and uid == "u2"


def test_pick_static_sticker():
    st = SimpleNamespace(
        file_id="st", file_unique_id="su", file_size=10, is_video=False, is_animated=False
    )
    assert _pick_file(_msg(sticker=st)) == ("st", "su", 10)


def test_pick_animated_sticker_uses_thumbnail():
    st = SimpleNamespace(
        file_id="st", file_unique_id="su", file_size=10, is_video=False, is_animated=True,
        thumbnail=SimpleNamespace(file_id="th", file_unique_id="tu", file_size=5),
    )
    assert _pick_file(_msg(sticker=st)) == ("th", "tu", 5)


def test_pick_video_thumbnail():
    v = SimpleNamespace(
        file_id="v", file_unique_id="vu", file_size=999,
        thumbnail=SimpleNamespace(file_id="th", file_unique_id="tu", file_size=5),
    )
    assert _pick_file(_msg(video=v)) == ("th", "tu", 5)


def test_pick_document_image():
    d = SimpleNamespace(file_id="d", file_unique_id="du", file_size=10, mime_type="image/png")
    assert _pick_file(_msg(document=d)) == ("d", "du", 10)


def test_pick_nothing():
    assert _pick_file(_msg()) == (None, None, 0)


async def test_resolve_returns_data_uri_no_token_leak():
    class B:
        token = "SECRET_TOKEN"

        async def get_file(self, fid):
            return SimpleNamespace(file_path="photos/x.jpg")

        async def download(self, fid):
            return io.BytesIO(b"\xff\xd8img")

    m = _msg(photo=[SimpleNamespace(file_id="f", file_unique_id="u", file_size=3)])
    url, uid = await resolve_image(B(), m)
    assert url.startswith("data:image/jpeg;base64,")
    assert "SECRET_TOKEN" not in url
    assert uid == "u"
