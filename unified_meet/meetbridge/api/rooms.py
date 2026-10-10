from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..bridge import link_public
from ..db import get_db
from ..models import DesiredState, LinkState, MeetingLink, Room, RoomEvent, User, utcnow
from ..providers import InvalidMeetingUrl
from ..security import new_invite_token
from .deps import Services, api_error, current_user, owned_room, require_csrf, services

router = APIRouter(prefix="/api/rooms", tags=["rooms"], dependencies=[Depends(require_csrf)])


class RoomIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    bot_display_name: str | None = Field(default=None, max_length=60)


class RoomPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    bot_display_name: str | None = Field(default=None, min_length=1, max_length=60)


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    passcode: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=80)


class LinkPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    passcode: str | None = Field(default=None, max_length=64)


def room_public(room: Room, svc: Services) -> dict:
    base = svc.settings.public_base_url.rstrip("/")
    return {
        "id": room.id,
        "title": room.title,
        "bot_display_name": room.bot_display_name,
        "invite_url": f"{base}/j/{room.invite_token}",
        "call_url": f"{base}/call/{room.invite_token}",
        "created_at": room.created_at.isoformat(),
        "links": [link_public(link) for link in room.links],
        "participants": svc.hub.snapshot(room.id),
    }


def _event(db: Session, room: Room, user: User, kind: str, detail: dict, link_id: str | None = None) -> None:
    db.add(RoomEvent(room_id=room.id, link_id=link_id, actor=f"user:{user.id}", kind=kind, detail=detail))


def _clean(text: str) -> str:
    return " ".join(text.split())


