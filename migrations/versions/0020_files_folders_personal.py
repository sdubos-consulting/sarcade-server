"""Folders of the shared files and « Personnel » folder (29 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shared_files", sa.Column("folder", sa.String(32), nullable=False, server_default="divers"))
    op.create_table(
        "personal_files",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_personal_files_owner_email", "personal_files", ["owner_email"])


def downgrade():
    op.drop_index("ix_personal_files_owner_email", "personal_files")
    op.drop_table("personal_files")
    op.drop_column("shared_files", "folder")
