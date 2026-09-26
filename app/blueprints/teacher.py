"""
Teacher Dashboard: creating Worlds, advancing rounds, and the Team Lookup
screen. This is the DB<->engine bridge -- engine.py stays pure, this module
is the "thin wrapper around it" its own docstring calls for.

Edge cases considered:
 1. Advancing a round is a TWO-STEP action, matching World.status
    ("collecting" -> process -> "transition" -> open next -> "collecting"):
    a teacher can't accidentally skip straight past the Round Transition
    Notice banner students are meant to see, because "processing" and
    "opening the next round" are separate button presses/POSTs.
 2. process_round() needs ALL firms (including already-bankrupt ones) in
    `states`, but only ACTIVE (non-bankrupt) firms in `decisions` -- mirrors
    engine.py's own documented contract exactly; getting this backwards
    would silently make bankrupt firms compete again.
 3. A firm that didn't submit gets a synthesized RoundDecision persisted
    with is_auto=True -- so RoundDecision stays "exactly one row per firm
    per round" even for non-submitters (per the models.py schema contract).
 4. Round 10 processing moves World.status straight to "complete" instead
    of "transition" -- there is no Round 11 to open.
 5. A World that's already "complete" or "processing" can't be advanced
    again via a stale/duplicate POST (e.g. double-clicked button) -- the
    status check in advance_round() only acts on "collecting" or
    "transition", anything else is a no-op with a flash message.
 6. Cumulative R&D/Ad spend must accumulate using the decision actually
    applied this round (submitted OR the zero-spend auto-decision) -- read
    from the same `decisions` dict process_round() consumed, not
    re-derived, so it can never drift from what was actually simulated.
 7. Team Lookup must work with zero rounds played yet (empty decisions/
    results lists render fine, not an error).
"""

import random
import secrets
import string

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from app.auth import current_firm, log_out_teacher, teacher_login_required
from app.avatars import AVATAR_CHOICES, BADGE_CHOICES, PRODUCT_CHOICES, TIER_ICONS, pick_unused_icons
from app.bots import BOT_PROFILES
from app.bots import decide as bot_decide
from app.constants import (
    CELEBRITY_LABELS,
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    ROUNDS_PER_WORLD,
    STARTING_CASH,
    STARTING_PLANT_CAPACITY,
    event_for_round,
)
from app.csv_export import build_export_rows, export_filename, rows_to_csv_string
from app.engine import FirmDecision, FirmState, process_round, synthesize_non_submission_decision
from app.extensions import db
from app.market_data import (
    PIE_COLORS,
    SEGMENT_ACCENTS,
    SEGMENT_ACCENT_FALLBACK,
    SEGMENT_TRAIT_DOTS,
    TIER_ACCENTS,
    mall_bay_width_css,
    build_pie_gradient,
    finale_delay_seconds,
    mall_scene,
    competitive_intel_rows,
    consumer_surplus_by_segment,
    cumulative_standings,
    latest_processed_round,
    market_shares_for_round,
    pie_slices,
    price_history_chart,
    round_totals,
    segment_overview,
    standings_with_rank_delta,
)
from app.scouting_report import build_scouting_report
from app.models import Firm, RoundDecision, RoundResult, RoundSnapshot, SegmentRoundResult, World

bp = Blueprint("teacher", __name__, url_prefix="/teacher")


def _generate_game_code(length=6):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(alphabet, k=length))
        if not World.query.filter_by(game_code=code).first():
            return code
    raise RuntimeError("Could not generate a unique game code after 50 attempts")


# Common, easy-to-read/spell words (all lowercase, no ambiguous-looking
# pairs) for reset passwords -- confirmed with the user: a teacher has to
# read this aloud or type it out to relay to a team on a Chromebook, so
# real words beat a random hex token here.
_RESET_PASSWORD_WORDS = (
    "river", "cloud", "tiger", "apple", "mango", "eagle", "coral", "amber",
    "ocean", "maple", "robin", "pearl", "delta", "comet", "lemon", "olive",
    "otter", "panda", "quartz", "ruby", "sunny", "trail", "violet", "willow",
)


def _generate_reset_password():
    """Two distinct words + one digit, hyphen-separated (e.g.
    "river-tiger-4")."""
    word1, word2 = random.sample(_RESET_PASSWORD_WORDS, 2)
    return f"{word1}-{word2}-{random.randint(0, 9)}"


