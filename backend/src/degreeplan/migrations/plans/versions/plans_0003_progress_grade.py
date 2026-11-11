"""Nullable self-reported letter grades; existing progress remains intact."""
import sqlalchemy as sa
from alembic import op

revision = "plans_0003"
down_revision = "plans_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Inline CHECK permits SQLite ADD COLUMN without rebuilding referenced tables.
    op.add_column("student_course_progress", sa.Column(
        "grade", sa.String(1),
        sa.CheckConstraint("grade IS NULL OR grade IN ('A', 'B', 'C', 'D', 'F')",
                           name="ck_student_course_progress_grade"),
        nullable=True,
    ))


def downgrade() -> None:
    with op.batch_alter_table("student_course_progress") as batch:
        batch.drop_constraint("ck_student_course_progress_grade", type_="check")
        batch.drop_column("grade")
