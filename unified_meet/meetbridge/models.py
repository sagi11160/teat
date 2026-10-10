from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """ISO-8601 in UTC with an explicit offset (SQLite hands back naive datetimes)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class Provider(str, enum.Enum):
    ZOOM = "zoom"
    GOOGLE_MEET = "google_meet"
    TEAMS = "teams"
    MOCK = "mock"


class DesiredState(str, enum.Enum):
    RUNNING = "running"
    STOPPED = "stopped"


class LinkState(str, enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    JOINING = "joining"
    WAITING_ADMISSION = "waiting_admission"
    LIVE = "live"
    RETRYING = "retrying"
    LEAVING = "leaving"
    STOPPED = "stopped"
    FAILED = "failed"


ACTIVE_LINK_STATES = {
    LinkState.STARTING,
    LinkState.JOINING,
    LinkState.WAITING_ADMISSION,
    LinkState.LIVE,
    LinkState.RETRYING,
    LinkState.LEAVING,
}


def _enum_check(column: str, enum_cls: type[enum.Enum]) -> str:
    values = ",".join(f"'{m.value}'" for m in enum_cls)
    return f"{column} IN ({values})"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    rooms: Mapped[list[Room]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    bot_display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="rooms")
    links: Mapped[list[MeetingLink]] = relationship(
        back_populates="room", cascade="all, delete-orphan", order_by="MeetingLink.created_at"
    )


class MeetingLink(Base):
    __tablename__ = "meeting_links"
    __table_args__ = (
        UniqueConstraint("room_id", "url", name="uq_link_room_url"),
        CheckConstraint(_enum_check("provider", Provider), name="ck_link_provider"),
        CheckConstraint(_enum_check("desired_state", DesiredState), name="ck_link_desired"),
        CheckConstraint(_enum_check("state", LinkState), name="ck_link_state"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    passcode: Mapped[str | None] = mapped_column(String(64))
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False, default=DesiredState.STOPPED.value)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=LinkState.IDLE.value)
    state_detail: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    room: Mapped[Room] = relationship(back_populates="links")


class RoomEvent(Base):
    __tablename__ = "room_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    link_id: Mapped[str | None] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LoginAttempt(Base):
    """Persisted failed-login counter so rate limits survive restarts and multiple workers."""

    __tablename__ = "login_attempts"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "ACTIVE_LINK_STATES",
    "AuthSession",
    "DesiredState",
    "LinkState",
    "LoginAttempt",
    "MeetingLink",
    "Provider",
    "Room",
    "RoomEvent",
    "User",
]
