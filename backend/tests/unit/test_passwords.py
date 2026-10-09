import hashlib
import secrets

import pytest

from degreeplan.config import Settings
from degreeplan.security.passwords import PasswordPolicyError, PasswordService, check_policy


@pytest.fixture(scope="module")
def service():
    return PasswordService(Settings.for_testing("sqlite:///a.db", "sqlite:///b.db"))


def test_argon2id_roundtrip(service):
    password = secrets.token_urlsafe(16)
    stored = service.hash(password)
    assert stored.startswith("$argon2id$")
    assert service.verify(stored, password) == (True, False)
    assert service.verify(stored, password + "x") == (False, False)


def test_hashes_are_salted(service):
    assert service.hash("same-password-twice") != service.hash("same-password-twice")


def test_unknown_user_and_garbage_hashes_never_verify(service):
    garbage = (None, "", "plaintext", "md5$abc$def", "scrypt:x:y:z$a$b", "scrypt:99999999:8:1$a$b", "$argon2id$broken")
    for stored in garbage:
        assert service.verify(stored, "anything-at-all")[0] is False


def test_oversized_password_is_rejected_without_hashing(service):
    assert service.verify(service.hash("short-enough-pw"), "x" * 5000) == (False, False)


def test_legacy_scrypt_verifies_and_requests_upgrade(service):
    password, salt, n = secrets.token_urlsafe(12), "pepperless01", 2**14
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=n, r=8, p=1, maxmem=132 * n * 8).hex()
    stored = f"scrypt:{n}:8:1${salt}${digest}"
    assert service.verify(stored, password) == (True, True)
    assert service.verify(stored, "wrong-password-xyz")[0] is False


def test_weaker_argon2_parameters_are_flagged_for_rehash():
    weak = PasswordService(Settings.for_testing("sqlite:///a.db", "sqlite:///b.db"))
    strong = PasswordService(
        Settings.for_testing("sqlite:///a.db", "sqlite:///b.db", argon2_memory_kib=128, argon2_time_cost=2)
    )
    stored = weak.hash("rehash-me-please-1")
    assert strong.verify(stored, "rehash-me-please-1") == (True, True)


@pytest.mark.parametrize(
    ("password", "username", "fragment"),
    [
        ("short", None, "at least 12"),
        ("password123", None, "at least 12"),
        ("administrator", None, "too common"),
        ("aaaaaaaaaaaaaaaa", None, "repetitive"),
        ("alice-is-my-name!", "alice", "username"),
        ("x" * 300, None, "at most"),
    ],
)
def test_policy_rejections(password, username, fragment):
    problems = check_policy(password, username=username)
    assert any(fragment in p for p in problems), problems


def test_policy_accepts_a_good_passphrase():
    assert check_policy("correct horse battery staple") == []


def test_validate_new_raises_with_all_problems(service):
    with pytest.raises(PasswordPolicyError) as exc:
        service.validate_new("alice", "alice")
    assert len(exc.value.problems) >= 2
