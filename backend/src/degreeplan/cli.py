"""Operator CLI (run with: flask --app degreeplan.wsgi <group> <command>).

* ``db``    migrations, status, backups
* ``users`` the ONLY way to create accounts in production (passwords are prompted, never passed as
            arguments, and never stored or logged in clear text)
* ``dev``   sample data for local development (not registered when APP_ENV=production)
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import click
import sqlalchemy as sa
from flask import Flask
from flask.cli import AppGroup
from sqlalchemy.engine import make_url

from . import audit, clock, context
from .config import sqlite_file_path
from .db import migrate, plans_conn, users_conn
from .repositories import catalog, people
from .repositories import users as users_repo
from .security.passwords import PasswordPolicyError

_USERNAME_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")

db_cli = AppGroup("db", help="Database migrations and backups.")
users_cli = AppGroup("users", help="Account management.")
dev_cli = AppGroup("dev", help="Local development helpers (disabled in production).")


# --------------------------------------------------------------------------- db
@db_cli.command("upgrade")
@click.option("--target", type=click.Choice(["users", "plans", "all"]), default="all", show_default=True)
def db_upgrade(target: str) -> None:
    """Apply migrations up to the latest revision."""
    engines = context.services().databases
    for name in ("users", "plans") if target == "all" else (target,):
        migrate.upgrade(getattr(engines, name), name)
        click.echo(f"{name}: upgraded to head ({migrate.head_revision(name)})")


@db_cli.command("stamp")
@click.option("--target", type=click.Choice(["users", "plans"]), required=True)
@click.option("--revision", default="head", show_default=True)
def db_stamp(target: str, revision: str) -> None:
    """Mark an EXISTING database as already matching a revision (no schema changes)."""
    migrate.stamp(getattr(context.services().databases, target), target, revision)
    click.echo(f"{target}: stamped {revision}")


@db_cli.command("status")
def db_status() -> None:
    """Show current vs expected migration revision for both databases."""
    ok = True
    for name, conn in (("users", users_conn()), ("plans", plans_conn())):
        current, head = migrate.current_revision(conn), migrate.head_revision(name)
        state = "ok" if current == head else "NEEDS MIGRATION"
        ok &= current == head
        click.echo(f"{name}: current={current} head={head} [{state}]")
    sys.exit(0 if ok else 1)


@db_cli.command("backup")
@click.option("--dest", type=click.Path(file_okay=False, path_type=Path), required=True,
              help="Directory for the backup files (restrict its permissions!).")
def db_backup(dest: Path) -> None:
    """Online backup of SQLite databases (consistent while the app runs). Verifies each copy."""
    settings = context.settings()
    stamp = datetime.fromtimestamp(clock.now(), UTC).strftime("%Y%m%dT%H%M%SZ")
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in (("users", settings.users_database_url), ("plans", settings.plans_database_url)):
        source_path = sqlite_file_path(url)
        if source_path is None:
            raise click.ClickException(
                f"{name}: not a SQLite file database ({make_url(url).get_backend_name()}). "
                "Use the database's native backup tooling."
            )
        target = dest / f"{name}-{stamp}.sqlite3"
        src = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if result != "ok":
            raise click.ClickException(f"{name}: integrity check FAILED on {target}")
        click.echo(f"{name}: backed up to {target}")


# --------------------------------------------------------------------------- users
def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return click.prompt("Password", hide_input=True, confirmation_prompt=True)


def _require_user(username: str) -> dict:
    user = users_repo.get_by_username(users_conn(), username)
    if not user:
        raise click.ClickException(f"No such user: {username}")
    return user


@users_cli.command("create")
@click.argument("username")
@click.option("--role", type=click.Choice(["student", "teacher", "admin"]), required=True)
@click.option("--first-name")
@click.option("--last-name")
@click.option("--department", help="Teachers only.")
@click.option("--program-id", type=int, help="Students only.")
@click.option("--advisor", help="Students only: username of the assigned teacher.")
@click.option("--password-stdin", is_flag=True, help="Read the password from one line of stdin (for automation).")
def users_create(username: str, role: str, first_name: str | None, last_name: str | None,
                 department: str | None, program_id: int | None, advisor: str | None, password_stdin: bool) -> None:
    """Create an account (and its student/teacher profile)."""
    svc = context.services()
    if not _USERNAME_RE.fullmatch(username):
        raise click.ClickException("Username must be 1-64 chars of letters, digits and _ . @ -")
    if role in ("student", "teacher") and not (first_name and last_name):
        raise click.ClickException("--first-name and --last-name are required for students and teachers")
    conn = users_conn()

    advisor_id = None
    if advisor:
        if role != "student":
            raise click.ClickException("--advisor applies to students only")
        teacher_user = _require_user(advisor)
        teacher = people.teacher_by_user_id(conn, teacher_user["id"])
        if not teacher:
            raise click.ClickException(f"{advisor} is not a teacher")
        advisor_id = teacher["id"]
    if program_id is not None and not catalog.program_exists(plans_conn(), program_id):
        raise click.ClickException(f"Unknown program id: {program_id}")

    password = _read_password(password_stdin)
    try:
        svc.passwords.validate_new(password, username)
    except PasswordPolicyError as exc:
        raise click.ClickException("; ".join(exc.problems)) from None

    try:
        user_id = users_repo.create(conn, username=username, password_hash=svc.passwords.hash(password),
                                    role=role, now=clock.now())
    except sa.exc.IntegrityError:
        conn.rollback()
        raise click.ClickException(f"User '{username}' already exists.") from None
    if role == "teacher":
        people.create_teacher(conn, user_id=user_id, first_name=first_name or "", last_name=last_name or "",
                              department=department)
    elif role == "student":
        people.create_student(conn, user_id=user_id, first_name=first_name or "", last_name=last_name or "",
                              program_id=program_id, advisor_id=advisor_id)
    audit.record(conn, "user.create", actor_role="cli", target_type="user", target_id=user_id,
                 detail={"role": role})
    conn.commit()
    click.echo(f"Created {role} '{username.lower()}'.")


@users_cli.command("set-password")
@click.argument("username")
@click.option("--password-stdin", is_flag=True)
def users_set_password(username: str, password_stdin: bool) -> None:
    """Set a new password and revoke all of the user's sessions."""
    svc = context.services()
    user = _require_user(username)
    password = _read_password(password_stdin)
    try:
        svc.passwords.validate_new(password, username)
    except PasswordPolicyError as exc:
        raise click.ClickException("; ".join(exc.problems)) from None
    conn = users_conn()
    users_repo.set_password_hash(conn, user["id"], svc.passwords.hash(password), clock.now())
    svc.sessions.revoke_all(conn, user["id"])
    audit.record(conn, "user.set_password", actor_role="cli", target_type="user", target_id=user["id"])
    conn.commit()
    click.echo(f"Password updated for '{user['username']}'; sessions revoked.")


