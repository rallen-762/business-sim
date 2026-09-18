"""
Single-player sandbox and unattended bots-only runs.

A MODE on top of the existing app, not a second simulator. Every number
comes from the same places a classroom game uses: app/engine.py for the
market model, app/bots.py for bot decisions, app/constants.py for the
ladders and Section 12 constants, app/csv_export.py for the export. This
module contributes no economics of its own -- if you find a formula here,
it's a bug.

What it actually changes:

 1. No teacher gate. A classroom round is two deliberate steps (process,
    then open the next) so a teacher controls pacing and students see the
    transition screen. Here one player is the whole class, so submitting a
    decision runs run_sandbox_round(), which calls the SAME
    _process_current_round/_open_next_round functions back to back. The
    separation is removed, not reimplemented.

 2. World.mode keeps these worlds off the Teacher Dashboard, and
    World.rounds lets a sandbox world be a different length later without
    touching ROUNDS_PER_WORLD (locked at 10 for now, per the spec).

Edge cases considered:
 1. Bots-only worlds have no human firm at all, so nothing can log into
    them -- they exist purely to be run and exported.
 2. A sandbox player's firm is a normal registered Firm, so every existing
    firm-facing page (dashboard, market, intel) works unchanged.
 3. Round processing is identical to classroom, including the no-show
    fallback: if the player somehow triggers a round without submitting,
    engine.synthesize_non_submission_decision covers them exactly as it
    would a student who missed a round.
 4. play_round refuses to run once the world is complete, or when the
    current round has no submission, so a stale POST or a double-click
    can't push a game past its last round or play a round for the player.
"""

import secrets

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)
from werkzeug.security import generate_password_hash

from app.auth import (
    current_firm, current_world, firm_login_required, log_in_firm,
    teacher_login_required,
)
from app.avatars import AVATAR_CHOICES, BADGE_CHOICES, PRODUCT_CHOICES, pick_unused_icons
from app.blueprints.teacher import _generate_game_code, _open_next_round, _process_current_round
from app.bots import BOT_PROFILES
from app.constants import (
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    MAX_SANDBOX_WORLDS,
    ROUNDS_PER_WORLD,
    STARTING_CASH,
    STARTING_PLANT_CAPACITY,
)
from app.extensions import db
from app.market_data import (
    mall_bay_width_css,
    latest_processed_round,
    mall_scene,
    standings_with_rank_delta,
)
from app.models import Firm, RoundDecision, World

bp = Blueprint("sandbox", __name__, url_prefix="/sandbox")

# Bot roster order is the single source of truth for "Bot #N" everywhere --
# #1 Underbidder, #2 Marketing, #3 Elite, #4 Random. Numbering a bot by its
# slot in this list is the only correct way to refer to one; don't hardcode
# a number next to a name anywhere else.
BOT_ORDER = tuple(BOT_PROFILES)


def bot_display_name(slot_number, profile):
    """"Bot #2 (Marketing)" -- slot number and profile label together, so a
    player never has to guess which bot is which."""
    return f"Bot #{slot_number} ({BOT_PROFILES[profile]})"


def _make_bot_firm(world, slot_number, profile, other_firms=()):
    """`other_firms`: firms already in this game (the player, earlier bots),
    whose icons this bot must not reuse -- see avatars.pick_unused_icons."""
    icons = pick_unused_icons(other_firms)
    return Firm(
        world_id=world.id,
        slot_number=slot_number,
        team_name=bot_display_name(slot_number, profile),
        # Never logged into -- a bot has no player. Random hash rather than
        # a blank/known one so the slot can't be claimed by guessing.
        password_hash=generate_password_hash(secrets.token_hex(16)),
        bot_profile=profile,
        cash=STARTING_CASH,
        plant_capacity=STARTING_PLANT_CAPACITY,
        last_price=BOOTSTRAP_DEFAULT_PRICE,
        last_track=BOOTSTRAP_DEFAULT_TRACK,
        **icons,
    )


