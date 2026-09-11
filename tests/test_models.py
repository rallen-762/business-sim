"""
Schema tests, run against an in-memory SQLite DB (not Postgres) -- fast,
zero setup, and sufficient to verify constraints/relationships/cascades.
Postgres-specific behavior (if any ever creeps in) would need its own test
against a real Postgres instance; nothing here relies on Postgres-only
features.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, World


@pytest.fixture
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_world(name="Period 3", game_code="ABC123", planned_firm_slots=8, **overrides):
    w = World(name=name, game_code=game_code, planned_firm_slots=planned_firm_slots, **overrides)
    db.session.add(w)
    db.session.commit()
    return w


def make_firm(world, slot_number=1, team_name=None, cash=1_000_000, plant_capacity=45_000, **overrides):
    f = Firm(
        world_id=world.id, slot_number=slot_number, team_name=team_name,
        cash=cash, plant_capacity=plant_capacity, **overrides,
    )
    db.session.add(f)
    db.session.commit()
    return f


# --------------------------------------------------------------------------- #
# World
# --------------------------------------------------------------------------- #

def test_create_world_defaults(app):
    w = make_world()
    assert w.current_round == 1
    assert w.status == "collecting"


def test_game_code_must_be_globally_unique(app):
    make_world(game_code="DUP01")
    with pytest.raises(IntegrityError):
        make_world(name="Period 5", game_code="DUP01")


def test_current_round_check_constraint_rejects_zero(app):
    w = World(name="Period 1", game_code="ZERO01", current_round=0)
    db.session.add(w)
    with pytest.raises(IntegrityError):
        db.session.commit()


# --------------------------------------------------------------------------- #
# Firm
# --------------------------------------------------------------------------- #

def test_firm_not_registered_until_password_set(app):
    w = make_world()
    f = make_firm(w)
    assert f.is_registered is False
    f.set_password("hoopdreams")
    db.session.commit()
    assert f.is_registered is True
    assert f.check_password("hoopdreams") is True
    assert f.check_password("wrong") is False


def test_password_is_hashed_not_plaintext(app):
    w = make_world()
    f = make_firm(w)
    f.set_password("hoopdreams")
    assert f.password_hash != "hoopdreams"


def test_team_name_unique_within_a_world(app):
    w = make_world()
    make_firm(w, slot_number=1, team_name="Nike")
    # Distinct slot_number so this is specifically testing the team_name
    # constraint, not colliding on slot_number too.
    with pytest.raises(IntegrityError):
        make_firm(w, slot_number=2, team_name="Nike")


def test_same_team_name_allowed_across_different_worlds(app):
    w1 = make_world(game_code="W1")
    w2 = make_world(name="Period 4", game_code="W2")
    make_firm(w1, team_name="Nike")
    # Should NOT raise -- uniqueness is scoped per-world.
    make_firm(w2, team_name="Nike")


def test_slot_number_unique_within_a_world(app):
    w = make_world()
    make_firm(w, slot_number=1, team_name=None)
    with pytest.raises(IntegrityError):
        make_firm(w, slot_number=1, team_name=None)


def test_multiple_unclaimed_slots_can_coexist_with_null_team_name(app):
    w = make_world()
    make_firm(w, slot_number=1, team_name=None)
    # Should NOT raise -- NULL != NULL for uniqueness purposes.
    make_firm(w, slot_number=2, team_name=None)


def test_register_sets_name_password_and_avatar_together(app):
    w = make_world()
    f = make_firm(w, slot_number=1, team_name=None)
    assert f.is_registered is False
    f.register("Air Jordan Co.", "sneakerhead1", avatar="logo_flame.png")
    db.session.commit()
    assert f.team_name == "Air Jordan Co."
    assert f.is_registered is True
    assert f.check_password("sneakerhead1") is True
    assert f.avatar == "logo_flame.png"


def test_deleting_world_cascades_to_firms(app):
    w = make_world()
    make_firm(w, slot_number=1, team_name="Nike")
    make_firm(w, slot_number=2, team_name="Adidas")
    assert Firm.query.count() == 2
    db.session.delete(w)
    db.session.commit()
    assert Firm.query.count() == 0


# --------------------------------------------------------------------------- #
# RoundDecision
# --------------------------------------------------------------------------- #

def test_one_decision_per_firm_per_round_enforced(app):
    w = make_world()
    f = make_firm(w)
    db.session.add(RoundDecision(
        firm_id=f.id, round_number=1, price=50, production_qty=45_000, track="Mid",
    ))
    db.session.commit()
    db.session.add(RoundDecision(
        firm_id=f.id, round_number=1, price=60, production_qty=40_000, track="Mid",
    ))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_same_firm_can_have_decisions_across_different_rounds(app):
    w = make_world()
    f = make_firm(w)
    db.session.add(RoundDecision(firm_id=f.id, round_number=1, price=50, production_qty=45_000, track="Mid"))
    db.session.add(RoundDecision(firm_id=f.id, round_number=2, price=55, production_qty=45_000, track="Mid"))
    db.session.commit()
    assert f.decisions.count() == 2


def test_auto_decision_flag_records_non_submission(app):
    w = make_world()
    f = make_firm(w)
    d = RoundDecision(
        firm_id=f.id, round_number=1, price=50, production_qty=45_000, track="Mid", is_auto=True,
    )
    db.session.add(d)
    db.session.commit()
    assert f.decisions.first().is_auto is True


def test_deleting_firm_cascades_to_decisions_and_results(app):
    w = make_world()
    f = make_firm(w)
    db.session.add(RoundDecision(firm_id=f.id, round_number=1, price=50, production_qty=45_000, track="Mid"))
    db.session.add(RoundResult(
        firm_id=f.id, round_number=1, units_sold_by_segment={"Low Income": 100}, units_sold_total=100,
        revenue=5000, production_cost=2500, fixed_cost=100_000, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=102_500, profit=-97_500, cash_before=1_000_000, cash_after=902_500,
        quality_level=1, ad_level=1, plant_capacity=45_000,
    ))
    db.session.commit()
    db.session.delete(f)
    db.session.commit()
    assert RoundDecision.query.count() == 0
    assert RoundResult.query.count() == 0


# --------------------------------------------------------------------------- #
# RoundResult
# --------------------------------------------------------------------------- #

def test_one_result_per_firm_per_round_enforced(app):
    w = make_world()
    f = make_firm(w)
    kwargs = dict(
        firm_id=f.id, round_number=1, units_sold_by_segment={}, units_sold_total=0,
        revenue=0, production_cost=0, fixed_cost=100_000, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=100_000, profit=-100_000, cash_before=1_000_000, cash_after=900_000,
        quality_level=1, ad_level=1, plant_capacity=45_000,
    )
    db.session.add(RoundResult(**kwargs))
    db.session.commit()
    db.session.add(RoundResult(**kwargs))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_units_sold_by_segment_round_trips_as_json(app):
    w = make_world()
    f = make_firm(w)
    segment_units = {"Low Income": 1234, "NBA Fans": 56, "Athletes": 7, "Wealthy": 8, "Casual/Fashion": 9}
    r = RoundResult(
        firm_id=f.id, round_number=1, units_sold_by_segment=segment_units, units_sold_total=1314,
        revenue=1, production_cost=1, fixed_cost=1, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=1, profit=0, cash_before=1, cash_after=1,
        quality_level=1, ad_level=1, plant_capacity=45_000,
    )
    db.session.add(r)
    db.session.commit()
    db.session.expire_all()
    fetched = RoundResult.query.first()
    assert fetched.units_sold_by_segment == segment_units
