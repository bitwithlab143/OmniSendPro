"""Pure unit tests: ids, crypto, retry schedule, classification, CSV parsing, health scoring."""

from __future__ import annotations

import base64
import io

import pytest

from app.core import security
from app.core.ids import uuid7
from app.services import settings as settings_service
from app.services.health import Window, next_status
from app.services.jobs import classify
from app.services.recipients import _rows, check_email, normalize_email


def test_uuid7_is_monotonic_and_versioned() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(i.version == 7 for i in ids[:10])


def test_encrypt_roundtrip_and_tamper() -> None:
    token = security.encrypt_secret("s3cret")
    assert token.startswith("v1:") and "s3cret" not in token
    assert security.decrypt_secret(token) == "s3cret"
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(Exception):  # noqa: B017
        security.decrypt_secret(tampered)


def test_signed_values() -> None:
    tok = security.sign_value("abc:1", "unsubscribe")
    assert security.unsign_value(tok, "unsubscribe") == "abc:1"
    assert security.unsign_value(tok, "other-purpose") is None
    assert security.unsign_value(tok + "x", "unsubscribe") is None
    assert security.unsign_value("garbage", "unsubscribe") is None


def test_password_policy() -> None:
    assert security.validate_password_strength("short") is not None
    assert security.validate_password_strength("alllowercaseletters") is not None
    assert security.validate_password_strength("Good-Passw0rd") is None
    h = security.hash_password("Good-Passw0rd")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "Good-Passw0rd")
    assert not security.verify_password(h, "bad")
    assert not security.verify_password(None, "anything")


def test_retry_schedule() -> None:
    schedule = [30, 60, 120, 300, 600]
    assert [settings_service.retry_delay(schedule, a) for a in range(1, 8)] == [30, 60, 120, 300, 600, 600, 600]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"outcome": "sent"}, "sent"),
        ({"outcome": "failed", "smtp_code": 451, "enhanced_code": "4.7.1"}, "deferred"),
        ({"outcome": "failed", "category": "timeout"}, "deferred"),
        ({"outcome": "failed", "smtp_code": 550, "enhanced_code": "5.1.1"}, "bounced"),
        ({"outcome": "failed", "smtp_code": 550, "enhanced_code": "5.7.1"}, "failed"),  # policy block ≠ bad mailbox
        ({"outcome": "failed", "smtp_code": 552}, "failed"),
        ({"outcome": "failed", "category": "invalid"}, "invalid"),
    ],
)
def test_classify(result: dict, expected: str) -> None:
    assert classify(result) == expected


def test_email_helpers() -> None:
    assert check_email("  User@Example.COM ") == "User@example.com"
    assert check_email("nope") is None
    assert check_email("a@b") is None
    assert normalize_email(" User@Example.COM ") == "user@example.com"


def test_csv_parsing_variants() -> None:
    semi = io.BytesIO(b"E-mail;Name\na@example.org;Ann\n")
    assert list(_rows(semi)) == [("a@example.org", {"name": "Ann"})]
    headerless = io.BytesIO(b"a@example.org\nb@example.org\n")
    assert [e for e, _ in _rows(headerless)] == ["a@example.org", "b@example.org"]
    bom = io.BytesIO("﻿email\nc@example.org\n".encode())
    assert [e for e, _ in _rows(bom)] == ["c@example.org"]


def test_health_scoring() -> None:
    assert Window(attempts=0).score() == 100.0
    assert Window(attempts=100, successes=100).score() == 100.0
    assert Window(attempts=100, successes=40, connection_failures=60).score() == pytest.approx(40.0)
    assert Window(attempts=1000, successes=999, complaints=1).score() == pytest.approx(95.0)
    cfg = settings_service.DEFAULTS
    from app.models.enums import ProviderStatus as S

    assert next_status(S.ACTIVE, 90, 0, cfg) == S.ACTIVE
    assert next_status(S.ACTIVE, 70, 0, cfg) == S.WARNING
    assert next_status(S.ACTIVE, 30, 0, cfg) == S.DEGRADED
    assert next_status(S.DEGRADED, 10, 1, cfg) == S.DEGRADED  # one bad window is not enough
    assert next_status(S.DEGRADED, 10, 3, cfg) == S.DISABLED
    assert next_status(S.DISABLED, 100, 0, cfg) == S.DISABLED  # manual re-enable only


def test_csv_row_crossing_sample_boundary_is_not_split() -> None:
    rows = [f"user{i}@example.org" for i in range(12000)]  # ~250 KB, crosses the 64 KB sample boundary
    data = io.BytesIO(("email\n" + "\n".join(rows) + "\n").encode())
    assert [e for e, _ in _rows(data)] == rows


def test_secrets_from_files(tmp_path) -> None:  # noqa: ANN001
    from app.core.config import Settings, file_secrets

    (tmp_path / "jwt").write_text("j" * 40 + "\n")
    (tmp_path / "key").write_text("v1:" + base64.b64encode(b"k" * 32).decode())
    env = {"JWT_SECRET_FILE": str(tmp_path / "jwt"), "ENCRYPTION_KEY_FILE": str(tmp_path / "key"),
           "WORKER_JWT_SECRET": "explicit-wins", "WORKER_JWT_SECRET_FILE": str(tmp_path / "jwt")}
    values = file_secrets(env)
    assert values == {"JWT_SECRET": "j" * 40, "ENCRYPTION_KEY": "v1:" + base64.b64encode(b"k" * 32).decode()}
    s = Settings(**values)
    assert s.jwt_secret == "j" * 40 and s.encryption_keys()["v1"] == b"k" * 32
    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        file_secrets({"JWT_SECRET_FILE": str(tmp_path / "missing")})
