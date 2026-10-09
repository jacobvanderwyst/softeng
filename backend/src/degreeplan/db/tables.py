"""Table definitions for both databases (SQLAlchemy Core).

This module is the single adaptation point for the real schemas: if a column or table is named
differently in the production databases, change it here (the repositories only use these objects).
Timestamps are integer epoch seconds (portable across every database engine).

There are NO cross-database foreign keys: ``students.program_id`` and ``plans.student_id`` are plain
integers that the application layer validates.
"""
import sqlalchemy as sa

users_md = sa.MetaData()
plans_md = sa.MetaData()

# --------------------------------------------------------------------------- users database
users = sa.Table(
    "users",
    users_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("username", sa.String(64), nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("role", sa.String(16), nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("password_changed_at", sa.BigInteger, nullable=True),
    sa.CheckConstraint("role IN ('student', 'teacher', 'admin')", name="ck_users_role"),
)

teachers = sa.Table(
    "teachers",
    users_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
    sa.Column("first_name", sa.String(100), nullable=False),
    sa.Column("last_name", sa.String(100), nullable=False),
    sa.Column("department", sa.String(120), nullable=True),
)

students = sa.Table(
    "students",
    users_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
    sa.Column("first_name", sa.String(100), nullable=False),
    sa.Column("last_name", sa.String(100), nullable=False),
    sa.Column("program_id", sa.Integer, nullable=True),  # soft reference into the plans database
    sa.Column("advisor_id", sa.Integer, sa.ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True),
)

sessions = sa.Table(
    "sessions",
    users_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("csrf_token", sa.String(64), nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("last_seen_at", sa.BigInteger, nullable=False),
    sa.Column("expires_at", sa.BigInteger, nullable=False),
    sa.Column("revoked_at", sa.BigInteger, nullable=True),
    sa.Column("ip", sa.String(45), nullable=True),
    sa.Column("user_agent", sa.String(200), nullable=True),
)
sa.Index("ix_sessions_user_id", sessions.c.user_id)

login_attempts = sa.Table(
    "login_attempts",
    users_md,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_failed_at", sa.BigInteger, nullable=False),
    sa.Column("locked_until", sa.BigInteger, nullable=False, server_default="0"),
)

audit_log = sa.Table(
    "audit_log",
    users_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("occurred_at", sa.BigInteger, nullable=False),
    sa.Column("request_id", sa.String(64), nullable=True),
    sa.Column("actor_user_id", sa.Integer, nullable=True),  # no FK: audit rows must outlive users
    sa.Column("actor_role", sa.String(16), nullable=True),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("target_type", sa.String(32), nullable=True),
    sa.Column("target_id", sa.String(64), nullable=True),
    sa.Column("outcome", sa.String(16), nullable=False),
    sa.Column("ip", sa.String(45), nullable=True),
    sa.Column("detail", sa.Text, nullable=True),
)
sa.Index("ix_audit_log_occurred_at", audit_log.c.occurred_at)

# --------------------------------------------------------------------------- plans database
programs = sa.Table(
    "programs",
    plans_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("code", sa.String(32), nullable=False, unique=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("total_credits", sa.Integer, nullable=False),
)

courses = sa.Table(
    "courses",
    plans_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("code", sa.String(32), nullable=False, unique=True),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("credits", sa.Integer, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.CheckConstraint("credits >= 0", name="ck_courses_credits"),
)

course_prerequisites = sa.Table(
    "course_prerequisites",
    plans_md,
    sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("prerequisite_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    sa.CheckConstraint("course_id <> prerequisite_id", name="ck_prereq_not_self"),
)

program_requirements = sa.Table(
    "program_requirements",
    plans_md,
    sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
)

plans = sa.Table(
    "plans",
    plans_md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("student_id", sa.Integer, nullable=False),  # soft reference into the users database
    sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id"), nullable=False),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
)
sa.Index("ix_plans_student_id", plans.c.student_id)

plan_courses = sa.Table(
    "plan_courses",
    plans_md,
    sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), primary_key=True),
    sa.Column("term_index", sa.Integer, nullable=False),
    sa.CheckConstraint("term_index >= 1", name="ck_plan_courses_term"),
)
