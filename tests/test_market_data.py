import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.constants import SEGMENT_BUYER_COUNT, SEGMENTS
from app.extensions import db
from app.market_data import (
    build_pie_gradient,
    competitive_intel_rows,
    consumer_surplus_by_segment,
    cumulative_standings,
    market_shares_for_round,
    round_totals,
    segment_overview,
)
from app.models import Firm, RoundDecision, RoundResult, SegmentRoundResult, World


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


def make_firm(world, slot_number, team_name, avatar="building-a.png", **overrides):
    f = Firm(
        world_id=world.id, slot_number=slot_number, team_name=team_name, avatar=avatar,
        cash=1_000_000, plant_capacity=45_000, **overrides,
    )
    f.set_password("testpass")  # market_data.py's queries filter on is_registered
    db.session.add(f)
    db.session.commit()
    return f


def make_decision(firm, round_number, **overrides):
    base = dict(price=80.0, production_qty=45_000, ad_spend=0, rd_spend=0, track="Standard", is_auto=False)
    base.update(overrides)
    d = RoundDecision(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(d)
    db.session.commit()
    return d


def make_result(firm, round_number, segment_units=None, **overrides):
    base = dict(
        units_sold_by_segment=segment_units or {"Low Income": 100}, units_sold_total=100, revenue=8000,
        production_cost=2250, fixed_cost=100_000, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=102_250, profit=-94_250,
        cash_before=1_000_000, cash_after=905_750, quality_level=1, ad_level=1, plant_capacity=45_000,
        loan_outstanding_after=0, is_bankrupt=False,
    )
    base.update(overrides)
    r = RoundResult(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(r)
    db.session.commit()
    return r


def make_segment_result(world, round_number, segment, **overrides):
    base = dict(total_buyers=1000.0, unsold_buyers=0.0, unsold_buyers_pct=0.0, avg_consumer_surplus=10.0)
    base.update(overrides)
    row = SegmentRoundResult(world_id=world.id, round_number=round_number, segment=segment, **base)
    db.session.add(row)
    db.session.commit()
    return row


# --------------------------------------------------------------------------- #
# consumer_surplus_by_segment
# --------------------------------------------------------------------------- #

def test_consumer_surplus_empty_before_any_round(app):
    world = make_world()
    assert consumer_surplus_by_segment(world, None) == []
    assert consumer_surplus_by_segment(world, 1) == []  # round_number given but nothing persisted for it yet


def test_consumer_surplus_returns_one_row_per_segment_in_order(app):
    world = make_world()
    for seg in SEGMENTS:
        make_segment_result(world, 1, seg, avg_consumer_surplus=5.0)
    rows = consumer_surplus_by_segment(world, 1)
    assert [row["name"] for row in rows] == list(SEGMENTS)


def test_consumer_surplus_none_when_nobody_could_afford_anyone(app):
    world = make_world()
    for seg in SEGMENTS:
        make_segment_result(
            world, 1, seg, unsold_buyers=SEGMENT_BUYER_COUNT[seg], unsold_buyers_pct=100.0,
            avg_consumer_surplus=None,
        )
    rows = consumer_surplus_by_segment(world, 1)
    assert all(row["avg_consumer_surplus"] is None for row in rows)
    assert all(row["unsold_buyers_pct"] == 100.0 for row in rows)


def test_consumer_surplus_scoped_to_the_requested_round_only(app):
    world = make_world()
    make_segment_result(world, 1, "Wealthy", avg_consumer_surplus=5.0)
    make_segment_result(world, 2, "Wealthy", avg_consumer_surplus=50.0)
    rows_round_1 = consumer_surplus_by_segment(world, 1)
    wealthy_row = next(r for r in rows_round_1 if r["name"] == "Wealthy")
    assert wealthy_row["avg_consumer_surplus"] == 5.0


# --------------------------------------------------------------------------- #
# cumulative_standings
# --------------------------------------------------------------------------- #

def test_cumulative_standings_empty_before_any_round(app):
    world = make_world()
    assert cumulative_standings(world) == []


def test_cumulative_standings_sorts_by_cumulative_profit_desc(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, profit=500)
    make_decision(adidas, 1)
    make_result(adidas, 1, profit=9000)

    standings = cumulative_standings(world)
    assert [row["firm"].team_name for row in standings] == ["Adidas", "Nike"]


def test_cumulative_standings_sums_across_multiple_rounds(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1)
    make_result(nike, 1, profit=1000, revenue=5000, units_sold_total=50)
    make_decision(nike, 2, price=90)
    make_result(nike, 2, profit=2000, revenue=6000, units_sold_total=60)

    standings = cumulative_standings(world)
    assert standings[0]["cum_profit"] == 3000
    assert standings[0]["cum_revenue"] == 11000
    assert standings[0]["cum_units"] == 110


def test_cumulative_standings_bar_pct_scales_to_the_biggest_firm(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, profit=1000)
    make_decision(adidas, 1)
    make_result(adidas, 1, profit=4000)

    standings = cumulative_standings(world)
    adidas_row = next(r for r in standings if r["firm"].team_name == "Adidas")
    nike_row = next(r for r in standings if r["firm"].team_name == "Nike")
    assert adidas_row["bar_pct"] == 100.0  # the biggest |profit| always fills the bar
    assert nike_row["bar_pct"] == 25.0


def test_cumulative_standings_bar_pct_uses_magnitude_for_a_losing_firm(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, profit=-2000)
    make_decision(adidas, 1)
    make_result(adidas, 1, profit=1000)

    standings = cumulative_standings(world)
    nike_row = next(r for r in standings if r["firm"].team_name == "Nike")
    assert nike_row["bar_pct"] == 100.0  # -2000 has the largest MAGNITUDE, even though it's a loss


def test_cumulative_standings_bar_pct_is_zero_when_everyone_is_at_zero(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1)
    make_result(nike, 1, profit=0)

    standings = cumulative_standings(world)
    assert standings[0]["bar_pct"] == 0  # no divide-by-zero


def test_cumulative_standings_price_is_latest_snapshot_not_summed(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1, price=80)
    make_result(nike, 1)
    make_decision(nike, 2, price=95)
    make_result(nike, 2)

    standings = cumulative_standings(world)
    assert standings[0]["latest_price"] == 95  # latest, not 80+95


def test_cumulative_standings_excludes_unregistered_firms(app):
    world = make_world()
    make_firm(world, 1, "Nike")
    unclaimed = Firm(world_id=world.id, slot_number=2, cash=1_000_000, plant_capacity=45_000)
    db.session.add(unclaimed)
    db.session.commit()
    # An unregistered firm should never have a RoundResult per the engine
    # wiring, but even if one somehow existed, it must still be excluded.
    make_result(unclaimed, 1)

    standings = cumulative_standings(world)
    assert len(standings) == 0  # unclaimed's phantom result doesn't count, and Nike never played


# --------------------------------------------------------------------------- #
# round_totals
# --------------------------------------------------------------------------- #

def test_round_totals_isolates_the_selected_round(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1, price=80)
    make_result(nike, 1, profit=1000, revenue=5000, units_sold_total=50)
    make_decision(nike, 2, price=95)
    make_result(nike, 2, profit=2000, revenue=6000, units_sold_total=60)

    round1 = round_totals(world, 1)
    assert round1[0]["profit"] == 1000
    assert round1[0]["price"] == 80

    round2 = round_totals(world, 2)
    assert round2[0]["profit"] == 2000
    assert round2[0]["price"] == 95


def test_round_totals_sorted_by_that_rounds_profit(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, profit=100)
    make_decision(adidas, 1)
    make_result(adidas, 1, profit=5000)

    rows = round_totals(world, 1)
    assert [row["firm"].team_name for row in rows] == ["Adidas", "Nike"]


# --------------------------------------------------------------------------- #
# market_shares_for_round
# --------------------------------------------------------------------------- #

def test_market_shares_sum_to_100_percent(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, units_sold_total=30)
    make_decision(adidas, 1)
    make_result(adidas, 1, units_sold_total=70)

    shares = market_shares_for_round(world, 1)
    total_pct = sum(row["share_pct"] for row in shares)
    assert abs(total_pct - 100.0) < 1e-9
    by_name = {row["firm"].team_name: row["share_pct"] for row in shares}
    assert abs(by_name["Nike"] - 30.0) < 1e-9
    assert abs(by_name["Adidas"] - 70.0) < 1e-9


def test_market_shares_all_zero_when_nobody_sold_anything(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    make_decision(nike, 1)
    make_result(nike, 1, units_sold_total=0)

    shares = market_shares_for_round(world, 1)
    assert shares[0]["share_pct"] == 0


# --------------------------------------------------------------------------- #
# build_pie_gradient
# --------------------------------------------------------------------------- #

def test_pie_gradient_has_one_stop_per_share():
    gradient = build_pie_gradient([{"share_pct": 40}, {"share_pct": 60}])
    assert gradient.startswith("conic-gradient(")
    assert "0.0000% 40.0000%" in gradient
    assert "40.0000% 100.0000%" in gradient


def test_pie_gradient_handles_all_zero_shares():
    gradient = build_pie_gradient([{"share_pct": 0}, {"share_pct": 0}])
    assert gradient == "conic-gradient(#4A5A5E 0% 100%)"


def test_pie_gradient_handles_empty_list():
    assert build_pie_gradient([]) == "conic-gradient(#4A5A5E 0% 100%)"


# --------------------------------------------------------------------------- #
# segment_overview
# --------------------------------------------------------------------------- #

def test_segment_overview_relative_size_matches_constants(app):
    world = make_world()
    overview = segment_overview(world, None)
    total_buyers = sum(SEGMENT_BUYER_COUNT.values())
    for seg_row, seg_name in zip(overview, SEGMENTS):
        expected_pct = SEGMENT_BUYER_COUNT[seg_name] / total_buyers * 100
        assert abs(seg_row["relative_size_pct"] - expected_pct) < 1e-9


def test_segment_overview_before_any_round_shows_zero_and_no_leading_track(app):
    world = make_world()
    overview = segment_overview(world, None)
    for seg_row in overview:
        assert seg_row["units_sold_this_round"] == 0
        assert seg_row["leading_track"] is None


def test_segment_overview_leading_track_picks_highest_selling_track(app):
    world = make_world()
    budget_firm = make_firm(world, 1, "BudgetCo")
    premium_firm = make_firm(world, 2, "PremiumCo")
    make_decision(budget_firm, 1, track="Budget")
    make_result(budget_firm, 1, segment_units={"Low Income": 200})
    make_decision(premium_firm, 1, track="Premium")
    make_result(premium_firm, 1, segment_units={"Low Income": 50})

    overview = segment_overview(world, 1)
    low_income = next(s for s in overview if s["name"] == "Low Income")
    assert low_income["leading_track"] == "Budget"
    assert low_income["units_sold_this_round"] == 250


def test_segment_overview_no_leading_track_when_segment_had_zero_sales(app):
    world = make_world()
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1, track="Standard")
    make_result(firm, 1, segment_units={"Low Income": 100, "Wealthy": 0})

    overview = segment_overview(world, 1)
    wealthy = next(s for s in overview if s["name"] == "Wealthy")
    assert wealthy["leading_track"] is None
    assert wealthy["units_sold_this_round"] == 0


def test_segment_overview_before_any_round_has_no_consumer_surplus_data(app):
    world = make_world()
    overview = segment_overview(world, None)
    for seg_row in overview:
        assert seg_row["avg_consumer_surplus"] is None
        assert seg_row["unsold_buyers_pct"] == 0


def test_segment_overview_merges_in_consumer_surplus_data(app):
    world = make_world()
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1, track="Standard")
    make_result(firm, 1, segment_units={"Low Income": 100})
    make_segment_result(world, 1, "Low Income", avg_consumer_surplus=12.5, unsold_buyers_pct=30.0)
    make_segment_result(world, 1, "Wealthy", avg_consumer_surplus=None, unsold_buyers_pct=100.0)

    overview = segment_overview(world, 1)
    low_income = next(s for s in overview if s["name"] == "Low Income")
    wealthy = next(s for s in overview if s["name"] == "Wealthy")
    assert low_income["avg_consumer_surplus"] == 12.5
    assert low_income["unsold_buyers_pct"] == 30.0
    assert wealthy["avg_consumer_surplus"] is None
    assert wealthy["unsold_buyers_pct"] == 100.0


# --------------------------------------------------------------------------- #
# competitive_intel_rows
# --------------------------------------------------------------------------- #

def test_competitive_intel_rows_excludes_hidden_fields(app):
    world = make_world()
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1, price=85, track="Premium", ad_spend=2000, rd_spend=99999)
    make_result(firm, 1, quality_level=3, loan_outstanding_after=0)

    rows = competitive_intel_rows(world, 1)
    assert len(rows) == 1
    row = rows[0]
    forbidden_keys = {"plant_capacity", "cash", "cash_after", "cash_before", "rd_spend"}
    assert forbidden_keys.isdisjoint(row.keys())
    # Visible fields ARE present:
    assert row["team_name"] == "Nike"
    assert row["price"] == 85
    assert row["track"] == "Premium"
    assert row["quality_level"] == 3
    assert row["ad_spend"] == 2000
    assert "units_sold" in row
    assert "market_share_pct" in row


def test_competitive_intel_loan_flag_is_boolean_not_amount(app):
    world = make_world()
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1)
    make_result(firm, 1, loan_outstanding_after=440_000)

    rows = competitive_intel_rows(world, 1)
    assert rows[0]["carrying_debt"] is True
    assert "loan_outstanding_after" not in rows[0]
    assert 440_000 not in rows[0].values()


def test_competitive_intel_no_debt_flag_when_no_loan(app):
    world = make_world()
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1)
    make_result(firm, 1, loan_outstanding_after=0)

    rows = competitive_intel_rows(world, 1)
    assert rows[0]["carrying_debt"] is False


def test_competitive_intel_sorted_by_market_share_desc(app):
    world = make_world()
    nike = make_firm(world, 1, "Nike")
    adidas = make_firm(world, 2, "Adidas")
    make_decision(nike, 1)
    make_result(nike, 1, units_sold_total=20)
    make_decision(adidas, 1)
    make_result(adidas, 1, units_sold_total=80)

    rows = competitive_intel_rows(world, 1)
    assert [r["team_name"] for r in rows] == ["Adidas", "Nike"]
