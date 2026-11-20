from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from .. import audit, clock, context
from ..db import plans_conn, users_conn
from ..errors import AuthError, ConflictError, ForbiddenError, TooManyRequestsError, ValidationError
from ..repositories import catalog, people
from ..repositories import users as users_repo
from ..security import lockout
from ..security.auth import current_auth, login_required, require_auth
from ..security.passwords import PasswordPolicyError
from ..security.protection import limiter
from ..validation import ChangePasswordIn, LoginIn, RegisterIn, parse_body

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__, url_prefix="/auth")

GENERIC_LOGIN_ERROR = "Invalid username or password."


def _profile(user_id: int, role: str) -> dict[str, Any] | None:
    conn = users_conn()
    if role == "student":
        s = people.student_by_user_id(conn, user_id)
        return None if s is None else {
            "id": s["id"], "first_name": s["first_name"], "last_name": s["last_name"],
            "program_id": s["program_id"],
        }
    if role == "teacher":
        t = people.teacher_by_user_id(conn, user_id)
        return None if t is None else {
            "id": t["id"], "first_name": t["first_name"], "last_name": t["last_name"],
            "department": t["department"],
        }
    return None


def _user_payload(user_id: int, username: str, role: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"username": username, "role": role}
    profile = _profile(user_id, role)
    if profile:
        payload["profile"] = profile
    return payload


def _set_session_cookie(response, token: str) -> None:
    s = context.settings()
    response.set_cookie(
        s.cookie_name, token, httponly=True, secure=s.cookie_secure, samesite="Lax", path="/"
    )


def _clear_session_cookie(response) -> None:
    s = context.settings()
    response.delete_cookie(s.cookie_name, httponly=True, secure=s.cookie_secure, samesite="Lax", path="/")


@bp.get("/registration-options")
@limiter.limit("30 per minute")
def registration_options():
    settings = context.settings()
    if not settings.registration_enabled:
        return jsonify({"enabled": False})
    programs = catalog.list_programs(plans_conn())
    return jsonify({
        "enabled": True,
        "password_min_length": settings.password_min_length,
        "programs": [{"id": program["id"], "code": program["code"], "name": program["name"]}
                     for program in programs],
    })


@bp.post("/register")
@limiter.limit(lambda: context.settings().registration_rate_limit)
def register():
    svc = context.services()
    if not svc.settings.registration_enabled:
        raise ForbiddenError("Self-registration is disabled. Contact your administrator.")
    if current_auth() is not None:
        raise ConflictError("Sign out before registering another account.")
    body = parse_body(RegisterIn)
    username = users_repo.normalize_username(body.username)
    try:
        svc.passwords.validate_new(body.password, username)
    except PasswordPolicyError as exc:
        raise ValidationError(
            "Password does not meet the policy.",
            details=[{"field": "password", "message": problem} for problem in exc.problems],
        ) from None
    if body.program_id is not None and not catalog.program_exists(plans_conn(), body.program_id):
        raise ValidationError(
            "Unknown degree program.", details=[{"field": "program_id", "message": "Choose a valid program."}],
        )
    # All mutable records are in one database: account, student profile and audit commit together.
    conn = users_conn()
    try:
        user_id = users_repo.create(
            conn, username=username, password_hash=svc.passwords.hash(body.password),
            role="student", now=clock.now(),
        )
        people.create_student(
            conn, user_id=user_id, first_name=body.first_name, last_name=body.last_name,
            program_id=body.program_id, advisor_id=None,
        )
        audit.record(conn, "auth.register", actor_user_id=user_id, actor_role="student",
                     target_type="user", target_id=user_id)
        conn.commit()
    except IntegrityError:
        conn.rollback()
        raise ConflictError("This username cannot be registered. Choose another username or sign in.") from None
    return jsonify({"ok": True}), 201


@bp.post("/login")
@limiter.limit(lambda: context.settings().login_rate_limit)
def login():
    svc = context.services()
    body = parse_body(LoginIn)
    conn = users_conn()
    key = lockout.lockout_key(body.username)

    wait = lockout.seconds_locked(conn, key)
    if wait:
        audit.record(conn, "auth.login", outcome="locked", detail={"username": key})
        conn.commit()
        raise TooManyRequestsError(headers={"Retry-After": str(wait)})

    user = users_repo.get_by_username(conn, body.username)
    ok, needs_rehash = svc.passwords.verify(user["password_hash"] if user else None, body.password)
    if not (ok and user and user["is_active"]):
        count = lockout.record_failure(conn, svc.settings, key)
        audit.record(conn, "auth.login", outcome="failure", detail={"username": key, "consecutive_failures": count})
        conn.commit()
        raise AuthError(GENERIC_LOGIN_ERROR)

    lockout.clear(conn, key)
    if needs_rehash:
        users_repo.set_password_hash(conn, user["id"], svc.passwords.hash(body.password))
    token, csrf = svc.sessions.create(conn, user["id"], request.remote_addr, request.headers.get("User-Agent"))
    audit.record(conn, "auth.login", actor_user_id=user["id"], actor_role=user["role"], target_type="user",
                 target_id=user["id"])
    conn.commit()

    response = jsonify({"user": _user_payload(user["id"], user["username"], user["role"]), "csrf_token": csrf})
    _set_session_cookie(response, token)
    return response


@bp.post("/logout")
@login_required
def logout():
    auth = require_auth()
    conn = users_conn()
    context.services().sessions.revoke(conn, auth.session_id)
    audit.record(conn, "auth.logout", target_type="user", target_id=auth.user_id)
    conn.commit()
    response = jsonify({"ok": True})
    _clear_session_cookie(response)
    return response


@bp.get("/me")
@login_required
def me():
    auth = require_auth()
    return jsonify({"user": _user_payload(auth.user_id, auth.username, auth.role), "csrf_token": auth.csrf_token})


@bp.post("/change-password")
@login_required
@limiter.limit("5 per minute")
def change_password():
    svc = context.services()
    auth = require_auth()
    body = parse_body(ChangePasswordIn)
    conn = users_conn()

    user = users_repo.get_by_username(conn, auth.username)
    ok, _ = svc.passwords.verify(user["password_hash"] if user else None, body.current_password)
    if not ok or not user:
        audit.record(conn, "auth.change_password", outcome="failure", target_type="user", target_id=auth.user_id)
        conn.commit()
        raise AuthError("Current password is incorrect.")
    try:
        svc.passwords.validate_new(body.new_password, auth.username)
    except PasswordPolicyError as exc:
        raise ValidationError("New password does not meet the policy.",
                              details=[{"field": "new_password", "message": p} for p in exc.problems]) from None

    users_repo.set_password_hash(conn, user["id"], svc.passwords.hash(body.new_password), clock.now())
    revoked = svc.sessions.revoke_all(conn, user["id"], except_session_id=auth.session_id)
    audit.record(conn, "auth.change_password", target_type="user", target_id=user["id"],
                 detail={"other_sessions_revoked": revoked})
    conn.commit()
    return jsonify({"ok": True})
