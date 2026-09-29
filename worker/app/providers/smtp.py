"""SMTP provider with a small connection pool (one message per SMTP transaction).

Connections are reused across messages; a connection that breaks is dropped and re-opened once before
the error is surfaced to the caller for classification.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ssl
import time
from email.message import EmailMessage
from typing import Any

import aiosmtplib


class SmtpProvider:
    def __init__(self, config: dict[str, Any], pool_size: int = 4, timeout: float = 30.0) -> None:
        self.host = config["host"]
        self.port = int(config["port"])
        self.username = config.get("username")
        self.password = config.get("password")
        self.tls_mode = config.get("tls_mode", "starttls")
        self.timeout = timeout
        self.pool_size = max(1, pool_size)
        self._idle: asyncio.Queue[aiosmtplib.SMTP] = asyncio.Queue()
        self._all: list[aiosmtplib.SMTP] = []
        self._sem = asyncio.Semaphore(self.pool_size)
        self._tls = ssl.create_default_context()
        self.last_used = time.monotonic()
        self.users = 0  # jobs currently using this pool (shared pools only)

    async def _open(self) -> aiosmtplib.SMTP:
        client = aiosmtplib.SMTP(
            hostname=self.host,
            port=self.port,
            use_tls=self.tls_mode == "ssl",
            start_tls=True if self.tls_mode == "starttls" else False,
            tls_context=self._tls,
            timeout=self.timeout,
        )
        await client.connect()
        if self.username:
            await client.login(self.username, self.password or "")
        self._all.append(client)
        return client

    async def connect(self) -> None:
        """Make sure one working connection exists, so auth/connection problems surface as a job-level
        failure before any recipient is attempted. Reuses an idle connection when the pool has one."""
        while not self._idle.empty():
            client = self._idle.get_nowait()
            if client.is_connected:
                await self._idle.put(client)
                return
            self._discard(client)
        client = await self._open()
        await self._idle.put(client)

    async def _checkout(self) -> aiosmtplib.SMTP:
        try:
            client = self._idle.get_nowait()
        except asyncio.QueueEmpty:
            return await self._open()
        if not client.is_connected:
            self._discard(client)
            return await self._open()
        return client

    def _discard(self, client: aiosmtplib.SMTP) -> None:
        with contextlib.suppress(ValueError):
            self._all.remove(client)
        with contextlib.suppress(Exception):
            client.close()

    @staticmethod
    async def _transmit(client: aiosmtplib.SMTP, message: EmailMessage | bytes, sender: str,
                        recipient: str) -> tuple[dict[str, Any], str]:
        if isinstance(message, bytes):
            # Pre-rendered wire bytes (fast path); aiosmtplib still applies dot-stuffing.
            return await client.sendmail(sender, [recipient], message)
        return await client.send_message(message, sender=sender, recipients=[recipient])

    async def send(self, message: EmailMessage | bytes, sender: str, recipient: str) -> str | None:
        self.last_used = time.monotonic()
        async with self._sem:
            client = await self._checkout()
            try:
                errors, response = await self._transmit(client, message, sender, recipient)
            except (aiosmtplib.SMTPServerDisconnected, aiosmtplib.SMTPConnectError, ConnectionError):
                self._discard(client)
                client = await self._open()  # one reconnect, then let the caller classify
                try:
                    errors, response = await self._transmit(client, message, sender, recipient)
                except BaseException:
                    self._discard(client)
                    raise
            except aiosmtplib.SMTPResponseException:
                # The server answered: the connection is still usable after RSET.
                with contextlib.suppress(Exception):
                    await client.rset()
                await self._idle.put(client)
                raise
            except BaseException:
                self._discard(client)
                raise
            await self._idle.put(client)
            if errors:
                code, text = next(iter(errors.values()))
                raise aiosmtplib.SMTPRecipientRefused(code, text, recipient)
            return response

    async def close(self) -> None:
        for client in list(self._all):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.quit(), timeout=5)
            self._discard(client)


class SmtpPoolRegistry:
    """Keeps one connection pool per provider configuration for the life of the worker process.

    Consecutive jobs for the same provider reuse already-open (and already-authenticated) connections
    instead of paying TCP + TLS + AUTH again for every job. The pool is keyed by the full connection
    configuration including a password fingerprint, so a rotated credential gets a fresh pool.
    """

    def __init__(self, max_connections: int, timeout: float, idle_ttl: float = 60.0) -> None:
        self.max_connections = max(1, max_connections)
        self.timeout = timeout
        self.idle_ttl = idle_ttl
        self._pools: dict[tuple[Any, ...], SmtpProvider] = {}

    @staticmethod
    def _key(config: dict[str, Any]) -> tuple[Any, ...]:
        secret = hashlib.sha256((config.get("password") or "").encode()).hexdigest()
        return (config.get("id"), config["host"], int(config["port"]), config.get("username"),
                config.get("tls_mode", "starttls"), secret, config.get("max_connections"))

    def acquire(self, config: dict[str, Any]) -> SmtpProvider:
        key = self._key(config)
        pool = self._pools.get(key)
        if pool is None:
            # A provider's own connection limit caps the pool shared by every job on this worker.
            size = int(config.get("max_connections") or self.max_connections)
            pool = self._pools[key] = SmtpProvider(config, pool_size=size, timeout=self.timeout)
        pool.users += 1
        pool.last_used = time.monotonic()
        return pool

    def release(self, pool: SmtpProvider) -> None:
        pool.users = max(0, pool.users - 1)
        pool.last_used = time.monotonic()

    async def sweep(self) -> None:
        """Close pools nobody used for ``idle_ttl`` seconds (providers keep idle sessions short)."""
        now = time.monotonic()
        for key, pool in list(self._pools.items()):
            if pool.users == 0 and now - pool.last_used > self.idle_ttl:
                del self._pools[key]
                await pool.close()

    async def close(self) -> None:
        for pool in list(self._pools.values()):
            await pool.close()
        self._pools.clear()
