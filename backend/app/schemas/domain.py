"""Schemas for providers, campaigns, workers, jobs, suppressions, settings, audit."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import (
    AssignmentStatus,
    CampaignStatus,
    JobStatus,
    ProviderStatus,
    ProviderType,
    RecipientStatus,
    SuppressionType,
    TlsMode,
    WorkerStatus,
)
from app.schemas.common import ORM

# --------------------------------------------------------------------------- providers


class ProviderBase(BaseModel):
    provider_name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    tls_mode: TlsMode = TlsMode.STARTTLS
    from_email: EmailStr
    from_name: str | None = Field(default=None, max_length=128)
    hourly_limit: int | None = Field(default=None, ge=1)
    daily_limit: int | None = Field(default=None, ge=1)
    per_second_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_connections: int | None = Field(default=None, ge=1, le=500)
    reports_delivery: bool = False


class ProviderCreate(ProviderBase):
    type: ProviderType = ProviderType.SMTP
    password: str | None = Field(default=None, max_length=1024)


class ProviderUpdate(BaseModel):
    provider_name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    tls_mode: TlsMode | None = None
    from_email: EmailStr | None = None
    from_name: str | None = Field(default=None, max_length=128)
    hourly_limit: int | None = Field(default=None, ge=1)
    daily_limit: int | None = Field(default=None, ge=1)
    per_second_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_connections: int | None = Field(default=None, ge=1, le=500)
    reports_delivery: bool | None = None


class BounceMailboxIn(BaseModel):
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    port: int = Field(default=993, ge=1, le=65535)
    ssl: bool = True
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=1024, description="Omit to keep the stored password")
    folder: str = Field(default="INBOX", min_length=1, max_length=255)
    delete_processed: bool = False


class BounceMailboxOut(BaseModel):
    host: str
    port: int
    ssl: bool
    username: str
    folder: str
    delete_processed: bool


class ProviderSecretIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class ProviderOut(ORM):
    id: uuid.UUID
    provider_name: str
    type: ProviderType
    host: str
    port: int
    username: str | None
    tls_mode: TlsMode
    from_email: str
    from_name: str | None
    hourly_limit: int | None
    daily_limit: int | None
    per_second_limit: int | None
    max_connections: int | None = None
    reports_delivery: bool = False
    status: ProviderStatus
    status_reason: str | None
    health_score: float
    has_secret: bool = False
    webhook_enabled: bool = False
    bounce_mailbox: BounceMailboxOut | None = None
    bounce_last_polled_at: datetime | None = None
    bounce_last_error: str | None = None
    bounce_processed_total: int = 0
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    created_at: datetime
    updated_at: datetime


class ProviderUsage(BaseModel):
    hour: int
    day: int


class ProviderDetail(ProviderOut):
    usage: ProviderUsage | None = None
    assigned_users: int = 0


class UserProviderOut(BaseModel):
    """What a USER sees: no host, credentials or limits of other tenants."""

    id: uuid.UUID
    provider_name: str
    from_email: str
    from_name: str | None
    status: ProviderStatus
    per_second_limit: int | None
    hourly_limit: int | None
    daily_limit: int | None


class AssignmentCreate(BaseModel):
    user_id: uuid.UUID
    provider_id: uuid.UUID


class AssignmentOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    provider_id: uuid.UUID
    status: AssignmentStatus
    assigned_at: datetime
    assigned_by: uuid.UUID | None
    revoked_at: datetime | None
    username: str | None = None
    provider_name: str | None = None


class HealthLogOut(ORM):
    window_start: datetime
    attempts: int
    successes: int
    auth_failures: int
    connection_failures: int
    timeouts: int
    deferrals: int
    bounces: int
    complaints: int
    health_score: float
    status: str
    created_at: datetime


# --------------------------------------------------------------------------- campaigns

MAX_BODY = 1_000_000


class CampaignBase(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=998)
    from_name: str | None = Field(default=None, max_length=128)
    from_email: EmailStr | None = None
    reply_to: EmailStr | None = None
    html_body: str | None = Field(default=None, max_length=MAX_BODY)
    text_body: str | None = Field(default=None, max_length=MAX_BODY)
    provider_id: uuid.UUID | None = None
    batch_size: int | None = Field(default=None, ge=1, le=100_000)
    scheduled_at: datetime | None = None

    @field_validator("subject", "from_name")
    @classmethod
    def _no_header_injection(cls, v: str | None) -> str | None:
        if v is not None and ("\r" in v or "\n" in v):
            raise ValueError("Line breaks are not allowed")
        return v


class CampaignCreate(CampaignBase):
    name: str = Field(min_length=1, max_length=200)


class AdminCampaignCreate(CampaignCreate):
    user_id: uuid.UUID


class CampaignUpdate(CampaignBase):
    pass


class StartRequest(BaseModel):
    consent_confirmed: bool = False


class CampaignOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    created_by: uuid.UUID | None
    provider_id: uuid.UUID | None
    name: str
    subject: str
    from_name: str | None
    from_email: str | None
    reply_to: str | None
    status: CampaignStatus
    batch_size: int
    total_recipients: int
    invalid_recipients: int
    duplicate_recipients: int
    processed: int
    sent: int
    delivered: int
    failed: int
    bounced: int
    complained: int
    deferred: int
    unsubscribed: int
    skipped_suppressed: int
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    consent_confirmed_at: datetime | None
    last_error: str | None
    username: str | None = None
    provider_name: str | None = None
    progress: dict[str, Any] | None = None
    missing: list[str] | None = None


class CampaignDetail(CampaignOut):
    html_body: str | None
    text_body: str | None


class CampaignStats(BaseModel):
    id: uuid.UUID
    status: CampaignStatus
    total_recipients: int
    processed: int
    sent: int
    delivered: int
    failed: int
    bounced: int
    complained: int
    deferred: int
    unsubscribed: int
    skipped_suppressed: int
    remaining: int
    percent: float
    speed: float
    jobs: dict[str, int]


class RecipientOut(ORM):
    id: int
    email: str
    status: RecipientStatus
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    variables: dict[str, Any]


class ImportResult(BaseModel):
    rows: int
    imported: int
    invalid: int
    duplicates: int
    invalid_samples: list[str]
    total_recipients: int
    status: CampaignStatus
    missing: list[str]


# --------------------------------------------------------------------------- workers & jobs


class WorkerCreate(BaseModel):
    worker_id: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=128)
    capacity: int = Field(default=80, ge=1, le=100_000)
    max_concurrent_jobs: int = Field(default=4, ge=1, le=256)


class WorkerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    capacity: int | None = Field(default=None, ge=1, le=100_000)
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=256)


class WorkerOut(ORM):
    id: uuid.UUID
    worker_id: str
    name: str
    version: str | None
    hostname: str | None
    capacity: int
    max_concurrent_jobs: int
    status: WorkerStatus
    disabled: bool
    registered_at: datetime | None
    last_heartbeat_at: datetime | None
    cpu: float | None
    memory: float | None
    active_jobs: int
    current_rate: float
    created_at: datetime


class WorkerCredentialOut(BaseModel):
    worker: WorkerOut
    credential: str
    note: str = "Store this credential in the worker's secret store. It is shown only once."


class HeartbeatOut(ORM):
    status: str
    cpu: float | None
    memory: float | None
    active_jobs: int
    current_rate: float
    timestamp: datetime


class JobOut(ORM):
    id: uuid.UUID
    campaign_id: uuid.UUID
    user_id: uuid.UUID
    batch_id: uuid.UUID
    provider_id: uuid.UUID
    worker_id: uuid.UUID | None
    status: JobStatus
    batch_size: int
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    last_error: str | None


# Worker API payloads


class WorkerTokenRequest(BaseModel):
    worker_id: str
    credential: str


class WorkerRegisterRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    version: str | None = Field(default=None, max_length=32)
    hostname: str | None = Field(default=None, max_length=255)
    capacity: int | None = Field(default=None, ge=1, le=100_000)
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=256)


class HeartbeatRequest(BaseModel):
    status: Literal["online", "draining", "offline"] = "online"
    cpu: float | None = Field(default=None, ge=0, le=100)
    memory: float | None = Field(default=None, ge=0, le=100)
    active_jobs: int = Field(default=0, ge=0)
    current_rate: float = Field(default=0, ge=0)
    timestamp: datetime | None = None


class AttemptRef(BaseModel):
    attempt_id: uuid.UUID


class RecipientResult(BaseModel):
    recipient_id: int
    outcome: Literal["sent", "failed"]
    provider_message_id: str | None = Field(default=None, max_length=255)
    smtp_code: int | None = Field(default=None, ge=100, le=599)
    enhanced_code: str | None = Field(default=None, max_length=16)
    category: Literal["rejected", "transient", "timeout", "connection", "invalid", "other"] | None = None
    error_message: str | None = Field(default=None, max_length=1000)


class ResultsRequest(AttemptRef):
    results: list[RecipientResult] = Field(max_length=10_000)


class FailureRequest(AttemptRef):
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=1000)
    transient: bool = True
    category: Literal["auth", "connection", "timeout", "provider", "other"] | None = None


class ReleaseRequest(AttemptRef):
    reason: str | None = Field(default=None, max_length=255)


# --------------------------------------------------------------------------- suppressions, settings, audit


class SuppressionCreate(BaseModel):
    email: EmailStr
    type: SuppressionType = SuppressionType.ADMIN_BLOCKED
    user_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=512)


class SuppressionOut(ORM):
    id: int
    scope_user_id: uuid.UUID | None
    email_normalized: str
    type: SuppressionType
    reason: str | None
    created_at: datetime
    created_by: uuid.UUID | None


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class AuditOut(ORM):
    id: int
    admin_id: uuid.UUID | None
    actor_type: str
    action: str
    resource: str
    resource_id: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    ip: str | None
    user_agent: str | None
    timestamp: datetime
    actor: str | None = None


class WebhookEvent(BaseModel):
    type: str
    provider_message_id: str | None = None
    email: str | None = None
    bounce_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    timestamp: datetime | None = None


class WebhookPayload(BaseModel):
    events: list[WebhookEvent] = Field(max_length=1000)


# --------------------------------------------------------------------------- API keys (DS-17)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=20)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class AdminApiKeyCreate(ApiKeyCreate):
    user_id: uuid.UUID


class ApiKeyOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID
    username: str | None = None
    name: str
    prefix: str
    scopes: list[str]
    created_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None
    active: bool = True


class ApiKeyCreated(ApiKeyOut):
    key: str
