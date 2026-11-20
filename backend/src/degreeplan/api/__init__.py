from flask import Blueprint, Flask

from . import admin, auth, catalog, health, plans, progress


def register_blueprints(app: Flask) -> None:
    v1 = Blueprint("v1", __name__, url_prefix="/api/v1")
    v1.register_blueprint(health.bp)
    v1.register_blueprint(auth.bp)
    v1.register_blueprint(catalog.bp)
    v1.register_blueprint(plans.bp)
    v1.register_blueprint(progress.bp)
    v1.register_blueprint(admin.bp)
    app.register_blueprint(v1)
