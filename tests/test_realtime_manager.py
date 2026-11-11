import pytest

from sarcade.realtime.manager import ConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.messages.append(payload)


@pytest.mark.asyncio
async def test_broadcast_is_event_scoped():
    manager = ConnectionManager()
    a, b = FakeWebSocket(), FakeWebSocket()
    await manager.connect("evt-a", a)
    await manager.connect("evt-b", b)
    await manager.broadcast("evt-a", {"type": "position.updated"})
    assert a.accepted and b.accepted
    assert a.messages == [{"type": "position.updated"}]
    assert b.messages == []
