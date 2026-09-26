"""Market Shifts: the scripted-events layer over an otherwise ordinary world.

The claims worth pinning are the ones that would fail silently: that a world
WITHOUT events is bit-for-bit unchanged, that a demand event's size and
direction never reach the student's screen, and that an already-played round
keeps being described by the ceilings it was actually played under.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.constants import (
    DEMAND_ORDER_ONE, DEMAND_ORDER_TWO, SUPPLY_ORDER_ONE, SUPPLY_ORDER_TWO,
    MARKET_EVENTS, event_for_round, wtp_multiplier_for_round,
)
from app.engine import FirmDecision, FirmState, process_round
from app.extensions import db
from app.models import World

TEACHER_PASSWORD = "test-teacher-pw"


@pytest.fixture
def app():
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "TESTING": True,
        "TEACHER_PASSWORD": TEACHER_PASSWORD,
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def two_firms():
    def state(fid):
        return FirmState(firm_id=fid, cash=1_000_000, plant_capacity=45_000,
                         pending_capacity_increase=0, cumulative_rd_spend=0.0,
                         cumulative_ad_spend=0.0, loan_outstanding=0.0,
                         loan_used_ever=False, bankrupt=False)

    states = {1: state(1), 2: state(2)}
    decisions = {
        1: FirmDecision(firm_id=1, price=80.0, production_qty=30_000, ad_spend=0.0,
                        rd_spend=0.0, track="Mid", celebrity_on=False, plant_investment=0),
        2: FirmDecision(firm_id=2, price=95.0, production_qty=30_000, ad_spend=0.0,
                        rd_spend=0.0, track="Premium", celebrity_on=False, plant_investment=0),
    }
    return states, decisions


# --- the schedule ---------------------------------------------------------- #

def test_the_schedule_is_exactly_the_four_briefed_events():
    assert [(e.round_number, e.kind) for e in MARKET_EVENTS] == [
        (2, "supply"), (4, "supply"), (6, "demand"), (8, "demand")]
    assert event_for_round(2).cost_multiplier == pytest.approx(1 + SUPPLY_ORDER_ONE)
    assert event_for_round(4).cost_multiplier == pytest.approx(1 - SUPPLY_ORDER_TWO)
    assert event_for_round(6).wtp_multiplier == pytest.approx(1 + DEMAND_ORDER_ONE)
    assert event_for_round(8).wtp_multiplier == pytest.approx(1 - DEMAND_ORDER_TWO)


def test_no_event_on_any_other_round_and_none_at_all_when_disabled():
    for r in (1, 3, 5, 7, 9, 10):
        assert event_for_round(r) is None
    for r in range(1, 11):
        assert event_for_round(r, events_enabled=False) is None


def test_each_event_moves_exactly_one_lever():
    """A supply event must not touch demand, or the visibility rules stop
    meaning anything: a demand shift hidden inside a supply event would reach
    students through their sales with no banner explaining it."""
    for event in MARKET_EVENTS:
        if event.is_supply:
            assert event.wtp_multiplier == 1.0, event.key
            assert event.cost_multiplier != 1.0, event.key
        else:
            assert event.cost_multiplier == 1.0, event.key
            assert event.wtp_multiplier != 1.0, event.key


# --- the engine ------------------------------------------------------------ #

def test_a_world_without_events_is_unchanged():
    """The whole design rests on this: no event means the identical round."""
    baseline = process_round(*two_firms())
    states, decisions = two_firms()
    with_none = process_round(states, decisions, None)
    for fid in (1, 2):
        assert with_none[fid].units_sold_total == baseline[fid].units_sold_total
        assert with_none[fid].profit == baseline[fid].profit
        assert with_none[fid].production_cost == baseline[fid].production_cost


def test_supply_event_moves_cost_and_leaves_units_alone():
    baseline = process_round(*two_firms())
    states, decisions = two_firms()
    shifted = process_round(states, decisions, event_for_round(2))
    for fid in (1, 2):
        assert shifted[fid].units_sold_total == baseline[fid].units_sold_total, "demand must not move"
        assert shifted[fid].production_cost == pytest.approx(
            baseline[fid].production_cost * (1 + SUPPLY_ORDER_ONE))


def test_cheaper_supply_event_cuts_cost_and_lifts_profit():
    baseline = process_round(*two_firms())
    states, decisions = two_firms()
    shifted = process_round(states, decisions, event_for_round(4))
    for fid in (1, 2):
        assert shifted[fid].production_cost == pytest.approx(
            baseline[fid].production_cost * (1 - SUPPLY_ORDER_TWO))
        assert shifted[fid].profit > baseline[fid].profit


def test_recession_costs_demand_and_a_matching_price_cut_wins_it_back():
    """The lesson the brief asks for: ignoring the recession loses ground, and
    a deliberate price cut the size of the event restores the buyer pool.

    Asserted on units DEMANDED, not sold: with only two firms in the pool both
    sell out their production run either way, so sales alone would hide the
    whole effect. That is the same capacity ceiling that will mute the Round 6
    event for any team already running flat out."""
    baseline = process_round(*two_firms())
    states, decisions = two_firms()
    ignored = process_round(states, decisions, event_for_round(8))
    assert ignored[1].units_demanded_total < baseline[1].units_demanded_total

    states, decisions = two_firms()
    for d in decisions.values():
        d.price *= (1 - DEMAND_ORDER_TWO)
    reacted = process_round(states, decisions, event_for_round(8))
    assert reacted[1].units_demanded_total > ignored[1].units_demanded_total
    assert reacted[1].units_demanded_total == pytest.approx(
        baseline[1].units_demanded_total, rel=0.05)


def test_positive_demand_event_lifts_demand():
    baseline = process_round(*two_firms())
    states, decisions = two_firms()
    shifted = process_round(states, decisions, event_for_round(6))
    assert shifted[1].units_demanded_total > baseline[1].units_demanded_total


def test_good_news_pays_a_capacity_bound_firm_only_if_it_raises_price():
    """An outward demand shift is a margin opportunity, not just a volume one.
    A firm already selling every unit it can build gains nothing by holding
    price -- and a great deal by raising it, because the shift widens the pool
    that can still afford the higher price. This is the Round 6 lesson."""
    def at(price, event):
        states, decisions = two_firms()
        for d in decisions.values():
            d.production_qty = 45_000
        decisions[1].price = price
        return process_round(states, decisions, event)[1]

    baseline = at(80.0, None)
    held = at(80.0, event_for_round(6))
    assert held.units_sold_total == baseline.units_sold_total == 45_000, "capacity-bound both ways"
    assert held.profit == baseline.profit, "holding price captures none of the good news"

    raised = at(94.0, event_for_round(6))
    assert raised.units_sold_total == 45_000, "still sells out at the higher price"
    assert raised.profit > held.profit * 1.4, "raising into the shift is worth real money"


# --- the flag -------------------------------------------------------------- #

def test_new_worlds_default_to_no_events(app):
    world = World(name="w", game_code="ABC123", planned_firm_slots=1)
    db.session.add(world)
    db.session.commit()
    assert world.events_enabled is False


def test_market_shifts_entry_creates_an_events_world_and_sandbox_does_not(client, app):
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    client.post("/sandbox/new", data={"team_name": "Plain"})
    shifts = World.query.filter(World.name.like("%Shifty%")).one()
    plain = World.query.filter(World.name.like("%Plain%")).one()
    assert shifts.events_enabled is True
    assert plain.events_enabled is False, "the existing sandbox must be untouched"
    assert shifts.mode == plain.mode == "sandbox", "same world shape, same engine"


def test_login_page_offers_the_new_mode(client):
    page = client.get("/login").data.decode()
    assert "Market Shifts" in page
    assert "/sandbox/market-shifts" in page


# --- the visibility rules -------------------------------------------------- #

def test_demand_magnitude_never_reaches_the_dashboard(client, app):
    """The brief's hardest rule. A student may see the banner and the title,
    and nothing stating how big the shift is or which way it went."""
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    world = World.query.filter(World.name.like("%Shifty%")).one()
    world.current_round = 8
    db.session.commit()

    page = client.get("/firm").data.decode()
    assert "A Recession Has Struck" in page, "the title is meant to show"

    # Scope the check to the event block: the rest of the dashboard is full of
    # unrelated percentages (bar widths, "1/10" quality) that mean nothing here.
    start = page.index("event-banner")
    block = page[start:page.index("</div>", page.index("event-banner-note"))]
    assert "%" not in block, f"the event block states a magnitude: {block!r}"
    for leak in ("0.9", "decrease", "negative", "fall", "drop", "less"):
        assert leak not in block.lower(), f"demand event leaked direction via {leak!r}"

    # And nothing anywhere may hand back the multiplier itself.
    assert "wtp_multiplier" not in page and "0.9" not in page


def test_the_live_reach_readout_is_gone_in_market_shifts_only(client, app):
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    shifts_page = client.get("/firm").data.decode()
    assert 'id="reach-bar-fill"' not in shifts_page

    client.post("/sandbox/new", data={"team_name": "Plain"})
    plain_page = client.get("/firm").data.decode()
    assert 'id="reach-bar-fill"' in plain_page, "sandbox keeps its readout"


def test_supply_event_is_allowed_to_be_transparent(client, app):
    """Supply events are the firm's own books, so the banner says so outright."""
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    world = World.query.filter(World.name.like("%Shifty%")).one()
    world.current_round = 2
    db.session.commit()
    page = client.get("/firm").data.decode()
    assert "The Cost of Metal Has Increased" in page
    assert "unit cost has changed" in page


