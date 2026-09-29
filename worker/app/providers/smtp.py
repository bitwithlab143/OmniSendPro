"""SMTP provider with a small connection pool (one message per SMTP transaction).

Connections are reused across messages; a connection that breaks is dropped and re-opened once before
the error is surfaced to the caller for classification.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
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
        """Open one connection eagerly so auth/connection problems surface as a job-level failure."""
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

    async def send(self, message: EmailMessage, sender: str, recipient: str) -> str | None:
        async with self._sem:
            client = await self._checkout()
            try:
                errors, response = await client.send_message(message, sender=sender, recipients=[recipient])
            except (aiosmtplib.SMTPServerDisconnected, aiosmtplib.SMTPConnectError, ConnectionError):
                self._discard(client)
                client = await self._open()  # one reconnect, then let the caller classify
                try:
                    errors, response = await client.send_message(message, sender=sender, recipients=[recipient])
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
