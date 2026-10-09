import json
import logging
import sys

import pytest

from degreeplan.logging_setup import JsonFormatter, RedactingTextFormatter, redact


@pytest.mark.parametrize(
    "text",
    [
        "login password=hunter2hunter2 failed",
        'payload {"password": "hunter2hunter2"}',
        "Authorization: Bearer abc.def.ghi",
        "cookie=__Host-sid=abcdef0123456789",
        "csrf_token=abc123 other=fine",
        "api_key: sk_live_123456",
    ],
)
def test_sensitive_pairs_are_redacted(text):
    out = redact(text)
    for secret in ("hunter2hunter2", "abc.def.ghi", "abcdef0123456789", "abc123", "sk_live_123456"):
        assert secret not in out
    assert "***" in out


def test_credentials_in_urls_are_redacted():
    out = redact("connecting to postgresql://app:s3cretpw@db.internal:5432/users")
    assert "s3cretpw" not in out
    assert "postgresql://app:***@db.internal" in out


def test_harmless_text_is_untouched():
    assert redact("GET /api/v1/courses -> 200") == "GET /api/v1/courses -> 200"


def _record(msg, *args, exc_info=None):
    return logging.LogRecord("t", logging.WARNING, __file__, 1, msg, args, exc_info)


def test_text_formatter_redacts():
    fmt = RedactingTextFormatter("%(levelname)s %(message)s")
    assert "hunter2" not in fmt.format(_record("bad login password=%s", "hunter2"))


def test_json_formatter_is_valid_json_and_redacts_message_and_traceback():
    try:
        raise RuntimeError("db failed token=topsecrettoken")
    except RuntimeError:
        record = _record("request failed password=%s", "hunter2", exc_info=sys.exc_info())
    record.request_id = "abc123"
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["request_id"] == "abc123" and payload["level"] == "WARNING"
    assert "hunter2" not in line and "topsecrettoken" not in line


def test_audit_payload_is_included_in_json():
    record = _record("auth.login success")
    record.audit = {"action": "auth.login", "outcome": "success"}
    assert json.loads(JsonFormatter().format(record))["audit"]["action"] == "auth.login"
