"""Asynchronous bounces (DSN) and complaints (ARF): parser, inbound endpoint, IMAP poller (design DS-16)."""

from __future__ import annotations

import hashlib
import hmac
import imaplib
import time
from typing import Any

import pytest
from httpx import AsyncClient

from app.db.session import sessionmaker
from app.models import Provider
from app.services import bounce_mailbox, inbound
from tests.conftest import Session, create_provider
from tests.test_events_and_health import _sent_campaign


def dsn(message_id: str | None, email: str, action: str = "failed", status: str = "5.1.1",
        campaign_id: str | None = None) -> bytes:
    original = []
    if message_id:
        original.append(f"Message-ID: {message_id}")
    if campaign_id:
        original.append(f"X-OmniSend-Campaign: {campaign_id}")
    original += [f"To: {email}", "Subject: Hello"]
    return (
        "From: MAILER-DAEMON@mx.example.net\r\n"
        "To: bounces@sender.example\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n'
        "\r\n--B\r\nContent-Type: text/plain\r\n\r\nYour message could not be delivered.\r\n"
        "\r\n--B\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mx.example.net\r\n\r\n"
        f"Final-Recipient: rfc822; {email.upper()}\r\n"
        f"Action: {action}\r\n"
        f"Status: {status}\r\n"
        f"Diagnostic-Code: smtp; 550 {status} mailbox\r\n  unavailable\r\n"
        "\r\n--B\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        + "\r\n".join(original) + "\r\n"
        "\r\n--B--\r\n"
    ).encode()


def arf(message_id: str, email: str, feedback_type: str = "abuse") -> bytes:
    return (
        "From: fbl@isp.example\r\n"
        "To: fbl@sender.example\r\n"
        "Subject: Abuse report\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=feedback-report; boundary="F"\r\n'
        "\r\n--F\r\nContent-Type: text/plain\r\n\r\nThis is an email abuse report.\r\n"
        "\r\n--F\r\nContent-Type: message/feedback-report\r\n\r\n"
        f"Feedback-Type: {feedback_type}\r\n"
        "User-Agent: ExampleFBL/1.0\r\n"
        "Version: 1\r\n"
        "\r\n--F\r\nContent-Type: message/rfc822\r\n\r\n"
        f"Message-ID: {message_id}\r\n"
        f"To: <{email}>\r\n"
        "Subject: Hello\r\n\r\nbody\r\n"
        "\r\n--F--\r\n"
    ).encode()


def _sign(secret: str, body: bytes) -> dict[str, str]:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-OmniSend-Timestamp": str(ts), "X-OmniSend-Signature": f"sha256={sig}",
            "Content-Type": "message/rfc822"}


# --------------------------------------------------------------------------- parser (unit)


@pytest.mark.parametrize(("action", "status", "expected"), [
    ("failed", "5.1.1", ("bounced", "hard")),
    ("failed", "5.1.10", ("bounced", "hard")),
    ("failed", "5.2.1", ("bounced", "hard")),
    ("failed", "5.2.2", ("bounced", "soft")),   # mailbox full
    ("failed", "5.7.1", ("bounced", "soft")),   # policy rejection: not the address's fault
    ("failed", None, ("bounced", "soft")),
    ("delayed", "4.4.7", ("deferred", None)),
    ("delivered", "2.0.0", ("delivered", None)),
    ("relayed", None, ("delivered", None)),
])
def test_classify_dsn(action: str, status: str | None, expected: tuple[str, str | None]) -> None:
    assert inbound.classify_dsn(action, status) == expected


def test_parse_dsn() -> None:
    report = inbound.parse_report(dsn("<abc@sender.example>", "User@Example.org", campaign_id="c-1"))
    assert report.kind == "dsn"
    assert report.original_message_id == "<abc@sender.example>"
    assert report.campaign_id == "c-1"
    [ev] = report.events
    assert ev["type"] == "bounced" and ev["bounce_type"] == "hard"
    assert ev["email"] == "user@example.org" and ev["error_code"] == "5.1.1"
    assert ev["error_message"] == "smtp; 550 5.1.1 mailbox unavailable"


