# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Subfolders of the event and personal files (29 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    # Free folder paths ("" = root) instead of the fixed folders by use;
    # « divers » was the catch-all: its files go to the root.
    with op.batch_alter_table("shared_files") as batch:
        batch.alter_column("folder", existing_type=sa.String(32), type_=sa.String(200),
                           existing_nullable=False, server_default="")
    op.execute("UPDATE shared_files SET folder = '' WHERE folder = 'divers'")
    op.add_column("personal_files", sa.Column("folder", sa.String(200), nullable=False, server_default=""))
    op.create_table(
        "file_folders",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(320), nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "owner", "path", name="uq_file_folder"),
    )


def downgrade():
    op.drop_table("file_folders")
    op.drop_column("personal_files", "folder")
    op.execute("UPDATE shared_files SET folder = 'divers' WHERE folder = '' OR length(folder) > 32")
    with op.batch_alter_table("shared_files") as batch:
        batch.alter_column("folder", existing_type=sa.String(200), type_=sa.String(32),
                           existing_nullable=False, server_default="divers")
