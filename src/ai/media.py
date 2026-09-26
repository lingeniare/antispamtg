from __future__ import annotations

import base64
import logging
from typing import Any

log = logging.getLogger("tg-antispam")

# лимит файла для vision — и лимит скачивания ботов Telegram ~20MB
_MAX_FILE_BYTES = 20 * 1024 * 1024


def _pick_file(m: Any) -> tuple[str | None, str | None, int]:
    """Выбирает (file_id, file_unique_id, size) лучшего изображения сообщения.

    - photo: максимальный размер
    - sticker: статичный .webp (tgs/webm не умеем — для них thumbnail)
    - animation/video/video_note: thumbnail
    - document: сам файл для image/*, иначе thumbnail
    """
    photo = getattr(m, "photo", None)
    if photo:
        p = photo[-1]
        return p.file_id, getattr(p, "file_unique_id", None), getattr(p, "file_size", 0) or 0

    sticker = getattr(m, "sticker", None)
    if sticker:
        if not sticker.is_video and not sticker.is_animated:
            return sticker.file_id, sticker.file_unique_id, sticker.file_size or 0
        thumb = getattr(sticker, "thumbnail", None)
        if thumb:
            return thumb.file_id, getattr(thumb, "file_unique_id", None), thumb.file_size or 0
        return None, None, 0

    for attr in ("animation", "video", "video_note"):
        obj = getattr(m, attr, None)
        if obj:
            thumb = getattr(obj, "thumbnail", None)
            if thumb:
                return thumb.file_id, getattr(thumb, "file_unique_id", None), thumb.file_size or 0
            return None, None, 0

    doc = getattr(m, "document", None)
    if doc:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        if mime.startswith("image/"):
            return doc.file_id, getattr(doc, "file_unique_id", None), doc.file_size or 0
        thumb = getattr(doc, "thumbnail", None)
        if thumb:
            return thumb.file_id, getattr(thumb, "file_unique_id", None), thumb.file_size or 0
        return None, None, 0

    return None, None, 0


def _mime_of(file_path: str) -> str:
    ext = (file_path or "").rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/jpeg")


async def resolve_image(bot: Any, m: Any) -> tuple[str | None, str | None]:
    """Возвращает (image_url, file_unique_id) для vision-модерации.

    Качает файл через bot.download и отдаёт data-URI — нельзя отдавать
    https://api.telegram.org/file/bot<TOKEN>/... в сторонний AI-API: токен утечёт.
    """
    file_id, file_unique_id, size = _pick_file(m)
    if not file_id:
        return None, None
    if size and size > _MAX_FILE_BYTES:
        log.info("media too big for vision: %s bytes", size)
        return None, file_unique_id
    try:
        f = await bot.get_file(file_id)
        path = getattr(f, "file_path", None)
        buf = await bot.download(file_id)
        if not buf:
            return None, file_unique_id
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
        if len(data) > _MAX_FILE_BYTES:
            return None, file_unique_id
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{_mime_of(path)};base64,{b64}", file_unique_id
    except Exception as e:
        log.warning("media download failed: %s", e)
        return None, file_unique_id