def test_parse_arf() -> None:
    report = inbound.parse_report(arf("<abc@sender.example>", "user@example.org"))
    assert report.kind == "arf"
    [ev] = report.events
    assert ev == {"type": "complained", "email": "user@example.org", "provider_message_id": "<abc@sender.example>",
                  "campaign_id": None, "error_code": "arf:abuse", "error_message": "ExampleFBL/1.0"}
    assert inbound.parse_report(arf("<abc@sender.example>", "user@example.org", "not-spam")).events == []


def test_parse_unrelated_mail() -> None:
    report = inbound.parse_report(b"From: someone@example.org\r\nSubject: out of office\r\n\r\nI am away.\r\n")
    assert report.kind == "unknown" and report.events == []
    assert inbound.parse_report(b"\x00\xff garbage").kind == "unknown"


# --------------------------------------------------------------------------- inbound endpoint


async def _webhook_secret(client: AsyncClient, admin: Session, provider_id: str) -> str:
    r = await client.post(f"/api/v1/admin/providers/{provider_id}/webhook-secret", headers=admin.headers)
    return r.json()["webhook_secret"]


async def test_inbound_dsn_and_arf(client: AsyncClient, admin: Session, user: Session) -> None:
    provider, campaign, recipients = await _sent_campaign(client, admin, user, n=4)
    secret = await _webhook_secret(client, admin, provider["id"])
    url = f"/api/v1/hooks/providers/{provider['id']}/inbound"

    hard = dsn("msg-0", recipients[0]["email"])
    r = await client.post(url, content=hard, headers={"Content-Type": "message/rfc822"})
    assert r.status_code == 401
    r = await client.post(url, content=hard, headers=_sign(secret, hard))
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "dsn", "accepted": 1, "duplicates": 0, "unmatched": 0}
    r = await client.post(url, content=hard, headers=_sign(secret, hard))
    assert r.json()["duplicates"] == 1

    soft = dsn("msg-1", recipients[1]["email"], status="5.2.2")
    assert (await client.post(url, content=soft, headers=_sign(secret, soft))).json()["accepted"] == 1
    delayed = dsn("msg-3", recipients[3]["email"], action="delayed", status="4.4.7")
    assert (await client.post(url, content=delayed, headers=_sign(secret, delayed))).json()["accepted"] == 1
    complaint = arf("msg-2", recipients[2]["email"])
    assert (await client.post(url, content=complaint, headers=_sign(secret, complaint))).json() == {
        "kind": "arf", "accepted": 1, "duplicates": 0, "unmatched": 0}

    unknown = dsn("<nobody@elsewhere>", "x@example.org")
    assert (await client.post(url, content=unknown, headers=_sign(secret, unknown))).json()["unmatched"] == 1
    junk = b"Subject: hi\r\n\r\nhello"
    assert (await client.post(url, content=junk, headers=_sign(secret, junk))).json()["kind"] == "unknown"

    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    # A soft bounce is stored as a deferral (the address may work later): only the hard bounce counts.
    assert stats["bounced"] == 1 and stats["complained"] == 1
    supp = (await client.get("/api/v1/admin/suppressions", headers=admin.headers)).json()["items"]
    # Soft bounces are recorded but never suppress.
    assert {(s["type"], s["email_normalized"]) for s in supp} == {
        ("hard_bounce", recipients[0]["email"]), ("complaint", recipients[2]["email"])}
    detail = (await client.get(f"/api/v1/admin/providers/{provider['id']}", headers=admin.headers)).json()
    assert detail["bounce_processed_total"] == 4


