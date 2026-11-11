"""Downloads served by the SARCADE server itself (card « Windows : publier
l'installateur sans GitHub », validated on 8 Nov 2026; GitHub suspended
since 6 Nov 2026).

The Windows installer, the Android APK and the sources are built on the i9
and baked into the last layer of the image (``SARCADE_DOWNLOADS=local`` of
the Dockerfile, written by ``tool/nas/publish-registry.ps1``), with a
manifest ``downloads.json``::

    {"generated_at": "2026-11-09T10:00:00Z",
     "files": [{"kind": "windows", "name": "sarcade-windows-setup.exe",
                "version": "0.1.0+1286", "build": "main@abc1234",
                "size": 123, "sha256": "…", "signed": false}, …]}

``/telechargements`` lists them (version, date, size, SHA-256), each file is
served at ``/telechargements/<name>``, and the Windows installer becomes the
Windows package of ``/api/v0.1/clients/latest`` when it is newer than the
one placed by the administrator: SARCADE for Windows updates from its
server, without GitHub. Same exposure as the web client.
"""
import html
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

DEFAULT_DOWNLOADS_ROOT = "/app/downloads"
MANIFEST = "downloads.json"
KINDS = ("windows", "android", "sources-app", "sources-server")
# Platform of /clients/latest for a kind (keys of admin.CLIENT_FILES).
PLATFORM_OF_KIND = {"windows": "windows", "android": "apk"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

router = APIRouter()


def downloads_root() -> Path:
    return Path(os.getenv("SARCADE_DOWNLOADS_ROOT") or DEFAULT_DOWNLOADS_ROOT)


def _valid(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    kind, name, sha, size = entry.get("kind"), entry.get("name"), entry.get("sha256"), entry.get("size")
    if kind not in KINDS or not isinstance(name, str) or not _NAME.match(name) or name == MANIFEST:
        return None
    if not isinstance(sha, str) or not _HEX64.match(sha) or isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    out = {"kind": kind, "name": name, "sha256": sha, "size": size, "signed": entry.get("signed") is True}
    for key in ("version", "build", "date"):
        v = entry.get(key)
        out[key] = v.strip()[:64] if isinstance(v, str) and v.strip() else None
    return out


def manifest() -> dict:
    """Content of downloads.json, only the entries whose file is present
    with the announced size; {"files": []} when there is nothing."""
    root = downloads_root()
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generated_at": None, "files": []}
    files = []
    for raw in data.get("files", []) if isinstance(data, dict) else []:
        entry = _valid(raw)
        if entry is None or any(f["name"] == entry["name"] for f in files):
            continue
        path = root / entry["name"]
        if path.is_file() and path.stat().st_size == entry["size"]:
            files.append(entry)
    files.sort(key=lambda f: KINDS.index(f["kind"]))
    generated = data.get("generated_at") if isinstance(data, dict) else None
    return {"generated_at": generated if isinstance(generated, str) else None, "files": files}


def _version_key(version: str | None) -> tuple:
    from sarcade.updates import policy
    v = (version or "").strip()
    build = v.partition("+")[2]
    return policy.parse_version(v) + (int(build) if build.isdigit() else -1,)


def newer(a: str | None, b: str | None) -> bool:
    """« 0.1.0+1286 » newer than « 0.1.0+1285 » (build number counted)."""
    return _version_key(a) > _version_key(b)


def bundled_packages() -> dict:
    """{"windows": {...}, "apk": {...}}: the packages of the image, in the
    shape of the « client_packages » setting, with their URL."""
    out = {}
    for f in manifest()["files"]:
        platform = PLATFORM_OF_KIND.get(f["kind"])
        if platform and f["version"]:
            out[platform] = {"version": f["version"], "sha256": f["sha256"], "size": f["size"],
                             "uploaded_at": f["date"], "build": f["build"], "source": "image",
                             "url": f"/telechargements/{f['name']}"}
    return out


def effective_packages(stored: dict) -> dict:
    """Packages placed by the administrator ([stored]), replaced by those of
    the image when these are newer or when the stored one came from the
    windows-dev pre-release of GitHub (source « github », no longer
    published; card « GitHub de nouveau actif (10 novembre 2026) »). One
    taken from the latest major release (source « release ») stays while it
    is newer."""
    packages = dict(stored or {})
    for platform, pkg in bundled_packages().items():
        cur = packages.get(platform)
        if not cur or cur.get("source") == "github" or newer(pkg["version"], cur.get("version")):
            packages[platform] = pkg
    return packages


TITLES = {"windows": "SARCADE pour Windows (installeur)", "android": "SARCADE pour Android (APK)",
          "sources-app": "Sources de l'application", "sources-server": "Sources du serveur"}
NOTES = {
    "android": "Pour les appareils sans Google Play. Sur un téléphone qui a SARCADE par Google Play, "
               "garder Google Play : cet APK ne peut pas le remplacer sans le désinstaller.",
    "sources-app": "Licence dans l'archive (fichier LICENSE).",
    "sources-server": "Licence dans l'archive (fichier LICENSE).",
}
UNSIGNED = ("Installeur pas encore signé : Windows affiche « Windows a protégé votre ordinateur ». "
            "Cliquer sur « Informations complémentaires », puis « Exécuter quand même ».")


def _size(n: int) -> str:
    return f"{n / 1024 ** 2:.1f} Mo".replace(".", ",") if n >= 1024 ** 2 else f"{max(1, round(n / 1024))} ko"


def render_page(data: dict) -> str:
    esc = html.escape
    rows = []
    for f in data["files"]:
        notes = [NOTES[f["kind"]]] if f["kind"] in NOTES else []
        if f["kind"] == "windows" and not f["signed"]:
            notes.append(UNSIGNED)
        meta = " · ".join(esc(x) for x in (f["version"], f["build"], f["date"], _size(f["size"])) if x)
        rows.append(
            f'<section><h2>{esc(TITLES[f["kind"]])}</h2>'
            f'<p class="meta">{meta}</p>'
            f'<p><a class="btn" href="/telechargements/{esc(f["name"])}" download>Télécharger {esc(f["name"])}</a></p>'
            + "".join(f"<p>{esc(n)}</p>" for n in notes)
            + f'<p class="sha">SHA-256 : <code>{esc(f["sha256"])}</code></p></section>')
    body = "".join(rows) or "<p>Aucun fichier sur ce serveur pour l'instant.</p>"
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SARCADE · Téléchargements</title>
<style>
:root{{color-scheme:light dark;--fg:#0A2A55;--bg:#f6f8fb;--card:#fff;--mute:#5b6676;--line:#dce3ee}}
@media (prefers-color-scheme:dark){{:root{{--fg:#e8eef8;--bg:#0b1220;--card:#121c2e;--mute:#9aa7bb;--line:#1c3a66}}}}
body{{margin:0;font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:760px;margin:0 auto;padding:24px 16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}}
h1{{font-size:1.6rem;margin:0 0 4px}} h2{{font-size:1.15rem;margin:0 0 4px}}
p{{overflow-wrap:anywhere}} .meta,.sha{{color:var(--mute);font-size:.9rem}} code{{word-break:break-all}}
.btn{{display:inline-block;max-width:100%;box-sizing:border-box;padding:10px 16px;border-radius:999px;background:#0A2A55;color:#fff;text-decoration:none}}
@media (prefers-color-scheme:dark){{.btn{{background:#1C3A66}}}}
</style></head><body><main><h1>Téléchargements</h1>
<p class="meta">Fichiers servis par ce serveur SARCADE. Vérifier l'empreinte SHA-256 après le téléchargement si besoin.</p>
{body}</main></body></html>"""


@router.get("/api/v0.1/downloads")
def list_downloads():
    data = manifest()
    return {**data, "files": [{**f, "url": f"/telechargements/{f['name']}"} for f in data["files"]]}


@router.get("/telechargements", include_in_schema=False)
def downloads_page():
    return HTMLResponse(render_page(manifest()), headers={"Cache-Control": "no-cache"})


@router.get("/telechargements/{name}", include_in_schema=False)
def download(name: str):
    entry = next((f for f in manifest()["files"] if f["name"] == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="no_such_download")
    media = "application/vnd.android.package-archive" if name.endswith(".apk") else "application/octet-stream"
    return FileResponse(downloads_root() / entry["name"], media_type=media, filename=entry["name"],
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


PRIVACY_PAGE = Path(__file__).with_name("confidentialite.html")


@router.get("/confidentialite", include_in_schema=False)
def privacy_page():
    """Privacy policy of the application (copy of site/confidentialite of
    sarcade-app), declared to Google Play: public once the porteur opens
    this path in Cloudflare Access (card « Téléchargements servis par le
    serveur SARCADE », choice 8)."""
    try:
        text = PRIVACY_PAGE.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="privacy_page_missing") from None
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})
