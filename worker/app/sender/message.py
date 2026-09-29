"""Message rendering: merge variables, unsubscribe headers/footer, MIME assembly.

Security: variable values are HTML-escaped in the HTML part and stripped of CR/LF in headers, so
recipient data can never inject markup or extra headers.
"""

from __future__ import annotations

import html
import re
import uuid
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate
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
