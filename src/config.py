from __future__ import annotations
import os
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

CONFIG_PATH = Path("config/config.yaml")
ENV_PATH = Path(".env")

class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    vega_api_key: str = Field(default="", alias="VEGA_API_KEY")
    vega_base_url: str = Field(default="https://api.vega.chat/v1", alias="VEGA_BASE_URL")
    vega_model: str = Field(default="gpt-4o-mini", alias="VEGA_MODEL")
    allowed_chats: str = Field(default="", alias="ALLOWED_CHATS")  # csv
    whitelist_users: str = Field(default="", alias="WHITELIST_USERS")
    default_language: str = Field(default="ru", alias="DEFAULT_LANGUAGE")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    filter_prompt_path: str = Field(default="config/filter_prompt.txt", alias="FILTER_PROMPT_PATH")
    captcha_timeout_sec: int = Field(default=120, alias="CAPTCHA_TIMEOUT_SEC")
    delete_spam: bool = Field(default=True, alias="DELETE_SPAM")
    mute_on_captcha_fail: bool = Field(default=True, alias="MUTE_ON_CAPTCHA_FAIL")
    ban_on_repeat_spam: bool = Field(default=False, alias="BAN_ON_REPEAT_SPAM")

    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}

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
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    ex = Path("config/config.example.yaml")
    if ex.exists():
        return yaml.safe_load(ex.read_text(encoding="utf-8")) or {}
    return {}

def load_settings() -> Settings:
    # yaml приоритетнее .env если задан
    y = load_yaml_config()
    # пробрасываем yaml в env-подобные ключи
    mapping = {
        "bot_token": "BOT_TOKEN",
        "vega_api_key": "VEGA_API_KEY",
        "vega_base_url": "VEGA_BASE_URL",
        "vega_model": "VEGA_MODEL",
        "default_language": "DEFAULT_LANGUAGE",
        "filter_prompt_path": "FILTER_PROMPT_PATH",
    }
    for k, envk in mapping.items():
        if y.get(k) and not os.getenv(envk):
            os.environ[envk] = str(y[k])
    if y.get("allowed_chats"):
        v = ",".join(str(x) for x in y["allowed_chats"])
        if not os.getenv("ALLOWED_CHATS"):
            os.environ["ALLOWED_CHATS"] = v
    if y.get("whitelist_users"):
        v = ",".join(str(x) for x in y["whitelist_users"])
        if not os.getenv("WHITELIST_USERS"):
            os.environ["WHITELIST_USERS"] = v
    if y.get("admin_users"):
        v = ",".join(str(x) for x in y["admin_users"])
        if not os.getenv("ADMIN_USER_IDS"):
            os.environ["ADMIN_USER_IDS"] = v
    return Settings()

def filter_prompt() -> str:
    s = load_settings()
    p = Path(s.filter_prompt_path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return Path("config/filter_prompt.txt").read_text(encoding="utf-8") if Path("config/filter_prompt.txt").exists() else ""
