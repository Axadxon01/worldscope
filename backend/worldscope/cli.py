"""Command-line tasks: `python -m worldscope.cli <command>`.

sync                       load reference data and sources from backend/config
invite EMAIL [--role R]    create an invitation and print its link once
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from worldscope.catalog import CatalogError
from worldscope.core.config import get_settings
from worldscope.db.session import create_api_engine, get_sync_engine


def cmd_sync() -> int:
    from worldscope.services.catalog_sync import sync_all

    try:
        with get_sync_engine().begin() as conn:
            report = sync_all(conn)
    except CatalogError as exc:
        print(f"Sync stopped, nothing was changed: {exc}", file=sys.stderr)
        return 1
    for line in report.lines():
        print(line)
    return 0


def cmd_prepare() -> int:
    """Migrations and sync in one interpreter. On a small hosted CPU each Python start
    costs seconds, and a sleeping free-plan API runs this on every wake-up."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    print("Applying database migrations")
    command.upgrade(config, "head")
    print("Loading reference data and sources")
    return cmd_sync()


async def _invite(email: str, role: str, base_url: str) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from worldscope.auth import service

    settings = get_settings()
    engine = create_api_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            try:
                invitation, token = await service.create_cli_invitation(db, settings, email, role)
            except service.AuthError as exc:
                print(exc.message, file=sys.stderr)
                return 1
    finally:
        await engine.dispose()
    print(
        f"Invitation for {invitation.email} ({invitation.role}), "
        f"valid until {invitation.expires_at:%Y-%m-%d %H:%M} UTC."
    )
    print("Send this link to them; it is shown only once:")
    print(f"{base_url.rstrip('/')}/invite#{token}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m worldscope.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="load reference data and sources from backend/config")
    sub.add_parser("prepare", help="apply migrations, then sync")
    invite = sub.add_parser("invite", help="create an invitation and print its link")
    invite.add_argument("email")
    invite.add_argument("--role", choices=["admin", "analyst"], default="analyst")
    invite.add_argument("--base-url", default="http://localhost:3000", help="the web app's address")
    args = parser.parse_args(argv)
    if args.command == "sync":
        return cmd_sync()
    if args.command == "prepare":
        return cmd_prepare()
    return asyncio.run(_invite(args.email, args.role, args.base_url))


if __name__ == "__main__":
    sys.exit(main())
