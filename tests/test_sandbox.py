"""
Sandbox mode: single-player vs bots, and unattended bots-only runs.

The load-bearing claim this mode makes is "same simulator, different
pacing". So the tests that matter most are the ones that would catch it
quietly becoming a second implementation: a sandbox round must produce the
same rows a classroom round does, and the sandbox password must not be a
back door into the Teacher Dashboard.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.blueprints.sandbox import BOT_ORDER, bot_display_name
from app.bots import BOT_PROFILES
from app.constants import MAX_SANDBOX_WORLDS, ROUNDS_PER_WORLD
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, SegmentRoundResult, World

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


def sandbox_login(client):
    """Kept as a no-op so the sandbox tests still read as "get into the
    sandbox, then...". There is no gate any more -- see
    test_sandbox_needs_no_password."""
    return None


def start_game(client, team_name="My Company", **bots):
    data = {"team_name": team_name}
    if bots:
        data.update(bots)
    else:
        data.update({f"bot_{p}": "on" for p in BOT_ORDER})
    return client.post("/sandbox/new", data=data)


def submit(client, **overrides):
    data = dict(price="80", production_qty="15000", ad_spend="0", rd_spend="0",
                track="Mid", plant_investment="0")
    data.update(overrides)
    return client.post("/firm/decisions", data=data)


# --------------------------------------------------------------------------- #
# Password gate -- sandbox must not be a weaker teacher login
# --------------------------------------------------------------------------- #

def test_sandbox_needs_no_password(client):
    # The gate is gone: the home page and game creation are both reachable
    # cold, with no session and no secret.
    assert client.get("/sandbox/").status_code == 200
    assert start_game(client).status_code == 302


def test_sandbox_login_route_is_gone(client):
    # Not merely unlinked -- removed, so nothing can still post a password
    # at it and no credential-shaped form survives on this path.
    assert client.get("/sandbox/login").status_code == 404
    assert client.post("/sandbox/login", data={"password": "x"}).status_code == 404


def test_open_sandbox_grants_no_teacher_access(client):
    # The point that survives losing the sandbox password: reaching the
    # sandbox must not let anyone process a real class's rounds or reset a
    # team's password.
    start_game(client)
    resp = client.get("/teacher/", follow_redirects=True)
    assert b"Teacher login required" in resp.data


def test_sandbox_player_cannot_be_claimed_through_the_game_code_door(client):
    # Sandbox worlds carry a game code like any other, and /login does not
    # filter by mode -- so the thing that keeps a stranger out of someone's
    # sandbox firm is that the firm already holds a password hash nobody was
    # ever shown. If a sandbox firm ever became unregistered, that code would
    # lead to the register page and hand the game away.
    start_game(client)
    with client.application.app_context():
        world = World.query.filter_by(mode="sandbox").first()
        player = Firm.query.filter_by(world_id=world.id, slot_number=1).first()
        assert player.is_registered
        code, world_id, firm_id = world.game_code, world.id, player.id

    fresh = client.application.test_client()
    fresh.post("/login", data={"game_code": code})
    resp = fresh.get(f"/login/{world_id}/{firm_id}", follow_redirects=True)
    assert b"Choose a Password" not in resp.data


def test_resume_route_is_gone(client):
    # It was the only way back into a sandbox game, and it logged you in by
    # guessable integer. Removed rather than re-gated.
    start_game(client)
    with client.application.app_context():
        world_id = World.query.filter_by(mode="sandbox").first().id
    assert client.get(f"/sandbox/play/{world_id}").status_code == 404


def test_login_screen_links_to_the_sandbox(client):
    body = client.get("/login").data.decode("utf-8")
    assert "/sandbox/" in body
    # and does it without asking for a credential
    assert 'name="password"' not in body.split("login-panel-sandbox")[1].split("</section>")[0]


# --------------------------------------------------------------------------- #
# World setup
# --------------------------------------------------------------------------- #

def test_new_game_creates_a_player_plus_the_chosen_bots(client):
    sandbox_login(client)
    start_game(client, team_name="Auralis")

    world = World.query.filter_by(mode="sandbox").one()
    firms = Firm.query.filter_by(world_id=world.id).order_by(Firm.slot_number).all()
    assert len(firms) == 1 + len(BOT_ORDER)
    assert firms[0].team_name == "Auralis"
    assert firms[0].bot_profile is None
    assert all(f.bot_profile for f in firms[1:])


def test_bot_numbering_matches_the_roster_order(client):
    # #1 Underbidder, #2 Marketing, #3 Elite, #4 Random -- numbering is by
    # slot in BOT_ORDER, and the UI must not drift from it.
    assert [BOT_PROFILES[p] for p in BOT_ORDER] == [
        "Underbidder", "Marketing", "Elite", "Random"
    ]
    assert bot_display_name(1, "underbidder") == "Bot #1 (Underbidder)"
    assert bot_display_name(2, "marketing") == "Bot #2 (Marketing)"
    assert bot_display_name(3, "elite") == "Bot #3 (Elite)"
    assert bot_display_name(4, "random") == "Bot #4 (Random)"


def test_choosing_a_subset_of_bots_is_respected(client):
    sandbox_login(client)
    start_game(client, bot_marketing="on", bot_elite="on")

    world = World.query.filter_by(mode="sandbox").one()
    profiles = [f.bot_profile for f in Firm.query.filter_by(world_id=world.id)
                .order_by(Firm.slot_number).all() if f.bot_profile]
    assert profiles == ["marketing", "elite"]


def test_a_game_with_no_bots_ticked_still_gets_a_full_field(client):
    # A stray empty submit shouldn't hand the player a one-firm monopoly.
    sandbox_login(client)
    client.post("/sandbox/new", data={"team_name": "Solo"})

    world = World.query.filter_by(mode="sandbox").one()
    bots = [f for f in Firm.query.filter_by(world_id=world.id) if f.bot_profile]
    assert len(bots) == len(BOT_ORDER)


def test_new_world_records_its_own_round_count(client):
    sandbox_login(client)
    start_game(client)
    world = World.query.filter_by(mode="sandbox").one()
    assert world.rounds == ROUNDS_PER_WORLD


# --------------------------------------------------------------------------- #
# Visibility -- sandbox worlds stay off the Teacher Dashboard
# --------------------------------------------------------------------------- #

def test_sandbox_worlds_never_appear_on_the_teacher_dashboard(client):
    sandbox_login(client)
    start_game(client, team_name="ShouldBeHidden")
    client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})

    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    body = client.get("/teacher/").data.decode("utf-8")
    assert "ShouldBeHidden" not in body
    assert "Bots-Only Run" not in body


def test_classroom_worlds_still_appear(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/teacher/worlds", data={"name": "Period 3", "planned_firm_slots": "2"})
    body = client.get("/teacher/").data.decode("utf-8")
    assert "Period 3" in body
    assert World.query.filter_by(name="Period 3").one().mode == "classroom"


# --------------------------------------------------------------------------- #
# The round loop -- no teacher step
# --------------------------------------------------------------------------- #

def test_submitting_runs_the_round_with_no_separate_click(client):
    # There used to be a "Run Round" button after submitting. With no one
    # else to wait for it was a pointless extra step -- submit IS the trigger.
    sandbox_login(client)
    start_game(client)
    world = World.query.filter_by(mode="sandbox").one()
    assert world.current_round == 1

    submit(client)

    world = World.query.filter_by(mode="sandbox").one()
    assert world.current_round == 2, "should advance without a teacher or a second click"
    assert world.status == "collecting", "next round should already be open"
    assert "Run Round" not in client.get("/firm").data.decode("utf-8")


def test_a_stale_run_round_post_does_not_play_a_round_for_the_player(client):
    # The old button's route still exists for games left mid-round. A repeat
    # or stale POST must NOT play the next round on the player's behalf.
    sandbox_login(client)
    start_game(client)
    submit(client)                      # runs round 1, opens round 2
    client.post("/sandbox/round")       # nothing submitted for round 2

    world = World.query.filter_by(mode="sandbox").one()
    assert world.current_round == 2
    assert RoundResult.query.filter_by(round_number=2).count() == 0


def test_a_game_left_with_a_submitted_unrun_round_finishes_it(client):
    # A sandbox game that had submitted but not clicked Run Round before this
    # change: the dashboard finishes the round itself (auto-POST), no button.
    sandbox_login(client)
    start_game(client)
    world = World.query.filter_by(mode="sandbox").one()
    player = Firm.query.filter_by(world_id=world.id, slot_number=1).one()
    db.session.add(RoundDecision(firm_id=player.id, round_number=1, price=80,
                                 production_qty=1000, track="Mid"))
    db.session.commit()

    body = client.get("/firm").data.decode("utf-8")
    assert "Run Round" not in body
    assert 'id="finish-round"' in body and "sandbox/round" in body

    resp = client.post("/sandbox/round")
    assert "/sandbox/results/" in resp.headers["Location"]
    assert World.query.filter_by(mode="sandbox").one().current_round == 2


def test_a_sandbox_round_produces_the_same_rows_a_classroom_round_does(client):
    # The core "same simulator" claim: one sandbox round writes a full set
    # of result rows for every firm plus the segment-level rows, exactly as
    # a teacher-processed round would.
    sandbox_login(client)
    start_game(client)
    submit(client)
    client.post("/sandbox/round")

    world = World.query.filter_by(mode="sandbox").one()
    firm_count = Firm.query.filter_by(world_id=world.id).count()
    results = (RoundResult.query.join(Firm)
               .filter(Firm.world_id == world.id, RoundResult.round_number == 1).all())
    assert len(results) == firm_count
    assert SegmentRoundResult.query.filter_by(world_id=world.id, round_number=1).count() == 5


def test_bots_act_every_round_without_being_triggered(client):
    sandbox_login(client)
    start_game(client)
    submit(client)
    client.post("/sandbox/round")

    world = World.query.filter_by(mode="sandbox").one()
    bot_ids = [f.id for f in Firm.query.filter_by(world_id=world.id) if f.bot_profile]
    for bot_id in bot_ids:
        assert RoundDecision.query.filter_by(firm_id=bot_id, round_number=1).count() == 1


def test_round_results_go_to_the_projector_view_then_the_market(client):
    sandbox_login(client)
    start_game(client)
    resp = submit(client)
    assert "/sandbox/results/" in resp.headers["Location"]

    world = World.query.filter_by(mode="sandbox").one()
    body = client.get(f"/sandbox/results/{world.id}").data.decode("utf-8")
    assert "present-exit" in body, "should be the existing projector view"
    assert "/market" in body, "its exit should lead to the Market Dashboard"


def test_playing_all_the_way_through_completes_the_game(client):
    sandbox_login(client)
    start_game(client)
    for _ in range(ROUNDS_PER_WORLD):
        submit(client)
        client.post("/sandbox/round")

    world = World.query.filter_by(mode="sandbox").one()
    assert world.status == "complete"
    assert world.current_round == ROUNDS_PER_WORLD


def test_a_finished_game_cannot_be_pushed_past_its_last_round(client):
    sandbox_login(client)
    start_game(client)
    for _ in range(ROUNDS_PER_WORLD):
        submit(client)
        client.post("/sandbox/round")

    client.post("/sandbox/round")  # stale POST / double click
    world = World.query.filter_by(mode="sandbox").one()
    assert world.current_round == ROUNDS_PER_WORLD
    assert world.status == "complete"


def test_the_sandbox_round_route_refuses_a_classroom_world(client):
    # A classroom team must not be able to self-process and skip the
    # teacher's pacing by POSTing here.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/teacher/worlds", data={"name": "Period 3", "planned_firm_slots": "1"})
    world = World.query.filter_by(name="Period 3").one()
    firm = Firm.query.filter_by(world_id=world.id).first()
    client.post(f"/register/{world.id}/{firm.id}", data={
        "team_name": "Nike", "password": "secret123", "avatar": "factory-01.png",
        "badge": "logo-01.png", "product_icon": "headphone-01.png"})
    submit(client)

    client.post("/sandbox/round")

    world = World.query.filter_by(name="Period 3").one()
    assert world.current_round == 1
    assert RoundResult.query.count() == 0


def test_player_sees_last_rounds_results_after_advancing(client):
    # Sandbox skips the transition screen, so the dashboard must recap the
    # round just played -- otherwise the player never sees their own
    # affordability table or the sold-out warning.
    sandbox_login(client)
    start_game(client)
    submit(client)
    client.post("/sandbox/round")

    body = client.get("/firm").data.decode("utf-8")
    assert "Round 1 Results" in body


# --------------------------------------------------------------------------- #
# Bots-only runs
# --------------------------------------------------------------------------- #

def test_bots_only_run_plays_every_round_then_opens_that_games_dashboard(client):
    # It used to return a CSV download, which gave you a file and nowhere to
    # look. Landing on the world page puts the round tables, scouting report
    # and Export CSV in front of you instead.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    resp = client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})

    world = World.query.filter_by(mode="bots_only").one()
    assert resp.status_code == 302
    assert f"/teacher/worlds/{world.id}" in resp.headers["Location"]
    assert world.status == "complete"
    assert world.current_round == ROUNDS_PER_WORLD

    body = client.get(f"/teacher/worlds/{world.id}").data.decode("utf-8")
    assert "Export CSV" in body, "the CSV must still be one click away"


def test_bots_only_world_has_no_human_firm(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})

    world = World.query.filter_by(mode="bots_only").one()
    firms = Firm.query.filter_by(world_id=world.id).all()
    assert firms and all(f.bot_profile for f in firms)


def test_bots_only_csv_uses_the_existing_export_format(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})
    world = World.query.filter_by(mode="bots_only").one()
    body = client.get(f"/teacher/worlds/{world.id}/export.csv").data.decode("utf-8")

    assert "Team Name" in body  # same header row the Teacher Dashboard emits
    assert "Bot #1 (Underbidder)" in body
    # Every round of the run is present, not just the last.
    assert body.count("Bot #1 (Underbidder)") >= ROUNDS_PER_WORLD


def test_bots_only_requires_the_teacher_password(client):
    # It lives on the Teacher Dashboard and hands off to a teacher-only page,
    # so the teacher session is the right gate -- and the sandbox password
    # must NOT be enough on its own.
    resp = client.post("/sandbox/bots", data={"bot_elite": "on"}, follow_redirects=True)
    assert b"Teacher login required" in resp.data
    assert World.query.filter_by(mode="bots_only").count() == 0

    sandbox_login(client)
    client.post("/sandbox/bots", data={"bot_elite": "on"}, follow_redirects=True)
    assert World.query.filter_by(mode="bots_only").count() == 0


def test_student_sandbox_page_no_longer_offers_bots_only(client):
    sandbox_login(client)
    body = client.get("/sandbox/").data.decode("utf-8")
    assert "sandbox/bots" not in body
    assert "New Single-Player Game" in body


def test_teacher_dashboard_offers_the_balance_run(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    body = client.get("/teacher/").data.decode("utf-8")
    assert "sandbox/bots" in body
    assert "Bot #1 (Underbidder)" in body


def test_finished_balance_runs_stay_reachable_without_burying_class_periods(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/teacher/worlds", data={"name": "Period 3", "planned_firm_slots": "2"})
    client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})

    run = World.query.filter_by(mode="bots_only").one()
    body = client.get("/teacher/").data.decode("utf-8")
    assert "Period 3" in body, "real class periods still listed"
    assert f"/teacher/worlds/{run.id}" in body, "finished run must not be lost"
    # ...but not mixed into Your Worlds.
    your_worlds = body[body.index("Your Worlds"):]
    assert "Bots-Only Run" not in your_worlds


def test_sandbox_setup_collects_no_password(client):
    # A visible input named "password", prefilled, posting to a fresh URL on
    # a shared *.onrender.com domain reads as a phishing form -- Safe
    # Browsing flagged this page as deceptive over it. The field was never
    # used for anything (the sandbox password gates the door and Resume
    # signs the player in directly), so it must stay gone.
    sandbox_login(client)
    body = client.get("/sandbox/").data.decode("utf-8")
    form = body[body.index("sandbox/new"):body.index("Start Playing")]
    assert 'name="password"' not in form
    assert 'type="password"' not in form


def test_a_sandbox_player_still_gets_an_unguessable_credential(client):
    # Removing the field must not leave the firm with a blank or shared
    # password -- the slot would otherwise be claimable by anyone who
    # reached the normal team login.
    sandbox_login(client)
    start_game(client)
    player = Firm.query.filter_by(slot_number=1).one()
    assert player.password_hash
    assert not player.check_password("sandbox")
    assert not player.check_password("")


def test_sandbox_results_board_has_the_persistent_exit_button(client):
    # Reported live: the pulsing exit on this board didn't read as visible.
    # Same solid, always-coloured button as every other projector board now.
    sandbox_login(client)
    start_game(client)
    submit(client)
    client.post("/sandbox/round")

    world = World.query.filter_by(mode="sandbox").one()
    body = client.get(f"/sandbox/results/{world.id}").data.decode("utf-8")
    assert '<a class="present-exit" href="/market"' in body
    assert "flash-once" not in body
    assert "http-equiv=\"refresh\"" not in body, "a frozen board must not reload"


def test_teacher_dashboard_offers_a_way_into_the_sandbox(client):
    # A direct form, not a link to the sandbox password gate -- a teacher is
    # already authenticated, so sending them off to type a second password
    # to reach their own machine's practice mode was pure friction.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    body = client.get("/teacher/").data.decode("utf-8")
    assert "/sandbox/new-from-teacher" in body
    assert "Play a Single-Player Game" in body


def test_sandbox_link_from_the_teacher_dashboard_opens_directly(client):
    # The sandbox has no password of its own to ask for any more, so the
    # teacher's link just opens it. The direction that still matters is the
    # other one, covered by test_open_sandbox_grants_no_teacher_access:
    # reaching the sandbox must confer nothing on the Teacher Dashboard.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    resp = client.get("/sandbox/")
    assert resp.status_code == 200
    assert b"New Single-Player Game" in resp.data


def test_entry_points_use_american_spelling(client):
    # "Practicing", not "Practising" -- and the British spelling must not
    # reappear on any surface, including ones that drop the word later.
    body = client.get("/login").data.decode("utf-8")
    assert "Practicing" in body
    assert "Practising" not in body

    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    assert "Practising" not in client.get("/teacher/").data.decode("utf-8")

    sandbox_login(client)
    assert "Practising" not in client.get("/sandbox/").data.decode("utf-8")


# --------------------------------------------------------------------------- #
# Two doors into the same single-player game
# --------------------------------------------------------------------------- #

def test_student_entry_points_still_offer_single_player(client):
    # Removing the bots-only card must not have taken single-player with it.
    body = client.get("/login").data.decode("utf-8")
    assert "/sandbox/" in body

    sandbox_login(client)
    home = client.get("/sandbox/").data.decode("utf-8")
    assert "New Single-Player Game" in home
    assert "Start Playing" in home


def test_teacher_can_start_a_single_player_game_directly(client):
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    body = client.get("/teacher/").data.decode("utf-8")
    assert "/sandbox/new-from-teacher" in body

    resp = client.post("/sandbox/new-from-teacher", data={
        "team_name": "Teacher Test", **{f"bot_{p}": "on" for p in BOT_ORDER},
    })
    assert "/firm" in resp.headers["Location"]

    world = World.query.filter_by(mode="sandbox").one()
    firms = Firm.query.filter_by(world_id=world.id).order_by(Firm.slot_number).all()
    assert firms[0].team_name == "Teacher Test"
    assert len(firms) == 1 + len(BOT_ORDER)


def test_teacher_stays_signed_in_as_teacher_while_playing(client):
    # Firm and teacher sessions are scoped separately, so starting a game
    # must not sign the teacher out of their own dashboard.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/new-from-teacher", data={"team_name": "Teacher Test"})

    assert client.get("/firm").status_code == 200
    assert client.get("/teacher/").status_code == 200


def test_both_doors_build_the_same_shape_of_game(client):
    # One helper builds both, so they can't drift apart.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/new-from-teacher", data={
        "team_name": "ViaTeacher", **{f"bot_{p}": "on" for p in BOT_ORDER}})
    client.get("/teacher/logout")
    client.get("/logout")

    sandbox_login(client)
    start_game(client, team_name="ViaSandbox")

    a, b = World.query.filter_by(mode="sandbox").order_by(World.id).all()
    for w in (a, b):
        assert w.rounds == ROUNDS_PER_WORLD
        assert w.mode == "sandbox"
    shape = lambda w: [f.bot_profile for f in Firm.query.filter_by(world_id=w.id)
                       .order_by(Firm.slot_number).all()]
    assert shape(a) == shape(b)


def test_the_teacher_door_still_requires_a_teacher(client):
    resp = client.post("/sandbox/new-from-teacher", data={"team_name": "Nope"},
                       follow_redirects=True)
    assert b"Teacher login required" in resp.data
    assert World.query.filter_by(mode="sandbox").count() == 0


def test_the_sandbox_page_lists_nobodys_games(client):
    # It used to list EVERY sandbox world anyone had created, with a Resume
    # button that signed you into that world's firm -- so on a shared
    # classroom device one student could drop straight into another's
    # practice game. There is no per-player ownership on a sandbox world to
    # filter by, so the list is gone rather than narrowed.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/bots", data={f"bot_{p}": "on" for p in BOT_ORDER})
    client.get("/teacher/logout")

    sandbox_login(client)
    start_game(client, team_name="SomeonesGame")
    client.get("/logout")

    body = client.get("/sandbox/").data.decode("utf-8")
    assert "SomeonesGame" not in body, "another player's game must not be listed"
    assert "Recent Sandbox Games" not in body
    assert "/sandbox/play/" not in body, "no Resume links"
    assert "New Single-Player Game" in body, "starting a game still works"


# --------------------------------------------------------------------------- #
# Icon choice -- a sandbox player picks their identity like a real team
# --------------------------------------------------------------------------- #

def test_both_single_player_forms_offer_the_icon_picker(client):
    from app.avatars import AVATAR_CHOICES

    sandbox_login(client)
    student = client.get("/sandbox/").data.decode("utf-8")
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    teacher = client.get("/teacher/").data.decode("utf-8")

    for body, where in ((student, "sandbox page"), (teacher, "teacher dashboard")):
        assert 'name="avatar"' in body, f"{where} missing factory picker"
        assert 'name="badge"' in body, f"{where} missing logo picker"
        assert 'name="product_icon"' in body, f"{where} missing product picker"
        assert AVATAR_CHOICES[1] in body, f"{where} offers only one choice"


def test_picked_icons_are_saved_on_the_player_firm(client):
    from app.avatars import AVATAR_CHOICES, BADGE_CHOICES, PRODUCT_CHOICES

    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/new-from-teacher", data={
        "team_name": "Picky",
        "avatar": AVATAR_CHOICES[3],
        "badge": BADGE_CHOICES[2],
        "product_icon": PRODUCT_CHOICES[1],
    })

    player = Firm.query.filter_by(slot_number=1).one()
    assert player.avatar == AVATAR_CHOICES[3]
    assert player.badge == BADGE_CHOICES[2]
    assert player.product_icon == PRODUCT_CHOICES[1]


def test_the_student_door_saves_picked_icons_too(client):
    from app.avatars import AVATAR_CHOICES

    sandbox_login(client)
    client.post("/sandbox/new", data={"team_name": "Picky", "avatar": AVATAR_CHOICES[5]})
    assert Firm.query.filter_by(slot_number=1).one().avatar == AVATAR_CHOICES[5]


def test_an_unknown_icon_falls_back_instead_of_saving_a_broken_image(client):
    # A filename that isn't a real asset would render as a broken <img>
    # forever -- that already happened once when an asset swap left stale
    # names in the DB. Never trust the posted value.
    from app.avatars import AVATAR_CHOICES, BADGE_CHOICES

    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/new-from-teacher", data={
        "team_name": "Sneaky", "avatar": "../../etc/passwd", "badge": "nope.png",
    })

    player = Firm.query.filter_by(slot_number=1).one()
    assert player.avatar in AVATAR_CHOICES
    assert player.badge in BADGE_CHOICES


def test_registration_and_sandbox_share_one_picker(client):
    # Both render the same partial, so a change to the asset lists can't
    # leave one surface offering icons the other doesn't.
    world_id = None
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/teacher/worlds", data={"name": "Period 9", "planned_firm_slots": "1"})
    world = World.query.filter_by(name="Period 9").one()
    firm = Firm.query.filter_by(world_id=world.id).first()
    client.get("/teacher/logout")

    register_page = client.get(f"/register/{world.id}/{firm.id}").data.decode("utf-8")
    sandbox_login(client)
    sandbox_page = client.get("/sandbox/").data.decode("utf-8")

    for field in ('name="avatar"', 'name="badge"', 'name="product_icon"'):
        assert field in register_page
        assert field in sandbox_page


def test_market_dashboard_keeps_a_sandbox_player_in_their_game(client):
    # Reported live: a teacher playing a sandbox game hit Exit on the
    # standings board and landed on a Market Dashboard whose every nav link
    # walked them back out of the game. The nav used to branch on "is there
    # a teacher session" rather than "which view is this".
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/sandbox/new-from-teacher", data={"team_name": "My Company"})
    submit(client)
    client.post("/sandbox/round")

    body = client.get("/market").data.decode("utf-8")
    assert 'href="/firm"' in body, "no way back into the game"
    assert "Back to My Company" in body
    # The teacher link is still offered, but as an explicit exit -- not as
    # the primary action, and not labelled with the sandbox world's name.
    assert "Exit to Teacher Dashboard" in body
    assert "Teacher Dashboard &mdash; Sandbox" not in body


def test_teachers_own_market_view_still_gets_teacher_nav(client):
    # The counterpart: /teacher/worlds/<id>/market is genuinely the
    # teacher's view and must keep pointing back at the world page.
    client.post("/teacher/login", data={"password": TEACHER_PASSWORD})
    client.post("/teacher/worlds", data={"name": "Period 4", "planned_firm_slots": "2"})
    world = World.query.filter_by(name="Period 4").one()

    body = client.get(f"/teacher/worlds/{world.id}/market").data.decode("utf-8")
    assert f"/teacher/worlds/{world.id}" in body
    assert "Back to" not in body


def test_a_sandbox_game_is_unreachable_once_its_session_ends(client):
    # This is the property that makes eviction safe, so it is worth pinning
    # rather than leaving implied. Resume is gone, and the player firm holds
    # a random hash nobody was shown, so a game cannot be re-entered by URL,
    # by game code, or by password once the session that made it is over.
    start_game(client, team_name="MyOwnGame")
    world = World.query.filter_by(mode="sandbox").one()
    client.get("/logout")

    assert client.get(f"/sandbox/play/{world.id}").status_code == 404

    resp = client.post("/login", data={"game_code": world.game_code},
                       follow_redirects=True)
    assert b"MyOwnGame" in resp.data          # the slot is listed...
    firm = Firm.query.filter_by(world_id=world.id, slot_number=1).one()
    resp = client.get(f"/login/{world.id}/{firm.id}", follow_redirects=True)
    assert b"Choose a Password" not in resp.data   # ...but not claimable
    assert b"Enter Team Password" in resp.data or b"Password" in resp.data


def test_sandbox_tour_opens_on_every_games_round_1_and_not_later(client):
    import json, re
    sandbox_login(client)
    start_game(client)

    def tour_settings(body):
        key = json.loads(re.search(r"const KEY = (.*?);", body).group(1))
        auto = json.loads(re.search(r"const AUTO = (.*?);", body).group(1))
        return key, auto

    round1 = client.get("/firm").data.decode("utf-8")
    assert tour_settings(round1) == (None, True), "no memory: every load of Round 1 opens it"
    assert "Rewatch the Page Intro" in round1

    submit(client)                                  # runs round 1
    round2 = client.get("/firm").data.decode("utf-8")
    assert tour_settings(round2) == (None, False), "later rounds: replay button only"
    assert "Rewatch the Page Intro" in round2


def test_bots_never_reuse_the_players_or_each_others_icons(client):
    from app.avatars import pick_unused_icons
    # Repeat: picks are random, so one lucky run proves little.
    for i in range(15):
        sandbox_login(client)
        start_game(client, team_name=f"Probe {i}", avatar="factory-03.png",
                   badge="logo-03.png", product_icon="headphone-03.png")
    for world in World.query.filter_by(mode="sandbox").all():
        firms = Firm.query.filter_by(world_id=world.id).all()
        assert len(firms) == 1 + len(BOT_ORDER)
        for field in ("avatar", "badge", "product_icon"):
            values = [getattr(f, field) for f in firms]
            assert len(set(values)) == len(values), f"{world.name}: duplicate {field} {values}"


def test_icon_picker_falls_back_to_least_used_when_a_pool_runs_out():
    from types import SimpleNamespace
    from app.avatars import AVATAR_CHOICES, pick_unused_icons
    everyone = [SimpleNamespace(avatar=a, badge=None, product_icon=None) for a in AVATAR_CHOICES]
    everyone.append(SimpleNamespace(avatar=AVATAR_CHOICES[0], badge=None, product_icon=None))
    picked = pick_unused_icons(everyone)
    assert picked["avatar"] in AVATAR_CHOICES[1:], "must pick one of the least-used, not the doubled one"


# --------------------------------------------------------------------------- #
# Capacity: the brake that replaced the password
# --------------------------------------------------------------------------- #

def _make_sandbox_worlds(n):
    """Bare sandbox worlds, straight into the DB. Deliberately not via
    start_game -- this is about the count, and building n real games with
    firms and round rows would make the test slow for no extra coverage."""
    from app.blueprints.teacher import _generate_game_code
    made = []
    for _ in range(n):
        w = World(name="Sandbox -- filler", game_code=_generate_game_code(),
                  planned_firm_slots=1, mode="sandbox", rounds=ROUNDS_PER_WORLD)
        db.session.add(w)
        made.append(w)
    db.session.commit()
    return [w.id for w in made]


def test_sandbox_worlds_are_capped(app, client):
    with app.app_context():
        _make_sandbox_worlds(MAX_SANDBOX_WORLDS)
        assert World.query.filter_by(mode="sandbox").count() == MAX_SANDBOX_WORLDS

    start_game(client)

    with app.app_context():
        # The new game is in, and the count did not grow past the ceiling.
        assert World.query.filter_by(mode="sandbox").count() == MAX_SANDBOX_WORLDS


def test_cap_evicts_the_oldest_and_keeps_the_newest(app, client):
    with app.app_context():
        ids = _make_sandbox_worlds(MAX_SANDBOX_WORLDS)
        oldest, newest = ids[0], ids[-1]

    start_game(client)

    with app.app_context():
        assert World.query.get(oldest) is None
        assert World.query.get(newest) is not None


def test_cap_leaves_classroom_worlds_alone(app, client):
    # The cap exists to protect the classroom game, so it must never be the
    # thing that deletes one.
    with app.app_context():
        classroom = World(name="Period 1", game_code="CLASS1",
                          planned_firm_slots=4, mode="classroom",
                          rounds=ROUNDS_PER_WORLD)
        db.session.add(classroom)
        db.session.commit()
        classroom_id = classroom.id
        _make_sandbox_worlds(MAX_SANDBOX_WORLDS)

    start_game(client)

    with app.app_context():
        assert World.query.get(classroom_id) is not None


def test_eviction_takes_the_whole_world_with_it(app, client):
    # Eviction leans on World's cascades; a partial delete would leave
    # orphaned firms and round rows behind, which is the exact growth the
    # cap is meant to stop.
    start_game(client, team_name="Doomed")
    with app.app_context():
        world = World.query.filter_by(mode="sandbox").first()
        doomed_id = world.id
        assert Firm.query.filter_by(world_id=doomed_id).count() > 0
        # Fill to the ceiling so the next creation evicts this one.
        _make_sandbox_worlds(MAX_SANDBOX_WORLDS)

    start_game(client, team_name="Survivor")

    with app.app_context():
        assert World.query.get(doomed_id) is None
        assert Firm.query.filter_by(world_id=doomed_id).count() == 0
        assert SegmentRoundResult.query.filter_by(world_id=doomed_id).count() == 0


def test_under_the_cap_nothing_is_evicted(app, client):
    start_game(client, team_name="First")
    with app.app_context():
        first_id = World.query.filter_by(mode="sandbox").first().id

    start_game(client, team_name="Second")

    with app.app_context():
        assert World.query.get(first_id) is not None
        assert World.query.filter_by(mode="sandbox").count() == 2


def test_a_sandbox_game_gives_every_firm_its_own_badge(app, client):
    # The sandbox picker cannot filter -- the world does not exist yet when
    # the player chooses. Uniqueness comes from the other side instead: bots
    # are built after the player and avoid every badge already in the game.
    start_game(client, team_name="Mine")
    with app.app_context():
        world = World.query.filter_by(mode="sandbox").one()
        badges = [f.badge for f in Firm.query.filter_by(world_id=world.id).all()]
        assert all(badges), badges
        assert len(badges) == len(set(badges)), badges


def test_a_sandbox_bot_never_wears_the_players_chosen_badge(app, client):
    start_game(client, team_name="Mine", badge="logo-03.png")
    with app.app_context():
        world = World.query.filter_by(mode="sandbox").one()
        player = Firm.query.filter_by(world_id=world.id, slot_number=1).one()
        # Guard against a vacuous pass: if the form did not actually apply
        # the chosen badge, the player would hold logo-01 and this test
        # would be asserting that no bot took an unrelated free badge.
        assert player.badge == "logo-03.png"
        bots = Firm.query.filter(Firm.world_id == world.id, Firm.id != player.id).all()
        assert "logo-03.png" not in [b.badge for b in bots]


def test_the_sandbox_results_board_plays_the_mall_intro(app, client):
    # Regression: the sandbox board sets hold=True to stop it auto-
    # refreshing, and the mall used to be gated on `not hold`. That turned
    # the animation off on the one screen a solo player actually sees after
    # a round -- the board looked identical to before the feature existed.
    start_game(client, team_name="Shopper Check")
    client.post("/firm/decisions", data={
        "price": "80", "production_qty": "10000", "ad_spend": "0",
        "rd_spend": "0", "track": "Mid", "plant_investment": "0",
    })
    with app.app_context():
        world = World.query.filter_by(mode="sandbox").one()
    body = client.get(f"/sandbox/results/{world.id}").data.decode()
    assert "present-stage" in body
    assert "mall-bay" in body
    assert "present-rows-after-mall" in body
    assert body.index("present-stage") < body.index("present-rows")