@bp.route("/")
@teacher_login_required
def dashboard():
    # Classroom worlds only. Sandbox and bots-only worlds are the player's
    # or a balance run's, not a class period -- listing them here would bury
    # real periods under throwaway runs.
    worlds = (
        World.query.filter_by(mode="classroom")
        .order_by(World.created_at.desc()).all()
    )
    # Balance runs are listed separately rather than in Your Worlds: they'd
    # bury real class periods, but a finished run still has to be reachable
    # after you navigate away from it.
    balance_runs = (
        World.query.filter_by(mode="bots_only")
        .order_by(World.created_at.desc()).limit(10).all()
    )
    return render_template(
        "teacher_dashboard.html",
        worlds=worlds,
        balance_runs=balance_runs,
        bot_order=tuple(BOT_PROFILES),
        bot_profiles=BOT_PROFILES,
        rounds_per_world=ROUNDS_PER_WORLD,
        avatars=AVATAR_CHOICES, badges=BADGE_CHOICES, products=PRODUCT_CHOICES,
    )


@bp.route("/worlds", methods=["POST"])
@teacher_login_required
def create_world():
    name = request.form.get("name", "").strip()
    try:
        planned_firm_slots = int(request.form.get("planned_firm_slots", 0))
    except ValueError:
        planned_firm_slots = 0

    if not name or planned_firm_slots < 1:
        flash("Enter a period name and a firm count of at least 1.")
        return redirect(url_for("teacher.dashboard"))

    # Market Shifts for a classroom period: the same world in every other
    # respect, with the four scripted events switched on. Off unless ticked,
    # so creating a world behaves exactly as it always has.
    world = World(name=name, game_code=_generate_game_code(), planned_firm_slots=planned_firm_slots,
                  events_enabled=request.form.get("events_enabled") == "yes")
    db.session.add(world)
    db.session.flush()  # assigns world.id before we reference it below

    for slot in range(1, planned_firm_slots + 1):
        db.session.add(Firm(
            world_id=world.id, slot_number=slot,
            cash=STARTING_CASH, plant_capacity=STARTING_PLANT_CAPACITY,
            # Bootstrap fallback so a firm that never once submits still has
            # a real price/track for synthesize_non_submission_decision() to
            # carry forward on Round 1 -- see constants.py's own comment on
            # why this is a platform default, not a locked-docs number.
            last_price=BOOTSTRAP_DEFAULT_PRICE, last_track=BOOTSTRAP_DEFAULT_TRACK,
        ))
    db.session.commit()

    flash(f"World '{name}' created. Game code: {world.game_code}")
    return redirect(url_for("teacher.view_world", world_id=world.id))


@bp.route("/worlds/<int:world_id>")
@teacher_login_required
def view_world(world_id):
    world = World.query.get_or_404(world_id)
    firms = Firm.query.filter_by(world_id=world.id).order_by(Firm.slot_number).all()
    registered_count = sum(1 for f in firms if f.is_registered)
    submitted_count = (
        RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == world.current_round)
        .count()
    )

    # Round selector: any round 1..current_round can be viewed (a round
    # beyond current_round doesn't exist yet). Defaults to the current one.
    selected_round = request.args.get("round", type=int) or world.current_round
    selected_round = max(1, min(selected_round, world.current_round))

    decisions_by_firm = {
        d.firm_id: d
        for d in RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == selected_round)
    }
    results_by_firm = {
        r.firm_id: r
        for r in RoundResult.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundResult.round_number == selected_round)
    }

    # Consumer surplus and the Scouting Report both intentionally use the
    # LATEST PROCESSED round, not the Firms table's selected_round toggle
    # above -- the two controls looked related (they used to share the
    # same round) but weren't, which read as confusing/broken. Each card
    # now shows its own round explicitly in its title instead.
    scouting_round = latest_processed_round(world)

    return render_template(
        "teacher_world.html", world=world, firms=firms,
        submitted_count=submitted_count, registered_count=registered_count,
        rounds_per_world=(world.rounds or ROUNDS_PER_WORLD), selected_round=selected_round,
        decisions_by_firm=decisions_by_firm, results_by_firm=results_by_firm,
        scouting_report=build_scouting_report(world), scouting_round=scouting_round,
        bot_profiles=BOT_PROFILES,
        consumer_surplus=consumer_surplus_by_segment(world, scouting_round),
        consumer_surplus_round=scouting_round,
        reset_passwords=session.get("reset_passwords", {}),
        undoable_round=_undoable_round(world),
        celebrity_labels=CELEBRITY_LABELS,
    )


