"""Logging: text or JSON, secret redaction, per-request IDs, separate audit stream."""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, g, has_request_context, request

from .config import Settings

AUDIT_LOGGER = "degreeplan.audit"
_TAG = "_degreeplan"

# key=value / "key": "value" pairs whose key looks sensitive, and credentials embedded in URLs.
_SENSITIVE_PAIR = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|authorization|api[_-]?key|cookie|set-cookie|csrf[_-]?token)"
    r"""(["']?\s*[:=]\s*["']?)([^\s,;"'}&]+)"""
)
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@\s]+)(@)", re.IGNORECASE)
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def redact(text: str) -> str:
    text = _AUTH_SCHEME.sub(r"\1 ***", text)
    text = _SENSITIVE_PAIR.sub(r"\1\2***", text)
    return _URL_CREDENTIALS.sub(r"\1***\3", text)


class _RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        return True


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": redact(record.getMessage()),
        }
        audit = getattr(record, "audit", None)
        if audit:
            payload["audit"] = audit
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def _formatter(settings: Settings) -> logging.Formatter:
    if settings.log_format == "json":
        return JsonFormatter()
    return RedactingTextFormatter("%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s")


def _tagged(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _TAG, True)
    return handler


def _remove_ours(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if getattr(h, _TAG, False):
            logger.removeHandler(h)
            h.close()


def configure_logging(app: Flask, settings: Settings) -> None:
    root = logging.getLogger()
    audit = logging.getLogger(AUDIT_LOGGER)
    _remove_ours(root)
    _remove_ours(audit)
    root.setLevel(settings.log_level)
    audit.setLevel(logging.INFO)
    audit.propagate = False  # audit records go to their own handlers only

    fmt, ctx = _formatter(settings), _RequestContextFilter()

    def attach(logger: logging.Logger, handler: logging.Handler) -> None:
        handler.setFormatter(fmt)
        handler.addFilter(ctx)
        logger.addHandler(_tagged(handler))

    console = logging.StreamHandler(sys.stderr)
    attach(root, console)
    attach(audit, logging.StreamHandler(sys.stderr))

    if settings.log_dir:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        for logger, name in ((root, "app.log"), (audit, "audit.log")):
            fh = RotatingFileHandler(
                settings.log_dir / name, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
            )
            attach(logger, fh)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    _register_request_logging(app)
    logging.getLogger(__name__).info("Logging configured (env=%s, format=%s)", settings.env_name, settings.log_format)


def _register_request_logging(app: Flask) -> None:
    log = logging.getLogger("degreeplan.request")

    @app.before_request
    def _start() -> None:
        incoming = request.headers.get("X-Request-ID", "")
        ok = 0 < len(incoming) <= 64 and incoming.isprintable() and re.fullmatch(r"[\w.-]+", incoming)
        g.request_id = incoming if ok else uuid.uuid4().hex[:12]
        g.start_time = time.perf_counter()

    @app.after_request
    def _finish(response):
        elapsed_ms = (time.perf_counter() - g.get("start_time", time.perf_counter())) * 1000
        level = logging.ERROR if response.status_code >= 500 else (
            logging.WARNING if response.status_code >= 400 else logging.INFO
        )
        auth = g.get("auth")  # only populated if the request needed authentication
        log.log(
            level,
            "%s %s -> %s (%.1f ms) user=%s ip=%s",
            request.method,
            request.path,  # never the query string or body
            response.status_code,
            elapsed_ms,
            auth.user_id if auth else "-",
            request.remote_addr,
        )
        response.headers["X-Request-ID"] = g.get("request_id", "-")
        return response
