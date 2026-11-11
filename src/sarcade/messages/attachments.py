"""Message attachments (photos) — validation shared by sync and upload.

A message only references files: the bytes travel through the files API,
uploaded by the client before the message (or later, after a network loss).
"""
import re

MAX_ATTACHMENTS = 4
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_MIME = re.compile(r"^[A-Za-z0-9.+-]{1,60}/[A-Za-z0-9.+-]{1,99}$")


def valid_file_id(value) -> bool:
    """Client-chosen file ids are also file names on disk: strict charset."""
    return isinstance(value, str) and bool(_ID.match(value))


def clean_mime_type(value, default: str = "application/octet-stream") -> str:
    if isinstance(value, str) and _MIME.match(value.strip()):
        return value.strip().lower()
    return default


def clean_attachments(value) -> list[dict]:
    """Canonical attachment list, or ValueError when it cannot be trusted."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ATTACHMENTS:
        raise ValueError("invalid_attachments")
    out = []
    for item in value:
        if not isinstance(item, dict) or not valid_file_id(item.get("file_id")):
            raise ValueError("invalid_attachment")
        size = item.get("size_bytes", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("invalid_attachment_size")
        name = item.get("name")
        name = name.strip()[:255] if isinstance(name, str) and name.strip() else "photo.jpg"
        out.append({
            "file_id": item["file_id"],
            "name": name,
            "mime_type": clean_mime_type(item.get("mime_type")),
            "size_bytes": size,
        })
    return out


def logbook_summary(body: str, attachments: list[dict]) -> str:
    if not attachments:
        return body
    photos = sum(1 for a in attachments if a["mime_type"].startswith("image/"))
    label = f"{photos} photo{'s' if photos > 1 else ''}" if photos == len(attachments) else f"{len(attachments)} pièce(s) jointe(s)"
    return f"{body} [{label}]" if body else f"[{label}]"