@users_cli.command("deactivate")
@click.argument("username")
def users_deactivate(username: str) -> None:
    """Disable an account and revoke its sessions."""
    user = _require_user(username)
    conn = users_conn()
    users_repo.set_active(conn, user["id"], False)
    context.services().sessions.revoke_all(conn, user["id"])
    audit.record(conn, "user.deactivate", actor_role="cli", target_type="user", target_id=user["id"])
    conn.commit()
    click.echo(f"Deactivated '{user['username']}'.")


@users_cli.command("activate")
@click.argument("username")
def users_activate(username: str) -> None:
    """Re-enable an account."""
    user = _require_user(username)
    conn = users_conn()
    users_repo.set_active(conn, user["id"], True)
    audit.record(conn, "user.activate", actor_role="cli", target_type="user", target_id=user["id"])
    conn.commit()
    click.echo(f"Activated '{user['username']}'.")


@users_cli.command("revoke-sessions")
@click.argument("username")
def users_revoke_sessions(username: str) -> None:
    """Force a user to log in again everywhere."""
    user = _require_user(username)
    conn = users_conn()
    count = context.services().sessions.revoke_all(conn, user["id"])
    audit.record(conn, "user.revoke_sessions", actor_role="cli", target_type="user", target_id=user["id"],
                 detail={"revoked": count})
    conn.commit()
    click.echo(f"Revoked {count} session(s) for '{user['username']}'.")


@users_cli.command("list")
def users_list() -> None:
    """List accounts (never shows password hashes)."""
    for u in users_repo.list_users(users_conn()):
        click.echo(f"{u['username']:<24} {u['role']:<8} {'active' if u['is_active'] else 'DISABLED'}")


# --------------------------------------------------------------------------- dev
@dev_cli.command("seed")
def dev_seed() -> None:
    """Load sample data with RANDOM passwords (printed once, never stored in code)."""
    from .devtools.sample_data import SAMPLE_USERNAMES, seed_sample_data

    svc = context.services()
    credentials = {u: secrets.token_urlsafe(16) for u in SAMPLE_USERNAMES}
    seed_sample_data(users_conn(), plans_conn(), settings=svc.settings, passwords=svc.passwords,
                     credentials=credentials)
    click.echo("Sample data loaded. Passwords below are shown ONCE:")
    for username, password in credentials.items():
        click.echo(f"  {username:<10} {password}")


def register_cli(app: Flask) -> None:
    app.cli.add_command(db_cli)
    app.cli.add_command(users_cli)
    if not app.extensions["degreeplan"].settings.is_production:
        app.cli.add_command(dev_cli)  # sample-data tooling does not even exist in production
