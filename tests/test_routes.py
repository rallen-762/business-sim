"""
Integration tests driving the full stack through Flask's test client:
teacher creates a World -> students register -> submit decisions -> teacher
advances the round -> results land in the DB and render correctly. This is
the "does the DB<->engine wiring actually work" check that the unit tests
for engine.py/models.py individually can't catch.
"""

import math
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
        price="80", production_qty="15000", ad_spend="0", rd_spend="0",
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


def test_world_page_title_shows_teacher_dashboard_and_world_name(client):
    world_id = create_world(client, name="Period 3")
    resp = client.get(f"/teacher/worlds/{world_id}")
    body = resp.data.decode()
    assert "Teacher Dashboard" in body
    assert "Period 3" in body


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
    submit_decision(client, price="90", production_qty="15000")
    with app.app_context():
        d = RoundDecision.query.filter_by(firm_id=firm_id, round_number=1).first()
        assert d is not None
        assert d.price == 90.0
        assert d.production_qty == 15000
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
# Overspend hard-block (server-side, independent of the client-side calculator)
# --------------------------------------------------------------------------- #

def test_submit_decision_rejects_overspend_even_if_client_bypassed_js(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    # $1,000,000 cash, Standard track ($50/unit) -> 25,000 units would cost
    # $1,250,000, more than available -- must be rejected regardless of what
    # the (bypassed) client-side calculator would have computed.
    resp = submit_decision(client, production_qty="25000", follow_redirects=True)
    assert b"reduce spending to submit" in resp.data.lower()
    with app.app_context():
        assert RoundDecision.query.filter_by(firm_id=firm_id).count() == 0


def test_submit_decision_allows_spend_exactly_equal_to_cash(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    # Exactly $1,000,000 at $50/unit = 20,000 units -- affordable, not "over."
    resp = submit_decision(client, production_qty="20000", follow_redirects=True)
    assert b"Decision submitted" in resp.data


def test_submit_decision_counts_all_spend_categories_toward_the_block(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    # 15,000 units ($750,000) + $300,000 R&D = $1,050,000 > $1,000,000 cash.
    resp = submit_decision(client, production_qty="15000", rd_spend="300000", follow_redirects=True)
    assert b"reduce spending to submit" in resp.data.lower()


# --------------------------------------------------------------------------- #
# R&D/Ad presets, projected loan interest, non-submission card, soft-penalty
# disabling, cumulative totals on the Round Results screen
# --------------------------------------------------------------------------- #

def test_dashboard_shows_rd_and_ad_presets(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "reach Quality Level 2" in body
    assert "$50,000" in body
    assert "reach Ad Level 2" in body
    assert "$125,000" in body


def test_dashboard_shows_team_identity_header(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike", avatar="building-a.png")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Nike" in body
    assert 'img/avatars/building-a.png' in body


def test_dashboard_quality_level_does_not_repeat_the_track_name(client):
    # Regression: Quality Level used to show "1 -- Low Quality Standard"
    # right next to a separate "Current Track: Standard" stat -- the track
    # name appeared twice. Quality Level should only ever show the quality
    # descriptor, never a track name.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Low Quality" in body
    assert "Low Quality Standard" not in body


def test_dashboard_shows_committed_spend_and_cash_as_separate_stats(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Committed Spend" in body
    assert "Cash on Hand" in body
    assert "$1,000,000" in body  # starting cash, rendered server-side now


def test_dashboard_shows_projected_loan_interest_for_existing_balance(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.get(firm_id)
        firm.loan_outstanding = 440_000
        db.session.commit()
    resp = client.get("/firm")
    body = resp.data.decode()
    # (440,000 - 100,000 principal) * 10% = $34,000 projected interest.
    assert "$34,000" in body


def test_dashboard_disables_plant_and_celebrity_when_indebted(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.get(firm_id)
        firm.loan_outstanding = 100_000
        db.session.commit()
    resp = client.get("/firm")
    body = resp.data.decode()
    assert 'id="plant_investment" name="plant_investment" onchange="recalc()" disabled' in body
    assert 'id="celebrity_on" name="celebrity_on" onchange="recalc()" disabled' in body


def test_round_results_screen_shows_cumulative_totals(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Cumulative Totals" in body
    assert "Cumulative Revenue" in body
    assert "Cumulative Profit" in body


def test_non_submission_shows_in_universe_status_card(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    # Never submits.
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/firm")
    assert b"Emergency Production Directive Issued" in resp.data


# --------------------------------------------------------------------------- #
# Full round-advance flow (the actual DB<->engine bridge)
# --------------------------------------------------------------------------- #

def test_advancing_round_processes_all_firms_and_creates_results(app, client):
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
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


def test_advancing_round_persists_segment_level_consumer_surplus_stats(app, client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        from app.constants import SEGMENTS
        from app.models import SegmentRoundResult
        rows = SegmentRoundResult.query.filter_by(world_id=world_id, round_number=1).all()
        assert {r.segment for r in rows} == set(SEGMENTS)

    resp = client.get(f"/teacher/worlds/{world_id}")
    assert b"Average Consumer Surplus by Segment" in resp.data
    assert b"buyers unsold" in resp.data


def test_firm_dashboard_shows_just_processed_round_result_during_transition(client):
    # Regression test: world.current_round is NOT incremented until the
    # teacher opens the next round, so during "transition" the just-
    # processed round's result lives at current_round itself, not
    # current_round - 1. Caught live via a real HTTP smoke test.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
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
    submit_decision(client, price="80", production_qty="15000")
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
    # A single registered firm with no real competition should capture
    # every buyer who can actually AFFORD it at $80 on Standard, not have
    # its share diluted by a phantom unregistered "competitor" using the
    # bootstrap default price/track. Since the willingness-to-pay redesign,
    # "no competition" no longer means "the entire buyer pool converts" --
    # some segments' buyers genuinely can't afford $80 on Standard at all,
    # and that's correct, not a leftover phantom-competitor bug.
    world_id = create_world(client, slots=3)
    register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = Firm.query.filter_by(world_id=world_id, slot_number=1).first()
        firm.plant_capacity = 1_000_000  # so production isn't the binding constraint
        firm.cash = 100_000_000  # ...and neither is cash, now that overspend is hard-blocked
        db.session.commit()
    submit_decision(client, price="80", production_qty="1000000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        r = RoundResult.query.filter_by(round_number=1).join(Firm).filter(Firm.world_id == world_id).first()
        from app.constants import SEGMENT_BUYER_COUNT, wtp_threshold_r
        expected_total = sum(
            max(0.0, min(1.0, 1 - wtp_threshold_r(seg, "Standard", 80))) * count
            for seg, count in SEGMENT_BUYER_COUNT.items()
        )
        assert math.isclose(r.units_sold_total, expected_total, rel_tol=1e-6)


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


def test_market_dashboard_shows_standings_ranking_pie_and_segments(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/market")
    body = resp.data.decode()
    assert 'class="standings-bars"' in body
    assert "standings-bar-fill" in body
    assert "Round Totals" in body
    assert "Market Share" in body
    assert "Customer Segments" in body
    assert "Low Income" in body and "Casual/Fashion" in body
    assert "View Competitive Intelligence Report" in body


def test_market_dashboard_pie_legend_uses_the_shared_theme_palette(client):
    # Regression: the legend swatches used to hardcode their OWN copy of a
    # bright charting-library-default color array, separate from (and
    # different than) market_data.PIE_COLORS -- clashing with the muted
    # theme and able to drift out of sync with the pie itself.
    from app.market_data import PIE_COLORS

    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}/market")
    body = resp.data.decode()
    assert PIE_COLORS[0] in body
    assert "#2159d1" not in body  # the old hardcoded default-blue swatch


def test_market_dashboard_standings_row_stays_aligned_without_an_avatar(app, client):
    # Regression: the avatar <img> used to be entirely OMITTED for a firm
    # with no avatar set (e.g. a legacy bot from before bot avatars
    # existed), which shifted every later CSS Grid column in that row left
    # by one track -- name/bar/value all rendered in the wrong-sized
    # column. The avatar slot must always be present as an element, empty
    # or not.
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.avatar = None
        db.session.commit()
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}/market")
    body = resp.data.decode()
    assert body.count('class="standings-avatar"') == body.count('class="standings-row"')
    assert "Nike" in body


def test_market_dashboard_market_link_reaches_intel_report(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    resp = client.get("/market/intel")
    body = resp.data.decode()
    assert "Nike" in body
    assert "Competitive Intelligence Report" in body
    # Hidden fields must never appear in the rendered page at all.
    assert "Plant Capacity" not in body
    assert "Cash" not in body
    assert "R&amp;D Spend" not in body and "R&D Spend" not in body


def test_teacher_intel_route_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.get(f"/teacher/worlds/{world_id}/intel", follow_redirects=True)
    assert b"Teacher login required" in resp.data


def test_teacher_intel_route_reachable(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}/intel")
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
        from app.models import SegmentRoundResult
        assert SegmentRoundResult.query.filter_by(world_id=world_id).count() == 0


def test_delete_nonexistent_world_returns_404(client):
    teacher_login(client)
    resp = client.post("/teacher/worlds/99999/delete")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Teacher World page: round selector, decision/output columns, running totals
# --------------------------------------------------------------------------- #

def _play_two_rounds(client, world_id, firm_id):
    """Registers Nike, plays two full rounds with different prices so each
    round's revenue/profit are distinguishable, leaving the world on Round 3
    ("collecting", no decision/result yet)."""
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # round 1 -> transition
    client.post(f"/teacher/worlds/{world_id}/advance")  # -> round 2, collecting
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    submit_decision(client, price="120", production_qty="15000")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")  # round 2 -> transition
    client.post(f"/teacher/worlds/{world_id}/advance")  # -> round 3, collecting


def test_round_selector_defaults_to_current_round(client):
    # create_world() already leaves the client logged in as teacher --
    # no firm registration needed just to check the selector's default.
    world_id = create_world(client, slots=1)
    resp = client.get(f"/teacher/worlds/{world_id}")
    assert 'value="1" selected' in resp.data.decode()


def _firms_table_only(html):
    # The Scouting Report section always reflects the LATEST processed
    # round regardless of the Firms table's round selector (by design) --
    # scope round-selector assertions to just the Firms table so the two
    # sections' independent "which round am I showing" behavior can't be
    # confused with each other.
    start = html.index('<h3>Firms</h3>')
    end = html.index('<h3>Scouting Report</h3>')
    return html[start:end]


def test_round_selector_shows_that_rounds_own_decision_and_result(app, client):
    world_id = create_world(client, slots=1)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    _play_two_rounds(client, world_id, firm_id)

    resp1 = client.get(f"/teacher/worlds/{world_id}?round=1")
    firms_table1 = _firms_table_only(resp1.data.decode())
    assert "80.00" in firms_table1  # round 1's price
    assert "120.00" not in firms_table1

    resp2 = client.get(f"/teacher/worlds/{world_id}?round=2")
    firms_table2 = _firms_table_only(resp2.data.decode())
    assert "120.00" in firms_table2
    assert "80.00" not in firms_table2


def test_pending_round_shows_dashes_not_a_crash(app, client):
    world_id = create_world(client, slots=1)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    _play_two_rounds(client, world_id, firm_id)  # leaves world on round 3, collecting, no decision yet

    resp = client.get(f"/teacher/worlds/{world_id}?round=3")
    assert resp.status_code == 200
    assert b'\xe2\x80\x94' in resp.data  # the em-dash placeholder, UTF-8 encoded


def test_cumulative_totals_respect_the_selected_round_not_the_live_round(app, client):
    world_id = create_world(client, slots=1)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    _play_two_rounds(client, world_id, firm_id)

    with app.app_context():
        from app.models import RoundResult
        r1 = RoundResult.query.filter_by(firm_id=firm_id, round_number=1).first()
        r2 = RoundResult.query.filter_by(firm_id=firm_id, round_number=2).first()

    # Viewing round 1: cumulative should be round 1 ONLY, even though the
    # world has since moved on to round 3.
    resp1 = client.get(f"/teacher/worlds/{world_id}?round=1")
    body1 = resp1.data.decode()
    assert f"${r1.revenue:,.0f}" in body1

    # Viewing round 2: cumulative should be round 1 + round 2.
    resp2 = client.get(f"/teacher/worlds/{world_id}?round=2")
    body2 = resp2.data.decode()
    expected_cum_revenue = r1.revenue + r2.revenue
    assert f"${expected_cum_revenue:,.0f}" in body2


# --------------------------------------------------------------------------- #
# Teacher-facing Market Dashboard
# --------------------------------------------------------------------------- #

def test_teacher_market_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.get(f"/teacher/worlds/{world_id}/market", follow_redirects=True)
    assert b"Teacher login required" in resp.data


def test_teacher_market_shows_latest_round_data(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}/market")
    assert b"Nike" in resp.data
    assert b"through Round 1" in resp.data


def test_teacher_world_page_links_to_market(client):
    world_id = create_world(client)
    resp = client.get(f"/teacher/worlds/{world_id}")
    assert f'/teacher/worlds/{world_id}/market'.encode() in resp.data


def test_scouting_report_placeholder_before_any_round(client):
    world_id = create_world(client)
    resp = client.get(f"/teacher/worlds/{world_id}")
    assert b"Scouting Report will appear after Round 1" in resp.data


def test_scouting_report_shows_top_firm_after_a_round(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    resp = client.get(f"/teacher/worlds/{world_id}")
    body = resp.data.decode()
    assert "Scouting Report" in body
    assert "#1 -- Nike" in body
    assert "not AI-generated" in body


def test_firm_dashboard_links_to_market(client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/firm")
    assert b'/market' in resp.data


def test_export_csv_requires_teacher_login(client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.get(f"/teacher/worlds/{world_id}/export.csv", follow_redirects=True)
    assert b"Teacher login required" in resp.data


def test_export_csv_returns_downloadable_csv_with_data(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
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
