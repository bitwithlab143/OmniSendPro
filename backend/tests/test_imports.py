"""Large recipient files: presigned uploads to object storage + background import (DS-21, P3-04/P3-05)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import boto3
import httpx
import pytest
from httpx import AsyncClient
from moto.server import ThreadedMotoServer
from sqlalchemy import update

from app.core.config import get_settings
from app.db.session import sessionmaker
from app.models import RecipientImport
from app.services import imports
from tests.conftest import Session, assign, create_provider, ready_campaign

BUCKET = "omnisend-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3_server() -> Iterator[str]:
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def storage_on(s3_server: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    s = get_settings()
    for key, value in {"object_storage_endpoint": s3_server, "object_storage_bucket": BUCKET,
                       "object_storage_access_key": "test", "object_storage_secret_key": "test",
                       "object_storage_max_bytes": 5 * 1024 * 1024}.items():
        monkeypatch.setattr(s, key, value)
    client = boto3.client("s3", endpoint_url=s3_server, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    client.create_bucket(Bucket=BUCKET)
    yield client
    for obj in client.list_objects_v2(Bucket=BUCKET).get("Contents", []):
        client.delete_object(Bucket=BUCKET, Key=obj["Key"])
    client.delete_bucket(Bucket=BUCKET)


def _csv(n: int, bad: int = 0) -> bytes:
    rows = ["email,first_name"] + [f"big{i}@example.org,Name{i}" for i in range(n)] + ["not-an-email,x"] * bad
    return ("\n".join(rows) + "\n").encode()


async def _upload(client: AsyncClient, session: Session, base: str, body: bytes) -> str:
    r = await client.post(f"{base}/recipients/upload-url", headers=session.headers,
                          json={"filename": "list.csv", "size": len(body)})
    assert r.status_code == 200, r.text
    post = r.json()
    async with httpx.AsyncClient() as s3:
        up = await s3.post(post["url"], data=post["fields"], files={"file": ("list.csv", body, "text/csv")})
    assert up.status_code in (200, 204), up.text
    return post["object_key"]


async def _campaign(client: AsyncClient, admin: Session, user: Session) -> dict:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    return await ready_campaign(client, user, provider["id"], ["first@example.org"])


async def test_disabled_by_default(client: AsyncClient, admin: Session, user: Session) -> None:
    campaign = await _campaign(client, admin, user)
    opts = (await client.get("/api/v1/user/options", headers=user.headers)).json()["uploads"]
    assert opts["object_storage"] is False and opts["max_object_bytes"] is None
    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/recipients/upload-url", headers=user.headers,
                          json={"size": 10})
    assert r.status_code == 400 and r.json()["error"]["code"] == "object_storage_disabled"


async def test_large_upload_flow(client: AsyncClient, admin: Session, user: Session, storage_on) -> None:  # noqa: ANN001
    campaign = await _campaign(client, admin, user)
    base = f"/api/v1/user/campaigns/{campaign['id']}"
    assert (await client.get("/api/v1/user/options", headers=user.headers)).json()["uploads"]["object_storage"]

    key = await _upload(client, user, base, _csv(12_000, bad=3))
    assert key.startswith(f"imports/{user.user_id}/{campaign['id']}/")
    r = await client.post(f"{base}/recipients/imports", headers=user.headers, json={"object_key": key, "replace": True})
    assert r.status_code == 202 and r.json()["status"] == "queued"

    # While the import is pending the campaign cannot start or take other recipient changes.
    r = await client.post(f"{base}/start", headers=user.headers, json={"consent_confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "import_in_progress"
    r = await client.post(f"{base}/recipients", headers=user.headers,
                          files={"file": ("x.csv", b"email\na@example.org\n", "text/csv")})
    assert r.status_code == 409
    r = await client.post(f"{base}/recipients/imports", headers=user.headers, json={"object_key": key})
    assert r.status_code == 409

    assert await imports.process_next() == 1
    [done] = (await client.get(f"{base}/recipients/imports", headers=user.headers)).json()
    assert done["status"] == "completed", done
    assert done["imported"] == 12_000 and done["invalid"] == 3 and done["rows"] == 12_003
    assert done["bytes_read"] > 0 and done["invalid_samples"] == ["not-an-email"] * 3
    detail = (await client.get(base, headers=user.headers)).json()
    assert detail["total_recipients"] == 12_000 and detail["status"] == "READY"
    assert "Contents" not in storage_on.list_objects_v2(Bucket=BUCKET), "object deleted after import"
    assert await imports.process_next() == 0

    # Admins can use the same flow on any campaign.
    assert (await client.get("/api/v1/admin/campaigns/upload-options", headers=admin.headers)).json()["object_storage"]
    key = await _upload(client, admin, f"/api/v1/admin/campaigns/{campaign['id']}", _csv(5))
    r = await client.post(f"/api/v1/admin/campaigns/{campaign['id']}/recipients/imports", headers=admin.headers,
                          json={"object_key": key})
    assert r.status_code == 202
    await imports.process_next()
    assert (await client.get(base, headers=user.headers)).json()["total_recipients"] == 12_000  # big0..4 dupes


async def test_import_validation_and_failures(client: AsyncClient, admin: Session, user: Session,
                                              storage_on, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    campaign = await _campaign(client, admin, user)
    base = f"/api/v1/user/campaigns/{campaign['id']}"
    r = await client.post(f"{base}/recipients/upload-url", headers=user.headers, json={"size": 50 * 1024 * 1024})
    assert r.status_code == 413
    r = await client.post(f"{base}/recipients/upload-url", headers=user.headers, json={"size": 5, "filename": "a.exe"})
    assert r.status_code == 415
    r = await client.post(f"{base}/recipients/imports", headers=user.headers,
                          json={"object_key": f"imports/{admin.user_id}/{campaign['id']}/x.csv"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_object_key"

    # Object never uploaded: the import fails cleanly and the campaign is untouched.
    missing = f"imports/{user.user_id}/{campaign['id']}/missing.csv"
    await client.post(f"{base}/recipients/imports", headers=user.headers, json={"object_key": missing, "replace": True})
    await imports.process_next()
    [failed] = (await client.get(f"{base}/recipients/imports", headers=user.headers)).json()
    assert failed["status"] == "failed" and "not found" in failed["error"]
    assert (await client.get(base, headers=user.headers)).json()["total_recipients"] == 1

    # A file larger than the limit is rejected while streaming (the POST policy should stop it earlier).
    key = await _upload(client, user, base, _csv(2000))
    monkeypatch.setattr(get_settings(), "object_storage_max_bytes", 1000)
    await client.post(f"{base}/recipients/imports", headers=user.headers, json={"object_key": key})
    await imports.process_next()
    latest = (await client.get(f"{base}/recipients/imports", headers=user.headers)).json()[0]
    assert latest["status"] == "failed" and "exceeds" in latest["error"]


async def test_stale_import_is_requeued(client: AsyncClient, admin: Session, user: Session, storage_on) -> None:  # noqa: ANN001
    campaign = await _campaign(client, admin, user)
    base = f"/api/v1/user/campaigns/{campaign['id']}"
    key = await _upload(client, user, base, _csv(10))
    r = await client.post(f"{base}/recipients/imports", headers=user.headers, json={"object_key": key})
    async with sessionmaker()() as db:  # a processor claimed it and died
        await db.execute(update(RecipientImport).where(RecipientImport.id == r.json()["id"]).values(
            status="processing", attempts=1, heartbeat_at=datetime.now(UTC) - timedelta(hours=1)))
        await db.commit()
    assert await imports.process_next() == 1
    [done] = (await client.get(f"{base}/recipients/imports", headers=user.headers)).json()
    assert done["status"] == "completed" and done["imported"] == 10
