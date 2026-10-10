from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from ..models import Provider
from .base import InvalidMeetingUrl, JoinSelectors, ParsedMeeting, ProviderAdapter

_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)*(zoom\.us|zoomgov\.com|zoom\.com)$")
_PATH_RES = (
    re.compile(r"^/j/(\d{9,11})/?$"),
    re.compile(r"^/w/(\d{9,11})/?$"),
    re.compile(r"^/wc/join/(\d{9,11})/?$"),
    re.compile(r"^/wc/(\d{9,11})/(?:join|start)/?$"),
    re.compile(r"^/s/(\d{9,11})/?$"),
)


class ZoomAdapter(ProviderAdapter):
    provider = Provider.ZOOM
    display_name = "Zoom"
    selectors = JoinSelectors(
        dismiss=(
            "#onetrust-accept-btn-handler",
            "button:has-text('Accept Cookies')",
            "button:has-text('Join from your browser')",
            "a:has-text('Join from your browser')",
            "button:has-text('Got it')",
        ),
        name_inputs=("#input-for-name", "input[placeholder='Your Name']", "input#inputname"),
        passcode_inputs=("#input-for-pwd", "input[placeholder='Meeting Passcode']", "input#inputpasscode"),
        join_buttons=("button.preview-join-button", "button#joinBtn", "button:has-text('Join')"),
        post_join_clicks=(
            "button:has-text('Join Audio by Computer')",
            "button.join-audio-by-voip__join-btn",
            "button[aria-label*='start my video' i]",
            "button[aria-label*='unmute my microphone' i]",
        ),
        in_call=(
            "button.footer__leave-btn",
            "button[aria-label='Leave']",
            "button:has-text('Leave'):near(.footer)",
        ),
        leave_buttons=("button.footer__leave-btn", "button[aria-label='Leave']", "button:has-text('Leave Meeting')"),
        waiting_text=(
            "Please wait, the meeting host will let you in soon",
            "Waiting for the host to start this meeting",
            "Host has joined. We've let them know you're here",
        ),
        denied_text=("You have been removed", "The host has removed you"),
        ended_text=("This meeting has been ended by host", "The meeting has been ended", "This meeting has ended"),
        invalid_text=("This meeting link is invalid", "Invalid meeting ID", "Meeting ID is not valid", "Incorrect passcode", "Passcode wrong"),
        blocked_text=("I'm not a robot", "Verify you are human", "Please sign in to join this meeting", "Only authenticated users can join"),
    )

    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or not _HOST_RE.match(host):
            raise InvalidMeetingUrl("not_zoom", "Not a Zoom link")
        meeting_id = None
        for rx in _PATH_RES:
            m = rx.match(parts.path)
            if m:
                meeting_id = m.group(1)
                break
        if meeting_id is None:
            if parts.path.startswith("/my/"):
                raise InvalidMeetingUrl(
                    "zoom_personal_link", "Personal (vanity) Zoom links are not supported; use the numeric meeting link"
                )
            raise InvalidMeetingUrl("zoom_no_meeting_id", "The Zoom link does not contain a meeting ID")
        # ``pwd`` in the URL is Zoom's encoded passcode; a typed passcode is only
        # needed when the link was shared without it.
        pwd = (parse_qs(parts.query).get("pwd") or [None])[0]
        query = f"?{urlencode({'pwd': pwd})}" if pwd else ""
        gov = host.endswith("zoomgov.com")
        canonical = f"https://{'zoomgov.com' if gov else 'zoom.us'}/j/{meeting_id}{query}"
        join_url = f"https://{'app.zoomgov.com' if gov else 'app.zoom.us'}/wc/{meeting_id}/join{query}"
        typed = passcode.strip() if passcode and passcode.strip() else None
        return ParsedMeeting(self.provider, canonical, join_url, meeting_id, typed)
