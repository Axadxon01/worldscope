"""Sign-in, sessions, first-admin setup, invitations and account administration.

Every function that changes state commits its own transaction, including the audit
entry that records it. Errors are raised as AuthError with a message written for the
person using the product; nothing here reveals whether an email has an account.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from worldscope.auth import limits
from worldscope.auth.passwords import (
    dummy_hash,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from worldscope.auth.tokens import hash_token, new_token, plausible
from worldscope.core.config import Settings
from worldscope.db.models import Invitation, User, UserSession
from worldscope.services import audit

SESSION_REFRESH_EVERY = timedelta(minutes=30)
# Any constant works; it only has to be the same for every API process.
SETUP_LOCK_ID = 7_424_191_001

SetupState = Literal["open", "closed", "unconfigured"]
InvitationStatus = Literal["pending", "used", "revoked", "expired"]


class AuthError(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class SignedIn:
    user: User
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_id: uuid.UUID
    expires_at: datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def _key(settings: Settings) -> bytes:
    return settings.secret_key.get_secret_value().encode()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _minutes(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


async def _limit(redis: Redis, rule: limits.Rule, subject: str) -> None:
    try:
        await limits.hit(redis, rule, subject)
    except limits.LimitExceeded as exc:
        raise AuthError(
            429, f"Too many attempts. Try again in {_minutes(exc.retry_after)}.", exc.retry_after
        ) from exc
    except limits.LimitsUnavailable as exc:
        raise AuthError(503, "Sign-in is unavailable right now. Try again shortly.") from exc


def _check_password(password: str, email: str) -> None:
    problem = password_problem(password, email)
    if problem:
        raise AuthError(422, problem)


# --- Sessions -------------------------------------------------------------------------


def start_session(
    db: AsyncSession, settings: Settings, user: User, ip: str | None, user_agent: str | None
) -> SignedIn:
    token = new_token()
    now = utcnow()
    expires_at = now + timedelta(days=settings.session_idle_days)
    db.add(
        UserSession(
            token_hash=hash_token(token, _key(settings)),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            ip=ip,
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    return SignedIn(user=user, token=token, expires_at=expires_at)


async def authenticate(
    db: AsyncSession, settings: Settings, token: str | None
) -> AuthContext | None:
    """The signed-in user for a session token, or None. Extends an active session."""
    if not plausible(token):
        return None
    assert token is not None
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(token, _key(settings)))
        )
    ).first()
    if row is None:
        return None
    session, user = row
    now = utcnow()
    hard_limit = session.created_at + timedelta(days=settings.session_max_days)
    if session.expires_at <= now or hard_limit <= now or user.disabled_at is not None:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at >= SESSION_REFRESH_EVERY:
        session.last_seen_at = now
        session.expires_at = min(now + timedelta(days=settings.session_idle_days), hard_limit)
        await db.commit()
    return AuthContext(user=user, session_id=session.id, expires_at=session.expires_at)


async def end_session(db: AsyncSession, context: AuthContext, ip: str | None) -> None:
    await db.execute(delete(UserSession).where(UserSession.id == context.session_id))
    audit.record(db, "logout", user_id=context.user.id, ip=ip)
    await db.commit()


# --- Sign-in ---------------------------------------------------------------------------


async def login(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> SignedIn:
    email = normalise_email(email)
    await _limit(redis, limits.LOGIN_PER_IP, ip or "unknown")
    try:
        await limits.check_account(redis, email)
    except limits.LimitExceeded as exc:
        raise AuthError(
            429,
            f"Too many failed attempts for this account. Try again in {_minutes(exc.retry_after)}.",
            exc.retry_after,
        ) from exc
    except limits.LimitsUnavailable as exc:
        raise AuthError(503, "Sign-in is unavailable right now. Try again shortly.") from exc

    user = await db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(user.password_hash if user else dummy_hash(), password)
    if user is None or not password_ok or user.disabled_at is not None:
        try:
            await limits.record_failure(redis, email)
        except limits.LimitsUnavailable as exc:
            raise AuthError(503, "Sign-in is unavailable right now. Try again shortly.") from exc
        reason = "unknown_email" if user is None else "disabled" if password_ok else "bad_password"
        audit.record(
            db,
            "login_failed",
            user_id=user.id if user else None,
            ip=ip,
            details={"reason": reason},
        )
        await db.commit()
        raise AuthError(401, "Email or password is incorrect.")

    try:
        await limits.clear_failures(redis, email)
    except limits.LimitsUnavailable as exc:
        raise AuthError(503, "Sign-in is unavailable right now. Try again shortly.") from exc
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    signed_in = start_session(db, settings, user, ip, user_agent)
    audit.record(db, "login", user_id=user.id, ip=ip)
    await db.commit()
    return signed_in


async def change_password(
    db: AsyncSession,
    redis: Redis,
    context: AuthContext,
    current_password: str,
    new_password: str,
    ip: str | None,
) -> None:
    user = context.user
    await _limit(redis, limits.PASSWORD_PER_USER, str(user.id))
    if not verify_password(user.password_hash, current_password):
        audit.record(db, "password_change_failed", user_id=user.id, ip=ip)
        await db.commit()
        raise AuthError(403, "Your current password is not correct.")
    _check_password(new_password, user.email)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    # Every other signed-in browser is signed out.
    await db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.id != context.session_id
        )
    )
    audit.record(db, "password_changed", user_id=user.id, ip=ip)
    await db.commit()


# --- First administrator ---------------------------------------------------------------


async def setup_state(db: AsyncSession, settings: Settings) -> SetupState:
    if await db.scalar(select(select(User.id).exists())):
        return "closed"
    return "open" if settings.setup_code else "unconfigured"


async def complete_setup(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    code: str,
    email: str,
    display_name: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> SignedIn:
    await _limit(redis, limits.SETUP_PER_IP, ip or "unknown")
    # Serialise concurrent attempts so two people cannot both become the first admin.
    await db.execute(select(func.pg_advisory_xact_lock(SETUP_LOCK_ID)))
    state = await setup_state(db, settings)
    if state != "open":
        await db.rollback()
        raise AuthError(409, "Setup is closed. Sign in, or ask an administrator for an invitation.")
    assert settings.setup_code is not None
    if not hmac.compare_digest(
        code.strip().encode(), settings.setup_code.get_secret_value().encode()
    ):
        audit.record(db, "setup_failed", ip=ip)
        await db.commit()
        raise AuthError(403, "The setup code is not correct.")
    email = normalise_email(email)
    _check_password(password, email)
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role="admin",
        last_login_at=utcnow(),
        password_changed_at=utcnow(),
    )
    db.add(user)
    await db.flush()
    signed_in = start_session(db, settings, user, ip, user_agent)
    audit.record(db, "setup_completed", user_id=user.id, ip=ip)
    await db.commit()
    return signed_in


async def create_cli_invitation(
    db: AsyncSession, settings: Settings, email: str, role: str
) -> tuple[Invitation, str]:
    """For hosts with a shell: an invitation that needs no signed-in administrator."""
    return await _new_invitation(db, settings, email, role, invited_by=None, ip=None)


# --- Invitations -----------------------------------------------------------------------


def invitation_status(invitation: Invitation, now: datetime | None = None) -> InvitationStatus:
    now = now or utcnow()
    if invitation.used_at is not None:
        return "used"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


async def _new_invitation(
    db: AsyncSession,
    settings: Settings,
    email: str,
    role: str,
    invited_by: uuid.UUID | None,
    ip: str | None,
) -> tuple[Invitation, str]:
    email = normalise_email(email)
    if await db.scalar(select(select(User.id).where(User.email == email).exists())):
        raise AuthError(409, "An account with this email already exists.")
    now = utcnow()
    # A new invitation replaces any earlier one still open for the same address.
    await db.execute(
        update(Invitation)
        .where(
            Invitation.email == email,
            Invitation.used_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .values(revoked_at=now)
    )
    token = new_token()
    invitation = Invitation(
        email=email,
        role=role,
        token_hash=hash_token(token, _key(settings)),
        invited_by=invited_by,
        created_at=now,
        expires_at=now + timedelta(days=settings.invitation_days),
    )
    db.add(invitation)
    await db.flush()
    audit.record(
        db,
        "invitation_created",
        user_id=invited_by,
        target=f"invitation:{invitation.id}",
        ip=ip,
        details={"role": role},
    )
    await db.commit()
    return invitation, token


async def create_invitation(
    db: AsyncSession, settings: Settings, admin: User, email: str, role: str, ip: str | None
) -> tuple[Invitation, str]:
    return await _new_invitation(db, settings, email, role, invited_by=admin.id, ip=ip)


async def find_invitation(
    db: AsyncSession, settings: Settings, token: str | None, *, for_update: bool = False
) -> Invitation | None:
    """The invitation for this token if it can still be used."""
    if not plausible(token):
        return None
    assert token is not None
    query = select(Invitation).where(Invitation.token_hash == hash_token(token, _key(settings)))
    if for_update:
        query = query.with_for_update()
    invitation = await db.scalar(query)
    if invitation is None or invitation_status(invitation) != "pending":
        return None
    return invitation


INVALID_INVITATION = (
    "This invitation link is not valid. It may have expired or been used already; "
    "ask an administrator for a new one."
)


async def accept_invitation(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    token: str,
    display_name: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> SignedIn:
    await _limit(redis, limits.INVITE_PER_IP, ip or "unknown")
    invitation = await find_invitation(db, settings, token, for_update=True)
    if invitation is None:
        await db.rollback()
        raise AuthError(404, INVALID_INVITATION)
    if await db.scalar(select(select(User.id).where(User.email == invitation.email).exists())):
        await db.rollback()
        raise AuthError(409, "An account with this email already exists. Sign in instead.")
    _check_password(password, invitation.email)
    now = utcnow()
    user = User(
        email=invitation.email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=invitation.role,
        last_login_at=now,
        password_changed_at=now,
    )
    db.add(user)
    await db.flush()
    invitation.used_at = now
    invitation.accepted_user_id = user.id
    signed_in = start_session(db, settings, user, ip, user_agent)
    audit.record(
        db, "invitation_accepted", user_id=user.id, target=f"invitation:{invitation.id}", ip=ip
    )
    await db.commit()
    return signed_in


async def list_invitations(db: AsyncSession) -> list[tuple[Invitation, str | None]]:
    inviter = aliased(User, name="inviter")
    rows = await db.execute(
        select(Invitation, inviter.display_name)
        .outerjoin(inviter, inviter.id == Invitation.invited_by)
        .order_by(Invitation.created_at.desc())
        .limit(200)
    )
    return [(invitation, name) for invitation, name in rows.all()]


async def revoke_invitation(
    db: AsyncSession, admin: User, invitation_id: uuid.UUID, ip: str | None
) -> None:
    invitation = await db.get(Invitation, invitation_id, with_for_update=True)
    if invitation is None or invitation_status(invitation) != "pending":
        await db.rollback()
        raise AuthError(404, "There is no open invitation with that id.")
    invitation.revoked_at = utcnow()
    audit.record(
        db, "invitation_revoked", user_id=admin.id, target=f"invitation:{invitation.id}", ip=ip
    )
    await db.commit()


# --- Accounts --------------------------------------------------------------------------


async def list_users(db: AsyncSession) -> list[User]:
    return list((await db.scalars(select(User).order_by(User.created_at))).all())


async def set_disabled(
    db: AsyncSession, admin: User, user_id: uuid.UUID, disabled: bool, ip: str | None
) -> User:
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        await db.rollback()
        raise AuthError(404, "There is no account with that id.")
    if disabled and user.id == admin.id:
        await db.rollback()
        raise AuthError(409, "You cannot disable your own account.")
    if disabled and user.role == "admin" and user.disabled_at is None:
        active_admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.disabled_at.is_(None))
        )
        if (active_admins or 0) <= 1:
            await db.rollback()
            raise AuthError(409, "At least one administrator must stay active.")
    if disabled and user.disabled_at is None:
        user.disabled_at = utcnow()
        await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
        audit.record(db, "user_disabled", user_id=admin.id, target=f"user:{user.id}", ip=ip)
    elif not disabled and user.disabled_at is not None:
        user.disabled_at = None
        audit.record(db, "user_enabled", user_id=admin.id, target=f"user:{user.id}", ip=ip)
    await db.commit()
    return user
