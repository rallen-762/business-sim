import os

from flask import Flask

from app.extensions import db


def _normalized_database_url():
    """Render's DATABASE_URL env var uses the legacy 'postgres://' scheme,
    which SQLAlchemy's psycopg2 dialect rejects outright (it requires
    'postgresql://'). Flagging this here rather than letting it surface as a
    cryptic deploy-time crash -- this is a real infra constraint, not a
    style choice."""
    url = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _normalized_database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    # Single global teacher password (not per-World) -- confirmed as the
    # simplest option for one teacher running several class periods.
    app.config["TEACHER_PASSWORD"] = os.environ.get("TEACHER_PASSWORD", "dev-teacher-change-me")

    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)

    with app.app_context():
        from app import models  # noqa: F401  -- registers models on db.metadata

        from app.blueprints import auth, firm, market, teacher
        app.register_blueprint(auth.bp)
        app.register_blueprint(firm.bp)
        app.register_blueprint(market.bp)
        app.register_blueprint(teacher.bp)

    return app
