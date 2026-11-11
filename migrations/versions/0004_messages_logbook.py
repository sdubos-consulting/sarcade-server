"""Messages, acknowledgements and operational logbook."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("messages",
      sa.Column("id",sa.String(64),primary_key=True),
      sa.Column("event_id",sa.String(64),sa.ForeignKey("events.id"),nullable=False),
      sa.Column("sender_id",sa.String(64),nullable=False),
      sa.Column("recipient_ids",sa.JSON(),nullable=False),
      sa.Column("priority",sa.String(16),nullable=False),
      sa.Column("body",sa.Text(),nullable=False),
      sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_messages_event_time","messages",["event_id","created_at"])
    op.create_table("acks",
      sa.Column("id",sa.String(64),primary_key=True),
      sa.Column("event_id",sa.String(64),sa.ForeignKey("events.id"),nullable=False),
      sa.Column("message_id",sa.String(64),sa.ForeignKey("messages.id"),nullable=False),
      sa.Column("actor_id",sa.String(64),nullable=False),
      sa.Column("status",sa.String(16),nullable=False),
      sa.Column("time",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_acks_message","acks",["message_id"])
    op.create_table("logbook",
      sa.Column("seq",sa.BigInteger(),primary_key=True,autoincrement=True),
      sa.Column("event_id",sa.String(64),sa.ForeignKey("events.id"),nullable=False),
      sa.Column("kind",sa.String(32),nullable=False),
      sa.Column("object_id",sa.String(64)),
      sa.Column("actor_id",sa.String(64)),
      sa.Column("summary",sa.Text(),nullable=False),
      sa.Column("time",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_logbook_event_seq","logbook",["event_id","seq"])

def downgrade():
    op.drop_table("logbook"); op.drop_table("acks"); op.drop_table("messages")
