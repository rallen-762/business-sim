"""
Tests the `flask init-db` CLI command used as Render's Pre-Deploy Command.

Edge cases considered:
 1. Must be safe to run on a totally empty database (first-ever deploy).
 2. Must be safe to run AGAIN on a database that already has the tables
    (every subsequent deploy) -- create_all() only adds missing tables, so
    this should be a no-op, not an error.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db


@pytest.fixture
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    yield app
    with app.app_context():
        db.session.remove()


def test_init_db_creates_all_tables(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["init-db"])
    assert result.exit_code == 0

    with app.app_context():
        table_names = set(inspect(db.engine).get_table_names())
        assert {"worlds", "firms", "round_decisions", "round_results"} <= table_names


def test_init_db_is_safe_to_run_twice(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["init-db"])
    second = runner.invoke(args=["init-db"])
    assert first.exit_code == 0
    assert second.exit_code == 0
