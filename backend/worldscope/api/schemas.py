"""Request and response bodies for /api/v1."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from worldscope.auth.passwords import MAX_LENGTH as PASSWORD_MAX


def _display_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Enter your name.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("The name contains characters that cannot be shown.")
    return value


DisplayName = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_display_name)]
Password = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX)]
Role = Literal["admin", "analyst"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=2000)


# --- Accounts -------------------------------------------------------------------------


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None
    disabled: bool


class SignedInOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


class MeOut(BaseModel):
    user: UserOut
    session_expires_at: datetime


class SetupStateOut(BaseModel):
    state: Literal["open", "closed", "unconfigured"]


class LoginIn(In):
    email: EmailStr
    password: Password


class SetupIn(In):
    code: str = Field(min_length=1, max_length=200)
    email: EmailStr
    display_name: DisplayName
    password: Password


InvitationToken = Annotated[str, Field(min_length=1, max_length=128)]


class InvitationTokenIn(In):
    token: InvitationToken


class AcceptInvitationIn(In):
    token: InvitationToken
    display_name: DisplayName
    password: Password


class PasswordChangeIn(In):
    current_password: Password
    new_password: Password


class InvitationIn(In):
    email: EmailStr
    role: Role = "analyst"


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    status: Literal["pending", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime
    invited_by: str | None


class InvitationCreatedOut(BaseModel):
    invitation: InvitationOut
    token: str


class InvitationPreviewOut(BaseModel):
    email: str
    role: Role
    expires_at: datetime


# --- Sources --------------------------------------------------------------------------


class CountryRef(BaseModel):
    iso3: str
    name: str


class SourceOut(BaseModel):
    slug: str
    name: str
    type: Literal["rss", "official", "api"]
    url: str
    homepage: str
    publisher_country: CountryRef | None
    language: str
    focus: list[str]
    ownership: str | None
    is_primary: bool
    licence: str | None
    storage_policy: Literal["link_only", "snippet"]
    terms_url: str | None
    review_status: Literal["pending", "approved", "rejected"]
    review_note: str | None
    enabled: bool
    last_success_at: datetime | None
    consecutive_failures: int


class SourcesOut(BaseModel):
    sources: list[SourceOut]


# --- Bookmarks ------------------------------------------------------------------------


class BookmarkIn(In):
    article_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


class BookmarkOut(BaseModel):
    id: uuid.UUID
    note: str | None
    created_at: datetime
    article_id: uuid.UUID
    title: str
    url: str
    source_name: str
    published_at: datetime | None


class BookmarksOut(BaseModel):
    bookmarks: list[BookmarkOut]
