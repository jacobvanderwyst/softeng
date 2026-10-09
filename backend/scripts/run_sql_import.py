"""Load data from another SQLite database into the plans database with plain SQL.

    python scripts/run_sql_import.py --source source.sqlite3 docs/examples/import-catalog.sql [--dry-run]

* The SOURCE database is attached read-only as ``src``; the TARGET (the backend's plans database, default
  ``backend/instance/plans.sqlite3``) is ``main``. Write ordinary ``INSERT INTO ... SELECT ... FROM src.<table>``
  statements in the SQL file.
* Everything runs in ONE transaction. Foreign keys are enforced and re-checked at the end; any error or violation
  rolls everything back, so a failed import leaves the target untouched.
* ``--dry-run`` runs the whole import, prints the resulting row counts, then rolls back.
* Run ``flask --app degreeplan.wsgi db upgrade`` first (and do NOT run ``dev seed``: the target must be empty).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CATALOG_TABLES = ("programs", "courses", "course_prerequisites", "program_requirements")


def statements(sql: str):
    """Split a SQL script into complete statements (comments and semicolons inside strings are handled)."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer
            buffer = ""
    if buffer.strip() and not all(part.strip().startswith("--") or not part.strip() for part in buffer.splitlines()):
        raise ValueError("The SQL file ends with an incomplete statement (missing semicolon?)")


def run(target: Path, source: Path, script: Path, dry_run: bool) -> int:
    for label, path in (("target", target), ("source", source), ("SQL file", script)):
        if not path.is_file():
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2
    if target.resolve() == source.resolve():
        print("error: source and target are the same file", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=rw", uri=True, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("ATTACH DATABASE ? AS src", (f"{source.resolve().as_uri()}?mode=ro",))
        existing = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
        if not set(CATALOG_TABLES) <= existing:
            print("error: the target is not migrated. Run: flask --app degreeplan.wsgi db upgrade", file=sys.stderr)
            return 2

        conn.execute("BEGIN")
        try:
            count = 0
            for statement in statements(script.read_text(encoding="utf-8")):
                conn.execute(statement)
                count += 1
            violations = conn.execute("PRAGMA main.foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"{len(violations)} foreign key violation(s), first in table '{violations[0][0]}' "
                    f"(row {violations[0][1]}). Check the id mapping in the SQL file."
                )
            totals = {t: conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0] for t in CATALOG_TABLES}  # noqa: S608
        except Exception as exc:
            conn.execute("ROLLBACK")
            print(f"IMPORT FAILED, nothing was changed: {exc}", file=sys.stderr)
            return 1

        conn.execute("ROLLBACK" if dry_run else "COMMIT")
        summary = ", ".join(f"{t}={n}" for t, n in totals.items())
        print(f"{count} statement(s) executed. Row counts after import: {summary}")
        print("DRY RUN: rolled back, nothing was saved." if dry_run else "Committed.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("script", type=Path, help="SQL file with INSERT ... SELECT statements")
    parser.add_argument("--source", type=Path, required=True, help="SQLite database to read from (attached as src)")
    parser.add_argument("--target", type=Path, default=BACKEND / "instance" / "plans.sqlite3",
                        help="the backend's plans database (default: backend/instance/plans.sqlite3)")
    parser.add_argument("--dry-run", action="store_true", help="run everything, print counts, then roll back")
    args = parser.parse_args(argv)
    return run(args.target, args.source, args.script, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
