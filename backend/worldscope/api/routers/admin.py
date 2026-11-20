"""Administration: invitations and accounts. Administrators only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from worldscope.api.deps import SessionDep, SettingsDep
from worldscope.api.routers.auth import user_out
from worldscope.api.schemas import InvitationCreatedOut, InvitationIn, InvitationOut, UserOut
from worldscope.api.security import AdminAuth, ClientIP, require_internal_key
from worldscope.auth import service
from worldscope.db.models import Invitation

router = APIRouter(
    prefix="/api/v1/admin", dependencies=[Depends(require_internal_key)], tags=["admin"]
)


def invitation_out(invitation: Invitation, invited_by: str | None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=service.invitation_status(invitation),
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        invited_by=invited_by,
    )


@router.get("/invitations")
async def list_invitations(_: AdminAuth, db: SessionDep) -> list[InvitationOut]:
    return [invitation_out(inv, name) for inv, name in await service.list_invitations(db)]


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: InvitationIn, context: AdminAuth, db: SessionDep, settings: SettingsDep, ip: ClientIP
) -> InvitationCreatedOut:
    invitation, token = await service.create_invitation(
        db, settings, context.user, body.email, body.role, ip
    )
    return InvitationCreatedOut(
        invitation=invitation_out(invitation, context.user.display_name), token=token
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID, context: AdminAuth, db: SessionDep, ip: ClientIP
) -> Response:
    await service.revoke_invitation(db, context.user, invitation_id, ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users")
async def list_users(_: AdminAuth, db: SessionDep) -> list[UserOut]:
    return [user_out(user) for user in await service.list_users(db)]


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: uuid.UUID, context: AdminAuth, db: SessionDep, ip: ClientIP
) -> UserOut:
    return user_out(await service.set_disabled(db, context.user, user_id, True, ip))


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: uuid.UUID, context: AdminAuth, db: SessionDep, ip: ClientIP
) -> UserOut:
    return user_out(await service.set_disabled(db, context.user, user_id, False, ip))