async def test_inbound_campaign_fallback_and_provider_isolation(
    client: AsyncClient, admin: Session, user: Session
) -> None:
    provider, campaign, recipients = await _sent_campaign(client, admin, user, n=2)
    other = await create_provider(client, admin, provider_name="Other SMTP")
    other_secret = await _webhook_secret(client, admin, other["id"])
    secret = await _webhook_secret(client, admin, provider["id"])

    # Some MTAs drop the Message-ID: fall back to campaign header + recipient address.
    report = dsn(None, recipients[0]["email"], campaign_id=campaign["id"])
    # A different provider must not be able to bounce messages it never sent.
    r = await client.post(f"/api/v1/hooks/providers/{other['id']}/inbound", content=report,
                          headers=_sign(other_secret, report))
    assert r.json() == {"kind": "dsn", "accepted": 0, "duplicates": 0, "unmatched": 1}
    r = await client.post(f"/api/v1/hooks/providers/{provider['id']}/inbound", content=report,
                          headers=_sign(secret, report))
    assert r.json()["accepted"] == 1
    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["bounced"] == 1


async def test_inbound_requires_enabled_endpoint(client: AsyncClient, admin: Session) -> None:
    provider = await create_provider(client, admin)
    r = await client.post(f"/api/v1/hooks/providers/{provider['id']}/inbound", content=b"x")
    assert r.status_code == 404


# --------------------------------------------------------------------------- IMAP poller


