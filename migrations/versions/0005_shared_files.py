"""Shared event files."""
from alembic import op
import sqlalchemy as sa

revision="0005"
down_revision="0004"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("shared_files",
      sa.Column("id",sa.String(64),primary_key=True),
      sa.Column("event_id",sa.String(64),sa.ForeignKey("events.id"),nullable=False,index=True),
      sa.Column("sender_id",sa.String(64),nullable=False),
      sa.Column("name",sa.String(255),nullable=False),
      sa.Column("mime_type",sa.String(160),nullable=False),
      sa.Column("size_bytes",sa.BigInteger(),nullable=False),
      sa.Column("storage_path",sa.Text(),nullable=False),
      sa.Column("sha256",sa.String(64),nullable=False),
      sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table("shared_files")
