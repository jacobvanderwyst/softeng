"""Baseline schema for the degree-plan database.

For an EXISTING plans database that already matches this schema, do not run this migration:
mark it as applied with `flask --app degreeplan.wsgi db stamp --target plans`.

Revision ID: plans_0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "plans_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "programs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("total_credits", sa.Integer, nullable=False),
        sa.UniqueConstraint("code", name="uq_programs_code"),
    )
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("credits", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.UniqueConstraint("code", name="uq_courses_code"),
        sa.CheckConstraint("credits >= 0", name="ck_courses_credits"),
    )
    op.create_table(
        "course_prerequisites",
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "prerequisite_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.CheckConstraint("course_id <> prerequisite_id", name="ck_prereq_not_self"),
    )
    op.create_table(
        "program_requirements",
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Integer, nullable=False),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_plans_student_id", "plans", ["student_id"])
    op.create_table(
        "plan_courses",
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id"), primary_key=True),
        sa.Column("term_index", sa.Integer, nullable=False),
        sa.CheckConstraint("term_index >= 1", name="ck_plan_courses_term"),
    )


def downgrade() -> None:
    op.drop_table("plan_courses")
    op.drop_index("ix_plans_student_id", table_name="plans")
    op.drop_table("plans")
    op.drop_table("program_requirements")
    op.drop_table("course_prerequisites")
    op.drop_table("courses")
    op.drop_table("programs")
