import asyncio
import json

from playwright.async_api import async_playwright

from meetbridge import cli
from meetbridge.bridge.runner import PlaywrightBotRunner
from meetbridge.config import get_settings

from .conftest import make_settings


def write_state(path, domain=".google.com"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cookies": [{"name": "SID", "value": "bridge-session", "domain": domain, "path": "/", "expires": -1,
                     "httpOnly": True, "secure": True, "sameSite": "Lax"}],
        "origins": [],
    }))


def test_guest_by_default(tmp_path):
    runner = PlaywrightBotRunner(make_settings(tmp_path), hub=None, registry=None)
    assert "storage_state" not in runner.context_options("google_meet")


def test_saved_sign_in_is_used_only_for_its_provider(tmp_path):
    settings = make_settings(tmp_path)
    write_state(settings.bot_account_file("google_meet"))
    runner = PlaywrightBotRunner(settings, hub=None, registry=None)
    assert runner.context_options("google_meet")["storage_state"].endswith("google_meet.json")
    assert "storage_state" not in runner.context_options("teams")


def test_saved_cookies_reach_the_bot_browser(tmp_path):
    settings = make_settings(tmp_path)
    write_state(settings.bot_account_file("google_meet"))
    options = PlaywrightBotRunner(settings, hub=None, registry=None).context_options("google_meet")

    async def cookies():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(**options)
            try:
                return await context.cookies("https://meet.google.com")
            finally:
                await browser.close()

    assert [c["value"] for c in asyncio.run(cookies()) if c["name"] == "SID"] == ["bridge-session"]


def test_bot_logout(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MB_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    try:
        account = get_settings().bot_account_file("teams")
        write_state(account, ".microsoft.com")
        assert cli.main(["bot-logout", "teams"]) == 0
        assert not account.exists()
        assert cli.main(["bot-logout", "teams"]) == 0
        assert "no saved sign-in" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


def test_bot_login_without_display_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    get_settings.cache_clear()
    try:
        assert cli.main(["bot-login", "zoom"]) == 1
        assert "display" in capsys.readouterr().err
        assert not get_settings().bot_account_file("zoom").exists()
    finally:
        get_settings.cache_clear()
