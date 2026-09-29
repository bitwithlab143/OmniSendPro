"""Client for the Worker API (/api/v1/worker/*). Handles token exchange, refresh and transient retries."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from app.config import VERSION, WorkerConfig

log = logging.getLogger("worker.api")


class StaleAttemptError(Exception):
    """The job is no longer owned by this attempt (lease expired / reclaimed)."""


class WorkerDisabledError(Exception):
    pass


class ApiClient:
    def __init__(self, config: WorkerConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=f"{config.api_url}/api/v1/worker",
            timeout=httpx.Timeout(30.0, connect=10.0),
            verify=config.verify_tls,
            transport=transport,
            headers={"User-Agent": f"omnisend-worker/{VERSION}"},
        )
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expires - 60:
                return self._token
            r = await self._http.post(
                "/token", json={"worker_id": self.config.worker_id, "credential": self.config.credential}
            )
            if r.status_code in (401, 403):
                raise WorkerDisabledError(r.text)
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.monotonic() + int(body["expires_in"])
            return self._token

    async def request(self, method: str, path: str, json: Any = None, retries: int = 5) -> httpx.Response:
        delay = 0.5
        refreshed = False
        for attempt in range(retries + 1):
            token = await self._ensure_token()
            try:
                r = await self._http.request(method, path, json=json, headers={"Authorization": f"Bearer {token}"})
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == retries:
                    raise
                log.warning("api_transport_error", extra={"path": path, "error": str(exc), "attempt": attempt})
            else:
                if r.status_code == 401 and not refreshed:
                    refreshed = True
                    await self._ensure_token(force=True)
                    continue
                if r.status_code == 409:
                    raise StaleAttemptError(r.text)
                if r.status_code < 500 and r.status_code != 429:
                    if r.status_code >= 400:
                        r.raise_for_status()
                    return r
                if attempt == retries:
                    r.raise_for_status()
                log.warning("api_server_error", extra={"path": path, "status": r.status_code, "attempt": attempt})
            await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311 - jitter
            delay = min(delay * 2, 10.0)
        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------ endpoints

    async def register(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"version": VERSION, "hostname": self.config.hostname}
        if self.config.name:
            payload["name"] = self.config.name
        if self.config.capacity:
            payload["capacity"] = self.config.capacity
        if self.config.max_concurrent_jobs:
            payload["max_concurrent_jobs"] = self.config.max_concurrent_jobs
        return (await self.request("POST", "/register", payload)).json()

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return (await self.request("POST", "/heartbeat", payload, retries=1)).json()

    async def claim(self) -> dict[str, Any] | None:
        r = await self.request("POST", "/jobs/claim")
        return None if r.status_code == 204 else r.json()

    async def renew(self, job_id: str, attempt_id: str) -> dict[str, Any]:
        return (await self.request("POST", f"/jobs/{job_id}/lease", {"attempt_id": attempt_id}, retries=2)).json()

    async def results(self, job_id: str, attempt_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        return (await self.request("POST", f"/jobs/{job_id}/results",
                                   {"attempt_id": attempt_id, "results": results})).json()

    async def ack(self, job_id: str, attempt_id: str) -> dict[str, Any]:
        return (await self.request("POST", f"/jobs/{job_id}/ack", {"attempt_id": attempt_id})).json()

    async def failure(self, job_id: str, attempt_id: str, *, error_code: str | None, error_message: str,
                      transient: bool, category: str) -> dict[str, Any]:
        return (await self.request("POST", f"/jobs/{job_id}/failure", {
            "attempt_id": attempt_id, "error_code": error_code, "error_message": error_message[:1000],
            "transient": transient, "category": category,
        })).json()

    async def release(self, job_id: str, attempt_id: str, reason: str) -> dict[str, Any]:
        return (await self.request("POST", f"/jobs/{job_id}/release",
                                   {"attempt_id": attempt_id, "reason": reason})).json()
