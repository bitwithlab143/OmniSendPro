"""Public endpoints: provider delivery webhooks and recipient unsubscribe (DS-05, DS-07, DS-09).

Both are unauthenticated by design and protected by signatures (HMAC) instead.
"""

from __future__ import annotations

import html
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.api.deps import DB, client_ip
from app.core.errors import ApiError
from app.core.redis import get_redis
from app.core.security import decrypt_secret, verify_webhook_signature
from app.models import Provider
from app.schemas.domain import WebhookPayload
from app.services import events

router = APIRouter(tags=["public"])

MAX_WEBHOOK_BYTES = 1024 * 1024


@router.post("/hooks/providers/{provider_id}")
async def provider_webhook(provider_id: uuid.UUID, request: Request, db: DB) -> dict[str, int]:
    provider = await db.get(Provider, provider_id)
    if provider is None or not provider.webhook_secret_encrypted:
        raise ApiError(404, "not_found", "Unknown webhook endpoint")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise ApiError(413, "payload_too_large", "Webhook payload too large")
    if not verify_webhook_signature(
        decrypt_secret(provider.webhook_secret_encrypted),
        body,
        request.headers.get("x-omnisend-timestamp", ""),
        request.headers.get("x-omnisend-signature", ""),
    ):
        raise ApiError(401, "invalid_signature", "Invalid webhook signature")
    try:
        payload = WebhookPayload.model_validate(json.loads(body))
    except (ValueError, ValidationError) as exc:
        raise ApiError(422, "invalid_payload", "Malformed webhook payload") from exc
    result = await events.ingest(db, provider.id, [e.model_dump() for e in payload.events])
    return {"accepted": result.accepted, "duplicates": result.duplicates, "unknown": result.unknown,
            "rejected": len(result.errors)}


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{title}</title><style>
:root{{color-scheme:light dark}}body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
margin:0;min-height:100vh;display:grid;place-items:center;background:Canvas;color:CanvasText}}
main{{max-width:420px;padding:32px;border:1px solid color-mix(in srgb,CanvasText 15%,transparent);
border-radius:12px;margin:16px}}h1{{font-size:1.25rem;margin:0 0 8px}}p{{opacity:.8;line-height:1.5}}
button{{font:inherit;padding:10px 16px;border-radius:8px;border:0;background:#2563eb;color:#fff;cursor:pointer}}
</style></head><body><main><h1>{title}</h1><p>{message}</p>{form}</main></body></html>"""


def _page(title: str, message: str, form: str = "", status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), message=html.escape(message), form=form),
        status_code=status,
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
                 "Cache-Control": "no-store"},
    )


@router.get("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe_page(token: str) -> HTMLResponse:
    # GET never changes state (mail scanners follow links); the button POSTs.
    if events.parse_unsubscribe_token(token) is None:
        return _page("Link not valid", "This unsubscribe link is invalid or has been altered.", status=404)
    form = f'<form method="post" action="/api/v1/u/{html.escape(token)}"><button type="submit">Unsubscribe</button></form>'
    return _page("Unsubscribe", "Click the button to stop receiving these emails.", form)


@router.post("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe(token: str, request: Request, db: DB) -> HTMLResponse:
    # Also serves RFC 8058 one-click (body "List-Unsubscribe=One-Click").
    ip = client_ip(request) or "unknown"
    redis = get_redis()
    key = f"ratelimit:unsubscribe:{ip}"
    if await redis.incr(key) > 60:
        return _page("Too many requests", "Please try again in a minute.", status=429)
    await redis.expire(key, 60)
    recipient = await events.unsubscribe(db, token)
    if recipient is None:
        return _page("Link not valid", "This unsubscribe link is invalid or has been altered.", status=404)
    return _page("You are unsubscribed", "You will no longer receive these emails from this sender.")
