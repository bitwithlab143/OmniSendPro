"""Worker configuration from environment (ARCHITECTURE.md §9: credentials never hard-coded)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _credential() -> str:
    path = os.environ.get("WORKER_CREDENTIAL_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    value = os.environ.get("WORKER_CREDENTIAL", "")
    if not value:
        raise SystemExit("WORKER_CREDENTIAL or WORKER_CREDENTIAL_FILE must be set")
    return value


@dataclass(slots=True)
class WorkerConfig:
    api_url: str
    worker_id: str
    credential: str
    name: str | None = None
    capacity: int | None = None  # msgs/sec across all jobs on this worker
    max_concurrent_jobs: int | None = None
    smtp_connections_per_job: int = 4
    redis_url: str | None = None
    log_level: str = "INFO"
    hostname: str = field(default_factory=socket.gethostname)
    result_flush_interval: float = 1.0
    result_flush_size: int = 200
    idle_poll_max: float = 5.0
    send_timeout: float = 30.0
    verify_tls: bool = True

    @classmethod
    def from_env(cls) -> WorkerConfig:
        api_url = os.environ.get("WORKER_API_URL", "http://localhost:8000").rstrip("/")
        worker_id = os.environ.get("WORKER_ID", "")
        if not worker_id:
            raise SystemExit("WORKER_ID must be set")
        return cls(
            api_url=api_url,
            worker_id=worker_id,
            credential=_credential(),
            name=os.environ.get("WORKER_NAME") or None,
            capacity=_int("WORKER_CAPACITY", 0) or None,
            max_concurrent_jobs=_int("WORKER_MAX_CONCURRENT_JOBS", 0) or None,
            smtp_connections_per_job=_int("SMTP_CONNECTIONS_PER_JOB", 4),
            redis_url=os.environ.get("REDIS_URL") or None,
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            send_timeout=float(os.environ.get("SMTP_TIMEOUT_SECONDS", "30")),
            verify_tls=os.environ.get("WORKER_API_VERIFY_TLS", "true").lower() != "false",
        )
