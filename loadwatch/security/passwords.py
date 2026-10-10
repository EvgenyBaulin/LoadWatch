"""Password hashing with Argon2id."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Library defaults (RFC 9106 low-memory profile, 64 MiB) are too heavy for a 1 GB host
# under concurrent logins; 19 MiB / t=2 follows the OWASP minimum recommendation.
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

# Verified against when the user does not exist, so timing does not reveal accounts.
_DUMMY_HASH = _hasher.hash("loadwatch-dummy-password")


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password``."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Check ``password`` against ``password_hash``; ``None`` burns equal time and fails."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
