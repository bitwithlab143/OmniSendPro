"""Map SMTP / network exceptions to the result categories understood by the control plane (ADR-008).

Per-recipient outcomes are reported in results; *systemic* problems (authentication, the provider being
unreachable) abort the job with a job-level failure so it is retried with backoff as a whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import aiosmtplib

_ENHANCED = re.compile(r"\b([245]\.\d{1,3}\.\d{1,3})\b")


@dataclass(slots=True)
class Classified:
    systemic: bool
    category: str  # rejected | transient | timeout | connection | invalid | auth | provider | other
    smtp_code: int | None = None
    enhanced_code: str | None = None
    message: str = ""
    transient: bool = True


def _enhanced(message: str) -> str | None:
    match = _ENHANCED.search(message or "")
    return match.group(1) if match else None


def classify_exception(exc: BaseException) -> Classified:
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return Classified(True, "auth", exc.code, _enhanced(exc.message), exc.message, transient=True)
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        inner = exc.recipients[0] if exc.recipients else None
        if inner is not None:
            return classify_exception(inner)
    if isinstance(exc, aiosmtplib.SMTPRecipientRefused | aiosmtplib.SMTPDataError | aiosmtplib.SMTPResponseException) \
            and not isinstance(exc, aiosmtplib.SMTPSenderRefused):
        code = exc.code
        if 400 <= code < 500:
            return Classified(False, "transient", code, _enhanced(exc.message), exc.message)
        return Classified(False, "rejected", code, _enhanced(exc.message), exc.message, transient=False)
    if isinstance(exc, aiosmtplib.SMTPSenderRefused):
        transient = 400 <= exc.code < 500
        return Classified(True, "provider", exc.code, _enhanced(exc.message), exc.message, transient=transient)
    if isinstance(exc, aiosmtplib.SMTPTimeoutError | TimeoutError):
        return Classified(False, "timeout", message=str(exc) or "timeout")
    if isinstance(exc, aiosmtplib.SMTPConnectError | aiosmtplib.SMTPServerDisconnected | ConnectionError | OSError):
        return Classified(False, "connection", message=str(exc) or type(exc).__name__)
    if isinstance(exc, ValueError | UnicodeError):
        return Classified(False, "invalid", message=str(exc), transient=False)
    return Classified(False, "other", message=f"{type(exc).__name__}: {exc}", transient=False)