def _chosen_icon(form, field, choices):
    """One picked icon, validated against the real choice list.

    Never trusts the posted value -- an unknown filename would render as a
    broken image forever (that already happened once, when an asset swap
    left old filenames in the DB). Falls back to the first choice rather
    than rejecting the whole submission: a sandbox game isn't worth an
    error page over a cosmetic field.
    """
    value = form.get(field, "")
    return value if value in choices else choices[0]


def _picked_icons(form):
    """All three icon choices from a sandbox form, each validated."""
    return {
        "avatar": _chosen_icon(form, "avatar", AVATAR_CHOICES),
        "badge": _chosen_icon(form, "badge", BADGE_CHOICES),
        "product_icon": _chosen_icon(form, "product_icon", PRODUCT_CHOICES),
    }


def _requested_profiles(form):
    """Bot profiles chosen on the setup form, in BOT_ORDER. Falls back to
    the full roster if nothing was ticked, so a stray empty submit still
    produces a playable game rather than a one-firm monopoly."""
    chosen = [p for p in BOT_ORDER if form.get(f"bot_{p}")]
    return chosen or list(BOT_ORDER)


# --------------------------------------------------------------------------- #
# Entry -- open, no gate
# --------------------------------------------------------------------------- #
#
# There is no sandbox password any more. It was a single secret shared with a
# whole class, so it never kept anyone out; what it actually did was cap world
# creation by accident. That job is now explicit and enforced where worlds are
# made -- see _evict_oldest_sandbox_worlds.


@bp.route("/")
def home():
    # No list of existing games here on purpose. It showed EVERY sandbox
    # world anyone had created, and its Resume button signed you into that
    # world's firm -- so on a shared classroom device one student could drop
    # straight into another's practice game. Removed rather than filtered,
    # since there is no per-player ownership on a sandbox world to filter by.
    # The /play/<id> route that survived it is gone too: a sandbox game now
    # lives and dies with the session that started it.
    return render_template(
        "sandbox_home.html",
        bot_order=BOT_ORDER,
        bot_profiles=BOT_PROFILES,
        bot_display_name=bot_display_name,
        default_rounds=ROUNDS_PER_WORLD,
        avatars=AVATAR_CHOICES, badges=BADGE_CHOICES, products=PRODUCT_CHOICES,
    )


# --------------------------------------------------------------------------- #
# Single-player: one human, configurable bots, no teacher
# --------------------------------------------------------------------------- #

def _evict_oldest_sandbox_worlds():
    """Keep the number of sandbox worlds at or below MAX_SANDBOX_WORLDS by
    deleting the oldest ones, and return how many were removed.

    Creating a sandbox game needs no login, so without this nothing bounds
    how many worlds exist -- and a sandbox world is not cheap: one World,
    one player Firm, a Firm per bot, and per round a RoundDecision and
    RoundResult for every firm plus a SegmentRoundResult per segment. A
    filled database takes the CLASSROOM game down with it, which is the
    blast radius worth caring about.

    Eviction rather than refusal: a student mid-lesson should never be told
    the sandbox is full. It is safe because a sandbox game was never
    recoverable anyway -- the player firm's password is a random hash that is
    generated, hashed and discarded, so a game is reachable only from the
    session that made it and is already garbage once that session ends.

    Oldest is by primary key, not a timestamp. World has no created_at, and
    adding one would mean an ALTER TABLE step in flask init-db -- the change
    shape this app is least forgiving of. Ids are monotonic, so they order
    creation exactly as well for this purpose.

    Deletion relies on the cascades configured on World (firms, segment
    round results, round snapshots), which tests/test_routes.py pins in
    test_delete_world_cascades_everything.
    """
    doomed = (
        World.query
        .filter_by(mode="sandbox")
        .order_by(World.id.desc())
        .offset(MAX_SANDBOX_WORLDS - 1)
        .all()
    )
    for world in doomed:
        db.session.delete(world)
    if doomed:
        db.session.commit()
    return len(doomed)