@bp.route("/worlds/<int:world_id>/market")
@teacher_login_required
def market(world_id):
    world = World.query.get_or_404(world_id)
    latest_round = latest_processed_round(world)

    standings = cumulative_standings(world)

    selected_round = request.args.get("round", type=int)
    if latest_round is not None:
        selected_round = max(1, min(selected_round or latest_round, latest_round))
    totals_for_round = round_totals(world, selected_round) if latest_round else []

    shares = market_shares_for_round(world, latest_round) if latest_round else []
    pie_gradient = build_pie_gradient(shares)

    segments = segment_overview(world, latest_round)

    return render_template(
        "market_dashboard.html", world=world, latest_round=latest_round,
        standings=standings,
        selected_round=selected_round, totals_for_round=totals_for_round,
        shares=shares, pie_gradient=pie_gradient, pie_colors=PIE_COLORS,
        pie_slices=pie_slices(shares), segments=segments, tier_icons=TIER_ICONS,
        segment_accents=SEGMENT_ACCENTS, segment_accent_fallback=SEGMENT_ACCENT_FALLBACK,
        tier_accents=TIER_ACCENTS, trait_dots=SEGMENT_TRAIT_DOTS,
        teacher_view=True,
    )


@bp.route("/worlds/<int:world_id>/present")
@teacher_login_required
def present(world_id):
    """Full-screen, read-only standings for the classroom projector.

    The round reveal is the most social moment in the game and it was
    happening privately on ~30 separate Chromebooks -- this puts it on the
    board. Deliberately read-only: no controls to mis-click while it's on
    the wall in front of the class. Self-refreshes (see the template's meta
    refresh) so processing a round on the laptop updates the projection
    without anyone touching it."""
    world = World.query.get_or_404(world_id)
    scene = mall_scene(world)
    standings = standings_with_rank_delta(world)
    return render_template(
        "present.html",
        world=world,
        standings=standings,
        latest_round=latest_processed_round(world),
        rounds_per_world=(world.rounds or ROUNDS_PER_WORLD),
        mall=scene,
        bay_width=mall_bay_width_css(len(scene)),
        hold=request.args.get("hold") == "1" or world.status == "complete",
        # Skipped only when the board is deliberately frozen for discussion.
        mall_intro=request.args.get("hold") != "1",
        finale=(world.status == "complete"),
        finale_at=finale_delay_seconds(len(standings), request.args.get("hold") != "1"),
        exit_url=url_for("teacher.view_world", world_id=world.id),
        exit_title="Back to the Teacher Dashboard",
    )


@bp.route("/worlds/<int:world_id>/intel")
@teacher_login_required
def intel(world_id):
    world = World.query.get_or_404(world_id)
    latest_round = latest_processed_round(world)
    rows = competitive_intel_rows(world, latest_round) if latest_round else []
    return render_template(
        "competitive_intel.html", world=world, latest_round=latest_round, rows=rows,
        price_chart=price_history_chart(world),
    )


@bp.route("/worlds/<int:world_id>/firms/<int:firm_id>/bot", methods=["POST"])
@teacher_login_required
def assign_bot(world_id, firm_id):
    """Assigns (or reassigns) a bot profile to a slot. Only ever touches an
    UNCLAIMED slot (never a human-registered one) or a slot that's already
    bot-controlled -- reassigning just swaps which profile drives it from
    here on, the underlying Firm row (cash, capacity, history) is untouched
    either way. A human-claimed slot can't be silently taken over through
    this route -- the "Add Bot" control only ever appears next to
    unclaimed rows in the template, and this is the server-side backstop
    for that."""
    firm = Firm.query.filter_by(id=firm_id, world_id=world_id).first_or_404()
    profile = request.form.get("profile", "")

    if profile not in BOT_PROFILES:
        flash("Choose a valid bot profile.")
        return redirect(url_for("teacher.view_world", world_id=world_id))

    if firm.is_registered and not firm.bot_profile:
        flash(f"Firm {firm.slot_number} is already claimed by a real team -- can't assign a bot there.")
        return redirect(url_for("teacher.view_world", world_id=world_id))

    is_new_assignment = firm.bot_profile is None
    firm.bot_profile = profile
    # Team name always reflects the CURRENT profile -- refreshed on a
    # reassignment too, not just first assignment, so it never goes stale
    # (e.g. still showing "Underbidder" after being switched to Elite).
    # Slot number keeps the name unique within the world even if the same
    # profile is assigned to multiple slots.
    firm.team_name = f"Bot #{firm.slot_number} ({BOT_PROFILES[profile]})"
    if is_new_assignment:
        # Random icons from the same pools real teams pick from, but never
        # one another firm in this game already shows -- a bot wearing a
        # team's factory, logo or product read as that team. Rolled once at
        # first assignment and left alone on reassignment (like a human
        # team's icons, it's the slot's look, not the strategy's).
        others = Firm.query.filter(Firm.world_id == world_id, Firm.id != firm.id).all()
        for field, value in pick_unused_icons([f for f in others if f.is_registered]).items():
            setattr(firm, field, value)
        # Placeholder password so is_registered is True and this slot
        # participates in process_round like any other firm -- never
        # actually used for anything (bots don't log in), just needs to be
        # a real, non-guessable hash. Only set once; no reason to churn it
        # on a reassignment.
        firm.password_hash = generate_password_hash(secrets.token_hex(16))

    db.session.commit()
    flash(f"Firm {firm.slot_number} is now bot-controlled ({BOT_PROFILES[profile]}).")
    return redirect(url_for("teacher.view_world", world_id=world_id))


