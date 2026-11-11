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
from typing import Any

import click
import sqlalchemy as sa
from flask import Flask
from flask.cli import AppGroup
from sqlalchemy.engine import make_url

from . import audit, clock, context
from .config import sqlite_file_path
from .db import migrate, plans_conn, users_conn
from .db import tables as t
from .db import verify as verification
from .repositories import catalog, people
from .repositories import plans as plans_repo
from .repositories import users as users_repo
from .security import lockout
from .security.passwords import PasswordPolicyError
from .services import catalog_import

_USERNAME_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")

db_cli = AppGroup("db", help="Database migrations and backups.")
users_cli = AppGroup("users", help="Account management.")
dev_cli = AppGroup("dev", help="Local development helpers (disabled in production).")
catalog_cli = AppGroup("catalog", help="Validated, non-destructive catalog import.")


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
    try:
        migrate.stamp(getattr(context.services().databases, target), target, revision)
    except (ValueError, sa.exc.SQLAlchemyError) as exc:
        raise click.ClickException("Schema adoption refused; validate a known matching schema.") from exc
    click.echo(f"{target}: stamped {revision}")


@db_cli.command("validate-schema")
@click.option("--target", type=click.Choice(["users", "plans"]), required=True)
@click.option("--revision", default="head", show_default=True)
def db_validate_schema(target: str, revision: str) -> None:
    """Read-only adoption check; --revision current validates a recorded older schema."""
    try:
        verification.validate_schema(users_conn() if target == "users" else plans_conn(), target, revision)
    except Exception as exc:
        raise click.ClickException("Schema validation failed; adoption is not safe.") from exc
    click.echo(f"{target}: schema validated against {revision}; no revision changed.")


@db_cli.command("verify")
@click.option("--schema-only", is_flag=True, help="Skip soft cross-database and profile references.")
def db_verify(schema_only: bool) -> None:
    """Verify migrations, exact schema, SQLite integrity/FKs, and soft references."""
    try:
        verification.verify(users_conn(), plans_conn(), schema_only=schema_only)
    except (ValueError, sa.exc.SQLAlchemyError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Database verification failed."
        raise click.ClickException(message) from exc
    click.echo("Databases verified." + (" Schema only; soft references not checked." if schema_only else ""))


@catalog_cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Validate and execute, then roll back all catalog writes.")
@click.option("--update", is_flag=True, help="Allow updates to existing scalar catalog values; never delete.")
def catalog_import_command(file: Path, dry_run: bool, update: bool) -> None:
    """Import a bounded version-1 JSON catalog with code-based references."""
    conn = plans_conn()
    try:
        data = catalog_import.load(file)
        counts = catalog_import.apply(conn, data, update=update)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except (ValueError, OSError, sa.exc.SQLAlchemyError, RecursionError) as exc:
        conn.rollback()
        # Pydantic errors may contain raw input. Never echo untrusted file contents.
        raise click.ClickException(
            "Catalog import rejected; no catalog changes saved. "
            "Check version, fields, bounds, duplicates, references, cycles and --update."
        ) from exc
    if not dry_run:
        audit.record(users_conn(), "catalog.import", actor_role="cli", detail={**counts, "update": update})
        users_conn().commit()
    click.echo(("Dry run rolled back: " if dry_run else "Catalog imported: ") + str(counts))


@catalog_cli.command("list")
def catalog_list() -> None:
    """List program IDs/codes/names for profile assignment; never modifies the catalog."""
    rows = plans_conn().execute(sa.select(t.programs).order_by(t.programs.c.code)).mappings()
    for row in rows:
        click.echo(f"{row['id']:<8} {row['code']:<32} {row['name']} (credits={row['total_credits']})")


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
    """Verify each SQLite backup; quiesce writers for a coherent cross-database pair."""
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
    for label, value, limit in (("first-name", first_name, 100), ("last-name", last_name, 100),
                                ("department", department, 120)):
        if value is not None and (not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value)):
            raise click.ClickException(f"--{label} must be nonempty bounded text without control characters")
    if department is not None and role != "teacher":
        raise click.ClickException("--department applies to teachers only")
    if program_id is not None and role != "student":
        raise click.ClickException("--program-id applies to students only")
    conn = users_conn()

    advisor_id = None
    if advisor:
        if role != "student":
            raise click.ClickException("--advisor applies to students only")
        teacher_user = _require_user(advisor)
        teacher = people.teacher_by_user_id(conn, teacher_user["id"])
        if not teacher or teacher_user["role"] != "teacher" or not teacher_user["is_active"]:
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
@click.option("--details", is_flag=True, help="Include user/profile IDs and student program/advisor IDs.")
def users_list(details: bool) -> None:
    """List accounts, optionally showing assignment IDs; never shows credential/session data."""
    conn = users_conn()
    for u in users_repo.list_users(conn):
        line = f"{u['username']:<24} {u['role']:<8} {'active' if u['is_active'] else 'DISABLED'}"
        if details:
            line += f" user_id={u['id']}"
            if u["role"] == "student":
                profile = people.student_by_user_id(conn, u["id"])
                if profile:
                    line += (f" student_id={profile['id']} program_id={profile['program_id']}"
                             f" advisor_id={profile['advisor_id']}")
                else:
                    line += " student_profile=MISSING"
            elif u["role"] == "teacher":
                profile = people.teacher_by_user_id(conn, u["id"])
                line += f" teacher_id={profile['id']}" if profile else " teacher_profile=MISSING"
        click.echo(line)


