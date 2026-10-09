"""Alembic environment for the PLANS database. Driven by degreeplan.db.migrate (shared connection)."""
from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations via `flask --app degreeplan.wsgi db upgrade` (online mode only)")

context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
