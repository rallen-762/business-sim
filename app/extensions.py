"""Shared Flask extension instances, kept separate from app/__init__.py so
models.py can import `db` without triggering circular imports with the app
factory."""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
