"""Token issuance, hashing, verification, and revocation.

Uses standard-library hashlib.scrypt with a salt prefix so stored hashes
are self-describing. Plaintext tokens are 32 random bytes, URL-safe b64
encoded, and prefixed ``saopt_``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import base64

TOKEN_PREFIX = "saopt_"
TOKEN_BYTES = 32
LOOKUP_PREFIX_LEN = 8  # first 8 chars after prefix
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


@dataclass
class IssuedToken:
    """Returned once at issuance. The plaintext is never stored."""

    plaintext: str
    encoded_hash: str
    lookup_prefix: str


def _b64(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_token() -> IssuedToken:
    """Generate a new 32-byte URL-safe token with ``saopt_`` prefix."""
    raw = secrets.token_bytes(TOKEN_BYTES)
    plaintext = TOKEN_PREFIX + _b64(raw)
    encoded = hash_token(plaintext)
    return IssuedToken(
        plaintext=plaintext,
        encoded_hash=encoded,
        lookup_prefix=plaintext[len(TOKEN_PREFIX):len(TOKEN_PREFIX) + LOOKUP_PREFIX_LEN],
    )


def hash_token(token: str, *, salt: bytes | None = None) -> str:
    """Produce a self-describing scrypt hash of *token*.

    Format: ``scrypt$16384$8$1${salt_hex}${digest_hex}``
    """
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        token.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_token(token: str, encoded: str) -> bool:
    """Return True if *token* matches the stored *encoded* hash."""
    try:
        parts = encoded.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        scheme, n, r, p, salt_hex, expected_hex = parts
        expected = bytes.fromhex(expected_hex)
        actual = hashlib.scrypt(
            token.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return scheme == "scrypt" and hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
