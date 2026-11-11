from datetime import UTC, datetime

from sarcade.domain import Event, Organization, Position, SyncOperation, Team, User


def test_initial_domain_objects():
    org = Organization(id="org_01", name="Demo")
    user = User(id="usr_01", display_name="Opérateur")
    event = Event(id="evt_01", name="Exercice", kind="exercise", status="active", created_at=datetime.now(UTC))
    team = Team(id="team_01", event_id=event.id, name="Équipe 1")
    pos = Position(id="pos_01", event_id=event.id, device_id="dev_01", lat=48.8, lon=2.1, time=datetime.now(UTC))
    op = SyncOperation(operation_id="op_01", object_id=pos.id, object_type="position", action="create", client_time=datetime.now(UTC), payload={})

    assert org.status == "active"
    assert user.locale == "fr-FR"
    assert team.event_id == event.id
    assert -90 <= pos.lat <= 90
    assert op.retry_count == 0