def test_banner_shows_once_then_becomes_a_chip(client, app):
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    world = World.query.filter(World.name.like("%Shifty%")).one()
    world.current_round = 2
    db.session.commit()
    first = client.get("/firm").data.decode()
    assert "event-banner" in first and "MARKET NEWS" in first
    second = client.get("/firm").data.decode()
    assert "event-chip" in second and "event-banner" not in second
    assert "The Cost of Metal Has Increased" in second, "the title persists all round"


def test_a_round_without_an_event_shows_no_banner(client, app):
    client.post("/sandbox/market-shifts/new", data={"team_name": "Shifty"})
    world = World.query.filter(World.name.like("%Shifty%")).one()
    world.current_round = 3
    db.session.commit()
    page = client.get("/firm").data.decode()
    assert "event-banner" not in page and "event-chip" not in page


# --- the already-played round ---------------------------------------------- #

def test_a_played_round_keeps_the_ceilings_it_was_played_under():
    """The priced-out column is recomputed, not stored. Round 7 must still be
    described by round 7's ceilings after round 8's recession fires."""
    assert wtp_multiplier_for_round(7, True) == 1.0
    assert wtp_multiplier_for_round(8, True) == pytest.approx(1 - DEMAND_ORDER_TWO)
    assert wtp_multiplier_for_round(8, False) == 1.0


