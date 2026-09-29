# API reference (overview)

Base path `/api/v1` (ARCHITECTURE.md §53). The live OpenAPI schema is served at `/api/openapi.json`
and Swagger UI at `/api/docs` in non-production environments; it is the canonical contract.

## Conventions
- JSON everywhere; errors are `{"error": {"code", "message", "details"}}`.
- Lists use cursor pagination: `?limit=50&cursor=<opaque>` → `{"items": [...], "next_cursor": "…" | null}` (max 200).
- Auth: `Authorization: Bearer <access token>` (15 min). Refresh token is an httpOnly, SameSite=Strict
  cookie scoped to `/api/v1/auth`; refresh/logout also require `X-Requested-With: XMLHttpRequest`.
- Every mutating admin call is written to the audit log.

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
| Dashboard & reports [`reports.read`] | `GET /dashboard`, `GET /reports/summary?days=`, `GET /reports/breakdown?group=user\|provider` |
| Users [`users.*`] | `GET/POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}/suspend\|activate\|disable`, `PUT /users/{id}/limits`, `POST /users/{id}/password`, `POST /users/{id}/reset-2fa` |
| Providers [`providers.*`] | `GET/POST /providers`, `GET/PATCH /providers/{id}`, `PUT /providers/{id}/secret`, `POST /providers/{id}/enable\|disable\|test`, `GET /providers/{id}/dns?selector=`, `GET /providers/{id}/health`, `POST/DELETE /providers/{id}/webhook-secret` |
| Assignments [`providers.*`] | `GET/POST /assignments`, `DELETE /assignments/{id}` |
| Campaigns [`campaigns.*`] | `GET/POST /campaigns` (`view=pending\|processing\|completed\|failed`), `GET/PATCH /campaigns/{id}`, `POST/DELETE/GET /campaigns/{id}/recipients`, `POST /campaigns/{id}/start\|pause\|resume\|cancel`, `GET /campaigns/{id}/stats\|report\|report.csv\|jobs` |
| Workers [`workers.*`] | `GET/POST /workers` (POST returns the credential once), `GET/PATCH /workers/{id}`, `POST /workers/{id}/disable\|enable\|rotate-credential`, `GET /workers/{id}/heartbeats` |
| Queues [`queues.*`] | `GET /queues`, `GET /queues/jobs?status=`, `GET /queues/jobs/{id}`, `POST /queues/jobs/{id}/requeue` (dead-letter only) |
| Suppressions [`suppressions.*`] | `GET/POST /suppressions`, `DELETE /suppressions/{id}` (complaints cannot be removed) |
| Settings [`settings.*`] | `GET/PUT /settings` |
| Audit [`audit.read`] | `GET /audit-logs?action=&resource=&resource_id=` |

## User — `/user` (always scoped to the caller)
`GET/PATCH /profile`, `POST /profile/password`, `POST /profile/2fa/setup|enable|disable`,
`GET /dashboard`, `GET /providers` (assigned only, no host/credentials), `GET /options`,
`GET/POST /campaigns`, `GET/PATCH /campaigns/{id}`, recipients upload/list/clear,
`POST /campaigns/{id}/start|pause|resume|cancel`, `GET /campaigns/{id}/stats|report|report.csv`,
`GET /jobs`, `GET /reports?days=`.

Starting a campaign requires `{"consent_confirmed": true}` and passes the DS-04 guards (ready,
provider assigned & healthy, From domain matches the provider, per-campaign recipient limit).

## Worker — `/worker` (see WORKER.md)
`POST /token`, `POST /register`, `POST /heartbeat`, `GET /health`, `POST /jobs/claim` (204 when idle),
`POST /jobs/{id}/lease|results|ack|failure|release` — every job call carries the `attempt_id`.

## Public
- `POST /hooks/providers/{provider_id}` — delivery events. Headers `X-OmniSend-Timestamp` (unix seconds)
  and `X-OmniSend-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`; timestamps older
  than 5 minutes are rejected. Body: `{"events": [{"type": "delivered|bounced|complained|deferred|unsubscribed",
  "provider_message_id": "<Message-ID>", "bounce_type": "hard|soft", "error_code", "error_message"}]}`.
  Events are de-duplicated on (provider, message id, type).
- `GET /u/{token}` shows an unsubscribe confirmation page; `POST /u/{token}` unsubscribes (also RFC 8058 one-click).
