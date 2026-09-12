"""
Teacher Dashboard: "Undo Last Round".

This reverses a whole class's financial state, so the tests here lean on the
two ways it could do real damage rather than on surface behaviour:
  1. restoring PART of a firm's state (see test_snapshot_fields_cover_...),
     which would leave a game subtly wrong rather than visibly broken;
  2. deleting a team's own submission, which would make undo destructive for
     exactly the groups it's supposed to rescue.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.blueprints.teacher import SNAPSHOT_FIELDS
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, RoundSnapshot, SegmentRoundResult, World

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


def teacher_login(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})


def create_world(client, name="Period 3", slots=1):
    teacher_login(client)
    client.post("/teacher/worlds", data={"name": name, "planned_firm_slots": str(slots)})
    return World.query.filter_by(name=name).first().id


def register_firm(client, world_id, slot_number, team_name, password="secret123"):
    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).first().id
    client.post(f"/register/{world_id}/{firm_id}", data={
        "team_name": team_name, "password": password, "avatar": "factory-01.png",
        "badge": "logo-01.png", "product_icon": "headphone-01.png",
    })
    return firm_id


def login_firm(client, world_id, slot_number, password="secret123"):
    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": password})


def submit_decision(client, **overrides):
    data = dict(price="80", production_qty="15000", ad_spend="0", rd_spend="0",
                track="Mid", plant_investment="0")
    data.update(overrides)
    return client.post("/firm/decisions", data=data)


def process(client, world_id):
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")


def firm_state(world_id, slot_number=1):
    firm = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).one()
    return {f: getattr(firm, f) for f in SNAPSHOT_FIELDS}


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #

def test_no_undo_button_before_any_round_is_processed(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    teacher_login(client)
    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Undo Round" not in body


def test_undo_with_nothing_to_undo_is_a_harmless_no_op(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    teacher_login(client)
    resp = client.post(f"/teacher/worlds/{world_id}/undo", follow_redirects=True)
    assert b"no processed round to undo" in resp.data
    assert World.query.get(world_id).current_round == 1


def test_undo_requires_teacher_login(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    client.get("/teacher/logout")

    resp = client.post(f"/teacher/worlds/{world_id}/undo", follow_redirects=True)
    assert b"Teacher login required" in resp.data
    assert RoundResult.query.filter_by(round_number=1).count() == 1


def test_undo_button_carries_a_confirmation_prompt(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)

    teacher_login(client)
    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Undo Round 1" in body
    assert "onsubmit" in body and "confirm(" in body
    assert "Undo Round 1?" in body
    assert "undone" in body


# --------------------------------------------------------------------------- #
# What it restores
# --------------------------------------------------------------------------- #

def test_undo_restores_every_snapshotted_financial_field(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, rd_spend="50000", ad_spend="25000")
    client.get("/logout")

    before = firm_state(world_id)
    process(client, world_id)
    assert firm_state(world_id) != before, "precondition: processing must change state"

    client.post(f"/teacher/worlds/{world_id}/undo")
    assert firm_state(world_id) == before


def test_undo_restores_capacity_bought_during_the_undone_round(client):
    # plant_capacity and pending_capacity_increase collapse into one stored
    # sum on RoundResult, so this is the case reverse-arithmetic would get
    # wrong. Snapshot restores both independently.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, plant_investment="100000")
    client.get("/logout")

    before = firm_state(world_id)
    process(client, world_id)

    firm = Firm.query.filter_by(world_id=world_id, slot_number=1).one()
    assert firm.pending_capacity_increase > 0, "precondition: expansion was bought"

    client.post(f"/teacher/worlds/{world_id}/undo")
    after = firm_state(world_id)
    assert after["plant_capacity"] == before["plant_capacity"]
    assert after["pending_capacity_increase"] == before["pending_capacity_increase"]


def test_undo_discards_that_rounds_market_outcome_data(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)

    assert RoundResult.query.filter_by(round_number=1).count() > 0
    assert SegmentRoundResult.query.filter_by(world_id=world_id, round_number=1).count() > 0

    client.post(f"/teacher/worlds/{world_id}/undo")

    assert RoundResult.query.filter_by(round_number=1).count() == 0
    assert SegmentRoundResult.query.filter_by(world_id=world_id, round_number=1).count() == 0


def test_undo_keeps_team_submissions_but_drops_generated_decisions(client):
    # A real submission is the team's own work and must survive so they can
    # adjust and resubmit. A no-show's synthesized row was produced BY the
    # processing being undone -- keeping it would make that team look like it
    # had already submitted, locking it out of the retry undo exists to give.
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, price="123")
    client.get("/logout")
    register_firm(client, world_id, 2, "Adidas")  # registers, never submits
    client.get("/logout")

    process(client, world_id)
    assert RoundDecision.query.filter_by(round_number=1, is_auto=True).count() == 1

    client.post(f"/teacher/worlds/{world_id}/undo")

    kept = RoundDecision.query.filter_by(round_number=1).all()
    assert len(kept) == 1
    assert kept[0].is_auto is False
    assert kept[0].price == 123


def test_a_team_can_resubmit_after_an_undo(client):
    # The end-to-end point of the feature.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, price="80")
    client.get("/logout")
    process(client, world_id)
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/undo")
    client.get("/teacher/logout")

    login_firm(client, world_id, 1)
    submit_decision(client, price="55")

    row = RoundDecision.query.filter_by(round_number=1).one()
    assert row.price == 55


# --------------------------------------------------------------------------- #
# Round counter / phase
# --------------------------------------------------------------------------- #

def test_undo_straight_after_processing_reopens_the_same_round(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    assert World.query.get(world_id).status == "transition"

    client.post(f"/teacher/worlds/{world_id}/undo")

    world = World.query.get(world_id)
    assert world.current_round == 1
    assert world.status == "collecting"


def test_undo_after_the_next_round_opened_walks_the_counter_back(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    client.post(f"/teacher/worlds/{world_id}/advance")  # open round 2
    assert World.query.get(world_id).current_round == 2

    client.post(f"/teacher/worlds/{world_id}/undo")

    world = World.query.get(world_id)
    assert world.current_round == 1
    assert world.status == "collecting"


def test_undo_reaches_only_the_newest_round_never_further_back(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")

    for _ in range(2):
        login_firm(client, world_id, 1)
        submit_decision(client)
        client.get("/logout")
        process(client, world_id)
        client.post(f"/teacher/worlds/{world_id}/advance")
        client.get("/teacher/logout")

    teacher_login(client)
    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Undo Round 2" in body
    assert "Undo Round 1" not in body

    client.post(f"/teacher/worlds/{world_id}/undo")
    assert RoundResult.query.filter_by(round_number=2).count() == 0
    assert RoundResult.query.filter_by(round_number=1).count() == 1, "round 1 untouched"


def test_reprocessing_after_an_undo_makes_only_the_new_round_undoable(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    client.post(f"/teacher/worlds/{world_id}/undo")

    # Process the same round again -- the snapshot must be rewritten, not
    # stacked, or a second undo would restore stale state.
    client.post(f"/teacher/worlds/{world_id}/advance")
    assert RoundSnapshot.query.filter_by(world_id=world_id, round_number=1).count() == 1

    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Undo Round 1" in body


# --------------------------------------------------------------------------- #
# The guard that matters most
# --------------------------------------------------------------------------- #

def test_snapshot_fields_cover_everything_processing_mutates(client):
    # The one way undo could corrupt a game rather than restore it: a new
    # Firm field starts being written during processing and nobody adds it to
    # SNAPSHOT_FIELDS, so undo silently leaves it at its post-round value.
    # Diff a firm's full column set across a real round instead of trusting
    # the list to stay in sync by hand.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, rd_spend="40000", ad_spend="30000", plant_investment="100000")
    client.get("/logout")

    identity_columns = {
        "id", "world_id", "slot_number", "team_name", "password_hash",
        "avatar", "badge", "product_icon", "bot_profile", "created_at",
    }

    firm = Firm.query.filter_by(world_id=world_id, slot_number=1).one()
    columns = [c.name for c in Firm.__table__.columns]
    before = {c: getattr(firm, c) for c in columns}

    process(client, world_id)

    firm = Firm.query.filter_by(world_id=world_id, slot_number=1).one()
    changed = {c for c in columns if getattr(firm, c) != before[c]}

    untracked = changed - set(SNAPSHOT_FIELDS) - identity_columns
    assert not untracked, (
        f"_process_current_round mutates {sorted(untracked)}, which Undo Last Round "
        f"would not restore -- add to SNAPSHOT_FIELDS"
    )


def test_deleting_a_world_cleans_up_its_snapshots(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    assert RoundSnapshot.query.filter_by(world_id=world_id).count() == 1

    client.post(f"/teacher/worlds/{world_id}/delete")
    assert RoundSnapshot.query.filter_by(world_id=world_id).count() == 0


def test_normal_play_still_blocks_editing_a_submission(client):
    # The reopen window is narrow on purpose. Outside an undo, "one
    # submission per round, no edits" must still hold.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, price="80")
    resp = submit_decision(client, price="55")

    assert RoundDecision.query.filter_by(round_number=1).one().price == 80
    assert World.query.get(world_id).reopened_round is None


def test_reopen_window_closes_once_the_round_is_processed_again(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client)
    client.get("/logout")
    process(client, world_id)
    client.post(f"/teacher/worlds/{world_id}/undo")
    assert World.query.get(world_id).reopened_round == 1

    client.post(f"/teacher/worlds/{world_id}/advance")  # process round 1 again
    assert World.query.get(world_id).reopened_round is None


def test_resubmitting_never_creates_a_second_decision_row(client):
    # RoundDecision is "exactly one row per firm per round" -- a reopened
    # round must UPDATE the kept row, not add a duplicate.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    login_firm(client, world_id, 1)
    submit_decision(client, price="80")
    client.get("/logout")
    process(client, world_id)
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/undo")
    client.get("/teacher/logout")

    login_firm(client, world_id, 1)
    submit_decision(client, price="55")
    submit_decision(client, price="42")

    rows = RoundDecision.query.filter_by(round_number=1).all()
    assert len(rows) == 1
    assert rows[0].price == 42
