from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..models import Provider
from .base import InvalidMeetingUrl, JoinSelectors, ParsedMeeting, ProviderAdapter

_CODE_RE = re.compile(r"^/([a-z]{3}-[a-z]{4}-[a-z]{3})/?$")
_LOOKUP_RE = re.compile(r"^/lookup/([a-z0-9_-]{3,64})/?$", re.IGNORECASE)


class GoogleMeetAdapter(ProviderAdapter):
    provider = Provider.GOOGLE_MEET
    display_name = "Google Meet"
    selectors = JoinSelectors(
        dismiss=(
            "button:has-text('Got it')",
            "button:has-text('Dismiss')",
            "button:has-text('Accept all')",
            "button:has-text('No thanks')",
        ),
        name_inputs=("input[aria-label='Your name']", "input[placeholder='Your name']", "input[type='text'][jsname]"),
        join_buttons=(
            "button:has-text('Join now')",
            "button:has-text('Ask to join')",
            "button:has-text('Switch here')",
        ),
        post_join_clicks=(
            "button[aria-label^='Turn on microphone']",
            "button[aria-label^='Turn on camera']",
            "button:has-text('Got it')",
        ),
        in_call=("button[aria-label='Leave call']", "[aria-label='Leave call']"),
        leave_buttons=("button[aria-label='Leave call']",),
        waiting_text=(
            "Asking to be let in",
            "You'll join the call when someone lets you in",
            "Please wait until a meeting host brings you into the call",
        ),
        denied_text=(
            "Someone in the call denied your request",
            "You've been removed from the meeting",
            "No one responded to your request",
        ),
        ended_text=("The call has ended", "You left the meeting", "The meeting has ended"),
        invalid_text=("Check your meeting code", "Invalid video call name", "This meeting code doesn't exist"),
        blocked_text=(
            "You can't join this video call",
            "You can't join this call",
            "Sign in with your Google account",
            "Your browser isn't supported",
        ),
    )

    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or host != "meet.google.com":
            raise InvalidMeetingUrl("not_google_meet", "Not a Google Meet link")
        path = parts.path.lower()
        m = _CODE_RE.match(path)
        if m:
            code = m.group(1)
        else:
            m = _LOOKUP_RE.match(parts.path)
            if not m:
                raise InvalidMeetingUrl("meet_no_code", "The Google Meet link must look like meet.google.com/abc-defg-hij")
            code = f"lookup/{m.group(1)}"
        canonical = f"https://meet.google.com/{code}"
        # hl=en pins the UI language so the join-flow text detection is deterministic.
        return ParsedMeeting(self.provider, canonical, f"{canonical}?hl=en", code, None)
