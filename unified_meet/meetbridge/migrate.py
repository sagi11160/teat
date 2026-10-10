from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import BASE_DIR


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade_to_head(database_url: str) -> None:
    if database_url.startswith("sqlite"):
        path = database_url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(database_url), "head")
