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

# кэш настроек и промпта
_settings_cache: tuple[float, Settings] | None = None
_prompt_cache: tuple[float, str, str] | None = None  # (ts, path, content)
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
    captcha_timeout_sec: int = Field(default=120, alias="CAPTCHA_TIMEOUT_SEC")
    delete_spam: bool = Field(default=True, alias="DELETE_SPAM")
    mute_on_captcha_fail: bool = Field(default=True, alias="MUTE_ON_CAPTCHA_FAIL")
    ban_on_repeat_spam: bool = Field(default=False, alias="BAN_ON_REPEAT_SPAM")

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
    for k in ("captcha_timeout_sec", "delete_spam", "mute_on_captcha_fail", "ban_on_repeat_spam"):
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
    }
    for k, envks in env_alias.items():
        if k in yaml_kwargs and any(_os.getenv(e) for e in envks):
            del yaml_kwargs[k]

    s = Settings(**yaml_kwargs)
    _settings_cache = (now, s)
    return s


def clear_settings_cache() -> None:
    global _settings_cache, _prompt_cache
    _settings_cache = None
    _prompt_cache = None


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
