import sqlalchemy as sa
from alembic import command
from sqlalchemy import inspect

from degreeplan.db import Databases, migrate
from degreeplan.db.tables import plans_md, users_md
from tests.conftest import sqlite_url


def make_dbs(tmp_path):
    from degreeplan.config import Settings

    settings = Settings.for_testing(sqlite_url(tmp_path / "u.sqlite3"), sqlite_url(tmp_path / "p.sqlite3"))
    return Databases(settings)


def schema(engine):
    insp = inspect(engine)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names() if t != "alembic_version"}


def expected(metadata):
    return {t.name: {c.name for c in t.columns} for t in metadata.tables.values()}


def test_migrations_create_exactly_the_schema_the_code_expects(tmp_path):
    """Guards against drift between db/tables.py (what the code queries) and the migrations."""
    dbs = make_dbs(tmp_path)
    try:
        migrate.upgrade(dbs.users, "users")
        migrate.upgrade(dbs.plans, "plans")
        assert schema(dbs.users) == expected(users_md)
        assert schema(dbs.plans) == expected(plans_md)
    finally:
        dbs.dispose()


def test_databases_are_at_head_after_upgrade_and_upgrade_is_idempotent(tmp_path):
    dbs = make_dbs(tmp_path)
    try:
        for name, engine in (("users", dbs.users), ("plans", dbs.plans)):
            migrate.upgrade(engine, name)
            migrate.upgrade(engine, name)  # second run is a no-op
            with engine.connect() as conn:
                assert migrate.is_at_head(conn, name)
    finally:
        dbs.dispose()


def test_stamp_adopts_an_existing_database_without_touching_its_data(tmp_path):
    dbs = make_dbs(tmp_path)
    try:
        users_md.create_all(dbs.users)  # an "existing" database that already matches the baseline
        with dbs.users.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO users (username, password_hash, role, is_active, created_at) "
                "VALUES ('existing', 'x', 'admin', 1, 0)"
            ))
        with dbs.users.connect() as conn:
            assert not migrate.is_at_head(conn, "users")
        migrate.stamp(dbs.users, "users")
        with dbs.users.connect() as conn:
            assert migrate.is_at_head(conn, "users")
            assert conn.execute(sa.text("SELECT username FROM users")).scalar_one() == "existing"
    finally:
        dbs.dispose()


def test_baseline_migration_can_be_rolled_back(tmp_path):
    dbs = make_dbs(tmp_path)
    try:
        migrate.upgrade(dbs.plans, "plans")
        with dbs.plans.begin() as conn:
            command.downgrade(migrate._config("plans", conn), "base")
        assert schema(dbs.plans) == {}
    finally:
        dbs.dispose()


def test_the_two_databases_have_no_cross_database_foreign_keys():
    for md, other in ((users_md, plans_md), (plans_md, users_md)):
        other_tables = set(other.tables)
        for table in md.tables.values():
            for fk in table.foreign_keys:
                assert fk.column.table.name not in other_tables - set(md.tables), (table.name, fk)


def test_sqlite_pragmas_are_applied(tmp_path):
    dbs = make_dbs(tmp_path)
    try:
        with dbs.users.connect() as conn:
            assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar_one() == 1
            assert conn.execute(sa.text("PRAGMA journal_mode")).scalar_one() == "wal"
            assert conn.execute(sa.text("PRAGMA busy_timeout")).scalar_one() == 10000
    finally:
        dbs.dispose()
