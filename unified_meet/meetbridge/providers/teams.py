from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Provider
from .base import InvalidMeetingUrl, JoinSelectors, ParsedMeeting, ProviderAdapter

_HOSTS = {"teams.microsoft.com", "teams.live.com", "teams.microsoft.us", "gov.teams.microsoft.us", "dod.teams.microsoft.us"}
_MEETUP_RE = re.compile(r"^/l/meetup-join/[^/]+/[^/]+$")
_MEET_RE = re.compile(r"^/meet/(\d{6,20})/?$")


class TeamsAdapter(ProviderAdapter):
    provider = Provider.TEAMS
    display_name = "Microsoft Teams"
    selectors = JoinSelectors(
        dismiss=(
            "button:has-text('Continue on this browser')",
            "[data-tid='joinOnWeb']",
            "button:has-text('Join on the web instead')",
            "button:has-text('Accept')",
        ),
        name_inputs=(
            "input[data-tid='prejoin-display-name-input']",
            "input[placeholder='Type your name']",
            "input[placeholder='Enter name']",
        ),
        join_buttons=("button[data-tid='prejoin-join-button']", "button:has-text('Join now')"),
        in_call=("button[data-tid='hangup-main-btn']", "#hangup-button", "button[aria-label='Leave']", "button[aria-label^='Leave (']"),
        leave_buttons=("button[data-tid='hangup-main-btn']", "#hangup-button", "button[aria-label='Leave']"),
        waiting_text=(
            "Someone in the meeting should let you in soon",
            "We've let people in the meeting know you're waiting",
            "When the meeting starts, we'll let people know you're waiting",
            "Waiting to be admitted",
        ),
        denied_text=("Sorry, but you were denied access to the meeting", "You've been removed from this meeting", "Someone removed you from the meeting"),
        # The last phrase is Teams' post-call page, shown once the bot is out of the call.
        ended_text=("The meeting has ended", "This meeting has ended", "You left the meeting",
                    "Meetings are just one tool in our belt"),
        invalid_text=("This meeting link is invalid", "We can't find this meeting", "We couldn't find this meeting",
                      "The meeting doesn't exist"),
        blocked_text=(
            "Anonymous users can't join this meeting",
            "Sign in to join this meeting",
            "Your organization doesn't allow",
        ),
    )

    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or host not in _HOSTS:
            raise InvalidMeetingUrl("not_teams", "Not a Microsoft Teams link")
        query = dict(parse_qsl(parts.query))
        if _MEETUP_RE.match(parts.path):
            meeting_id = parts.path.split("/")[3]
            keep = {k: v for k, v in query.items() if k in ("context",)}
        else:
            m = _MEET_RE.match(parts.path)
            if not m:
                raise InvalidMeetingUrl("teams_no_meeting", "The Teams link must be a meeting join link (…/l/meetup-join/… or …/meet/…)")
            meeting_id = m.group(1)
            if "p" not in query and not passcode:
                raise InvalidMeetingUrl("teams_passcode_required", "This Teams link needs its passcode (the ?p=… part)")
            keep = {"p": query.get("p") or (passcode or "").strip()}
        canonical = urlunsplit(("https", host, parts.path, urlencode(keep), ""))
        join_query = dict(keep)
        # Skip the "open the desktop app?" launcher and go straight to the web client.
        join_query.update({"anon": "true", "msLaunch": "false", "directDl": "true", "suppressPrompt": "true"})
        join_url = urlunsplit(("https", host, parts.path, urlencode(join_query), ""))
        return ParsedMeeting(self.provider, canonical, join_url, meeting_id, None)