def _create_single_player_game(team_name, profiles, icons=None):
    """Build a sandbox world with one human firm plus the chosen bots, and
    return the player's Firm. Shared by both entry points (the student-side
    sandbox page and the Teacher Dashboard) so the two can never drift into
    creating subtly different games.

    `icons` is {avatar, badge, product_icon} as picked on the form. Omitted
    (or partly omitted) it falls back to the first of each, which is what
    every sandbox game used to get -- a fixed identity nobody chose."""
    icons = icons or {}
    world = World(
        name=f"Sandbox -- {team_name}",
        game_code=_generate_game_code(),
        planned_firm_slots=1 + len(profiles),
        mode="sandbox",
        rounds=ROUNDS_PER_WORLD,
    )
    db.session.add(world)
    db.session.flush()

    player = Firm(
        world_id=world.id, slot_number=1, team_name=team_name,
        # Random and never shown. Firm requires a hash, but a sandbox player
        # never types one -- creating the game signs them straight in.
        # Asking for one added no security and cost us a Safe Browsing
        # "deceptive site" flag (see sandbox_home.html).
        #
        # This hash is also what keeps the game from being claimed by
        # someone else: sandbox worlds carry a game code and /login does not
        # filter by mode, so the slot is reachable -- but is_registered is
        # "password_hash is not None", so it asks for a password that exists
        # nowhere instead of offering to set one. Pinned by
        # test_sandbox_player_cannot_be_claimed_through_the_game_code_door.
        password_hash=generate_password_hash(secrets.token_hex(16)),
        cash=STARTING_CASH, plant_capacity=STARTING_PLANT_CAPACITY,
        last_price=BOOTSTRAP_DEFAULT_PRICE, last_track=BOOTSTRAP_DEFAULT_TRACK,
        avatar=icons.get("avatar") or AVATAR_CHOICES[0],
        badge=icons.get("badge") or BADGE_CHOICES[0],
        product_icon=icons.get("product_icon") or PRODUCT_CHOICES[0],
    )
    db.session.add(player)

    in_game = [player]
    for offset, profile in enumerate(profiles, start=2):
        bot = _make_bot_firm(world, offset, profile, in_game)
        in_game.append(bot)
        db.session.add(bot)

    db.session.commit()
    return player


@bp.route("/new", methods=["POST"])
def new_game():
    """Start a single-player game from the student-side sandbox page."""
    _evict_oldest_sandbox_worlds()
    player = _create_single_player_game(
        request.form.get("team_name", "").strip() or "My Company",
        _requested_profiles(request.form),
        _picked_icons(request.form),
    )
    # Straight into the firm -- the whole point is no waiting room.
    log_in_firm(player)
    return redirect(url_for("firm.dashboard"))


@bp.route("/new-from-teacher", methods=["POST"])
@teacher_login_required
def new_game_from_teacher():
    """Start a single-player game straight from the Teacher Dashboard.

    Same game, different door: a teacher trying the sim themselves shouldn't
    have to go find the student login and type a second password. The
    teacher session stays intact alongside the firm session (they're scoped
    separately in app/auth.py), so the Teacher Dashboard is still one click
    away -- and the firm dashboard's "also signed in as teacher" banner
    makes that state visible rather than silent.
    """
    player = _create_single_player_game(
        request.form.get("team_name", "").strip() or "My Company",
        _requested_profiles(request.form),
        _picked_icons(request.form),
    )
    log_in_firm(player)
    return redirect(url_for("firm.dashboard"))


@bp.route("/round", methods=["POST"])
@firm_login_required
def play_round():
    """Run a sandbox round that was submitted but never run. No page links
    here any more -- see run_sandbox_round, which submission calls itself."""
    firm = current_firm()
    world = current_world()

    if world is None or world.mode != "sandbox":
        flash("Rounds can only be run this way in a sandbox game.")
        return redirect(url_for("firm.dashboard"))

    if world.status == "complete":
        flash("This game is already finished.")
        return redirect(url_for("market.dashboard"))

    # Submitting a decision runs the round by itself now (see
    # firm.submit_decision), so this route only acts if a submitted round is
    # still waiting -- a game left mid-round before that change. Without the
    # check, a stale or repeated POST would play the NEXT round on the
    # player's behalf with an auto-decision they never made.
    if not RoundDecision.query.filter_by(firm_id=firm.id, round_number=world.current_round).first():
        return redirect(url_for("firm.dashboard"))

    return redirect(run_sandbox_round(world))


