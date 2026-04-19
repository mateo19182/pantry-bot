from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    telegram_token: str
    openrouter_api_key: str
    openrouter_model: str
    whitelist: frozenset[int]
    db_path: str


def load_config() -> Config:
    load_dotenv()

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is required")

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required")

    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview").strip()

    raw_whitelist = os.environ.get("WHITELIST", "").strip()
    whitelist = frozenset(
        int(x) for x in raw_whitelist.split(",") if x.strip()
    )
    if not whitelist:
        raise RuntimeError("WHITELIST must contain at least one Telegram user ID")

    db_path = os.environ.get("DB_PATH", "./data/pantry.db").strip()

    return Config(
        telegram_token=token,
        openrouter_api_key=api_key,
        openrouter_model=model,
        whitelist=whitelist,
        db_path=db_path,
    )
