"""Message rendering: merge variables, unsubscribe headers/footer, MIME assembly.

Security: variable values are HTML-escaped in the HTML part and stripped of CR/LF in headers, so
recipient data can never inject markup or extra headers.
"""

from __future__ import annotations

import base64
import html
import re
import secrets
import time
import uuid
from email import policy as _policy
from email.header import Header
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Any

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(p|div|h[1-6]|li|tr)>|<br\s*/?>", re.I)
_WS = re.compile(r"[ \t]+")


def _clean_header(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


def render(template: str | None, variables: dict[str, Any], mode: str) -> str:
    if not template:
        return ""

    def sub(match: re.Match[str]) -> str:
        value = str(variables.get(match.group(1).lower(), variables.get(match.group(1), "")))
        if mode == "html":
            return html.escape(value, quote=True)
        if mode == "header":
            return _clean_header(value)
        return value

    return _VAR.sub(sub, template)


def html_to_text(markup: str) -> str:
    text = _BLOCK.sub("\n", markup)
    text = html.unescape(_TAG.sub("", text))
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def build(campaign: dict[str, Any], recipient: dict[str, Any]) -> tuple[EmailMessage, str]:
    """Return (message, message_id). The Message-ID doubles as provider_message_id for webhooks."""
    variables = {**{k.lower(): v for k, v in (recipient.get("variables") or {}).items()},
                 "email": recipient["email"], "unsubscribe_url": recipient["unsubscribe_url"]}
    from_email = campaign["from_email"]
    domain = from_email.rsplit("@", 1)[-1]
    message_id = f"<{uuid.uuid4().hex}@{domain}>"
    unsub = recipient["unsubscribe_url"]

    msg = EmailMessage()
    msg["Subject"] = render(campaign.get("subject") or "", variables, "header")
    local, _, dom = from_email.partition("@")
    msg["From"] = Address(display_name=_clean_header(campaign.get("from_name") or ""), username=local, domain=dom)
    msg["To"] = recipient["email"]
    if campaign.get("reply_to"):
        msg["Reply-To"] = campaign["reply_to"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id
    # RFC 2369 + RFC 8058 one-click unsubscribe (required by major mailbox providers for bulk mail).
    msg["List-Unsubscribe"] = f"<{unsub}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["X-OmniSend-Campaign"] = str(campaign["id"])

    html_tpl = campaign.get("html_body")
    text_tpl = campaign.get("text_body")
    has_unsub_link = any("unsubscribe_url" in (t or "") for t in (html_tpl, text_tpl))

    text_part = render(text_tpl, variables, "text") if text_tpl else html_to_text(render(html_tpl, variables, "html"))
    if not has_unsub_link:
        text_part += f"\n\n--\nUnsubscribe: {unsub}"
    msg.set_content(text_part)
    if html_tpl:
        html_part = render(html_tpl, variables, "html")
        if not has_unsub_link:
            footer = (f'<p style="font-size:12px;color:#6b7280;margin-top:24px">'
                      f'<a href="{html.escape(unsub, quote=True)}">Unsubscribe</a></p>')
            html_part = html_part.replace("</body>", footer + "</body>") if "</body>" in html_part else html_part + footer
        msg.add_alternative(html_part, subtype="html")
    return msg, message_id


# --------------------------------------------------------------------------- fast path
#
# Building an EmailMessage per recipient spends most of the worker's CPU re-parsing and re-folding
# headers (see docs/PERFORMANCE.md). A campaign is identical for every recipient except for merge
# variables, so the MIME skeleton is compiled once per job and each recipient is rendered straight to
# wire-format bytes. Bodies are base64 (always 7-bit safe, lines ≤ 76) and the MIME boundary starts
# with "=_", which can never occur inside base64 data.


_SMTPUTF8 = _policy.SMTPUTF8.clone(linesep="\r\n")


def _b64_lines(text: str) -> bytes:
    return base64.encodebytes(text.encode("utf-8")).replace(b"\n", b"\r\n")


def _encode_header(value: str) -> str:
    """RFC 2047-encode and fold when needed; plain ASCII short values pass through untouched."""
    if value.isascii() and len(value) <= 900:
        return value
    return Header(value, "utf-8" if not value.isascii() else "us-ascii", maxlinelen=76).encode()


class _DateCache:
    """RFC 5322 Date header, recomputed at most once per second."""

    def __init__(self) -> None:
        self._second = -1
        self._value = ""

    def get(self) -> str:
        now = int(time.time())
        if now != self._second:
            self._second, self._value = now, formatdate(now, usegmt=True)
        return self._value


class CompiledCampaign:
    """Per-job compiled message template. ``render`` returns (wire bytes, Message-ID)."""

    def __init__(self, campaign: dict[str, Any]) -> None:
        self.campaign = campaign
        self.from_email: str = campaign["from_email"]
        self.domain = self.from_email.rsplit("@", 1)[-1]
        self.subject_tpl: str = campaign.get("subject") or ""
        self.html_tpl: str | None = campaign.get("html_body")
        self.text_tpl: str | None = campaign.get("text_body")
        self.has_unsub_link = any("unsubscribe_url" in (t or "") for t in (self.html_tpl, self.text_tpl))
        self.boundary = "=_osp_" + secrets.token_hex(12)
        self._dates = _DateCache()
        from_name = _clean_header(campaign.get("from_name") or "")
        static = [
            f"From: {formataddr((from_name, self.from_email), charset='utf-8')}",
        ]
        if campaign.get("reply_to"):
            static.append(f"Reply-To: {_clean_header(campaign['reply_to'])}")
        static += [
            "List-Unsubscribe-Post: List-Unsubscribe=One-Click",
            f"X-OmniSend-Campaign: {campaign['id']}",
            "MIME-Version: 1.0",
        ]
        self._static = "\r\n".join(static)
        # Subject / bodies without merge variables are rendered once for the whole job.
        self._static_subject = (None if _VAR.search(self.subject_tpl)
                                else _encode_header(render(self.subject_tpl, {}, "header")))

    def render(self, recipient: dict[str, Any]) -> tuple[bytes, str]:
        email = recipient["email"]
        if not email.isascii():
            # Internationalised addresses need SMTPUTF8 (raw UTF-8 headers): use the email-package path.
            msg, message_id = build(self.campaign, recipient)
            return msg.as_bytes(policy=_SMTPUTF8), message_id
        variables = {**{k.lower(): v for k, v in (recipient.get("variables") or {}).items()},
                     "email": email, "unsubscribe_url": recipient["unsubscribe_url"]}
        unsub = recipient["unsubscribe_url"]
        message_id = f"<{uuid.uuid4().hex}@{self.domain}>"
        subject = self._static_subject
        if subject is None:
            subject = _encode_header(render(self.subject_tpl, variables, "header"))

        text_part = (render(self.text_tpl, variables, "text") if self.text_tpl
                     else html_to_text(render(self.html_tpl, variables, "html")))
        if not self.has_unsub_link:
            text_part += f"\n\n--\nUnsubscribe: {unsub}"
        text_part = text_part.replace("\r\n", "\n").replace("\n", "\r\n")

        headers = (
            f"{self._static}\r\n"
            f"To: {email}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: {self._dates.get()}\r\n"
            f"Message-ID: {message_id}\r\n"
            f"List-Unsubscribe: <{unsub}>\r\n"
        )
        if not self.html_tpl:
            body = (
                b"Content-Type: text/plain; charset=\"utf-8\"\r\n"
                b"Content-Transfer-Encoding: base64\r\n\r\n"
            ) + _b64_lines(text_part)
            return headers.encode() + body, message_id

        html_part = render(self.html_tpl, variables, "html")
        if not self.has_unsub_link:
            footer = (f'<p style="font-size:12px;color:#6b7280;margin-top:24px">'
                      f'<a href="{html.escape(unsub, quote=True)}">Unsubscribe</a></p>')
            html_part = html_part.replace("</body>", footer + "</body>") if "</body>" in html_part else html_part + footer
        b = self.boundary
        parts = [
            (headers + f'Content-Type: multipart/alternative; boundary="{b}"\r\n\r\n'
             f"--{b}\r\nContent-Type: text/plain; charset=\"utf-8\"\r\nContent-Transfer-Encoding: base64\r\n\r\n").encode(),
            _b64_lines(text_part),
            (f"--{b}\r\nContent-Type: text/html; charset=\"utf-8\"\r\nContent-Transfer-Encoding: base64\r\n\r\n").encode(),
            _b64_lines(html_part),
            f"--{b}--\r\n".encode(),
        ]
        return b"".join(parts), message_id