@bp.route("/worlds/<int:world_id>/firms/<int:firm_id>/bot/remove", methods=["POST"])
@teacher_login_required
def remove_bot(world_id, firm_id):
    """Reverts a bot-controlled slot back to a true unclaimed slot (team
    name/password/bot_profile cleared) so a real student can register it,
    or the teacher can assign a different bot fresh. Deliberately does NOT
    touch cash/plant_capacity/cumulative spend or any past RoundDecision/
    RoundResult row on this Firm -- see models.py note 16: a student
    claiming this slot afterward picks up exactly where the bot left off."""
    firm = Firm.query.filter_by(id=firm_id, world_id=world_id).first_or_404()
    if not firm.bot_profile:
        flash(f"Firm {firm.slot_number} isn't bot-controlled.")
        return redirect(url_for("teacher.view_world", world_id=world_id))

    firm.bot_profile = None
    firm.team_name = None
    firm.password_hash = None
    # Hand the icons back too. Badges are unique per world, so a bot that
    # kept its badge after being removed would hold one hostage on an empty
    # slot -- the student who claims the slot could not choose it, and
    # neither could anyone else. Unlike cash and capacity (kept on purpose,
    # see the docstring), the icons are the bot's look, not the slot's
    # history.
    firm.avatar = None
    firm.badge = None
    firm.product_icon = None
    db.session.commit()
    flash(f"Bot removed from Firm {firm.slot_number} -- that slot is unclaimed again.")
    return redirect(url_for("teacher.view_world", world_id=world_id))


@bp.route("/worlds/<int:world_id>/firms/<int:firm_id>/reset-password", methods=["POST"])
@teacher_login_required
def reset_firm_password(world_id, firm_id):
    """Generates a new random password for a human-registered team and
    reveals it once via the Game Management section. Passwords are stored
    as one-way hashes (see Firm.check_password) -- there's no way to
    recover and display a team's EXISTING password, only ever verify a
    guess against it. Confirmed with the user: rather than switch to
    plaintext storage (a real security regression) just to support an
    always-available "show the current password" lookup, a reset
    generates a brand new password and shows THAT once. The plaintext is
    kept only in this teacher's Flask session (signed cookie, server-side
    for the session's lifetime) under reset_passwords[firm_id] -- never
    written to the database -- so "Show Password" in the template can
    keep displaying it across page loads for the rest of this login, but
    it's gone once the teacher logs out, and it was never the team's
    original self-chosen password to begin with."""
    firm = Firm.query.filter_by(id=firm_id, world_id=world_id).first_or_404()
    if not firm.is_registered or firm.bot_profile:
        flash(f"Firm {firm.slot_number} isn't a real team -- nothing to reset.")
        return redirect(url_for("teacher.view_world", world_id=world_id))

    new_password = _generate_reset_password()
    firm.set_password(new_password)
    db.session.commit()

    reset_passwords = session.get("reset_passwords", {})
    reset_passwords[str(firm.id)] = new_password
    session["reset_passwords"] = reset_passwords

    flash(f"Password reset for {firm.team_name}: {new_password}")
    return redirect(url_for("teacher.view_world", world_id=world_id))


