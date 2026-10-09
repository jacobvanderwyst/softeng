"""Request-level protections: Origin + CSRF checks, security headers, CORS, rate limiting."""
from __future__ import annotations

import hmac
import logging

from flask import Flask, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ..config import Settings
from ..errors import ForbiddenError
from .auth import current_auth

log = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"
# Login has no session yet; it is protected by the Origin check, SameSite cookies, lockout and rate limits.
CSRF_EXEMPT_ENDPOINTS = {"v1.auth.login"}


def _allowed_origins(settings: Settings) -> set[str]:
    return {request.host_url.rstrip("/"), *settings.cors_origins}


def _check_request(settings: Settings) -> None:
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("Origin")
    if origin and origin not in _allowed_origins(settings):
        log.warning("Rejected cross-origin state-changing request from %r", origin[:100])
        raise ForbiddenError("Origin not allowed.")
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    auth = current_auth()
    if auth is None:
        return  # not logged in: the route's own authentication check answers 401
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent.encode(), auth.csrf_token.encode()):
        log.warning("CSRF check failed for %s %s", request.method, request.path)
        raise ForbiddenError("Missing or invalid CSRF token.")


def init_app(app: Flask, settings: Settings) -> None:
    limiter.init_app(app)

    if settings.cors_origins:
        CORS(
            app,
            resources={r"/api/*": {"origins": list(settings.cors_origins)}},
            supports_credentials=True,
            allow_headers=["Content-Type", CSRF_HEADER, "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    app.before_request(lambda: _check_request(settings))

    @app.after_request
    def _security_headers(response):
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        h.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
