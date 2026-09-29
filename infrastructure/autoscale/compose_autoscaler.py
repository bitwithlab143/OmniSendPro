#!/usr/bin/env python3
"""Scale docker-compose delivery workers from the control plane's autoscaling signal (design DS-23, P4-03).

    METRICS_URL=http://localhost:8000/metrics python3 compose_autoscaler.py

Reads ``omnisend_workers_desired`` (open jobs ÷ ``autoscale_jobs_per_worker``, bounded by the min/max
settings) and runs ``docker compose up -d --scale worker=N``. The workers must use a *pool* credential
(Admin → Workers → Provision → Pool) so every container registers as its own instance.

Scale-up is immediate; scale-down waits until the lower value has held for SCALE_DOWN_COOLDOWN seconds, so
a short gap between campaigns does not stop and restart workers. Standard library only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request

METRIC = re.compile(r"^omnisend_workers_desired\s+([0-9.]+)\s*$", re.M)


def parse_desired(text: str) -> int | None:
    match = METRIC.search(text)
    return int(float(match.group(1))) if match else None


def decide(current: int, desired: int, lower_since: float | None, now: float,
           cooldown: float) -> tuple[int, float | None]:
    """Return (target replicas, time since which a lower target has been requested)."""
    if desired > current:
        return desired, None
    if desired == current:
        return current, None
    if lower_since is None:
        return current, now
    if now - lower_since >= cooldown:
        return desired, None
    return current, lower_since


def scale(n: int, compose_args: list[str]) -> None:
    cmd = ["docker", "compose", *compose_args, "up", "-d", "--no-recreate", "--scale", f"worker={n}", "worker"]
    print(f"scaling workers to {n}: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)  # noqa: S603 - fixed command, operator-provided args


def main() -> None:
    url = os.environ.get("METRICS_URL", "http://localhost:8000/metrics")
    interval = float(os.environ.get("POLL_SECONDS", "15"))
    cooldown = float(os.environ.get("SCALE_DOWN_COOLDOWN", "300"))
    compose_args = os.environ.get("COMPOSE_ARGS", "--profile worker").split()
    current = int(os.environ.get("INITIAL_WORKERS", "1"))
    lower_since: float | None = None
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - operator-provided URL
                desired = parse_desired(r.read().decode())
        except OSError as exc:
            print(f"metrics unavailable: {exc}", file=sys.stderr, flush=True)
            desired = None
        if desired is not None:
            target, lower_since = decide(current, desired, lower_since, time.monotonic(), cooldown)
            if target != current:
                try:
                    scale(target, compose_args)
                    current = target
                except subprocess.CalledProcessError as exc:
                    print(f"scale failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
