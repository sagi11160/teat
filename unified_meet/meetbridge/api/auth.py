from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (
    clear_login_failures,
    create_session,
    hash_password,
    login_allowed,
    record_login_failure,
    revoke_session,
    verify_password,
)
from .deps import SESSION_COOKIE, Services, api_error, current_user, require_csrf, services

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(require_csrf)])
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("display name is required")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


def user_public(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


def _set_cookie(response: Response, token: str, svc: Services) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=svc.settings.session_ttl_hours * 3600,
        httponly=True,
        secure=svc.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=201)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db), svc: Services = Depends(services)):
    if not svc.settings.allow_registration:
        raise api_error(403, "registration_closed", "Registration is disabled on this server")
    exists = db.scalar(select(func.count()).select_from(User).where(User.email == body.email))
    if exists:
        raise api_error(409, "email_taken", "An account with this email already exists")
    user = User(email=body.email, display_name=body.display_name, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    _set_cookie(response, create_session(db, user, svc.settings.session_ttl_hours), svc)
    return user_public(user)


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    svc: Services = Depends(services),
):
    email = body.email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    key = f"{email}|{client_ip}"
    if not login_allowed(db, key):
        raise api_error(429, "too_many_attempts", "Too many failed attempts. Try again in a few minutes")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(user.password_hash, body.password):
        record_login_failure(db, key)
        db.commit()
        raise api_error(401, "invalid_credentials", "Incorrect email or password")
    clear_login_failures(db, key)
    _set_cookie(response, create_session(db, user, svc.settings.session_ttl_hours), svc)
    return user_public(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_public(user)
