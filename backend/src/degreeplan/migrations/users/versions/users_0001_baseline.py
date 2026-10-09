"""Baseline schema for the users database.

For an EXISTING users database that already matches this schema, do not run this migration:
mark it as applied with `flask --app degreeplan.wsgi db stamp --target users`.

Revision ID: users_0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "users_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("password_changed_at", sa.BigInteger, nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('student', 'teacher', 'admin')", name="ck_users_role"),
    )
    op.create_table(
        "teachers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("department", sa.String(120), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_teachers_user_id"),
    )
    op.create_table(
        "students",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("program_id", sa.Integer, nullable=True),
        sa.Column("advisor_id", sa.Integer, sa.ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_students_user_id"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("expires_at", sa.BigInteger, nullable=False),
        sa.Column("revoked_at", sa.BigInteger, nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(200), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "login_attempts",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_failed_at", sa.BigInteger, nullable=False),
        sa.Column("locked_until", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.BigInteger, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("actor_user_id", sa.Integer, nullable=True),
        sa.Column("actor_role", sa.String(16), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
    )
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_occurred_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("login_attempts")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("students")
    op.drop_table("teachers")
    op.drop_table("users")
