from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from meetbridge.bridge.runner import MEETING_ENDED, STOPPED, BotJob
from meetbridge.config import Settings
from meetbridge.main import create_app
from meetbridge.providers import JoinError

CSRF = {"X-MeetBridge": "1"}
# Set to e.g. postgresql+psycopg://user:pass@localhost/meetbridge_test to run the
# API/supervisor suites against PostgreSQL. The database's public schema is wiped.
PG_URL = os.environ.get("MB_TEST_DATABASE_URL", "")


def reset_postgres(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def default_db_url(tmp_path) -> str:
    if PG_URL:
        reset_postgres(PG_URL)
        return PG_URL
    return f"sqlite:///{tmp_path / 'test.db'}"


def make_settings(tmp_path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "database_url": overrides.pop("database_url", None) or default_db_url(tmp_path),
        "data_dir": tmp_path / "data",
        "secret_key": "test-secret-key-that-is-long-enough-0123456789",
        "public_base_url": "http://testserver",
        "supervisor_interval_seconds": 0.05,
        "bot_retry_base_seconds": 0.05,
        "bot_max_retries": 2,
        "enable_mock_provider": True,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeRunner:
    """Scriptable stand-in for the browser bot.

    ``behaviours[url]`` is a list consumed one item per run attempt; the last item
    repeats. Items: "live" (stay in call until stopped), "ended" (join then the
    meeting ends), "wait" (stay in the lobby until stopped), a JoinError to raise,
    or "crash".
    """

    def __init__(self, *_: Any) -> None:
        self.behaviours: dict[str, list[Any]] = {}
        self.jobs: list[BotJob] = []
        self.closed = False

    def script(self, url: str, *items: Any) -> None:
        self.behaviours[url] = list(items)

    async def run(self, job: BotJob, on_state: Callable, stop: asyncio.Event) -> str:
        self.jobs.append(job)
        items = self.behaviours.get(job.url, ["live"])
        item = items.pop(0) if len(items) > 1 else items[0]
        await on_state("joining", "")
        if isinstance(item, JoinError):
            raise item
        if item == "crash":
            raise RuntimeError("simulated crash")
        if item == "wait":
            await on_state("waiting_admission", "")
            await stop.wait()
            return STOPPED
        await on_state("live", "")
        if item == "ended":
            return MEETING_ENDED
        await stop.wait()
        return STOPPED

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings, runner):
    app = create_app(settings, runner_factory=lambda *_: runner)
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str = "owner@example.com", name: str = "Owner") -> dict:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "display_name": name, "password": "correct horse battery"},
        headers=CSRF,
    )
    assert res.status_code == 201, res.text
    return res.json()


def switch_user(client: TestClient, email: str, name: str = "Other") -> dict:
    """Drops the current session cookie and registers/logs in as another user."""
    client.cookies.clear()
    return register(client, email, name)


def wait_for(predicate: Callable[[], Any], timeout: float = 5.0, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s (last={last!r})")
