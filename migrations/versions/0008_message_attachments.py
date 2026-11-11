"""Photos and other files attached to messages."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("attachments", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("messages", "attachments")
