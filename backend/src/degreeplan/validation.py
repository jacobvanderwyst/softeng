"""Pydantic request models + a strict JSON body parser."""
from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from flask import request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import BadRequestError, UnsupportedMediaTypeError

T = TypeVar("T", bound=BaseModel)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(_Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value):
        return value.strip() if isinstance(value, str) else value


class AccountCreateIn(_Strict):
    # Passwords are never trimmed; username/profile fields are normalized explicitly.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)

    @field_validator("username", "first_name", "last_name", mode="before")
    @classmethod
    def clean_public_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value.isprintable():
                raise ValueError("Use text without control characters.")
        return value


class RegisterIn(AccountCreateIn):
    program_id: int | None = Field(default=None, ge=1, strict=True)


class InstructorCreateIn(AccountCreateIn):
    department: str | None = Field(default=None, max_length=120)

    @field_validator("department")
    @classmethod
    def clean_department(cls, value):
        if value is None:
            return None
        value = value.strip()
        if value and not value.isprintable():
            raise ValueError("Use text without control characters.")
        return value or None


class ProgramAdminIn(_Strict):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]*$")
    name: str = Field(min_length=1, max_length=200)
    total_credits: int = Field(ge=1, le=1000, strict=True)
    course_ids: list[Annotated[int, Field(ge=1, strict=True)]] = Field(min_length=1, max_length=10000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.isprintable():
            raise ValueError("Use text without control characters.")
        return value

    @field_validator("course_ids")
    @classmethod
    def unique_courses(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Required courses must be unique.")
        return value


class ProgramAdminUpdateIn(ProgramAdminIn):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


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


class PlanSemesterIn(_Strict):
    term_index: int = Field(ge=1, le=16, strict=True)
    name: str = Field(min_length=1, max_length=120)


class CourseProgressIn(_Strict):
    status: Literal["Not Started", "In Progress", "Completed"]
    grade: Literal["A", "B", "C", "D", "F"] | None = None


class CourseGradeIn(_Strict):
    grade: Literal["A", "B", "C", "D", "F"] | None

def parse_body(model: type[T]) -> T:
    """Parse and validate the JSON body (pydantic errors become 422 responses)."""
    if not request.is_json:
        raise UnsupportedMediaTypeError()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Request body must be a JSON object.")
    return model.model_validate(data)
