"""Generates an ephemeral TEST licence chain for the demonstration and the CI:
a test root, a licence key and a « test » licence enabling the « siem »
module. Only the server started with SARCADE_LICENCE_TEST_ROOT set to the
printed public key accepts it, and always shows it as a test licence.

The private keys stay in the output folder (ignored by git) and are thrown
away with it: they are never real SARCADE keys.

Usage: python demo/make_test_licence.py demo/licence-test
       -> prints the public test root (for SARCADE_LICENCE_TEST_ROOT)
"""
import base64
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def pub(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                         serialization.PublicFormat.Raw)).decode()


def sign(key, payload):
    return base64.b64encode(key.sign(canonical(payload))).decode()


out = Path(sys.argv[1] if len(sys.argv) > 1 else "licence-test")
out.mkdir(parents=True, exist_ok=True)
root, key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
today = datetime.now(UTC).date()
cert = {"id": "K-TEST", "root": "test", "public_key": pub(key),
        "not_before": (today - timedelta(days=1)).isoformat(), "not_after": (today + timedelta(days=120)).isoformat()}
lic = {"id": "L-TEST-DEMO", "organisation": "ADRASEC Démo", "kind": "test", "modules": ["siem"],
       "issued_at": today.isoformat(), "expires_at": (today + timedelta(days=90)).isoformat(), "price_eur": 0}
doc = {"format": "sarcade-licence/1", "licence": lic, "signature": sign(key, lic),
       "key": {**cert, "signature": sign(root, cert)}}
(out / "licence.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
# Licence of an organisation (lot 26): modules ADRASEC and Race, used by
# demo/verify_directory.py for the organisation it creates.
org = {"id": "L-TEST-ORG", "organisation": "Organisation de démonstration", "kind": "test",
       "modules": ["adrasec", "race"], "issued_at": today.isoformat(),
       "expires_at": (today + timedelta(days=90)).isoformat(), "price_eur": 0}
org_doc = {"format": "sarcade-licence/1", "licence": org, "signature": sign(key, org),
           "key": {**cert, "signature": sign(root, cert)}}
(out / "licence-org.json").write_text(json.dumps(org_doc, ensure_ascii=False, indent=2), encoding="utf-8")
(out / "root.pub").write_text(pub(root) + "\n", encoding="utf-8")
print(pub(root))
