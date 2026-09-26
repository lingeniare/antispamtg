from __future__ import annotations

import time
from pathlib import Path

import yaml
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = _PROJECT_ROOT / "config/config.yaml"
ENV_PATH = _PROJECT_ROOT / ".env"
_CONFIG_EXAMPLE = _PROJECT_ROOT / "config/config.example.yaml"
_DEFAULT_PROMPT = _PROJECT_ROOT / "config/filter_prompt.txt"
_DEFAULT_CHAT_PROMPT = _PROJECT_ROOT / "config/chat_prompt.txt"

# кэш настроек и промпта
_settings_cache: tuple[float, Settings] | None = None
_prompt_cache: tuple[float, str, str] | None = None  # (ts, path, content)
_chat_prompt_cache: tuple[float, str, str] | None = None
_CACHE_TTL = 60  # сек


class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    vega_api_key: str = Field(
        default="", validation_alias=AliasChoices("VEGA_API_KEY", "OPENROUTER_API_KEY", "AI_API_KEY")
    )
    vega_base_url: str = Field(
        default="https://api.vega.chat/v1",
        validation_alias=AliasChoices("VEGA_BASE_URL", "OPENROUTER_BASE_URL", "AI_BASE_URL"),
    )
    vega_model: str = Field(
        default="z-ai/glm-5.3-flash", validation_alias=AliasChoices("VEGA_MODEL", "OPENROUTER_MODEL", "AI_MODEL")
    )
    allowed_chats: str = Field(default="", alias="ALLOWED_CHATS")  # csv
    whitelist_users: str = Field(default="", alias="WHITELIST_USERS")
    default_language: str = Field(default="ru", alias="DEFAULT_LANGUAGE")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    filter_prompt_path: str = Field(default="config/filter_prompt.txt", alias="FILTER_PROMPT_PATH")
    delete_spam: bool = Field(default=True, alias="DELETE_SPAM")
    ban_on_repeat_spam: bool = Field(default=False, alias="BAN_ON_REPEAT_SPAM")
    # наказание за спам: "permanent" — перманентный мьют с первого нарушения,
    # "progressive" — старая эскалация 1д/7д/пермач по счётчику нарушений
    mute_policy: str = Field(default="permanent", alias="MUTE_POLICY")
    # image vision: always — сканить все медиа, new_users — только новые юзеры и форварды,
    # suspect — новые юзеры/форварды/медиа со ссылкой в подписи, off — выключено
    vision_mode: str = Field(default="suspect", alias="VISION_MODE")
    # vision-модель; пусто = использовать vega_model (glm-5.3-flash и gpt-6-luna уже умеют image+video)
    vega_vision_model: str = Field(default="", alias="VEGA_VISION_MODEL")
    # испытательный срок вместо капчи: юзер "новый" пока < PROBATION_HOURS часов в чате
    # или < PROBATION_MSGS проверенных сообщений
    probation_hours: int = Field(default=24, alias="PROBATION_HOURS")
    probation_msgs: int = Field(default=5, alias="PROBATION_MSGS")
    # сканировать bio/имя профиля при входе и первом сообщении
    bio_scan: bool = Field(default=True, alias="BIO_SCAN")
    # сообщений в RATE_WINDOW_SEC до вердикта "flood" без LLM
    rate_limit_count: int = Field(default=6, alias="RATE_LIMIT_COUNT")
    rate_window_sec: int = Field(default=10, alias="RATE_WINDOW_SEC")
    # разговорная личность ВЕГА: отвечает на @mention, ответы ей, имя в тексте, ЛС (админы/whitelist)
    chat_enabled: bool = Field(default=True, alias="CHAT_ENABLED")
    vega_chat_model: str = Field(default="", alias="VEGA_CHAT_MODEL")  # пусто = vega_model
    chat_prompt_path: str = Field(default="config/chat_prompt.txt", alias="CHAT_PROMPT_PATH")
    # веб-поиск в ответах (OpenRouter plugins id=web; работает если провайдер поддерживает)
    chat_web_search: bool = Field(default=False, alias="CHAT_WEB_SEARCH")
    chat_max_tokens: int = Field(default=500, alias="CHAT_MAX_TOKENS")
    # % чистых сообщений, которые ВЕГА увидит БЕЗ триггера и решит сама — встрять или молчать.
    # 0 = только по триггерам. Каждое ambient-сообщение = 1 LLM-вызов (цена свободы воли).
    chat_ambient_pct: int = Field(default=0, alias="CHAT_AMBIENT_PCT")
    # false (дефолт): после даунтайма доедаем буфер апдейтов Telegram (~24ч) и чистим
    # спам задним числом; true — выбросить очередь при старте (старое поведение)
    drop_pending_updates: bool = Field(default=False, alias="DROP_PENDING_UPDATES")

    model_config = {"env_file": str(_PROJECT_ROOT / ".env"), "extra": "ignore", "populate_by_name": True}

    @field_validator("bot_token")
    @classmethod
    def _validate_token(cls, v: str) -> str:
        if v and ":" not in v:
            raise ValueError("BOT_TOKEN должен быть вида 123456:ABC...")
        return v

    @property
    def allowed_chat_list(self) -> list[str]:
        return [c.strip() for c in self.allowed_chats.split(",") if c.strip()]

    @property
    def whitelist_list(self) -> list[int]:
        out: list[int] = []
        for x in self.whitelist_users.split(","):
            x = x.strip()
            if x.lstrip("-").isdigit():
                try:
                    out.append(int(x))
                except ValueError:
                    continue
        return out

    @property
    def admin_list(self) -> list[int]:
        out: list[int] = []
        for x in self.admin_user_ids.split(","):
            x = x.strip()
            if x.lstrip("-").isdigit():
                try:
                    out.append(int(x))
                except ValueError:
                    continue
        return out


