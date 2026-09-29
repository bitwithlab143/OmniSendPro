# Worker

Workers are stateless delivery nodes (ARCHITECTURE.md §8–§10). They never receive database
credentials: they authenticate to the Worker API with a provisioned credential (ADR-004).

## Provisioning
1. Admin → Workers → *Provision worker* → copy `WORKER_ID` and `WORKER_CREDENTIAL` (shown once).
2. Run the worker with:

| Variable | Default | Meaning |
|---|---|---|
| `WORKER_API_URL` | `http://localhost:8000` | Control plane (use the dedicated worker-API endpoint, nginx :8082) |
| `WORKER_ID` | – | Provisioned id |
| `WORKER_CREDENTIAL` / `WORKER_CREDENTIAL_FILE` | – | Secret (prefer a mounted secret file) |
| `REDIS_URL` | unset | Enables the shared per-provider rate limit (recommended with >1 worker) |
| `WORKER_CAPACITY` | from admin | Max messages/sec for this node |
| `WORKER_MAX_CONCURRENT_JOBS` | from admin | Parallel jobs |
| `SMTP_CONNECTIONS_PER_JOB` | 4 | Parallel SMTP sessions per job when the provider has no *Max connections* set |
| `SMTP_TIMEOUT_SECONDS` | 30 | Per-operation timeout |

`python worker.py` or the `worker` image (uses uvloop when installed). Stop with SIGTERM: the worker stops claiming, running jobs
report their results and release unsent recipients, then it sends an `offline` heartbeat.

## Lifecycle
`token → register → heartbeat (every 10 s) → claim → lease renew (every lease/3) → send → results
(batched every second / 200 results) → ack | failure | release`.

- **Claim** returns the batch's still-`queued` recipients after a just-before-send suppression check,
  the campaign content, the provider's SMTP settings and the effective per-second limit (provider ∩
  user; halved while the provider is DEGRADED). Quota is reserved atomically at claim and the unused
  part refunded when the attempt ends.
- **Lease renewal** answers `continue` or `stop` (campaign paused/cancelled, provider disabled).
- **Results** are per recipient: `sent` (with `provider_message_id` = our Message-ID) or `failed` with
  SMTP code/enhanced code/category. The control plane classifies them (ADR-008): 4xx/timeouts →
  deferred and retried on the backoff schedule; 5.1.x / 550–553 → hard bounce + global suppression;
  other 5xx → failed.
- **Failure** is for systemic problems (auth, provider unreachable, 5 consecutive connection errors):
  the whole job retries with backoff and dead-letters after `max_attempts`.
- A crashed worker's jobs are recovered when its lease expires or it is marked OFFLINE (90 s without
  heartbeat); a late report from the old attempt gets `409 stale_attempt`.

## Messages
Merge variables `{{column}}` from the CSV (HTML-escaped in HTML, CR/LF stripped in headers),
`{{email}}`, `{{unsubscribe_url}}`. Every message has `List-Unsubscribe` + `List-Unsubscribe-Post:
List-Unsubscribe=One-Click`; an unsubscribe footer is appended unless the template links it already.

## Performance
Messages are compiled once per job and rendered straight to wire bytes; authenticated SMTP
connections are reused across jobs (per provider, closed after 60 s idle). The provider's
*Max connections* setting decides how many SMTP sessions run in parallel. See `PERFORMANCE.md`.

## Scaling
Add workers to add throughput; capacity is pull-based (a worker only claims when it has a free job
slot), so no central assignment is needed. The 1.5M/hour target (~417 msg/s) is reached by
provider limits × workers, e.g. 6 workers × 80 msg/s — see ARCHITECTURE.md §20 and §64.
