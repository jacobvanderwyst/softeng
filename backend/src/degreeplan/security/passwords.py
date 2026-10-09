"""Password hashing (Argon2id), legacy scrypt verification + upgrade, and the password policy."""
from __future__ import annotations

import hashlib
import hmac
from functools import cached_property

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..config import Settings

MAX_PASSWORD_LENGTH = 256

# A small denylist of very common passwords (NIST SP 800-63B: reject known-bad choices).
_COMMON = frozenset(
    """password password1 password12 password123 passw0rd123 letmein123 welcome123 qwerty123456
    123456789012 111111111111 abcdefghijkl iloveyou1234 administrator changeme1234 trustno1trustno1
    qwertyuiop12 1q2w3e4r5t6y universityof student12345 teacher12345 admin1234567 monkey123456""".split()
)


class PasswordPolicyError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def check_policy(password: str, *, username: str | None = None, min_length: int = 12) -> list[str]:
    problems: list[str] = []
    if len(password) < min_length:
        problems.append(f"Password must be at least {min_length} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON:
        problems.append("Password is too common.")
    if username and len(username) >= 3 and username.lower() in lowered:
        problems.append("Password must not contain the username.")
    if len(set(password)) < 5:
        problems.append("Password is too repetitive.")
    return problems


def _verify_legacy_scrypt(stored: str, password: str) -> bool:
    """Verify a werkzeug-format hash: scrypt:<n>:<r>:<p>$<salt>$<hexdigest>."""
    try:
        method, salt, expected = stored.split("$", 2)
        _, n_raw, r_raw, p_raw = method.split(":")
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if not (2 <= n <= 2**17) or r < 1 or p < 1:  # refuse absurd cost parameters
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=n, r=r, p=p, maxmem=132 * n * r * p
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class PasswordService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )

    @cached_property
    def _dummy_hash(self) -> str:
        return self._hasher.hash("timing-equalisation-only")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def validate_new(self, password: str, username: str | None = None) -> None:
        problems = check_policy(password, username=username, min_length=self._settings.password_min_length)
        if problems:
            raise PasswordPolicyError(problems)

    def verify(self, stored: str | None, password: str) -> tuple[bool, bool]:
        """Return (password_ok, needs_rehash). Takes comparable time for unknown users."""
        if len(password) > MAX_PASSWORD_LENGTH:
            return False, False
        if stored is None:
            self._burn(password)
            return False, False
        if stored.startswith("$argon2"):
            try:
                self._hasher.verify(stored, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False, False
            return True, self._hasher.check_needs_rehash(stored)
        if stored.startswith("scrypt:"):
            ok = _verify_legacy_scrypt(stored, password)
            return ok, ok  # upgrade legacy hashes to Argon2id on next successful login
        self._burn(password)
        return False, False

    def _burn(self, password: str) -> None:
        try:
            self._hasher.verify(self._dummy_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
