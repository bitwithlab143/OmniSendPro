"""Pipelined SMTP client (design DS-22, P3-15) against a scriptable server."""

from __future__ import annotations

import ssl
import time

import aiosmtplib
import pytest

from app.providers.smtp import SmtpProvider
from app.providers.smtp_client import SmtpConnection, dot_stuff, normalize
from tests.scriptable_smtp import ScriptableSmtp, ServerOptions

BODY = b"Subject: hi\r\n\r\nline one\r\n.starts with a dot\r\n..two dots\r\nend\r\n"


async def _server(**options) -> ScriptableSmtp:  # noqa: ANN003
    server = ScriptableSmtp(ServerOptions(**options))
    await server.start()
    return server


def test_body_helpers() -> None:
    assert normalize(b"a\nb\rc") == b"a\r\nb\r\nc\r\n"
    assert dot_stuff(b".x\r\nok\r\n.y\r\n") == b"..x\r\nok\r\n..y\r\n"


@pytest.mark.parametrize(("options", "mode", "trips"), [
    ({"pipelining": True, "chunking": True}, "chunking", 1),
    ({"pipelining": True, "chunking": False}, "pipelining", 2),
    ({"pipelining": False, "chunking": False}, "lockstep", 4),
    ({"pipelining": False, "chunking": True}, "lockstep", 4),  # CHUNKING alone is not enough
])
async def test_modes_and_round_trips(options: dict, mode: str, trips: int) -> None:
    server = await _server(**options)
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none", timeout=5)
        await conn.connect()
        assert conn.mode == mode
        before = server.round_trips
        await conn.send("news@brand.example", "a@example.org", BODY)
        assert server.round_trips - before == trips
        # The body arrives byte-for-byte (dot-stuffing undone by the server in DATA mode).
        [msg] = server.messages
        assert (msg.sender, msg.recipient, msg.body) == ("news@brand.example", "a@example.org", BODY)
        assert msg.via == ("BDAT" if mode == "chunking" else "DATA")
        await conn.quit()
    finally:
        await server.stop()


async def test_pipelining_can_be_disabled() -> None:
    server = await _server()
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none", pipelining=False)
        await conn.connect()
        assert conn.mode == "lockstep"
    finally:
        await server.stop()


@pytest.mark.parametrize("chunking", [True, False])
async def test_rejections_keep_the_connection_in_sync(chunking: bool) -> None:
    server = await _server(chunking=chunking, reject_rcpt={"gone@example.org"}, reject_mail={"bad@brand.example"})
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none")
        await conn.connect()
        with pytest.raises(aiosmtplib.SMTPRecipientRefused) as rcpt:
            await conn.send("news@brand.example", "gone@example.org", BODY)
        assert rcpt.value.code == 550 and "5.1.1" in rcpt.value.message
        await conn.rset()
        with pytest.raises(aiosmtplib.SMTPSenderRefused):
            await conn.send("bad@brand.example", "a@example.org", BODY)
        await conn.rset()
        # Every pipelined reply was consumed: the next transaction on the same connection works.
        await conn.send("news@brand.example", "ok@example.org", BODY)
        assert [m.recipient for m in server.messages] == ["ok@example.org"]
        assert server.connections == 1
    finally:
        await server.stop()


async def test_data_rejection() -> None:
    server = await _server(chunking=False, reject_data=True)
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none")
        await conn.connect()
        with pytest.raises(aiosmtplib.SMTPDataError) as err:
            await conn.send("news@brand.example", "a@example.org", BODY)
        assert err.value.code == 554
    finally:
        await server.stop()


@pytest.mark.parametrize("mechanisms", [("PLAIN", "LOGIN"), ("LOGIN",)])
async def test_auth(mechanisms: tuple[str, ...]) -> None:
    server = await _server(auth=mechanisms)
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none", username="user", password="secret")
        await conn.connect()
        await conn.send("news@brand.example", "a@example.org", BODY)
        bad = SmtpConnection("127.0.0.1", server.port, tls_mode="none", username="user", password="wrong")
        with pytest.raises(aiosmtplib.SMTPAuthenticationError):
            await bad.connect()
    finally:
        await server.stop()