class FakeImap:
    """Just enough of imaplib.IMAP4 for the poller: UID SEARCH / FETCH / STORE."""

    error = imaplib.IMAP4.error
    mailbox: dict[bytes, bytes] = {}
    flags: dict[bytes, str] = {}
    logins: list[tuple[str, str]] = []
    fail_login = False

    def __init__(self, host: str, port: int, **_: Any) -> None:
        self.capabilities = ("IMAP4REV1",)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if FakeImap.fail_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        FakeImap.logins.append((user, password))
        return "OK", [b""]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return "OK", [str(len(self.mailbox)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return "OK", [b" ".join(u for u in self.mailbox if u not in self.flags)]
        if command == "FETCH":
            uid = args[0]
            return "OK", [(b"%s (UID %s BODY[] {%d}" % (uid, uid, len(self.mailbox[uid])), self.mailbox[uid]), b")"]
        if command == "STORE":
            for uid in args[0].split(","):
                FakeImap.flags[uid.encode()] = args[2]
            return "OK", [b""]
        raise AssertionError(command)

    def expunge(self) -> tuple[str, list[bytes]]:
        return "OK", [b""]

    def logout(self) -> None:
        return None


@pytest.fixture
def fake_imap(monkeypatch: pytest.MonkeyPatch) -> type[FakeImap]:
    FakeImap.mailbox, FakeImap.flags, FakeImap.logins, FakeImap.fail_login = {}, {}, [], False
    monkeypatch.setattr(bounce_mailbox.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(bounce_mailbox.imaplib, "IMAP4", FakeImap)

    async def resolvable(host: str) -> str | None:
        return "Refusing to connect to 169.254.169.254" if host == "metadata.internal" else None

    monkeypatch.setattr(bounce_mailbox.providers, "blocked_host", resolvable)
    return FakeImap


async def test_bounce_mailbox_poll(client: AsyncClient, admin: Session, user: Session,
                                   fake_imap: type[FakeImap]) -> None:
    provider, campaign, recipients = await _sent_campaign(client, admin, user, n=2)
    base = f"/api/v1/admin/providers/{provider['id']}/bounce-mailbox"
    cfg = {"host": "imap.example.net", "username": "bounces@sender.example", "folder": "INBOX"}
    r = await client.put(base, headers=admin.headers, json=cfg)
    assert r.status_code == 400, "a password is required the first time"
    r = await client.put(base, headers=admin.headers, json={**cfg, "password": "s3cret"})
    assert r.status_code == 200, r.text
    assert r.json()["bounce_mailbox"]["host"] == "imap.example.net"
    assert "password" not in r.json()["bounce_mailbox"]

    fake_imap.mailbox = {
        b"1": dsn("msg-0", recipients[0]["email"]),
        b"2": arf("msg-1", recipients[1]["email"]),
        b"3": b"Subject: out of office\r\n\r\naway",
    }
    r = await client.post(f"{base}/test", headers=admin.headers)
    assert r.json() == {"ok": True, "message": "Connected; 3 unread message(s) waiting", "unseen": 3}
    assert fake_imap.logins[-1] == ("bounces@sender.example", "s3cret")

    r = await client.post(f"{base}/poll", headers=admin.headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"fetched": 3, "accepted": 2, "duplicates": 0, "unmatched": 0, "unrecognised": 1,
                        "error": None}
    # Processed reports are marked read; the unrecognised one stays unread for a human.
    assert fake_imap.flags == {b"1": r"(\Seen)", b"2": r"(\Seen)"}
    r = await client.post(f"{base}/poll", headers=admin.headers)
    assert r.json()["fetched"] == 1 and r.json()["accepted"] == 0

    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["bounced"] == 1 and stats["complained"] == 1
    detail = (await client.get(f"/api/v1/admin/providers/{provider['id']}", headers=admin.headers)).json()
    assert detail["bounce_processed_total"] == 2 and detail["bounce_last_error"] is None
    assert detail["bounce_last_polled_at"] is not None

    # Changing settings without a password keeps the stored one.
    r = await client.put(base, headers=admin.headers, json={**cfg, "folder": "Bounces", "delete_processed": True})
    assert r.status_code == 200 and r.json()["bounce_mailbox"]["folder"] == "Bounces"
    fake_imap.fail_login = True
    r = await client.post(f"{base}/poll", headers=admin.headers)
    detail = (await client.get(f"/api/v1/admin/providers/{provider['id']}", headers=admin.headers)).json()
    assert "AUTHENTICATIONFAILED" in detail["bounce_last_error"]
    r = await client.post(f"{base}/test", headers=admin.headers)
    assert r.json()["ok"] is False

    # SSRF guard: link-local / metadata addresses are refused before connecting.
    await client.put(base, headers=admin.headers, json={**cfg, "host": "metadata.internal"})
    r = await client.post(f"{base}/test", headers=admin.headers)
    assert r.json() == {"ok": False, "message": "Refusing to connect to 169.254.169.254"}
    # Plain IMAP without STARTTLS is refused rather than sending the password in clear text.
    fake_imap.fail_login = False
    await client.put(base, headers=admin.headers, json={**cfg, "ssl": False, "port": 143})
    r = await client.post(f"{base}/test", headers=admin.headers)
    assert r.json()["ok"] is False and "STARTTLS" in r.json()["message"]

    logs = (await client.get("/api/v1/admin/audit-logs", headers=admin.headers)).json()["items"]
    assert "PROVIDER_BOUNCE_MAILBOX_SET" in {e["action"] for e in logs}
    r = await client.delete(base, headers=admin.headers)
    assert r.status_code == 200 and r.json()["bounce_mailbox"] is None


async def test_scheduler_polls_due_mailboxes(client: AsyncClient, admin: Session, user: Session,
                                             fake_imap: type[FakeImap]) -> None:
    provider, _, recipients = await _sent_campaign(client, admin, user, n=1)
    await client.put(f"/api/v1/admin/providers/{provider['id']}/bounce-mailbox", headers=admin.headers,
                     json={"host": "imap.example.net", "username": "b", "password": "p"})
    fake_imap.mailbox = {b"7": dsn("msg-0", recipients[0]["email"])}
    async with sessionmaker()() as db:
        assert await bounce_mailbox.poll_due(db) == 1
        # Interval not elapsed yet: not polled again.
        fake_imap.mailbox[b"8"] = dsn("msg-0", recipients[0]["email"], status="5.1.2")
        assert await bounce_mailbox.poll_due(db) == 0
        p = await db.get(Provider, provider["id"])
        assert p is not None and p.bounce_processed_total == 1
