from __future__ import annotations

from datetime import UTC, datetime

from flask import request

from ..errors import ValidationError


def int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(f"Query parameter '{name}' must be an integer.") from None
    if not minimum <= value <= maximum:
        raise ValidationError(f"Query parameter '{name}' must be between {minimum} and {maximum}.")
    return value


def iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, UTC).isoformat()