def load_yaml_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def _merge_yaml_to_kwargs(y: dict) -> dict:
    """Маппит yaml-ключи в kwargs для Settings без мутации os.environ."""
    out: dict = {}
    mapping = {
        "bot_token": "bot_token",
        "vega_api_key": "vega_api_key",
        "vega_base_url": "vega_base_url",
        "vega_model": "vega_model",
        "default_language": "default_language",
        "filter_prompt_path": "filter_prompt_path",
        "mute_policy": "mute_policy",
        "vision_mode": "vision_mode",
        "vega_vision_model": "vega_vision_model",
        "probation_hours": "probation_hours",
        "probation_msgs": "probation_msgs",
        "bio_scan": "bio_scan",
        "rate_limit_count": "rate_limit_count",
        "rate_window_sec": "rate_window_sec",
        "chat_enabled": "chat_enabled",
        "vega_chat_model": "vega_chat_model",
        "chat_prompt_path": "chat_prompt_path",
        "chat_web_search": "chat_web_search",
        "chat_max_tokens": "chat_max_tokens",
        "chat_ambient_pct": "chat_ambient_pct",
    }
    for k, sk in mapping.items():
        if y.get(k) not in (None, ""):
            out[sk] = str(y[k])
    # списковые поля
    if y.get("allowed_chats"):
        v = ",".join(str(x) for x in y["allowed_chats"])
        out.setdefault("allowed_chats", v)
    if y.get("whitelist_users"):
        v = ",".join(str(x) for x in y["whitelist_users"])
        out.setdefault("whitelist_users", v)
    if y.get("admin_users"):
        v = ",".join(str(x) for x in y["admin_users"])
        out.setdefault("admin_user_ids", v)
    # bool/int из yaml
    for k in (
        "delete_spam",
        "ban_on_repeat_spam",
        "bio_scan",
        "probation_hours",
        "probation_msgs",
        "rate_limit_count",
        "rate_window_sec",
        "chat_enabled",
        "chat_web_search",
        "chat_max_tokens",
        "chat_ambient_pct",
    ):
        if k in y and y[k] is not None:
            out[k] = y[k]
    return out


