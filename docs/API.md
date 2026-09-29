# API reference (overview)

Base path `/api/v1` (ARCHITECTURE.md §53). The live OpenAPI schema is served at `/api/openapi.json`
and Swagger UI at `/api/docs` in non-production environments; it is the canonical contract.

## Conventions
- JSON everywhere; errors are `{"error": {"code", "message", "details"}}`.
- Lists use cursor pagination: `?limit=50&cursor=<opaque>` → `{"items": [...], "next_cursor": "…" | null}` (max 200).
- Auth: `Authorization: Bearer <access token>` (15 min). Refresh token is an httpOnly, SameSite=Strict
  cookie scoped to `/api/v1/auth`; refresh/logout also require `X-Requested-With: XMLHttpRequest`.
- Every mutating admin call is written to the audit log.
- **API keys** (design DS-17): `Authorization: Bearer osk_<prefix>_<secret>` on `/user/*` only. Effective
  permissions = key scopes ∩ the owner's role; profile, password, 2FA and key management are refused
  (403), as are `/auth/*`, `/admin/*` and `/worker/*`. Limited to `api_key_requests_per_minute` per key
  (Settings, default 600) → `429`. A suspended owner or a revoked/expired key → `401 invalid_api_key`.

## Auth — `/auth`
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | `{login, password, app: "admin"\|"user"}` → `ok` \| `mfa_required` \| `mfa_setup_required` (admins must enrol TOTP) |
| POST | `/auth/mfa/verify` | `{mfa_token, code}` → tokens; enables TOTP on first enrolment; codes are single-use |
| POST | `/auth/refresh` | `{app}` + cookie → rotated tokens; reuse of a rotated token revokes the whole session family |
| POST | `/auth/logout` | revokes the refresh token |
| GET | `/auth/me` | current principal incl. permissions |

## Admin — `/admin` (RBAC permission in brackets)
| Area | Endpoints |
|---|---|
| Dashboard & reports [`reports.read`] | `GET /dashboard`, `GET /dashboard/stream` (SSE), `GET /reports/summary?days=`, `GET /reports/breakdown?group=user\|provider` (served by the read replica when configured) |
| Users [`users.*`] | `GET/POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/suspend\|activate\|disable`, `PUT /users/{id}/limits`, `POST /users/{id}/password`, `POST /users/{id}/reset-2fa` |
| Providers [`providers.*`] | `GET/POST /providers`, `GET/PATCH /providers/{id}`, `PUT /providers/{id}/secret`, `POST /providers/{id}/enable\|disable\|test`, `GET /providers/{id}/dns?selector=`, `GET /providers/{id}/health`, `POST/DELETE /providers/{id}/webhook-secret`, `PUT/DELETE /providers/{id}/bounce-mailbox`, `POST /providers/{id}/bounce-mailbox/test\|poll` |
| Assignments [`providers.*`] | `GET/POST /assignments`, `DELETE /assignments/{id}` |
| Campaigns [`campaigns.*`] | `GET/POST /campaigns` (`view=pending\|processing\|completed\|failed`), `GET/PATCH /campaigns/{id}`, `POST/DELETE/GET /campaigns/{id}/recipients`, `POST /campaigns/{id}/start\|pause\|resume\|cancel`, `GET /campaigns/{id}/stats\|report\|report.csv\|jobs`, `GET /campaigns/{id}/stream` (SSE), `GET /campaigns/upload-options`, `POST /campaigns/{id}/recipients/upload-url`, `GET/POST /campaigns/{id}/recipients/imports` |
| Workers [`workers.*`] | `GET/POST /workers` (POST returns the credential once; `{"pool": true}` provisions an autoscaling pool), `GET/PATCH /workers/{id}`, `POST /workers/{id}/disable\|enable\|rotate-credential`, `GET /workers/{id}/heartbeats` |
| Queues [`queues.*`] | `GET /queues` (jobs by status, `event_inbox` pending/dead/oldest, `autoscale` open jobs/desired workers), `GET /queues/jobs?status=`, `GET /queues/jobs/{id}`, `POST /queues/jobs/{id}/requeue` (dead-letter only) |
| Suppressions [`suppressions.*`] | `GET/POST /suppressions`, `DELETE /suppressions/{id}` (complaints cannot be removed) |
| Settings [`settings.*`] | `GET/PUT /settings` |
| Audit [`audit.read`] | `GET /audit-logs?action=&resource=&resource_id=` |
| API keys [`users.*`] | `GET /api-keys?user_id=&include_revoked=`, `GET /api-keys/scopes`, `POST /api-keys` (`{user_id, name, scopes, expires_in_days}`, returns the key once), `DELETE /api-keys/{id}` (revoke) |

