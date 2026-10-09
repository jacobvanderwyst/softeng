"""Access to the per-application service container (settings, databases, hashing, sessions)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import current_app

if TYPE_CHECKING:
    from .config import Settings
    from .db.engines import Databases
    from .security.passwords import PasswordService
    from .security.sessions import SessionManager


@dataclass
class Services:
    settings: Settings
    databases: Databases
    passwords: PasswordService
    sessions: SessionManager


def services() -> Services:
    return current_app.extensions["degreeplan"]


def settings() -> Settings:
    return services().settings
