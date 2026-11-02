"""SBOM of the image: built by docker/make_sbom.py, served at /sarcade-sbom.cdx.json."""
import importlib.util
import json
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from sarcade.api.app import app

ROOT = Path(__file__).resolve().parents[1]


def load_make_sbom():
    spec = importlib.util.spec_from_file_location("make_sbom", ROOT / "docker" / "make_sbom.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RAW = {
    "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
    "metadata": {"component": {"type": "file", "name": "/"}},
    "components": [
        {"bom-ref": "pkg:pypi/fastapi@0.115.0", "type": "library", "name": "fastapi", "version": "0.115.0",
         "purl": "pkg:pypi/fastapi@0.115.0"},
        {"bom-ref": "pkg:deb/debian/libexpat1@2.6.2", "type": "library", "name": "libexpat1", "version": "2.6.2",
         "purl": "pkg:deb/debian/libexpat1@2.6.2?distro=debian-13"},
        {"bom-ref": "file-1", "type": "file", "name": "/usr/lib/x.so"},
    ],
    "dependencies": [{"ref": "pkg:pypi/fastapi@0.115.0"}, {"ref": "file-1"}],
}


# --- route ---------------------------------------------------------------------

def test_sbom_served_when_present(tmp_path, monkeypatch):
    path = tmp_path / "sarcade-image.json"
    path.write_text(json.dumps(RAW), encoding="utf-8")
    monkeypatch.setenv("SARCADE_SBOM_FILE", str(path))
    r = TestClient(app).get("/sarcade-sbom.cdx.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.cyclonedx+json")
    assert r.headers["cache-control"] == "no-cache"
    assert r.json()["bomFormat"] == "CycloneDX"


def test_sbom_absent_answers_404(tmp_path, monkeypatch):
    monkeypatch.setenv("SARCADE_SBOM_FILE", str(tmp_path / "missing.json"))
    r = TestClient(app).get("/sarcade-sbom.cdx.json")
    assert r.status_code == 404
    assert r.json() == {"detail": "sbom_not_available"}


def test_sbom_route_not_in_openapi():
    assert "/sarcade-sbom.cdx.json" not in TestClient(app).get("/openapi.json").json()["paths"]


# --- build step ------------------------------------------------------------------

def web_root(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    web.mkdir()
    (web / "sarcade-web.sha256").write_text("sha256:" + "ab" * 32 + "\n", encoding="utf-8")
    (web / "version.json").write_text('{"version": "0.1.0", "build_number": "415"}', encoding="utf-8")
    return web


def test_finalize_keeps_packages_and_adds_web_client(tmp_path):
    mod = load_make_sbom()
    doc = mod.finalize(json.loads(json.dumps(RAW)), "1.2.3", str(web_root(tmp_path)))
    names = [c["name"] for c in doc["components"]]
    assert names == ["fastapi", "libexpat1", "sarcade-web"]
    web = doc["components"][-1]
    assert web["version"] == "0.1.0+415"
    assert web["hashes"] == [{"alg": "SHA-256", "content": "ab" * 32}]
    assert doc["dependencies"] == [{"ref": "pkg:pypi/fastapi@0.115.0"}]
    assert doc["metadata"]["component"]["name"] == "sarcade-server"
    assert doc["metadata"]["component"]["version"] == "1.2.3"


def test_finalize_without_web_client(tmp_path):
    doc = load_make_sbom().finalize(json.loads(json.dumps(RAW)), "", str(tmp_path / "none"))
    assert "sarcade-web" not in [c["name"] for c in doc["components"]]
    assert doc["metadata"]["component"]["version"] == "dev"


def test_no_tool_skips_without_failing(tmp_path, capsys):
    out = tmp_path / "sbom" / "sarcade-image.json"
    assert load_make_sbom().main(str(tmp_path / "empty"), str(out)) == 0
    assert not out.exists()
    assert "sbom: no syft" in capsys.readouterr().out


def fake_syft(tmp_path: Path, script: str) -> Path:
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    syft = tool_dir / "syft"
    syft.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    syft.chmod(syft.stat().st_mode | stat.S_IEXEC)
    return tool_dir


def test_scan_writes_the_sbom(tmp_path, capsys):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(RAW), encoding="utf-8")
    # The output path is given as "-o cyclonedx-json=<path>".
    tool_dir = fake_syft(tmp_path, f'for a in "$@"; do case "$a" in cyclonedx-json=*) cp {raw} "${{a#cyclonedx-json=}}";; esac; done\n')
    out = tmp_path / "sbom" / "sarcade-image.json"
    assert load_make_sbom().main(str(tool_dir), str(out), root=str(tmp_path), web_root=str(web_root(tmp_path))) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [c["name"] for c in doc["components"]] == ["fastapi", "libexpat1", "sarcade-web"]
    assert "3 components" in capsys.readouterr().out


def test_failed_scan_leaves_no_file_and_never_fails(tmp_path, capsys):
    tool_dir = fake_syft(tmp_path, "echo boom >&2\nexit 3\n")
    out = tmp_path / "sbom" / "sarcade-image.json"
    out.parent.mkdir()
    out.write_text("stale", encoding="utf-8")
    assert load_make_sbom().main(str(tool_dir), str(out), root=str(tmp_path)) == 0
    assert not out.exists()
    assert "sbom: not generated (syft exit 3: boom)" in capsys.readouterr().out


def test_syft_runs_offline_on_installed_packages():
    mod = load_make_sbom()
    command = mod.syft_command(Path("/t/syft"), "/", Path("/tmp/o.json"), "")
    assert command[command.index("--override-default-catalogers") + 1] == "image"
    assert "./proc/**" in command and "./tmp/**" in command
    env = mod.syft_env()
    assert env["SYFT_CHECK_FOR_APP_UPDATE"] == "false"
    assert env["SYFT_PYTHON_SEARCH_REMOTE_LICENSES"] == "false"


def test_dockerfile_builds_the_sbom_last():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM anchore/syft:" in dockerfile
    assert dockerfile.index("RUN --mount=type=bind,from=sbom-tool") > dockerfile.index("fetch_web_client.py /tmp/web-src")
    assert "ENV SARCADE_SBOM_FILE=/app/sbom/sarcade-image.json" in dockerfile
