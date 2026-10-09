"""Request authentication: lazy session loading and access decorators."""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import g, request

from .. import context
from ..db import users_conn
from ..errors import AuthError, ForbiddenError
from .sessions import AuthContext


def session_token() -> str | None:
    token = request.cookies.get(context.settings().cookie_name)
    return token if token and 16 <= len(token) <= 128 else None


def current_auth() -> AuthContext | None:
    """The authenticated user for this request (loaded once, only when needed)."""
    if "auth" not in g:
        token = session_token()
        g.auth = context.services().sessions.load(users_conn(), token) if token else None
    return g.auth


def require_auth() -> AuthContext:
    auth = current_auth()
    if auth is None:
        raise AuthError()
    return auth


def login_required(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        require_auth()
        return fn(*args, **kwargs)

    return wrapper


def roles_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if require_auth().role not in roles:
                raise ForbiddenError()
            return fn(*args, **kwargs)

        return wrapper

    return decorator
