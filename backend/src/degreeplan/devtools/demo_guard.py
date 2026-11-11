"""Own and constrain repository-local demo data; never inspect password values."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

MARKER_NAME = ".edunav-demo.json"
MARKER = {"format": 1, "purpose": "edunav-local-demo"}
DATABASES = ("users.sqlite3", "plans.sqlite3")


class DemoSafetyError(ValueError):
    """A path or existing data directory cannot safely be treated as demo-owned."""


def assert_plain_path(path: Path) -> None:
    """Reject symlinks, Windows junctions/reparse points, and hard-linked leaf files."""
    absolute = path.absolute()
    for item in (*reversed(absolute.parents), absolute):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DemoSafetyError("Demo/production data paths must not contain links or reparse points.")
        if item == absolute and stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise DemoSafetyError("Database and ownership files must not be hard-linked.")


def demo_directory(backend: Path) -> Path:
    root = backend.absolute() / "instance"
    assert_plain_path(root)
    if root.exists() and not root.is_dir():
        raise DemoSafetyError("The demo instance path must be a directory.")
    database_files = [name + suffix for name in DATABASES for suffix in ("", "-wal", "-shm")]
    for name in (*database_files, MARKER_NAME):
        assert_plain_path(root / name)
    return root


def assert_owned_demo(backend: Path) -> Path:
    root = demo_directory(backend)
    marker = root / MARKER_NAME
    try:
        if marker.stat().st_size > 1024:
            raise DemoSafetyError("Invalid demo ownership marker.")
        owned = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoSafetyError("Demo ownership is not established. Run demo.ps1 first.") from exc
    if owned != MARKER:
        raise DemoSafetyError("Invalid demo ownership marker.")
    return root


def _validate_existing(root: Path) -> None:
    required = {
        "users.sqlite3": {"users", "students", "teachers", "sessions", "audit_log", "alembic_version"},
        "plans.sqlite3": {"programs", "courses", "plans", "plan_courses", "alembic_version"},
    }
    existing = [name for name in DATABASES if (root / name).exists()]
    if set(existing) != set(DATABASES):
        raise DemoSafetyError("Cannot adopt an incomplete database pair; preserve and repair it manually.")
    for name in existing:
        path = root / name
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required[name] <= tables or conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise DemoSafetyError("Existing demo databases failed schema/integrity validation.")
        except sqlite3.Error as exc:
            raise DemoSafetyError("Existing demo databases cannot be adopted safely.") from exc


def prepare_demo(backend: Path, *, adopt_existing: bool = False) -> Path:
    root = demo_directory(backend)
    if (root / MARKER_NAME).exists():
        return assert_owned_demo(backend)
    if any((root / name).exists() for name in DATABASES):
        if not adopt_existing:
            raise DemoSafetyError(
                "Existing databases have no demo ownership marker. Review them, then use -AdoptExistingDemo once."
            )
        _validate_existing(root)
    root.mkdir(parents=True, exist_ok=True)
    # Do not overwrite a competing marker or follow a link created at the ownership path.
    descriptor = os.open(root / MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
        json.dump(MARKER, marker)
        marker.write("\n")
    return assert_owned_demo(backend)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify ownership without creating anything.")
    parser.add_argument("--adopt-existing", action="store_true", help="Explicitly adopt a validated legacy demo pair.")
    args = parser.parse_args()
    from ..config import BACKEND_DIR

    try:
        if args.check:
            assert_owned_demo(BACKEND_DIR)
        else:
            prepare_demo(BACKEND_DIR, adopt_existing=args.adopt_existing)
    except DemoSafetyError as exc:
        parser.exit(1, f"Demo safety check failed: {exc}\n")
    print("Repository-local demo ownership verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
