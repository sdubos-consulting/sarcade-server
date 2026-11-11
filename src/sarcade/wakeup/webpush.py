"""Web Push (RFC 8030) with VAPID (RFC 8292) and aes128gcm encryption
(RFC 8188 / RFC 8291), without third-party service account: the server
holds its own P-256 key pair."""
from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_private_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def load_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("not an EC key")
    return key


def public_key_b64(key: ec.EllipticCurvePrivateKey) -> str:
    """Application server key given to the browser (pushManager.subscribe)."""
    return b64u(key.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """aes128gcm body for one subscription (RFC 8291), one record."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_public = sender.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = sender.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + body


def vapid_header(endpoint: str, key: ec.EllipticCurvePrivateKey, subject: str, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": subject}
    token = jwt.encode(claims, key, algorithm="ES256")
    return f"vapid t={token}, k={public_key_b64(key)}"


def parse_subscription(token: str) -> dict:
    """Subscription sent by the browser (PushSubscription.toJSON())."""
    sub = json.loads(token)
    endpoint = sub["endpoint"]
    keys = sub["keys"]
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ValueError("invalid endpoint")
    unb64u(keys["p256dh"]); unb64u(keys["auth"])
    return {"endpoint": endpoint, "p256dh": keys["p256dh"], "auth": keys["auth"]}
