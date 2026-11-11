from fastapi.testclient import TestClient

from sarcade.api.app import app


def test_ping_answered_by_pong():
    client = TestClient(app)
    with client.websocket_connect("/api/v0.1/events/evt-ping/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong", "data": {}}
        ws.send_text("anything else")
        ws.send_text("ping")
        assert ws.receive_json()["type"] == "pong"
