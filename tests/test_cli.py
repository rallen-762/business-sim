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
    from app.avatars import AVATAR_CHOICES, BADGE_CHOICES, PRODUCT_CHOICES

    with app.app_context():
        db.create_all()
        world = World(name="Period 1", game_code="ICON01", planned_firm_slots=2)
        db.session.add(world)
        db.session.commit()

        # avatar names a deleted file; badge/product_icon predate their columns.
        stale = Firm(world_id=world.id, slot_number=1, team_name="Nike", cash=1, plant_capacity=1,
                     avatar="building-k.png", badge=None, product_icon=None)
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
        assert fixed.product_icon in PRODUCT_CHOICES
        # An unclaimed slot legitimately has no icons -- don't invent any.
        assert db.session.get(Firm, unclaimed_id).avatar is None
        assert db.session.get(Firm, unclaimed_id).product_icon is None

    def icons_of(firm_id):
        f = db.session.get(Firm, firm_id)
        return (f.avatar, f.badge, f.product_icon)

    # Stable across re-runs rather than re-rolled on every deploy.
    with app.app_context():
        first = icons_of(stale_id)
    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0
    with app.app_context():
        assert icons_of(stale_id) == first


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


def test_init_db_adds_sold_out_columns_to_an_existing_round_results_table(app):
    # The load-bearing case: a database created BEFORE the sold-out columns
    # existed. create_all() only creates missing TABLES, so round_results
    # keeps its old shape -- and SQLAlchemy SELECTs every column on every
    # RoundResult query, so without the ALTER TABLE here the first page that
    # reads a result 500s. That's the whole site, not a degraded corner.
    with app.app_context():
        db.create_all()
        with db.engine.connect() as conn:
            for col in ("units_demanded_total", "units_lost_to_capacity"):
                conn.execute(sa.text(f"ALTER TABLE round_results DROP COLUMN {col}"))
            conn.commit()
        cols = {c["name"] for c in sa.inspect(db.engine).get_columns("round_results")}
        assert "units_demanded_total" not in cols, "precondition: column really is gone"

    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0

    with app.app_context():
        cols = {c["name"] for c in sa.inspect(db.engine).get_columns("round_results")}
        assert "units_demanded_total" in cols
        assert "units_lost_to_capacity" in cols
        # And a real query against the model must now work end to end.
        assert RoundResult.query.all() == []


def test_init_db_backfills_sold_out_columns_to_zero_not_null(app):
    # Rows that predate the columns can't have their shortfall reconstructed.
    # They must read as "didn't sell out" (0), never NULL -- the model
    # declares both columns NOT NULL, and a NULL would break the template's
    # comparison as well as violate the constraint.
    with app.app_context():
        db.create_all()
        world = World(name="Period 1", game_code="BF0001", planned_firm_slots=1)
        db.session.add(world)
        db.session.commit()
        firm = Firm(world_id=world.id, slot_number=1, team_name="Legacy",
                    cash=1_000_000, plant_capacity=45_000)
        db.session.add(firm)
        db.session.commit()
        firm_id = firm.id

        # Write a result row through raw SQL with the new columns dropped,
        # exactly like a row written by the previous version of the app.
        with db.engine.connect() as conn:
            for col in ("units_demanded_total", "units_lost_to_capacity"):
                conn.execute(sa.text(f"ALTER TABLE round_results DROP COLUMN {col}"))
            conn.execute(sa.text(
                "INSERT INTO round_results (firm_id, round_number, units_sold_by_segment,"
                " units_sold_total, revenue, production_cost, fixed_cost, ad_cost, rd_cost,"
                " celebrity_cost, plant_investment_cost, total_cost, profit, cash_before,"
                " cash_after, quality_level, ad_level, plant_capacity,"
                " new_pending_capacity_increase, loan_taken_this_round,"
                " loan_principal_paid, loan_interest_charged, loan_outstanding_after,"
                " loan_used_ever_after, went_bankrupt_this_round, is_bankrupt, is_auto,"
                " plant_investment_blocked, celebrity_blocked, created_at)"
                " VALUES (:fid, 1, '{}', 1000, 80000, 50000, 100000, 0, 0, 0, 0, 150000,"
                " -70000, 1000000, 930000, 1, 1, 45000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,"
                " CURRENT_TIMESTAMP)"
            ), {"fid": firm_id})
            conn.commit()

    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0

    with app.app_context():
        row = RoundResult.query.filter_by(firm_id=firm_id, round_number=1).one()
        assert row.units_demanded_total == 0
        assert row.units_lost_to_capacity == 0