async def test_international_addresses() -> None:
    server = await _server(smtputf8=True)
    plain = await _server(smtputf8=False)
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none")
        await conn.connect()
        await conn.send("news@brand.example", "jürgen@exämple.de", BODY)
        assert server.messages[0].recipient == "jürgen@exämple.de"
        conn2 = SmtpConnection("127.0.0.1", plain.port, tls_mode="none")
        await conn2.connect()
        with pytest.raises(ValueError, match="SMTPUTF8"):
            await conn2.send("news@brand.example", "jürgen@exämple.de", BODY)
        with pytest.raises(ValueError):
            await conn2.send("news@brand.example", "a@example.org>\r\nRCPT TO:<x@y", BODY)
    finally:
        await server.stop()
        await plain.stop()


async def test_disconnect_and_starttls_required() -> None:
    server = await _server()
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="starttls")
        with pytest.raises(aiosmtplib.SMTPException, match="STARTTLS"):
            await conn.connect()
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="none")
        await conn.connect()
        await server.stop()
        conn.close()
        with pytest.raises(aiosmtplib.SMTPServerDisconnected):
            await conn.send("news@brand.example", "a@example.org", BODY)
    finally:
        await server.stop()
    with pytest.raises(aiosmtplib.SMTPConnectError):
        await SmtpConnection("127.0.0.1", 1, tls_mode="none", timeout=2).connect()


async def test_latency_speedup_through_the_pool() -> None:
    """With 30 ms per round trip, chunking sends ~4x faster than lock-step on one connection."""
    elapsed = {}
    for mode, opts in (("chunking", {}), ("lockstep", {"pipelining": False, "chunking": False})):
        server = await _server(latency=0.03, **opts)
        try:
            pool = SmtpProvider({"host": "127.0.0.1", "port": server.port, "tls_mode": "none"}, pool_size=1)
            await pool.connect()
            start = time.perf_counter()
            for i in range(8):
                await pool.send(BODY, "news@brand.example", f"r{i}@example.org")
            elapsed[mode] = time.perf_counter() - start
            assert len(server.messages) == 8
            await pool.close()
        finally:
            await server.stop()
    assert elapsed["chunking"] * 2.5 < elapsed["lockstep"], elapsed


async def test_pool_recovers_after_rejection() -> None:
    server = await _server(reject_rcpt={"gone@example.org"})
    try:
        pool = SmtpProvider({"host": "127.0.0.1", "port": server.port, "tls_mode": "none"}, pool_size=1)
        with pytest.raises(aiosmtplib.SMTPRecipientRefused):
            await pool.send(BODY, "news@brand.example", "gone@example.org")
        await pool.send(BODY, "news@brand.example", "ok@example.org")
        assert server.connections == 1 and [m.recipient for m in server.messages] == ["ok@example.org"]
        await pool.close()
    finally:
        await server.stop()


def _tls_contexts(tmp_path) -> tuple[ssl.SSLContext, ssl.SSLContext]:  # noqa: ANN001
    """Self-signed certificate for 127.0.0.1: server context + a client context that trusts it."""
    from datetime import UTC, datetime, timedelta
    from ipaddress import ip_address

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


async def test_starttls_and_implicit_tls(tmp_path) -> None:  # noqa: ANN001
    server_ctx, client_ctx = _tls_contexts(tmp_path)
    server = await _server(starttls=server_ctx, auth=("PLAIN",))
    try:
        conn = SmtpConnection("127.0.0.1", server.port, tls_mode="starttls", tls_context=client_ctx,
                              username="user", password="secret")
        await conn.connect()
        assert server.tls_upgrades == 1 and conn.mode == "chunking", "extensions re-read after STARTTLS"
        await conn.send("news@brand.example", "a@example.org", BODY)
        # The default context verifies certificates: a self-signed server is refused.
        with pytest.raises((ssl.SSLError, aiosmtplib.SMTPException, ConnectionError)):
            await SmtpConnection("127.0.0.1", server.port, tls_mode="starttls").connect()
    finally:
        await server.stop()
    implicit = await _server(implicit_tls=server_ctx)
    try:
        conn = SmtpConnection("127.0.0.1", implicit.port, tls_mode="ssl", tls_context=client_ctx)
        await conn.connect()
        await conn.send("news@brand.example", "b@example.org", BODY)
        assert implicit.messages[0].recipient == "b@example.org"
    finally:
        await implicit.stop()