@bp.route("/worlds/<int:world_id>/delete", methods=["POST"])
@teacher_login_required
def delete_world(world_id):
    """Permanently deletes a World and everything under it (Firms,
    RoundDecisions, RoundResults -- cascade is enforced both at the ORM
    level and the DB foreign keys, see models.py). Irreversible; the
    template-side confirm() dialog is the only guard against a misclick,
    matching this app's general "keep it simple" auth/UX posture -- there's
    no undo/trash, so this is meant for cleaning up test/mistaken Worlds,
    not something to click lightly on a real class period's data."""
    world = World.query.get_or_404(world_id)
    name = world.name
    db.session.delete(world)
    db.session.commit()
    flash(f"World '{name}' and all its data were permanently deleted.")
    return redirect(url_for("teacher.dashboard"))


@bp.route("/worlds/<int:world_id>/advance", methods=["POST"])
@teacher_login_required
def advance_round(world_id):
    world = World.query.get_or_404(world_id)

    if world.status == "collecting":
        _process_current_round(world)
    elif world.status == "transition":
        _open_next_round(world)
    else:
        flash("This world isn't in a state that can be advanced right now.")

    return redirect(url_for("teacher.view_world", world_id=world.id))


@bp.route("/worlds/<int:world_id>/student-standings", methods=["POST"])
@teacher_login_required
def toggle_student_standings(world_id):
    """Turns the standings board on team screens on or off for this game.
    Takes effect from the next dashboard load, including mid-round."""
    world = World.query.get_or_404(world_id)
    world.show_standings_to_students = request.form.get("enabled") == "1"
    db.session.commit()
    flash(
        "Teams will see the standings board after each round is processed."
        if world.show_standings_to_students
        else "Teams will no longer see the standings board after each round."
    )
    return redirect(url_for("teacher.view_world", world_id=world.id))


# Exactly the Firm fields _process_current_round mutates. If that function
# ever starts writing another field, it MUST be added here or Undo Last Round
# will silently leave that field at its post-round value -- which is the one
# way this feature could corrupt a game rather than restore it. A test pins
# this list against the real mutation set.
SNAPSHOT_FIELDS = (
    "cash",
    "plant_capacity",
    "pending_capacity_increase",
    "cumulative_rd_spend",
    "rd_spend_by_track",
    "cumulative_ad_spend",
    "loan_outstanding",
    "loan_used_ever",
    "bankrupt",
    "last_price",
    "last_track",
)


def rebuild_rd_spend_by_track(firm_id):
    """{tier: R&D} reconstructed from this firm's PROCESSED rounds -- each
    round's R&D credited to the tier it sold that round. Only rounds with a
    result count: a submission for a round not yet processed (or one whose
    results were undone) never reached cumulative spend either.

    Used by init-db to backfill firms from before quality was tier-bound, and
    by undo for snapshots that predate the field."""
    rows = (
        db.session.query(RoundDecision.track, db.func.sum(RoundDecision.rd_spend))
        .join(RoundResult, db.and_(
            RoundResult.firm_id == RoundDecision.firm_id,
            RoundResult.round_number == RoundDecision.round_number,
        ))
        .filter(RoundDecision.firm_id == firm_id, RoundDecision.rd_spend > 0)
        .group_by(RoundDecision.track)
        .all()
    )
    return {track: float(total) for track, total in rows if total}


def _capture_snapshot(world, firms):
    """Record every firm's pre-round state so this round can be undone.
    Replaces any existing snapshot for the same round (a round can be
    processed again after an undo)."""
    RoundSnapshot.query.filter_by(world_id=world.id, round_number=world.current_round).delete()
    db.session.add(RoundSnapshot(
        world_id=world.id,
        round_number=world.current_round,
        world_status=world.status,
        firm_states={
            str(f.id): {field: getattr(f, field) for field in SNAPSHOT_FIELDS}
            for f in firms
        },
    ))


def _undoable_round(world):
    """The single round Undo Last Round can revert, or None.

    That's the most recently PROCESSED round -- never further back, so undo
    can't walk a game backwards one click at a time. Requires a snapshot to
    exist: rounds processed before this feature shipped have results but no
    snapshot, and undoing those would be a guess."""
    latest = (
        db.session.query(RoundResult.round_number)
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .order_by(RoundResult.round_number.desc())
        .first()
    )
    if latest is None:
        return None
    round_number = latest[0]
    snapshot = RoundSnapshot.query.filter_by(
        world_id=world.id, round_number=round_number
    ).first()
    return round_number if snapshot else None


