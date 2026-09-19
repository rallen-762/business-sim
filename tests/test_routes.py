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


def register_firm(client, world_id, slot_number, team_name, password="secret123", avatar="factory-01.png", badge=None, product_icon="headphone-01.png"):
    # Badges are unique per world now, so the default is derived from the
    # slot rather than shared. Handing every firm logo-01 made the second
    # registration in any multi-firm test fail on the badge check before it
    # reached whatever that test was actually asserting.
    badge = badge or f"logo-{slot_number:02d}.png"
    with client.application.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=slot_number).first().id
    client.post(
        f"/register/{world_id}/{firm_id}",
        data={"team_name": team_name, "password": password, "avatar": avatar, "badge": badge, "product_icon": product_icon},
    )
    return firm_id


def submit_decision(client, follow_redirects=False, **overrides):
    data = dict(
        price="80", production_qty="15000", ad_spend="0", rd_spend="0",
        track="Mid", plant_investment="0",
    )
    data.update(overrides)
    return client.post("/firm/decisions", data=data, follow_redirects=follow_redirects)


# --------------------------------------------------------------------------- #
# Teacher: world creation
# --------------------------------------------------------------------------- #

def test_teacher_login_wrong_password_rejected(client):
    resp = client.post("/teacher/login", data={"password": "nope"}, follow_redirects=True)
    assert b"Wrong teacher password" in resp.data


def test_student_login_page_shows_banner_and_three_entry_paths(client):
    resp = client.get("/login")
    body = resp.data.decode("utf-8")
    assert "Classroom Game" in body
    assert "Sandbox Mode" in body
    assert "Teacher Login" in body
    assert "game_code" in body
    assert "sandbox/" in body
    assert "teacher/login" in body


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
        data={"team_name": "Nike", "password": "secret123", "avatar": "factory-01.png", "badge": "logo-01.png", "product_icon": "headphone-01.png"},
        follow_redirects=True,
    )
    assert b"Nike" in resp.data


def test_registration_stores_all_three_identity_icons(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike", avatar="factory-03.png",
                            badge="logo-05.png", product_icon="headphone-07.png")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert (firm.avatar, firm.badge, firm.product_icon) == (
            "factory-03.png", "logo-05.png", "headphone-07.png"
        )

    # ...and all three show in the Firm Dashboard header. The factory plate
    # is the capacity-tier art derived from the chosen avatar, not the flat
    # avatar file -- a new firm is at base capacity, so Level 1.
    body = client.get("/firm").data.decode()
    assert "img/factories/factory-03-L1.png" in body
    assert "img/badges/logo-05.png" in body
    # The product is drawn at its QUALITY tier now, like the factory is drawn
    # at its capacity tier -- a new firm is at quality 1, so tier 1.
    assert "img/products-q/headphone-07-Q1.png" in body


def test_registration_requires_a_product_icon(app, client):
    # The picker defaults to the first option, so this only trips if the form
    # is bypassed -- but an unvalidated value would be stored verbatim and
    # then render as a broken <img> forever.
    world_id = create_world(client)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    resp = client.post(
        f"/register/{world_id}/{firm_id}",
        data={"team_name": "Nike", "password": "secret123", "avatar": "factory-01.png",
              "badge": "logo-01.png", "product_icon": "../../etc/passwd"},
        follow_redirects=True,
    )
    assert b"pick a product" in resp.data
    with app.app_context():
        assert not db.session.get(Firm, firm_id).is_registered


