# Security

Implements ARCHITECTURE.md §32–§34, §45, §48 (design DS-10, DS-11, ADR-005/006).

| Control | Implementation |
|---|---|
| Passwords | Argon2id (argon2-cffi defaults), min 12 chars with 3 character classes, rehash on login when parameters change, constant-ish time for unknown users |
| Brute force | 5 failures → 15-min account lock; 20 login attempts/min/IP (Redis); nginx `limit_req` on login/MFA |
| Admin 2FA | TOTP mandatory for admin-panel roles, enrolled on first login; codes single-use (replay cache) |
| Sessions | 15-min access JWT in memory only; refresh token in httpOnly, SameSite=Strict cookie, rotated on every use, stored hashed; reuse revokes the family; password/role change revokes all sessions and invalidates outstanding access tokens (session version claim) |
| CSRF | SameSite=Strict + mandatory `X-Requested-With` on cookie endpoints; API otherwise uses bearer tokens |
| Worker auth | Per-worker credential (hashed), exchanged for a short-lived token with a separate signing key and audience; worker tokens cannot call user/admin APIs; disabled workers are rejected |
| RBAC | Role → permission matrix in `app/core/rbac.py`; only SUPER_ADMIN manages admin accounts; users can only reach their own campaigns and assigned providers |
| Secrets at rest | Provider passwords, webhook secrets and TOTP seeds: AES-256-GCM with versioned keys; never returned by the API; redacted from audit logs |
| Input validation | Pydantic schemas, header-injection checks on subject/from name, LIKE-wildcard escaping, CSV formula neutralisation in exports, request-size limits (API + nginx) |
| Outbound | Provider test refuses link-local/metadata addresses; email merge values are HTML-escaped / header-stripped; preview iframe is fully sandboxed |
| Webhooks | HMAC-SHA256 over timestamp + body, 5-minute replay window, de-duplication |
| Headers | API: nosniff, DENY framing, no-referrer, CSP `default-src 'none'`, HSTS in production. Panels (nginx): strict CSP without inline scripts |
| Audit | Append-only `audit_logs` for every sensitive action (who, what, old/new, IP, user agent) |
| Compliance | Consent confirmation to start, automatic unsubscribe/bounce/complaint suppression, one-click unsubscribe, provider limits enforced |

## Reporting a vulnerability
Do not open a public issue; contact the maintainers privately.

## Known follow-ups
See `PROGRESS.md` → Blockers & risks (e.g. API keys for programmatic access, alert delivery, restricted Redis ACL for workers).