def _process_current_round(world, rng=None):
    # rng: defaults to None -> app.bots.decide() falls back to the stdlib
    # `random` module for true randomness in live class play. The headless
    # balance-testing harness (app/bot_sim.py) passes a seeded
    # random.Random(seed) here instead, so a trial can be reproduced exactly.
    # An unclaimed slot (never registered) isn't a firm in the game yet --
    # it must NOT compete for demand or get a result row. Caught live: an
    # empty slot was silently consuming market share with a blank "Team
    # Name" in the export. Registered firms participate regardless of
    # bankruptcy (process_round needs bankrupt firms present in `states` to
    # emit their frozen row); only unregistered slots are excluded entirely.
    firms = [f for f in Firm.query.filter_by(world_id=world.id).all() if f.is_registered]
    active_firms = [f for f in firms if not f.bankrupt]

    # Before ANY mutation below -- this is what Undo Last Round restores.
    _capture_snapshot(world, firms)

    states = {}
    for firm in firms:
        states[firm.id] = FirmState(
            firm_id=firm.id, cash=firm.cash, plant_capacity=firm.plant_capacity,
            pending_capacity_increase=firm.pending_capacity_increase,
            cumulative_rd_spend=firm.cumulative_rd_spend, cumulative_ad_spend=firm.cumulative_ad_spend,
            loan_outstanding=firm.loan_outstanding, loan_used_ever=firm.loan_used_ever,
            bankrupt=firm.bankrupt, rd_spend_by_track=dict(firm.rd_spend_by_track or {}),
        )

    decisions = {}
    for firm in active_firms:
        submitted = RoundDecision.query.filter_by(
            firm_id=firm.id, round_number=world.current_round
        ).first()
        if submitted:
            decisions[firm.id] = FirmDecision(
                firm_id=firm.id, price=submitted.price, production_qty=submitted.production_qty,
                ad_spend=submitted.ad_spend, rd_spend=submitted.rd_spend, track=submitted.track,
                celebrity_on=submitted.celebrity_on, celebrity=submitted.celebrity,
                plant_investment=submitted.plant_investment,
                is_auto=False,
            )
        elif firm.bot_profile:
            # Bots always act -- "teacher should never have to manually
            # trigger a bot's turn" -- using ONLY this firm's own history
            # (never another firm's decisions/results), per app/bots.py's
            # locked visibility rule.
            last_decision = (
                RoundDecision.query.filter_by(firm_id=firm.id)
                .order_by(RoundDecision.round_number.desc()).first()
            )
            last_result = (
                RoundResult.query.filter_by(firm_id=firm.id)
                .order_by(RoundResult.round_number.desc()).first()
            )
            bot_decision = bot_decide(
                profile=firm.bot_profile, firm_id=firm.id, round_number=world.current_round,
                cash=firm.cash, capacity=firm.effective_capacity,
                cumulative_ad_spend=firm.cumulative_ad_spend,
                rd_spend_by_track=dict(firm.rd_spend_by_track or {}),
                loan_outstanding=firm.loan_outstanding,
                last_price=last_decision.price if last_decision else None,
                last_profit=last_result.profit if last_result else None,
                last_units_lost_to_capacity=(last_result.units_lost_to_capacity or 0.0) if last_result else 0.0,
                rng=rng,
            )
            decisions[firm.id] = bot_decision
            db.session.add(RoundDecision(
                firm_id=firm.id, round_number=world.current_round, price=bot_decision.price,
                production_qty=bot_decision.production_qty, ad_spend=bot_decision.ad_spend,
                rd_spend=bot_decision.rd_spend, track=bot_decision.track,
                celebrity_on=bot_decision.celebrity_on, celebrity=bot_decision.celebrity,
                plant_investment=bot_decision.plant_investment,
                is_auto=True,
            ))
            # Keep last_price/last_track current for this firm -- matters if
            # a teacher later removes the bot and a real student takes over
            # the slot (see models.py note 16): both the Firm Dashboard's
            # quality label and a future non-submission fallback read these.
            firm.last_price = bot_decision.price
            firm.last_track = bot_decision.track
        else:
            auto = synthesize_non_submission_decision(
                firm_id=firm.id, last_price=firm.last_price, last_track=firm.last_track,
                cash=firm.cash, plant_capacity=firm.effective_capacity,
            )
            decisions[firm.id] = auto
            db.session.add(RoundDecision(
                firm_id=firm.id, round_number=world.current_round, price=auto.price,
                production_qty=auto.production_qty, ad_spend=auto.ad_spend, rd_spend=auto.rd_spend,
                track=auto.track, celebrity_on=auto.celebrity_on, celebrity=auto.celebrity,
                plant_investment=auto.plant_investment,
                is_auto=True,
            ))

    # Market Shifts worlds only: None everywhere else, which leaves
    # process_round() on its no-event path.
    event = event_for_round(world.current_round, world.events_enabled)
    results = process_round(states, decisions, event)

    for firm in firms:
        r = results[firm.id]
        db.session.add(RoundResult(
            firm_id=firm.id, round_number=world.current_round,
            units_sold_by_segment=r.units_sold_by_segment, units_sold_total=r.units_sold_total,
            revenue=r.revenue, production_cost=r.production_cost, fixed_cost=r.fixed_cost,
            ad_cost=r.ad_cost, rd_cost=r.rd_cost, celebrity_cost=r.celebrity_cost,
            plant_investment_cost=r.plant_investment_cost, total_cost=r.total_cost, profit=r.profit,
            cash_before=r.cash_before, cash_after=r.cash_after, quality_level=r.quality_level,
            ad_level=r.ad_level, plant_capacity=r.plant_capacity,
            new_pending_capacity_increase=r.new_pending_capacity_increase,
            units_demanded_total=r.units_demanded_total,
            units_lost_to_capacity=r.units_lost_to_capacity,
            loan_taken_this_round=r.loan_taken_this_round > 0, loan_principal_paid=r.loan_principal_paid,
            loan_interest_charged=r.loan_interest_charged, loan_outstanding_after=r.loan_outstanding_after,
            loan_used_ever_after=r.loan_used_ever_after, went_bankrupt_this_round=r.went_bankrupt_this_round,
            is_bankrupt=r.is_bankrupt, is_auto=r.is_auto,
            plant_investment_blocked=r.plant_investment_blocked, celebrity_blocked=r.celebrity_blocked,
        ))

        firm.cash = r.cash_after
        firm.plant_capacity = r.plant_capacity
        firm.pending_capacity_increase = r.new_pending_capacity_increase
        firm.loan_outstanding = r.loan_outstanding_after
        firm.loan_used_ever = r.loan_used_ever_after
        firm.bankrupt = r.is_bankrupt

        if firm.id in decisions:
            d = decisions[firm.id]
            firm.cumulative_rd_spend += d.rd_spend
            firm.cumulative_ad_spend += d.ad_spend
            if d.rd_spend:
                # A NEW dict -- see the JSON-column note on Firm.rd_spend_by_track.
                by_track = dict(firm.rd_spend_by_track or {})
                by_track[d.track] = by_track.get(d.track, 0.0) + d.rd_spend
                firm.rd_spend_by_track = by_track

    # Segment-level (not firm-level) willingness-to-pay stats -- a natural
    # byproduct of the same Step 4b sweep that computed raw_units above
    # (see engine.RoundResults.segment_stats), persisted so the Teacher
    # Dashboard's Average Consumer Surplus by Segment card can read past
    # rounds without re-running the engine.
    for seg, stats in results.segment_stats.items():
        db.session.add(SegmentRoundResult(
            world_id=world.id, round_number=world.current_round, segment=seg,
            total_buyers=stats.total_buyers, unsold_buyers=stats.unsold_buyers,
            unsold_buyers_pct=stats.unsold_buyers_pct, avg_consumer_surplus=stats.avg_consumer_surplus,
        ))

    # Per-world length, not the global constant -- see World.rounds. Falls
    # back to the constant for a World object built before the column
    # existed (defensive; init-db backfills every row to 10).
    world.status = "complete" if world.current_round >= (world.rounds or ROUNDS_PER_WORLD) else "transition"
    # The reopened window closes as soon as the round is processed again.
    world.reopened_round = None
    db.session.commit()


