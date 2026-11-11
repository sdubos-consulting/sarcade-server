# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Subfolders of the Files screen, for the event files and the personal
files. A folder is a path: "" for the root, else 1 to 3 segments joined by
"/". Folders are kept on their own (``file_folders``) so that an empty
folder stays; the list also contains the folders implied by the files."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import FileFolderRow

MAX_DEPTH = 3
MAX_SEGMENT = 60
MAX_NAME = 255


class FolderError(ValueError):
    """Refused folder operation: HTTP status and detail."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bad_chars(text: str) -> bool:
    return any(ch in "/\\" or ord(ch) < 32 or ord(ch) == 127 for ch in text)


def clean_path(raw, *, allow_root: bool = True) -> str:
    """Canonical folder path; FolderError(400) when invalid or too deep."""
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise FolderError(400, "invalid_folder")
    raw = raw.strip().strip("/")
    if not raw:
        if allow_root:
            return ""
        raise FolderError(400, "invalid_folder")
    segments = [s.strip() for s in raw.split("/")]
    if len(segments) > MAX_DEPTH:
        raise FolderError(400, "folder_too_deep")
    for s in segments:
        if not 1 <= len(s) <= MAX_SEGMENT or s in (".", "..") or _bad_chars(s):
            raise FolderError(400, "invalid_folder")
    return "/".join(segments)


def clean_name(raw) -> str:
    if not isinstance(raw, str):
        raise FolderError(400, "invalid_name")
    name = raw.strip()
    if not 1 <= len(name) <= MAX_NAME or _bad_chars(name) or name in (".", ".."):
        raise FolderError(400, "invalid_name")
    return name


def depth(path: str) -> int:
    return len(path.split("/")) if path else 0


def ancestors(path: str) -> list[str]:
    """"A/B/C" → ["A", "A/B", "A/B/C"]."""
    parts = path.split("/") if path else []
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _rows(db: Session, scope: str, owner: str) -> list[FileFolderRow]:
    return list(db.scalars(select(FileFolderRow).where(FileFolderRow.scope == scope,
                                                       FileFolderRow.owner == owner)).all())


def all_paths(db: Session, scope: str, owner: str, files) -> list[str]:
    """Every known folder of the scope, parents included, sorted."""
    known: set[str] = set()
    for p in [r.path for r in _rows(db, scope, owner)] + [f.folder or "" for f in files]:
        known.update(ancestors(p))
    return sorted(known, key=lambda p: (p.casefold(), p))


def ensure(db: Session, scope: str, owner: str, path: str, now: datetime | None = None) -> None:
    """Stores the folder and its parents (no-op for the root)."""
    if not path:
        return
    stored = {r.path for r in _rows(db, scope, owner)}
    for p in ancestors(path):
        if p not in stored:
            db.add(FileFolderRow(scope=scope, owner=owner, path=p, created_at=now or datetime.now(UTC)))
    db.flush()


def create(db: Session, scope: str, owner: str, files, raw_path) -> str:
    path = clean_path(raw_path, allow_root=False)
    if path in all_paths(db, scope, owner, files):
        raise FolderError(409, "folder_exists")
    ensure(db, scope, owner, path)
    return path


def rename(db: Session, scope: str, owner: str, files, raw_from, raw_to) -> tuple[str, str]:
    """Renames or moves a folder with its files and subfolders."""
    src = clean_path(raw_from, allow_root=False)
    dst = clean_path(raw_to, allow_root=False)
    known = all_paths(db, scope, owner, files)
    if src not in known:
        raise FolderError(404, "folder_not_found")
    if src == dst:
        return src, dst
    if under(dst, src) or dst in known:
        raise FolderError(409, "folder_exists" if dst in known else "folder_inside_itself")
    moved = [p for p in known if under(p, src)]
    if any(depth(dst + p[len(src):]) > MAX_DEPTH for p in moved):
        raise FolderError(400, "folder_too_deep")
    for r in _rows(db, scope, owner):
        if under(r.path, src):
            r.path = dst + r.path[len(src):]
    for f in files:
        if under(f.folder or "", src):
            f.folder = dst + f.folder[len(src):]
    db.flush()
    ensure(db, scope, owner, dst)
    return src, dst


def delete(db: Session, scope: str, owner: str, files, raw_path) -> str:
    """Deletes an empty folder (no file, no subfolder)."""
    path = clean_path(raw_path, allow_root=False)
    known = all_paths(db, scope, owner, files)
    if path not in known:
        raise FolderError(404, "folder_not_found")
    if any(under(f.folder or "", path) for f in files) or any(p != path and under(p, path) for p in known):
        raise FolderError(409, "folder_not_empty")
    for r in _rows(db, scope, owner):
        if r.path == path:
            db.delete(r)
    db.flush()
    return path
