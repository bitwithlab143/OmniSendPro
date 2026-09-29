"""Password hashing, JWTs, TOTP, secret encryption and signed tokens.

Design refs: DS-10 (auth), DS-11 / ADR-006 (envelope encryption), ADR-005 (tokens).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

# --------------------------------------------------------------------------- passwords (Argon2id)

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 recommended params)
# A valid hash used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> str | None:
    """Return an error message, or None when the password is acceptable."""
    if len(password) < 12:
        return "Password must be at least 12 characters long"
    if len(password) > 256:
        return "Password is too long"
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return "Password must contain at least three of: lowercase, uppercase, digit, symbol"
    return None


# --------------------------------------------------------------------------- opaque tokens


def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --------------------------------------------------------------------------- JWT

ALGORITHM = "HS256"
USER_AUDIENCE = "omnisend:user"
WORKER_AUDIENCE = "omnisend:worker"
MFA_AUDIENCE = "omnisend:mfa"


def _now() -> datetime:
    return datetime.now(UTC)


def session_version(password_changed_at: datetime | None) -> int:
    """Changes whenever the password changes; embedded in access tokens to invalidate old sessions."""
    return int(password_changed_at.timestamp() * 1000) if password_changed_at else 0


def create_access_token(user_id: uuid.UUID, role: str, app: str, version: int = 0) -> tuple[str, int]:
    settings = get_settings()
    ttl = settings.access_token_minutes * 60
    payload = {
        "sub": str(user_id),
        "role": role,
        "app": app,
        "sv": version,
        "aud": USER_AUDIENCE,
        "iat": _now(),
        "exp": _now() + timedelta(seconds=ttl),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), ttl


def create_mfa_token(user_id: uuid.UUID, app: str, purpose: str) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "app": app,
        "purpose": purpose,  # "verify" | "setup"
        "aud": MFA_AUDIENCE,
        "iat": _now(),
        "exp": _now() + timedelta(minutes=5),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_worker_token(worker_pk: uuid.UUID, worker_id: str) -> tuple[str, int]:
    settings = get_settings()
    ttl = settings.worker_token_minutes * 60
    payload = {
        "sub": str(worker_pk),
        "wid": worker_id,
        "aud": WORKER_AUDIENCE,
        "iat": _now(),
        "exp": _now() + timedelta(seconds=ttl),
    }
    # Workers use a separate signing key and audience, so their tokens can never pass user auth.
    return jwt.encode(payload, settings.worker_jwt_secret, algorithm=ALGORITHM), ttl


def decode_token(token: str, audience: str) -> dict[str, Any]:
    settings = get_settings()
    key = settings.worker_jwt_secret if audience == WORKER_AUDIENCE else settings.jwt_secret
    return jwt.decode(token, key, algorithms=[ALGORITHM], audience=audience, options={"require": ["exp", "sub"]})


# --------------------------------------------------------------------------- TOTP


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name="OmniSendPro")


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# --------------------------------------------------------------------------- encryption at rest (AES-256-GCM)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt with the newest key. Output: '<version>:<b64(nonce|ciphertext)>'."""
    keys = get_settings().encryption_keys()
    version = list(keys)[-1]
    nonce = os.urandom(12)
    ct = AESGCM(keys[version]).encrypt(nonce, plaintext.encode(), version.encode())
    return f"{version}:{base64.b64encode(nonce + ct).decode()}"


def decrypt_secret(token: str) -> str:
    version, _, blob = token.partition(":")
    keys = get_settings().encryption_keys()
    if version not in keys:
        raise ValueError(f"unknown encryption key version {version!r}")
    raw = base64.b64decode(blob)
    return AESGCM(keys[version]).decrypt(raw[:12], raw[12:], version.encode()).decode()


def encryption_key_version(token: str) -> str:
    return token.partition(":")[0]


# --------------------------------------------------------------------------- signed URL tokens (unsubscribe)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _signing_key(purpose: str) -> bytes:
    return hmac.new(get_settings().jwt_secret.encode(), purpose.encode(), hashlib.sha256).digest()


def sign_value(value: str, purpose: str) -> str:
    sig = hmac.new(_signing_key(purpose), value.encode(), hashlib.sha256).digest()[:16]
    return f"{_b64(value.encode())}.{_b64(sig)}"


def unsign_value(token: str, purpose: str) -> str | None:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        value = _unb64(payload_b64).decode()
        expected = hmac.new(_signing_key(purpose), value.encode(), hashlib.sha256).digest()[:16]
        if hmac.compare_digest(expected, _unb64(sig_b64)):
            return value
    except (ValueError, UnicodeDecodeError):
        return None
    return None


def verify_webhook_signature(secret: str, body: bytes, timestamp: str, signature: str, tolerance: int = 300) -> bool:
    """Signature = hex(HMAC_SHA256(secret, f"{timestamp}.{body}")); rejects stale timestamps (replay)."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(_now().timestamp()) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    provided = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
