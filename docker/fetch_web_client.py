"""Installs the SARCADE web client into the image (Docker build step).

The archive is downloaded by the ``ADD`` of the Dockerfile ("web" stage),
which invalidates the build cache whenever a new client is published.
Non-fatal: without archive (``SARCADE_WEB=none``) or with a broken one, the
image still builds and the server simply does not serve the web client (the
API is unaffected).
"""
import sys
import tarfile
from pathlib import Path


def main(archive: str, target: str) -> int:
    source = Path(archive) if archive else None
    if source is None or not source.is_file():
        print("web client: no archive, skipped (API only)")
        return 0
    dest = Path(target)
    try:
        with tarfile.open(source, mode="r:gz") as tar:
            dest.mkdir(parents=True, exist_ok=True)
            tar.extractall(dest, filter="data")
    except Exception as exc:  # noqa: BLE001 - any failure leaves the API usable
        print(f"web client: not embedded ({exc})")
        return 0
    if not (dest / "index.html").is_file():
        print("web client: archive has no index.html, not served")
        return 0
    version = dest / "version.json"
    detail = f", {version.read_text(encoding='utf-8').strip()}" if version.is_file() else ""
    print(f"web client: embedded in {dest}{detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2] if len(sys.argv) > 2 else "/app/web"))
