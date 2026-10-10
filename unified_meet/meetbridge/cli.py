"""Operations CLI: ``python -m meetbridge.cli <command>``.

Commands: doctor, migrate, backup, restore, create-user.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select, text

from . import __version__
from .config import Settings, get_settings


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    path = url.split("///", 1)[-1]
    return None if not path or path == ":memory:" else Path(path)


def _ok(label: str, detail: str = "") -> None:
    print(f"  [ok]   {label}{': ' + detail if detail else ''}")


def _warn(label: str, detail: str) -> None:
    print(f"  [warn] {label}: {detail}")


def _fail(label: str, detail: str) -> None:
    print(f"  [FAIL] {label}: {detail}")


# ------------------------------------------------------------------- doctor
def doctor(settings: Settings, check_browser: bool = True) -> int:
    """Checks configuration, database, migrations and the bridge browser. Exit 1 on failure."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from .migrate import alembic_config

    print(f"MeetBridge {__version__} doctor (env={settings.env})")
    failures = 0

    problems = settings.validate_for_startup()
    if problems:
        failures += 1
        for p in problems:
            _fail("config", p)
    else:
        _ok("config")
    if not settings.secret_key:
        _warn("secret key", f"MB_SECRET_KEY not set; a generated key in {settings.data_dir} is used")
    if not settings.public_base_url.startswith("https://"):
        _warn("public URL", "browsers only allow camera/microphone on https:// (or localhost)")
    if settings.enable_mock_provider:
        _warn("mock provider", "MockMeet test platform is enabled")
    if not any("turn:" in str(s.get("urls")) for s in settings.ice_servers):
        _warn("ICE", "no TURN server configured; browser guests behind strict NATs may not connect")

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        _ok("data dir writable", str(settings.data_dir))
    except OSError as exc:
        failures += 1
        _fail("data dir", str(exc))

    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(alembic_config(settings.database_url)).get_current_head()
        _ok("database reachable")
        if current == head:
            _ok("schema up to date", str(head))
        else:
            _warn("schema", f"at {current or 'empty'}, head is {head}; it is upgraded automatically on start "
                            "or with `python -m meetbridge.cli migrate`")
    except Exception as exc:
        failures += 1
        _fail("database", str(exc).splitlines()[0])

    if check_browser:
        try:
            asyncio.run(_check_browser(settings))
            _ok("bridge browser", "Chromium launches")
        except Exception as exc:
            failures += 1
            _fail("bridge browser", f"{str(exc).splitlines()[0]} (run `python -m playwright install --with-deps chromium`)")

    print("result:", "FAILED" if failures else "healthy")
    return 1 if failures else 0


async def _check_browser(settings: Settings) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        kwargs = {"headless": True}
        if settings.browser_channel:
            kwargs["channel"] = settings.browser_channel
        browser = await p.chromium.launch(**kwargs)
        await browser.close()


# ------------------------------------------------------------ backup/restore
def backup(settings: Settings, target: Path | None) -> int:
    src = _sqlite_path(settings.database_url)
    if src is None:
        print("Backups for PostgreSQL are taken with the database's own tooling, e.g.:\n"
              "  pg_dump --format=custom --file=meetbridge.dump \"$DATABASE_URL\"", file=sys.stderr)
        return 2
    if not src.exists():
        print(f"database file not found: {src}", file=sys.stderr)
        return 1
    if target is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = settings.data_dir / "backups" / f"meetbridge-{stamp}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as source, sqlite3.connect(target) as dest:
        source.backup(dest)  # consistent online copy, safe while the server runs
    os.chmod(target, 0o600)
    print(f"backup written: {target}")
    return 0


def _validate_backup(path: Path) -> str | None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return "integrity check failed"
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        return f"not a SQLite database ({exc})"
    missing = {"alembic_version", "users", "rooms", "meeting_links"} - tables
    return f"missing tables: {', '.join(sorted(missing))}" if missing else None


def restore(settings: Settings, source: Path, assume_yes: bool) -> int:
    from .migrate import upgrade_to_head

    dest = _sqlite_path(settings.database_url)
    if dest is None:
        print("Restore for PostgreSQL: pg_restore --clean --dbname \"$DATABASE_URL\" meetbridge.dump", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"backup file not found: {source}", file=sys.stderr)
        return 1
    problem = _validate_backup(source)
    if problem:
        print(f"refusing to restore {source}: {problem}", file=sys.stderr)
        return 1
    if not assume_yes:
        print(f"This replaces the current database ({dest}) with {source}.\n"
              "Stop the server first. Re-run with --yes to confirm.", file=sys.stderr)
        return 3
    if dest.exists():
        safety = settings.data_dir / "backups" / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        backup(settings, safety)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(dest) as out:
        src.backup(out)
    upgrade_to_head(settings.database_url)
    print(f"restored {source} -> {dest}")
    return 0


# --------------------------------------------------------------- create-user
def create_user(settings: Settings, email: str, name: str) -> int:
    from .db import init_engine, session_scope
    from .migrate import upgrade_to_head
    from .models import User
    from .security import hash_password

    password = os.environ.get("MB_NEW_USER_PASSWORD") or getpass.getpass("Password (min 10 chars): ")
    if len(password) < 10:
        print("password must be at least 10 characters", file=sys.stderr)
        return 1
    email = email.strip().lower()
    upgrade_to_head(settings.database_url)
    init_engine(settings.database_url)
    with session_scope() as db:
        if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
            print(f"a user with email {email} already exists", file=sys.stderr)
            return 1
        db.add(User(email=email, display_name=name.strip() or email, password_hash=hash_password(password)))
    print(f"user created: {email}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meetbridge.cli", description="MeetBridge operations")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check configuration, database and browser")
    d.add_argument("--skip-browser", action="store_true")
    sub.add_parser("migrate", help="upgrade the database schema to the latest version")
    b = sub.add_parser("backup", help="online backup of the SQLite database")
    b.add_argument("path", nargs="?", type=Path)
    r = sub.add_parser("restore", help="replace the database with a backup (server must be stopped)")
    r.add_argument("path", type=Path)
    r.add_argument("--yes", action="store_true", help="confirm overwriting the current database")
    u = sub.add_parser("create-user", help="create an organizer account (password from prompt or MB_NEW_USER_PASSWORD)")
    u.add_argument("--email", required=True)
    u.add_argument("--name", default="")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.cmd == "doctor":
        return doctor(settings, check_browser=not args.skip_browser)
    if args.cmd == "migrate":
        from .migrate import upgrade_to_head

        upgrade_to_head(settings.database_url)
        print("database schema is up to date")
        return 0
    if args.cmd == "backup":
        return backup(settings, args.path)
    if args.cmd == "restore":
        return restore(settings, args.path, args.yes)
    if args.cmd == "create-user":
        return create_user(settings, args.email, args.name)
    return 2


if __name__ == "__main__":
    sys.exit(main())
