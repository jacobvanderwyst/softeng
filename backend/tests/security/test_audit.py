import json
import logging

from degreeplan.logging_setup import AUDIT_LOGGER
from degreeplan.repositories import security_store
from tests.helpers import PASSWORDS, ApiClient
from tests.integration.test_plans import add, make_plan


def audit_rows(app):
    with app.extensions["degreeplan"].databases.users.connect() as conn:
        return list(reversed(security_store.recent_audit(conn, 100)))  # oldest first


def actions(app):
    return [(r["action"], r["outcome"]) for r in audit_rows(app)]


def test_login_success_and_failure_are_audited(app, api):
    api.login("alice", "wrong-password-guess")
    api.login("alice", PASSWORDS["alice"])
    assert actions(app) == [("auth.login", "failure"), ("auth.login", "success")]
    success = audit_rows(app)[1]
    assert success["actor_role"] == "student" and success["target_type"] == "user" and success["ip"]
    assert success["request_id"]


def test_failed_login_detail_has_username_but_never_the_password(app, api):
    api.login("alice", "SuperSecretGuess-123")
    failure = audit_rows(app)[0]
    detail = json.loads(failure["detail"])
    assert detail["username"] == "alice" and detail["consecutive_failures"] == 1
    assert "SuperSecretGuess-123" not in json.dumps(failure)


def test_lockout_is_audited(make_app):
    app = make_app(lockout_threshold=3, lockout_seconds=300)
    client = ApiClient(app)
    for _ in range(3):
        client.login("alice", "wrong-guess")
    client.login("alice", "wrong-guess")
    assert ("auth.login", "locked") in actions(app)


def test_logout_and_password_change_are_audited(app, alice):
    alice.post("/api/v1/auth/change-password",
               json={"current_password": PASSWORDS["alice"], "new_password": "another-long-passphrase-9"})
    alice.post("/api/v1/auth/logout")
    names = [a for a, _ in actions(app)]
    assert "auth.change_password" in names and names[-1] == "auth.logout"


def test_plan_changes_are_audited_with_actor_and_target(app, alice):
    pid = make_plan(alice)
    add(alice, pid, 1, 1)
    alice.patch(f"/api/v1/plans/{pid}", json={"name": "renamed"})
    alice.delete(f"/api/v1/plans/{pid}")
    plan_events = [(r["action"], r["target_id"], r["actor_role"]) for r in audit_rows(app)
                   if r["action"].startswith("plan.")]
    assert plan_events == [
        ("plan.create", str(pid), "student"),
        ("plan.course.set", str(pid), "student"),
        ("plan.update", str(pid), "student"),
        ("plan.delete", str(pid), "student"),
    ]


def test_audit_events_are_also_written_to_the_audit_log_stream(app, api, audit_caplog):
    api.login("alice", PASSWORDS["alice"])
    records = [r for r in audit_caplog.records if r.name == AUDIT_LOGGER]
    assert records and records[0].audit["action"] == "auth.login"
    assert records[0].audit["outcome"] == "success"
    assert records[0].levelno == logging.INFO
