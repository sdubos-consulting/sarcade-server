import pytest

from sarcade.messages.attachments import clean_attachments, clean_mime_type, logbook_summary, valid_file_id

FID = "0192f6a0-0000-7000-8000-000000000001"


def test_absent_attachments_are_empty():
    assert clean_attachments(None) == []
    assert clean_attachments([]) == []


def test_attachment_is_canonicalised():
    out = clean_attachments([{"file_id": FID, "name": "  photo.jpg ", "mime_type": "IMAGE/JPEG", "size_bytes": 12, "x": 1}])
    assert out == [{"file_id": FID, "name": "photo.jpg", "mime_type": "image/jpeg", "size_bytes": 12}]


@pytest.mark.parametrize("value", [
    "x", [1], [{"file_id": "../etc/passwd"}], [{"file_id": FID, "size_bytes": -1}],
    [{"file_id": FID, "size_bytes": True}], [{"file_id": FID}] * 5,
])
def test_invalid_attachments_are_refused(value):
    with pytest.raises(ValueError):
        clean_attachments(value)


def test_file_id_and_mime_rules():
    assert valid_file_id(FID)
    assert not valid_file_id("a/b") and not valid_file_id("") and not valid_file_id(None)
    assert clean_mime_type("text/html; charset=utf-8") == "application/octet-stream"
    assert clean_mime_type(None) == "application/octet-stream"
    assert clean_mime_type("image/png") == "image/png"


def test_logbook_summary():
    photo = {"file_id": FID, "name": "p.jpg", "mime_type": "image/jpeg", "size_bytes": 1}
    assert logbook_summary("Texte", []) == "Texte"
    assert logbook_summary("Véhicule", [photo]) == "Véhicule [1 photo]"
    assert logbook_summary("", [photo, photo]) == "[2 photos]"
