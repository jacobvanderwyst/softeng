"""Pydantic request models + a strict JSON body parser."""
from __future__ import annotations

from typing import TypeVar

from flask import request
from pydantic import BaseModel, ConfigDict, Field

from .errors import BadRequestError, UnsupportedMediaTypeError

T = TypeVar("T", bound=BaseModel)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(_Strict):
    # Not stripped: whitespace may be part of a passphrase.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PlanCreateIn(_Strict):
    name: str = Field(min_length=1, max_length=120)
    program_id: int = Field(ge=1)


class PlanUpdateIn(_Strict):
    name: str = Field(min_length=1, max_length=120)


class PlanCourseIn(_Strict):
    course_id: int = Field(ge=1)
    term_index: int = Field(ge=1, le=16, description="1 = first term of the plan")


def parse_body(model: type[T]) -> T:
    """Parse and validate the JSON body (pydantic errors become 422 responses)."""
    if not request.is_json:
        raise UnsupportedMediaTypeError()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Request body must be a JSON object.")
    return model.model_validate(data)
