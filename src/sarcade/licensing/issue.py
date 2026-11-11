"""Offline issuing tool of SARCADE Pro licences.

To run on a machine of the project holder, ideally disconnected; the
private keys are written encrypted with a passphrase (asked, or read from
``SARCADE_LICENCE_PASSPHRASE``) and never enter the repository nor a
conversation.

    python -m sarcade.licensing.issue init-root --id R-2026 --out root.key
    python -m sarcade.licensing.issue new-key --root root.key --root-id R-2026 \\
        --id K-2026-1 --years 3 --out licence-key.key
    python -m sarcade.licensing.issue sign --key licence-key.key \\
        --organisation "ADRASEC 78" --kind association --modules siem,assist \\
        --out adrasec78.licence.json
    python -m sarcade.licensing.issue verify adrasec78.licence.json

``new-key`` also writes ``<out>.cert.json`` (public certificate of the
licence key), used by ``sign``. ``init-root`` prints the public root key to
add to ``roots.py``.
"""
from __future__ import annotations

import argparse
import base64
from datetime import UTC, date, datetime
import getpass
import json
import os
from pathlib import Path
import sys
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .core import FORMAT, KINDS, MODULES, canonical, verify


def _passphrase(confirm: bool) -> bytes | None:
    env = os.getenv("SARCADE_LICENCE_PASSPHRASE")
    if env is not None:
        return env.encode() or None
    first = getpass.getpass("Phrase de passe de la clé privée : ")
    if confirm and getpass.getpass("Confirmation : ") != first:
        sys.exit("Les phrases de passe diffèrent.")
    return first.encode() or None


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def write_private(key: Ed25519PrivateKey, path: Path, passphrase: bytes | None) -> None:
    enc = (serialization.BestAvailableEncryption(passphrase) if passphrase
           else serialization.NoEncryption())
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    if path.exists():
        sys.exit(f"{path} existe déjà : aucune clé n'est écrasée.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def read_private(path: Path, passphrase: bytes | None) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit(f"{path} n'est pas une clé Ed25519.")
    return key


def sign_b64(key: Ed25519PrivateKey, payload: dict) -> str:
    return base64.b64encode(key.sign(canonical(payload))).decode()


def make_key_certificate(root: Ed25519PrivateKey, root_id: str, key: Ed25519PrivateKey, key_id: str,
                         not_before: date, not_after: date) -> dict:
    cert = {"id": key_id, "root": root_id, "public_key": public_b64(key),
            "not_before": not_before.isoformat(), "not_after": not_after.isoformat()}
    return {**cert, "signature": sign_b64(root, cert)}


def make_licence(key: Ed25519PrivateKey, certificate: dict, organisation: str, kind: str, modules: list[str],
                 issued: date, expires: date, licence_id: str | None = None, price_eur: float = 0) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    unknown = set(modules) - set(MODULES)
    if unknown:
        raise ValueError(f"unknown modules: {sorted(unknown)}")
    lic = {"id": licence_id or f"L-{issued.year}-{uuid.uuid4().hex[:8]}", "organisation": organisation,
           "kind": kind, "modules": sorted(set(modules)), "issued_at": issued.isoformat(),
           "expires_at": expires.isoformat(), "price_eur": price_eur}
    return {"format": FORMAT, "licence": lic, "signature": sign_b64(key, lic), "key": certificate}


def _years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:  # 29 February
        return start.replace(year=start.year + years, day=28)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sarcade.licensing.issue", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("init-root", help="nouvelle clé racine (une fois, hors ligne)")
    r.add_argument("--id", required=True)
    r.add_argument("--out", required=True, type=Path)
    k = sub.add_parser("new-key", help="nouvelle clé de licence certifiée par la racine")
    k.add_argument("--root", required=True, type=Path)
    k.add_argument("--root-id", required=True)
    k.add_argument("--id", required=True)
    k.add_argument("--years", type=int, default=3)
    k.add_argument("--out", required=True, type=Path)
    s = sub.add_parser("sign", help="émettre une licence")
    s.add_argument("--key", required=True, type=Path)
    s.add_argument("--cert", type=Path, help="certificat de la clé (défaut : <key>.cert.json)")
    s.add_argument("--organisation", required=True)
    s.add_argument("--kind", choices=KINDS, default="standard")
    s.add_argument("--modules", default=",".join(MODULES))
    s.add_argument("--years", type=int, default=1)
    s.add_argument("--id")
    s.add_argument("--price-eur", type=float, default=0)
    s.add_argument("--out", required=True, type=Path)
    v = sub.add_parser("verify", help="vérifier une licence")
    v.add_argument("file", type=Path)
    a = ap.parse_args(argv)
    today = datetime.now(UTC).date()

    if a.cmd == "init-root":
        key = Ed25519PrivateKey.generate()
        write_private(key, a.out, _passphrase(True))
        print(f"Clé racine {a.id} écrite dans {a.out} (à garder hors ligne, sauvegardée).")
        print("Clé publique à ajouter dans src/sarcade/licensing/roots.py :")
        print(f'    "{a.id}": "{public_b64(key)}",')
    elif a.cmd == "new-key":
        root = read_private(a.root, _passphrase(False))
        key = Ed25519PrivateKey.generate()
        cert = make_key_certificate(root, a.root_id, key, a.id, today, _years(today, a.years))
        write_private(key, a.out, _passphrase(True))
        cert_path = a.out.with_name(a.out.name + ".cert.json")
        cert_path.write_text(json.dumps(cert, indent=2), encoding="utf-8")
        print(f"Clé de licence {a.id} : {a.out}, certificat {cert_path} (valable jusqu'au {cert['not_after']}).")
    elif a.cmd == "sign":
        key = read_private(a.key, _passphrase(False))
        cert_path = a.cert or a.key.with_name(a.key.name + ".cert.json")
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        modules = [m.strip() for m in a.modules.split(",") if m.strip()]
        doc = make_licence(key, cert, a.organisation, a.kind, modules, today, _years(today, a.years),
                           a.id, a.price_eur)
        if a.out.exists():
            sys.exit(f"{a.out} existe déjà.")
        a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Licence {doc['licence']['id']} pour {a.organisation} jusqu'au {doc['licence']['expires_at']} : {a.out}")
    elif a.cmd == "verify":
        state = verify(json.loads(a.file.read_text(encoding="utf-8")))
        print(json.dumps(state.as_dict(), ensure_ascii=False, indent=2))
        return 0 if state.status in ("valid", "expiring", "grace") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
