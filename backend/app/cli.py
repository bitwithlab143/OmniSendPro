"""Operational CLI.

    python -m app.cli create-admin --email admin@example.com --username admin
    python -m app.cli seed-reference
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from app.db.session import dispose_engine, sessionmaker
from app.services import bootstrap


async def _create_admin(email: str, username: str, password: str) -> None:
    async with sessionmaker()() as db:
        await bootstrap.ensure_reference_data(db)
        user = await bootstrap.create_super_admin(db, email, password, username)
        print(f"Created SUPER_ADMIN {user.username} <{user.email}> ({user.id})")
    await dispose_engine()


async def _seed() -> None:
    async with sessionmaker()() as db:
        await bootstrap.ensure_reference_data(db)
    await dispose_engine()
    print("Reference data ensured")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omnisend")
    sub = parser.add_subparsers(dest="cmd", required=True)
    admin = sub.add_parser("create-admin", help="Create a SUPER_ADMIN account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--username", default="admin")
    sub.add_parser("seed-reference", help="Ensure roles and permissions exist")
    args = parser.parse_args(argv)

    if args.cmd == "create-admin":
        password = os.environ.get("OMNISEND_ADMIN_PASSWORD") or getpass.getpass("Password: ")
        try:
            asyncio.run(_create_admin(args.email, args.username, password))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif args.cmd == "seed-reference":
        asyncio.run(_seed())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
