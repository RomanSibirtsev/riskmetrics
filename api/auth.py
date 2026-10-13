"""Cookie-based authentication dependencies and helpers."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher, Type, exceptions as argon_exceptions
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth_models import AuthSession, AuthUser
from .database import get_session


SESSION_COOKIE = "risk_session"
SESSION_TTL = timedelta(hours=12)
password_hasher = PasswordHasher(type=Type.ID)
_DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if len(normalized) > 320 or normalized.count("@") != 1:
        raise ValueError("Введите корректный email.")
    local, domain = normalized.split("@", 1)
    if not local or not domain or "." not in domain or any(ch.isspace() for ch in normalized):
        raise ValueError("Введите корректный email.")
    return normalized


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, session: Session, user: AuthUser) -> None:
    now = datetime.now(timezone.utc)
    session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    raw_token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + SESSION_TTL,
        )
    )
    session.commit()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=os.getenv("AUTH_COOKIE_SECURE", "false").casefold() == "true",
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=os.getenv("AUTH_COOKIE_SECURE", "false").casefold() == "true",
        samesite="lax",
        path="/",
    )


def verify_password(password_hash: str | None, password: str) -> bool:
    candidate = password_hash or _DUMMY_HASH
    try:
        valid = password_hasher.verify(candidate, password)
    except (argon_exceptions.VerifyMismatchError, argon_exceptions.VerificationError, argon_exceptions.InvalidHashError):
        return False
    return bool(valid and password_hash)


def enforce_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {str(request.base_url).rstrip("/")}
    allowed.update(
        item.strip().rstrip("/")
        for item in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    if origin.rstrip("/") not in allowed:
        raise HTTPException(
            status_code=403,
            detail={"code": "origin_forbidden", "message": "Запрос отклонён."},
        )


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> AuthUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Войдите в аккаунт."},
        )
    now = datetime.now(timezone.utc)
    user = session.scalar(
        select(AuthUser)
        .join(AuthSession, AuthSession.user_id == AuthUser.id)
        .where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.expires_at > now,
            AuthUser.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Войдите в аккаунт."},
        )
    return user


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
