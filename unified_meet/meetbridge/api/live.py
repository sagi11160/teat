from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..db import get_db, session_scope
from ..hub import MAX_SIGNAL_BYTES, RoomFull
from ..models import Room
from ..security import resolve_session
from .deps import SESSION_COOKIE, Services, api_error, services

log = logging.getLogger("meetbridge.live")
router = APIRouter(tags=["live"])

SOURCE_WEB = "web"


def _room_by_token(db, token: str) -> Room | None:
    if not token or len(token) > 64:
        return None
    return db.scalar(select(Room).where(Room.invite_token == token))


@router.get("/api/invite/{token}")
def invite_info(token: str, db=Depends(get_db, scope="function"), svc: Services = Depends(services)):
    """Public: what an invitee needs to pick how to join. Never exposes passcodes or IDs."""
    room = _room_by_token(db, token)
    if room is None:
        raise api_error(404, "invite_not_found", "This invitation link is not valid (it may have been replaced)")
    options = []
    for link in room.links:
        adapter = svc.registry.get(link.provider)
        meeting = adapter.parse(link.url, link.passcode)
        options.append(
            {
                "provider": link.provider,
                "provider_name": adapter.display_name,
                "label": link.label,
                "join_url": adapter.public_join_url(meeting),
                "bridged": link.state == "live",
                "passcode": link.passcode if link.provider == "zoom" else None,
            }
        )
    return {
        "title": room.title,
        "options": options,
        "call_url": f"/call/{token}",
        "participants": len(svc.hub.peers(room.id)),
    }


@router.websocket("/ws/call/{token}")
async def call_socket(websocket: WebSocket, token: str):
    svc: Services = websocket.app.state.services
    with session_scope() as db:
        room = _room_by_token(db, token)
        room_id = room.id if room else None
    name = " ".join((websocket.query_params.get("name") or "").split())[:60]
    await websocket.accept()
    if room_id is None:
        await websocket.send_json({"type": "error", "code": "invite_not_found"})
        await websocket.close(code=4404)
        return
    if not name:
        await websocket.send_json({"type": "error", "code": "name_required"})
        await websocket.close(code=4400)
        return

    send_lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with send_lock:
            await websocket.send_json(msg)

    try:
        peer = await svc.hub.join(room_id, name, SOURCE_WEB, send)
    except RoomFull:
        await websocket.send_json({"type": "error", "code": "room_full"})
        await websocket.close(code=4429)
        return
    try:
        while True:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=75)
            if len(raw) > MAX_SIGNAL_BYTES:
                await send({"type": "error", "code": "message_too_large"})
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                await send({"type": "error", "code": "bad_json"})
                continue
            if isinstance(msg, dict) and msg.get("type") == "leave":
                break
            await svc.hub.handle(peer, msg)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        log.exception("call socket error")
    finally:
        await svc.hub.leave(peer)
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/api/rooms/{room_id}/stream")
async def room_stream(room_id: str, request: Request, svc: Services = Depends(services)):
    """Server-sent events for the organizer dashboard: link states and participants."""
    # Authorised with a short-lived DB session so no transaction stays open for the
    # lifetime of the stream.
    with session_scope() as db:
        user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
        room = db.get(Room, room_id)
        allowed = user is not None and room is not None and room.owner_id == user.id
    if user is None:
        raise api_error(401, "not_authenticated", "Please sign in")
    if not allowed:
        raise api_error(404, "room_not_found", "Room not found")
    queue = svc.bus.subscribe(room_id)

    async def events():
        try:
            snapshot = {"type": "participants", "participants": svc.hub.snapshot(room_id)}
            yield f"data: {json.dumps(snapshot)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            svc.bus.unsubscribe(room_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
