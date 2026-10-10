"""Provider adapter contract.

An adapter knows how to (1) validate and normalize a meeting URL for its platform and
(2) drive that platform's *web client* inside a Playwright page until the bridge bot
is in the call. Join flows are written as "act on what is visible" loops rather than
fixed step sequences, because every platform shows interstitials (cookie banners,
"continue in browser", device prompts) in a different order depending on account,
region and A/B tests.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..models import Provider

log = logging.getLogger("meetbridge.providers")


class InvalidMeetingUrl(ValueError):
    """The URL is not a joinable meeting link for the provider. ``code`` is user-facing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JoinError(RuntimeError):
    """Join failed. ``terminal`` errors must not be retried automatically."""

    def __init__(self, code: str, terminal: bool, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.terminal = terminal


class CallStatus(enum.Enum):
    UNKNOWN = "unknown"
    PRE_JOIN = "pre_join"
    WAITING = "waiting"
    IN_CALL = "in_call"
    DENIED = "denied"
    ENDED = "ended"
    INVALID = "invalid"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ParsedMeeting:
    provider: Provider
    url: str
    join_url: str
    meeting_id: str
    passcode: str | None = None


@dataclass(frozen=True)
class Detection:
    status: CallStatus
    reason: str = ""


StateCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class JoinSelectors:
    dismiss: Sequence[str] = ()
    name_inputs: Sequence[str] = ()
    passcode_inputs: Sequence[str] = ()
    join_buttons: Sequence[str] = ()
    post_join_clicks: Sequence[str] = ()
    in_call: Sequence[str] = ()
    leave_buttons: Sequence[str] = ()
    waiting_text: Sequence[str] = ()
    denied_text: Sequence[str] = ()
    ended_text: Sequence[str] = ()
    invalid_text: Sequence[str] = ()
    blocked_text: Sequence[str] = ()


class ProviderAdapter(ABC):
    provider: Provider
    display_name: str
    selectors: JoinSelectors

    @abstractmethod
    def parse(self, url: str, passcode: str | None = None) -> ParsedMeeting: ...

    def public_join_url(self, meeting: ParsedMeeting) -> str:
        """The link shown to human invitees (opens the platform's own app or site)."""
        return meeting.url

    # ------------------------------------------------------------------ detection
    async def detect(self, page: Any) -> Detection:
        s = self.selectors
        # In-call controls win over text matches so that chat messages or captions
        # containing e.g. "the call has ended" cannot end the bridge.
        if await _any_visible(page, s.in_call):
            return Detection(CallStatus.IN_CALL)
        text = _normalize(await _page_text(page))
        for phrase in s.denied_text:
            if _normalize(phrase) in text:
                return Detection(CallStatus.DENIED, phrase)
        for phrase in s.ended_text:
            if _normalize(phrase) in text:
                return Detection(CallStatus.ENDED, phrase)
        for phrase in s.invalid_text:
            if _normalize(phrase) in text:
                return Detection(CallStatus.INVALID, phrase)
        for phrase in s.blocked_text:
            if _normalize(phrase) in text:
                return Detection(CallStatus.BLOCKED, phrase)
        for phrase in s.waiting_text:
            if _normalize(phrase) in text:
                return Detection(CallStatus.WAITING, phrase)
        if await _any_visible(page, s.join_buttons) or await _any_visible(page, s.name_inputs):
            return Detection(CallStatus.PRE_JOIN)
        return Detection(CallStatus.UNKNOWN)

    # ----------------------------------------------------------------------- join
    async def join(
        self,
        page: Any,
        meeting: ParsedMeeting,
        display_name: str,
        on_state: StateCallback,
        join_timeout: float,
        admission_timeout: float,
    ) -> None:
        """Returns once the bot is in the call; raises JoinError otherwise."""
        await page.goto(meeting.join_url, wait_until="domcontentloaded", timeout=45_000)
        s = self.selectors
        started = time.monotonic()
        waiting_since: float | None = None
        name_filled = False
        passcode_filled = False
        while True:
            detection = await self.detect(page)
            if detection.status == CallStatus.IN_CALL:
                await self._post_join(page)
                return
            if detection.status in _TERMINAL:
                raise JoinError(_TERMINAL[detection.status], terminal=True, message=detection.reason)
            now = time.monotonic()
            if detection.status == CallStatus.WAITING:
                if waiting_since is None:
                    waiting_since = now
                    await on_state("waiting_admission", "")
                if now - waiting_since > admission_timeout:
                    raise JoinError("admission_timeout", terminal=False)
                await asyncio.sleep(1.0)
                continue
            if waiting_since is None and now - started > join_timeout:
                raise JoinError("join_timeout", terminal=False)

            await _click_first(page, s.dismiss)
            if not name_filled:
                name_filled = await _fill_first(page, s.name_inputs, display_name)
            if meeting.passcode and not passcode_filled:
                passcode_filled = await _fill_first(page, s.passcode_inputs, meeting.passcode)
            await _click_first(page, s.join_buttons)
            await asyncio.sleep(1.0)

    async def _post_join(self, page: Any) -> None:
        for selector in self.selectors.post_join_clicks:
            await _click_first(page, [selector])

    async def leave(self, page: Any) -> None:
        await _click_first(page, self.selectors.leave_buttons)


_TERMINAL = {
    CallStatus.DENIED: "admission_denied",
    CallStatus.ENDED: "meeting_ended",
    CallStatus.INVALID: "meeting_not_found",
    CallStatus.BLOCKED: "platform_blocked_bot",
}


# --------------------------------------------------------------------- page helpers
def _frames(page: Any) -> list[Any]:
    try:
        return list(page.frames)
    except Exception:  # page closed
        return []


_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " "})


def _normalize(text: str) -> str:
    """Lower-cased, with typographic quotes and non-breaking spaces folded to ASCII."""
    return text.translate(_QUOTES).lower()


async def _page_text(page: Any) -> str:
    chunks: list[str] = []
    for frame in _frames(page):
        try:
            chunks.append(await frame.evaluate("() => document.body ? document.body.innerText : ''"))
        except Exception:
            continue
    return "\n".join(chunks)


async def _first_visible(page: Any, selectors: Sequence[str]) -> Any | None:
    for frame in _frames(page):
        for selector in selectors:
            try:
                loc = frame.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    return loc
            except Exception:
                continue
    return None


async def _any_visible(page: Any, selectors: Sequence[str]) -> bool:
    return await _first_visible(page, selectors) is not None


async def _click_first(page: Any, selectors: Sequence[str]) -> bool:
    loc = await _first_visible(page, selectors)
    if loc is None:
        return False
    try:
        if await loc.is_enabled():
            await loc.click(timeout=3_000)
            return True
    except Exception as exc:
        log.debug("click failed: %s", exc)
    return False


async def _fill_first(page: Any, selectors: Sequence[str], value: str) -> bool:
    loc = await _first_visible(page, selectors)
    if loc is None:
        return False
    try:
        await loc.fill(value, timeout=3_000)
        return True
    except Exception as exc:
        log.debug("fill failed: %s", exc)
        return False
