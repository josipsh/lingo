"""Create call transcript and learning-memory tables."""

import sqlalchemy as sa
from alembic import op

revision = "20260924_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learners",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "call_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_call_id", sa.String(128), nullable=False),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learners.id"), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text),
        sa.Column("praise", sa.Text),
        sa.Column("reusable_item", sa.Text),
        sa.Column("language_evidence", sa.Text),
        sa.Column("proficiency_evidence", sa.Text),
    )
    op.create_index(
        "ix_call_sessions_external_call_id", "call_sessions", ["external_call_id"], unique=True
    )
    op.create_index("ix_call_sessions_learner_id", "call_sessions", ["learner_id"])
    op.create_table(
        "turns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("call_sessions.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_turn_session_sequence"),
    )
    op.create_index("ix_turns_session_id", "turns", ["session_id"])
    op.create_table(
        "corrections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("call_sessions.id"), nullable=False),
        sa.Column("source_turn_id", sa.Integer, sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("original", sa.Text, nullable=False),
        sa.Column("corrected", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("pattern_key", sa.String(160)),
        sa.Column("language_transfer", sa.Text),
    )
    op.create_index("ix_corrections_session_id", "corrections", ["session_id"])
    op.create_index("ix_corrections_source_turn_id", "corrections", ["source_turn_id"])
    op.create_index("ix_corrections_pattern_key", "corrections", ["pattern_key"])
    op.create_table(
        "learning_patterns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learners.id"), nullable=False),
        sa.Column("pattern_key", sa.String(160), nullable=False),
        sa.Column("example_original", sa.Text, nullable=False),
        sa.Column("example_corrected", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("high_confidence_turns", sa.Integer, nullable=False),
        sa.Column("recurring", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("learner_id", "pattern_key", name="uq_learner_pattern"),
    )
    op.create_index("ix_learning_patterns_learner_id", "learning_patterns", ["learner_id"])


def downgrade() -> None:
    op.drop_table("learning_patterns")
    op.drop_table("corrections")
    op.drop_table("turns")
    op.drop_table("call_sessions")
    op.drop_table("learners")
