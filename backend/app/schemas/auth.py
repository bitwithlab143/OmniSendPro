from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AppName = Literal["admin", "user"]


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    app: AppName


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=8)


class RefreshRequest(BaseModel):
    app: AppName


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    full_name: str | None
    role: str
    permissions: list[str]
    totp_enabled: bool
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    status: Literal["ok", "mfa_required", "mfa_setup_required"]
    access_token: str | None = None
    expires_in: int | None = None
    user: MeResponse | None = None
    mfa_token: str | None = None
    otpauth_uri: str | None = None
    totp_secret: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    user: MeResponse


class TotpSetupResponse(BaseModel):
    otpauth_uri: str
    totp_secret: str


class TotpCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)
