import hashlib
import secrets
import time
from typing import Optional, Tuple
from fastapi import Request, Response

from app import config
from app.db import get_db


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_or_create_session(
    request: Request, response: Optional[Response] = None
) -> Tuple[str, str, bool]:
    """
    Returns (raw_token, id_hash, is_new).
    If valid cookie exists and not expired, extends expiry (sliding expiry) and returns it.
    Otherwise, generates a new session and sets the cookie on response if provided.
    """
    cookie_token = request.cookies.get(config.COOKIE_NAME)
    now = time.time()
    new_expires_at = now + config.SESSION_TTL_SECONDS

    if cookie_token:
        token_hash = hash_session_token(cookie_token)
        async with get_db() as conn:
            row = await conn.execute_fetchall(
                "SELECT id_hash, expires_at FROM sessions WHERE id_hash = ?",
                (token_hash,),
            )
            if row and row[0]["expires_at"] > now:
                # Valid existing session: extend expiry (sliding expiry)
                await conn.execute(
                    """
                    UPDATE sessions 
                    SET last_active_at = ?, expires_at = ?
                    WHERE id_hash = ?
                    """,
                    (now, new_expires_at, token_hash),
                )
                await conn.commit()
                if response:
                    _set_session_cookie(response, cookie_token)
                return cookie_token, token_hash, False

    # Create new session
    raw_token = secrets.token_urlsafe(32)
    id_hash = hash_session_token(raw_token)

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id_hash, created_at, last_active_at, expires_at, scan_state)
            VALUES (?, ?, ?, ?, 'idle')
            """,
            (id_hash, now, now, new_expires_at),
        )
        await conn.commit()

    if response:
        _set_session_cookie(response, raw_token)

    return raw_token, id_hash, True


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=raw_token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite=config.COOKIE_SAMESITE,
        secure=config.COOKIE_SECURE,
        path="/",
    )


async def delete_session(raw_token: str, response: Optional[Response] = None) -> None:
    id_hash = hash_session_token(raw_token)
    async with get_db() as conn:
        await conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        await conn.commit()

    if response:
        response.delete_cookie(
            key=config.COOKIE_NAME,
            path="/",
            httponly=True,
            samesite=config.COOKIE_SAMESITE,
            secure=config.COOKIE_SECURE,
        )
