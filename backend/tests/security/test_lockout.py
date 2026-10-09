from degreeplan import clock
from tests.helpers import PASSWORDS, ApiClient


def at(monkeypatch, offset: int):
    base = clock.now()
    monkeypatch.setattr(clock, "now", lambda: base + offset)


def test_account_locks_after_repeated_failures_even_for_the_right_password(make_app):
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    assert [client.login("alice", "wrong-guess-1").status_code for _ in range(3)] == [401, 401, 401]

    locked = client.login("alice", PASSWORDS["alice"])
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.get_json()["error"]["code"] == "too_many_requests"


def test_lockout_applies_identically_to_unknown_usernames(make_app):
    """Responses must not reveal whether an account exists."""
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    for _ in range(3):
        client.login("no-such-user", "wrong-guess-1")
    assert client.login("no-such-user", "anything").status_code == 429


def test_lockout_expires_and_a_correct_password_works_again(make_app, monkeypatch):
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    for _ in range(3):
        client.login("alice", "wrong-guess-1")
    assert client.login("alice", PASSWORDS["alice"]).status_code == 429
    at(monkeypatch, 301)
    assert client.login("alice", PASSWORDS["alice"]).status_code == 200


def test_successful_login_resets_the_failure_counter(make_app):
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    client.login("alice", "wrong-guess-1")
    client.login("alice", "wrong-guess-2")
    assert client.login("alice", PASSWORDS["alice"]).status_code == 200  # counter reset
    assert [client.login("alice", "wrong-guess-3").status_code for _ in range(2)] == [401, 401]


def test_repeated_failures_back_off_exponentially(make_app, monkeypatch):
    app = make_app(lockout_threshold=3, lockout_seconds=60)
    client = ApiClient(app)
    for _ in range(3):
        client.login("alice", "wrong-guess-1")
    first_wait = int(client.login("alice", "x").headers["Retry-After"])
    at(monkeypatch, first_wait + 1)
    client.login("alice", "wrong-guess-again")  # fourth failure: lock doubles
    second_wait = int(client.login("alice", "x").headers["Retry-After"])
    assert second_wait > first_wait


def test_failures_in_different_accounts_do_not_lock_each_other(make_app):
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    for _ in range(3):
        client.login("alice", "wrong-guess-1")
    assert ApiClient(app).login("bob", PASSWORDS["bob"]).status_code == 200
