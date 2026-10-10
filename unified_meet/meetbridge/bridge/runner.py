"""Runs one bridge bot: a browser context that joins an external meeting and becomes
a member of the MeetBridge room on that platform's behalf."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..config import Settings
from ..hub import Hub, Peer
from ..models import Provider
from ..providers import CallStatus, JoinError, ProviderRegistry
from ..providers.base import StateCallback

log = logging.getLogger("meetbridge.bot")

INJECT_JS = (Path(__file__).parent / "inject.js").read_text(encoding="utf-8")
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
_BROWSER_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
]
# Platforms that decode audio in WASM and play it through WebAudio instead of
# exposing remote WebRTC tracks.
_WEBAUDIO_PROVIDERS = {Provider.ZOOM}
STOPPED = "stopped"
MEETING_ENDED = "meeting_ended"


@dataclass(frozen=True)
class BotJob:
    link_id: str
    room_id: str
    room_title: str
    provider: str
    url: str
    passcode: str | None
    display_name: str


class BotRunner(Protocol):
    async def run(self, job: BotJob, on_state: StateCallback, stop: asyncio.Event) -> str: ...

    async def close(self) -> None: ...


class PlaywrightBotRunner:
    def __init__(self, settings: Settings, hub: Hub, registry: ProviderRegistry):
        self.settings = settings
        self.hub = hub
        self.registry = registry
        self._pw: Any = None
        self._browser: Any = None
        self._browser_lock = asyncio.Lock()

    async def _get_browser(self) -> Any:
        async with self._browser_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            from playwright.async_api import async_playwright

            if self._pw is None:
                self._pw = await async_playwright().start()
            kwargs: dict[str, Any] = {"headless": self.settings.browser_headless, "args": _BROWSER_ARGS}
            if self.settings.browser_channel:
                kwargs["channel"] = self.settings.browser_channel
            self._browser = await self._pw.chromium.launch(**kwargs)
            return self._browser

    async def browser_available(self) -> bool:
        try:
            await self._get_browser()
            return True
        except Exception as exc:
            log.warning("browser unavailable: %s", exc)
            return False

    async def close(self) -> None:
        async with self._browser_lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def run(self, job: BotJob, on_state: StateCallback, stop: asyncio.Event) -> str:
        adapter = self.registry.get(job.provider)
        meeting = adapter.parse(job.url, job.passcode)
        browser = await self._get_browser()
        context = await browser.new_context(
            permissions=["microphone", "camera"],
            bypass_csp=True,
            locale="en-US",
            user_agent=_UA,
            viewport={"width": 1280, "height": 720},
        )
        peer: Peer | None = None
        outbox: asyncio.Queue = asyncio.Queue(maxsize=1000)
        pump_task: asyncio.Task | None = None
        try:
            page_holder: dict[str, Any] = {}

            async def from_page(_source: Any, payload: str) -> None:
                if peer is None or len(payload) > 64 * 1024:
                    return
                try:
                    msg = json.loads(payload)
                except ValueError:
                    return
                await self.hub.handle(peer, msg)

            await context.expose_binding("__mbSend", from_page)
            config = {
                "captureWebAudio": Provider(job.provider) in _WEBAUDIO_PROVIDERS,
                "roomTitle": job.room_title,
                "platformName": adapter.display_name,
                "waitingText": "Waiting for participants from other platforms",
            }
            await context.add_init_script(f"window.__mbConfig = {json.dumps(config)};\n{INJECT_JS}")
            page = await context.new_page()
            page_holder["page"] = page
            page.on("console", lambda m: m.type == "error" and log.debug("page[%s]: %s", job.link_id, m.text))

            await on_state("joining", "")
            join = asyncio.create_task(
                adapter.join(
                    page,
                    meeting,
                    job.display_name,
                    on_state,
                    self.settings.bot_join_timeout_seconds,
                    self.settings.bot_admission_timeout_seconds,
                )
            )
            stopper = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({join, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if stopper in done:
                join.cancel()
                return STOPPED
            stopper.cancel()
            join.result()

            async def pump() -> None:
                while True:
                    msg = await outbox.get()
                    try:
                        await page.evaluate("m => window.__mbHubReceive && window.__mbHubReceive(m)", msg)
                    except Exception as exc:
                        log.debug("deliver to page failed: %s", exc)

            pump_task = asyncio.create_task(pump())

            async def to_page(msg: dict[str, Any]) -> None:
                await outbox.put(msg)

            peer = await self.hub.join(job.room_id, adapter.display_name, job.provider, to_page, link_id=job.link_id)
            await on_state("live", "")
            return await self._monitor(adapter, page, stop)
        finally:
            if peer is not None:
                await self.hub.leave(peer)
            if pump_task is not None:
                pump_task.cancel()
            try:
                await context.close()
            except Exception:
                pass

    async def _monitor(self, adapter: Any, page: Any, stop: asyncio.Event) -> str:
        misses = 0
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                break
            if page.is_closed():
                raise JoinError("browser_page_closed", terminal=False)
            detection = await adapter.detect(page)
            if detection.status == CallStatus.IN_CALL:
                misses = 0
                continue
            if detection.status in (CallStatus.ENDED, CallStatus.DENIED):
                return MEETING_ENDED
            misses += 1
            # Tolerate short UI transitions (layout changes, reconnect banners).
            if misses >= 5:
                raise JoinError("dropped_from_call", terminal=False)
        try:
            await adapter.leave(page)
        except Exception:
            pass
        return STOPPED
