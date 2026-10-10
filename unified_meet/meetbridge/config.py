"""Runtime configuration loaded from environment variables (prefix ``MB_``)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MB_", env_file=BASE_DIR / ".env", extra="ignore")

    env: str = Field("development", description="development | test | production")
    data_dir: Path = Field(BASE_DIR / "data", description="Generated secret key and backups live here")
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'meetbridge.db'}"
    secret_key: str = Field("", description="Random string used to derive session cookie signatures")
    public_base_url: str = "http://localhost:8080"
    host: str = "0.0.0.0"
    port: int = 8080

    session_ttl_hours: int = 72
    allow_registration: bool = True
    cookie_secure: bool = False

    # Default STUN only; set a TURN server for participants behind strict NATs.
    ice_servers: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"urls": ["stun:stun.l.google.com:19302"]}]
    )

    max_active_bots: int = 6
    max_links_per_room: int = 6
    max_participants_per_room: int = 12
    bot_max_retries: int = 3
    bot_retry_base_seconds: float = 5.0
    bot_join_timeout_seconds: float = 90.0
    bot_admission_timeout_seconds: float = 600.0
    bot_default_name: str = "MeetBridge"
    browser_channel: str = Field("", description="'' = bundled Chromium, or 'chrome' / 'msedge'")
    browser_headless: bool = True
    bot_accounts_dir: Path | None = Field(
        None, description="Saved sign-ins for bridge bots, <provider>.json (default: <data_dir>/bot-accounts)"
    )
    supervisor_interval_seconds: float = 3.0

    enable_mock_provider: bool = Field(
        False, description="Serves the local MockMeet test platform. Never enable in production."
    )
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("ice_servers", mode="before")
    @classmethod
    def _parse_ice(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = json.loads(value)
        return value

    @field_validator("env")
    @classmethod
    def _check_env(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError("MB_ENV must be development, test or production")
        return value

    def bot_account_file(self, provider: str) -> Path:
        return (self.bot_accounts_dir or self.data_dir / "bot-accounts") / f"{provider}.json"

    def validate_for_startup(self) -> list[str]:
        """Returns fatal configuration problems (empty list when the config is usable)."""
        problems: list[str] = []
        if self.env == "production":
            if len(self.secret_key) < 32:
                problems.append("MB_SECRET_KEY must be set to at least 32 random characters in production")
            if self.enable_mock_provider:
                problems.append("MB_ENABLE_MOCK_PROVIDER must be false in production")
            if not self.public_base_url.startswith("https://"):
                problems.append("MB_PUBLIC_BASE_URL must use https:// in production (browsers require it for camera/mic)")
        if self.max_active_bots < 1:
            problems.append("MB_MAX_ACTIVE_BOTS must be >= 1")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
