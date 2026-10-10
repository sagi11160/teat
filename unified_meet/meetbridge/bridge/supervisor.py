"""Reconciles each meeting link's *desired* state (set by the organizer) with the
bridge bots actually running, and owns retries, capacity limits and restart recovery."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import session_scope
from ..hub import EventBus
from ..models import ACTIVE_LINK_STATES, DesiredState, LinkState, MeetingLink, Room, RoomEvent, iso, utcnow
from ..providers import InvalidMeetingUrl, JoinError
from .runner import MEETING_ENDED, BotJob, BotRunner

log = logging.getLogger("meetbridge.supervisor")


def link_public(link: MeetingLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "provider": link.provider,
        "url": link.url,
        "label": link.label,
        "has_passcode": bool(link.passcode),
        "desired_state": link.desired_state,
        "state": link.state,
        "state_detail": link.state_detail,
        "attempts": link.attempts,
        "retry_at": iso(link.retry_at),
        "state_changed_at": iso(link.state_changed_at),
    }


class Supervisor:
    def __init__(self, settings: Settings, runner: BotRunner, bus: EventBus):
        self.settings = settings
        self.runner = runner
        self.bus = bus
        self._tasks: dict[str, asyncio.Task] = {}
        self._stops: dict[str, asyncio.Event] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._recover_after_restart)
        self._loop_task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        self._closing = True
        if self._loop_task:
            self._loop_task.cancel()
        for stop in self._stops.values():
            stop.set()
        if self._tasks:
            await asyncio.wait(list(self._tasks.values()), timeout=15)
        await self.runner.close()

    def kick(self) -> None:
        self._wake.set()

    def is_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def running_link_ids(self) -> set[str]:
        return set(self._tasks)

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:
                log.exception("reconcile failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.supervisor_interval_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _recover_after_restart(self) -> None:
        with session_scope() as db:
            for link in db.scalars(select(MeetingLink)):
                if link.desired_state == DesiredState.RUNNING.value:
                    link.attempts = 0
                    link.retry_at = None
                    self._set_state(db, link, LinkState.STARTING, "resumed_after_restart", actor="system")
                elif link.state in {s.value for s in ACTIVE_LINK_STATES}:
                    self._set_state(db, link, LinkState.STOPPED, "server_restart", actor="system")

    # --------------------------------------------------------------- reconcile
    async def reconcile(self) -> None:
        if self._closing:
            return
        plan = await asyncio.to_thread(self._plan)
        for link_id in plan["stop"]:
            stop = self._stops.get(link_id)
            if stop:
                stop.set()
        for job in plan["start"]:
            stop = asyncio.Event()
            self._stops[job.link_id] = stop
            self._tasks[job.link_id] = asyncio.create_task(self._run(job, stop))

    def _plan(self) -> dict[str, list]:
        now = utcnow()
        start: list[BotJob] = []
        stop: list[str] = []
        with session_scope() as db:
            links = list(db.scalars(select(MeetingLink)))
            existing = {link.id for link in links}
            for link_id in self._tasks:
                if link_id not in existing:
                    stop.append(link_id)
            capacity = self.settings.max_active_bots - len(self._tasks)
            for link in links:
                running = link.id in self._tasks
                if link.desired_state == DesiredState.STOPPED.value:
                    if running:
                        stop.append(link.id)
                    elif link.state in {s.value for s in ACTIVE_LINK_STATES}:
                        self._set_state(db, link, LinkState.STOPPED, "", actor="system")
                    continue
                if running:
                    continue
                retry_at = link.retry_at
                if retry_at is not None and _aware(retry_at) > now:
                    continue
                if capacity <= 0:
                    if link.state_detail != "waiting_for_capacity":
                        self._set_state(db, link, LinkState.STARTING, "waiting_for_capacity", actor="system")
                    continue
                capacity -= 1
                room = db.get(Room, link.room_id)
                self._set_state(db, link, LinkState.STARTING, "", actor="system")
                start.append(
                    BotJob(
                        link_id=link.id,
                        room_id=link.room_id,
                        room_title=room.title if room else "",
                        provider=link.provider,
                        url=link.url,
                        passcode=link.passcode,
                        display_name=room.bot_display_name if room else self.settings.bot_default_name,
                    )
                )
        return {"start": start, "stop": stop}

    # --------------------------------------------------------------------- run
    async def _run(self, job: BotJob, stop: asyncio.Event) -> None:
        async def on_state(state: str, detail: str) -> None:
            await asyncio.to_thread(self._update, job.link_id, LinkState(state), detail)

        outcome: str
        error: JoinError | None = None
        try:
            outcome = await self.runner.run(job, on_state, stop)
        except JoinError as exc:
            error = exc
            outcome = "error"
        except InvalidMeetingUrl as exc:
            error = JoinError(exc.code, terminal=True)
            outcome = "error"
        except asyncio.CancelledError:
            outcome = "stopped"
        except Exception as exc:
            log.exception("bot crashed link=%s", job.link_id)
            error = JoinError("bot_crashed", terminal=False, message=str(exc)[:200])
            outcome = "error"
        finally:
            self._tasks.pop(job.link_id, None)
            self._stops.pop(job.link_id, None)
        await asyncio.to_thread(self._finish, job.link_id, outcome, error, stop.is_set())
        self.kick()

    def _update(self, link_id: str, state: LinkState, detail: str) -> None:
        with session_scope() as db:
            link = db.get(MeetingLink, link_id)
            if link is None:
                return
            if state == LinkState.LIVE:
                link.attempts = 0
                link.retry_at = None
            self._set_state(db, link, state, detail, actor="bot")

    def _finish(self, link_id: str, outcome: str, error: JoinError | None, stop_requested: bool) -> None:
        with session_scope() as db:
            link = db.get(MeetingLink, link_id)
            if link is None:
                return
            if self._closing and link.desired_state == DesiredState.RUNNING.value:
                # Desired state stays "running" so the bridge resumes on next start.
                self._set_state(db, link, LinkState.STOPPED, "server_restart", actor="system")
                return
            if stop_requested or link.desired_state == DesiredState.STOPPED.value:
                self._set_state(db, link, LinkState.STOPPED, "", actor="bot")
                return
            if outcome == MEETING_ENDED:
                link.desired_state = DesiredState.STOPPED.value
                self._set_state(db, link, LinkState.STOPPED, "meeting_ended", actor="bot")
                return
            if error is None:
                link.desired_state = DesiredState.STOPPED.value
                self._set_state(db, link, LinkState.STOPPED, outcome, actor="bot")
                return
            if error.terminal:
                link.desired_state = DesiredState.STOPPED.value
                self._set_state(db, link, LinkState.FAILED, error.code, actor="bot", extra={"message": str(error)[:200]})
                return
            link.attempts += 1
            if link.attempts > self.settings.bot_max_retries:
                link.desired_state = DesiredState.STOPPED.value
                self._set_state(db, link, LinkState.FAILED, error.code, actor="bot", extra={"message": str(error)[:200], "gave_up": True})
                return
            delay = self.settings.bot_retry_base_seconds * (2 ** (link.attempts - 1))
            link.retry_at = utcnow() + timedelta(seconds=delay)
            self._set_state(db, link, LinkState.RETRYING, error.code, actor="bot", extra={"retry_in_seconds": delay})

    def _set_state(
        self,
        db: Any,
        link: MeetingLink,
        state: LinkState,
        detail: str,
        actor: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        changed = link.state != state.value or link.state_detail != detail
        link.state = state.value
        link.state_detail = detail
        link.state_changed_at = utcnow()
        if not changed:
            return
        payload = {"state": state.value, "detail": detail, **(extra or {})}
        db.add(RoomEvent(room_id=link.room_id, link_id=link.id, actor=actor, kind="link_state", detail=payload))
        db.flush()
        self.bus_publish(link.room_id, {"type": "link", "link": link_public(link)})

    def bus_publish(self, room_id: str, event: dict[str, Any]) -> None:
        # State changes are written from worker threads (asyncio.to_thread).
        if self._event_loop is not None:
            self._event_loop.call_soon_threadsafe(self.bus.publish, room_id, event)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
