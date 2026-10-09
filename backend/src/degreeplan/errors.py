"""Application errors and centralized JSON error handling.

Every error response has the same shape:
    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]?}}
Unexpected errors are logged with a traceback server-side; clients only ever see a generic message.
"""
from __future__ import annotations

import logging

from flask import Flask, g, jsonify
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details
        self.headers = headers or {}


class BadRequestError(AppError):
    status_code, code, message = 400, "bad_request", "Bad request."


class AuthError(AppError):
    status_code, code, message = 401, "unauthorized", "Authentication required."


class ForbiddenError(AppError):
    status_code, code, message = 403, "forbidden", "You do not have permission to do that."


class NotFoundError(AppError):
    status_code, code, message = 404, "not_found", "Resource not found."


class ConflictError(AppError):
    status_code, code, message = 409, "conflict", "Request conflicts with existing data."


class UnsupportedMediaTypeError(AppError):
    status_code, code, message = 415, "unsupported_media_type", "Content-Type must be application/json."


class ValidationError(AppError):
    status_code, code, message = 422, "validation_error", "Request validation failed."


class TooManyRequestsError(AppError):
    status_code, code, message = 429, "too_many_requests", "Too many attempts. Try again later."


def _body(code: str, message: str, details: list | None = None) -> dict:
    err: dict = {"code": code, "message": message, "request_id": g.get("request_id")}
    if details:
        err["details"] = details
    return {"error": err}


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        log.warning("%s: %s", exc.code, exc.message)  # expected client error: no traceback
        return jsonify(_body(exc.code, exc.message, exc.details)), exc.status_code, exc.headers

    @app.errorhandler(PydanticValidationError)
    def handle_pydantic(exc: PydanticValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        log.warning("validation_error: %s", details)
        return jsonify(_body("validation_error", "Request validation failed.", details)), 422

    @app.errorhandler(IntegrityError)
    def handle_integrity(exc: IntegrityError):
        log.warning("Integrity error: %s", exc.orig)
        return jsonify(_body("conflict", "Request conflicts with existing data.")), 409

    @app.errorhandler(OperationalError)
    def handle_operational(exc: OperationalError):
        log.exception("Database unavailable or busy")
        return jsonify(_body("service_unavailable", "The service is temporarily unavailable.")), 503

    @app.errorhandler(SQLAlchemyError)
    def handle_db_error(exc: SQLAlchemyError):
        log.exception("Database error")
        return jsonify(_body("database_error", "A database error occurred.")), 500

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        code = (exc.name or "http_error").lower().replace(" ", "_")
        level = logging.ERROR if (exc.code or 500) >= 500 else logging.WARNING
        log.log(level, "HTTP %s: %s", exc.code, exc.description)
        headers = dict(exc.get_headers()) if hasattr(exc, "get_headers") else {}
        headers.pop("Content-Type", None)
        headers.pop("Content-Length", None)
        return jsonify(_body(code, exc.description or exc.name)), exc.code or 500, headers

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        log.exception("Unhandled exception")
        return jsonify(_body("internal_error", "An unexpected error occurred.")), 500