## User — `/user` (always scoped to the caller)
`GET/PATCH /profile`, `POST /profile/password`, `POST /profile/2fa/setup|enable|disable`,
`GET /dashboard`, `GET /providers` (assigned only, no host/credentials), `GET /options`,
`GET/POST /campaigns`, `GET/PATCH /campaigns/{id}`, recipients upload/list/clear,
`POST /campaigns/{id}/start|pause|resume|cancel`, `GET /campaigns/{id}/stats|report|report.csv`,
`GET /jobs`, `GET /reports?days=`, `GET/POST /api-keys`, `DELETE /api-keys/{id}` (panel session only),
`GET /dashboard/stream`, `GET /campaigns/{id}/stream` (SSE), `POST /campaigns/{id}/recipients/upload-url`,
`GET/POST /campaigns/{id}/recipients/imports` (object-storage imports; `GET /options` → `uploads`).

**Live updates (SSE, design DS-20):** `text/event-stream` with `event: update` (same JSON as the matching GET),
`: ping` every 15 s and `event: end` (`final` when the campaign finished, `timeout` after 5 minutes: reconnect).
Send the bearer token (fetch streaming); browsers' EventSource cannot set it.

**Large recipient files (design DS-21):** `upload-url {filename, size}` → `{url, fields, object_key, max_bytes}`;
POST the file as multipart form data (fields first, then `file`) straight to `url`; then
`POST …/recipients/imports {object_key, replace}` → 202; poll `GET …/recipients/imports` for progress.

```bash
curl -H "Authorization: Bearer $OMNISEND_API_KEY" https://panel.example.com/api/v1/user/campaigns
```

Campaign content accepts `{{csv_column}}` merge variables and template tags (`#USERID#`, `#EMAIL#`,
`#RANDOM#`, `#SUBSID#`, `#INVOICE#`, `#REF#`, `#HASH#`, `#DATE#`, `#TIME#`, `#OTP#`, `#$$#`, `#MASSAGE#`) in
the subject and bodies. `message_list` (≤ 100 strings) supplies `#MASSAGE#`; the timezone for `#DATE#`/`#TIME#`
is the `template_timezone` setting. See design DS-24.

Starting a campaign requires `{"consent_confirmed": true}` and passes the DS-04 guards (ready,
provider assigned & healthy, From domain matches the provider, per-campaign recipient limit).

## Worker — `/worker` (see WORKER.md)
`POST /token` (`{worker_id, credential, instance?}`; `instance` is required for pool credentials), `POST /register`, `POST /heartbeat`, `GET /health`, `POST /jobs/claim` (204 when idle),
`POST /jobs/{id}/lease|results|ack|failure|release` — every job call carries the `attempt_id`.

## Public
- `POST /hooks/providers/{provider_id}` — delivery events. Headers `X-OmniSend-Timestamp` (unix seconds)
  and `X-OmniSend-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`; timestamps older
  than 5 minutes are rejected. Body: `{"events": [{"type": "delivered|bounced|complained|deferred|unsubscribed",
  "provider_message_id": "<Message-ID>", "bounce_type": "hard|soft", "error_code", "error_message"}]}`.
  Events are de-duplicated on (provider, message id, type). Valid events are queued and the endpoint answers
  **202** `{"queued", "rejected"}`; the runner's processor applies them (design DS-19).
- `POST /hooks/providers/{provider_id}/inbound` — one raw RFC 5322 bounce (DSN, RFC 3464) or complaint
  (ARF, RFC 5965) email, signed exactly like the delivery webhook (max 1 MB). Answers **202** `{"queued": 1}`;
  the processor records `{"kind": "dsn|arf|unknown", "accepted", "duplicates", "unmatched"}` on the inbox row. Reports are matched by the original
  Message-ID, else by `X-OmniSend-Campaign` + recipient address, and only to messages sent through that
  provider. `infrastructure/deployment/forward-bounce.sh` pipes a message from an MTA (design DS-16).
- `GET /u/{token}` shows an unsubscribe confirmation page; `POST /u/{token}` unsubscribes (also RFC 8058 one-click).
