from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import RoleName, UserStatus
from app.schemas.common import ORM

_USERNAME = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")


class LimitsIn(BaseModel):
    daily_limit: int | None = Field(default=None, ge=0, le=100_000_000)
    hourly_limit: int | None = Field(default=None, ge=0, le=10_000_000)
    per_second_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_batch_size: int | None = Field(default=None, ge=1, le=100_000)
    max_recipients_per_campaign: int | None = Field(default=None, ge=1, le=100_000_000)


class LimitsOut(ORM, LimitsIn):
    updated_at: datetime | None = None


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    full_name: str | None = Field(default=None, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    role: RoleName = RoleName.USER
    limits: LimitsIn | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        if not _USERNAME.match(v):
            raise ValueError("Username must be 3-64 characters: letters, digits, . _ -")
        return v


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    full_name: str | None = Field(default=None, max_length=128)
    role: RoleName | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str | None) -> str | None:
        if v is not None and not _USERNAME.match(v):
            raise ValueError("Username must be 3-64 characters: letters, digits, . _ -")
        return v


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    username: str
    full_name: str | None
    role: str
    status: UserStatus
    totp_enabled: bool
    created_at: datetime
    last_login_at: datetime | None
    locked_until: datetime | None
    limits: LimitsOut | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _role_name(cls, v: object) -> str:
        return getattr(v, "name", v)  # type: ignore[return-value]


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=128)
