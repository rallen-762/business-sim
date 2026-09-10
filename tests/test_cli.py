"""
Tests the `flask init-db` CLI command used as Render's Pre-Deploy Command.

Edge cases considered:
 1. Must be safe to run on a totally empty database (first-ever deploy).
 2. Must be safe to run AGAIN on a database that already has the tables
    (every subsequent deploy) -- create_all() only adds missing tables, so
    this should be a no-op, not an error.
 3. Must ADD firms.bot_profile to a database whose firms table predates
    that column (i.e. production before this deploy) -- create_all()
    alone can't do this (it only creates missing TABLES), so init-db has
    its own targeted ADD COLUMN check for this one additive change.
"""

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, World


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


def test_init_db_adds_bot_profile_to_a_pre_existing_firms_table(app):
    # Simulates production BEFORE this deploy: a firms table that already
    # exists but predates the bot_profile column -- db.create_all() alone
    # would see the table already exists and do nothing.
    with app.app_context():
        old_columns = [c.copy() for c in Firm.__table__.columns if c.name != "bot_profile"]
        meta = sa.MetaData()
        sa.Table("firms", meta, *old_columns)
        meta.create_all(db.engine)
        db.metadata.create_all(db.engine, tables=[World.__table__, RoundDecision.__table__, RoundResult.__table__])

        assert "bot_profile" not in {c["name"] for c in inspect(db.engine).get_columns("firms")}

    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0

    with app.app_context():
        assert "bot_profile" in {c["name"] for c in inspect(db.engine).get_columns("firms")}


def test_init_db_add_column_step_is_safe_to_run_twice(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["init-db"])
    second = runner.invoke(args=["init-db"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    with app.app_context():
        assert "bot_profile" in {c["name"] for c in inspect(db.engine).get_columns("firms")}
