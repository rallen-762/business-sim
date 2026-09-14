"""Tier-bound quality (history rebuild, undo) and the optional standings
board on classroom team screens."""

import pytest

from app import create_app
from app.bots import decide
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, RoundSnapshot, World

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


def register_firm(client, world_id, slot_number=1, team_name="Nike"):
    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).first().id
    client.post(f"/register/{world_id}/{firm_id}", data={
        "team_name": team_name, "password": "secret123", "avatar": "factory-01.png",
        "badge": "logo-01.png", "product_icon": "headphone-01.png",
    })
    return firm_id


def submit(client, **overrides):
    data = dict(price="80", production_qty="1000", ad_spend="0", rd_spend="0",
                track="Mid", plant_investment="0")
    data.update(overrides)
    return client.post("/firm/decisions", data=data)


def advance(client, world_id):
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")


def set_standings(client, world_id, on):
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/student-standings", data={"enabled": "1" if on else "0"})


# --------------------------------------------------------------------------- #
# Standings board on classroom team screens
# --------------------------------------------------------------------------- #

def test_standings_are_on_by_default_for_a_new_classroom_game(client):
    # Robert: after Process Round, team screens show the standings. It first
    # shipped defaulting to off, which was not what he asked for.
    world_id = create_world(client)
    register_firm(client, world_id)
    submit(client)
    advance(client, world_id)

    assert World.query.get(world_id).show_standings_to_students is True
    resp = client.get("/firm")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/firm/standings")


