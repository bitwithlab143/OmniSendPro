"""Provider connection testing and sender-authentication DNS checks (§3, §17, DS-06)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosmtplib
import dns.asyncresolver
import dns.exception
import dns.resolver

from app.core.security import decrypt_secret
from app.models import Provider
from app.models.enums import TlsMode

TEST_TIMEOUT = 10


@dataclass(slots=True)
class TestResult:
    ok: bool
    message: str
    latency_ms: int | None = None


async def _blocked_host(host: str) -> str | None:
    """Refuse link-local / metadata targets (SSRF guard for an admin-supplied host)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "Host name could not be resolved"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return f"Refusing to connect to {ip}"
    return None


async def test_connection(provider: Provider) -> TestResult:
    blocked = await _blocked_host(provider.host)
    if blocked:
        return TestResult(False, blocked)
    password = decrypt_secret(provider.credential.encrypted_secret) if provider.credential else None
    client = aiosmtplib.SMTP(
        hostname=provider.host,
        port=provider.port,
        use_tls=provider.tls_mode == TlsMode.SSL,
        start_tls=True if provider.tls_mode == TlsMode.STARTTLS else False,
        timeout=TEST_TIMEOUT,
    )
    started = datetime.now(UTC)
    try:
        await client.connect()
        if provider.username:
            await client.login(provider.username, password or "")
        await client.noop()
        latency = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return TestResult(True, "Connected and authenticated" if provider.username else "Connected", latency)
    except aiosmtplib.SMTPAuthenticationError as exc:
        return TestResult(False, f"Authentication failed ({exc.code})")
    except aiosmtplib.SMTPException as exc:
        return TestResult(False, f"SMTP error: {exc}"[:500])
    except (OSError, TimeoutError) as exc:
        return TestResult(False, f"Connection failed: {exc}"[:500])
    finally:
        try:
            await client.quit()
        except Exception:  # noqa: BLE001, S110 - best effort close
            client.close()


async def _txt(name: str) -> list[str]:
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answer = await resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return [b"".join(r.strings).decode(errors="replace") for r in answer]


async def check_sender_domain(domain: str, dkim_selector: str | None = None) -> dict[str, object]:
    domain = domain.strip().lower().rstrip(".")
    spf_records, dmarc_records = await asyncio.gather(_txt(domain), _txt(f"_dmarc.{domain}"))
    spf = next((r for r in spf_records if r.lower().startswith("v=spf1")), None)
    dmarc = next((r for r in dmarc_records if r.lower().startswith("v=dmarc1")), None)
    dkim: str | None = None
    if dkim_selector:
        dkim_records = await _txt(f"{dkim_selector}._domainkey.{domain}")
        dkim = next((r for r in dkim_records if "p=" in r), None)
    warnings = []
    if not spf:
        warnings.append("No SPF record found")
    if not dmarc:
        warnings.append("No DMARC record found")
    if dkim_selector and not dkim:
        warnings.append(f"No DKIM key found for selector '{dkim_selector}'")
    return {
        "domain": domain,
        "spf": spf,
        "dmarc": dmarc,
        "dkim_selector": dkim_selector,
        "dkim": dkim,
        "ok": not warnings,
        "warnings": warnings,
    }
