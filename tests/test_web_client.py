from fastapi import FastAPI
from fastapi.testclient import TestClient

from sarcade.api.web_client import mount_web_client


def make_app(tmp_path, with_index=True):
    if with_index:
        (tmp_path / "index.html").write_text("<html>SARCADE</html>", encoding="utf-8")
    (tmp_path / "main.dart.js").write_text("// app", encoding="utf-8")
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    mounted = mount_web_client(app, tmp_path)
    return app, mounted


def test_not_mounted_without_root():
    assert mount_web_client(FastAPI(), None) is False
    assert mount_web_client(FastAPI(), "") is False


def test_not_mounted_without_index(tmp_path):
    _, mounted = make_app(tmp_path, with_index=False)
    assert mounted is False


def test_serves_client_and_keeps_api(tmp_path):
    app, mounted = make_app(tmp_path)
    assert mounted
    client = TestClient(app)
    root = client.get("/")
    assert root.status_code == 200
    assert "SARCADE" in root.text
    assert root.headers["cache-control"] == "no-cache"
    asset = client.get("/main.dart.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "no-cache"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/missing.js").status_code == 404
