"""Wake-up of the devices when SARCADE is closed (28 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wake_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", "device_id", "platform", name="uq_wake_token"),
    )
    op.create_index("ix_wake_tokens_event_id", "wake_tokens", ["event_id"])
    op.create_table(
        "wake_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("private_pem", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("wake_keys")
    op.drop_index("ix_wake_tokens_event_id", "wake_tokens")
    op.drop_table("wake_tokens")
