"""Password hashing (PBKDF2-HMAC-SHA256) and opaque session tokens.

Raw token is never stored: only its SHA-256 digest, so a leaked DB cannot be
replayed. Tokens are issued/validated by the backend, not decoded (no JWT).
"""
import datetime as dt
import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def hash_password(password, *, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return salt, digest


def verify_password(password, salt, digest, iterations=ITERATIONS):
    if not salt or not digest:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, digest)


def new_token():
    """(raw opaque token, its stored digest). Token = 256 bits of entropy."""
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)


def token_digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def now_utc():
    return dt.datetime.now(dt.timezone.utc)