"""Small SMTP server for protocol tests: toggle PIPELINING / CHUNKING / AUTH / SMTPUTF8, reject chosen
addresses, and simulate network latency *per round trip* (replies to everything that arrived in one read
are sent together after one delay, like a real server behind a WAN link)."""

from __future__ import annotations

import asyncio
import base64
import socket
import ssl
from dataclasses import dataclass, field


@dataclass
class ServerOptions:
    pipelining: bool = True
    chunking: bool = True
    smtputf8: bool = True
    auth: tuple[str, ...] = ()           # e.g. ("PLAIN", "LOGIN"); empty = no AUTH advertised
    credentials: tuple[str, str] = ("user", "secret")
    reject_rcpt: set[str] = field(default_factory=set)
    reject_mail: set[str] = field(default_factory=set)
    reject_data: bool = False
    latency: float = 0.0
    starttls: ssl.SSLContext | None = None     # advertise STARTTLS and upgrade with this context
    implicit_tls: ssl.SSLContext | None = None  # TLS from the first byte (port 465 style)


@dataclass
class Received:
    sender: str
    recipient: str
    body: bytes
    via: str  # DATA | BDAT


class ScriptableSmtp:
    def __init__(self, options: ServerOptions | None = None) -> None:
        self.options = options or ServerOptions()
        self.messages: list[Received] = []
        self.commands: list[str] = []
        self.round_trips = 0
        self.connections = 0
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._server = await asyncio.start_server(self._handle, sock=sock, ssl=self.options.implicit_tls)
        self.tls_upgrades = 0

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    _tls_active = False

    def _ehlo(self) -> bytes:
        o = self.options
        ext = ["sink.test"] + (["PIPELINING"] if o.pipelining else []) + (["CHUNKING"] if o.chunking else [])
        ext += ["8BITMIME"] + (["SMTPUTF8"] if o.smtputf8 else []) + ([f"AUTH {' '.join(o.auth)}"] if o.auth else [])
        if o.starttls is not None and not self._tls_active:
            ext.append("STARTTLS")
        return b"".join(f"250{'-' if i < len(ext) - 1 else ' '}{e}\r\n".encode() for i, e in enumerate(ext))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        o = self.options
        buf = b""
        state: dict[str, object] = {"mail": None, "rcpt": None, "ok": False}
        pending: list[bytes] = [b"220 sink.test ESMTP\r\n"]
        mode = "cmd"  # cmd | data | bdat | auth_user | auth_pass
        bdat_left = 0
        bdat_buf = b""
        data_buf = b""
        try:
            while True:
                if pending:
                    if o.latency:
                        await asyncio.sleep(o.latency)
                    self.round_trips += 1
                    writer.write(b"".join(pending))
                    await writer.drain()
                    pending = []
                chunk = await reader.read(65536)
                if not chunk:
                    return
                buf += chunk
                while True:
                    if mode == "bdat":
                        take = buf[:bdat_left]
                        bdat_buf += take
                        buf = buf[len(take):]
                        bdat_left -= len(take)
                        if bdat_left:
                            break
                        mode = "cmd"
                        if state["ok"]:
                            self.messages.append(Received(str(state["mail"]), str(state["rcpt"]), bdat_buf, "BDAT"))
                            pending.append(b"250 2.0.0 queued\r\n")
                        else:
                            pending.append(b"503 5.5.1 No valid recipients\r\n")
                        state.update(mail=None, rcpt=None, ok=False)
                        bdat_buf = b""
                        continue
                    if b"\r\n" not in buf:
                        break
                    line, buf = buf.split(b"\r\n", 1)
                    if mode == "data":
                        if line == b".":
                            mode = "cmd"
                            if o.reject_data:
                                pending.append(b"554 5.7.1 Message rejected\r\n")
                            else:
                                self.messages.append(Received(str(state["mail"]), str(state["rcpt"]),
                                                              _unstuff(data_buf), "DATA"))
                                pending.append(b"250 2.0.0 queued\r\n")
                            state.update(mail=None, rcpt=None, ok=False)
                            data_buf = b""
                        else:
                            data_buf += line + b"\r\n"
                        continue
                    if mode == "auth_user":
                        mode = "auth_pass"
                        state["user"] = base64.b64decode(line).decode()
                        pending.append(b"334 UGFzc3dvcmQ6\r\n")
                        continue
                    if mode == "auth_pass":
                        mode = "cmd"
                        ok = (state["user"], base64.b64decode(line).decode()) == o.credentials
                        pending.append(b"235 2.7.0 ok\r\n" if ok else b"535 5.7.8 bad credentials\r\n")
                        continue
                    text = line.decode("utf-8", "replace")
                    self.commands.append(text.split(" ", 1)[0].upper() if not text.upper().startswith("BDAT")
                                         else "BDAT")
                    upper = text.upper()
                    if upper.startswith("EHLO"):
                        pending.append(self._ehlo())
                    elif upper.startswith("AUTH PLAIN"):
                        _, user, password = base64.b64decode(text.split()[2]).decode().split("\0")
                        pending.append(b"235 2.7.0 ok\r\n" if (user, password) == o.credentials
                                       else b"535 5.7.8 bad credentials\r\n")
                    elif upper.startswith("AUTH LOGIN"):
                        mode = "auth_user"
                        pending.append(b"334 VXNlcm5hbWU6\r\n")
                    elif upper.startswith("MAIL FROM:"):
                        sender = text[10:].split(">")[0].lstrip("<")
                        if sender in o.reject_mail:
                            pending.append(b"550 5.7.1 Sender rejected\r\n")
                        else:
                            state["mail"] = sender
                            pending.append(b"250 2.1.0 ok\r\n")
                    elif upper.startswith("RCPT TO:"):
                        rcpt = text[8:].split(">")[0].lstrip("<")
                        if state["mail"] is None:
                            pending.append(b"503 5.5.1 MAIL first\r\n")
                        elif rcpt in o.reject_rcpt:
                            pending.append(b"550 5.1.1 User unknown\r\n")
                        else:
                            state.update(rcpt=rcpt, ok=True)
                            pending.append(b"250 2.1.5 ok\r\n")
                    elif upper == "DATA":
                        if state["ok"]:
                            mode = "data"
                            pending.append(b"354 go ahead\r\n")
                        else:
                            pending.append(b"554 5.5.1 No valid recipients\r\n")
                    elif upper.startswith("BDAT"):
                        bdat_left = int(text.split()[1])
                        mode = "bdat"
                    elif upper == "STARTTLS" and o.starttls is not None:
                        writer.write(b"220 2.0.0 ready for TLS\r\n")
                        await writer.drain()
                        await writer.start_tls(o.starttls)
                        self._tls_active = True
                        self.tls_upgrades += 1
                        buf = b""
                    elif upper == "RSET":
                        state.update(mail=None, rcpt=None, ok=False)
                        pending.append(b"250 2.0.0 reset\r\n")
                    elif upper == "QUIT":
                        writer.write(b"221 bye\r\n")
                        await writer.drain()
                        return
                    else:
                        pending.append(b"250 ok\r\n")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


def _unstuff(data: bytes) -> bytes:
    lines = data.split(b"\r\n")
    return b"\r\n".join(line[1:] if line.startswith(b"..") else line for line in lines)
