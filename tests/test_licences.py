"""Signed SARCADE Pro licences (note 11, decisions of 10 Oct 2026)."""
from datetime import UTC, date, datetime
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from sarcade import licensing
from sarcade.licensing import core, issue

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
ROOT = Ed25519PrivateKey.generate()
KEY = Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def roots(monkeypatch, tmp_path):
    monkeypatch.setitem(core.ROOT_KEYS, "R-TEST", issue.public_b64(ROOT))
    monkeypatch.setenv("SARCADE_LICENCE_FILE", str(tmp_path / "licence.json"))
    monkeypatch.delenv("SARCADE_LICENCE_TEST_ROOT", raising=False)
    licensing._state = None
    yield
    licensing._state = None


def cert(not_before=date(2026, 1, 1), not_after=date(2029, 1, 1), key=KEY, root=ROOT, root_id="R-TEST", key_id="K-1"):
    return issue.make_key_certificate(root, root_id, key, key_id, not_before, not_after)


def licence(kind="association", modules=("siem", "assist"), issued=date(2026, 10, 1), expires=date(2027, 10, 1),
            certificate=None, key=KEY, licence_id="L-1"):
    return issue.make_licence(key, certificate or cert(key=key), "ADRASEC 78", kind, list(modules), issued, expires,
                              licence_id)


def test_valid_association_licence():
    s = core.verify(licence(), NOW)
    assert s.status == "valid" and s.modules == {"siem", "assist"}
    d = s.as_dict()
    assert d["organisation"] == "ADRASEC 78" and d["kind"] == "association" and d["days_left"] == 356


def test_no_licence_means_core_only():
    assert core.verify(None, NOW).status == "none"
    assert licensing.enabled_modules() == set()


def test_tampered_licence_refused():
    doc = licence()
    doc["licence"]["modules"].append("assist")
    assert core.verify(doc, NOW).reason == "bad_signature"
    doc = licence()
    doc["licence"]["expires_at"] = "2099-01-01"
    assert core.verify(doc, NOW).status == "invalid"


def test_key_not_certified_by_root():
    other_root = Ed25519PrivateKey.generate()
    doc = licence(certificate=cert(root=other_root))
    assert core.verify(doc, NOW).reason == "bad_key_signature"
    doc = licence(certificate=cert(root_id="R-UNKNOWN"))
    assert core.verify(doc, NOW).reason == "unknown_root"


def test_key_must_be_valid_when_licence_issued():
    c = cert(not_before=date(2026, 1, 1), not_after=date(2026, 6, 1))
    assert core.verify(licence(certificate=c), NOW).reason == "key_not_valid_at_issue"
    # A key that expires later does not invalidate the licences issued before.
    c = cert(not_before=date(2026, 1, 1), not_after=date(2026, 10, 2))
    assert core.verify(licence(certificate=c), datetime(2027, 3, 1, tzinfo=UTC)).status == "valid"


def test_expiry_grace_then_pro_modules_stop():
    doc = licence(expires=date(2026, 10, 20))
    assert core.verify(doc, NOW).status == "expiring"
    grace = core.verify(doc, datetime(2026, 11, 15, tzinfo=UTC))
    assert grace.status == "grace" and grace.modules == {"siem", "assist"}
    gone = core.verify(doc, datetime(2026, 11, 20, tzinfo=UTC))
    assert gone.status == "expired" and gone.modules == set()


def test_revocation_list(monkeypatch):
    monkeypatch.setattr(core, "REVOKED_LICENCES", {"L-1"})
    assert core.verify(licence(), NOW).reason == "revoked"
    monkeypatch.setattr(core, "REVOKED_LICENCES", set())
    monkeypatch.setattr(core, "REVOKED_KEYS", {"K-1"})
    assert core.verify(licence(), NOW).reason == "key_revoked"


def test_test_root_only_for_test_licences(monkeypatch):
    test_root, test_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    c = cert(root=test_root, root_id="test", key=test_key, key_id="K-TEST")
    doc = licence(kind="test", certificate=c, key=test_key)
    assert core.verify(doc, NOW).reason == "unknown_root"
    monkeypatch.setenv("SARCADE_LICENCE_TEST_ROOT", issue.public_b64(test_root))
    s = core.verify(doc, NOW)
    assert s.status == "valid" and s.test
    assert core.verify(licence(kind="association", certificate=c, key=test_key), NOW).reason == "bad_kind"


def test_install_refuses_invalid_and_keeps_installed():
    good = licence()
    assert licensing.install(good, NOW).status == "valid"
    assert licensing.pro_enabled("siem") and licensing.pro_enabled("assist") and not licensing.pro_enabled("locate")
    bad = licence()
    bad["signature"] = good["signature"][:-4] + "AAA="
    assert licensing.install(bad, NOW).status == "invalid"
    assert json.loads(licensing.licence_path().read_text())["licence"]["id"] == "L-1"
    assert licensing.check(NOW).status == "valid"
    assert licensing.client_view() == {"modules": ["assist", "siem"], "status": "valid"}


def test_issuing_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SARCADE_LICENCE_PASSPHRASE", "phrase de test")
    assert issue.main(["init-root", "--id", "R-CLI", "--out", str(tmp_path / "root.key")]) == 0
    printed = capsys.readouterr().out
    public = printed.strip().splitlines()[-1].split('"')[3]
    monkeypatch.setitem(core.ROOT_KEYS, "R-CLI", public)
    assert (tmp_path / "root.key").stat().st_mode & 0o077 == 0
    assert b"ENCRYPTED" in (tmp_path / "root.key").read_bytes()
    issue.main(["new-key", "--root", str(tmp_path / "root.key"), "--root-id", "R-CLI", "--id", "K-CLI",
                "--out", str(tmp_path / "key.key")])
    issue.main(["sign", "--key", str(tmp_path / "key.key"), "--organisation", "ADRASEC 78", "--kind", "association",
                "--modules", "siem,assist", "--out", str(tmp_path / "l.json")])
    assert issue.main(["verify", str(tmp_path / "l.json")]) == 0
    doc = json.loads((tmp_path / "l.json").read_text())
    assert doc["licence"]["price_eur"] == 0 and doc["licence"]["modules"] == ["assist", "siem"]
    # No key is ever overwritten.
    with pytest.raises(SystemExit):
        issue.main(["init-root", "--id", "R-CLI", "--out", str(tmp_path / "root.key")])


def test_locate_is_no_longer_a_pro_module():
    """Locate removed (ADR-006, 25 October 2026): an older licence naming it
    stays valid, the unknown module is simply not enabled."""
    old = licence(modules=("siem",))
    old["licence"]["modules"] = ["locate", "siem"]  # issued before the removal
    old["signature"] = issue.sign_b64(KEY, old["licence"])
    s = core.verify(old, NOW)
    assert s.status == "valid" and s.modules == {"siem"}
