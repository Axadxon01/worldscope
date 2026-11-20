"""Sign-in, first-admin setup, invitations (accepting) and the signed-in account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from worldscope.api.deps import RedisDep, SessionDep, SettingsDep
from worldscope.api.schemas import (
    AcceptInvitationIn,
    InvitationPreviewOut,
    InvitationTokenIn,
    LoginIn,
    MeOut,
    PasswordChangeIn,
    SetupIn,
    SetupStateOut,
    SignedInOut,
    UserOut,
)
from worldscope.api.security import ClientIP, CurrentAuth, UserAgent, require_internal_key
from worldscope.auth import service
from worldscope.auth.service import INVALID_INVITATION, AuthError, SignedIn
from worldscope.db.models import User

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_internal_key)], tags=["auth"])


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        disabled=user.disabled_at is not None,
    )


def signed_in_out(signed_in: SignedIn) -> SignedInOut:
    return SignedInOut(
        token=signed_in.token, expires_at=signed_in.expires_at, user=user_out(signed_in.user)
    )


@router.get("/auth/setup")
async def setup_state(db: SessionDep, settings: SettingsDep) -> SetupStateOut:
    return SetupStateOut(state=await service.setup_state(db, settings))


@router.post("/auth/setup", status_code=status.HTTP_201_CREATED)
async def complete_setup(
    body: SetupIn,
    db: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    ip: ClientIP,
    agent: UserAgent,
) -> SignedInOut:
    signed_in = await service.complete_setup(
        db, redis, settings, body.code, body.email, body.display_name, body.password, ip, agent
    )
    return signed_in_out(signed_in)


@router.post("/auth/login")
async def login(
    body: LoginIn,
    db: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    ip: ClientIP,
    agent: UserAgent,
) -> SignedInOut:
    signed_in = await service.login(db, redis, settings, body.email, body.password, ip, agent)
    return signed_in_out(signed_in)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(context: CurrentAuth, db: SessionDep, ip: ClientIP) -> Response:
    await service.end_session(db, context, ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me")
async def me(context: CurrentAuth) -> MeOut:
    return MeOut(user=user_out(context.user), session_expires_at=context.expires_at)


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeIn, context: CurrentAuth, db: SessionDep, redis: RedisDep, ip: ClientIP
) -> Response:
    await service.change_password(db, redis, context, body.current_password, body.new_password, ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/preview")
async def preview_invitation(
    body: InvitationTokenIn, db: SessionDep, settings: SettingsDep
) -> InvitationPreviewOut:
    # The token travels in the body, never the URL, so it stays out of access logs.
    invitation = await service.find_invitation(db, settings, body.token)
    if invitation is None:
        raise AuthError(404, INVALID_INVITATION)
    return InvitationPreviewOut(
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )


@router.post("/invitations/accept", status_code=status.HTTP_201_CREATED)
async def accept_invitation(
    body: AcceptInvitationIn,
    db: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    ip: ClientIP,
    agent: UserAgent,
) -> SignedInOut:
    signed_in = await service.accept_invitation(
        db, redis, settings, body.token, body.display_name, body.password, ip, agent
    )
    return signed_in_out(signed_in)
