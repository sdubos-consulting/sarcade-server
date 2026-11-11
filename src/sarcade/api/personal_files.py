"""« Personnel » folder of the Files screen (decision of 28 Oct 2026):
procedures, radio plans… of one person, kept by the server, visible to
that person only, tied to the email checked by Cloudflare Access. Not a
safe for identity papers (said in the application). Quota per user set by
the server manager (100 MB by default)."""
import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade.db.models import PersonalFileRow
from sarcade.files import folders
from sarcade.messages import attachments as msg_attachments
from sarcade.profile.preferences import normal_email
from sarcade.access.verify import identity_of
from sarcade.updates.service import get_value

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
ROOT = Path(os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")) / "personal"
MAX_FILE_BYTES = int(os.getenv("SARCADE_MAX_FILE_BYTES", str(25 * 1024 * 1024)))
DEFAULT_QUOTA_MB = 100


def quota_bytes(db: Session) -> int:
    mb = get_value(db, "personal_quota_mb", DEFAULT_QUOTA_MB)
    return int(mb if isinstance(mb, (int, float)) and not isinstance(mb, bool) and mb > 0 else DEFAULT_QUOTA_MB) * 1024 * 1024


def _owner(request: Request) -> str:
    identity = identity_of(request)
    if identity is None or not identity.email:
        raise HTTPException(status_code=409, detail="no_identity")
    return normal_email(identity.email)


def _used(db: Session, owner: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(PersonalFileRow.size_bytes), 0))
                         .where(PersonalFileRow.owner_email == owner)) or 0)


def _dict(r: PersonalFileRow) -> dict:
    return {"id": r.id, "name": r.name, "mime_type": r.mime_type, "size_bytes": r.size_bytes,
            "sha256": r.sha256, "created_at": r.created_at, "folder": r.folder or ""}


SCOPE = "personal"


def _files(db: Session, owner: str) -> list[PersonalFileRow]:
    return list(db.scalars(select(PersonalFileRow).where(PersonalFileRow.owner_email == owner)
                           .order_by(PersonalFileRow.created_at.desc())).all())


@router.get("/me/files")
def list_personal(request: Request, db: Session = Depends(get_db)):
    owner = _owner(request)
    rows = _files(db, owner)
    return {"files": [_dict(r) for r in rows], "folders": folders.all_paths(db, SCOPE, owner, rows),
            "used_bytes": _used(db, owner), "quota_bytes": quota_bytes(db)}


@router.post("/me/files", status_code=201)
async def upload_personal(request: Request, file: UploadFile = File(...), folder: str | None = Form(None),
                          db: Session = Depends(get_db)):
    owner = _owner(request)
    try:
        folder = folders.clean_path(folder if folder is not None else request.query_params.get("folder"))
    except folders.FolderError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")
    if _used(db, owner) + len(data) > quota_bytes(db):
        raise HTTPException(status_code=413, detail="quota_exceeded")
    fid = str(uuid.uuid4())
    directory = ROOT / hashlib.sha256(owner.encode()).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / fid
    path.write_bytes(data)
    row = PersonalFileRow(id=fid, owner_email=owner, name=(file.filename or "fichier")[:255],
                          mime_type=msg_attachments.clean_mime_type(file.content_type), size_bytes=len(data),
                          storage_path=str(path), sha256=hashlib.sha256(data).hexdigest(), created_at=datetime.now(UTC),
                          folder=folder)
    db.add(row)
    folders.ensure(db, SCOPE, owner, folder)
    db.commit()
    return _dict(row)


def _mine(db: Session, owner: str, file_id: str) -> PersonalFileRow:
    row = db.get(PersonalFileRow, file_id)
    if row is None or row.owner_email != owner:
        raise HTTPException(status_code=404, detail="file_not_found")
    return row


# --- Subfolders (owner only) ----------------------------------------------

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402


class FolderIn(BaseModel):
    path: str = Field(max_length=400)


class FolderMove(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", max_length=400)
    to: str = Field(max_length=400)


class FilePatch(BaseModel):
    folder: str | None = Field(default=None, max_length=400)
    name: str | None = Field(default=None, max_length=400)


def _folder_call(fn, *args):
    try:
        return fn(*args)
    except folders.FolderError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/me/files/folders")
def list_personal_folders(request: Request, db: Session = Depends(get_db)):
    owner = _owner(request)
    return {"folders": folders.all_paths(db, SCOPE, owner, _files(db, owner))}


@router.post("/me/files/folders", status_code=201)
def create_personal_folder(body: FolderIn, request: Request, db: Session = Depends(get_db)):
    owner = _owner(request)
    files = _files(db, owner)
    path = _folder_call(folders.create, db, SCOPE, owner, files, body.path)
    db.commit()
    return {"path": path, "folders": folders.all_paths(db, SCOPE, owner, files)}


@router.patch("/me/files/folders")
def move_personal_folder(body: FolderMove, request: Request, db: Session = Depends(get_db)):
    owner = _owner(request)
    files = _files(db, owner)
    src, dst = _folder_call(folders.rename, db, SCOPE, owner, files, body.from_, body.to)
    db.commit()
    return {"from": src, "to": dst, "folders": folders.all_paths(db, SCOPE, owner, files)}


@router.delete("/me/files/folders", status_code=204)
def delete_personal_folder(request: Request, path: str = Query(max_length=400), db: Session = Depends(get_db)):
    owner = _owner(request)
    _folder_call(folders.delete, db, SCOPE, owner, _files(db, owner), path)
    db.commit()


@router.patch("/me/files/{file_id}")
def move_personal_file(file_id: str, body: FilePatch, request: Request, db: Session = Depends(get_db)):
    """Moves (``folder``) and/or renames (``name``) a file; names may repeat."""
    owner = _owner(request)
    row = _mine(db, owner, file_id)
    if body.folder is not None:
        row.folder = _folder_call(folders.clean_path, body.folder)
        folders.ensure(db, SCOPE, owner, row.folder)
    if body.name is not None:
        row.name = _folder_call(folders.clean_name, body.name)
    db.commit()
    return _dict(row)


@router.get("/me/files/{file_id}/content")
def download_personal(file_id: str, request: Request, db: Session = Depends(get_db)):
    row = _mine(db, _owner(request), file_id)
    path = Path(row.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file_content_not_found")
    return FileResponse(path, media_type=row.mime_type, filename=row.name, headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/me/files/{file_id}", status_code=204)
def delete_personal(file_id: str, request: Request, db: Session = Depends(get_db)):
    row = _mine(db, _owner(request), file_id)
    Path(row.storage_path).unlink(missing_ok=True)
    db.delete(row)
    db.commit()


# --- Quota, set by the server manager (console d'administration) ----------

from pydantic import BaseModel, Field  # noqa: E402

from .admin import require_admin  # noqa: E402


class QuotaIn(BaseModel):
    mb: int = Field(ge=1, le=100_000)


@router.get("/admin/personal-quota")
def get_quota(_admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    return {"mb": quota_bytes(db) // (1024 * 1024)}


@router.put("/admin/personal-quota")
def put_quota(body: QuotaIn, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    from sarcade.updates.service import set_value
    set_value(db, "personal_quota_mb", body.mb)
    db.commit()
    return {"mb": body.mb}
