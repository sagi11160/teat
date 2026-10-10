"""Adapter for MockMeet, the local WebRTC meeting platform used by the automated tests.

Only registered when ``MB_ENABLE_MOCK_PROVIDER=true``; it exercises exactly the same
bridge code path (page injection, getUserMedia override, RTCPeerConnection capture)
as the real platforms.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..models import Provider
from .base import InvalidMeetingUrl, JoinSelectors, ParsedMeeting, ProviderAdapter

_PATH_RE = re.compile(r"^/mock-meet/([a-z0-9-]{3,40})/?$")


class MockMeetAdapter(ProviderAdapter):
    provider = Provider.MOCK
    display_name = "MockMeet (test)"
    selectors = JoinSelectors(
        name_inputs=("#mm-name",),
        join_buttons=("#mm-join",),
        in_call=("#mm-leave",),
        leave_buttons=("#mm-leave",),
        waiting_text=("Waiting for the host to let you in",),
        denied_text=("The host denied your request",),
        ended_text=("The meeting has ended",),
    )

    def __init__(self, base_url: str):
        self._base = urlsplit(base_url)

    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting:
        parts = urlsplit(url.strip())
        if (parts.hostname or "").lower() != (self._base.hostname or "").lower() or parts.port != self._base.port:
            raise InvalidMeetingUrl("not_mock", "Not a MockMeet link")
        m = _PATH_RE.match(parts.path)
        if not m:
            raise InvalidMeetingUrl("mock_no_code", "MockMeet links look like /mock-meet/<code>")
        canonical = f"{self._base.scheme}://{self._base.netloc}/mock-meet/{m.group(1)}"
        return ParsedMeeting(self.provider, canonical, canonical, m.group(1), None)
