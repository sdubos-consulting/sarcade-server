from collections import defaultdict
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, event_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections[event_id].add(websocket)

    def disconnect(self, event_id: str, websocket: WebSocket):
        self._connections[event_id].discard(websocket)
        if not self._connections[event_id]:
            self._connections.pop(event_id, None)

    async def broadcast(self, event_id: str, payload: dict, exclude: WebSocket | None = None):
        dead = []
        for ws in list(self._connections.get(event_id, ())):
            if ws is exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(event_id, ws)


manager = ConnectionManager()


async def broadcast_all(payload: dict) -> None:
    """Same message on every event channel (revocations, rights changed)."""
    for event_id in list(manager._connections):
        await manager.broadcast(event_id, payload)