@users_cli.command("revoke-all-sessions")
@click.option("--yes", is_flag=True, help="Confirm revocation of every account's sessions after restore.")
def users_revoke_all_sessions(yes: bool) -> None:
    """Force every account to log in again (required after a restore)."""
    if not yes:
        raise click.ClickException("Pass --yes to revoke all sessions.")
    conn = users_conn()
    result = conn.execute(sa.update(t.sessions).where(t.sessions.c.revoked_at.is_(None))
                          .values(revoked_at=clock.now()))
    audit.record(conn, "user.revoke_all_sessions", actor_role="cli", detail={"revoked": result.rowcount})
    conn.commit()
    click.echo(f"Revoked {result.rowcount} session(s).")


@users_cli.command("update-profile")
@click.argument("username")
@click.option("--first-name")
@click.option("--last-name")
@click.option("--department")
@click.option("--program-id", type=click.IntRange(min=1))
@click.option("--advisor")
@click.option("--clear-department", is_flag=True)
@click.option("--clear-program", is_flag=True)
@click.option("--clear-advisor", is_flag=True)
def users_update_profile(username: str, first_name: str | None, last_name: str | None,
                         department: str | None, program_id: int | None, advisor: str | None,
                         clear_department: bool, clear_program: bool, clear_advisor: bool) -> None:
    """Update an existing student/teacher profile; omitted fields are retained."""
    user = _require_user(username)
    conn = users_conn()
    if user["role"] not in ("student", "teacher"):
        raise click.ClickException("Administrators do not have editable profiles.")
    is_student = user["role"] == "student"
    table = t.students if is_student else t.teachers
    profile = (people.student_by_user_id if is_student else people.teacher_by_user_id)(conn, user["id"])
    if not profile:
        raise click.ClickException("Missing profile; database repair is required.")
    values: dict[str, Any] = {}
    for field, value, limit in (("first_name", first_name, 100), ("last_name", last_name, 100),
                                ("department", department, 120)):
        if value is not None:
            if not value.strip() or len(value.strip()) > limit or any(ord(c) < 32 for c in value):
                raise click.ClickException(f"{field} must be nonempty, bounded text without control characters.")
            values[field] = value.strip()
    if (department is not None or clear_department) and is_student:
        raise click.ClickException("--department applies to teachers only.")
    if (program_id is not None or advisor is not None or clear_program or clear_advisor) and not is_student:
        raise click.ClickException("--program-id and --advisor apply to students only.")
    for supplied, clear, label in ((department, clear_department, "department"),
                                (program_id, clear_program, "program"), (advisor, clear_advisor, "advisor")):
        if supplied is not None and clear:
            raise click.ClickException(f"Cannot set and clear {label} together.")
    if clear_department:
        values["department"] = None
    if program_id is not None:
        if not catalog.program_exists(plans_conn(), program_id):
            raise click.ClickException(f"Unknown program id: {program_id}")
        values["program_id"] = program_id
    if clear_program:
        values["program_id"] = None
    if advisor is not None:
        teacher_user = _require_user(advisor)
        teacher = people.teacher_by_user_id(conn, teacher_user["id"])
        if teacher_user["role"] != "teacher" or not teacher or not teacher_user["is_active"]:
            raise click.ClickException("Advisor must be an active teacher.")
        values["advisor_id"] = teacher["id"]
    if clear_advisor:
        values["advisor_id"] = None
    if not values:
        raise click.ClickException("Supply at least one profile field or clear flag.")
    conn.execute(sa.update(table).where(table.c.id == profile["id"]).values(**values))
    audit.record(conn, "user.update_profile", actor_role="cli", target_type="user", target_id=user["id"],
                 detail={"fields": sorted(values)})
    conn.commit()
    click.echo(f"Profile updated for '{user['username']}'.")


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