def run_sandbox_round(world):
    """Process the current round and open the next, in one action, and
    return where the player should land. Called the moment a sandbox player
    submits -- there's no one else to wait for, so a separate "Run Round"
    click was a pointless extra step.

    This is the teacher's two-button sequence collapsed into one, calling
    the very same functions -- the classroom flow keeps its deliberate
    separation, and this mode skips it without forking the round logic."""
    _process_current_round(world)
    if world.status != "complete":
        _open_next_round(world)

    # Results first, on the same projector screen the classroom uses. Its
    # exit returns to the Market Dashboard, from which the player clicks back
    # into their firm -- the review beat, rather than dropping them straight
    # back into the next decision form.
    return url_for("sandbox.results", world_id=world.id)


@bp.route("/results/<int:world_id>")
@firm_login_required
def results(world_id):
    """The round-results board after a sandbox round.

    Renders present.html -- the SAME projector view a classroom game shows
    on the wall, not a copy -- with its exit pointed at the Market
    Dashboard instead of the Teacher Dashboard. Reached by the player
    themselves, so it can't use teacher.present, which is teacher-gated.
    """
    world = World.query.get_or_404(world_id)
    firm = current_firm()
    if firm.world_id != world.id:
        flash("That game isn't yours.")
        return redirect(url_for("firm.dashboard"))

    scene = mall_scene(world)
    return render_template(
        "present.html",
        world=world,
        standings=standings_with_rank_delta(world),
        latest_round=latest_processed_round(world),
        rounds_per_world=(world.rounds or ROUNDS_PER_WORLD),
        mall=scene,
        bay_width=mall_bay_width_css(len(scene)),
        # No auto-refresh: a solo player is reading at their own pace, not
        # watching a board that has to stay current for a room.
        hold=True,
        mall_intro=True,
        exit_url=url_for("market.dashboard"),
        exit_title="Back to the Market Dashboard",
    )


# --------------------------------------------------------------------------- #
# Bots-only: unattended balance runs
# --------------------------------------------------------------------------- #

@bp.route("/bots", methods=["POST"])
@teacher_login_required
def run_bots_only():
    """Runs a whole game start to finish with no human firm, then drops the
    teacher on that game's own dashboard.

    Teacher-gated, not sandbox-gated: this is a balance-testing tool, it
    lives on the Teacher Dashboard, and it hands off to a teacher-only page.
    It used to return a CSV download directly, which gave you a file and
    nowhere to look -- the world page has the round-by-round tables, the
    scouting report and the same Export CSV button, so the file is still one
    click away but the results are actually explorable.
    """
    profiles = _requested_profiles(request.form)

    world = World(
        name="Bots-Only Run",
        game_code=_generate_game_code(),
        planned_firm_slots=len(profiles),
        mode="bots_only",
        rounds=ROUNDS_PER_WORLD,
    )
    db.session.add(world)
    db.session.flush()

    in_game = []
    for slot, profile in enumerate(profiles, start=1):
        bot = _make_bot_firm(world, slot, profile, in_game)
        in_game.append(bot)
        db.session.add(bot)
    db.session.commit()

    run_to_completion(world)

    flash(
        f"Balance run complete -- {len(profiles)} bots, {world.rounds} rounds. "
        "Use Export CSV below for the full round-by-round data."
    )
    return redirect(url_for("teacher.view_world", world_id=world.id))


def run_to_completion(world, rng=None):
    """Play a world out to its last round with no human input.

    Bounded by world.rounds rather than looping until status flips, so a
    logic change that failed to set "complete" can't spin forever inside a
    web request.
    """
    for _ in range(world.rounds or ROUNDS_PER_WORLD):
        if world.status == "complete":
            break
        _process_current_round(world, rng=rng)
        if world.status != "complete":
            _open_next_round(world)
    return world
