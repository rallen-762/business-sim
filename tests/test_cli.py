"""
Tests the `flask init-db` CLI command used as Render's Pre-Deploy Command.

Edge cases considered:
 1. Must be safe to run on a totally empty database (first-ever deploy).
 2. Must be safe to run AGAIN on a database that already has the tables
    (every subsequent deploy) -- create_all() only adds missing tables, so
    this should be a no-op, not an error.
 3. Must ADD firms.bot_profile and firms.badge to a database whose firms
    table predates those columns (i.e. production before this deploy) --
    create_all() alone can't do this (it only creates missing TABLES), so
    init-db has its own targeted ADD COLUMN check for each additive change.
 4. Headphone Company Simulator reskin: must rewrite old Track/segment
    values (Budget/Standard, Basketball Players) already persisted from
    before the rename -- everywhere they're stored, including inside the
    round_results.units_sold_by_segment JSON column -- and that rewrite
    must be idempotent (safe to run on every deploy, not just once).
"""

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, SegmentRoundResult, World


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


def test_init_db_adds_badge_to_a_pre_existing_firms_table(app):
    # Same idea as the bot_profile test above, for the badge column added
    # for the Headphone Company Simulator asset pack.
    with app.app_context():
        old_columns = [c.copy() for c in Firm.__table__.columns if c.name != "badge"]
        meta = sa.MetaData()
        sa.Table("firms", meta, *old_columns)
        meta.create_all(db.engine)
        db.metadata.create_all(db.engine, tables=[World.__table__, RoundDecision.__table__, RoundResult.__table__])

        assert "badge" not in {c["name"] for c in inspect(db.engine).get_columns("firms")}

    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0

    with app.app_context():
        assert "badge" in {c["name"] for c in inspect(db.engine).get_columns("firms")}


def test_init_db_repairs_icons_pointing_at_deleted_files(app):
    # The Headphone Company Simulator asset swap replaced the avatar pool and
    # deleted the old Kenney building PNGs -- every firm registered before it
    # still names one, which renders as a broken <img>, not as "no icon".
    from app.avatars import AVATAR_CHOICES, BADGE_CHOICES

    with app.app_context():
        db.create_all()
        world = World(name="Period 1", game_code="ICON01", planned_firm_slots=2)
        db.session.add(world)
        db.session.commit()

        stale = Firm(world_id=world.id, slot_number=1, team_name="Nike", cash=1, plant_capacity=1,
                     avatar="building-k.png", badge=None)
        stale.set_password("x")
        unclaimed = Firm(world_id=world.id, slot_number=2, cash=1, plant_capacity=1)
        db.session.add_all([stale, unclaimed])
        db.session.commit()
        stale_id, unclaimed_id = stale.id, unclaimed.id

    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0

    with app.app_context():
        fixed = db.session.get(Firm, stale_id)
        assert fixed.avatar in AVATAR_CHOICES
        assert fixed.badge in BADGE_CHOICES
        # An unclaimed slot legitimately has no icons -- don't invent any.
        assert db.session.get(Firm, unclaimed_id).avatar is None

    # Stable across re-runs rather than re-rolled on every deploy.
    with app.app_context():
        first = (db.session.get(Firm, stale_id).avatar, db.session.get(Firm, stale_id).badge)
    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0
    with app.app_context():
        assert (db.session.get(Firm, stale_id).avatar, db.session.get(Firm, stale_id).badge) == first


def test_init_db_rewrites_old_track_and_segment_values(app):
    # Simulates a world that played rounds BEFORE the Headphone Company
    # Simulator reskin: old Track values ("Standard") and the old segment
    # name ("Basketball Players") persisted in every place they're stored,
    # including inside a JSON column. init-db's rename step must catch all
    # of them, not just the ones in a plain string column.
    with app.app_context():
        db.create_all()
        world = World(name="Period 1", game_code="OLD123", planned_firm_slots=1)
        db.session.add(world)
        db.session.commit()

        firm = Firm(
            world_id=world.id, slot_number=1, team_name="Nike", cash=1_000_000,
            plant_capacity=45_000, last_price=80.0, last_track="Standard",
        )
        db.session.add(firm)
        db.session.commit()

        db.session.add(RoundDecision(
            firm_id=firm.id, round_number=1, price=80.0, production_qty=1000,
            track="Standard",
        ))
        db.session.add(RoundResult(
            firm_id=firm.id, round_number=1,
            units_sold_by_segment={"Basketball Players": 500, "Wealthy": 10},
            units_sold_total=510, revenue=0, production_cost=0, fixed_cost=0,
            ad_cost=0, rd_cost=0, celebrity_cost=0, plant_investment_cost=0,
            total_cost=0, profit=0, cash_before=0, cash_after=0,
            quality_level=1, ad_level=1, plant_capacity=45_000,
        ))
        db.session.add(SegmentRoundResult(
            world_id=world.id, round_number=1, segment="Basketball Players",
            total_buyers=1000, unsold_buyers=0, unsold_buyers_pct=0,
        ))
        db.session.commit()
        firm_id, decision_world_id = firm.id, world.id

    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.last_track == "Mid"

        decision = RoundDecision.query.filter_by(firm_id=firm_id, round_number=1).first()
        assert decision.track == "Mid"

        result_row = RoundResult.query.filter_by(firm_id=firm_id, round_number=1).first()
        assert result_row.units_sold_by_segment == {"Athletes": 500, "Wealthy": 10}

        segment_row = SegmentRoundResult.query.filter_by(world_id=decision_world_id, round_number=1).first()
        assert segment_row.segment == "Athletes"

    # Idempotent: running it again must not error and must leave the
    # already-renamed values alone (nothing left to rename).
    second = app.test_cli_runner().invoke(args=["init-db"])
    assert second.exit_code == 0
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.last_track == "Mid"
