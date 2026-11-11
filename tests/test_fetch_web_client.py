"""Web client embedded in the Docker image (docker/fetch_web_client.py, Dockerfile)."""
import hashlib
import importlib.util
import io
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_fetch():
    spec = importlib.util.spec_from_file_location("fetch_web_client", ROOT / "docker" / "fetch_web_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_extracts_archive(tmp_path, capsys):
    archive = make_archive(
        tmp_path / "sarcade-web.tar.gz",
        {"index.html": "<html>SARCADE</html>", "version.json": '{"version":"0.1.0","build_number":"512"}'},
    )
    dest = tmp_path / "web"
    assert load_fetch().main(str(archive), str(dest)) == 0
    assert (dest / "index.html").read_text(encoding="utf-8") == "<html>SARCADE</html>"
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (dest / "sarcade-web.sha256").read_text(encoding="utf-8").strip() == digest
    assert digest in capsys.readouterr().out


def test_no_archive_is_not_fatal(tmp_path, capsys):
    """SARCADE_WEB=none: the web stage is empty, the image is API only."""
    fetch = load_fetch()
    assert fetch.main(str(tmp_path / "absent.tar.gz"), str(tmp_path / "web")) == 0
    assert fetch.main("", str(tmp_path / "web")) == 0
    assert not (tmp_path / "web").exists()
    assert "API only" in capsys.readouterr().out


def test_broken_archive_is_not_fatal(tmp_path, capsys):
    archive = tmp_path / "sarcade-web.tar.gz"
    archive.write_bytes(b"<html>404</html>")
    assert load_fetch().main(str(archive), str(tmp_path / "web")) == 0
    assert "not embedded" in capsys.readouterr().out


def test_archive_without_index_is_not_served(tmp_path, capsys):
    archive = make_archive(tmp_path / "sarcade-web.tar.gz", {"main.dart.js": "// app"})
    assert load_fetch().main(str(archive), str(tmp_path / "web")) == 0
    assert "no index.html" in capsys.readouterr().out


def test_unsafe_member_is_refused(tmp_path, capsys):
    archive = make_archive(tmp_path / "sarcade-web.tar.gz", {"../evil.txt": "x", "index.html": "ok"})
    assert load_fetch().main(str(archive), str(tmp_path / "web")) == 0
    assert not (tmp_path / "evil.txt").exists()
    assert "not embedded" in capsys.readouterr().out


def test_dockerfile_follows_the_published_client():
    """The cache of the web client follows the published archive by itself:
    downloaded by ADD (checked at every build), not by a RUN that Docker would
    keep in cache until SARCADE_WEB_CACHEBUST changes."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ADD \$\{SARCADE_WEB_URL\} /sarcade-web\.tar\.gz$", dockerfile, re.M)
    # Card « GitHub de nouveau actif (10 novembre 2026) »: local client by
    # default (built on the i9), optional and never the web-dev pre-release.
    assert re.search(r"^ARG SARCADE_WEB=local$", dockerfile, re.M)
    assert re.search(r"^COPY pyproject\.toml sarcade-web\.tar\.g\[z\] /$", dockerfile, re.M)
    assert "releases/latest/download/sarcade-web.tar.gz" in dockerfile
    assert "download/web-dev" not in dockerfile
    assert re.search(r"^FROM web-\$\{SARCADE_WEB\} AS web$", dockerfile, re.M)
    assert "FROM scratch AS web-none" in dockerfile
    assert "--mount=type=bind,from=web" in dockerfile
    assert "ARG SARCADE_WEB_CACHEBUST" in dockerfile  # manual forcing kept
    assert "urlopen" not in (ROOT / "docker" / "fetch_web_client.py").read_text(encoding="utf-8")


def test_nas_tools_need_no_cachebust():
    for name in ("sarcade-switch.sh",):
        script = (ROOT / "tool" / "nas" / name).read_text(encoding="utf-8")
        assert "SARCADE_WEB_CACHEBUST" not in script, name
