"""Application settings.

Rules:
* No secret has a default value in code. Secrets come from the environment, or from a file named by
  ``<NAME>_FILE`` (so the host can protect them with file ACLs / Docker-style secrets).
* ``APP_ENV`` must be set explicitly (development | testing | production). Nothing silently defaults
  to a permissive mode.
* Production fails fast on weak or missing settings instead of starting insecurely.
* Secret-bearing fields are excluded from ``repr`` so they cannot leak through logging.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parents[1]  # <repo>/backend when running from the source tree
ENVIRONMENTS = ("development", "testing", "production")
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "example", "dev-only", "insecure")
_ORIGIN_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?")


class ConfigError(RuntimeError):
    """Raised for invalid or unsafe configuration. Messages never contain secret values."""


# --------------------------------------------------------------------------- env helpers
def _read_secret(env: Mapping[str, str], name: str) -> str | None:
    direct = env.get(name)
    file_ref = env.get(f"{name}_FILE")
    if direct and file_ref:
        raise ConfigError(f"Set only one of {name} or {name}_FILE, not both")
    if file_ref:
        try:
            value = Path(file_ref).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Cannot read {name}_FILE ({file_ref}): {exc.strerror}") from None
        if not value:
            raise ConfigError(f"{name}_FILE ({file_ref}) is empty")
        return value
    return direct or None


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def sqlite_file_path(url: str) -> Path | None:
    """Return the file path of a SQLite URL (None for other backends or in-memory)."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise ConfigError("Database URL is not a valid SQLAlchemy URL") from None
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return None
    return Path(parsed.database)


def load_dotenv_for_development(env: dict[str, str] | None = None) -> None:
    """Load backend/.env into the environment, only when APP_ENV=development.

    Real environment variables always win, and APP_ENV can never be set from the file.
    """
    target = os.environ if env is None else env
    if target.get("APP_ENV", "").strip().lower() != "development":
        return
    path = BACKEND_DIR / ".env"
    if not path.is_file():
        return
    for key, value in dotenv_values(path).items():
        if key != "APP_ENV" and value is not None:
            target.setdefault(key, value)