def _open_next_round(world):
    world.current_round += 1
    world.status = "collecting"
    db.session.commit()


@bp.route("/worlds/<int:world_id>/undo", methods=["POST"])
@teacher_login_required
def undo_round(world_id):
    """Revert the most recently processed round.

    Deliberately NOT a general undo history: only the latest processed round
    is reachable, so a teacher can fix a premature "Process Round" click but
    can't quietly rewind a game several rounds.

    Team submissions for the round survive on purpose -- the point is that
    groups resubmit or adjust and the teacher re-processes once everyone's
    ready. The auto-generated decisions do NOT survive: bot moves and
    no-show fallbacks are produced BY the processing being undone, so
    leaving them would freeze a bot's move and, worse, leave a no-show team
    looking like it had already submitted, locking it out of the retry this
    feature exists to give it.
    """
    world = World.query.get_or_404(world_id)

    round_number = _undoable_round(world)
    if round_number is None:
        flash("There's no processed round to undo yet.")
        return redirect(url_for("teacher.view_world", world_id=world.id))

    snapshot = RoundSnapshot.query.filter_by(
        world_id=world.id, round_number=round_number
    ).first()

    firms = Firm.query.filter_by(world_id=world.id).all()
    firms_by_id = {f.id: f for f in firms}
    for firm_id_str, saved in snapshot.firm_states.items():
        firm = firms_by_id.get(int(firm_id_str))
        if firm is None:
            continue  # slot deleted since the round ran -- nothing to restore
        for field, value in saved.items():
            setattr(firm, field, value)

    firm_ids = list(firms_by_id)
    if firm_ids:
        RoundResult.query.filter(
            RoundResult.firm_id.in_(firm_ids),
            RoundResult.round_number == round_number,
        ).delete(synchronize_session=False)
        # Bot/no-show rows were generated by this processing run; real
        # submissions (is_auto=False) are the teams' own work and stay.
        RoundDecision.query.filter(
            RoundDecision.firm_id.in_(firm_ids),
            RoundDecision.round_number == round_number,
            RoundDecision.is_auto.is_(True),
        ).delete(synchronize_session=False)

    SegmentRoundResult.query.filter_by(
        world_id=world.id, round_number=round_number
    ).delete(synchronize_session=False)

    # A snapshot taken before tier-bound quality shipped has no
    # rd_spend_by_track to restore. Rebuild it from history instead -- the
    # undone round's results were just deleted, so it drops out on its own.
    for firm_id_str, saved in snapshot.firm_states.items():
        firm = firms_by_id.get(int(firm_id_str))
        if firm is not None and "rd_spend_by_track" not in saved:
            firm.rd_spend_by_track = rebuild_rd_spend_by_track(firm.id)

    # Reopen the round that was undone. Covers both shapes: undoing straight
    # after processing (current_round is already that round) and undoing
    # after the next round was opened (current_round has moved past it).
    world.current_round = round_number
    world.status = snapshot.world_status if snapshot.world_status else "collecting"
    # Lets teams edit the submissions we just kept (see World.reopened_round).
    world.reopened_round = round_number

    db.session.delete(snapshot)
    db.session.commit()

    flash(f"Round {round_number} was undone. Teams' submissions were kept -- adjust and process again when ready.")
    return redirect(url_for("teacher.view_world", world_id=world.id))


