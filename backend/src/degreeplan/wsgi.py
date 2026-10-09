"""WSGI entry point.

    Production:  waitress-serve --listen=127.0.0.1:8000 degreeplan.wsgi:app   (behind a TLS proxy)
    CLI:         flask --app degreeplan.wsgi users list

Settings come from the environment (APP_ENV is mandatory). A .env file is read only when
APP_ENV=development.
"""
from .config import load_dotenv_for_development

load_dotenv_for_development()

from . import create_app  # noqa: E402

app = create_app()
