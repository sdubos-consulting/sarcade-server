# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Writes downloads.json of /telechargements (sarcade.api.downloads), called
by tool/nas/publish-registry.ps1:

    python -I tool/nas/downloads_manifest.py <dossier> <app-files.json> <sources-serveur.tar.gz> <main@commit>

<dossier> holds the files of the image (downloads/ next to the Dockerfile);
app-files.json comes from tool/build-downloads.ps1 of sarcade-app (Windows,
APK, sources of the application); the archive of the server sources is
added. Every entry is checked against the file actually present.
"""
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(folder: str, app_files: str, server_sources: str, server_build: str) -> int:
    root = Path(folder)
    raw = json.loads(Path(app_files).read_text(encoding="utf-8-sig"))
    entries = raw if isinstance(raw, list) else [raw]
    src = Path(server_sources)
    entries.append({"kind": "sources-server", "name": src.name, "version": None, "build": server_build,
                    "date": date.today().isoformat(), "signed": False})
    files = []
    for e in entries:
        path = root / e["name"]
        if not path.is_file():
            print(f"absent : {path}", file=sys.stderr)
            return 1
        files.append({**e, "size": path.stat().st_size, "sha256": sha256(path)})
        if e.get("sha256") and e["sha256"].lower() != files[-1]["sha256"]:
            print(f"empreinte différente : {path}", file=sys.stderr)
            return 1
    manifest = {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    (root / "downloads.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in files:
        print(f"{f['kind']:15} {f['name']}  {f['size'] / 1024 ** 2:.1f} Mo")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
