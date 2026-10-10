"""Opt-in checks against the real Zoom, Google Meet and Teams web clients.

    MB_LIVE_PLATFORM_TESTS=1 pytest tests/live

Always checks that a nonexistent meeting is reported as ``meeting_not_found``.
To check a real join, also set any of MB_LIVE_ZOOM_URL, MB_LIVE_MEET_URL,
MB_LIVE_TEAMS_URL to a meeting that allows guests; the bridge must reach the call
(or its waiting room) within the join timeout. Admit it from the host side to
see it go live.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from playwright.async_api import async_playwright

from meetbridge.bridge.runner import _BROWSER_ARGS, _UA, INJECT_JS
from meetbridge.config import Settings
from meetbridge.providers import JoinError, ProviderRegistry

pytestmark = [
    pytest.mark.live_platforms,
    pytest.mark.skipif(os.environ.get("MB_LIVE_PLATFORM_TESTS") != "1", reason="set MB_LIVE_PLATFORM_TESTS=1"),
]

NONEXISTENT = {
    "zoom": "https://zoom.us/j/123456789",
    "google_meet": "https://meet.google.com/abc-defg-hij",
    "teams": "https://teams.microsoft.com/meet/12345678901?p=abcdef",
}
REAL = {"zoom": "MB_LIVE_ZOOM_URL", "google_meet": "MB_LIVE_MEET_URL", "teams": "MB_LIVE_TEAMS_URL"}


async def _join(url: str, join_timeout: float = 60, admission_timeout: float = 5):
    registry = ProviderRegistry(Settings(_env_file=None))
    meeting = registry.parse(url)
    adapter = registry.get(meeting.provider)
    states: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=_BROWSER_ARGS)
        context = await browser.new_context(permissions=["microphone", "camera"], bypass_csp=True,
                                            locale="en-US", user_agent=_UA)
        await context.expose_binding("__mbSend", lambda *_: None)
        await context.add_init_script("window.__mbConfig = {};\n" + INJECT_JS)
        page = await context.new_page()

        async def on_state(state, _detail):
            states.append(state)

        try:
            await adapter.join(page, meeting, "MeetBridge live test", on_state, join_timeout, admission_timeout)
            outcome = "in_call"
            await adapter.leave(page)
        except JoinError as exc:
            outcome = exc.code
        await browser.close()
    return outcome, states


@pytest.mark.parametrize("provider", list(NONEXISTENT))
def test_nonexistent_meeting_is_reported(provider):
    outcome, _ = asyncio.run(_join(NONEXISTENT[provider]))
    # Google Meet answers anonymous guests from some networks (e.g. cloud IPs) with
    # "You can't join this video call" instead of "Check your meeting code".
    allowed = {"meeting_not_found", "platform_blocked_bot"} if provider == "google_meet" else {"meeting_not_found"}
    assert outcome in allowed


@pytest.mark.parametrize("provider", list(REAL))
def test_real_meeting_join(provider):
    url = os.environ.get(REAL[provider])
    if not url:
        pytest.skip(f"set {REAL[provider]} to a meeting that allows guests")
    outcome, states = asyncio.run(_join(url, admission_timeout=120))
    assert outcome == "in_call" or (outcome == "admission_timeout" and "waiting_admission" in states), (outcome, states)
