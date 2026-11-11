"""Public root keys of SARCADE Pro licences (Ed25519, base64, raw 32 bytes).

The root key pair is generated offline by the project holder with
``python -m sarcade.licensing.issue init-root``; only the PUBLIC key is
added here, by a pull request. The private key never enters the repository
nor a conversation. Several roots may coexist during a rotation.
"""

ROOT_KEYS: dict[str, str] = {
    # "R-2026": "<public key printed by init-root>",
}
