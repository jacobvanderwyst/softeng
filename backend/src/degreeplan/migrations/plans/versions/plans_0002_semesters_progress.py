"""Named (including empty) semesters and independent student-reported progress.

Revision ID: plans_0002
Revises: plans_0001
"""
import sqlalchemy as sa
from alembic import op

revision = "plans_0002"
down_revision = "plans_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing numbered-term plans remain intact; the API synthesizes their term names.
    op.create_table(
        "plan_semesters",
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("term_index", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.CheckConstraint("term_index BETWEEN 1 AND 16", name="ck_plan_semesters_term"),
    )
    op.create_table(
        "student_course_progress",
        # Students live in a separate database: deliberately no cross-database foreign key.
        sa.Column("student_id", sa.Integer, primary_key=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "status IN ('Not Started', 'In Progress', 'Completed')", name="ck_student_course_progress_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("student_course_progress")
    op.drop_table("plan_semesters")
