from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..bridge import Supervisor
from ..config import Settings
from ..db import get_db
from ..hub import EventBus, Hub
from ..models import Room, User
from ..providers import ProviderRegistry
from ..security import resolve_session

SESSION_COOKIE = "mb_session"
CSRF_HEADER = "x-meetbridge"


@dataclass
class Services:
    settings: Settings
    hub: Hub
    bus: EventBus
    registry: ProviderRegistry
    supervisor: Supervisor


def services(request: Request) -> Services:
    return request.app.state.services


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def require_csrf(request: Request) -> None:
    """Mutating requests must carry a custom header; browsers refuse to send one
    cross-site without a CORS preflight, which this app never grants."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get(CSRF_HEADER) != "1":
        raise api_error(403, "csrf_header_missing", "Missing X-MeetBridge header")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise api_error(401, "not_authenticated", "Please sign in")
    return user


def owned_room(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Room:
    room = db.get(Room, room_id)
    # 404 for both "missing" and "not yours" so room IDs cannot be probed.
    if room is None or room.owner_id != user.id:
        raise api_error(404, "room_not_found", "Room not found")
    return room