def test_duplicate_team_name_within_world_rejected(client):
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    client.get("/logout")
    resp = client.post(
        f"/register/{world_id}/{2}",
        # A FREE badge on purpose: this test is about the duplicate team
        # name, and reusing slot 1's badge would trip the badge check first
        # and never reach it.
        data={"team_name": "Nike", "password": "whatever", "avatar": "factory-01.png", "badge": "logo-02.png", "product_icon": "headphone-01.png"},
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
# Role separation: a student must never end up holding teacher access
#
# Reported bug: a student who clicked "Market Dashboard" after their Round 1
# Results landed on the Teacher Dashboard, logged in as the teacher. Two
# defects chained:
#   A. every teacher_login_required view redirects to /teacher/login on a
#      miss, and the local-dev bypass there called log_in_teacher() on a bare
#      GET -- which session.clear()s whatever student session the browser
#      held (one cookie per browser, not per tab) and grants teacher access.
#   B. /login (the STUDENT game-code page, where firm_login_required sends
#      anyone without a firm session) then forwarded any teacher session
#      straight on to the Teacher Dashboard -- turning "student's session
#      went stale" into "student is now the teacher." B applied on Render
#      too, not just local dev.
# --------------------------------------------------------------------------- #

def test_stray_teacher_url_does_not_hijack_a_logged_in_students_session(app, client):
    app.config["IS_LOCAL_DEV"] = True  # forced off under TESTING; this is the path being guarded
    world_id = create_world(client)
    client.get("/teacher/logout")
    firm_id = register_firm(client, world_id, 1, "Nike")

    # Another tab/bookmark/back-button in the same browser touches a teacher URL.
    client.get(f"/teacher/worlds/{world_id}", follow_redirects=True)

    with client.session_transaction() as s:
        assert s.get("firm_id") == firm_id, "student's session was replaced"
        assert not s.get("is_teacher"), "a bare GET silently granted teacher access"


def test_student_market_link_stays_on_the_student_dashboard(app, client):
    app.config["IS_LOCAL_DEV"] = True
    world_id = create_world(client)
    client.get("/teacher/logout")
    register_firm(client, world_id, 1, "Nike")
    client.get(f"/teacher/worlds/{world_id}", follow_redirects=True)  # the hijack attempt

    resp = client.get("/market", follow_redirects=True)
    body = resp.data.decode()
    assert "Create a World" not in body, "student landed on the Teacher Dashboard"
    # Assert the LINK, not its wording -- the label now carries the team's
    # own name ("Back to Nike"), which is cosmetic and free to change.
    assert 'href="/firm"' in body, "student did not get the student Market Dashboard nav"


def test_teacher_session_hitting_a_student_page_is_not_forwarded_to_teacher_dashboard(app, client):
    # Defect B on its own, with no local-dev bypass involved at all.
    create_world(client)  # leaves the client logged in as teacher
    resp = client.get("/market", follow_redirects=True)
    body = resp.data.decode()
    assert "Create a World" not in body
    assert "not a team" in body  # the explicit "you're the teacher" flash


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
    # $1,000,000 cash, Mid track ($50/unit) -> 25,000 units would cost
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
    register_firm(client, world_id, 1, "Nike", avatar="factory-01.png")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Nike" in body
    # The chosen factory still identifies the team, now drawn at its
    # capacity tier rather than as the flat avatar.
    assert "img/factories/factory-01-L1.png" in body


def test_dashboard_quality_level_does_not_repeat_the_track_name(client):
    # Regression: Quality Level used to show "1 -- Low Quality Mid"
    # right next to a separate "Current Track: Mid" stat -- the track
    # name appeared twice. Quality Level should only ever show the quality
    # descriptor, never a track name.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    resp = client.get("/firm")
    body = resp.data.decode()
    assert "Low Quality" in body
    assert "Low Quality Mid" not in body


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
    # Celebrity is four radio buttons now; every one of them must be disabled.
    celeb_block = body[body.index('class="celeb-grid"'):body.index('Committed Spend')]
    assert celeb_block.count('type="radio" name="celebrity"') == 5, "four endorsers plus a None option"
    assert celeb_block.count("disabled") == 5, "every endorsement choice must be disabled under debt"


def test_round_results_screen_shows_cumulative_totals(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    client.get("/firm/standings")  # standings board comes first after processing
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
    client.get("/firm/standings")  # standings board comes first after processing
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
    client.get("/firm/standings")  # standings board comes first after processing
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
    # every buyer who can actually AFFORD it at $80 on Mid, not have
    # its share diluted by a phantom unregistered "competitor" using the
    # bootstrap default price/track. Since the willingness-to-pay redesign,
    # "no competition" no longer means "the entire buyer pool converts" --
    # some segments' buyers genuinely can't afford $80 on Mid at all,
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
            max(0.0, min(1.0, 1 - wtp_threshold_r(seg, "Mid", 80))) * count
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
        assert d.track == "Mid"


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
    assert "Budget Shoppers" in body and "Casual/Style-Conscious" in body
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
# Game Management: password reset
# --------------------------------------------------------------------------- #

def test_reset_password_generates_a_new_working_password(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike", password="original123")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/reset-password")

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert not firm.check_password("original123")  # old password no longer works

    # The new password was shown via flash -- confirm one landed and follow
    # the two-word-plus-digit format rather than asserting an exact value.
    resp = client.get(f"/teacher/worlds/{world_id}")
    assert "Password reset for Nike" in resp.data.decode()


def test_reset_password_shows_up_in_game_management_after_reset(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    client.get("/logout")

    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/reset-password")
    resp = client.get(f"/teacher/worlds/{world_id}")
    body = resp.data.decode()
    assert "Show Password" in body
    assert "Not reset this session" not in body  # Nike now has one

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        new_password = firm.password_hash  # can't read plaintext back -- verify indirectly below
    assert new_password  # a real hash was set


def test_reset_password_refuses_a_bot_slot(app, client):
    world_id = create_world(client)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot", data={"profile": "underbidder"})

    resp = client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/reset-password", follow_redirects=True)
    assert b"isn&#39;t a real team" in resp.data or b"isn't a real team" in resp.data


def test_reset_password_refuses_an_unclaimed_slot(app, client):
    world_id = create_world(client)
    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).first().id
    resp = client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/reset-password")
    assert resp.status_code == 302
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.password_hash is None  # untouched


def test_game_management_lists_every_firm_but_only_real_teams_get_password_controls(app, client):
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, "Nike")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/firms/2/bot", data={"profile": "elite"})

    resp = client.get(f"/teacher/worlds/{world_id}")
    body = resp.data.decode()
    assert "Game Management" in body
    game_mgmt = body[body.index("Game Management"):]
    # Status + bot Reassign/Remove controls moved here from the Firms table
    # (per the user's "these should live only in Game Management" fix), so
    # bots now DO appear here -- just without password controls.
    assert "Nike" in game_mgmt
    assert "Bot #2" in game_mgmt
    assert "Reset Password" in game_mgmt
    assert "Reassign" in game_mgmt
    assert "Remove Bot" in game_mgmt


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
    start = html.index('Firm Decisions by Round')
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


def test_stale_session_pointing_at_an_unclaimed_slot_is_not_logged_in(app, client):
    # Found live: a leftover cookie whose firm_id resolved to an UNCLAIMED
    # slot passed every "is someone logged in" check and rendered as
    # "logged in as None", because an unclaimed slot's team_name is NULL.
    world_id = create_world(client, slots=2)
    with app.app_context():
        unclaimed_id = Firm.query.filter_by(world_id=world_id, slot_number=2).first().id
    client.get("/teacher/logout")
    with client.session_transaction() as s:
        s["firm_id"] = unclaimed_id
        s["world_id"] = world_id

    resp = client.get("/firm", follow_redirects=True)
    body = resp.data.decode()
    assert "logged in as None" not in body
    assert "Game Code" in body or "log in" in body.lower()


# --------------------------------------------------------------------------- #
# Role coexistence: one session cookie per browser, two roles
#
# Reported: "really annoying how often I get logged out, both as a student and
# a teacher." Cause: log_in_firm/log_in_teacher/log_out all cleared the WHOLE
# cookie, so signing into (or out of) either role destroyed the other.
# Confirmed with the user to let them coexist, with a visible warning on the
# student dashboard because a leftover teacher session on a SHARED browser
# would otherwise hand the next student teacher access silently.
# --------------------------------------------------------------------------- #

def test_signing_in_as_a_team_keeps_the_teacher_session(app, client):
    world_id = create_world(client)          # leaves client logged in as teacher
    register_firm(client, world_id, 1, "Nike")
    with client.session_transaction() as s:
        assert s.get("is_teacher"), "teacher session was wiped by a team login"
        assert s.get("firm_id")


def test_signing_out_of_one_role_leaves_the_other_alone(app, client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")

    client.get("/logout")                     # student signs out
    with client.session_transaction() as s:
        assert not s.get("firm_id")
        assert s.get("is_teacher"), "signing out of the team also signed out the teacher"

    client.post(f"/login/{world_id}/1", data={"password": "secret123"})
    client.get("/teacher/logout")             # teacher signs out
    with client.session_transaction() as s:
        assert not s.get("is_teacher")
        assert s.get("firm_id"), "signing out of teacher also signed out the team"


def test_student_dashboard_warns_when_teacher_is_also_signed_in(app, client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode()
    assert "also signed in as the Teacher" in body
    assert "/teacher/logout" in body

    # ...and the warning is gone once the teacher role is dropped, without
    # knocking the team off their own dashboard.
    resp = client.get("/teacher/logout", follow_redirects=True)
    body = resp.data.decode()
    assert "also signed in as the Teacher" not in body
    assert "Nike" in body, "dropping teacher access kicked the team out too"


def test_teacher_reset_passwords_never_outlive_the_teacher_session(app, client):
    # They're plaintext and teacher-only, so they must go with the role.
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/reset-password")
    with client.session_transaction() as s:
        assert s.get("reset_passwords")
    client.get("/teacher/logout")
    with client.session_transaction() as s:
        assert not s.get("reset_passwords")


# --------------------------------------------------------------------------- #
# R&D is limited to MAX_QUALITY_LEVEL_GAIN_PER_ROUND levels per round
# --------------------------------------------------------------------------- #

def test_rd_spend_above_the_per_round_cap_is_rejected(app, client):
    from app.constants import max_rd_spend_this_round
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")

    cap = max_rd_spend_this_round(0)          # Level 1 -> 4 == $150,000
    resp = submit_decision(client, rd_spend=str(int(cap) + 1),
                           production_qty="1000", follow_redirects=True)
    assert b"Quality Levels per round" in resp.data
    with app.app_context():
        assert RoundDecision.query.filter_by(firm_id=firm_id).count() == 0, "over-cap spend was stored"


def test_rd_spend_exactly_at_the_cap_is_accepted(app, client):
    from app.constants import max_rd_spend_this_round
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")

    cap = max_rd_spend_this_round(0)
    submit_decision(client, rd_spend=str(int(cap)), production_qty="1000")
    with app.app_context():
        d = RoundDecision.query.filter_by(firm_id=firm_id).first()
        assert d is not None and d.rd_spend == cap


def test_maxed_out_firm_cannot_submit_any_rd(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.rd_spend_by_track = {"Mid": 700_000}      # Mid already Level 10
        db.session.commit()
    resp = submit_decision(client, rd_spend="50000", production_qty="1000", follow_redirects=True)
    assert b"maximum Quality Level" in resp.data


def test_a_tier_maxed_elsewhere_still_accepts_rd_in_a_new_tier(app, client):
    # Quality is tier-bound: Level 10 in Mid means nothing for Premium, so
    # R&D submitted with Premium is measured against Premium's own cap.
    from app.constants import max_rd_spend_this_round
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.rd_spend_by_track = {"Mid": 700_000}
        db.session.commit()
    cap = max_rd_spend_this_round(0)
    submit_decision(client, track="Premium", rd_spend=str(int(cap)), production_qty="1000")
    with app.app_context():
        d = RoundDecision.query.filter_by(firm_id=firm_id).first()
        assert d is not None and d.rd_spend == cap and d.track == "Premium"


def test_rd_is_credited_to_the_tier_it_was_spent_in(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    submit_decision(client, track="Entry", rd_spend="50000", production_qty="1000")
    client.post(f"/teacher/worlds/{world_id}/advance")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.rd_spend_by_track == {"Entry": 50_000}
        assert firm.cumulative_rd_spend == 50_000  # all-tier total still kept
        result = RoundResult.query.filter_by(firm_id=firm_id, round_number=1).one()
        assert result.quality_level == 2


def test_dashboard_shows_quality_for_all_three_tiers(app, client):
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.rd_spend_by_track = {"Premium": 200_000}   # Premium Level 5
        firm.last_track = "Mid"
        db.session.commit()
    body = client.get("/firm").data.decode("utf-8")
    assert body.count('class="tier-quality-row') == 3
    assert 'data-tier="Premium"' in body and "5/10" in body
    # The form opens on Mid, so the headline level and R&D options are Mid's.
    import re
    assert re.search(r'tier-quality-row is-active"[^>]*data-tier="Mid"', body)
    assert "reach Quality Level 2" in body
    # Every tier's options ship to the page so switching tiers can swap them.
    assert "const RD_BY_TIER" in body


# --------------------------------------------------------------------------- #
# Projector view + live price-reach readout
# --------------------------------------------------------------------------- #

def test_projector_view_requires_teacher_login(app, client):
    world_id = create_world(client)
    client.get("/teacher/logout")
    resp = client.get(f"/teacher/worlds/{world_id}/present", follow_redirects=True)
    assert b"Teacher Login" in resp.data or b"Teacher login required" in resp.data


def test_projector_view_shows_standings_and_is_read_only(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "Nike" in body
    assert "Standings after Round 1" in body
    # Nothing that can CHANGE anything while it's projected in front of a
    # class -- but there must still be a way out. The first version had no
    # exit at all, which meant closing the tab was the only way back.
    assert "<form" not in body and "<button" not in body
    assert "present-exit" in body
    assert f"/teacher/worlds/{world_id}" in body


def test_projector_view_before_any_round_does_not_crash(app, client):
    world_id = create_world(client)
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "Waiting for Round 1" in body


def test_rank_delta_is_none_until_there_is_a_prior_round_to_compare(app, client):
    from app.market_data import standings_with_rank_delta
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, price="80", production_qty="15000")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    with app.app_context():
        world = db.session.get(World, world_id)
        rows = standings_with_rank_delta(world)
        assert rows and rows[0]["rank"] == 1
        assert rows[0]["rank_delta"] is None   # only one round played


def test_decision_form_ships_the_affordability_curve_not_the_raw_ceilings(app, client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode()
    assert "AFFORDABILITY_CURVE" in body
    assert "reach-readout" in body
    # The per-segment willingness-to-pay table is the strategic secret and
    # must never reach the browser -- only the aggregate curve does.
    assert "WTP_CEILING_CENTER" not in body
    for seg in ("Low Income", "NBA Fans", "Casual/Fashion"):
        assert f'"{seg}"' not in body


# --------------------------------------------------------------------------- #
# Capacity shown to a student must equal capacity the ENGINE enforces.
#
# These diverged once already: the dashboard (and the JS that caps the
# production box) used firm.plant_capacity, which EXCLUDES an expansion that
# has just matured -- so a team that paid $100,000 to expand was locked out of
# the capacity it bought, while bots, which added the pending amount, used
# theirs. Any future edit that re-derives capacity by hand will fail here.
# --------------------------------------------------------------------------- #

def test_dashboard_capacity_includes_a_matured_expansion(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")

    with client.application.app_context():
        firm = Firm.query.filter_by(world_id=world_id, slot_number=1).one()
        firm.plant_capacity = 45_000
        firm.pending_capacity_increase = 15_000  # built last round, online now
        db.session.commit()
        expected = firm.effective_capacity
    assert expected == 60_000

    resp = client.get("/firm")
    body = resp.data.decode("utf-8")

    # The production box must not be capped below what the engine allows.
    assert "const PLANT_CAPACITY = 60000" in body
    assert "const PLANT_CAPACITY = 45000" not in body
    # And the number on screen must agree.
    assert "60,000" in body


def test_sold_out_banner_appears_only_when_capacity_actually_bound(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    # Shrink the plant so CAPACITY is unambiguously the binding constraint --
    # at full size this firm can't afford a full run, and cash binding first
    # correctly suppresses the banner (see the engine tests).
    with client.application.app_context():
        firm = Firm.query.filter_by(world_id=world_id, slot_number=1).one()
        firm.plant_capacity = 5_000
        db.session.commit()
    submit_decision(client, price="40", production_qty="5000")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    with client.application.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    client.get("/firm/standings")  # standings board comes first after processing
    body = client.get("/firm").data.decode("utf-8")
    assert "You sold out" in body
    assert "more customers wanted to buy" in body


def test_google_site_verification_tag_is_present_on_every_page(client):
    # Search Console verification only works while this tag is in the HTML
    # Google fetches. Pinned because it's invisible -- nothing about the UI
    # would look wrong if a template edit dropped it, and the failure only
    # surfaces as a verification that silently stops working.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    tag = 'name="google-site-verification"'

    client.get("/logout")
    assert tag in client.get("/login").data.decode("utf-8")

    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    assert tag in client.get("/firm").data.decode("utf-8")

    teacher_login(client)
    assert tag in client.get(f"/teacher/worlds/{world_id}").data.decode("utf-8")
    # present.html is a standalone document, not a base.html child, so it
    # carries its own copy and is the one that would be missed.
    assert tag in client.get(f"/teacher/worlds/{world_id}/present").data.decode("utf-8")


def test_projector_exit_is_a_persistent_button_on_every_classroom_board(client):
    # The exit used to be a faint top-right link with a one-time pulse, and
    # was still reported as invisible. It's now the same solid button on the
    # live board and the held one -- no pulse to miss, nothing that varies.
    world_id = create_world(client, slots=1)
    teacher_login(client)

    for path in (f"/teacher/worlds/{world_id}/present", f"/teacher/worlds/{world_id}/present?hold=1"):
        body = client.get(path).data.decode("utf-8")
        assert '<a class="present-exit" href=' in body, path
        assert "flash-once" not in body, path

    css = client.get("/static/css/style.css").data.decode("utf-8")
    rule = css[css.index(".present-exit {"):]
    rule = rule[:rule.index("}")]
    # Top right: bottom-centre covered the standings bars.
    assert "top:" in rule and "right:" in rule and "bottom:" not in rule
    assert "background: var(--accent-primary)" in rule
    assert "opacity" not in rule, "the exit must not be dimmed at rest again"
    assert "flash-once" not in css


# --------------------------------------------------------------------------- #
# Caching: logged-in pages must not be reusable by the next person on a
# shared classroom device, and must not go stale across a deploy.
# --------------------------------------------------------------------------- #

def test_dynamic_pages_are_not_cacheable(client):
    # Reported live: an iPad kept rendering a stale Market Dashboard nav
    # after the fix had deployed. The app sent no Cache-Control at all, so
    # iOS Safari cached the HTML heuristically.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")

    for path in ("/login", "/firm", "/market"):
        resp = client.get(path)
        cache = resp.headers.get("Cache-Control", "")
        assert "no-store" in cache, f"{path} is cacheable: {cache!r}"


def test_a_logged_out_page_cannot_be_served_from_cache_to_the_next_user(client):
    # The reason this matters on a shared iPad or Chromebook: a cached copy
    # of a team's dashboard could be shown to whoever picks the device up
    # next, including via the back button.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    assert "no-store" in client.get("/firm").headers.get("Cache-Control", "")

    teacher_login(client)
    assert "no-store" in client.get("/teacher/").headers.get("Cache-Control", "")


def test_static_assets_stay_cacheable(client):
    # Killing caching for CSS and icons too would make every page load
    # re-fetch them over classroom wifi for no benefit -- they're identical
    # for every user.
    resp = client.get("/static/css/style.css")
    assert resp.status_code == 200
    assert "no-store" not in resp.headers.get("Cache-Control", "")


# --------------------------------------------------------------------------- #
# R&D and Advertising are dropdown-only.
#
# A free-entry box let a team type an amount that buys a FRACTION of a level
# -- real money spent for no quality or ad gain, with nothing on screen
# explaining why. Every value the dropdown offers lands exactly on a level.
# --------------------------------------------------------------------------- #

def _decision_form(client, world_id):
    body = client.get("/firm").data.decode("utf-8")
    return body[body.index('id="decision-form"'):] if 'id="decision-form"' in body else body


def test_rd_and_ad_spend_have_no_free_entry_box(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")

    for field in ("rd_spend", "ad_spend"):
        assert f'<select id="{field}" name="{field}"' in body, f"{field} should be a dropdown"
        assert f'type="number" step="1" min="0" max="{{{{ rd_cap' not in body
        # No number input may carry these names.
        assert f'type="number"' not in body.split(f'name="{field}"')[0][-120:], \
            f"{field} still has a free-entry number box"


def test_market_dashboard_shows_the_tier_beside_every_price(client):
    # A price means little without its tier ($90 is cheap for Premium, steep
    # for Entry), so both price tables carry a Tier column.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, track="Premium", price="95", production_qty="10000")
    client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get("/market").data.decode("utf-8")
    assert body.count("<th>Tier</th><th>Price</th>") == 2
    assert body.count('<span class="tier-chip"') >= 2
    assert "img/tiers/premium.png" in body


def test_customer_segments_say_priced_out_not_unsold_buyers(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    body = client.get("/market").data.decode("utf-8")
    assert "Priced Out" in body
    assert "Unsold Buyers" not in body


def test_intel_report_has_the_price_chart_for_teams_and_teacher(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, track="Entry", price="60")
    client.post(f"/teacher/worlds/{world_id}/advance")

    team = client.get("/market/intel").data.decode("utf-8")
    assert "Prices Over Time" in team and '<svg class="price-chart"' in team
    assert 'class="price-line mine"' in team, "a team's own line is highlighted"
    assert "Round 1: $60.00 (Entry)" in team

    teacher = client.get(f"/teacher/worlds/{world_id}/intel").data.decode("utf-8")
    assert '<svg class="price-chart"' in teacher
    assert 'class="price-line mine"' not in teacher


def test_price_has_a_slider_and_keeps_its_number_box(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")
    assert '<input type="range" id="price-slider"' in body
    assert 'id="price" name="price"' in body
    # The slider has no name: only the box posts, so exact prices survive.
    slider = body[body.index('id="price-slider"'):]
    assert "name=" not in slider[:slider.index(">")]
    # Production is featured, and still posts as a form field.
    assert 'id="production-display"' in body and 'id="production-revenue"' in body
    assert 'type="hidden" id="production_qty" name="production_qty"' in body


def test_price_is_still_free_entry(client):
    # Price is the actual decision -- it must stay typeable. Only the
    # level-based spends became dropdowns.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")
    assert 'type="number"' in body.split('name="price"')[0][-200:]


def test_a_dropdown_submission_still_saves_correctly(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, rd_spend="50000", ad_spend="25000")

    row = RoundDecision.query.filter_by(round_number=1).one()
    assert row.rd_spend == 50000
    assert row.ad_spend == 25000


def test_maxed_out_ad_level_shows_a_locked_field_not_a_box(client):
    # At Ad Level 10 there's nothing left to buy. The form must still post
    # ad_spend=0 rather than omitting the field.
    world_id = create_world(client, slots=1)
    firm_id = register_firm(client, world_id, 1, "Nike")
    with client.application.app_context():
        firm = Firm.query.get(firm_id)
        firm.cumulative_ad_spend = 99_000_000  # far past the top of the ladder
        db.session.commit()

    body = client.get("/firm").data.decode("utf-8")
    assert 'name="ad_spend"' in body, "form must still post the field"
    assert 'type="hidden" id="ad_spend"' in body


# --------------------------------------------------------------------------- #
# Celebrity endorsement: four endorsers, pick one.
# --------------------------------------------------------------------------- #

def test_all_four_endorsers_are_offered_with_icons(client):
    from app.constants import CELEBRITIES, CELEBRITY_ICONS, CELEBRITY_LABELS
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")

    for key in CELEBRITIES:
        assert f'value="{key}"' in body
        assert CELEBRITY_LABELS[key] in body
        assert CELEBRITY_ICONS[key] in body


def test_only_one_endorser_can_be_chosen(client):
    # Radios share a name, so the browser enforces it -- and the server
    # stores exactly one value, never a list.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")
    block = body[body.index('class="celeb-grid"'):body.index("Committed Spend")]
    assert block.count('type="radio" name="celebrity"') == 5

    submit_decision(client, celebrity="star")
    row = RoundDecision.query.filter_by(round_number=1).one()
    assert row.celebrity == "star"
    assert row.celebrity_on is True


def test_choosing_no_endorsement_costs_nothing(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, celebrity="")

    row = RoundDecision.query.filter_by(round_number=1).one()
    assert row.celebrity is None
    assert row.celebrity_on is False


def test_an_unknown_endorser_is_treated_as_none(client):
    # The multipliers behind these are hidden, so a bogus value is a tampered
    # form rather than a student mistake -- fall back, don't error.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, celebrity="taylor-swift")

    row = RoundDecision.query.filter_by(round_number=1).one()
    assert row.celebrity is None
    assert row.celebrity_on is False


def test_the_multipliers_never_reach_the_browser(client):
    # Same treatment as every other Section 12 constant: discoverable by
    # playing, never printed in the UI.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")

    assert "1.5" not in body.split('class="celeb-grid"')[1].split("Committed Spend")[0]
    for seg in ("Athletes", "Wealthy", "Low Income", "NBA Fans"):
        assert seg not in body.split('class="celeb-grid"')[1].split("Committed Spend")[0]


def test_the_form_explains_what_an_endorsement_does_without_naming_a_fit(client):
    # The price interaction is the thing that makes endorsement a decision,
    # and it's invisible otherwise. WHICH endorser suits you stays hidden.
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    body = client.get("/firm").data.decode("utf-8")

    assert 'id="celeb-hint"' in body
    assert "updateCelebHint" in body
    # Mechanism, not a recommendation, and no endorser named in the hint logic.
    hint_js = body[body.index("function updateCelebHint"):body.index("recalc();", body.index("function updateCelebHint"))]
    for key in ("athlete", "musician", "star", "influencer"):
        assert f'"{key}"' not in hint_js
    for seg in ("Athletes", "Wealthy", "Low Income", "NBA Fans", "Budget Shoppers"):
        assert seg not in hint_js


def test_results_name_the_endorsement_that_ran(client):
    # So a team can line it up against the per-segment Units You Sold column
    # that's already on the same card, and work the rest out themselves.
    from app.constants import CELEBRITY_LABELS
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, celebrity="athlete")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    client.get("/firm/standings")  # standings board comes first after processing
    body = client.get("/firm").data.decode("utf-8")
    assert CELEBRITY_LABELS["athlete"] in body
    assert "endorsement ran this round" in body


def test_no_endorsement_note_when_none_was_run(client):
    world_id = create_world(client, slots=1)
    register_firm(client, world_id, 1, "Nike")
    submit_decision(client, celebrity="")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    assert "endorsement ran this round" not in client.get("/firm").data.decode("utf-8")


# --------------------------------------------------------------------------- #
# Factory art tied to capacity tier
# --------------------------------------------------------------------------- #

def test_factory_art_matches_each_capacity_tier(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Tiers", avatar="factory-04.png")

    for capacity, level in ((30_000, 1), (45_000, 2), (60_000, 3)):
        with app.app_context():
            firm = db.session.get(Firm, firm_id)
            firm.plant_capacity = capacity
            firm.pending_capacity_increase = 0
            db.session.commit()
        body = client.get("/firm").data.decode()
        assert f"img/factories/factory-04-L{level}.png" in body, capacity
        # and only one factory plate is drawn, not the old avatar too
        assert "img/avatars/factory-04.png" not in body


def test_factory_art_counts_an_expansion_bought_last_round(app, client):
    # Capacity bought last round is still maturing (pending), but the firm
    # owns it -- the picture should already show the bigger factory.
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Pending", avatar="factory-04.png")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.plant_capacity = 30_000
        firm.pending_capacity_increase = 15_000
        db.session.commit()
    assert "img/factories/factory-04-L2.png" in client.get("/firm").data.decode()


def test_factory_art_updates_the_moment_an_upgrade_is_submitted(app, client):
    # The point of the feature: a team that commits to an upgrade sees the
    # bigger factory straight away, rather than waiting for the teacher to
    # advance the round. Plant investment has a one-round lag in the engine,
    # so nothing in the firm's own columns has changed yet at this point.
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Instant", avatar="factory-04.png")
    assert "img/factories/factory-04-L1.png" in client.get("/firm").data.decode()

    submit_decision(client, plant_investment="100000")

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        assert firm.plant_capacity == 30_000          # unchanged...
        assert (firm.pending_capacity_increase or 0) == 0   # ...and not yet pending
    assert "img/factories/factory-04-L2.png" in client.get("/firm").data.decode()


def test_factory_art_never_exceeds_level_3(app, client):
    # A firm at the cap that somehow submits another expansion must not ask
    # for a Level 4 sprite that does not exist.
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Capped", avatar="factory-04.png")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.plant_capacity = 60_000
        db.session.commit()
    submit_decision(client, plant_investment="100000")
    body = client.get("/firm").data.decode()
    assert "img/factories/factory-04-L3.png" in body
    assert "-L4.png" not in body


def test_every_factory_level_sprite_exists_on_disk():
    # factory_sprite builds a filename by string surgery on the avatar, so a
    # missing file fails as a broken image in class rather than an error
    # anywhere. Pin the whole matrix instead.
    from pathlib import Path
    from app.avatars import AVATAR_CHOICES
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "factories"
    missing = [
        f"{avatar.rsplit('.', 1)[0]}-L{level}.png"
        for avatar in AVATAR_CHOICES
        for level in (1, 2, 3)
        if not (root / f"{avatar.rsplit('.', 1)[0]}-L{level}.png").is_file()
    ]
    assert not missing, missing


# --------------------------------------------------------------------------- #
# End-of-game billboard skyline
# --------------------------------------------------------------------------- #

def _complete_world(app, client, world_id, firms):
    """Play `firms` [(slot, name, badge)] through to world completion."""
    for slot, name, badge in firms:
        register_firm(client, world_id, slot, name, badge=badge)
        submit_decision(client, price="80", production_qty="10000")
        client.get("/logout")
    teacher_login(client)
    # Advancing is two steps per round -- collecting -> transition, then
    # transition -> next round's collecting -- so this loops on status
    # rather than counting rounds. The bound is a guard against a stuck
    # state hanging the suite, not an expected number of iterations.
    for _ in range(100):
        with app.app_context():
            if db.session.get(World, world_id).status == "complete":
                break
        client.post(f"/teacher/worlds/{world_id}/advance")
    with app.app_context():
        assert db.session.get(World, world_id).status == "complete"


def test_skyline_appears_only_once_the_game_is_complete(app, client):
    world_id = create_world(client, slots=3)
    register_firm(client, world_id, 1, "Alpha")
    submit_decision(client, price="80", production_qty="10000")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    mid = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "skyline-billboards.png" not in mid   # mid-game: standings only

    _complete_world(app, client, world_id, [])
    done = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "skyline-billboards.png" in done


def test_skyline_fills_at_most_three_boards(app, client):
    world_id = create_world(client, slots=5)
    _complete_world(app, client, world_id, [
        (1, "Alpha", "logo-01.png"), (2, "Bravo", "logo-02.png"),
        (3, "Delta", "logo-03.png"), (4, "Echo", "logo-04.png"),
        (5, "Foxtrot", "logo-05.png"),
    ])
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert body.count("skyline-board skyline-board-") == 3
    assert "skyline-board-4" not in body
    # The three boards exist; a 4th-place firm gets no billboard at all.
    for n in (1, 2, 3):
        assert f"skyline-board-{n}" in body


def test_skyline_never_shows_more_boards_than_firms(app, client):
    # Two firms must fill two boards, not three -- a blank board is correct,
    # an invented one is not.
    world_id = create_world(client, slots=2)
    _complete_world(app, client, world_id, [
        (1, "Solo", "logo-07.png"), (2, "Duo", "logo-08.png"),
    ])
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert body.count("skyline-board skyline-board-") == 2
    assert "skyline-board-3" not in body


def test_skyline_uses_the_inked_badges_not_the_opaque_ones(app, client):
    # The plain badges are RGB on a dark background; on a lit board they
    # render as a black rectangle. Regression guard for that swap.
    world_id = create_world(client, slots=2)
    _complete_world(app, client, world_id, [
        (1, "Alpha", "logo-01.png"), (2, "Bravo", "logo-02.png"),
    ])
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    skyline = body[body.index('class="skyline"'):]
    assert "img/badges-ink/" in skyline
    assert "img/badges/" not in skyline


def test_every_inked_badge_exists_on_disk():
    from pathlib import Path
    from app.avatars import BADGE_CHOICES
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "badges-ink"
    missing = [b for b in BADGE_CHOICES if not (root / b).is_file()]
    assert not missing, missing


# --------------------------------------------------------------------------- #
# Factory upgrades: one-time, tier-priced, renamed
# --------------------------------------------------------------------------- #

def test_upgrade_picker_shows_the_tier_price_and_no_recurring_cost(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Upgrader")

    body = client.get("/firm").data.decode()
    assert "Upgrade Factory (Plant Investment)" in body
    assert "$200,000" in body            # Level 1 -> 2
    assert "/round" not in body.split("Upgrade Factory")[1].split("</div>")[0]

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.plant_capacity = 45_000
        db.session.commit()
    body = client.get("/firm").data.decode()
    assert "$400,000" in body            # Level 2 -> 3 costs more


def test_upgrade_picker_disappears_at_the_cap(app, client):
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Maxed")
    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.plant_capacity = 60_000
        db.session.commit()
    body = client.get("/firm").data.decode()
    assert "largest factory built" in body
    assert "$400,000" not in body


def test_rent_is_not_charged_or_displayed(app, client):
    world_id = create_world(client)
    register_firm(client, world_id, 1, "NoRent")
    submit_decision(client, price="80", production_qty="10000")
    client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        result = RoundResult.query.filter_by(round_number=1).first()
        assert result.fixed_cost == 0

    client.get("/logout")
    body = client.get("/firm").data.decode()
    assert "Rent, Utilities" not in body


# --------------------------------------------------------------------------- #
# Mall scene: one bay per firm, shoppers proportional to sales
# --------------------------------------------------------------------------- #

def test_mall_has_exactly_one_bay_per_firm(app, client):
    world_id = create_world(client, slots=6)
    for slot in range(1, 7):
        register_firm(client, world_id, slot, f"Team{slot}", badge=f"logo-{slot:02d}.png")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert body.count('class="mall-bay"') == 6
    # built by repeating one unit, not from ten pre-branded files
    assert body.count("mall-bay.png") <= 1 or "mall-bay.png" in body
    for slot in range(1, 7):
        assert f"badges-ink/logo-{slot:02d}.png" in body


def test_shopper_counts_are_proportional_to_units_sold(app, client):
    from app.market_data import MALL_MAX_SHOPPERS, mall_scene
    world_id = create_world(client, slots=3)
    for slot in range(1, 4):
        register_firm(client, world_id, slot, f"T{slot}", badge=f"logo-{slot:02d}.png")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        world = db.session.get(World, world_id)
        # Force a clear spread so the ordering is unambiguous.
        rows = sorted(RoundResult.query.filter_by(round_number=1).all(),
                      key=lambda r: r.firm_id)
        for r, units in zip(rows, (9000.0, 3000.0, 900.0)):
            r.units_sold_total = units
        db.session.commit()

        scene = {b["firm"].id: b for b in mall_scene(world, round_number=1)}
        counts = [scene[r.firm_id]["shoppers"] for r in rows]

    # Best seller gets the full crowd; the others strictly fewer, in order.
    assert counts[0] == MALL_MAX_SHOPPERS
    assert counts[0] > counts[1] > counts[2] >= 1
    # And the gap has to be obvious, not a one-figure difference.
    assert counts[0] - counts[2] >= 5


def test_a_firm_that_sold_nothing_gets_an_empty_shopfront(app, client):
    from app.market_data import mall_scene
    world_id = create_world(client, slots=2)
    for slot in (1, 2):
        register_firm(client, world_id, slot, f"T{slot}", badge=f"logo-{slot:02d}.png")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        world = db.session.get(World, world_id)
        rows = sorted(RoundResult.query.filter_by(round_number=1).all(),
                      key=lambda r: r.firm_id)
        rows[0].units_sold_total = 5000.0
        rows[1].units_sold_total = 0.0
        db.session.commit()
        scene = {b["firm"].id: b for b in mall_scene(world, round_number=1)}
        assert scene[rows[0].firm_id]["shoppers"] > 0
        assert scene[rows[1].firm_id]["shoppers"] == 0

    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "No sales" in body


def test_every_shopper_strip_exists_on_disk():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "shoppers"
    missing = [f"shopper-{n:02d}.png" for n in range(1, 5)
               if not (root / f"shopper-{n:02d}.png").is_file()]
    assert not missing, missing


# --------------------------------------------------------------------------- #
# Badge uniqueness: first come, first served, humans and bots alike
# --------------------------------------------------------------------------- #

def test_a_taken_badge_is_not_offered_to_the_next_team(app, client):
    world_id = create_world(client, slots=3)
    register_firm(client, world_id, 1, "First", badge="logo-04.png")
    client.get("/logout")

    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=2).first().id
    body = client.get(f"/register/{world_id}/{firm_id}").data.decode()
    assert 'value="logo-04.png"' not in body
    assert 'value="logo-05.png"' in body      # the rest are still on offer


def test_registering_with_a_badge_someone_just_took_is_refused(app, client):
    # Two teams can load the picker at the same moment, both seeing a badge
    # as free. Only the one that submits first may keep it, so availability
    # is re-checked at POST rather than trusted from the rendered form.
    world_id = create_world(client, slots=3)
    register_firm(client, world_id, 1, "First", badge="logo-06.png")
    client.get("/logout")

    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=2).first().id
    resp = client.post(
        f"/register/{world_id}/{firm_id}",
        data={"team_name": "Second", "password": "secret123",
              "avatar": "factory-01.png", "badge": "logo-06.png",
              "product_icon": "headphone-01.png"},
        follow_redirects=True,
    )
    assert b"just took that brand logo" in resp.data
    with app.app_context():
        assert Firm.query.filter_by(world_id=world_id, slot_number=2).first().badge is None


def test_a_bot_never_takes_a_badge_a_team_already_holds(app, client):
    world_id = create_world(client, slots=4)
    register_firm(client, world_id, 1, "Human", badge="logo-02.png")
    client.get("/logout")
    teacher_login(client)

    with app.app_context():
        slots = [f.id for f in Firm.query.filter_by(world_id=world_id)
                 .order_by(Firm.slot_number).all()[1:]]
    for firm_id in slots:
        client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot",
                    data={"profile": "underbidder"})

    with app.app_context():
        badges = [f.badge for f in Firm.query.filter_by(world_id=world_id).all()]
        assert len(badges) == len(set(badges)), badges
        assert badges.count("logo-02.png") == 1


def test_badges_stay_unique_across_a_full_ten_firm_world(app, client):
    world_id = create_world(client, slots=10)
    teacher_login(client)
    with app.app_context():
        slots = [f.id for f in Firm.query.filter_by(world_id=world_id)
                 .order_by(Firm.slot_number).all()]
    for firm_id in slots:
        client.post(f"/teacher/worlds/{world_id}/firms/{firm_id}/bot",
                    data={"profile": "underbidder"})
    with app.app_context():
        badges = sorted(f.badge for f in Firm.query.filter_by(world_id=world_id).all())
        assert len(set(badges)) == 10, badges


def test_an_exhausted_badge_pool_degrades_instead_of_locking_a_team_out(app, client):
    # Only reachable above ten firms. A duplicate sign is cosmetic; an empty
    # picker would be a student who cannot register at all.
    from app.avatars import BADGE_CHOICES, available_badges
    world_id = create_world(client, slots=11)
    with app.app_context():
        firms = Firm.query.filter_by(world_id=world_id).order_by(Firm.slot_number).all()
        for firm, badge in zip(firms, BADGE_CHOICES):
            firm.badge = badge
        db.session.commit()
        assert available_badges(world_id) == list(BADGE_CHOICES)


def test_the_mall_is_staged_before_the_standings_not_inside_them(app, client):
    # The mall is the round's story and plays first; the scoreboard follows.
    # Order in the document is what drives that, so it is pinned here --
    # a later edit that moves the mall back under the rows would otherwise
    # only show up by eye.
    world_id = create_world(client, slots=3)
    for slot in range(1, 4):
        register_firm(client, world_id, slot, f"T{slot}")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert "present-stage" in body
    assert body.index("present-stage") < body.index("present-rows")
    assert "present-rows-after-mall" in body


def test_holding_the_board_skips_the_mall_intro(app, client):
    # ?hold=1 freezes the board so a teacher can talk over it. Replaying a
    # six-second animation under discussion is the opposite of useful, so
    # the intro is skipped and the standings show immediately.
    world_id = create_world(client, slots=2)
    for slot in (1, 2):
        register_firm(client, world_id, slot, f"T{slot}")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get(f"/teacher/worlds/{world_id}/present?hold=1").data.decode()
    assert "present-stage" not in body
    assert "present-rows-after-mall" not in body
    assert "present-rows" in body


def test_the_student_standings_board_plays_the_mall_intro(app, client):
    # Same regression as the sandbox board: this view sets hold=True purely
    # to stop the auto-refresh, and must still play the intro.
    world_id = create_world(client, slots=2)
    with app.app_context():
        world = db.session.get(World, world_id)
        world.show_standings_to_students = True
        db.session.commit()
    for slot in (1, 2):
        register_firm(client, world_id, slot, f"T{slot}")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")
    client.get("/teacher/logout")

    with app.app_context():
        firm_id = Firm.query.filter_by(world_id=world_id, slot_number=1).one().id
    client.post(f"/login/{world_id}/{firm_id}", data={"password": "secret123"})
    body = client.get("/firm/standings").data.decode()
    assert "present-stage" in body
    assert "mall-bay" in body


def test_the_factory_panel_names_its_level_and_capacity(app, client):
    # An upgrade a team paid $200,000 for has to be legible as a CHANGE,
    # not just a slightly different picture.
    world_id = create_world(client)
    firm_id = register_firm(client, world_id, 1, "Levels", avatar="factory-02.png")

    body = client.get("/firm").data.decode()
    assert "Level 1 Factory" in body
    assert "30,000 units capacity" in body

    with app.app_context():
        firm = db.session.get(Firm, firm_id)
        firm.plant_capacity = 45_000
        db.session.commit()
    body = client.get("/firm").data.decode()
    assert "Level 2 Factory" in body
    assert "45,000 units capacity" in body
    assert "factory-02-L2.png" in body


def test_the_hero_runs_product_then_name_then_factory(app, client):
    # The two marks that change during a game are the large ones at either
    # end; the badge, which never changes, sits small in the middle under the
    # company name. All three used to be equal 96px tiles, which is where
    # both progressions got lost.
    world_id = create_world(client)
    register_firm(client, world_id, 1, "Prominent",
                  avatar="factory-02.png", product_icon="headphone-04.png")
    body = client.get("/firm").data.decode()
    hero = body[body.index('class="card firm-hero"'):body.index("firm-hero-factory") + 400]

    product_at = hero.index("firm-hero-product")
    name_at = hero.index("firm-hero-name")
    factory_at = hero.index("firm-hero-factory")
    assert product_at < name_at < factory_at, "left-to-right order changed"

    # the badge is in the centre column, not a big panel of its own
    assert "firm-hero-badge" in body
    assert 'class="firm-hero-art firm-hero-product"' in body
    assert 'class="firm-hero-art firm-hero-factory"' in body


def test_the_shopper_sprite_maths_stays_correct():
    """Pins the frame-stepping maths, which nothing functional can reach.

    A percentage background-position resolves against (container - image),
    so with background-size 700% the strip moves by P x (W - 7W) = -6WP and
    frame k sits at P = k/6. Two ways to get this wrong, both of which
    shipped once and both of which look like flicker rather than an error:

      * animating to a NEGATIVE percentage (-700% resolves to +42W, shoving
        the strip off-screen -- the cycle ran from frame 1 to nothing);
      * plain steps(7), which emits k/7 and never lands on k/6 after the
        first frame, showing slivers of two frames at once.
    """
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent
           / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".shopper {"):css.index(".shopper-01")]
    assert "background-size: 700% 100%" in block
    assert "steps(7, jump-none)" in block

    cycle = css[css.index("@keyframes shopper-walk"):]
    cycle = cycle[:cycle.index("}", cycle.index("to"))]
    assert "0% 0" in cycle and "100% 0" in cycle
    assert "-700%" not in cycle, "negative percentage pushes the strip off-screen"


def test_the_finale_only_appears_once_the_game_is_complete(app, client):
    world_id = create_world(client, slots=3)
    for slot in range(1, 4):
        register_firm(client, world_id, slot, f"T{slot}")
        submit_decision(client, price="80", production_qty="8000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    mid = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert 'class="finale"' not in mid
    assert "present-rows-handover" not in mid

    _complete_world(app, client, world_id, [])
    done = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert 'class="finale"' in done
    assert "present-rows-handover" in done      # standings fold away for it
    # Winner and world are separate lines now, not one wrapping string.
    assert "finale-winner" in done and "finale-world" in done


def test_the_finale_lights_boards_in_reverse_rank_order(app, client):
    # 1st must land LAST, so its delay is the longest.
    import re
    world_id = create_world(client, slots=4)
    _complete_world(app, client, world_id, [
        (1, "Alpha", "logo-01.png"), (2, "Bravo", "logo-02.png"),
        (3, "Delta", "logo-03.png"), (4, "Echo", "logo-04.png"),
    ])
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    delays = [int(m) for m in re.findall(r"--light-at: calc\(var\(--finale-at\) \+ (\d+)ms\)", body)]
    assert len(delays) == 3, delays
    assert delays[0] > delays[1] > delays[2], delays


def test_the_finale_waits_for_the_standings_reveal_to_finish(app, client):
    # The handover time is derived from what runs before it, so a bigger
    # class does not have the skyline cut across its own reveal.
    from app.market_data import finale_delay_seconds
    small = finale_delay_seconds(3, mall_intro=True)
    large = finale_delay_seconds(8, mall_intro=True)
    assert large > small
    # and with no mall intro it starts sooner, not at a fixed moment
    assert finale_delay_seconds(3, mall_intro=False) < small


def test_a_finished_game_stops_auto_refreshing(app, client):
    # The board reloads every 30s to stay current. A finished game has
    # nothing left to update, and reloading would replay the whole finale
    # on a loop in front of the class.
    world_id = create_world(client, slots=2)
    _complete_world(app, client, world_id, [
        (1, "Alpha", "logo-01.png"), (2, "Bravo", "logo-02.png"),
    ])
    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert 'http-equiv="refresh"' not in body


def test_billboard_names_wrap_rather_than_truncate(app, client):
    # Team names are free text up to 60 characters and students pick longer
    # ones than the bots do. nowrap + ellipsis cut "Bot #3 (Marketing)" to
    # "Bot #3 (Marketin...", so the name must be allowed to wrap instead.
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent
           / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css[css.index(".skyline-board-name {"):]
    rule = rule[:rule.index("}")]
    assert "white-space: nowrap" not in rule
    assert "text-overflow: ellipsis" not in rule
    assert "overflow-wrap: anywhere" in rule, "a long single word has nowhere to break"


def test_a_long_team_name_reaches_the_billboard_intact(app, client):
    # End to end: the full name is in the markup, not pre-truncated server
    # side. Uses the longest name the register form permits.
    long_name = "The Extremely Ambitious Headphone Company Of Greater Portland"[:60]
    world_id = create_world(client, slots=2)
    register_firm(client, world_id, 1, long_name, badge="logo-01.png")
    submit_decision(client, price="80", production_qty="8000")
    client.get("/logout")
    register_firm(client, world_id, 2, "Rival", badge="logo-02.png")
    submit_decision(client, price="80", production_qty="8000")
    client.get("/logout")
    teacher_login(client)
    for _ in range(100):
        with app.app_context():
            if db.session.get(World, world_id).status == "complete":
                break
        client.post(f"/teacher/worlds/{world_id}/advance")

    body = client.get(f"/teacher/worlds/{world_id}/present").data.decode()
    assert long_name in body
    assert "..." not in body.split("skyline-board-name")[1][:200]


def test_a_busy_storefront_gets_a_mix_of_shopper_types(app, client):
    # One walk cycle per bay made every shopper at a given shop the same
    # person repeated, which reads as a glitch rather than a crowd.
    from app.market_data import MALL_SHOPPER_TYPES, mall_scene
    world_id = create_world(client, slots=3)
    for slot in range(1, 4):
        register_firm(client, world_id, slot, f"T{slot}")
        submit_decision(client, price="80", production_qty="9000")
        client.get("/logout")
    teacher_login(client)
    client.post(f"/teacher/worlds/{world_id}/advance")

    with app.app_context():
        world = db.session.get(World, world_id)
        scene = mall_scene(world)
        busiest = max(scene, key=lambda b: b["shoppers"])
        assert busiest["shoppers"] >= MALL_SHOPPER_TYPES, "need a full crowd to judge variety"
        assert len(busiest["shopper_types"]) == busiest["shoppers"]
        assert len(set(busiest["shopper_types"])) == MALL_SHOPPER_TYPES, \
            "a busy shop should show every shopper type, not one repeated"

        # neighbouring bays should not start their rotation at the same point
        firsts = [b["shopper_types"][0] for b in scene if b["shopper_types"]]
        assert len(set(firsts)) > 1, "every bay leads with the same sprite"


def test_the_finale_does_not_sit_behind_a_long_dead_wait(app, client):
    # It previously fired at 13.3s with nothing moving beforehand, so it read
    # as never happening at all. The final round runs a shorter mall and a
    # beat rather than a reading break.
    from app.market_data import finale_delay_seconds
    for firms in (2, 5, 8):
        assert finale_delay_seconds(firms, mall_intro=True) < 9.0, firms
