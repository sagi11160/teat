from __future__ import annotations

from ..config import Settings
from ..models import Provider
from .base import (
    CallStatus,
    Detection,
    InvalidMeetingUrl,
    JoinError,
    ParsedMeeting,
    ProviderAdapter,
)
from .google_meet import GoogleMeetAdapter
from .mock import MockMeetAdapter
from .teams import TeamsAdapter
from .zoom import ZoomAdapter


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self._adapters: dict[Provider, ProviderAdapter] = {
            Provider.ZOOM: ZoomAdapter(),
            Provider.GOOGLE_MEET: GoogleMeetAdapter(),
            Provider.TEAMS: TeamsAdapter(),
        }
        if settings.enable_mock_provider:
            self._adapters[Provider.MOCK] = MockMeetAdapter(settings.public_base_url)

    def get(self, provider: Provider | str) -> ProviderAdapter:
        return self._adapters[Provider(provider)]

    def enabled(self) -> list[ProviderAdapter]:
        return list(self._adapters.values())

    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting:
        """Detects the provider from the URL and returns the normalized meeting."""
        url = (url or "").strip()
        if not url:
            raise InvalidMeetingUrl("empty_url", "A meeting link is required")
        if len(url) > 1000:
            raise InvalidMeetingUrl("url_too_long", "The link is too long")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        last_specific: InvalidMeetingUrl | None = None
        for adapter in self._adapters.values():
            try:
                return adapter.parse(url, passcode)
            except InvalidMeetingUrl as exc:
                if not exc.code.startswith("not_"):
                    last_specific = exc
        raise last_specific or InvalidMeetingUrl(
            "unsupported_provider", "Only Zoom, Google Meet and Microsoft Teams links are supported"
        )


__all__ = [
    "CallStatus",
    "Detection",
    "InvalidMeetingUrl",
    "JoinError",
    "ParsedMeeting",
    "ProviderAdapter",
    "ProviderRegistry",
]
