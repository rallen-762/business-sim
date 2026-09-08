"""
Integration tests driving the full stack through Flask's test client:
teacher creates a World -> students register -> submit decisions -> teacher
advances the round -> results land in the DB and render correctly. This is
the "does the DB<->engine wiring actually work" check that the unit tests
for engine.py/models.py individually can't catch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, World

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


def create_world(client, name="Period 3", slots=2):
    teacher_login(client)
    client.post("/teacher/worlds", data={"name": name, "planned_firm_slots": str(slots)})
    with client.application.app_context():
        return World.query.filter_by(name=name).first().id


def register_firm(client, world_id, slot_number, team_name, password="secret123", avatar="building-a.png"):
    with client.application.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).first().id
    client.post(
        f"/register/{world_id}/{firm_id}",
        data={"team_name": team_name, "password": password, "avatar": avatar},
    )
    return firm_id


def submit_decision(client, follow_redirects=False, **overrides):
    data = dict(
        price="80", production_qty="45000", ad_spend="0", rd_spend="0",
        track="Standard", plant_investment="0",
    )
    data.update(overrides)
    return client.post("/firm/decisions", data=data, follow_redirects=follow_redirects)


# --------------------------------------------------------------------------- #
# Teacher: world creation
# --------------------------------------------------------------------------- #

def test_teacher_login_wrong_password_rejected(client):
    resp = client.post("/teacher/login", data={"password": "nope"}, follow_redirects=True)
    assert b"Wrong teacher password" in resp.data


def test_create_world_pre_creates_firm_slots(app, client):
    world_id = create_world(client, slots=3)
    with app.app_context():
        firms = Firm.query.filter_by(world_id=world_id).all()
        assert len(firms) == 3
        assert all(f.team_name is None and not f.is_registered for f in firms)
        assert {f.slot_number for f in firms} == {1, 2, 3}


def test_created_world_starts_in_collecting_status_round_1(app, client):
    world_id = create_world(client)
    with app.app_context():
        world = World.query.get(world_id)
        assert world.status == "collecting"
        assert world.current_round == 1


# --------------------------------------------------------------------------- #
# Student registration / login
# --------------------------------------------------------------------------- #

def test_register_then_redirected_to_firm_dashboard(client):
    world_id = create_world(client)
    with client.application.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    resp = client.post(
        f"/register/{world_id}/{firm_id}",
        data={"team_name": "Nike", "password": "secret123", "avatar": "building-a.png"},
        follow_redirects=True,
    )
    assert b"Nike" in resp.data


def test_duplicate_team_name_within_world_rejected(client):
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    client.get("/logout")
    resp = client.post(
        f"/register/{world_id}/{2}",
        data={"team_name": "Nike", "password": "whatever", "avatar": "building-a.png"},
        follow_redirects=True,
    )
    # Should redirect back to the register form with a flash, not create a second Nike.
    assert b"already taken" in resp.data


def test_login_with_wrong_password_rejected(client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike", password="right-pw")
    client.get("/logout")
    resp = client.post(f"/login/{world_id}/{firm_id}", data={"password": "wrong-pw"}, follow_redirects=True)
    assert b"Wrong password" in resp.data


def test_login_with_correct_password_reaches_dashboard(client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike", password="right-pw")
    client.get("/logout")
    resp = client.post(f"/login/{world_id}/{firm_id}", data={"password": "right-pw"}, follow_redirects=True)
    assert b"Nike" in resp.data


# --------------------------------------------------------------------------- #
# Firm decision submission
# --------------------------------------------------------------------------- #

def test_submitting_decision_persists_it(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="90", production_qty="40000")
    with app.app_context():
        d = RoundDecision.query.filter_by(firm_id=firm_id, round_number=1).first()
        assert d is not None
        assert d.price == 90.0
        assert d.production_qty == 40000
        assert d.is_auto is False


def test_cannot_submit_decision_twice(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    resp = submit_decision(client, follow_redirects=True)
    assert b"already submitted" in resp.data


def test_bankrupt_firm_cannot_submit(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.get(firm_id)
        firm.bankrupt = True
        db.session.commit()
    resp = submit_decision(client, follow_redirects=True)
    assert b"bankrupt" in resp.data
    with app.app_context():
        assert RoundDecision.query.filter_by(firm_id=firm_id).count() == 0


# --------------------------------------------------------------------------- #
# Full round-advance flow (the actual DB<->engine bridge)
# --------------------------------------------------------------------------- #

def test_advancing_round_processes_all_firms_and_creates_results(app, client):
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="45000")
    client.get("/logout")

    # Firm 2 REGISTERS but never submits a decision -- exercises the
    # non-submission auto-decision path using its own bootstrap default
    # price/track. (An unregistered slot is a separate case, covered by
    # test_unregistered_slot_does_not_compete_and_gets_no_result_row --
    # that one must NOT get a result row at all.)
    register_firm(client, world_id, 2, "Adidas")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # collecting -> transition

    with app.app_context():
        world = World.query.get(world_id)
        assert world.status == "transition"
        assert world.current_round == 1  # still round 1 -- opening round 2 is a separate step

        results = RoundResult.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).all()
        assert len(results) == 2  # both registered firms get a result row, submitted or not

        decisions = RoundDecision.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).all()
        assert len(decisions) == 2
        auto_decisions = [d for d in decisions if d.is_auto]
        assert len(auto_decisions) == 1  # only the non-submitting firm got a synthesized decision


def test_firm_dashboard_shows_just_processed_round_result_during_transition(client):
    # Regression test: world.current_round is NOT incremented until the
    # teacher opens the next round, so during "transition" the just-
    # processed round's result lives at current_round itself, not
    # current_round - 1. Caught live via a real HTTP smoke test.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="45000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # collecting -> transition
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/firm")
    assert b"Round 1 Results" in resp.data
    assert b"Units Sold" in resp.data


def test_unregistered_slot_does_not_compete_and_gets_no_result_row(app, client):
    # Regression test: caught live via smoke test -- an unclaimed slot was
    # silently competing in demand-pull with a blank team name and consuming
    # market share. An unregistered Firm must be fully excluded from
    # process_round, not just displayed oddly.
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="45000")
    # Firm 2 is never registered at all.
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        results = RoundResult.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).all()
        assert len(results) == 1  # only the registered firm gets a result row
        decisions = RoundDecision.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).all()
        assert len(decisions) == 1  # no auto-decision synthesized for the unclaimed slot either


def test_unregistered_slot_does_not_dilute_a_solo_registered_firms_market_share(app, client):
    # A single registered firm with no real competition should capture the
    # full segment, not have its share diluted by a phantom unregistered
    # "competitor" using the bootstrap default price/track.
    world_id = create_world(client, slots=3)
    register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.filter_by(world_id=world_id, slot_number=1).first()
        firm.plant_capacity = 1_000_000  # so production isn't the binding constraint
        db.session.commit()
    submit_decision(client, price="80", production_qty="1000000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        r = RoundResult.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).first()
        # With zero real competitors, every buyer in every segment should
        # convert (raw demand is unconstrained since capacity is huge).
        from app.constants import SEGMENT_BUYER_COUNT
        assert r.units_sold_total == sum(SEGMENT_BUYER_COUNT.values())


def test_opening_next_round_increments_round_and_resets_status(app, client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # -> transition
    client.post(f"/teacher/worlds/{world_id}/advance")  # -> next round, collecting

    with app.app_context():
        world = World.query.get(world_id)
        assert world.status == "collecting"
        assert world.current_round == 2


def test_round_10_completion_marks_world_complete_not_transition(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")

    teacher_login(client)
    with app.app_context():
        world = World.query.get(world_id)
        world.current_round = 10
        db.session.commit()

    client.get("/logout")
    # Log back in as the firm to submit round 10's decision.
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        world = World.query.get(world_id)
        assert world.status == "complete"


def test_non_submitting_firm_result_matches_engine_bootstrap_default_price(app, client):
    # Directly checks that a firm which never once submitted (no last_price
    # of its own) gets processed using the confirmed bootstrap default,
    # rather than crashing or silently using None.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    # Never submits.
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        d = RoundDecision.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).first()
        assert d.is_auto is True
        assert d.price == 80.00
        assert d.track == "Standard"


def test_bankrupt_firm_gets_frozen_result_row_every_round(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.get(firm_id)
        firm.bankrupt = True
        db.session.commit()

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        r = RoundResult.query.filter_by(firm_id=firm_id, round_number=1).first()
        assert r is not None
        assert r.is_bankrupt is True
        assert r.units_sold_total == 0


# --------------------------------------------------------------------------- #
# Market dashboard / team lookup smoke tests
# --------------------------------------------------------------------------- #

def test_market_dashboard_shows_no_data_message_before_any_round_processed(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/market")
    assert b"No rounds have been processed" in resp.data


def test_market_dashboard_shows_latest_round_after_processing(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/market")
    assert b"Nike" in resp.data


def test_team_lookup_shows_decision_and_result_history(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}/lookup?firm_id={firm_id}")
    assert b"Nike" in resp.data
    assert b"Round Results" in resp.data


# --------------------------------------------------------------------------- #
# CSV export route
# --------------------------------------------------------------------------- #

def test_delete_world_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.post(f"/teacher/worlds/{world_id}/delete", follow_redirects=True)
    assert b"Teacher login required" in resp.data
    with client.application.app_context():
        assert World.query.get(world_id) is not None  # untouched


def test_delete_world_cascades_everything(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # creates a RoundResult too

    resp = client.post(f"/teacher/worlds/{world_id}/delete", follow_redirects=True)
    assert b"permanently deleted" in resp.data

    with app.app_context():
        assert World.query.get(world_id) is None
        assert Firm.query.get(firm_id) is None
        assert RoundDecision.query.filter_by(firm_id=firm_id).count() == 0
        assert RoundResult.query.filter_by(firm_id=firm_id).count() == 0


def test_delete_nonexistent_world_returns_404(client):
    teacher_login(client)
    resp = client.post("/teacher/worlds/99999/delete")
    assert resp.status_code == 404


def test_export_csv_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.get(f"/teacher/worlds/{world_id}/export.csv", follow_redirects=True)
    assert b"Teacher login required" in resp.data


def test_export_csv_returns_downloadable_csv_with_data(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="45000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    resp = client.get(f"/teacher/worlds/{world_id}/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    body = resp.data.decode("utf-8")
    assert "Team Name" in body  # header row
    assert "Nike" in body  # data row


def test_export_csv_works_before_any_round_is_processed(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    client.get("/logout")

    teacher_login(client)
    resp = client.get(f"/teacher/worlds/{world_id}/export.csv")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    lines = body.strip("\r\n").split("\r\n")
    assert len(lines) == 1  # header only, no crash on an empty world
