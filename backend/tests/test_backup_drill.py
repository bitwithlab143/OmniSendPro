"""Backup + restore drill against the test database (design DS-23, P4-06)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import Session
from tests.test_events_and_health import _sent_campaign

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "infrastructure/backup"
pytestmark = pytest.mark.skipif("_pg_dump_too_old()", reason="PostgreSQL client tools missing or older than the server")


def _pg_dump_too_old() -> bool:
    """pg_dump refuses servers newer than itself (e.g. an older client on a CI runner)."""
    if shutil.which("pg_dump") is None:
        return True
    client = int(subprocess.run(["pg_dump", "--version"], capture_output=True, text=True,  # noqa: S603, S607
                                check=True).stdout.split()[-1].split(".")[0])
    server = subprocess.run(["psql", _url(), "-tAc", "SHOW server_version_num"], capture_output=True,  # noqa: S603, S607
                            text=True)
    return server.returncode != 0 or client < int(server.stdout.strip()) // 10000


def _url() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", *args], capture_output=True, text=True, env={**os.environ, **env},  # noqa: S603, S607
                          timeout=120)


async def test_restore_drill_with_data(client: AsyncClient, admin: Session, user: Session) -> None:
    await _sent_campaign(client, admin, user, n=25)  # campaigns, recipients, jobs, events, audit rows …
    result = _run(str(SCRIPTS / "restore_drill.sh"), DATABASE_URL=_url())
    assert result.returncode == 0, result.stderr
    assert "restore drill OK" in result.stdout


def test_tampered_backup_is_refused(tmp_path: Path) -> None:
    result = _run(str(SCRIPTS / "backup.sh"), DATABASE_URL=_url(), BACKUP_DIR=str(tmp_path))
    assert result.returncode == 0, result.stderr
    dump = Path(result.stdout.strip())
    assert dump.exists() and Path(f"{dump}.sha256").exists()
    with dump.open("ab") as f:
        f.write(b"corruption")
    restored = _run(str(SCRIPTS / "restore.sh"), str(dump), _url().rsplit("/", 1)[0] + "/omnisend_never_created")
    assert restored.returncode == 2 and "checksum mismatch" in restored.stderr