@bp.route("/worlds/<int:world_id>/export.csv")
@teacher_login_required
def export_csv(world_id):
    world = World.query.get_or_404(world_id)
    rows = build_export_rows(world)
    csv_text = rows_to_csv_string(rows)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={export_filename(world)}"},
    )


@bp.route("/worlds/<int:world_id>/lookup")
@teacher_login_required
def team_lookup(world_id):
    world = World.query.get_or_404(world_id)
    query = request.args.get("q", "").strip()

    firms_q = Firm.query.filter_by(world_id=world.id)
    if query:
        firms_q = firms_q.filter(Firm.team_name.ilike(f"%{query}%"))
    firms = firms_q.order_by(Firm.slot_number).all()

    selected_firm = None
    decisions = []
    results = []
    firm_id = request.args.get("firm_id", type=int)
    if firm_id:
        selected_firm = Firm.query.filter_by(id=firm_id, world_id=world.id).first()
        if selected_firm:
            decisions = RoundDecision.query.filter_by(firm_id=firm_id).order_by(RoundDecision.round_number).all()
            results = RoundResult.query.filter_by(firm_id=firm_id).order_by(RoundResult.round_number).all()

    return render_template(
        "teacher_lookup.html", world=world, firms=firms, query=query,
        selected_firm=selected_firm, decisions=decisions, results=results,
        celebrity_labels=CELEBRITY_LABELS,
    )


@bp.route("/logout")
@teacher_login_required
def logout():
    log_out_teacher()
    # A team can be signed in on this same browser (roles coexist now -- see
    # auth.log_in_firm). Dropping teacher access shouldn't kick them off
    # their own dashboard, which is exactly what the student-facing "you're
    # also signed in as Teacher" banner links here to do.
    if current_firm():
        return redirect(url_for("firm.dashboard"))
    return redirect(url_for("auth.game_code_entry"))
