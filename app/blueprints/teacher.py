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
import string

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from app.auth import log_out, teacher_login_required
from app.constants import (
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    ROUNDS_PER_WORLD,
    STARTING_CASH,
    STARTING_PLANT_CAPACITY,
)
from app.csv_export import build_export_rows, export_filename, rows_to_csv_string
from app.engine import FirmDecision, FirmState, process_round, synthesize_non_submission_decision
from app.extensions import db
from app.market_data import (
    build_pie_gradient,
    competitive_intel_rows,
    cumulative_standings,
    latest_processed_round,
    market_shares_for_round,
    round_totals,
    segment_overview,
)
from app.models import Firm, RoundDecision, RoundResult, World

bp = Blueprint("teacher", __name__, url_prefix="/teacher")


def _generate_game_code(length=6):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(alphabet, k=length))
        if not World.query.filter_by(game_code=code).first():
            return code
    raise RuntimeError("Could not generate a unique game code after 50 attempts")


@bp.route("/")
@teacher_login_required
def dashboard():
    worlds = World.query.order_by(World.created_at.desc()).all()
    return render_template("teacher_dashboard.html", worlds=worlds)


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

    world = World(name=name, game_code=_generate_game_code(), planned_firm_slots=planned_firm_slots)
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

    # Running totals through the SELECTED round specifically -- viewing a
    # past round must show totals as of THAT round, not the world's current
    # round, even though more rounds may have been played since.
    cumulative_rows = (
        db.session.query(
            RoundResult.firm_id,
            db.func.sum(RoundResult.revenue).label("cum_revenue"),
            db.func.sum(RoundResult.profit).label("cum_profit"),
            db.func.sum(RoundResult.units_sold_total).label("cum_units"),
        )
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number <= selected_round)
        .group_by(RoundResult.firm_id)
        .all()
    )
    cumulative_by_firm = {row.firm_id: row for row in cumulative_rows}

    return render_template(
        "teacher_world.html", world=world, firms=firms,
        submitted_count=submitted_count, registered_count=registered_count,
        rounds_per_world=ROUNDS_PER_WORLD, selected_round=selected_round,
        decisions_by_firm=decisions_by_firm, results_by_firm=results_by_firm,
        cumulative_by_firm=cumulative_by_firm,
    )


@bp.route("/worlds/<int:world_id>/market")
@teacher_login_required
def market(world_id):
    world = World.query.get_or_404(world_id)
    latest_round = latest_processed_round(world)

    standings = cumulative_standings(world)
    podium = standings[:3]

    selected_round = request.args.get("round", type=int)
    if latest_round is not None:
        selected_round = max(1, min(selected_round or latest_round, latest_round))
    totals_for_round = round_totals(world, selected_round) if latest_round else []

    shares = market_shares_for_round(world, latest_round) if latest_round else []
    pie_gradient = build_pie_gradient(shares)

    segments = segment_overview(world, latest_round)

    return render_template(
        "market_dashboard.html", world=world, latest_round=latest_round,
        standings=standings, podium=podium,
        selected_round=selected_round, totals_for_round=totals_for_round,
        shares=shares, pie_gradient=pie_gradient, segments=segments,
    )


@bp.route("/worlds/<int:world_id>/intel")
@teacher_login_required
def intel(world_id):
    world = World.query.get_or_404(world_id)
    latest_round = latest_processed_round(world)
    rows = competitive_intel_rows(world, latest_round) if latest_round else []
    return render_template(
        "competitive_intel.html", world=world, latest_round=latest_round, rows=rows,
    )


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


def _process_current_round(world):
    # An unclaimed slot (never registered) isn't a firm in the game yet --
    # it must NOT compete for demand or get a result row. Caught live: an
    # empty slot was silently consuming market share with a blank "Team
    # Name" in the export. Registered firms participate regardless of
    # bankruptcy (process_round needs bankrupt firms present in `states` to
    # emit their frozen row); only unregistered slots are excluded entirely.
    firms = [f for f in Firm.query.filter_by(world_id=world.id).all() if f.is_registered]
    active_firms = [f for f in firms if not f.bankrupt]

    states = {}
    for firm in firms:
        states[firm.id] = FirmState(
            firm_id=firm.id, cash=firm.cash, plant_capacity=firm.plant_capacity,
            pending_capacity_increase=firm.pending_capacity_increase,
            cumulative_rd_spend=firm.cumulative_rd_spend, cumulative_ad_spend=firm.cumulative_ad_spend,
            loan_outstanding=firm.loan_outstanding, loan_used_ever=firm.loan_used_ever,
            bankrupt=firm.bankrupt,
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
                celebrity_on=submitted.celebrity_on, plant_investment=submitted.plant_investment,
                is_auto=False,
            )
        else:
            auto = synthesize_non_submission_decision(
                firm_id=firm.id, last_price=firm.last_price, last_track=firm.last_track,
                cash=firm.cash, plant_capacity=firm.plant_capacity,
            )
            decisions[firm.id] = auto
            db.session.add(RoundDecision(
                firm_id=firm.id, round_number=world.current_round, price=auto.price,
                production_qty=auto.production_qty, ad_spend=auto.ad_spend, rd_spend=auto.rd_spend,
                track=auto.track, celebrity_on=auto.celebrity_on, plant_investment=auto.plant_investment,
                is_auto=True,
            ))

    results = process_round(states, decisions)

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

    world.status = "complete" if world.current_round >= ROUNDS_PER_WORLD else "transition"
    db.session.commit()


def _open_next_round(world):
    world.current_round += 1
    world.status = "collecting"
    db.session.commit()


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
    )


@bp.route("/logout")
@teacher_login_required
def logout():
    log_out()
    return redirect(url_for("auth.game_code_entry"))
