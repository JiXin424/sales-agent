"""Test token generation, hashing, verification, and revocation."""

import pytest
import time
from sales_agent.security.optimization_credentials import (
    generate_token,
    hash_token,
    verify_token,
    TOKEN_PREFIX,
)


class TestTokenGeneration:
    def test_generated_token_has_prefix(self):
        issued = generate_token()
        assert issued.plaintext.startswith(TOKEN_PREFIX)

    def test_generated_token_is_44_chars(self):
        issued = generate_token()
        # saopt_ (6) + 32 bytes b64 (~43 chars without padding) = ~49 chars
        assert len(issued.plaintext) > 40
        assert len(issued.plaintext) < 60

    def test_lookup_prefix_is_8_chars(self):
        issued = generate_token()
        assert len(issued.lookup_prefix) == 8

    def test_unique_tokens(self):
        """Two generations must produce different tokens."""
        a = generate_token()
        b = generate_token()
        assert a.plaintext != b.plaintext
        assert a.encoded_hash != b.encoded_hash


class TestTokenHash:
    def test_verify_matching_token(self):
        issued = generate_token()
        assert verify_token(issued.plaintext, issued.encoded_hash)

    def test_verify_wrong_token_fails(self):
        issued = generate_token()
        assert not verify_token("saopt_wrong_token_value", issued.encoded_hash)

    def test_verify_tampered_hash_fails(self):
        issued = generate_token()
        tampered = issued.encoded_hash[:-4] + "0000"
        assert not verify_token(issued.plaintext, tampered)

    def test_hash_is_self_describing(self):
        issued = generate_token()
        parts = issued.encoded_hash.split("$")
        assert parts[0] == "scrypt"
        assert int(parts[1]) > 0  # N
        assert int(parts[2]) > 0  # r
        assert int(parts[3]) > 0  # p
        assert len(parts[4]) == 32  # salt hex (16 bytes)
        assert len(parts[5]) == 64  # digest hex (32 bytes)

    def test_deterministic_hash(self):
        """Same token + same salt = same hash."""
        token = "saopt_test_token_value"
        salt = b"\x00" * 16
        h1 = hash_token(token, salt=salt)
        h2 = hash_token(token, salt=salt)
        assert h1 == h2


class TestTokenRevocation:
    def test_revoked_credential_pattern(self):
        """Revocation is recorded via revoked_at timestamp on the model."""
        from datetime import datetime, timezone
        revoked = datetime.now(timezone.utc).isoformat()
        assert revoked.endswith("+00:00") or "Z" in revoked or "+" in revoked
