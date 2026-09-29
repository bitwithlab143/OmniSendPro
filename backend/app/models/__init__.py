"""SQLAlchemy models for every table in design DS-03."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Sequence,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import uuid7
from app.db.base import Base, TimestampMixin
from app.models.enums import (
    AssignmentStatus,
    AttemptOutcome,
    CampaignStatus,
    EventType,
    JobStatus,
    ProviderStatus,
    ProviderType,
    RecipientStatus,
    SuppressionType,
    TlsMode,
    UserStatus,
    WorkerStatus,
)


def enum_col(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint (portable, migration friendly) instead of native PG enums."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def ts(nullable: bool = True, **kw: Any) -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=nullable, **kw)


# --------------------------------------------------------------------------- identity & access

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        Index("uq_users_username_lower", func.lower(text("username")), unique=True),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320))
    username: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(enum_col(UserStatus, "user_status"), default=UserStatus.ACTIVE)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = ts()
    last_login_at: Mapped[datetime | None] = ts()
    password_changed_at: Mapped[datetime | None] = ts()

    role: Mapped[Role] = relationship(lazy="joined")
    limits: Mapped[UserLimits | None] = relationship(
        back_populates="user", uselist=False, lazy="selectin", foreign_keys="UserLimits.user_id"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    app: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = ts()
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """Programmatic access to the User API (design DS-17). Only the SHA-256 hash is stored."""

    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = ts()
    revoked_at: Mapped[datetime | None] = ts()
    last_used_at: Mapped[datetime | None] = ts()
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class UserLimits(Base):
    __tablename__ = "user_limits"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    hourly_limit: Mapped[int | None] = mapped_column(Integer)
    per_second_limit: Mapped[int | None] = mapped_column(Integer)
    max_batch_size: Mapped[int | None] = mapped_column(Integer)
    max_recipients_per_campaign: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    user: Mapped[User] = relationship(back_populates="limits", foreign_keys=[user_id])


# --------------------------------------------------------------------------- providers


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    id: Mapped[uuid.UUID] = uuid_pk()
    provider_name: Mapped[str] = mapped_column(String(128), unique=True)
    type: Mapped[ProviderType] = mapped_column(enum_col(ProviderType, "provider_type"), default=ProviderType.SMTP)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(255))
    tls_mode: Mapped[TlsMode] = mapped_column(enum_col(TlsMode, "tls_mode"), default=TlsMode.STARTTLS)
    from_email: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(128))
    hourly_limit: Mapped[int | None] = mapped_column(Integer)
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    per_second_limit: Mapped[int | None] = mapped_column(Integer)
    # Max simultaneous SMTP connections one worker opens to this provider (provider policy). None = worker default.
    max_connections: Mapped[int | None] = mapped_column(Integer)
    # Bounce/FBL mailbox (DS-16): {host, port, ssl, username, folder, delete_processed}; password encrypted.
    bounce_mailbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    bounce_mailbox_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    bounce_last_polled_at: Mapped[datetime | None] = ts()
    bounce_last_error: Mapped[str | None] = mapped_column(String(512))
    bounce_processed_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # True when the provider confirms deliveries through the webhook; otherwise SMTP acceptance = delivered.
    reports_delivery: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[ProviderStatus] = mapped_column(
        enum_col(ProviderStatus, "provider_status"), default=ProviderStatus.ACTIVE, index=True
    )
    health_score: Mapped[float] = mapped_column(Float, default=100.0, server_default=text("100"))
    consecutive_bad_windows: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    last_tested_at: Mapped[datetime | None] = ts()
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(String(512))
    status_reason: Mapped[str | None] = mapped_column(String(255))

    credential: Mapped[ProviderCredential | None] = relationship(
        back_populates="provider", uselist=False, lazy="selectin", cascade="all, delete-orphan"
    )


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"
    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), unique=True)
    encrypted_secret: Mapped[str] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(String(16))
    rotated_at: Mapped[datetime | None] = ts()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    provider: Mapped[Provider] = relationship(back_populates="credential")


class ProviderAssignment(Base):
    __tablename__ = "provider_assignments"
    __table_args__ = (
        Index(
            "uq_provider_assignments_active",
            "user_id",
            "provider_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        enum_col(AssignmentStatus, "assignment_status"), default=AssignmentStatus.ACTIVE
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = ts()

    provider: Mapped[Provider] = relationship(lazy="joined", foreign_keys=[provider_id])
    user: Mapped[User] = relationship(lazy="joined", foreign_keys=[user_id])


class ProviderHealthLog(Base):
    __tablename__ = "provider_health_logs"
    id: Mapped[int] = mapped_column(BigInteger, Sequence("provider_health_logs_id_seq"), primary_key=True,
                                    server_default=text("nextval('provider_health_logs_id_seq'::regclass)"))
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    auth_failures: Mapped[int] = mapped_column(Integer, default=0)
    connection_failures: Mapped[int] = mapped_column(Integer, default=0)
    timeouts: Mapped[int] = mapped_column(Integer, default=0)
    deferrals: Mapped[int] = mapped_column(Integer, default=0)
    bounces: Mapped[int] = mapped_column(Integer, default=0)
    complaints: Mapped[int] = mapped_column(Integer, default=0)
    health_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 primary_key=True)
    # Range-partitioned by month (design DS-18); the partition key must be part of the primary key.
    __table_args__ = (Index("ix_provider_health_logs_provider_time", "provider_id", "created_at"),
                      {"postgresql_partition_by": "RANGE (created_at)"})


# --------------------------------------------------------------------------- campaigns


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("idx_campaign_user", "user_id"), Index("ix_campaigns_status", "status"))
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("providers.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(998), default="")
    from_name: Mapped[str | None] = mapped_column(String(128))
    from_email: Mapped[str | None] = mapped_column(String(320))
    reply_to: Mapped[str | None] = mapped_column(String(320))
    html_body: Mapped[str | None] = mapped_column(Text)
    text_body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CampaignStatus] = mapped_column(
        enum_col(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT
    )
    batch_size: Mapped[int] = mapped_column(Integer, default=1000)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    invalid_recipients: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    duplicate_recipients: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    processed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    sent: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    delivered: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    bounced: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    complained: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    deferred: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    unsubscribed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    skipped_suppressed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    batches_built: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    consent_confirmed_at: Mapped[datetime | None] = ts()
    consent_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    scheduled_at: Mapped[datetime | None] = ts()
    started_at: Mapped[datetime | None] = ts()
    paused_at: Mapped[datetime | None] = ts()
    completed_at: Mapped[datetime | None] = ts()
    cancelled_at: Mapped[datetime | None] = ts()
    last_error: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(lazy="joined", foreign_keys=[user_id])
    provider: Mapped[Provider | None] = relationship(lazy="joined", foreign_keys=[provider_id])


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "email_normalized", name="uq_campaign_recipients_campaign_email"),
        Index("ix_campaign_recipients_batch_status", "batch_id", "status"),
        Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),
        Index(
            "ix_campaign_recipients_provider_message_id",
            "provider_message_id",
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaign_batches.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320))
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[RecipientStatus] = mapped_column(
        enum_col(RecipientStatus, "recipient_status"), default=RecipientStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(String(512))
    sent_at: Mapped[datetime | None] = ts()
    last_event_at: Mapped[datetime | None] = ts()


class CampaignBatch(Base):
    __tablename__ = "campaign_batches"
    __table_args__ = (UniqueConstraint("campaign_id", "sequence_no", name="uq_campaign_batches_seq"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    recipient_count: Mapped[int] = mapped_column(Integer)
    retry_round: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[JobStatus] = mapped_column(enum_col(JobStatus, "batch_status"), default=JobStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- workers & jobs


class Worker(TimestampMixin, Base):
    __tablename__ = "workers"
    id: Mapped[uuid.UUID] = uuid_pk()
    worker_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(32))
    capacity: Mapped[int] = mapped_column(Integer, default=80)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=4, server_default=text("4"))
    status: Mapped[WorkerStatus] = mapped_column(enum_col(WorkerStatus, "worker_status"), default=WorkerStatus.OFFLINE)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    credential_hash: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime | None] = ts()
    last_heartbeat_at: Mapped[datetime | None] = ts()
    cpu: Mapped[float | None] = mapped_column(Float)
    memory: Mapped[float | None] = mapped_column(Float)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    current_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    hostname: Mapped[str | None] = mapped_column(String(255))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (Index("ix_worker_heartbeats_worker_time", "worker_id", "timestamp"),
                      {"postgresql_partition_by": "RANGE (timestamp)"})  # daily partitions (DS-18)
    id: Mapped[int] = mapped_column(BigInteger, Sequence("worker_heartbeats_id_seq"), primary_key=True,
                                    server_default=text("nextval('worker_heartbeats_id_seq'::regclass)"))
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    cpu: Mapped[float | None] = mapped_column(Float)
    memory: Mapped[float | None] = mapped_column(Float)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0)
    current_rate: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), primary_key=True)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_available_at", "available_at"),
        Index(
            "idx_jobs_claimable",
            "status",
            "available_at",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
        Index(
            "idx_jobs_lease",
            "lease_expires_at",
            postgresql_where=text("status IN ('claimed', 'processing')"),
        ),
        Index("ix_jobs_campaign_status", "campaign_id", "status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_batches.id", ondelete="CASCADE"), unique=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("providers.id", ondelete="RESTRICT"))
    worker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"))
    status: Mapped[JobStatus] = mapped_column(enum_col(JobStatus, "job_status"), default=JobStatus.PENDING)
    batch_size: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_expires_at: Mapped[datetime | None] = ts()
    current_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None] = ts()
    completed_at: Mapped[datetime | None] = ts()
    last_error: Mapped[str | None] = mapped_column(String(512))


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = ts()
    outcome: Mapped[AttemptOutcome] = mapped_column(
        enum_col(AttemptOutcome, "attempt_outcome"), default=AttemptOutcome.RUNNING
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))


class EmailEvent(Base):
    __tablename__ = "email_events"
    __table_args__ = (
        Index("idx_events_campaign_time", "campaign_id", "created_at"),
        Index("ix_email_events_created_at", "created_at"),
        # Monthly range partitions (DS-18). Provider-event de-duplication lives in email_event_keys (ADR-013).
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Sequence("email_events_id_seq"), primary_key=True,
                                    server_default=text("nextval('email_events_id_seq'::regclass)"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    recipient_id: Mapped[int | None] = mapped_column(BigInteger)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    event_type: Mapped[EventType] = mapped_column(enum_col(EventType, "event_type"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 primary_key=True)


class EmailEventKey(Base):
    """De-duplication keys for provider-reported events (ADR-013); unpartitioned, pruned by retention."""

    __tablename__ = "email_event_keys"
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# --------------------------------------------------------------------------- compliance & system


class Suppression(Base):
    __tablename__ = "suppression_list"
    __table_args__ = (
        Index(
            "uq_suppression_scope_email",
            "scope_user_id",
            "email_normalized",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("idx_suppression_lookup", "email_normalized"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    scope_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email_normalized: Mapped[str] = mapped_column(String(320))
    type: Mapped[SuppressionType] = mapped_column(enum_col(SuppressionType, "suppression_type"))
    reason: Mapped[str | None] = mapped_column(String(512))
    source_event_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_resource", "resource", "resource_id"),
        {"postgresql_partition_by": "RANGE (timestamp)"},  # monthly partitions (DS-18)
    )
    id: Mapped[int] = mapped_column(BigInteger, Sequence("audit_logs_id_seq"), primary_key=True,
                                    server_default=text("nextval('audit_logs_id_seq'::regclass)"))
    admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")  # user | system | worker
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), primary_key=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "EmailEventKey",
    "ApiKey",
    "AuditLog",
    "Campaign",
    "CampaignBatch",
    "CampaignRecipient",
    "EmailEvent",
    "Job",
    "JobAttempt",
    "Permission",
    "Provider",
    "ProviderAssignment",
    "ProviderCredential",
    "ProviderHealthLog",
    "RefreshToken",
    "Role",
    "Suppression",
    "SystemSetting",
    "User",
    "UserLimits",
    "Worker",
    "WorkerHeartbeat",
    "role_permissions",
]
