import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.constants import SEGMENTS
from app.csv_export import build_export_rows, export_filename, rows_to_csv_string
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


def make_world(planned_firm_slots=2, **overrides):
    w = World(name="Period 3", game_code="ABC123", planned_firm_slots=planned_firm_slots, **overrides)
    db.session.add(w)
    db.session.commit()
    return w


def make_firm(world, slot_number, team_name, **overrides):
    f = Firm(
        world_id=world.id, slot_number=slot_number, team_name=team_name,
        cash=1_000_000, plant_capacity=45_000, **overrides,
    )
    db.session.add(f)
    db.session.commit()
    return f


def make_decision(firm, round_number, **overrides):
    base = dict(price=80.0, production_qty=45_000, ad_spend=0, rd_spend=0, track="Mid", is_auto=False)
    base.update(overrides)
    d = RoundDecision(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(d)
    db.session.commit()
    return d


def make_result(firm, round_number, **overrides):
    base = dict(
        units_sold_by_segment={"Low Income": 100}, units_sold_total=100, revenue=8000,
        production_cost=2250, fixed_cost=100_000, ad_cost=0, rd_cost=0, celebrity_cost=0,
        plant_investment_cost=0, total_cost=102_250, profit=-94_250,
        cash_before=1_000_000, cash_after=905_750, quality_level=1, ad_level=1, plant_capacity=45_000,
        is_bankrupt=False,
    )
    base.update(overrides)
    r = RoundResult(firm_id=firm.id, round_number=round_number, **base)
    db.session.add(r)
    db.session.commit()
    return r


# --------------------------------------------------------------------------- #
# build_export_rows
# --------------------------------------------------------------------------- #

def test_empty_world_produces_no_rows(app):
    world = make_world()
    assert build_export_rows(world) == []


def test_single_result_row_has_expected_fields(app):
    world = make_world(planned_firm_slots=1)
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1, price=85.0, track="Premium")
    make_result(firm, 1, revenue=8500, profit=-100)

    rows = build_export_rows(world)
    assert len(rows) == 1
    row = rows[0]
    assert row["Team Name"] == "Nike"
    assert row["Firm Slot"] == 1
    assert row["Round"] == 1
    assert row["Price"] == 85.0
    assert row["Tier"] == "Premium"
    assert row["Revenue"] == 8500
    assert row["Profit"] == -100
    assert row["Units Sold - Low Income"] == 100
    # Segments never sold to should still be present, defaulted to 0.
    for seg in SEGMENTS:
        if seg != "Low Income":
            assert row[f"Units Sold - {seg}"] == 0


def test_rows_sorted_by_slot_then_round_regardless_of_insertion_order(app):
    world = make_world(planned_firm_slots=2)
    firm1 = make_firm(world, 1, "Nike")
    firm2 = make_firm(world, 2, "Adidas")

    # Insert deliberately out of order.
    make_decision(firm2, 1)
    make_result(firm2, 1)
    make_decision(firm1, 2)
    make_result(firm1, 2)
    make_decision(firm1, 1)
    make_result(firm1, 1)
    make_decision(firm2, 2)
    make_result(firm2, 2)

    rows = build_export_rows(world)
    ordering = [(r["Firm Slot"], r["Round"]) for r in rows]
    assert ordering == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_bankrupt_firm_row_included_and_marked(app):
    world = make_world(planned_firm_slots=1)
    firm = make_firm(world, 1, "Nike", bankrupt=True)
    make_result(firm, 3, is_bankrupt=True, units_sold_total=0, units_sold_by_segment={})

    rows = build_export_rows(world)
    assert len(rows) == 1
    assert rows[0]["Bankrupt This Round"] is True
    # No decision was ever made for a frozen bankrupt round -- fields blank, not a crash.
    assert rows[0]["Price"] == ""


def test_result_without_matching_decision_does_not_crash(app):
    # Defensive case: a RoundResult should always have a paired
    # RoundDecision in practice, but the export must not explode if not.
    world = make_world(planned_firm_slots=1)
    firm = make_firm(world, 1, "Nike")
    make_result(firm, 1)
    rows = build_export_rows(world)
    assert rows[0]["Price"] == ""
    assert rows[0]["Tier"] == ""


# --------------------------------------------------------------------------- #
# rows_to_csv_string
# --------------------------------------------------------------------------- #

def test_csv_string_has_header_only_for_empty_rows(app):
    csv_text = rows_to_csv_string([])
    lines = csv_text.strip("\r\n").split("\r\n")
    assert len(lines) == 1
    assert "Team Name" in lines[0]


def test_csv_string_round_trips_a_real_row(app):
    world = make_world(planned_firm_slots=1)
    firm = make_firm(world, 1, "Nike")
    make_decision(firm, 1)
    make_result(firm, 1)
    rows = build_export_rows(world)
    csv_text = rows_to_csv_string(rows)

    import csv
    import io
    reader = csv.DictReader(io.StringIO(csv_text))
    parsed = list(reader)
    assert len(parsed) == 1
    assert parsed[0]["Team Name"] == "Nike"
    assert parsed[0]["Round"] == "1"


def test_export_filename_is_filesystem_safe():
    world = World(name="Period 3 / A.M.!", game_code="XYZ789", planned_firm_slots=1)
    filename = export_filename(world)
    assert filename.endswith("_XYZ789_export.csv")
    assert "/" not in filename and "!" not in filename
