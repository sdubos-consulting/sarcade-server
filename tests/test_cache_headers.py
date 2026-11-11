from fastapi.testclient import TestClient

from sarcade.api.app import app


def test_api_answers_are_never_cached():
    client = TestClient(app)
    assert client.get("/health").headers["cache-control"] == "no-store"
    # Any API path, even a 404, carries the header.
    assert client.get("/api/v0.1/does-not-exist").headers["cache-control"] == "no-store"
