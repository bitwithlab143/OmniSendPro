"""Application settings loaded from environment variables (ARCHITECTURE.md §35, design DS-14)."""

from __future__ import annotations

import base64
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ONLY = "change-me-dev-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend", alias="DATABASE_URL"
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    jwt_secret: str = Field(default=_DEV_ONLY + "-jwt-0000000000000000000000", alias="JWT_SECRET")
    worker_jwt_secret: str = Field(default=_DEV_ONLY + "-worker-000000000000000000", alias="WORKER_JWT_SECRET")
    # base64-encoded 32-byte key(s). Format: "v1:<b64key>[,v2:<b64key>]"; the last one encrypts new data.
    encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")

    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")
    cors_origins: str = Field(default="http://localhost:5173,http://localhost:5174", alias="CORS_ORIGINS")

    access_token_minutes: int = Field(default=15, alias="ACCESS_TOKEN_MINUTES")
    refresh_token_days: int = Field(default=7, alias="REFRESH_TOKEN_DAYS")
    worker_token_minutes: int = Field(default=15, alias="WORKER_TOKEN_MINUTES")
    admin_require_2fa: bool = Field(default=True, alias="ADMIN_REQUIRE_2FA")
    cookie_secure: bool | None = Field(default=None, alias="COOKIE_SECURE")

    max_request_bytes: int = Field(default=2 * 1024 * 1024, alias="MAX_REQUEST_BYTES")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")

    # Optional S3-compatible object storage for large recipient files (design DS-21, ADR-015).
    object_storage_endpoint: str | None = Field(default=None, alias="OBJECT_STORAGE_ENDPOINT")
    # Endpoint the *browser* uploads to, when it differs from the internal one (e.g. http://minio:9000).
    object_storage_public_endpoint: str | None = Field(default=None, alias="OBJECT_STORAGE_PUBLIC_ENDPOINT")
    object_storage_bucket: str | None = Field(default=None, alias="OBJECT_STORAGE_BUCKET")
    object_storage_region: str = Field(default="us-east-1", alias="OBJECT_STORAGE_REGION")
    object_storage_access_key: str | None = Field(default=None, alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: str | None = Field(default=None, alias="OBJECT_STORAGE_SECRET_KEY")
    object_storage_path_style: bool = Field(default=True, alias="OBJECT_STORAGE_PATH_STYLE")
    object_storage_max_bytes: int = Field(default=1024 * 1024 * 1024, alias="OBJECT_STORAGE_MAX_BYTES")

    run_scheduler: bool = Field(default=True, alias="RUN_SCHEDULER")
    scheduler_interval_seconds: float = Field(default=5.0, alias="SCHEDULER_INTERVAL_SECONDS")
    health_interval_seconds: float = Field(default=60.0, alias="HEALTH_INTERVAL_SECONDS")
    maintenance_interval_seconds: float = Field(default=3600.0, alias="MAINTENANCE_INTERVAL_SECONDS")

    bootstrap_admin_email: str | None = Field(default=None, alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str | None = Field(default=None, alias="BOOTSTRAP_ADMIN_PASSWORD")

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.is_production:
            for name in ("jwt_secret", "worker_jwt_secret"):
                value = getattr(self, name)
                if _DEV_ONLY in value or len(value) < 32:
                    raise ValueError(f"{name.upper()} must be set to a strong secret (>=32 chars) in production")
            if not self.encryption_key:
                raise ValueError("ENCRYPTION_KEY must be set in production")
        return self

    @property
    def object_storage_enabled(self) -> bool:
        return bool(self.object_storage_bucket and self.object_storage_access_key and self.object_storage_secret_key)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def encryption_keys(self) -> dict[str, bytes]:
        """Return {version: key}. In development a deterministic key is derived when none is configured."""
        if not self.encryption_key:
            import hashlib

            return {"v1": hashlib.sha256(b"omnisend-dev-encryption-key").digest()}
        keys: dict[str, bytes] = {}
        for part in self.encryption_key.split(","):
            part = part.strip()
            if not part:
                continue
            version, _, b64 = part.partition(":") if ":" in part else ("v1", "", part)
            key = base64.b64decode(b64)
            if len(key) != 32:
                raise ValueError("ENCRYPTION_KEY entries must decode to 32 bytes")
            keys[version] = key
        return keys


@lru_cache
def get_settings() -> Settings:
    return Settings()
