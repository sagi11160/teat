"""In-memory room hub: membership and WebRTC signalling relay.

Media flows peer-to-peer (full mesh) between room members; each external platform is
a single member (its bridge bot), so a room with three platforms and a handful of
browser guests stays well inside what a mesh handles. The hub lives in one process;
running several app instances requires sticky routing per room (see README).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .models import utcnow

log = logging.getLogger("meetbridge.hub")

MAX_SIGNAL_BYTES = 64 * 1024
SendFn = Callable[[dict[str, Any]], Awaitable[None]]


class RoomFull(Exception):
    pass


@dataclass
class Peer:
    id: str
    room_id: str
    name: str
    source: str
    send: SendFn
    link_id: str | None = None
    audio: bool = True
    video: bool = True
    joined_at: str = field(default_factory=lambda: utcnow().isoformat())

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "link_id": self.link_id,
            "audio": self.audio,
            "video": self.video,
            "joined_at": self.joined_at,
        }


class EventBus:
    """Per-room fan-out of status events to organizer dashboards (SSE)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, room_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.setdefault(room_id, set()).add(q)
        return q

    def unsubscribe(self, room_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(room_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(room_id, None)

    def publish(self, room_id: str, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(room_id, ())):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)


class Hub:
    def __init__(self, bus: EventBus, ice_servers: list[dict[str, Any]], max_participants: int):
        self._rooms: dict[str, dict[str, Peer]] = {}
        self._lock = asyncio.Lock()
        self.bus = bus
        self.ice_servers = ice_servers
        self.max_participants = max_participants

    def peers(self, room_id: str) -> list[Peer]:
        return list(self._rooms.get(room_id, {}).values())

    def snapshot(self, room_id: str) -> list[dict[str, Any]]:
        return [p.public() for p in self.peers(room_id)]

    async def join(
        self, room_id: str, name: str, source: str, send: SendFn, link_id: str | None = None
    ) -> Peer:
        async with self._lock:
            members = self._rooms.setdefault(room_id, {})
            if len(members) >= self.max_participants:
                raise RoomFull()
            peer = Peer(id=secrets.token_hex(8), room_id=room_id, name=name, source=source, send=send, link_id=link_id)
            others = list(members.values())
            members[peer.id] = peer
        for other in others:
            await self._deliver(other, {"type": "peer-joined", "peer": peer.public()})
        await self._deliver(
            peer,
            {
                "type": "welcome",
                "peer_id": peer.id,
                "peers": [o.public() for o in others],
                "ice_servers": self.ice_servers,
            },
        )
        log.info("peer joined room=%s peer=%s source=%s", room_id, peer.id, source)
        self.bus.publish(room_id, {"type": "participants", "participants": self.snapshot(room_id)})
        return peer

    async def leave(self, peer: Peer) -> None:
        async with self._lock:
            members = self._rooms.get(peer.room_id)
            if not members or members.pop(peer.id, None) is None:
                return
            others = list(members.values())
            if not members:
                self._rooms.pop(peer.room_id, None)
        for other in others:
            await self._deliver(other, {"type": "peer-left", "peer_id": peer.id})
        log.info("peer left room=%s peer=%s", peer.room_id, peer.id)
        self.bus.publish(peer.room_id, {"type": "participants", "participants": self.snapshot(peer.room_id)})

    async def handle(self, peer: Peer, msg: Any) -> None:
        """Processes one client message. Invalid messages are answered with an error."""
        if not isinstance(msg, dict):
            await self._deliver(peer, {"type": "error", "code": "bad_message"})
            return
        kind = msg.get("type")
        if kind == "signal":
            target_id = msg.get("to")
            data = msg.get("data")
            target = self._rooms.get(peer.room_id, {}).get(target_id) if isinstance(target_id, str) else None
            if target is None or not isinstance(data, dict):
                await self._deliver(peer, {"type": "error", "code": "unknown_peer"})
                return
            await self._deliver(target, {"type": "signal", "from": peer.id, "data": data})
        elif kind == "media-state":
            peer.audio = bool(msg.get("audio", peer.audio))
            peer.video = bool(msg.get("video", peer.video))
            update = {"type": "media-state", "peer_id": peer.id, "audio": peer.audio, "video": peer.video}
            for other in self.peers(peer.room_id):
                if other.id != peer.id:
                    await self._deliver(other, update)
            self.bus.publish(peer.room_id, {"type": "participants", "participants": self.snapshot(peer.room_id)})
        elif kind == "ping":
            await self._deliver(peer, {"type": "pong"})
        else:
            await self._deliver(peer, {"type": "error", "code": "unknown_type"})

    async def close_room(self, room_id: str) -> None:
        async with self._lock:
            members = self._rooms.pop(room_id, {})
        for peer in members.values():
            await self._deliver(peer, {"type": "room-closed"})

    async def _deliver(self, peer: Peer, msg: dict[str, Any]) -> None:
        try:
            await peer.send(msg)
        except Exception as exc:  # a dead socket must not break the others
            log.debug("deliver to %s failed: %s", peer.id, exc)
