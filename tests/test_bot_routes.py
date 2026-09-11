"""
Integration tests for bot assignment/removal routes and bot participation
in round processing, driven through Flask's test client (same style as
test_routes.py).
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


def create_world(client, name="Period 3", slots=4):
    teacher_login(client)
    client.post("/teacher/worlds", data={"name": name, "planned_firm_slots": str(slots)})
    with client.application.app_context():
        return World.query.filter_by(name=name).first().id


def first_unclaimed_firm_id(app, world_id):
    with app.app_context():
        return Firm.query.filter_by(world_id=world_id, team_name=None).order_by(Firm.slot_number).first().id


# --------------------------------------------------------------------------- #
# Assign / reassign / remove
# --------------------------------------------------------------------------- #

def test_assign_bot_claims_an_unclaimed_slot(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)

    resp = client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "elite"})
    assert resp.status_code == 302

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile == "elite"
        assert firm.is_registered is True
        assert "Elite" in firm.team_name


def test_assign_bot_rejects_invalid_profile(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)

    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "not-a-profile"})

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile is None
        assert firm.is_registered is False


def test_assign_bot_refuses_a_human_claimed_slot(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/register/{world_id}/{firm_id}", data={
        "team_name": "Real Team", "password": "secret123", "avatar": "factory-01.png", "badge": "logo-01.png",
    })

    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "elite"})

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile is None
        assert firm.team_name == "Real Team"


def test_reassign_bot_updates_profile_and_team_name(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "underbidder"})

    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "elite"})

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile == "elite"
        assert "Elite" in firm.team_name
        assert "Underbidder" not in firm.team_name


def test_assign_bot_gives_it_a_random_avatar_from_the_real_pool(app, client):
    from app.avatars import AVATAR_CHOICES

    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "underbidder"})

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.avatar in AVATAR_CHOICES


def test_reassigning_a_bot_keeps_its_original_avatar(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "underbidder"})

    with app.app_context():
        original_avatar = db.session.get(Firm, firm_id).avatar

    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "marketing"})

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.avatar == original_avatar  # rolled once, not re-rolled on reassignment


def test_remove_bot_reverts_to_unclaimed_but_keeps_history(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "underbidder"})
    client.post(f"/teacher/worlds/{world_id}/advance")  # process round 1 -- bot auto-submits

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        cash_after_round_1 = firm.cash
        assert cash_after_round_1 != 1_000_000  # something actually happened

    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot/remove")

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile is None
        assert firm.team_name is None
        assert firm.is_registered is False
        assert firm.cash == cash_after_round_1  # history/state untouched
        assert RoundResult.query.filter_by(firm_id=firm_id).count() == 1  # past round kept


def test_remove_bot_on_a_non_bot_slot_is_a_no_op(app, client):
    world_id = create_world(client)
    firm_id = first_unclaimed_firm_id(app, world_id)

    resp = client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot/remove")
    assert resp.status_code == 302

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.bot_profile is None
        assert firm.is_registered is False


# --------------------------------------------------------------------------- #
# Bot participation in round processing
# --------------------------------------------------------------------------- #

def test_bot_auto_submits_every_round_without_any_human_input(app, client):
    world_id = create_world(client, slots=2)
    with app.app_context():
        firm_ids = [f.id for f in Firm.query.filter_by(world_id=world_id).order_by(Firm.slot_number).all()]
    bot_firm_id, other_firm_id = firm_ids

    client.post(f"/teacher/worlds/{world_id}/firms/{bot_firm_id}/bot", data={"profile": "marketing"})
    client.post(f"/register/{world_id}/{other_firm_id}", data={
        "team_name": "Human Team", "password": "secret123", "avatar": "factory-01.png", "badge": "logo-01.png",
    })
    client.post("/firm/decisions", data={
        "price": "90", "production_qty": "10000", "ad_spend": "0", "rd_spend": "0", "track": "Mid",
    })  # human team submits; bot never does anything explicitly

    teacher_login(client)  # registering/logging in as the firm above replaced the session
    resp = client.post(f"/teacher/worlds/{world_id}/advance")
    assert resp.status_code == 302

    with app.app_context():
        assert RoundDecision.query.filter_by(firm_id=bot_firm_id, round_number=1).count() == 1
        assert RoundResult.query.filter_by(firm_id=bot_firm_id, round_number=1).count() == 1
        decision = RoundDecision.query.filter_by(firm_id=bot_firm_id, round_number=1).first()
        assert decision.is_auto is True


def test_bot_keeps_playing_across_multiple_rounds(app, client):
    world_id = create_world(client, slots=1)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "random"})

    for _ in range(3):
        client.post(f"/teacher/worlds/{world_id}/advance")  # process round
        client.post(f"/teacher/worlds/{world_id}/advance")  # open next round

    with app.app_context():
        assert RoundDecision.query.filter_by(firm_id=firm_id).count() == 3
        assert RoundResult.query.filter_by(firm_id=firm_id).count() == 3


def test_removed_bots_slot_can_be_claimed_by_a_real_student_mid_game(app, client):
    world_id = create_world(client, slots=1)
    firm_id = first_unclaimed_firm_id(app, world_id)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "elite"})
    client.post(f"/teacher/worlds/{world_id}/advance")  # round 1 processed by the bot
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot/remove")

    resp = client.post(f"/register/{world_id}/{firm_id}", data={
        "team_name": "Took Over Team", "password": "secret123", "avatar": "factory-01.png", "badge": "logo-01.png",
    })
    assert resp.status_code == 302

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.team_name == "Took Over Team"
        assert firm.bot_profile is None
        assert firm.is_registered is True
        assert RoundResult.query.filter_by(firm_id=firm_id).count() == 1  # the bot's round 1 is still there
