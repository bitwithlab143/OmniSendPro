"""Status vocabularies (design DS-03, DS-04, DS-05, DS-06, DS-07, DS-08)."""

from __future__ import annotations

from enum import StrEnum


class RoleName(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    USER = "USER"
    VIEWER = "VIEWER"


ADMIN_PANEL_ROLES = {RoleName.SUPER_ADMIN, RoleName.ADMIN, RoleName.OPERATOR, RoleName.VIEWER}


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Admin-menu views over the campaign state machine (OQ-08).
CAMPAIGN_VIEWS: dict[str, set[CampaignStatus]] = {
    "pending": {CampaignStatus.DRAFT, CampaignStatus.READY, CampaignStatus.QUEUED},
    "processing": {CampaignStatus.PROCESSING, CampaignStatus.PAUSED},
    "completed": {CampaignStatus.COMPLETED},
    "failed": {CampaignStatus.FAILED, CampaignStatus.CANCELLED},
}


class RecipientStatus(StrEnum):
    PENDING = "pending"  # imported, not yet in a batch
    QUEUED = "queued"  # in a batch waiting to be sent
    SENT = "sent"
    DELIVERED = "delivered"
    DEFERRED = "deferred"  # transient failure, will be retried
    BOUNCED = "bounced"
    FAILED = "failed"
    COMPLAINED = "complained"
    UNSUBSCRIBED = "unsubscribed"
    SUPPRESSED = "suppressed"  # skipped by the suppression check
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


OPEN_JOB_STATUSES = {JobStatus.PENDING, JobStatus.CLAIMED, JobStatus.PROCESSING, JobStatus.RETRY}
CLAIMABLE_JOB_STATUSES = {JobStatus.PENDING, JobStatus.RETRY}
LEASED_JOB_STATUSES = {JobStatus.CLAIMED, JobStatus.PROCESSING}


class ProviderType(StrEnum):
    SMTP = "smtp"
    API = "api"


class TlsMode(StrEnum):
    STARTTLS = "starttls"
    SSL = "ssl"
    NONE = "none"


class ProviderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


SENDABLE_PROVIDER_STATUSES = {ProviderStatus.ACTIVE, ProviderStatus.WARNING, ProviderStatus.DEGRADED}
SELECTABLE_PROVIDER_STATUSES = {ProviderStatus.ACTIVE, ProviderStatus.WARNING}


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class WorkerStatus(StrEnum):
    ONLINE = "online"
    WARNING = "warning"
    OFFLINE = "offline"


class SuppressionType(StrEnum):
    UNSUBSCRIBE = "unsubscribe"
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    INVALID = "invalid"
    ADMIN_BLOCKED = "admin_blocked"


# OQ-05: bounce / complaint / admin blocks are global; unsubscribes are scoped to the sending user.
GLOBAL_SUPPRESSION_TYPES = {
    SuppressionType.HARD_BOUNCE,
    SuppressionType.COMPLAINT,
    SuppressionType.ADMIN_BLOCKED,
    SuppressionType.INVALID,
}


class EventType(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SENT = "sent"
    DELIVERED = "delivered"
    DEFERRED = "deferred"
    BOUNCED = "bounced"
    FAILED = "failed"
    COMPLAINED = "complained"
    UNSUBSCRIBED = "unsubscribed"
    SUPPRESSED = "suppressed"


class AttemptOutcome(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LEASE_EXPIRED = "lease_expired"
    RELEASED = "released"
