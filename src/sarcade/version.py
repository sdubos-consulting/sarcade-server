# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Version of the running server (card « Consolidation : version de
référence et versions déployées », lot 28): version, commit, label of the
reference version, digest of the web client served and of the SBOM of the
image. Read in 10 seconds what runs on the NAS.

Set at the construction of the image (``.github/workflows/image.yml``):

``SARCADE_VERSION``  version (« 0.1.0-exercice1 » for a label, else empty)
``SARCADE_COMMIT``   commit built (40 characters)
``SARCADE_TAG``      label of the reference version (« v0.1.0-exercice1 »)
                     when the image is built from a label, else empty

The reference is the label when there is one, else « main@1a2b3c4 ».
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sarcade.updates import service as updates

_sbom_cache: dict = {"key": None, "digest": None}


def commit() -> str | None:
    c = (os.getenv("SARCADE_COMMIT") or "").strip().lower()
    return c if c and all(ch in "0123456789abcdef" for ch in c) else None


def tag() -> str | None:
    t = (os.getenv("SARCADE_TAG") or "").strip()
    return t[:64] or None


def reference(tag_: str | None = None, commit_: str | None = None, branch: str = "main") -> str:
    """« v0.1.0-exercice1 », else « main@1a2b3c4 », else « dev » (local build)."""
    if tag_:
        return tag_
    if commit_:
        return f"{branch}@{commit_[:7]}"
    return "dev"


def web_digest(root: str | None = None) -> str | None:
    """« sha256:… » of the web client served (written by docker/fetch_web_client.py)."""
    root = root if root is not None else os.getenv("SARCADE_WEB_ROOT")
    if not root:
        return None
    try:
        text = (Path(root) / "sarcade-web.sha256").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if text.startswith("sha256:") else (f"sha256:{text}" if text else None)


def sbom_digest(path: Path | None = None) -> str | None:
    """« sha256:… » of the SBOM served at /sarcade-sbom.cdx.json, kept while
    the file is unchanged."""
    if path is None:
        from sarcade.api.sbom import sbom_file
        path = sbom_file()
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _sbom_cache["key"] != key:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sbom_cache.update(key=key, digest=f"sha256:{h.hexdigest()}")
    return _sbom_cache["digest"]


def public_repo() -> str | None:
    """Public repository where an issue may be opened (card « Signaler un
    problème : mail en principal, lien GitHub en second », 11 Nov 2026,
    choice 3): ``SARCADE_PUBLIC_REPO_URL``, a https://github.com/owner/repo
    address; None (no link in the application) when empty or anything else."""
    import os
    import re
    url = (os.environ.get("SARCADE_PUBLIC_REPO_URL") or "").strip().rstrip("/")
    return url if re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url) else None


def info() -> dict:
    c, t = commit(), tag()
    return {"service": "sarcade-server", "version": updates.installed_version(), "commit": c,
            "commit_short": c[:7] if c else None, "tag": t, "reference": reference(t, c),
            "web_sha256": web_digest(), "sbom_sha256": sbom_digest(), "public_repo": public_repo()}