# --------------------------------------------------------------------------- settings
@dataclass(frozen=True)
class Settings:
    env_name: str
    # Secrets / secret-bearing values: never part of repr().
    secret_key: str = field(default="", repr=False)
    secret_key_fallbacks: tuple[str, ...] = field(default=(), repr=False)
    users_database_url: str = field(default="", repr=False)
    plans_database_url: str = field(default="", repr=False)
    ratelimit_storage_uri: str = field(default="memory://", repr=False)
    # Logging
    log_level: str = "INFO"
    log_format: str = "text"  # text | json
    log_dir: Path | None = None
    # HTTP / sessions
    cors_origins: tuple[str, ...] = ()
    cookie_secure: bool = True
    session_idle_timeout: int = 1800
    session_absolute_timeout: int = 8 * 3600
    proxy_hops: int = 0
    max_content_length: int = 1_048_576
    # Abuse protection
    ratelimit_enabled: bool = True
    default_rate_limit: str = "300 per minute"
    login_rate_limit: str = "20 per minute"
    lockout_threshold: int = 5
    lockout_seconds: int = 300
    # Business rules
    max_credits_per_term: int = 21
    password_min_length: int = 12
    # Password hashing cost (Argon2id). Not configurable from the environment on purpose.
    argon2_memory_kib: int = 65536
    argon2_time_cost: int = 3
    argon2_parallelism: int = 4

    # ----- derived
    @property
    def is_production(self) -> bool:
        return self.env_name == "production"

    @property
    def cookie_name(self) -> str:
        # The __Host- prefix makes browsers require Secure + Path=/ + no Domain.
        return "__Host-sid" if self.cookie_secure else "sid"

    def flask_config(self) -> dict[str, object]:
        """Non-secret values mirrored into app.config (plus what Flask extensions need)."""
        return {
            "ENV_NAME": self.env_name,
            "TESTING": self.env_name == "testing",
            "DEBUG": False,  # never enable Flask's debugger: it executes code on errors
            "MAX_CONTENT_LENGTH": self.max_content_length,
            "PROPAGATE_EXCEPTIONS": False,
            "RATELIMIT_ENABLED": self.ratelimit_enabled,
            "RATELIMIT_DEFAULT": self.default_rate_limit,
            "RATELIMIT_STORAGE_URI": self.ratelimit_storage_uri,
            "RATELIMIT_HEADERS_ENABLED": True,
        }

    # ----- construction
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        name = (env.get("APP_ENV") or "").strip().lower()
        if name not in ENVIRONMENTS:
            raise ConfigError(f"APP_ENV must be set to one of: {', '.join(ENVIRONMENTS)}")
        development = name == "development"

        secret = _read_secret(env, "SECRET_KEY")
        if not secret:
            if not development:
                raise ConfigError("SECRET_KEY (or SECRET_KEY_FILE) is required")
            secret = secrets.token_urlsafe(48)
            log.warning("SECRET_KEY not set: using a random ephemeral key (sessions reset on restart)")

        fallbacks_raw = _read_secret(env, "SECRET_KEY_FALLBACKS") or ""
        fallbacks = tuple(k.strip() for k in fallbacks_raw.split(",") if k.strip())

        users_url = _read_secret(env, "USERS_DATABASE_URL")
        plans_url = _read_secret(env, "PLANS_DATABASE_URL")
        if development:
            instance = BACKEND_DIR / "instance"
            users_url = users_url or f"sqlite:///{(instance / 'users.sqlite3').as_posix()}"
            plans_url = plans_url or f"sqlite:///{(instance / 'plans.sqlite3').as_posix()}"
        if not users_url or not plans_url:
            raise ConfigError("USERS_DATABASE_URL and PLANS_DATABASE_URL are required")

        origins = tuple(o.strip().rstrip("/") for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        log_dir = env.get("LOG_DIR")

        settings = cls(
            env_name=name,
            secret_key=secret,
            secret_key_fallbacks=fallbacks,
            users_database_url=users_url,
            plans_database_url=plans_url,
            ratelimit_storage_uri=_read_secret(env, "RATELIMIT_STORAGE_URI") or "memory://",
            log_level=(env.get("LOG_LEVEL") or "INFO").upper(),
            log_format=(env.get("LOG_FORMAT") or "text").lower(),
            log_dir=Path(log_dir) if log_dir else None,
            cors_origins=origins,
            cookie_secure=_bool(env, "COOKIE_SECURE", not development),
            session_idle_timeout=_int(env, "SESSION_IDLE_TIMEOUT", 1800, 60, 7 * 86400),
            session_absolute_timeout=_int(env, "SESSION_ABSOLUTE_TIMEOUT", 8 * 3600, 300, 30 * 86400),
            proxy_hops=_int(env, "PROXY_HOPS", 0, 0, 5),
            max_content_length=_int(env, "MAX_CONTENT_LENGTH", 1_048_576, 1024, 10_485_760),
            ratelimit_enabled=_bool(env, "RATELIMIT_ENABLED", True),
            default_rate_limit=env.get("DEFAULT_RATE_LIMIT") or "300 per minute",
            login_rate_limit=env.get("LOGIN_RATE_LIMIT") or "20 per minute",
            lockout_threshold=_int(env, "LOCKOUT_THRESHOLD", 5, 3, 50),
            lockout_seconds=_int(env, "LOCKOUT_SECONDS", 300, 30, 86400),
            max_credits_per_term=_int(env, "MAX_CREDITS_PER_TERM", 21, 1, 60),
            password_min_length=_int(env, "PASSWORD_MIN_LENGTH", 12, 12, 128),
        )
        settings.validate()
        return settings

    @classmethod
    def for_testing(cls, users_database_url: str, plans_database_url: str, **overrides: object) -> Settings:
        """Settings for the test-suite: production-like cookies/headers, cheap hashing, no log files."""
        base = cls(
            env_name="testing",
            secret_key=secrets.token_urlsafe(48),
            users_database_url=users_database_url,
            plans_database_url=plans_database_url,
            log_level="DEBUG",
            ratelimit_enabled=False,
            argon2_memory_kib=64,
            argon2_time_cost=1,
            argon2_parallelism=1,
        )
        settings = replace(base, **overrides)  # type: ignore[arg-type]
        settings.validate()
        return settings

    # ----- validation
    def validate(self) -> None:
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("LOG_LEVEL is not a valid level")
        if self.log_format not in {"text", "json"}:
            raise ConfigError("LOG_FORMAT must be text or json")
        if not self.secret_key:
            raise ConfigError("SECRET_KEY is required")
        for origin in self.cors_origins:
            if origin == "*" or not _ORIGIN_RE.fullmatch(origin):
                raise ConfigError(f"CORS_ORIGINS entry {origin!r} must be an exact origin like https://host")
        if self.session_idle_timeout > self.session_absolute_timeout:
            raise ConfigError("SESSION_IDLE_TIMEOUT must not exceed SESSION_ABSOLUTE_TIMEOUT")
        if self.users_database_url == self.plans_database_url:
            raise ConfigError("USERS_DATABASE_URL and PLANS_DATABASE_URL must point to different databases")
        if self.is_production:
            self._validate_production()

    def _validate_production(self) -> None:
        for key in (self.secret_key, *self.secret_key_fallbacks):
            if len(key) < 32 or any(m in key.lower() for m in _PLACEHOLDER_MARKERS):
                raise ConfigError("SECRET_KEY must be a random value of at least 32 characters")
        if not self.cookie_secure:
            raise ConfigError("COOKIE_SECURE cannot be disabled in production")
        for origin in self.cors_origins:
            if not origin.startswith("https://"):
                raise ConfigError("Production CORS_ORIGINS must use https://")
        for label, url in (("USERS_DATABASE_URL", self.users_database_url),
                           ("PLANS_DATABASE_URL", self.plans_database_url)):
            path = sqlite_file_path(url)
            if path is None:
                continue
            if not path.is_absolute():
                raise ConfigError(f"{label}: SQLite path must be absolute in production")
            resolved = path.resolve()
            if BACKEND_DIR in resolved.parents or (BACKEND_DIR.parent / ".git").exists() and (
                BACKEND_DIR.parent in resolved.parents
            ):
                raise ConfigError(f"{label}: database files must live outside the source tree")
        if not self.ratelimit_enabled:
            raise ConfigError("RATELIMIT_ENABLED cannot be disabled in production")
        if self.ratelimit_storage_uri.startswith("memory://"):
            log.warning("Rate limits use in-memory storage: only correct for a single worker process")
