"""Request authentication for /api/v1.

Two layers:
1. The internal key: only the WorldScope web app may call the API. Browsers never talk
   to the API directly; the web app's server forwards their requests with this key.
2. The session: `Authorization: Bearer <session token>`, which the web app reads from
   its own httpOnly cookie.

The client's IP is taken from `X-Client-IP` only on requests that carry the internal
key, because only the web app can be trusted to report it.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from worldscope.api.deps import SessionDep, SettingsDep
from worldscope.auth.service import AuthContext, authenticate
from worldscope.db.rls import bind_user


def require_internal_key(request: Request, settings: SettingsDep) -> None:
    expected = settings.internal_api_key
    if expected is None:  # development and tests without a key configured
        return
    given = request.headers.get("x-internal-key", "")
    if not hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    # Reached only through require_internal_key, so the header comes from the web app.
    forwarded = _valid_ip(request.headers.get("x-client-ip"))
    if forwarded:
        return forwarded
    return _valid_ip(request.client.host if request.client else None)


def user_agent(request: Request) -> str | None:
    return request.headers.get("x-client-user-agent") or request.headers.get("user-agent")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def current_auth(request: Request, db: SessionDep, settings: SettingsDep) -> AuthContext:
    context = await authenticate(db, settings, bearer_token(request))
    if context is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    await bind_user(db, context.user.id)
    return context


CurrentAuth = Annotated[AuthContext, Depends(current_auth)]


def require_admin(context: CurrentAuth) -> AuthContext:
    if context.user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only administrators can do this.")
    return context


AdminAuth = Annotated[AuthContext, Depends(require_admin)]
ClientIP = Annotated[str | None, Depends(client_ip)]
UserAgent = Annotated[str | None, Depends(user_agent)]
