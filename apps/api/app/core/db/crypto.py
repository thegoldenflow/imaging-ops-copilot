"""Field-level encryption for PHI columns, plus blind indexes for lookups.

Values are encrypted with AES-256-GCM before they reach the database. The
ciphertext is bound to its table and column (associated data), so a value copied
into another column does not decrypt. Stored form: "<key id>:<base64(nonce + ciphertext)>";
the key id leaves room for key rotation.

Encrypted columns cannot be searched, so lookups use a blind index: an
HMAC-SHA256 of the normalized value under a second key, stored next to the
ciphertext. It reveals only whether two rows share a value.

Generate a key:  uv run python -m app.core.db.crypto
"""

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ID = "k1"


class MissingKeyError(RuntimeError):
    pass


def _key(name: str, min_bytes: int) -> bytes:
    raw = os.getenv(name)
    if not raw:
        raise MissingKeyError(
            f"{name} is not set. Generate one with `uv run python -m app.core.db.crypto` and put it in apps/api/.env")
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError as e:
        raise MissingKeyError(f"{name} must be base64") from e
    if len(key) < min_bytes:
        raise MissingKeyError(f"{name} must decode to at least {min_bytes} bytes")
    return key


class PhiCipher:
    def __init__(self, key: bytes, index_key: bytes) -> None:
        self._aead = AESGCM(key[:32])
        self._index_key = index_key

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = secrets.token_bytes(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return f"{KEY_ID}:{base64.b64encode(nonce + sealed).decode()}"

    def decrypt(self, token: str, context: str) -> str:
        key_id, _, body = token.partition(":")
        if key_id != KEY_ID:
            raise ValueError(f"Unknown PHI key id {key_id!r}")
        raw = base64.b64decode(body)
        return self._aead.decrypt(raw[:12], raw[12:], context.encode()).decode()

    def blind_index(self, value: str) -> str:
        return hmac.new(self._index_key, normalize(value).encode(), hashlib.sha256).hexdigest()


def normalize(value: str) -> str:
    return " ".join(value.lower().split())


_cipher: PhiCipher | None = None


def cipher() -> PhiCipher:
    global _cipher
    if _cipher is None:
        _cipher = PhiCipher(_key("PHI_ENCRYPTION_KEY", 32), _key("PHI_BLIND_INDEX_KEY", 32))
    return _cipher


if __name__ == "__main__":
    print("PHI_ENCRYPTION_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())
    print("PHI_BLIND_INDEX_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())