@dev_cli.command("clear-saved-data")
@click.option("--yes", is_flag=True, help="Confirm deletion of ALL students' saved plans and progress.")
def clear_demo_saved_data(yes: bool) -> None:
    """Delete saved demo plans/semesters/courses/progress, retaining accounts and the catalog."""
    if context.settings().is_production:
        raise click.ClickException("Demo saved data cannot be cleared in production.")
    if not yes:
        raise click.ClickException(
            "This deletes ALL students' saved plans and progress. Pass --yes to confirm."
        )
    counts = plans_repo.clear_saved_data(plans_conn())
    plans_conn().commit()
    audit.record(users_conn(), "dev.saved_data.clear", actor_role="cli", detail=counts)
    users_conn().commit()
    click.echo("Cleared saved data: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    click.echo("Accounts, passwords, profiles, advisor assignments and catalog kept.")


@dev_cli.command("refresh-demo-passwords")
def refresh_demo_passwords() -> None:
    """Generate and display new Alice/teacher1 passwords without resetting any data."""
    svc = context.services()
    if svc.settings.is_production:
        raise click.ClickException("Demo passwords cannot be refreshed in production.")
    conn = users_conn()
    accounts = []
    # Validate BOTH accounts before changing either. Never activate or change someone's role.
    for username, role in (("alice", "student"), ("teacher1", "teacher")):
        user = users_repo.get_by_username(conn, username)
        if not user or user["role"] != role or not user["is_active"]:
            raise click.ClickException(
                f"Expected active demo {role} '{username}'. Use -KeepDemoPasswords for custom databases."
            )
        accounts.append(user)

    credentials: dict[str, str] = {}
    now = clock.now()
    for user in accounts:
        password = secrets.token_urlsafe(max(16, svc.settings.password_min_length))
        while True:
            try:
                svc.passwords.validate_new(password, user["username"])
                break
            except PasswordPolicyError:
                # Rare random matches to the username must still meet the normal policy.
                password = secrets.token_urlsafe(max(16, svc.settings.password_min_length))
        users_repo.set_password_hash(conn, user["id"], svc.passwords.hash(password), now)
        svc.sessions.revoke_all(conn, user["id"])
        lockout.clear(conn, lockout.lockout_key(user["username"]))
        audit.record(conn, "user.set_password", actor_role="cli", target_type="user", target_id=user["id"])
        credentials[user["username"]] = password
    conn.commit()
    # Print only after the whole transaction succeeds; never put credentials in audit logs.
    click.echo("Fresh demo passwords (shown once this launch; old sessions revoked):")
    for username, password in credentials.items():
        click.echo(f"  {username:<10} {password}")


def register_cli(app: Flask) -> None:
    app.cli.add_command(db_cli)
    app.cli.add_command(users_cli)
    app.cli.add_command(catalog_cli)
    if not app.extensions["degreeplan"].settings.is_production:
        app.cli.add_command(dev_cli)  # sample-data tooling does not even exist in production
