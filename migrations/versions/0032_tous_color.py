# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Colour of « Tous » (card « Messages : couleur de Tous imposée », validated
on 5 Nov 2026, 18 h 33): the default grey-blue becomes blue. Only the groups
« Tous » still in the original grey-blue change; a colour chosen by hand is
kept. The revision goes up so that the applications take the change.
"""
from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

OLD = 0xFF546E7A
NEW = 0xFF1565C0


def _json(value):
    if value is None or isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _recolour(source: int, target: int):
    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = bind.execute(sa.text("SELECT id, data, revision FROM comm_groups WHERE deleted_at IS NULL")).fetchall()
    for gid, raw, rev in rows:
        data = _json(raw)
        if not isinstance(data, dict) or data.get("kind") != "all" or data.get("color") != source:
            continue
        data["color"] = target
        bind.execute(sa.text("UPDATE comm_groups SET data = :d, revision = :r, updated_at = :t WHERE id = :id"),
                     {"d": json.dumps(data), "r": (rev or 1) + 1, "t": now, "id": gid})


def upgrade():
    _recolour(OLD, NEW)


def downgrade():
    _recolour(NEW, OLD)
