"""Serve only public, packaged EduNav assets; API security policy remains unchanged."""
from pathlib import Path

from flask import Blueprint, send_from_directory

ASSET_DIR = Path(__file__).resolve().parent / "frontend"
PUBLIC_FILES = frozenset({
    "index.html", "degree.html", "planner.html", "courses.html", "login.html",
    "style.css", "integration.css", "app.js", "model.js", "api-client.js",
})
bp = Blueprint("web", __name__)


@bp.get("/")
def index():
    return asset("index.html")


@bp.get("/<filename>")
def asset(filename: str):
    if filename not in PUBLIC_FILES:
        from .errors import NotFoundError

        raise NotFoundError("Page not found.")
    response = send_from_directory(ASSET_DIR, filename)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response
