import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, World
from app.scouting_report import build_scouting_report


@pytest.fixture
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_world(planned_firm_slots=3, **overrides):
    w = World(name="Period 3", game_code="ABC123", planned_firm_slots=planned_firm_slots, **overrides)
    db.session.add(w)
    db.session.commit()
    return w


def make_firm(world, slot_number, team_name, **overrides):
    f = Firm(
        world_id=world.id, slot_number=slot_number, team_name=team_name,
        cash=1_000_000, plant_capacity=45_000, **overrides,
    )
    f.set_password("testpass")
    db.session.add(f)
    db.session.commit()
    return f


def make_decision(firm, round_number, **overrides):
    base = dict(price=80.0, production_qty=15_000, ad_spend=0, rd_spend=0, track="Standard", celebrity_on=False, plant_investment=0, is_auto=False)
    base.update(overrides)
    d = RoundDecision(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(d)
    db.session.commit()
    return d


def make_result(firm, round_number, segment_units=None, **overrides):
    base = dict(
        units_sold_by_segment=segment_units or {}, units_sold_total=0, revenue=0,
        production_cost=0, fixed_cost=100_000, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=100_000, profit=-100_000,
        cash_before=1_000_000, cash_after=900_000, quality_level=1, ad_level=1, plant_capacity=45_000,
        loan_outstanding_after=0, is_bankrupt=False,
    )
    base.update(overrides)
    r = RoundResult(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(r)
    db.session.commit()
    return r


def test_empty_before_any_round(app):
    world = make_world()
    assert build_scouting_report(world) == []


def test_ranks_by_cumulative_revenue_not_profit(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, revenue=10_000, profit=-500)  # higher revenue, lower profit
    make_decision(adidas, 1)
    make_result(adidas, 1, revenue=5_000, profit=2_000)  # lower revenue, higher profit

    report = build_scouting_report(world)
    assert [r["team_name"] for r in report] == ["Nike", "Adidas"]


def test_revenue_tie_broken_by_profit(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, revenue=10_000, profit=1_000)
    make_decision(adidas, 1)
    make_result(adidas, 1, revenue=10_000, profit=5_000)

    report = build_scouting_report(world)
    assert [r["team_name"] for r in report] == ["Adidas", "Nike"]


def test_reports_at_most_3_even_with_more_firms(app):
    world = make_world(planned_firm_slots=4)
    for i in range(1, 5):
        f = make_firm(world, i, f"Firm{i}")
        make_decision(f, 1)
        make_result(f, 1, revenue=1000 * i, profit=100 * i)

    report = build_scouting_report(world)
    assert len(report) == 3
    assert report[0]["team_name"] == "Firm4"  # highest revenue


def test_unregistered_firm_excluded_from_report_and_field_average(app):
    world = make_world(planned_firm_slots=2)
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1, price=100)
    make_result(nike, 1, revenue=5000, profit=1000)
    # Slot 2 stays unregistered -- should never appear, and never pollute
    # the field average even if it somehow had data.

    report = build_scouting_report(world)
    assert len(report) == 1
    assert "field average of $100.00" not in report[0]["summary"]  # only firm -> no meaningful "average", in-line-with wording


def test_summary_mentions_price_above_average(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1, price=100)
    make_result(nike, 1, revenue=10_000, profit=1000)
    make_decision(adidas, 1, price=50)
    make_result(adidas, 1, revenue=1, profit=1)

    report = build_scouting_report(world)
    nike_entry = next(r for r in report if r["team_name"] == "Nike")
    assert "above the field average" in nike_entry["summary"]


def test_summary_mentions_celebrity_and_plant_investment(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1, celebrity_on=True, plant_investment=100_000)
    make_result(nike, 1, revenue=5000, profit=1000)

    report = build_scouting_report(world)
    assert "Celebrity Endorsement" in report[0]["summary"]
    assert "expanding plant capacity" in report[0]["summary"]


def test_summary_credits_leading_segment(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, revenue=5000, profit=1000, segment_units={"Low Income": 900, "Wealthy": 10})
    make_decision(adidas, 1)
    make_result(adidas, 1, revenue=1, profit=1, segment_units={"Low Income": 100, "Wealthy": 90})

    report = build_scouting_report(world)
    nike_entry = next(r for r in report if r["team_name"] == "Nike")
    assert "Low Income" in nike_entry["summary"]


def test_tied_segment_share_credits_no_one(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, revenue=5000, profit=1000, segment_units={"Low Income": 50})
    make_decision(adidas, 1)
    make_result(adidas, 1, revenue=1, profit=1, segment_units={"Low Income": 50})

    report = build_scouting_report(world)
    nike_entry = next(r for r in report if r["team_name"] == "Nike")
    assert "Low Income" not in nike_entry["summary"]


def test_firm_with_no_decision_gets_fallback_sentence_not_a_crash(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    # RoundResult exists (e.g. bankrupt/frozen) but no RoundDecision this round.
    make_result(nike, 1, revenue=0, profit=-100_000, is_bankrupt=True)

    report = build_scouting_report(world)
    assert len(report) == 1
    assert "didn't submit" in report[0]["summary"]
