"""Provider state detection against static pages in a real browser.

The phrases come from the platforms' live pages (verified against zoom.us,
meet.google.com and teams.microsoft.com with nonexistent meeting links).
"""

import asyncio

import pytest
from playwright.async_api import async_playwright

from meetbridge.providers.base import CallStatus
from meetbridge.providers.google_meet import GoogleMeetAdapter
from meetbridge.providers.mock import MockMeetAdapter
from meetbridge.providers.teams import TeamsAdapter
from meetbridge.providers.zoom import ZoomAdapter


def detect(adapter, html):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            try:
                return await adapter.detect(page)
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.parametrize(
    "adapter, html, status",
    [
        (TeamsAdapter(), "<h1>We can\u2019t find this meeting</h1><button>Rejoin call</button><button>Dismiss</button>", CallStatus.INVALID),
        (TeamsAdapter(), "<button>Rejoin</button><h1>Meetings are just one tool in our belt.</h1>", CallStatus.ENDED),
        (TeamsAdapter(), "<p>Someone in the meeting should let you in soon</p>", CallStatus.WAITING),
        (TeamsAdapter(), "<p>Anonymous users can't join this meeting.</p>", CallStatus.BLOCKED),
        (TeamsAdapter(), "<input placeholder='Type your name'><button>Join now</button>", CallStatus.PRE_JOIN),
        (TeamsAdapter(), "<button data-tid='hangup-main-btn'>Leave</button><p>chat: The meeting has ended?</p>", CallStatus.IN_CALL),
        (GoogleMeetAdapter(), "<h1>Check your meeting code</h1>", CallStatus.INVALID),
        (GoogleMeetAdapter(), "<p>You can\u2019t join this video call</p>", CallStatus.BLOCKED),
        (GoogleMeetAdapter(), "<p>Someone in the call denied your request to join</p>", CallStatus.DENIED),
        (GoogleMeetAdapter(), "<p>Asking to be let in\u2026</p>", CallStatus.WAITING),
        (ZoomAdapter(), "<p>This meeting link is invalid (3,001)</p>", CallStatus.INVALID),
        (ZoomAdapter(), "<p>Please wait, the meeting host will let you in soon.</p>", CallStatus.WAITING),
        (ZoomAdapter(), "<p>This meeting has been ended by host</p>", CallStatus.ENDED),
        (ZoomAdapter(), "<input id='input-for-name'><button class='preview-join-button'>Join</button>", CallStatus.PRE_JOIN),
        (MockMeetAdapter("http://x"), "<p>The host denied your request</p>", CallStatus.DENIED),
        (MockMeetAdapter("http://x"), "<p>nothing recognisable</p>", CallStatus.UNKNOWN),
    ],
)
def test_detect(adapter, html, status):
    assert detect(adapter, html).status == status
