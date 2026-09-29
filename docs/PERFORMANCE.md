# Performance & tuning

How fast OmniSendPro sends, what limits it, and which knobs to turn. Target from ARCHITECTURE.md §20/§64:
**1,500,000 messages/hour ≈ 417 msgs/s**, reached by adding workers and provider capacity.

> Speed is always bounded by what your providers allow. Raise limits only to the values in your
> provider agreement — never use extra IPs/accounts to get around a provider's limits (§3).

## Measured results

Load test: `tests/load/run_load.py` — real API (+ scheduler), real worker processes, a local SMTP sink,
60k recipients, HTML message with a merge variable. Machine: **one 4-vCPU VM running everything**
(API, PostgreSQL, Redis, workers and the sink), so absolute numbers are conservative.

| Change | 1 worker | 2 workers | 3 workers |
|---|---|---|---|
| Before (EmailMessage per recipient) | 396 msgs/s | 726 msgs/s | – |
| After (compiled messages, pooled SMTP, uvloop) | **2,307 msgs/s** | **3,734 msgs/s** | **4,803 msgs/s** (~17.3M/hour) |

Network latency is what dominates with a real provider. With **25 ms** simulated round-trip time per
SMTP reply (1 worker, 4 jobs):

| SMTP connections per job | Throughput |
|---|---|
| 4 (default) | 204 msgs/s |
| 16 | 790 msgs/s (3.9×) |

Time from *Start* to the first message: **~1 s** (was up to one scheduler interval + one worker poll).

## What was optimised (and why)

1. **Compiled messages (worker).** Profiling showed ~50% of worker CPU in Python's `email` package
   re-parsing/re-folding headers for every recipient. A campaign is now compiled once per job and each
   recipient is rendered straight to wire bytes (base64 bodies, CRLF, RFC 2047 only when needed).
   ~110× faster per message; equivalence with the reference builder is covered by tests
   (`worker/tests/test_fast_message.py`). International (non-ASCII) addresses use the SMTPUTF8 path.
2. **Connection reuse across jobs (worker).** Authenticated SMTP connections are kept per provider for
   the life of the worker process instead of reconnecting (TCP + TLS + AUTH) for every job; idle pools
   close after 60 s; a rotated password gets a new pool.
3. **Provider `max_connections` (admin → Provider).** The number of simultaneous SMTP sessions each
   worker opens to that provider. Over real networks this is the biggest lever (see table above).
4. **uvloop** event loop for the worker (automatic when installed).
5. **Immediate start.** Starting/resuming a campaign wakes the scheduler leader through Redis, and idle
   workers poll at most every 2 s.
6. **Bug fixes found by load testing:** CSV rows crossing the 64 KB detection sample were split in two;
   several API processes could race creating the bootstrap admin at startup.

## Tuning checklist (fastest first)

| Knob | Where | Effect |
|---|---|---|
| **Max connections** | Admin → Providers → Edit | Parallel SMTP sessions per worker for that provider. Set to what the provider allows (often 10–50). |
| **Per-second / hourly / daily limits** | Provider and user limits | Hard caps. The effective per-second rate is min(provider, user); DEGRADED providers run at 50%. |
| **Worker capacity** | Admin → Workers (default 80 msgs/s) | Per-worker cap. Raise it (e.g. 1,000+) once the provider limits are set. |
| **Max concurrent jobs** | Admin → Workers | Jobs processed in parallel per worker (default 4). |
| **More workers** | Provision more workers (one `WORKER_ID` each) | Throughput scales ~linearly until providers or the database saturate. One worker process ≈ one CPU core. |
| `SMTP_CONNECTIONS_PER_JOB` | Worker env (default 4) | Used when a provider has no `max_connections`. |
| Batch size | Campaign (500 / 1,000 / 5,000) | Bigger batches = fewer claims; smaller = smoother spreading over workers. 1,000 is a good default. |
| `REDIS_URL` on workers | Worker env | Required for a per-provider rate limit shared across workers. |
| API processes | `uvicorn --workers N` (image default 2) | The API is not the bottleneck at these rates (<20% CPU at 4.8k msgs/s). |

Rule of thumb for a real provider: `msgs/s per worker ≈ connections ÷ (≈4 × round-trip time)`, e.g.
16 connections at 25 ms ≈ 160–200 msgs/s. For 1.5M/hour (417/s) at 25 ms RTT: ~40 total connections,
e.g. 3 workers × max_connections 16 — if the provider permits it.

## How to measure

```bash
# local SMTP sink, 2 sink processes, 25 ms simulated provider latency
.venv/bin/python tests/load/run_load.py --recipients 60000 --workers 2 --jobs 4 --connections 16 \
    --sink-processes 2 --latency-ms 25
```

Options: `--provider-rate` (per-second limit), `--batch-size`, `--api-workers`, `--scheduler-interval`,
`--profile <file>` (py-spy folded stacks of worker 0; summarise with `tests/load/fold.py`).
The test only ever talks to its own local sink.

## Next steps

- **SMTP PIPELINING (RFC 2920):** send MAIL/RCPT/DATA in one round trip — roughly halves per-message
  latency on real networks (needs a custom SMTP client; aiosmtplib does not pipeline).
- **Multi-process worker launcher:** one command that runs N worker processes (one per core) with their
  own identities.
- **Results path:** at >10k msgs/s per campaign, move counter updates to Redis and flush to Postgres
  periodically to avoid row contention on the campaign row.
- **Load test at scale** on separate machines (API, DB, workers) with a real provider sandbox.
