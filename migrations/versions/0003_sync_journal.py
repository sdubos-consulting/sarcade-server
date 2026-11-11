"""Synchronization journal and cursor."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "sync_operations",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("operation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("event_id", sa.String(64), nullable=False, index=True),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("client_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_index("ix_sync_event_seq", "sync_operations", ["event_id", "seq"])

def downgrade():
    op.drop_table("sync_operations")
