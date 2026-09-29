"""Bounce / feedback-loop mailbox polling over IMAP — design DS-16, ADR-011.

imaplib is blocking, so every IMAP session runs in a worker thread. Messages are fetched with
BODY.PEEK[] (not marked read) and only marked \\Seen — or deleted — after they were processed, so a
crash between fetch and processing never loses a report.
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models import Provider
from app.services import inbound, providers
from app.services import settings as settings_service

log = logging.getLogger("omnisend.bounces")

IMAP_TIMEOUT = 30
FETCH_LIMIT = 200


@dataclass(slots=True)
class MailboxConfig:
    host: str
    port: int
    ssl: bool
    username: str
    password: str
    folder: str = "INBOX"
    delete_processed: bool = False


def config_for(provider: Provider) -> MailboxConfig | None:
    cfg = provider.bounce_mailbox
    if not cfg or not provider.bounce_mailbox_secret_encrypted:
        return None
    return MailboxConfig(
        host=cfg["host"], port=int(cfg.get("port") or 993), ssl=bool(cfg.get("ssl", True)),
        username=cfg["username"], password=decrypt_secret(provider.bounce_mailbox_secret_encrypted),
        folder=cfg.get("folder") or "INBOX", delete_processed=bool(cfg.get("delete_processed", False)),
    )


def _connect(cfg: MailboxConfig) -> imaplib.IMAP4:
    if cfg.ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ssl.create_default_context(),
                                                  timeout=IMAP_TIMEOUT)
    else:
        client = imaplib.IMAP4(cfg.host, cfg.port, timeout=IMAP_TIMEOUT)
        if "STARTTLS" not in client.capabilities:
            _logout(client)
            raise imaplib.IMAP4.error("Server does not offer STARTTLS; refusing to send the password in clear text")
        client.starttls(ssl_context=ssl.create_default_context())
    client.login(cfg.username, cfg.password)
    typ, _ = client.select(_quote(cfg.folder))
    if typ != "OK":
        raise imaplib.IMAP4.error(f"Cannot open folder {cfg.folder!r}")
    return client


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fetch_unseen(cfg: MailboxConfig, limit: int) -> list[tuple[bytes, bytes]]:
    client = _connect(cfg)
    try:
        typ, data = client.uid("SEARCH", None, "UNSEEN")
        uids = (data[0] or b"").split()[:limit] if typ == "OK" else []
        out: list[tuple[bytes, bytes]] = []
        for uid in uids:
            typ, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            for part in parts:
                if isinstance(part, tuple) and len(part) == 2:
                    out.append((uid, part[1]))
                    break
        return out
    finally:
        _logout(client)


def _mark(cfg: MailboxConfig, uids: list[bytes]) -> None:
    if not uids:
        return
    client = _connect(cfg)
    try:
        uid_set = b",".join(uids).decode()
        if cfg.delete_processed:
            client.uid("STORE", uid_set, "+FLAGS.SILENT", r"(\Deleted)")
            client.expunge()
        else:
            client.uid("STORE", uid_set, "+FLAGS.SILENT", r"(\Seen)")
    finally:
        _logout(client)


def _logout(client: imaplib.IMAP4) -> None:
    try:
        client.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _probe(cfg: MailboxConfig) -> int:
    client = _connect(cfg)
    try:
        typ, data = client.uid("SEARCH", None, "UNSEEN")
        return len((data[0] or b"").split()) if typ == "OK" else 0
    finally:
        _logout(client)


async def test_mailbox(cfg: MailboxConfig) -> dict[str, Any]:
    blocked = await providers.blocked_host(cfg.host)
    if blocked:
        return {"ok": False, "message": blocked}
    try:
        unseen = await asyncio.to_thread(_probe, cfg)
    except (TimeoutError, imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:300]}
    return {"ok": True, "message": f"Connected; {unseen} unread message(s) waiting", "unseen": unseen}


async def poll_provider(db: AsyncSession, provider: Provider) -> dict[str, int]:
    """Fetch, process and mark one batch of reports for one provider."""
    cfg = config_for(provider)
    if cfg is None:
        return {"fetched": 0, "accepted": 0, "duplicates": 0, "unmatched": 0, "unrecognised": 0}
    totals = {"fetched": 0, "accepted": 0, "duplicates": 0, "unmatched": 0, "unrecognised": 0}
    try:
        blocked = await providers.blocked_host(cfg.host)
        if blocked:
            raise OSError(blocked)
        messages = await asyncio.to_thread(_fetch_unseen, cfg, FETCH_LIMIT)
        done: list[bytes] = []
        for uid, raw in messages:
            result = await inbound.process_raw(db, provider, raw)
            totals["fetched"] += 1
            totals["accepted"] += result.accepted
            totals["duplicates"] += result.duplicates
            totals["unmatched"] += result.unmatched
            if result.kind == "unknown":
                totals["unrecognised"] += 1
                continue  # leave unread for a human to look at
            done.append(uid)
        await asyncio.to_thread(_mark, cfg, done)
        provider.bounce_last_error = None
    except (TimeoutError, imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        provider.bounce_last_error = f"{type(exc).__name__}: {exc}"[:512]
        log.warning("bounce_mailbox_failed", extra={"provider": provider.provider_name, "error": str(exc)})
    provider.bounce_last_polled_at = datetime.now(UTC)
    await db.commit()
    if totals["fetched"]:
        log.info("bounce_mailbox_polled", extra={"provider": provider.provider_name, **totals})
    return totals


async def poll_due(db: AsyncSession) -> int:
    """Scheduler hook: poll every configured mailbox whose interval elapsed. Returns reports accepted."""
    interval = int(await settings_service.get(db, "bounce_poll_interval_seconds"))
    cutoff = datetime.now(UTC) - timedelta(seconds=interval)
    providers = (
        await db.execute(
            select(Provider).where(
                Provider.bounce_mailbox.is_not(None),
                (Provider.bounce_last_polled_at.is_(None)) | (Provider.bounce_last_polled_at < cutoff),
            )
        )
    ).scalars().all()
    accepted = 0
    for provider in providers:
        accepted += (await poll_provider(db, provider))["accepted"]
    return accepted
