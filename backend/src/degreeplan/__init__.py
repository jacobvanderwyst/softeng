"""Degree plan tool backend (Flask application factory)."""
from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .api import register_blueprints
from .cli import register_cli
from .config import Settings
from .context import Services
from .errors import register_error_handlers
from .logging_setup import configure_logging
from .security import protection
from .security.passwords import PasswordService
from .security.sessions import SessionManager

__version__ = "1.0.0"


def create_app(settings: Settings | None = None) -> Flask:
    """Build the application. With no argument, settings are read from the environment (APP_ENV, ...)."""
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config.from_mapping(settings.flask_config())  # non-secret values only

    if settings.proxy_hops > 0:
        # Trust exactly this many reverse proxies for client IP / scheme / host. Only valid when a proxy
        # you control overwrites the X-Forwarded-* headers (never expose the app directly with this on).
        hops = settings.proxy_hops
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)  # type: ignore[method-assign]

    configure_logging(app, settings)  # first: request IDs exist before other before_request hooks
    app.extensions["degreeplan"] = Services(
        settings=settings,
        databases=db.Databases(settings),
        passwords=PasswordService(settings),
        sessions=SessionManager(settings),
    )
    db.init_app(app)
    protection.init_app(app, settings)
    register_error_handlers(app)
    register_blueprints(app)
    register_cli(app)
    return app
