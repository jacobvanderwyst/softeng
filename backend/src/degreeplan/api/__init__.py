from flask import Blueprint, Flask

from . import auth, catalog, health, plans


def register_blueprints(app: Flask) -> None:
    v1 = Blueprint("v1", __name__, url_prefix="/api/v1")
    v1.register_blueprint(health.bp)
    v1.register_blueprint(auth.bp)
    v1.register_blueprint(catalog.bp)
    v1.register_blueprint(plans.bp)
    app.register_blueprint(v1)
