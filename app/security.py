"""SIN encryption at rest.

Fernet (authenticated AES) protects the readable SIN in the database. Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and put it in SIN_ENCRYPTION_KEY. With no key set, values pass through as plaintext so the app
still runs - but the SIN is then NOT protected, so set the key in any real deployment.

Lookup for returning customers decrypts-and-compares (see submission.prefill_existing) rather
than storing a hash of the SIN - the plaintext never sits in the database to be indexed on.
"""
from functools import lru_cache

from .config import settings

_PREFIX = "enc:"          # marks an encrypted value so reveal_sin knows to decrypt


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(settings.sin_encryption_key.encode())


def protect_sin(sin: str | None) -> str | None:
    """Value to STORE for a SIN - ciphertext if a key is set, else plaintext (with a warning)."""
    if not sin:
        return sin
    if not settings.sin_encryption_key:
        print("[security] SIN_ENCRYPTION_KEY not set - storing SIN in plaintext.")
        return sin
    return _PREFIX + _fernet().encrypt(sin.encode()).decode()


def reveal_sin(stored: str | None) -> str | None:
    """Decrypt a stored SIN for display. Plaintext / empty values pass through unchanged."""
    if not stored or not str(stored).startswith(_PREFIX):
        return stored
    try:
        return _fernet().decrypt(str(stored)[len(_PREFIX):].encode()).decode()
    except Exception as e:
        print(f"[security] SIN decrypt failed: {e}")
        return "[unreadable]"


def digits(sin: str | None) -> str:
    return "".join(c for c in (sin or "") if c.isdigit())


# ---- Admin password hashing (stdlib scrypt; no extra dependency) ----
# Stored as "scrypt$<salt hex>$<hash hex>" so the format is self-describing.

def hash_password(password: str) -> str:
    import hashlib, secrets
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    import hashlib, hmac
    try:
        scheme, salt_hex, dk_hex = (stored or "").split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk.hex(), dk_hex)
