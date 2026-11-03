"""Signed SARCADE Pro licences, verified offline (note 11 « Architecture
modulaire et SARCADE Pro », decisions of 10 Oct 2026).

Chain of trust, all Ed25519:

    root key (offline, kept by the project holder)
      └── signs a licence key certificate (id, public key, validity)
            └── the licence key signs the licences

A licence is a JSON file::

    {"format": "sarcade-licence/1",
     "licence": {"id", "organisation", "kind", "modules", "issued_at",
                 "expires_at", "price_eur"},
     "signature": "<base64, licence key over the canonical licence>",
     "key": {"id", "root", "public_key", "not_before", "not_after",
             "signature": "<base64, root key over the canonical certificate>"}}

Rules: verified at start and every day, without network; after the expiry a
grace of 30 days, then only the Pro modules stop (Core never depends on a
licence); one licence per organisation, any number of servers; revocation
list shipped with the updates. The free association licence uses the same
mechanism with ``kind: association`` and a price of 0.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import json
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .revoked import REVOKED_KEYS, REVOKED_LICENCES
from .roots import ROOT_KEYS

FORMAT = "sarcade-licence/1"
# Locate was removed on 25 October 2026 (ADR-006, amendment): beacon search,
# SDR and 406 MHz are Core. An older licence naming it simply ignores it.
# Modules of the organisations (lot 26): « race » and « adrasec » (free for the
# ADRASEC, association licence) join the Pro modules.
MODULES = ("siem", "assist", "race", "adrasec")
KINDS = ("standard", "association", "test")
GRACE_DAYS = 30
WARN_DAYS = 30


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode(), validate=True)


def _verify(public_b64: str, signature_b64: str, payload: dict) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(b64decode(public_b64))
        key.verify(b64decode(signature_b64), canonical(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _day(value) -> date:
    return date.fromisoformat(str(value)[:10])


def test_root() -> str | None:
    """Root accepted for test licences only (CI, demonstration), given by
    ``SARCADE_LICENCE_TEST_ROOT``; such licences are always shown as test."""
    value = os.getenv("SARCADE_LICENCE_TEST_ROOT", "").strip()
    return value or None


@dataclass
class LicenceState:
    status: str  # none, valid, expiring, grace, expired, invalid
    modules: set[str] = field(default_factory=set)
    reason: str | None = None
    licence: dict | None = None
    key_id: str | None = None
    test: bool = False
    days_left: int | None = None
    checked_at: str | None = None

    def as_dict(self) -> dict:
        lic = self.licence or {}
        return {"status": self.status, "modules": sorted(self.modules), "reason": self.reason,
                "id": lic.get("id"), "organisation": lic.get("organisation"), "kind": lic.get("kind"),
                "issued_at": lic.get("issued_at"), "expires_at": lic.get("expires_at"),
                "licensed_modules": sorted(lic.get("modules") or []), "key_id": self.key_id,
                "test": self.test, "days_left": self.days_left, "grace_days": GRACE_DAYS,
                "checked_at": self.checked_at}


def verify(document: dict | None, now: datetime | None = None) -> LicenceState:
    now = now or datetime.now(UTC)
    checked = now.isoformat()
    if not document:
        return LicenceState("none", checked_at=checked)

    def invalid(reason: str, lic: dict | None = None) -> LicenceState:
        return LicenceState("invalid", reason=reason, licence=lic, checked_at=checked)

    if not isinstance(document, dict) or document.get("format") != FORMAT:
        return invalid("unknown_format")
    lic, key = document.get("licence"), document.get("key")
    if not isinstance(lic, dict) or not isinstance(key, dict):
        return invalid("unknown_format")
    try:
        cert = {k: key[k] for k in ("id", "root", "public_key", "not_before", "not_after")}
        issued, expires = _day(lic["issued_at"]), _day(lic["expires_at"])
        not_before, not_after = _day(cert["not_before"]), _day(cert["not_after"])
        modules = {str(m) for m in lic["modules"]}
        kind = str(lic["kind"])
        str(lic["id"]), str(lic["organisation"])
    except (KeyError, TypeError, ValueError):
        return invalid("missing_field", lic)

    # 1. Licence key certified by a root of SARCADE (or the test root).
    root_public = ROOT_KEYS.get(str(cert["root"]))
    is_test = False
    if root_public is None and test_root() and cert["root"] == "test":
        root_public, is_test = test_root(), True
    if root_public is None:
        return invalid("unknown_root", lic)
    if not _verify(root_public, str(key.get("signature", "")), cert):
        return invalid("bad_key_signature", lic)
    # 2. Licence signed by that key.
    if not _verify(str(cert["public_key"]), str(document.get("signature", "")), lic):
        return invalid("bad_signature", lic)
    # 3. Contents.
    if is_test != (kind == "test") or kind not in KINDS:
        return invalid("bad_kind", lic)
    if not (not_before <= issued <= not_after):
        return invalid("key_not_valid_at_issue", lic)
    if expires < issued:
        return invalid("bad_dates", lic)
    if cert["id"] in REVOKED_KEYS:
        return invalid("key_revoked", lic)
    if lic["id"] in REVOKED_LICENCES:
        return invalid("revoked", lic)

    known = modules & set(MODULES)
    today = now.date()
    days_left = (expires - today).days
    base = {"licence": lic, "key_id": str(cert["id"]), "test": is_test, "days_left": days_left,
            "checked_at": checked}
    if today < issued - timedelta(days=1):
        return LicenceState("invalid", reason="not_yet_valid", **base)
    if days_left >= 0:
        status = "expiring" if days_left <= WARN_DAYS else "valid"
        return LicenceState(status, modules=known, **base)
    if days_left >= -GRACE_DAYS:
        return LicenceState("grace", modules=known, reason="expired_in_grace", **base)
    return LicenceState("expired", reason="expired", **base)