@router.get("")
def list_rooms(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"), svc: Services = Depends(services)):
    rooms = db.scalars(select(Room).where(Room.owner_id == user.id).order_by(Room.created_at.desc()))
    return [room_public(r, svc) for r in rooms]


@router.post("", status_code=201)
def create_room(
    body: RoomIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function"), svc: Services = Depends(services)
):
    title = _clean(body.title)
    if not title:
        raise api_error(422, "title_required", "Room title is required")
    bot_name = _clean(body.bot_display_name or "") or f"{svc.settings.bot_default_name} · {title}"[:60]
    room = Room(owner_id=user.id, title=title, invite_token=new_invite_token(), bot_display_name=bot_name)
    db.add(room)
    db.flush()
    _event(db, room, user, "room_created", {"title": title})
    db.refresh(room)
    return room_public(room, svc)


@router.get("/{room_id}")
def get_room(room: Room = Depends(owned_room), svc: Services = Depends(services)):
    return room_public(room, svc)


@router.patch("/{room_id}")
def update_room(
    body: RoomPatch,
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    changes = {}
    if body.title is not None:
        title = _clean(body.title)
        if not title:
            raise api_error(422, "title_required", "Room title is required")
        room.title = changes["title"] = title
    if body.bot_display_name is not None:
        name = _clean(body.bot_display_name)
        if not name:
            raise api_error(422, "bot_name_required", "Bridge display name is required")
        room.bot_display_name = changes["bot_display_name"] = name
    if changes:
        _event(db, room, user, "room_updated", changes)
    return room_public(room, svc)


@router.post("/{room_id}/rotate-invite")
async def rotate_invite(
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    room.invite_token = new_invite_token()
    _event(db, room, user, "invite_rotated", {})
    db.flush()
    # Old links stop working; people already in the call keep their session.
    return room_public(room, svc)


@router.delete("/{room_id}", status_code=204)
async def delete_room(
    room: Room = Depends(owned_room), db: Session = Depends(get_db, scope="function"), svc: Services = Depends(services)
):
    room_id = room.id
    db.delete(room)
    db.commit()
    await svc.hub.close_room(room_id)
    svc.supervisor.kick()


# ------------------------------------------------------------------------ links
@router.post("/{room_id}/links", status_code=201)
def add_link(
    body: LinkIn,
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    count = db.scalar(select(func.count()).select_from(MeetingLink).where(MeetingLink.room_id == room.id))
    if count >= svc.settings.max_links_per_room:
        raise api_error(409, "too_many_links", f"A room can bridge at most {svc.settings.max_links_per_room} meetings")
    try:
        meeting = svc.registry.parse(body.url, body.passcode)
    except InvalidMeetingUrl as exc:
        raise api_error(422, exc.code, str(exc)) from exc
    link = MeetingLink(
        room_id=room.id,
        provider=meeting.provider.value,
        url=meeting.url,
        label=_clean(body.label),
        passcode=meeting.passcode,
        desired_state=DesiredState.STOPPED.value,
        state=LinkState.IDLE.value,
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError as exc:
        raise api_error(409, "link_exists", "This meeting is already linked to the room") from exc
    _event(db, room, user, "link_added", {"provider": link.provider}, link.id)
    return link_public(link)


def _owned_link(room: Room, link_id: str, db: Session) -> MeetingLink:
    link = db.get(MeetingLink, link_id)
    if link is None or link.room_id != room.id:
        raise api_error(404, "link_not_found", "Meeting link not found")
    return link


@router.patch("/{room_id}/links/{link_id}")
def update_link(
    link_id: str,
    body: LinkPatch,
    room: Room = Depends(owned_room),
    db: Session = Depends(get_db, scope="function"),
):
    link = _owned_link(room, link_id, db)
    if body.label is not None:
        link.label = _clean(body.label)
    if body.passcode is not None:
        link.passcode = body.passcode.strip() or None
    return link_public(link)


@router.delete("/{room_id}/links/{link_id}", status_code=204)
def delete_link(
    link_id: str,
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    link = _owned_link(room, link_id, db)
    _event(db, room, user, "link_removed", {"provider": link.provider}, link.id)
    db.delete(link)
    db.commit()
    svc.supervisor.kick()


def _set_desired(link: MeetingLink, desired: DesiredState) -> None:
    link.desired_state = desired.value
    if desired == DesiredState.RUNNING:
        link.attempts = 0
        link.retry_at = None
        if link.state in (LinkState.IDLE.value, LinkState.STOPPED.value, LinkState.FAILED.value):
            link.state = LinkState.STARTING.value
            link.state_detail = ""
            link.state_changed_at = utcnow()
    elif link.state not in (LinkState.IDLE.value, LinkState.STOPPED.value, LinkState.FAILED.value):
        link.state = LinkState.LEAVING.value
        link.state_detail = ""
        link.state_changed_at = utcnow()


@router.post("/{room_id}/links/{link_id}/start")
def start_link(
    link_id: str,
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    link = _owned_link(room, link_id, db)
    if link.desired_state != DesiredState.RUNNING.value:
        _set_desired(link, DesiredState.RUNNING)
        _event(db, room, user, "bridge_start_requested", {"provider": link.provider}, link.id)
        db.commit()
        svc.supervisor.kick()
    return link_public(link)


@router.post("/{room_id}/links/{link_id}/stop")
def stop_link(
    link_id: str,
    room: Room = Depends(owned_room),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
    svc: Services = Depends(services),
):
    link = _owned_link(room, link_id, db)
    if link.desired_state != DesiredState.STOPPED.value:
        _set_desired(link, DesiredState.STOPPED)
        _event(db, room, user, "bridge_stop_requested", {"provider": link.provider}, link.id)
        db.commit()
        svc.supervisor.kick()
    return link_public(link)


@router.get("/{room_id}/events")
def room_events(
    room: Room = Depends(owned_room),
    db: Session = Depends(get_db, scope="function"),
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1),
):
    stmt = select(RoomEvent).where(RoomEvent.room_id == room.id)
    if before:
        stmt = stmt.where(RoomEvent.id < before)
    rows = db.scalars(stmt.order_by(RoomEvent.id.desc()).limit(limit))
    return [
        {
            "id": e.id,
            "kind": e.kind,
            "link_id": e.link_id,
            "actor": e.actor.split(":", 1)[0],
            "detail": e.detail,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]
