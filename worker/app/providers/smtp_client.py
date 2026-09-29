"""Purpose-built asyncio SMTP client for the send path (design DS-22, ADR-016).

With one recipient per message, lock-step SMTP costs four round trips per message (MAIL, RCPT, DATA,
body). This client uses the extensions the server advertises to cut that down:

* PIPELINING + CHUNKING (RFC 2920 + RFC 3030): ``MAIL``, ``RCPT``, ``BDAT <n> LAST`` and the body go out in
  one write → **1 round trip**.
* PIPELINING only: ``MAIL``, ``RCPT``, ``DATA`` in one write, then the dot-stuffed body → **2 round trips**.
* Neither: classic lock-step → 4 round trips.

It never pipelines anything the server did not advertise, always reads every reply it asked for (so the
connection stays in sync after a rejection) and raises aiosmtplib's exception types, so retry
classification and connection-pool recovery are unchanged. aiosmtplib itself cannot pipeline: it parses
one reply at a time and drops data that arrives while a reply is already waiting.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import ssl
from dataclasses import dataclass, field

import aiosmtplib

_LINE_ENDINGS = re.compile(rb"\r\n|\r|\n")
_DOT_LINES = re.compile(rb"(^|\r\n)\.")
MAX_LINE = 8192


@dataclass(slots=True)
class Reply:
    code: int
    message: str

    @property
    def ok(self) -> bool:
        return 200 <= self.code < 300


@dataclass(slots=True)
class ServerInfo:
    extensions: set[str] = field(default_factory=set)
    auth: set[str] = field(default_factory=set)

    @property
    def pipelining(self) -> bool:
        return "PIPELINING" in self.extensions

    @property
    def chunking(self) -> bool:
        return "CHUNKING" in self.extensions


def normalize(body: bytes) -> bytes:
    body = _LINE_ENDINGS.sub(b"\r\n", body)
    return body if body.endswith(b"\r\n") else body + b"\r\n"


def dot_stuff(body: bytes) -> bytes:
    return _DOT_LINES.sub(rb"\1..", body)


def _address(value: str, utf8: bool) -> bytes:
    if "\r" in value or "\n" in value or "<" in value or ">" in value:
        raise ValueError(f"Invalid address {value!r}")
    return value.encode("utf-8" if utf8 else "ascii")


class SmtpConnection:
    def __init__(self, host: str, port: int, *, tls_mode: str = "starttls", username: str | None = None,
                 password: str | None = None, timeout: float = 30.0, pipelining: bool = True,
                 tls_context: ssl.SSLContext | None = None, local_hostname: str = "omnisend.local") -> None:
        self.host, self.port = host, port
        self.tls_mode = tls_mode
        self.username, self.password = username, password
        self.timeout = timeout
        self.allow_pipelining = pipelining
        self.tls_context = tls_context or ssl.create_default_context()
        self.local_hostname = local_hostname
        self.info = ServerInfo()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    # ------------------------------------------------------------------ connection

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def mode(self) -> str:
        if self.allow_pipelining and self.info.pipelining and self.info.chunking:
            return "chunking"
        if self.allow_pipelining and self.info.pipelining:
            return "pipelining"
        return "lockstep"

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.port, limit=1 << 16,
                    ssl=self.tls_context if self.tls_mode == "ssl" else None,
                    server_hostname=self.host if self.tls_mode == "ssl" else None,
                ),
                self.timeout,
            )
        except TimeoutError as exc:
            raise aiosmtplib.SMTPConnectTimeoutError(f"Timed out connecting to {self.host}:{self.port}") from exc
        except OSError as exc:
            raise aiosmtplib.SMTPConnectError(f"Error connecting to {self.host}:{self.port}: {exc}") from exc
        greeting = await self._read()
        if greeting.code != 220:
            self.close()
            raise aiosmtplib.SMTPConnectResponseError(greeting.code, greeting.message)
        await self._ehlo()
        if self.tls_mode == "starttls":
            if "STARTTLS" not in self.info.extensions:
                self.close()
                raise aiosmtplib.SMTPException("Server does not support STARTTLS")
            reply = await self._command(b"STARTTLS")
            if reply.code != 220:
                self.close()
                raise aiosmtplib.SMTPResponseException(reply.code, reply.message)
            assert self._writer is not None
            await self._writer.start_tls(self.tls_context, server_hostname=self.host)
            await self._ehlo()
        if self.username:
            await self._auth()

    async def _ehlo(self) -> None:
        reply = await self._command(b"EHLO " + self.local_hostname.encode())
        if reply.code != 250:
            reply = await self._command(b"HELO " + self.local_hostname.encode())
            if reply.code != 250:
                raise aiosmtplib.SMTPHeloError(reply.code, reply.message)
            self.info = ServerInfo()
            return
        info = ServerInfo()
        for line in reply.message.split("\n")[1:]:
            words = line.strip().upper().split()
            if not words:
                continue
            info.extensions.add(words[0])
            if words[0] == "AUTH":
                info.auth.update(words[1:])
        self.info = info

    async def _auth(self) -> None:
        user, password = (self.username or ""), (self.password or "")
        if "PLAIN" in self.info.auth or not self.info.auth:
            token = base64.b64encode(f"\0{user}\0{password}".encode()).decode()
            reply = await self._command(b"AUTH PLAIN " + token.encode())
        else:
            reply = await self._command(b"AUTH LOGIN")
            if reply.code == 334:
                reply = await self._command(base64.b64encode(user.encode()))
            if reply.code == 334:
                reply = await self._command(base64.b64encode(password.encode()))
        if reply.code != 235:
            raise aiosmtplib.SMTPAuthenticationError(reply.code, reply.message)

    # ------------------------------------------------------------------ I/O

    async def _read(self) -> Reply:
        assert self._reader is not None
        lines: list[str] = []
        while True:
            try:
                raw = await asyncio.wait_for(self._reader.readline(), self.timeout)
            except TimeoutError as exc:
                self.close()
                raise aiosmtplib.SMTPReadTimeoutError("Timed out waiting for server response") from exc
            except (ConnectionError, asyncio.LimitOverrunError, ValueError) as exc:
                self.close()
                raise aiosmtplib.SMTPServerDisconnected(f"Connection lost: {exc}") from exc
            if not raw:
                self.close()
                raise aiosmtplib.SMTPServerDisconnected("Unexpected EOF received")
            if len(raw) > MAX_LINE:
                self.close()
                raise aiosmtplib.SMTPResponseException(500, "Response too long")
            try:
                code = int(raw[:3])
            except ValueError as exc:
                self.close()
                raise aiosmtplib.SMTPResponseException(500, f"Malformed reply: {raw[:80]!r}") from exc
            lines.append(raw[4:].decode("utf-8", "replace").strip())
            if raw[3:4] != b"-":
                return Reply(code, "\n".join(lines))

    async def _write(self, data: bytes) -> None:
        if not self.is_connected:
            raise aiosmtplib.SMTPServerDisconnected("Connection lost")
        assert self._writer is not None
        self._writer.write(data)
        try:
            await asyncio.wait_for(self._writer.drain(), self.timeout)
        except TimeoutError as exc:
            self.close()
            raise aiosmtplib.SMTPTimeoutError("Timed out sending data") from exc
        except ConnectionError as exc:
            self.close()
            raise aiosmtplib.SMTPServerDisconnected(f"Connection lost: {exc}") from exc

    async def _command(self, line: bytes) -> Reply:
        await self._write(line + b"\r\n")
        return await self._read()

    # ------------------------------------------------------------------ transactions

    async def send(self, sender: str, recipient: str, message: bytes) -> str:
        """Send one message to one recipient; returns the server's final reply text."""
        utf8 = not (sender.isascii() and recipient.isascii())
        if utf8 and "SMTPUTF8" not in self.info.extensions:
            raise ValueError("Internationalised address and the server does not support SMTPUTF8")
        mail = b"MAIL FROM:<" + _address(sender, utf8) + b">" + (b" SMTPUTF8" if utf8 else b"")
        rcpt = b"RCPT TO:<" + _address(recipient, utf8) + b">"
        body = normalize(message)
        mode = self.mode
        if mode == "chunking":
            await self._write(mail + b"\r\n" + rcpt + b"\r\n" + b"BDAT %d LAST\r\n" % len(body) + body)
            mail_r, rcpt_r, data_r = await self._read(), await self._read(), await self._read()
            self._check(mail_r, rcpt_r, sender, recipient)
            if not data_r.ok:
                raise aiosmtplib.SMTPDataError(data_r.code, data_r.message)
            return data_r.message
        if mode == "pipelining":
            await self._write(mail + b"\r\n" + rcpt + b"\r\nDATA\r\n")
            mail_r, rcpt_r, start_r = await self._read(), await self._read(), await self._read()
            if start_r.code == 354 and not (mail_r.ok and rcpt_r.ok):
                # Some servers accept DATA despite a failed envelope: end it with an empty message.
                await self._write(b".\r\n")
                await self._read()
            self._check(mail_r, rcpt_r, sender, recipient)
            if start_r.code != 354:
                raise aiosmtplib.SMTPDataError(start_r.code, start_r.message)
        else:
            mail_r = await self._command(mail)
            self._check(mail_r, None, sender, recipient)
            rcpt_r = await self._command(rcpt)
            self._check(mail_r, rcpt_r, sender, recipient)
            start_r = await self._command(b"DATA")
            if start_r.code != 354:
                raise aiosmtplib.SMTPDataError(start_r.code, start_r.message)
        await self._write(dot_stuff(body) + b".\r\n")
        final = await self._read()
        if not final.ok:
            raise aiosmtplib.SMTPDataError(final.code, final.message)
        return final.message

    @staticmethod
    def _check(mail_r: Reply, rcpt_r: Reply | None, sender: str, recipient: str) -> None:
        if not mail_r.ok:
            raise aiosmtplib.SMTPSenderRefused(mail_r.code, mail_r.message, sender)
        if rcpt_r is not None and not rcpt_r.ok:
            raise aiosmtplib.SMTPRecipientRefused(rcpt_r.code, rcpt_r.message, recipient)

    async def rset(self) -> None:
        reply = await self._command(b"RSET")
        if not reply.ok:
            raise aiosmtplib.SMTPResponseException(reply.code, reply.message)

    async def noop(self) -> None:
        await self._command(b"NOOP")

    async def quit(self) -> None:
        with contextlib.suppress(Exception):
            await self._command(b"QUIT")
        self.close()

    def close(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._writer = None