def test_when_turned_off_teams_go_straight_to_results(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    set_standings(client, world_id, False)
    submit(client)
    advance(client, world_id)

    resp = client.get("/firm")
    assert resp.status_code == 200
    assert "Round 1 Results" in resp.data.decode("utf-8")
    # And the board isn't reachable by URL while it's off.
    assert client.get("/firm/standings").status_code == 302


def test_when_on_a_team_sees_the_board_once_then_its_own_results(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    set_standings(client, world_id, True)
    submit(client)

    # Before processing: nothing to show, the team stays on its dashboard.
    assert client.get("/firm").status_code == 200

    advance(client, world_id)
    first = client.get("/firm")
    assert first.status_code == 302 and first.headers["Location"].endswith("/firm/standings")

    board = client.get("/firm/standings").data.decode("utf-8")
    assert "Standings after Round 1" in board
    assert '<a class="present-exit" href="/firm"' in board
    assert 'http-equiv="refresh"' not in board, "a team's board is frozen, not auto-reloading"
    assert "<form" not in board and "<button" not in board

    # Exit lands on the Results card -- no redirect loop back to the board.
    after = client.get("/firm")
    assert after.status_code == 200
    body = after.data.decode("utf-8")
    assert "Round 1 Results" in body
    assert "View Class Standings" in body, "the board stays one click away"


def test_the_board_shows_again_after_the_next_round(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    set_standings(client, world_id, True)
    submit(client)
    advance(client, world_id)
    client.get("/firm/standings")
    advance(client, world_id)            # Open Round 2
    submit(client)
    advance(client, world_id)            # Process Round 2

    resp = client.get("/firm")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/firm/standings")


def test_teacher_can_turn_it_off_mid_game(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    set_standings(client, world_id, True)
    submit(client)
    advance(client, world_id)
    set_standings(client, world_id, False)

    assert client.get("/firm").status_code == 200
    assert "View Class Standings" not in client.get("/firm").data.decode("utf-8")


def test_teacher_page_shows_the_toggle_and_its_state(client):
    world_id = create_world(client)
    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Stop Showing Standings on Team Screens" in body
    set_standings(client, world_id, False)
    body = client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    assert "Show Standings on Team Screens" in body
    assert "Stop Showing" not in body


def test_toggle_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    client.post(f"/teacher/worlds/{world_id}/student-standings", data={"enabled": "0"})
    assert World.query.get(world_id).show_standings_to_students is True


def test_a_submitted_classroom_team_page_reloads_itself_to_catch_processing(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    form_page = client.get("/firm").data.decode("utf-8")
    assert 'http-equiv="refresh"' not in form_page, "never reload a half-filled decision form"
    submit(client)
    waiting = client.get("/firm").data.decode("utf-8")
    assert "Round 1 Submitted" in waiting
    assert 'http-equiv="refresh"' in waiting


# --------------------------------------------------------------------------- #
# Tier-bound quality: history rebuild, undo, bots
# --------------------------------------------------------------------------- #

def _played_firm_with_null_by_track(client, world_id):
    """A firm that played rounds in two tiers, then has rd_spend_by_track
    wiped to NULL -- exactly how a pre-existing firm looks right after the
    column is added."""
    firm_id = register_firm(client, world_id)
    submit(client, track="Mid", rd_spend="50000")
    advance(client, world_id)
    advance(client, world_id)
    submit(client, track="Premium", rd_spend="100000")
    advance(client, world_id)
    advance(client, world_id)
    submit(client, track="Premium", rd_spend="50000")   # round 3: submitted, NOT processed
    firm = db.session.get(Firm, firm_id)
    firm.rd_spend_by_track = None
    db.session.commit()
    return firm_id


def test_init_db_rebuilds_per_tier_rd_from_processed_rounds_only(app, client):
    world_id = create_world(client)
    firm_id = _played_firm_with_null_by_track(client, world_id)

    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    assert "Rebuilt per-tier R&D" in result.output
    assert "WARNING" not in result.output

    firm = db.session.get(Firm, firm_id)
    assert firm.rd_spend_by_track == {"Mid": 50_000, "Premium": 100_000}
    assert firm.cumulative_rd_spend == 150_000

    # Idempotent: a second deploy leaves it alone.
    again = app.test_cli_runner().invoke(args=["init-db"])
    assert "Rebuilt per-tier R&D" not in again.output


def test_undo_restores_per_tier_rd(client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id)
    submit(client, track="Entry", rd_spend="50000")
    advance(client, world_id)
    assert db.session.get(Firm, firm_id).rd_spend_by_track == {"Entry": 50_000}

    client.post(f"/teacher/worlds/{world_id}/undo")
    db.session.expire_all()
    assert db.session.get(Firm, firm_id).rd_spend_by_track == {}


def test_undo_of_a_snapshot_from_before_the_field_rebuilds_from_history(client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id)
    submit(client, track="Mid", rd_spend="50000")
    advance(client, world_id)
    advance(client, world_id)
    submit(client, track="Premium", rd_spend="100000")
    advance(client, world_id)

    # Strip the new field out of round 2's snapshot, as if it was taken by
    # the previous version of the app.
    snap = RoundSnapshot.query.filter_by(world_id=world_id, round_number=2).one()
    snap.firm_states = {
        fid: {k: v for k, v in saved.items() if k != "rd_spend_by_track"}
        for fid, saved in snap.firm_states.items()
    }
    db.session.commit()

    client.post(f"/teacher/worlds/{world_id}/undo")
    db.session.expire_all()
    assert db.session.get(Firm, firm_id).rd_spend_by_track == {"Mid": 50_000}


def test_elite_bot_measures_its_ramp_in_the_tier_it_is_selling():
    class FixedRng:
        def uniform(self, a, b): return (a + b) / 2
        def choice(self, seq): return seq[0]
        def random(self): return 0.5

    base = dict(firm_id=1, cash=10_000_000, capacity=45_000,
                cumulative_ad_spend=0, loan_outstanding=0, last_price=None, last_profit=None,
                rng=FixedRng())
    # Round 4 is its first Premium round. Its 210k of Mid R&D must not count:
    # the ramp target (280k) is measured against Premium's 0.
    d = decide("elite", round_number=4, rd_spend_by_track={"Mid": 210_000}, **base)
    assert d.track == "Premium"
    assert d.rd_spend == 280_000


# --------------------------------------------------------------------------- #
# Onboarding: segments on the Firm page, section title, tours, icon note
# --------------------------------------------------------------------------- #

def test_firm_page_shows_customer_segment_profiles_and_the_decision_title(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    body = client.get("/firm").data.decode("utf-8")
    assert 'id="firm-segments"' in body
    assert body.count('class="segment-trait-dots"') == 15   # 5 segments x 3 traits
    assert "Relative Size" in body
    # Only the profile half comes over -- not the round's numbers.
    section = body[body.index('id="firm-segments"'):body.index('id="decision-title"')]
    assert "Avg. Consumer Surplus" not in section and "Leading Tier" not in section
    assert "This Round: Price &amp; Investment" in body
    assert body.index('id="decision-title"') < body.index('id="decision-form"')


def test_firm_page_tour_has_three_steps_keyed_to_this_team(client):
    import json, re
    world_id = create_world(client)
    firm_id = register_firm(client, world_id)
    body = client.get("/firm").data.decode("utf-8")
    steps = json.loads(re.search(r"const STEPS = (\[.*?\]);", body, re.S).group(1))
    assert len(steps) == 3
    assert "10 rounds" in steps[0]["text"] and "profit" in steps[0]["text"]
    assert steps[1]["target"] == "#firm-segments"
    assert "Price Sensitivity" in steps[1]["text"] and "Quality Focus" in steps[1]["text"] and "Brand Pull" in steps[1]["text"]
    assert steps[1]["title"] == "Know your customers"
    assert "Round 2" in steps[2]["text"] and "overspend" in steps[2]["text"]
    assert f'"hcs-tour-firm-{firm_id}"' in body
    assert '<button type="button" class="tour-replay"' in body


def test_tour_is_not_on_the_results_screen(client):
    world_id = create_world(client)
    register_firm(client, world_id)
    set_standings(client, world_id, False)
    submit(client)
    advance(client, world_id)
    body = client.get("/firm").data.decode("utf-8")
    assert "Round 1 Results" in body
    assert 'id="tour"' not in body


def test_market_dashboard_intro_only_for_teams_once_there_is_data(client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id)
    assert 'id="tour"' not in client.get("/market").data.decode("utf-8"), "nothing to point at before round 1"
    submit(client)
    advance(client, world_id)

    team = client.get("/market").data.decode("utf-8")
    assert 'id="tour"' in team and "competitive advantage" in team
    assert f'"hcs-market-intro-firm-{firm_id}"' in team
    teacher = client.get(f"/teacher/worlds/{world_id}/market").data.decode("utf-8")
    assert 'id="tour"' not in teacher


def test_icon_picker_says_the_icons_are_cosmetic(client):
    world_id = create_world(client)
    firm = Firm.query.filter_by(world_id=world_id, slot_number=1).first()
    body = client.get(f"/register/{world_id}/{firm.id}").data.decode("utf-8")
    note = body[body.index('class="info-note"'):]
    assert "Just for looks" in note[:400]
    assert body.index('class="info-note"') < body.index('name="avatar"')