def load_settings(*, use_cache: bool = True) -> Settings:
    global _settings_cache
    now = time.monotonic()
    if use_cache and _settings_cache and (now - _settings_cache[0] < _CACHE_TTL):
        return _settings_cache[1]

    y = load_yaml_config()
    yaml_kwargs = _merge_yaml_to_kwargs(y)
    # yaml не перетирает явно заданные env-переменные — pydantic сам приоритизирует env,
    # но мы передаём yaml только если env пустой для этих полей
    # чтобы не мутировать os.environ, фильтруем: если env уже есть, убираем yaml-значение
    import os as _os

    env_alias = {
        "bot_token": ("BOT_TOKEN",),
        "vega_api_key": ("VEGA_API_KEY", "OPENROUTER_API_KEY", "AI_API_KEY"),
        "vega_base_url": ("VEGA_BASE_URL", "OPENROUTER_BASE_URL", "AI_BASE_URL"),
        "vega_model": ("VEGA_MODEL", "OPENROUTER_MODEL", "AI_MODEL"),
        "default_language": ("DEFAULT_LANGUAGE",),
        "filter_prompt_path": ("FILTER_PROMPT_PATH",),
        "allowed_chats": ("ALLOWED_CHATS",),
        "whitelist_users": ("WHITELIST_USERS",),
        "admin_user_ids": ("ADMIN_USER_IDS",),
        "mute_policy": ("MUTE_POLICY",),
        "vision_mode": ("VISION_MODE",),
        "vega_vision_model": ("VEGA_VISION_MODEL",),
        "probation_hours": ("PROBATION_HOURS",),
        "probation_msgs": ("PROBATION_MSGS",),
        "bio_scan": ("BIO_SCAN",),
        "rate_limit_count": ("RATE_LIMIT_COUNT",),
        "rate_window_sec": ("RATE_WINDOW_SEC",),
        "chat_enabled": ("CHAT_ENABLED",),
        "vega_chat_model": ("VEGA_CHAT_MODEL",),
        "chat_prompt_path": ("CHAT_PROMPT_PATH",),
        "chat_web_search": ("CHAT_WEB_SEARCH",),
        "chat_max_tokens": ("CHAT_MAX_TOKENS",),
        "chat_ambient_pct": ("CHAT_AMBIENT_PCT",),
    }
    for k, envks in env_alias.items():
        if k in yaml_kwargs and any(_os.getenv(e) for e in envks):
            del yaml_kwargs[k]

    s = Settings(**yaml_kwargs)
    _settings_cache = (now, s)
    return s


def clear_settings_cache() -> None:
    global _settings_cache, _prompt_cache, _chat_prompt_cache
    _settings_cache = None
    _prompt_cache = None
    _chat_prompt_cache = None


def filter_prompt(*, use_cache: bool = True) -> str:
    global _prompt_cache
    s = load_settings(use_cache=use_cache)
    p = Path(s.filter_prompt_path)
    now = time.monotonic()
    if use_cache and _prompt_cache and _prompt_cache[1] == str(p) and (now - _prompt_cache[0] < _CACHE_TTL):
        return _prompt_cache[2]
    text = ""
    if p.exists():
        text = p.read_text(encoding="utf-8")
    elif _DEFAULT_PROMPT.exists():
        text = _DEFAULT_PROMPT.read_text(encoding="utf-8")
    _prompt_cache = (now, str(p), text)
    return text


def chat_prompt(*, use_cache: bool = True) -> str:
    """Промпт личности ВЕГА — config/chat_prompt.txt, кэш 60с."""
    global _chat_prompt_cache
    s = load_settings(use_cache=use_cache)
    p = Path(s.chat_prompt_path)
    now = time.monotonic()
    if use_cache and _chat_prompt_cache and _chat_prompt_cache[1] == str(p) and (now - _chat_prompt_cache[0] < _CACHE_TTL):
        return _chat_prompt_cache[2]
    text = ""
    if p.exists():
        text = p.read_text(encoding="utf-8")
    elif _DEFAULT_CHAT_PROMPT.exists():
        text = _DEFAULT_CHAT_PROMPT.read_text(encoding="utf-8")
    _chat_prompt_cache = (now, str(p), text)
    return text
