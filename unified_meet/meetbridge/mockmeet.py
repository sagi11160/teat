"""MockMeet: a minimal, self-contained WebRTC meeting platform for automated tests.

It behaves like a third-party platform from the bridge's point of view: its own page,
its own pre-join screen, its own signalling, an optional lobby, and the host can end
the meeting. Mounted only when MB_ENABLE_MOCK_PROVIDER=true.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .hub import EventBus, Hub, Peer

router = APIRouter(prefix="/mock-meet", include_in_schema=False)
_PAGE = (Path(__file__).parent / "web" / "mockmeet.html").read_text(encoding="utf-8")


@dataclass
class _Meeting:
    lobby: bool = False
    ended: bool = False
    waiting: dict[str, asyncio.Future] = field(default_factory=dict)


_hub = Hub(EventBus(), ice_servers=[], max_participants=50)
_meetings: dict[str, _Meeting] = {}


def _meeting(code: str) -> _Meeting:
    return _meetings.setdefault(code, _Meeting())


class MeetingConfig(BaseModel):
    lobby: bool


@router.get("/{code}", response_class=HTMLResponse)
def page(code: str):
    return HTMLResponse(_PAGE.replace("__CODE__", json.dumps(code)))


@router.post("/api/{code}/config")
def configure(code: str, body: MeetingConfig):
    m = _meeting(code)
    m.lobby = body.lobby
    m.ended = False
    return {"lobby": m.lobby}


@router.post("/api/{code}/admit-all")
def admit_all(code: str):
    m = _meeting(code)
    for fut in m.waiting.values():
        if not fut.done():
            fut.set_result(True)
    return {"admitted": len(m.waiting)}


@router.post("/api/{code}/deny-all")
def deny_all(code: str):
    m = _meeting(code)
    for fut in m.waiting.values():
        if not fut.done():
            fut.set_result(False)
    return {"denied": len(m.waiting)}


@router.post("/api/{code}/end")
async def end(code: str):
    m = _meeting(code)
    m.ended = True
    peers: list[Peer] = _hub.peers(code)
    for p in peers:
        await p.send({"type": "ended"})
    return {"ended": len(peers)}


@router.get("/api/{code}/participants")
def participants(code: str):
    return _hub.snapshot(code)


@router.websocket("/ws/{code}")
async def socket(websocket: WebSocket, code: str):
    await websocket.accept()
    m = _meeting(code)
    name = (websocket.query_params.get("name") or "guest")[:60]
    if m.ended:
        await websocket.send_json({"type": "ended"})
        await websocket.close()
        return
    if m.lobby:
        await websocket.send_json({"type": "lobby"})
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        key = f"{id(websocket)}"
        m.waiting[key] = fut
        try:
            admitted = await fut
        finally:
            m.waiting.pop(key, None)
        if not admitted:
            await websocket.send_json({"type": "denied"})
            await websocket.close()
            return

    async def send(msg: dict) -> None:
        await websocket.send_json(msg)

    peer = await _hub.join(code, name, "mock", send)
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            await _hub.handle(peer, msg)
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        await _hub.leave(peer)
