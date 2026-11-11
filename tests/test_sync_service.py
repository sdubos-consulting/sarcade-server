from datetime import UTC, datetime
from unittest.mock import MagicMock
from sarcade.sync.service import record_operation

class FakeResult:
    def __init__(self,value=None): self.value=value

def test_rejects_unknown_object_type():
    db=MagicMock()
    db.scalar.return_value=None
    status,cursor=record_operation(db,event_id="e1",operation_id="op1",object_id="x1",
        object_type="unknown",action="create",payload={},client_time=datetime.now(UTC))
    assert status=="rejected"
    assert cursor==0
    db.add.assert_not_called()

def test_duplicate_operation_is_idempotent():
    db=MagicMock()
    existing=MagicMock(); existing.seq=42
    db.scalar.return_value=existing
    status,cursor=record_operation(db,event_id="e1",operation_id="op1",object_id="p1",
        object_type="position",action="create",payload={},client_time=datetime.now(UTC))
    assert status=="duplicate"
    assert cursor==42
    db.add.assert_not_called()