# --- the teacher's entry point --------------------------------------------- #

def teacher_client(app):
    c = app.test_client()
    c.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    return c


def test_teacher_can_create_a_market_shifts_period(app):
    c = teacher_client(app)
    c.post("/teacher/worlds", data={"name": "Period 3", "planned_firm_slots": "2",
                                    "events_enabled": "yes"})
    world = World.query.filter_by(name="Period 3").one()
    assert world.events_enabled is True
    assert world.mode == "classroom", "still an ordinary teacher-run period"


def test_a_teacher_world_has_no_events_unless_asked(app):
    c = teacher_client(app)
    c.post("/teacher/worlds", data={"name": "Period 4", "planned_firm_slots": "2"})
    assert World.query.filter_by(name="Period 4").one().events_enabled is False


def test_market_shifts_is_offered_inside_the_sandbox_card(client):
    page = client.get("/login").data.decode()
    assert "Sandbox Mode Market Shifts" in page
    assert "/sandbox/market-shifts" in page
    assert "login-panel-shifts" not in page, "it is no longer its own card"


# --- supply transparency: the shown cost must be the charged cost ---------- #

def playing_firm(client, app, round_number):
    client.post("/sandbox/market-shifts/new", data={"team_name": "CostCheck"})
    world = World.query.filter(World.name.like("%CostCheck%")).one()
    world.current_round = round_number
    db.session.commit()
    return world


def test_the_cost_line_shows_the_event_adjusted_unit_cost(client, app):
    """The brief makes supply events fully transparent: the number lands in
    the firm's own cost line. It was being charged by the engine and never
    shown, so a team priced against a cost that was already wrong."""
    playing_firm(client, app, 2)  # +8% metal costs
    page = client.get("/firm").data.decode()
    assert "$54.00" in page, "round 2 must show the raised unit cost"
    assert "(Mid ($50.00/unit)" not in page


def test_a_cheaper_supply_event_shows_the_lower_cost(client, app):
    playing_firm(client, app, 4)  # -18% microchip
    assert "$41.00" in client.get("/firm").data.decode()


def test_a_demand_event_leaves_the_cost_line_alone(client, app):
    """A demand event must not move costs -- that would leak it into a number
    the student can read directly."""
    playing_firm(client, app, 8)
    assert "$50.00" in client.get("/firm").data.decode()


def test_affordability_check_uses_the_event_cost(client, app):
    """Server-side spend validation must use the same unit cost the engine
    will charge, or a team is cleared to commit to a plan it cannot pay for
    and gets pushed into a loan it never chose."""
    world = playing_firm(client, app, 2)
    # 20,000 units at the base $50 is exactly $1,000,000 -- affordable before
    # the event, $1,080,000 and NOT affordable with it.
    page = client.post("/firm/decisions", data={
        "price": "80", "production_qty": "20000", "ad_spend": "0", "rd_spend": "0",
        "track": "Mid", "plant_investment": "0"}, follow_redirects=True).data.decode()
    assert "only have" in page, "the raised cost must be enforced, not just displayed"
