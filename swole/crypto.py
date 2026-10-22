"""Passphrase encryption for everything committed to the repo.

Format (JSON): {"v":1, "kdf":"PBKDF2-SHA256", "iter":N, "salt":b64, "iv":b64, "ct":b64}
Plaintext is gzipped JSON; ct is AES-256-GCM output with the 16-byte tag appended,
which is exactly what WebCrypto's AES-GCM decrypt expects, so site/index.html can
decrypt the same file in the browser.
"""

import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000


def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(obj, passphrase: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    plain = gzip.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), mtime=0)
    ct = AESGCM(_key(passphrase, salt, ITERATIONS)).encrypt(iv, plain, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt(envelope: dict, passphrase: str):
    salt = base64.b64decode(envelope["salt"])
    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ct"])
    plain = AESGCM(_key(passphrase, salt, envelope["iter"])).decrypt(iv, ct, None)
    return json.loads(gzip.decompress(plain))


def read_file(path: str, passphrase: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return decrypt(json.load(f), passphrase)


def write_file(path: str, obj, passphrase: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(encrypt(obj, passphrase), f)
    os.replace(tmp, path)
